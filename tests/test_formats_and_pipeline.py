from datetime import date
from pathlib import Path
import pandas as pd
import pytest

from subsweep.ingest import (
    FormatDetectionError, StatementTooLargeError, load_statement, merge_statements,
)
from subsweep.ingest.formats import (
    CARD_SPLIT, GENERIC, MANUAL, PAYPAL, ColumnMapping, classify_header, detect_format,
    sniff_delimiter, read_rows,
)

SAMPLES = Path(__file__).resolve().parent.parent / "sample_data"
COLUMNS = ["id", "date", "merchant_clean", "merchant_raw", "amount", "currency", "source"]


# ---------- detection ----------
@pytest.mark.parametrize("header,field", [
    ("Date", "date"), ("Txn Date", "date"), ("Posting Date", "date"), ("Narration", "description"),
    ("Particulars", "description"), ("Amount (INR)", "amount"), ("Withdrawal Amt.", "debit"),
    ("Deposit Amt.", "credit"), ("Debit", "debit"), ("Credit", "credit"), ("Gross", "gross"),
    ("Transaction ID", "ref"), ("Currency", "currency"), ("Balance", None), ("", None),
])
def test_classify_header(header, field):
    assert classify_header(header) == field


def test_detect_generic_bank():
    rows = read_rows("Date,Description,Amount\n2026-01-01,X,-1\n", ",")
    d = detect_format(rows)
    assert d.format == GENERIC and d.ok


def test_detect_credit_card_split():
    d = detect_format(read_rows("Date,Description,Debit,Credit\n", ","))
    assert d.format == CARD_SPLIT and d.mapping.debit == "Debit"


def test_detect_indian_withdrawal_deposit_as_split():
    d = detect_format(read_rows("Date,Narration,Withdrawal Amt.,Deposit Amt.,Balance\n", ","))
    assert d.format == CARD_SPLIT and d.ok


def test_detect_paypal():
    d = detect_format(read_rows("Date,Time,Name,Type,Status,Currency,Gross,Fee,Net,Transaction ID\n", ","))
    assert d.format == PAYPAL and d.mapping.description == "Name" and d.mapping.amount == "Gross"


def test_detection_skips_preamble_rows():
    text = "Account:,Joe\nPeriod:,Sep\n\nDate,Description,Amount\n2026-01-01,X,-1\n"
    d = detect_format(read_rows(text, ","))
    assert d.header_row == 3 and d.format == GENERIC


def test_unknown_headers_not_ok():
    d = detect_format(read_rows("foo,bar,baz\n1,2,3\n", ","))
    assert not d.ok


@pytest.mark.parametrize("text,delim", [("a,b,c\n1,2,3", ","), ("a;b;c\n1;2;3", ";"), ("a\tb\tc\n1\t2\t3", "\t"), ("abc", ",")])
def test_sniff_delimiter(text, delim):
    assert sniff_delimiter(text) == delim


# ---------- generic bank ----------
def test_generic_bank_basic():
    res = load_statement("Date,Description,Amount\n2026-10-01,NETFLIX.COM,-15.49\n2026-10-02,REFUND,5.00\n")
    t = res.transactions
    assert list(t.columns[:7]) == COLUMNS
    assert t.amount.tolist() == [-15.49, 5.0]
    assert t.merchant_clean[0] == "Netflix" and t.merchant_raw[0] == "NETFLIX.COM"
    assert t.currency.tolist() == ["USD", "USD"]


def test_generic_bank_semicolon_delimiter_and_decimal_comma():
    res = load_statement("Date;Description;Amount\n12.10.2026;SPOTIFY;-9,99\n")
    assert res.transactions.amount[0] == -9.99


def test_generic_bank_drcr_type_column_flips_sign():
    csv_text = "Date,Narration,Amount,Dr/Cr\n01/10/2026,A,100,DR\n02/10/2026,B,100,CR\n"
    t = load_statement(csv_text).transactions
    assert t.amount.tolist() == [-100.0, 100.0]


