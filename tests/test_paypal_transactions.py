"""Transaction Search: windows, pagination, parameters, normalisation. HTTP is mocked."""
from datetime import date, datetime, timedelta, timezone

import pytest

from subsweep.detect import detect_recurring
from subsweep.paypal import PayPalPermissionError
from subsweep.paypal.transactions import (
    PAYPAL_COLUMNS, SCOPE_SEARCH, fetch_transactions, fmt, split_windows, to_transactions,
)
from tests.paypal_helpers import FakePayPal, detail, make, token_body

PATH = "/v1/reporting/transactions"
NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
UTC = timezone.utc


def page(details, total_pages=1, refreshed="2026-11-04T10:00:00Z"):
    return {"transaction_details": details, "total_pages": total_pages, "total_items": len(details),
            "last_refreshed_datetime": refreshed}


# ---------------------------------------------------------------- windows
def test_windows_cover_the_range_without_gaps_or_overlap_and_stay_under_31_days():
    s, e = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 4, 1, 23, 59, 59, tzinfo=UTC)
    w = split_windows(s, e)
    assert w[0][0] == s and w[-1][1] == e and len(w) == 3
    for (a, b), (c, d) in zip(w, w[1:]):
        assert c == b + timedelta(seconds=1)
    assert all((b - a).days <= 30 for a, b in w)


def test_short_range_is_one_window():
    s = datetime(2026, 10, 1, tzinfo=UTC)
    assert len(split_windows(s, s + timedelta(days=10))) == 1


def test_date_format_is_rfc3339_utc_with_seconds():
    assert fmt(datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC)) == "2026-10-01T00:00:00Z"


# ---------------------------------------------------------------- fetching
def test_request_parameters_match_the_documented_endpoint():
    fake = FakePayPal().on("GET", PATH, page([detail()]))
    client, _ = make(fake)
    fetch_transactions(client, date(2026, 10, 1), date(2026, 10, 31), now=NOW)
    (req,) = fake.api_requests()
    q = dict(req.url.params)
    assert q == {"start_date": "2026-10-01T00:00:00Z", "end_date": "2026-10-31T23:59:59Z",
                 "fields": "all", "page_size": "500", "page": "1"}


def test_long_ranges_make_one_call_per_window():
    fake = FakePayPal().on("GET", PATH, page([]))
    client, _ = make(fake)
    b = fetch_transactions(client, date(2026, 6, 1), date(2026, 10, 31), now=NOW)
    assert b.windows == 5 and len(fake.api_requests()) == 5     # 153 days -> five 31-day windows
    starts = [r.url.params["start_date"] for r in fake.api_requests()]
    assert starts == sorted(starts)


def test_pages_are_followed_until_total_pages():
    fake = FakePayPal().on("GET", PATH, page([detail(tid="A")], 3), page([detail(tid="B")], 3), page([detail(tid="C")], 3))
    client, _ = make(fake)
    b = fetch_transactions(client, date(2026, 10, 1), date(2026, 10, 31), now=NOW)
    assert [d["transaction_info"]["transaction_id"] for d in b.details] == ["A", "B", "C"]
    assert [r.url.params["page"] for r in fake.api_requests()] == ["1", "2", "3"] and b.pages == 3


def test_duplicate_transaction_ids_across_pages_are_collapsed():
    fake = FakePayPal().on("GET", PATH, page([detail(tid="A")], 2), page([detail(tid="A"), detail(tid="B")], 2))
    client, _ = make(fake)
    assert len(fetch_transactions(client, date(2026, 10, 1), date(2026, 10, 31), now=NOW).details) == 2


def test_future_end_date_is_clamped_with_a_note():
    fake = FakePayPal().on("GET", PATH, page([]))
    client, _ = make(fake)
    b = fetch_transactions(client, date(2026, 11, 1), date(2026, 12, 31), now=NOW)
    assert fake.api_requests()[0].url.params["end_date"] == "2026-11-04T12:00:00Z"
    assert any("in the future" in n for n in b.notes)


def test_start_after_end_is_a_friendly_error():
    client, _ = make(FakePayPal())
    with pytest.raises(ValueError, match="start date needs to be before"):
        fetch_transactions(client, date(2026, 10, 31), date(2026, 10, 1), now=NOW)


def test_old_ranges_get_the_three_year_note_and_the_delay_note():
    fake = FakePayPal().on("GET", PATH, page([]))
    client, _ = make(fake)
    b = fetch_transactions(client, date(2022, 1, 1), date(2022, 1, 20), now=NOW)
    assert any("three years" in n for n in b.notes) and any("3 hours" in n for n in b.notes)


