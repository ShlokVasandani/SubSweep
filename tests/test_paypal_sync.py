"""Merging uploads with PayPal API rows, matching subscriptions, and the PAYPAL_MISMATCH finding."""
from datetime import date, datetime, timezone

import pytest

from subsweep.detect import detect_recurring, load_rules
from subsweep.findings import PAYPAL_MISMATCH, generate_findings
from subsweep.paypal.subscriptions import LiveSubscription
from subsweep.paypal.sync import match_paypal_subscriptions, merge_sources, source_summary, status_table
from subsweep.paypal.transactions import to_transactions
from tests.helpers import series, tx
from tests.paypal_helpers import detail

D = date
UTC = timezone.utc


def api(*rows):
    """rows: (date_iso, amount_positive_value, tid, item, sub_id|None)"""
    ds = [detail(value=f"{v:.2f}", when=f"{d}T10:00:00+0000", tid=t, item=i, sub_id=s) for d, v, t, i, s in rows]
    return to_transactions(ds)[0]


def upload(rows, ref=None):
    df = tx(rows)
    if ref:
        df["source_ref"] = ref
    return df


def live(**kw):
    base = dict(id="I-1", status="ACTIVE", plan_id="P-1", plan_name="Streamly Plus", plan_amount=9.99, plan_currency="USD",
                manage_url="https://www.sandbox.paypal.com/myaccount/autopay/")
    base.update(kw)
    return LiveSubscription(**base)


# ---------------------------------------------------------------- merge
def test_api_row_with_same_transaction_id_is_dropped_and_upload_row_confirmed():
    up = upload([(D(2026, 10, 5), "Streamly Plus", -9.99, True)], ref=["TXA"])
    m = merge_sources(up, api(("2026-10-05", 9.99, "TXA", "Streamly Plus", "I-1")))
    assert len(m) == 1 and m.iloc[0].source == "upload:test"
    assert bool(m.iloc[0].confirmed_by_paypal_api) and m.iloc[0].paypal_subscription_id == "I-1"


def test_transaction_id_match_works_even_when_the_dates_are_far_apart():
    up = upload([(D(2026, 10, 1), "Streamly Plus", -9.99, True)], ref=["TXA"])
    m = merge_sources(up, api(("2026-10-25", 9.99, "TXA", "Streamly Plus", "I-1")))
    assert len(m) == 1 and bool(m.iloc[0].confirmed_by_paypal_api) and m.iloc[0].date == D(2026, 10, 1)


def test_bank_paypal_row_matches_api_row_by_amount_and_date_window():
    up = upload([(D(2026, 10, 5), "PAYPAL *STREAMLY", -9.99, True)])
    m = merge_sources(up, api(("2026-10-07", 9.99, "TXA", "Streamly Plus", "I-1")))
    assert len(m) == 1 and bool(m.iloc[0].confirmed_by_paypal_api) and m.iloc[0].paypal_txn_id == "TXA"
    assert m.iloc[0].paypal_subscription_id == "I-1"


def test_date_outside_window_does_not_match_and_both_rows_are_kept():
    up = upload([(D(2026, 10, 5), "PAYPAL *STREAMLY", -9.99, True)])
    m = merge_sources(up, api(("2026-10-20", 9.99, "TXA", "Streamly Plus", "I-1")))
    assert len(m) == 2 and sorted(m.source) == ["paypal_api", "upload:test"]


def test_window_is_configurable():
    up = upload([(D(2026, 10, 5), "PAYPAL *STREAMLY", -9.99, True)])
    a = api(("2026-10-12", 9.99, "TXA", "Streamly Plus", "I-1"))
    assert len(merge_sources(up, a)) == 2 and len(merge_sources(up, a, window_days=10)) == 1


def test_non_paypal_bank_rows_are_never_matched():
    up = upload([(D(2026, 10, 5), "Some Shop", -9.99, False)])
    assert len(merge_sources(up, api(("2026-10-05", 9.99, "TXA", "Streamly Plus", None)))) == 2


def test_different_amount_or_currency_does_not_match():
    up = upload([(D(2026, 10, 5), "PAYPAL *STREAMLY", -9.99, True)])
    assert len(merge_sources(up, api(("2026-10-05", 10.99, "TXA", "Streamly Plus", None)))) == 2
    eur = upload([(D(2026, 10, 5), "PAYPAL *STREAMLY", -9.99, True, "EUR")])
    assert len(merge_sources(eur, api(("2026-10-05", 9.99, "TXA", "Streamly Plus", None)))) == 2


def test_each_bank_row_matches_at_most_one_api_row_so_real_repeats_survive():
    up = upload([(D(2026, 10, 5), "PAYPAL *STREAMLY", -9.99, True)])
    m = merge_sources(up, api(("2026-10-05", 9.99, "TXA", "Streamly Plus", None), ("2026-10-05", 9.99, "TXB", "Streamly Plus", None)))
    assert len(m) == 2 and m.confirmed_by_paypal_api.sum() == 1 and (m.source == "paypal_api").sum() == 1


