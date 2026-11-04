import pytest
from subsweep.ingest import load_statement, merge_statements

HDR = "Date,Description,Amount\n"


def test_same_day_repeated_charges_are_kept_and_flagged():
    t = load_statement(HDR + "2026-10-02,SWIGGY,-10\n2026-10-02,SWIGGY,-10\n2026-10-03,OTHER,-1\n").transactions
    assert len(t) == 3
    assert t.possible_duplicate.tolist() == [True, True, False]
    assert t.id.is_unique


def test_rows_with_same_transaction_id_are_collapsed():
    csv_text = (
        "Date,Time,Name,Type,Status,Currency,Gross,Fee,Net,Transaction ID\n"
        "10/01/2026,01:00,Netflix,Subscription Payment,Completed,USD,-15.49,0,-15.49,AAA111\n"
        "10/01/2026,01:00,Netflix,Subscription Payment,Completed,USD,-15.49,0,-15.49,AAA111\n"
        "10/01/2026,02:00,Netflix,Subscription Payment,Completed,USD,-15.49,0,-15.49,BBB222\n"
    )
    res = load_statement(csv_text)
    assert len(res.transactions) == 2
    assert res.dropped["exact duplicate (same transaction ID)"] == 1


def test_different_amounts_same_day_not_duplicates():
    t = load_statement(HDR + "2026-10-02,SHOP,-10\n2026-10-02,SHOP,-11\n").transactions
    assert not t.possible_duplicate.any()


def test_merge_overlapping_exports_removes_overlap():
    a = load_statement(HDR + "2026-10-01,A,-1\n2026-10-02,B,-2\n")
    b = load_statement(HDR + "2026-10-02,B,-2\n2026-10-03,C,-3\n")
    merged = merge_statements([a, b])
    assert merged.merchant_raw.tolist() == ["A", "B", "C"]


def test_merge_keeps_genuine_same_day_repeat_present_in_both_files():
    a = load_statement(HDR + "2026-10-02,SWIGGY,-10\n2026-10-02,SWIGGY,-10\n")
    b = load_statement(HDR + "2026-10-02,SWIGGY,-10\n2026-10-02,SWIGGY,-10\n2026-10-04,X,-1\n")
    merged = merge_statements([a, b])
    assert (merged.merchant_raw == "SWIGGY").sum() == 2          # max(2, 2), not 4
    assert merged[merged.merchant_raw == "SWIGGY"].possible_duplicate.all()


def test_merge_takes_max_count_when_one_file_has_more_repeats():
    a = load_statement(HDR + "2026-10-02,SWIGGY,-10\n")
    b = load_statement(HDR + "2026-10-02,SWIGGY,-10\n2026-10-02,SWIGGY,-10\n")
    assert len(merge_statements([a, b])) == 2


def test_merge_dedupes_by_transaction_id_across_files():
    row = "10/01/2026,01:00,Netflix,Subscription Payment,Completed,USD,-15.49,0,-15.49,AAA111\n"
    h = "Date,Time,Name,Type,Status,Currency,Gross,Fee,Net,Transaction ID\n"
    assert len(merge_statements([load_statement(h + row), load_statement(h + row)])) == 1


def test_merge_of_nothing_is_empty():
    assert merge_statements([]).empty


def test_ids_stable_after_merge():
    a = load_statement(HDR + "2026-10-01,A,-1\n")
    assert merge_statements([a, a]).id.tolist() == merge_statements([a]).id.tolist()