def test_generic_bank_currency_column_overrides_default():
    csv_text = "Date,Description,Amount,Currency\n2026-10-01,A,-5,EUR\n2026-10-02,B,-5,\n"
    t = load_statement(csv_text, default_currency="INR").transactions
    assert t.currency.tolist() == ["EUR", "INR"]


def test_currency_symbol_in_amount_wins_over_default():
    t = load_statement("Date,Description,Amount\n2026-10-01,A,₹-499\n", default_currency="USD").transactions
    assert t.currency[0] == "INR"


def test_amount_header_currency_used_as_default():
    t = load_statement("Date,Description,Amount (INR)\n2026-10-01,A,-499\n").transactions
    assert t.currency[0] == "INR"


def test_invert_sign_for_positive_purchase_exports():
    m = ColumnMapping(date="Date", description="Description", amount="Amount", invert_sign=True)
    t = load_statement("Date,Description,Amount\n2026-10-01,A,25\n", mapping=m).transactions
    assert t.amount[0] == -25.0


def test_positive_only_amounts_warn():
    body = "".join(f"2026-10-{d:02d},Shop {d},{d}.00\n" for d in range(1, 8))
    res = load_statement("Date,Description,Amount\n" + body)
    assert any("positive" in w for w in res.warnings)


def test_bad_rows_are_skipped_with_friendly_warning():
    res = load_statement("Date,Description,Amount\nnot-a-date,A,-5\n2026-10-01,B,oops\n2026-10-02,C,-3\n")
    assert len(res.transactions) == 1
    assert res.dropped["unreadable date"] == 1 and res.dropped["unreadable amount"] == 1
    assert len(res.warnings) == 2


def test_footer_total_row_dropped_silently():
    res = load_statement("Date,Description,Amount\n2026-10-01,A,-5\nTotal,,-5\n")
    assert len(res.transactions) == 1 and res.warnings == []


def test_bytes_with_bom_and_cp1252():
    raw = "Date,Description,Amount\n2026-10-01,Café Rouge,-4.50\n".encode("cp1252")
    assert load_statement(raw).transactions.merchant_raw[0] == "Café Rouge"
    bom = b"\xef\xbb\xbfDate,Description,Amount\n2026-10-01,X,-1\n"
    assert load_statement(bom).format == GENERIC


def test_embedded_commas_and_quotes_in_description():
    t = load_statement('Date,Description,Amount\n2026-10-01,"ACME, INC ""PRO""",-1.00\n').transactions
    assert t.merchant_raw[0] == 'ACME, INC "PRO"'


# ---------- date order across formats ----------
def test_dayfirst_inferred_for_whole_file():
    t = load_statement("Date,Description,Amount\n01/02/2026,A,-1\n20/02/2026,B,-1\n").transactions
    assert t.date.tolist() == [date(2026, 2, 1), date(2026, 2, 20)]


def test_monthfirst_inferred():
    t = load_statement("Date,Description,Amount\n01/02/2026,A,-1\n02/20/2026,B,-1\n").transactions
    assert t.date.tolist() == [date(2026, 1, 2), date(2026, 2, 20)]


def test_caller_can_force_dayfirst_on_ambiguous_file():
    res = load_statement("Date,Description,Amount\n01/02/2026,A,-1\n", dayfirst=True)
    assert res.transactions.date[0] == date(2026, 2, 1)


# ---------- credit card split ----------
def test_card_split_debit_credit():
    csv_text = "Date,Description,Debit,Credit\n12 Oct 2026,SHOP,\"$15.49\",\n13 Oct 2026,REFUND,,\"$5.00\"\n"
    t = load_statement(csv_text).transactions
    assert t.amount.tolist() == [-15.49, 5.0]
    assert t.date[0] == date(2026, 10, 12)


def test_card_split_parenthesised_credit_is_still_a_credit():
    t = load_statement("Date,Description,Debit,Credit\n2026-10-01,R,,(12.00)\n").transactions
    assert t.amount[0] == 12.0


def test_card_split_sample_file():
    res = load_statement(SAMPLES / "card_split.csv")
    assert res.format == CARD_SPLIT
    t = res.transactions
    assert t[t.merchant_clean == "Adobe Creative Cloud"].currency.iloc[0] == "EUR"
    assert (t.merchant_clean == "Netflix").sum() == 2