def test_unmatched_api_rows_are_added_with_their_own_source_label():
    m = merge_sources(upload([(D(2026, 10, 1), "Coffee", -3.0, False)]), api(("2026-10-05", 9.99, "TXA", "Streamly Plus", "I-1")))
    assert source_summary(m) == {"upload": 1, "paypal_api": 1, "paypal_mock": 0, "confirmed_by_paypal": 0}


def test_merge_with_empty_sides():
    a = api(("2026-10-05", 9.99, "TXA", "Streamly Plus", "I-1"))
    assert len(merge_sources(upload([]), a)) == 1
    assert len(merge_sources(upload([(D(2026, 10, 1), "Coffee", -3.0, False)]), a.iloc[0:0])) == 1
    assert source_summary(merge_sources(upload([]), a.iloc[0:0])) == {"upload": 0, "paypal_api": 0, "paypal_mock": 0, "confirmed_by_paypal": 0}


def test_merged_table_is_sorted_and_ids_are_unique():
    m = merge_sources(upload([(D(2026, 10, 9), "A", -1.0, False), (D(2026, 10, 1), "B", -2.0, False)]),
                      api(("2026-10-05", 9.99, "TXA", "Streamly Plus", "I-1")))
    assert m.date.is_monotonic_increasing and m.id.is_unique


# ---------------------------------------------------------------- matching to live subscriptions
def paypal_sub(merchant="Streamly Plus", amount=9.99, sub_id="I-1", n=9, start=D(2026, 1, 5)):
    rows = []
    d = start
    from datetime import timedelta
    for i in range(n):
        rows.append(detail(value=f"{amount:.2f}", when=f"{(start + timedelta(days=30 * i)).isoformat()}T10:00:00+0000",
                           tid=f"TX{i}", item=merchant, sub_id=sub_id))
    df, _ = to_transactions(rows)
    return df, detect_recurring(df).subscriptions


def test_exact_match_through_the_subscription_id_on_transactions():
    t, subs = paypal_sub()
    m = match_paypal_subscriptions(subs, t, [live(id="I-1"), live(id="I-2")])
    assert m[subs.iloc[0].id].id == "I-1"


def test_fuzzy_match_by_plan_name_and_price_when_no_id_is_present():
    t, subs = paypal_sub(sub_id=None)
    m = match_paypal_subscriptions(subs, t, [live(id="I-7")])
    assert m[subs.iloc[0].id].id == "I-7"


def test_fuzzy_match_needs_the_price_to_agree():
    t, subs = paypal_sub(sub_id=None)
    assert match_paypal_subscriptions(subs, t, [live(plan_amount=19.99)]) == {}


def test_fuzzy_match_needs_a_similar_name():
    t, subs = paypal_sub(sub_id=None)
    assert match_paypal_subscriptions(subs, t, [live(plan_name="Totally Different Gym")]) == {}


def test_non_paypal_streams_are_not_fuzzy_matched():
    t = tx(series("Streamly Plus", D(2026, 1, 5), 30, [9.99] * 9))
    subs = detect_recurring(t).subscriptions
    assert match_paypal_subscriptions(subs, t, [live()]) == {}


def test_one_live_subscription_is_not_assigned_to_two_streams():
    t1, s1 = paypal_sub(sub_id=None)
    t2 = tx(series("Streamly Plus", D(2026, 1, 12), 30, [9.99] * 9, via_paypal=True))
    both = t1.drop(columns=["paypal_subscription_id"]).copy()
    import pandas as pd
    t = pd.concat([both, t2], ignore_index=True)
    t["id"] = [f"u{i}" for i in range(len(t))]
    subs = detect_recurring(t).subscriptions
    assert len(subs) == 2
    assert len(match_paypal_subscriptions(subs, t, [live()])) == 1


def test_status_table_rows_carry_the_manage_link():
    t, subs = paypal_sub()
    lv = live(id="I-1", next_billing_time=datetime(2026, 11, 2, tzinfo=UTC), last_payment_amount=9.99)
    (row,) = status_table(subs, match_paypal_subscriptions(subs, t, [lv]))
    assert row["status"] == "ACTIVE" and row["manage_url"].endswith("/myaccount/autopay/")
    assert row["next_billing_time"].year == 2026 and row["last_payment_amount"] == 9.99


# ---------------------------------------------------------------- PAYPAL_MISMATCH finding
def mismatch_report(status="CANCELLED", since=datetime(2026, 7, 1, tzinfo=UTC), plan_amount=9.99, rules=None, n=9):
    t, subs = paypal_sub(n=n)
    lv = live(status=status, status_update_time=since, plan_amount=plan_amount)
    matches = match_paypal_subscriptions(subs, t, [lv])
    return generate_findings(subs, t, rules=rules, paypal_status=matches), subs


