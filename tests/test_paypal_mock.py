"""Mock mode: bundled demo data, the PAYPAL_MODE switch, and the APPROVAL_PENDING fallback."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from subsweep.detect import detect_recurring
from subsweep.findings import DUPLICATE, PAYPAL_MISMATCH, generate_findings
from subsweep.paypal import PayPalNotConfigured, current_mode, get_paypal_view, load_mock
from subsweep.paypal.__main__ import main as paypal_cli
from subsweep.paypal.provider import DEFAULT_MOCK_PATH, MOCK_NOTICE
from subsweep.paypal.sync import match_paypal_subscriptions, merge_sources, source_summary, status_table
from tests.helpers import tx
from tests.paypal_helpers import FakePayPal, detail, make

MOCK = {"PAYPAL_MODE": "mock"}
NAMES = ["Streamly Plus", "Streamly Plus (Annual)", "CloudBox Storage", "FitPass Gym"]
PATH = "/v1/reporting/transactions"


def view(**kw):
    return get_paypal_view(None, environ=kw.pop("environ", MOCK), **kw)


# ---------------------------------------------------------------- mode switch
@pytest.mark.parametrize("env,mode", [({}, "live"), ({"PAYPAL_MODE": ""}, "live"), ({"PAYPAL_MODE": "   "}, "live"), ({"PAYPAL_MODE": "live"}, "live"),
                                      ({"PAYPAL_MODE": "mock"}, "mock"), ({"PAYPAL_MODE": " MOCK "}, "mock")])
def test_mode_parsing(env, mode):
    assert current_mode(env) == mode


def test_unknown_mode_is_a_friendly_error():
    with pytest.raises(PayPalNotConfigured, match="'live' or 'mock'"):
        current_mode({"PAYPAL_MODE": "sandboxish"})


def test_mode_reads_the_real_environment_by_default(monkeypatch):
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    assert current_mode() == "mock"
    monkeypatch.delenv("PAYPAL_MODE")
    assert current_mode() == "live"


def test_mock_mode_needs_no_client_credentials_or_network():
    v = view()
    assert v.is_mock and v.mode == "mock" and v.reason and v.notes == [MOCK_NOTICE]


def test_mock_mode_never_touches_the_network_even_when_a_client_is_given():
    fake = FakePayPal()
    client, _ = make(fake)
    v = get_paypal_view(client, environ=MOCK)
    assert v.is_mock and fake.requests == []


def test_live_mode_without_a_client_explains_both_options():
    with pytest.raises(PayPalNotConfigured, match="PAYPAL_MODE=mock"):
        get_paypal_view(None, environ={})


# ---------------------------------------------------------------- the bundled data file
def test_default_file_is_the_tracked_data_file():
    assert DEFAULT_MOCK_PATH.name == "paypal_mock.json" and DEFAULT_MOCK_PATH.parent.name == "data" and DEFAULT_MOCK_PATH.is_file()


def test_four_demo_subscriptions_with_the_expected_statuses():
    subs = {s.plan_name: s for s in load_mock().subscriptions}
    assert list(subs) == NAMES
    assert [subs[n].status for n in NAMES] == ["ACTIVE", "ACTIVE", "ACTIVE", "CANCELLED"]
    assert all(s.data_source == "mock" and s.id.startswith("I-MOCK") for s in subs.values())
    assert subs["Streamly Plus"].plan_amount == 9.99 and subs["Streamly Plus (Annual)"].plan_amount == 99.99
    assert subs["Streamly Plus (Annual)"].plan_interval == "YEAR x1"


def test_active_subscriptions_have_next_billing_and_last_payment_and_cancelled_has_neither_next_nor_future():
    subs = {s.plan_name: s for s in load_mock().subscriptions}
    for n in NAMES[:3]:
        assert subs[n].next_billing_time is not None and subs[n].last_payment_amount == subs[n].plan_amount
    fit = subs["FitPass Gym"]
    assert fit.next_billing_time is None and fit.status_update_time == datetime(2026, 8, 11, 14, 30, tzinfo=timezone.utc)
    assert fit.manage_url.endswith("/myaccount/autopay/")


def test_each_subscription_has_a_few_charges():
    df = view().transactions
    counts = df.groupby("paypal_subscription_id").size()
    assert len(counts) == 4 and counts.min() >= 3
    assert dict(df.merchant_raw.value_counts()) == {"Streamly Plus": 5, "CloudBox Storage": 5, "FitPass Gym": 5, "Streamly Plus (Annual)": 3}


def test_mock_transactions_are_payer_side_debits_labelled_as_mock():
    df = view().transactions
    assert (df.amount < 0).all() and df.source.eq("paypal_mock").all() and df.via_paypal.all()
    assert df.id.is_unique and (df.paypal_subscription_id != "").all()


def test_merchant_perspective_keeps_paypals_sign():
    assert (view(perspective="merchant").transactions.amount > 0).all()


def test_date_range_filters_the_demo_charges():
    df = view(start=date(2026, 9, 1), end=date(2026, 9, 30)).transactions
    assert len(df) == 3 and df.date.between(date(2026, 9, 1), date(2026, 9, 30)).all()
    assert view(start=date(2030, 1, 1)).transactions.empty
    assert len(view(start=date(2026, 1, 1), end=date(2026, 12, 31)).transactions) == 16      # 15 monthly + annual 2026; 2024/2025 excluded


def test_data_file_has_no_emails_or_secrets():
    text = DEFAULT_MOCK_PATH.read_text()
    assert "@" not in text and "secret" not in text.lower() and "access_token" not in text


def test_custom_file_via_argument_or_environment(tmp_path):
    f = tmp_path / "m.json"
    f.write_text(json.dumps({"subscriptions": [{"id": "I-X", "plan_name": "Solo", "status": "ACTIVE"}], "transactions": []}))
    assert [s.id for s in load_mock(f).subscriptions] == ["I-X"]
    assert [s.id for s in load_mock(environ={"PAYPAL_MOCK_FILE": str(f)}).subscriptions] == ["I-X"]
    assert view(environ={**MOCK, "PAYPAL_MOCK_FILE": str(f)}).transactions.empty


def test_missing_or_broken_file_gives_friendly_errors(tmp_path):
    with pytest.raises(PayPalNotConfigured, match="wasn't found"):
        load_mock(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(PayPalNotConfigured, match="valid JSON"):
        load_mock(bad)


def test_manage_url_can_be_overridden_for_mock_data():
    subs = load_mock(environ={"PAYPAL_MANAGE_URL": "https://example.test/manage"}).subscriptions
    assert {s.manage_url for s in subs} == {"https://example.test/manage"}


# ---------------------------------------------------------------- mock data drives the findings
def test_mock_data_produces_the_duplicate_and_the_cancelled_but_charging_mismatch():
    v = view()
    subs = detect_recurring(v.transactions).subscriptions
    assert set(subs.merchant) == set(NAMES) and subs.via_paypal.all() and subs.active.all()
    matches = match_paypal_subscriptions(subs, v.transactions, v.live)
    assert len(matches) == 4
    rep = generate_findings(subs, v.transactions, paypal_status=matches)
    (dup,) = [f for f in rep.findings if f.type == DUPLICATE]
    (mm,) = [f for f in rep.findings if f.type == PAYPAL_MISMATCH]
    assert dup.merchant == "Streamly Plus" and len(dup.subscription_ids) == 2
    assert mm.merchant == "FitPass Gym" and mm.severity == "high" and mm.evidence["kind"] == "cancelled_but_charged"
    assert mm.evidence["charges_after"] == 2 and mm.evidence["total_after"] == pytest.approx(48.0)
    assert "cancelled since 11 Aug 2026" in mm.reason and "10 Oct 2026" in mm.reason
    assert mm.evidence["manage_url"].endswith("/myaccount/autopay/")


def test_only_fitpass_is_a_mismatch():
    v = view()
    subs = detect_recurring(v.transactions).subscriptions
    rep = generate_findings(subs, v.transactions, paypal_status=match_paypal_subscriptions(subs, v.transactions, v.live))
    assert [f.merchant for f in rep.findings if f.type == PAYPAL_MISMATCH] == ["FitPass Gym"]


def test_status_table_shows_four_rows_with_one_cancelled():
    v = view()
    subs = detect_recurring(v.transactions).subscriptions
    rows = status_table(subs, match_paypal_subscriptions(subs, v.transactions, v.live))
    assert sorted(r["status"] for r in rows) == ["ACTIVE", "ACTIVE", "ACTIVE", "CANCELLED"]
    assert all(r["manage_url"] for r in rows)


def test_mock_rows_merge_with_an_upload_and_are_counted_as_demo_not_live():
    up = tx([(date(2026, 10, 1), "Coffee", -3.0, False)])
    merged = merge_sources(up, view().transactions)
    assert source_summary(merged) == {"upload": 1, "paypal_api": 0, "paypal_mock": 18, "confirmed_by_paypal": 0}


def test_bank_paypal_row_is_confirmed_by_a_mock_payment():
    up = tx([(date(2026, 10, 6), "PAYPAL *STREAMLY", -9.99, True)])
    merged = merge_sources(up, view().transactions)
    row = merged[merged.source == "upload:test"].iloc[0]
    assert bool(row.confirmed_by_paypal_api) and row.paypal_subscription_id.startswith("I-MOCK")


# ---------------------------------------------------------------- APPROVAL_PENDING fallback (live mode)
def live_fake(statuses, plan_name="Streamly Plus"):
    f = FakePayPal().on("GET", PATH, {"transaction_details": [], "total_pages": 1})
    for i, st in enumerate(statuses, 1):
        f.on("GET", f"/v1/billing/subscriptions/I-{i}", {"id": f"I-{i}", "plan_id": "P-1", "status": st})
    f.on("GET", "/v1/billing/plans/P-1", {"id": "P-1", "name": plan_name, "billing_cycles": []})
    return f


def test_pending_approval_falls_back_to_demo_data_and_says_why():
    f = live_fake(["APPROVAL_PENDING", "APPROVAL_PENDING"])
    client, _ = make(f)
    v = get_paypal_view(client, registry_ids=["I-1", "I-2"], environ={})
    assert v.is_mock and "2 of 2" in v.reason and "APPROVAL_PENDING" in v.reason and "waiting for buyer approval" in v.reason
    assert v.transactions.source.eq("paypal_mock").all() and len(v.live) == 4 and v.notes == [MOCK_NOTICE]
    assert not [r for r in f.api_requests() if r.url.path == PATH]          # no live transaction fetch was wasted


def test_one_pending_among_active_still_switches_the_whole_view_to_demo():
    client, _ = make(live_fake(["ACTIVE", "APPROVAL_PENDING"]))
    v = get_paypal_view(client, registry_ids=["I-1", "I-2"], environ={})
    assert v.is_mock and "1 of 2" in v.reason
    assert all(l.data_source == "mock" for l in v.live)          # never a mix of live and demo subscriptions


def test_all_active_uses_live_data_not_the_demo():
    f = live_fake(["ACTIVE", "ACTIVE"])
    f.routes[("GET", PATH)] = [{"transaction_details": [detail(tid="T1", sub_id="I-1", item="Streamly Plus")], "total_pages": 1}]
    client, _ = make(f)
    v = get_paypal_view(client, registry_ids=["I-1", "I-2"], environ={})
    assert not v.is_mock and v.reason == "" and [l.status for l in v.live] == ["ACTIVE", "ACTIVE"]
    assert v.transactions.source.eq("paypal_api").all() and all(l.data_source == "live" for l in v.live)


def test_approved_but_not_yet_active_is_not_treated_as_pending():
    client, _ = make(live_fake(["APPROVED"]))
    assert not get_paypal_view(client, registry_ids=["I-1"], environ={}).is_mock


def test_without_a_registry_ids_are_discovered_from_transactions_then_checked():
    f = live_fake(["APPROVAL_PENDING"])
    f.routes[("GET", PATH)] = [{"transaction_details": [detail(tid="T1", sub_id="I-1", item="Streamly Plus")], "total_pages": 1}]
    client, _ = make(f)
    v = get_paypal_view(client, registry_ids=[], environ={})
    assert v.is_mock                                           # discovered I-1, found it pending, fell back


def test_real_lookup_errors_are_not_hidden_by_the_fallback():
    f = FakePayPal().on("GET", PATH, {"transaction_details": [], "total_pages": 1})
    client, _ = make(f)                                        # I-1 route absent -> 404 for that subscription
    v = get_paypal_view(client, registry_ids=["I-1"], environ={})
    assert not v.is_mock and "I-1" in v.errors and v.live == []


def test_live_view_carries_transaction_search_notes():
    f = live_fake(["ACTIVE"])
    client, _ = make(f)
    assert any("3 hours" in n for n in get_paypal_view(client, registry_ids=["I-1"], environ={}).notes)


def test_mock_fallback_respects_the_requested_date_range():
    client, _ = make(live_fake(["APPROVAL_PENDING"]))
    v = get_paypal_view(client, date(2026, 9, 1), date(2026, 9, 30), registry_ids=["I-1"], environ={})
    assert v.is_mock and len(v.transactions) == 3


# ---------------------------------------------------------------- seed / approval flow untouched
def test_seed_module_knows_nothing_about_mock_mode():
    src = (Path(__file__).resolve().parents[1] / "subsweep" / "paypal" / "seed.py").read_text()
    assert "mock" not in src.lower() and "provider" not in src


# ---------------------------------------------------------------- CLI
def test_cli_status_in_mock_mode_needs_no_credentials(monkeypatch, capsys):
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    assert paypal_cli(["status"]) == 0
    out = capsys.readouterr().out
    assert "[DEMO DATA]" in out and "FitPass Gym" in out and "CANCELLED" in out and "myaccount/autopay" in out


def test_cli_transactions_in_mock_mode_labels_the_source(monkeypatch, capsys):
    monkeypatch.setenv("PAYPAL_MODE", "mock")
    assert paypal_cli(["transactions", "--days", "100"]) == 0
    out = capsys.readouterr().out
    assert "[DEMO DATA]" in out and "paypal_mock" in out and "paypal_api" not in out


def test_cli_live_mode_without_credentials_is_still_a_friendly_error(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PAYPAL_MODE", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    assert paypal_cli(["status"]) == 2
    assert "isn't connected yet" in capsys.readouterr().out


def test_a_malformed_date_in_the_mock_file_is_skipped_not_fatal(tmp_path):
    bad = detail(tid="BAD", when="not-a-date", sub_id="I-1")
    good = detail(tid="OK", when="2026-10-05T09:00:00+0000", sub_id="I-1")
    f = tmp_path / "m.json"
    f.write_text(json.dumps({"subscriptions": [], "transactions": [bad, good]}))
    for kw in ({}, {"start": date(2026, 1, 1), "end": date(2026, 12, 31)}):
        v = get_paypal_view(None, environ={"PAYPAL_MODE": "mock"}, mock_path=f, **kw)
        assert v.transactions.source_ref.tolist() == ["OK"]