# ---------- PayPal ----------
def test_paypal_sample_filters_non_spend_rows():
    res = load_statement(SAMPLES / "paypal_activity.csv")
    assert res.format == PAYPAL
    t = res.transactions
    assert len(t) == 5
    assert t.via_paypal.all()
    assert "paypal status 'denied'" in res.dropped
    assert not t.merchant_raw.str.contains("Bank Account").any()


def test_paypal_keeps_refunds_as_credits():
    t = load_statement(SAMPLES / "paypal_activity.csv").transactions
    assert (t.amount > 0).sum() == 1


def test_paypal_description_falls_back_to_item_title():
    csv_text = (
        "Date,Time,Name,Type,Status,Currency,Gross,Fee,Net,Transaction ID,Item Title\n"
        "10/20/2026,01:00:00,,Express Checkout Payment,Completed,USD,-9.00,0.00,-9.00,AB1,Domain renewal\n"
    )
    assert load_statement(csv_text).transactions.merchant_raw[0] == "Domain renewal"


def test_paypal_prefix_in_bank_description_sets_via_paypal():
    t = load_statement("Date,Description,Amount\n2026-10-01,PAYPAL *SPOTIFYAB 402-935-7733,-10.99\n").transactions
    assert t.via_paypal[0]


def test_paypal_source_ref_kept():
    t = load_statement(SAMPLES / "paypal_activity.csv").transactions
    assert (t.source_ref != "").all()


# ---------- bank sample / messy ----------
def test_bank_sample_with_preamble_indian_amounts_and_footer():
    res = load_statement(SAMPLES / "bank_generic_messy.csv")
    t = res.transactions
    assert res.format == GENERIC and len(t) == 8
    assert t.currency.eq("INR").all()
    assert t[t.merchant_clean == "Salary Credit Acme"].amount.iloc[0] == 123456.0
    assert t.date.min() == date(2026, 9, 13)
    assert res.dropped == {"blank or footer row": 1}


# ---------- manual mapping ----------
def test_detection_failure_carries_headers_and_guesses():
    with pytest.raises(FormatDetectionError) as e:
        load_statement("When,What,HowMuch\n2026-10-01,A,-5\n")
    assert e.value.headers == ["When", "What", "HowMuch"]


def test_manual_mapping_dict_works():
    mapping = {"date": "When", "description": "What", "amount": "HowMuch"}
    res = load_statement("When,What,HowMuch\n2026-10-01,NETFLIX.COM,-5\n", mapping=mapping)
    assert res.format == MANUAL and res.transactions.merchant_clean[0] == "Netflix"


def test_manual_mapping_missing_column_errors_friendly():
    with pytest.raises(FormatDetectionError, match="isn't in this file"):
        load_statement("A,B,C\n1,2,3\n", mapping={"date": "A", "description": "B", "amount": "Zzz"})


def test_incomplete_manual_mapping_rejected():
    with pytest.raises(FormatDetectionError):
        load_statement("A,B,C\n1,2,3\n", mapping={"date": "A"})


def test_empty_file_friendly_error():
    with pytest.raises(FormatDetectionError, match="empty"):
        load_statement("\n\n")


# ---------- limits ----------
def test_size_limit_enforced():
    with pytest.raises(StatementTooLargeError):
        load_statement(b"x" * 2000, max_bytes=1000)


def test_row_limit_enforced():
    body = "".join(f"2026-10-01,S{i},-1\n" for i in range(20))
    with pytest.raises(StatementTooLargeError):
        load_statement("Date,Description,Amount\n" + body, max_rows=10)


# ---------- output contract ----------
def test_output_columns_and_types():
    t = load_statement(SAMPLES / "card_split.csv").transactions
    assert list(t.columns[:7]) == COLUMNS
    assert t.id.is_unique
    assert pd.api.types.is_float_dtype(t.amount)
    assert t.date.is_monotonic_increasing


def test_ids_are_deterministic():
    a = load_statement(SAMPLES / "card_split.csv").transactions.id.tolist()
    b = load_statement(SAMPLES / "card_split.csv").transactions.id.tolist()
    assert a == b