def test_cancelled_at_paypal_but_still_charged_is_flagged_high_with_exact_figures():
    rep, subs = mismatch_report()
    (f,) = [f for f in rep.findings if f.type == PAYPAL_MISMATCH]
    assert f.severity == "high" and f.evidence["kind"] == "cancelled_but_charged"
    assert "cancelled since 1 Jul 2026" in f.reason and "$9.99" in f.reason
    assert f.evidence["charges_after"] == 3 and f.evidence["total_after"] == pytest.approx(29.97)
    assert f.evidence["manage_url"].endswith("/myaccount/autopay/") and f.evidence["paypal_subscription_id"] == "I-1"
    assert f.potential_savings_annual == pytest.approx(9.99 * 12)


@pytest.mark.parametrize("status", ["SUSPENDED", "EXPIRED"])
def test_suspended_and_expired_count_as_ended(status):
    (f,) = [f for f in mismatch_report(status=status)[0].findings if f.type == PAYPAL_MISMATCH]
    assert status.lower() in f.reason


def test_active_at_paypal_is_not_a_mismatch():
    assert [f for f in mismatch_report(status="ACTIVE")[0].findings if f.type == PAYPAL_MISMATCH] == []


def test_charges_before_the_cancellation_are_not_a_mismatch():
    rep, _ = mismatch_report(since=datetime(2026, 12, 1, tzinfo=UTC))
    assert [f for f in rep.findings if f.type == PAYPAL_MISMATCH] == []


def test_grace_days_ignore_the_cancellation_day_charge():
    # last charge is 2026-09-02 (9 charges, 30 days apart); cancelled the same day -> inside the grace window
    rep, _ = mismatch_report(since=datetime(2026, 9, 2, 12, tzinfo=UTC))
    assert [f for f in rep.findings if f.type == PAYPAL_MISMATCH] == []
    strict = load_rules(overrides={"findings": {"paypal": {"grace_days": -1}}})
    rep2, _ = mismatch_report(since=datetime(2026, 9, 2, 12, tzinfo=UTC), rules=strict)
    assert len([f for f in rep2.findings if f.type == PAYPAL_MISMATCH]) == 1


def test_price_difference_is_a_low_informational_finding_with_no_savings():
    rep, _ = mismatch_report(status="ACTIVE", plan_amount=12.99)
    (f,) = [f for f in rep.findings if f.type == PAYPAL_MISMATCH]
    assert f.severity == "low" and f.evidence["kind"] == "amount_differs" and f.potential_savings_annual == 0
    assert "$12.99" in f.reason and "$9.99" in f.reason


def test_small_price_difference_within_tolerance_is_ignored():
    assert [f for f in mismatch_report(status="ACTIVE", plan_amount=10.00)[0].findings if f.type == PAYPAL_MISMATCH] == []


def test_mismatch_counts_in_savings_only_for_cancelled_kind():
    cancelled, _ = mismatch_report()
    price, _ = mismatch_report(status="ACTIVE", plan_amount=12.99)
    assert cancelled.summary["potential_savings_annual"] > 0 and price.summary["potential_savings_annual"] == 0


def test_no_paypal_data_means_no_mismatch_findings():
    t, subs = paypal_sub()
    assert [f for f in generate_findings(subs, t).findings if f.type == PAYPAL_MISMATCH] == []


def test_dismissed_mismatch_leaves_the_totals():
    rep, subs = mismatch_report()
    fid = [f for f in rep.findings if f.type == PAYPAL_MISMATCH][0].id
    t, subs = paypal_sub()
    matches = match_paypal_subscriptions(subs, t, [live(status="CANCELLED", status_update_time=datetime(2026, 7, 1, tzinfo=UTC))])
    after = generate_findings(subs, t, paypal_status=matches, dismissed=[fid])
    assert [f.status for f in after.findings if f.type == PAYPAL_MISMATCH] == ["dismissed"]


def test_ended_streams_never_get_mismatch():
    # statement charges stopped long ago -> subscription inactive -> nothing to flag
    t, subs = paypal_sub(n=3, start=D(2025, 1, 5))
    t2 = tx([(D(2026, 9, 30), "Anchor", -1.0)])
    import pandas as pd
    allt = pd.concat([t, t2], ignore_index=True)
    allt["id"] = [f"z{i}" for i in range(len(allt))]
    s = detect_recurring(allt).subscriptions
    assert generate_findings(s, allt, paypal_status={}).findings == []


def test_known_subscription_ids_merge_registry_and_transactions_without_duplicates():
    from subsweep.paypal.sync import known_subscription_ids
    t, _ = paypal_sub(sub_id="I-9")
    assert known_subscription_ids(t, ["I-1", "I-9"]) == ["I-1", "I-9"]
    assert known_subscription_ids(None, None) == [] and known_subscription_ids(tx([]), ["I-2"]) == ["I-2"]
