"""Subscriptions API (read-only): live status, plans, error isolation. HTTP is mocked."""
from datetime import datetime, timezone

import pytest

from subsweep.paypal.subscriptions import (
    LiveSubscription, get_plan, get_subscription, list_plans, live_status, parse_time, plan_summary,
)
from tests.paypal_helpers import FakePayPal, make

SUB = {
    "id": "I-AAA", "plan_id": "P-1", "status": "ACTIVE", "status_update_time": "2026-10-02T08:00:00Z",
    "start_time": "2026-10-02T08:00:00Z",
    "billing_info": {"next_billing_time": "2026-11-02T10:00:00Z", "failed_payments_count": 1,
                     "last_payment": {"amount": {"currency_code": "USD", "value": "9.99"}, "time": "2026-10-02T08:05:00Z"}},
}
PLAN = {"id": "P-1", "name": "Streamly Plus", "status": "ACTIVE", "product_id": "PROD-1", "billing_cycles": [
    {"tenure_type": "TRIAL", "sequence": 1, "pricing_scheme": {"fixed_price": {"value": "0", "currency_code": "USD"}},
     "frequency": {"interval_unit": "WEEK", "interval_count": 1}},
    {"tenure_type": "REGULAR", "sequence": 2, "pricing_scheme": {"fixed_price": {"value": "9.99", "currency_code": "USD"}},
     "frequency": {"interval_unit": "MONTH", "interval_count": 1}}]}


def test_subscription_status_next_billing_and_last_payment():
    fake = FakePayPal().on("GET", "/v1/billing/subscriptions/I-AAA", SUB).on("GET", "/v1/billing/plans/P-1", PLAN)
    client, _ = make(fake)
    s = get_subscription(client, "I-AAA")
    assert s.status == "ACTIVE" and s.plan_name == "Streamly Plus" and s.plan_amount == 9.99
    assert s.next_billing_time == datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
    assert (s.last_payment_amount, s.last_payment_currency) == (9.99, "USD")
    assert s.last_payment_time == datetime(2026, 10, 2, 8, 5, tzinfo=timezone.utc)
    assert s.failed_payments_count == 1 and s.plan_interval == "MONTH x1"
    assert s.manage_url == "https://www.sandbox.paypal.com/myaccount/autopay/" and not s.is_ended


@pytest.mark.parametrize("status,ended", [("ACTIVE", False), ("APPROVAL_PENDING", False), ("APPROVED", False),
                                          ("CANCELLED", True), ("SUSPENDED", True), ("EXPIRED", True)])
def test_ended_statuses(status, ended):
    assert LiveSubscription(id="I-1", status=status).is_ended is ended


def test_plan_prefers_the_regular_cycle_over_the_trial():
    assert plan_summary(PLAN)["amount"] == 9.99 and plan_summary(PLAN)["interval"] == "MONTH x1"


def test_missing_optional_fields_do_not_crash():
    fake = FakePayPal().on("GET", "/v1/billing/subscriptions/I-B", {"id": "I-B", "status": "CANCELLED"})
    s = get_subscription(make(fake)[0], "I-B")
    assert s.status == "CANCELLED" and s.next_billing_time is None and s.last_payment_amount is None and s.plan_name == ""


def test_plan_lookup_failure_still_returns_the_subscription():
    fake = FakePayPal().on("GET", "/v1/billing/subscriptions/I-AAA", SUB)      # plan route absent -> 404
    s = get_subscription(make(fake)[0], "I-AAA")
    assert s.status == "ACTIVE" and s.plan_name == ""


def test_plans_are_cached_across_subscriptions():
    fake = (FakePayPal().on("GET", "/v1/billing/subscriptions/I-1", {**SUB, "id": "I-1"})
            .on("GET", "/v1/billing/subscriptions/I-2", {**SUB, "id": "I-2"}).on("GET", "/v1/billing/plans/P-1", PLAN))
    live, errors = live_status(make(fake)[0], ["I-1", "I-2", "I-1"])
    assert [l.id for l in live] == ["I-1", "I-2"] and errors == {}
    assert sum(1 for r in fake.api_requests() if r.url.path == "/v1/billing/plans/P-1") == 1


def test_one_unknown_subscription_does_not_hide_the_rest():
    fake = FakePayPal().on("GET", "/v1/billing/subscriptions/I-AAA", SUB).on("GET", "/v1/billing/plans/P-1", PLAN)
    live, errors = live_status(make(fake)[0], ["I-AAA", "I-GONE"])
    assert [l.id for l in live] == ["I-AAA"] and "doesn't know this subscription" in errors["I-GONE"]


def test_other_errors_become_friendly_per_item_messages():
    fake = FakePayPal().on("GET", "/v1/billing/subscriptions/I-X", (403, {"name": "NOT_AUTHORIZED"}))
    live, errors = live_status(make(fake)[0], ["I-X"])
    assert live == [] and "isn't permitted" in errors["I-X"]


def test_list_plans_follows_pages_and_filters_by_product():
    fake = FakePayPal().on("GET", "/v1/billing/plans", {"plans": [PLAN], "total_pages": 2}, {"plans": [{**PLAN, "id": "P-2"}], "total_pages": 2})
    plans = list_plans(make(fake)[0], product_id="PROD-1")
    assert [p["id"] for p in plans] == ["P-1", "P-2"]
    assert fake.api_requests()[0].url.params["product_id"] == "PROD-1"


def test_get_plan_summary():
    fake = FakePayPal().on("GET", "/v1/billing/plans/P-1", PLAN)
    assert get_plan(make(fake)[0], "P-1")["name"] == "Streamly Plus"


def test_parse_time_handles_z_and_garbage():
    assert parse_time("2026-10-02T08:00:00Z").tzinfo is not None and parse_time("nonsense") is None and parse_time(None) is None


def test_reading_status_only_ever_sends_get_requests():
    fake = FakePayPal().on("GET", "/v1/billing/subscriptions/I-AAA", SUB).on("GET", "/v1/billing/plans/P-1", PLAN)
    live_status(make(fake)[0], ["I-AAA"])
    assert {r.method for r in fake.api_requests()} == {"GET"}


def test_module_exposes_no_cancel_or_suspend_helpers():
    import subsweep.paypal.subscriptions as m
    assert not [n for n in dir(m) if "cancel" in n.lower() or "suspend" in n.lower()]