def test_last_refreshed_is_reported():
    fake = FakePayPal().on("GET", PATH, page([], refreshed="2026-11-04T09:30:00Z"))
    client, _ = make(fake)
    assert fetch_transactions(client, date(2026, 10, 1), date(2026, 10, 5), now=NOW).last_refreshed == "2026-11-04T09:30:00Z"


def test_missing_search_scope_is_named_on_403():
    fake = FakePayPal().on("GET", PATH, (403, {"name": "NOT_AUTHORIZED", "message": "Authorization failed due to insufficient permissions."}))
    fake.token_responses = [token_body(scope="openid https://uri.paypal.com/services/subscriptions")]
    client, _ = make(fake)
    with pytest.raises(PayPalPermissionError) as e:
        fetch_transactions(client, date(2026, 10, 1), date(2026, 10, 5), now=NOW)
    assert e.value.missing_scope == SCOPE_SEARCH and SCOPE_SEARCH in e.value.user_message


# ---------------------------------------------------------------- normalising
def test_subscription_payment_becomes_a_payer_side_debit():
    df, dropped = to_transactions([detail(value="10.99", item="Streamly Plus", sub_id="I-ABC", tid="TX9")])
    r = df.iloc[0]
    assert r.amount == -10.99 and r.currency == "USD" and r.source == "paypal_api" and bool(r.via_paypal)
    assert r.merchant_raw == "Streamly Plus" and r.source_ref == "TX9" and r.paypal_subscription_id == "I-ABC"
    assert r.date == date(2026, 10, 5) and dropped == {}


def test_merchant_perspective_keeps_paypals_sign_and_uses_the_payer_name():
    df, _ = to_transactions([detail(value="10.99", item="Streamly Plus")], perspective="merchant")
    assert df.iloc[0].amount == 10.99 and df.iloc[0].merchant_raw == "Pat Buyer"


def test_bad_perspective_rejected():
    with pytest.raises(ValueError):
        to_transactions([], perspective="nope")


@pytest.mark.parametrize("code", ["T0400", "T0200", "T2107", "T1100", "T0501"])
def test_non_payment_event_codes_are_dropped(code):
    df, dropped = to_transactions([detail(code=code)])
    assert df.empty and sum(dropped.values()) == 1


@pytest.mark.parametrize("status,kept", [("S", True), ("P", True), ("D", False), ("V", False)])
def test_status_filter(status, kept):
    df, dropped = to_transactions([detail(status=status)])
    assert (len(df) == 1) is kept and (not kept) == bool(dropped)


def test_unreadable_rows_are_skipped_not_fatal():
    bad = detail()
    bad["transaction_info"]["transaction_amount"] = {}
    df, dropped = to_transactions([bad, detail(tid="OK")])
    assert len(df) == 1 and dropped == {"unreadable amount or date": 1}


def test_label_fallback_order():
    cart = to_transactions([detail(item="Cart Name", subject="Subj", note="Note")])[0].iloc[0].merchant_raw
    subj = to_transactions([detail(subject="Subj", note="Note")])[0].iloc[0].merchant_raw
    note = to_transactions([detail(note="Note")])[0].iloc[0].merchant_raw
    none = to_transactions([detail()])[0].iloc[0].merchant_raw
    assert (cart, subj, note, none) == ("Cart Name", "Subj", "Note", "PayPal payment")


def test_non_subscription_reference_types_do_not_set_a_subscription_id():
    d = detail()
    d["transaction_info"]["paypal_reference_id"], d["transaction_info"]["paypal_reference_id_type"] = "5TY1", "ODR"
    assert to_transactions([d])[0].iloc[0].paypal_subscription_id == ""


def test_output_shape_ids_and_repeat_flags():
    rows = [detail(tid="A", when="2026-10-05T01:00:00+0000"), detail(tid="B", when="2026-10-05T09:00:00+0000")]
    df, _ = to_transactions(rows)
    assert list(df.columns) == PAYPAL_COLUMNS and df.id.is_unique and df.possible_duplicate.all()
    assert df.id.tolist() == to_transactions(rows)[0].id.tolist()


def test_empty_input_gives_empty_table_with_columns():
    df, dropped = to_transactions([])
    assert df.empty and list(df.columns) == PAYPAL_COLUMNS and dropped == {}


def test_paypal_rows_feed_straight_into_recurring_detection():
    months = [date(2026, m, 5) for m in range(5, 11)]
    rows = [detail(tid=f"T{i}", when=f"{d.isoformat()}T10:00:00+0000", item="CloudBox Storage", sub_id="I-1")
            for i, d in enumerate(months)]
    df, _ = to_transactions(rows)
    subs = detect_recurring(df).subscriptions
    assert len(subs) == 1 and subs.iloc[0].via_paypal and subs.iloc[0].merchant == "CloudBox Storage"
