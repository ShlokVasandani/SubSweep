"""Seeding is state-changing: confirmation gate, correct bodies, idempotent reruns, no secrets saved."""
import json

import pytest

from subsweep.paypal import ConfirmationRequired
from subsweep.paypal.seed import (
    DEMO_PLANS, load_registry, plan_actions, plan_body, registry_subscription_ids, request_id, seed,
    subscription_body, wait_until_active,
)
from tests.paypal_helpers import FakePayPal, make

SUBS = [(201, {"id": f"I-{i}", "status": "APPROVAL_PENDING",
               "links": [{"rel": "self", "href": "https://x/self"}, {"rel": "approve", "href": f"https://sandbox.paypal.com/approve/{i}"}]})
        for i in range(1, 5)]


def fake():
    return (FakePayPal().on("POST", "/v1/catalogs/products", (201, {"id": "PROD-1"}))
            .on("POST", "/v1/billing/plans", *[(201, {"id": f"P-{i}"}) for i in range(1, 5)])
            .on("POST", "/v1/billing/subscriptions", *SUBS))


YES = lambda actions: True
NO = lambda actions: False


def test_nothing_is_sent_without_confirmation(tmp_path):
    f = fake()
    client, _ = make(f)
    with pytest.raises(ConfirmationRequired):
        seed(client, registry_path=tmp_path / "r.json", confirm=None)
    with pytest.raises(ConfirmationRequired):
        seed(client, registry_path=tmp_path / "r.json", confirm=NO)
    assert f.requests == [] and not (tmp_path / "r.json").exists()


def test_confirmation_receives_the_exact_list_of_actions(tmp_path):
    seen = []
    client, _ = make(fake())
    seed(client, registry_path=tmp_path / "r.json", confirm=lambda a: seen.append(a) or True)
    assert seen[0][0] == "create product 'SubSweep Demo Services'" and len(seen[0]) == 9


def test_creates_product_then_plans_then_subscriptions_in_order(tmp_path):
    f = fake()
    client, _ = make(f)
    reg = seed(client, registry_path=tmp_path / "r.json", confirm=YES)
    calls = [(r.method, r.url.path) for r in f.api_requests()]
    assert calls[0] == ("POST", "/v1/catalogs/products")
    assert calls[1:3] == [("POST", "/v1/billing/plans"), ("POST", "/v1/billing/subscriptions")]
    assert calls.count(("POST", "/v1/billing/plans")) == 4 and calls.count(("POST", "/v1/billing/subscriptions")) == 4
    assert reg["product_id"] == "PROD-1" and reg["subscriptions"]["fitpass"]["id"] == "I-4"
    assert reg["subscriptions"]["cloudbox"]["approve_url"].endswith("/approve/3")


def test_request_bodies_match_the_documented_schema():
    body = plan_body("PROD-1", DEMO_PLANS[0])
    assert set(body) >= {"product_id", "name", "billing_cycles", "payment_preferences"}
    cyc = body["billing_cycles"][0]
    assert cyc["frequency"] == {"interval_unit": "MONTH", "interval_count": 1} and cyc["tenure_type"] == "REGULAR"
    assert cyc["sequence"] == 1 and cyc["pricing_scheme"]["fixed_price"] == {"value": "9.99", "currency_code": "USD"}
    assert body["payment_preferences"]["auto_bill_outstanding"] is True
    assert plan_body("P", DEMO_PLANS[1])["billing_cycles"][0]["frequency"]["interval_unit"] == "YEAR"
    sub = subscription_body("P-1", "buyer@example.com", "https://r", "https://c")
    assert sub["plan_id"] == "P-1" and sub["subscriber"] == {"email_address": "buyer@example.com"}
    ctx = sub["application_context"]
    assert ctx["return_url"] == "https://r" and ctx["cancel_url"] == "https://c" and ctx["user_action"] == "SUBSCRIBE_NOW"
    assert "subscriber" not in subscription_body("P-1", None, "https://r", "https://c")


def test_idempotency_headers_are_sent_and_stable(tmp_path):
    f = fake()
    client, _ = make(f)
    seed(client, registry_path=tmp_path / "r.json", confirm=YES)
    ids = [r.headers["PayPal-Request-Id"] for r in f.api_requests()]
    assert len(ids) == len(set(ids)) == 9 and all(r.headers["Prefer"] == "return=representation" for r in f.api_requests())
    assert request_id("plan", "P", "k") == request_id("plan", "P", "k") != request_id("plan", "P", "j")


def test_rerun_with_a_complete_registry_sends_nothing(tmp_path):
    client, _ = make(fake())
    seed(client, registry_path=tmp_path / "r.json", confirm=YES)
    f2 = fake()
    client2, _ = make(f2)
    reg = seed(client2, registry_path=tmp_path / "r.json", confirm=None)      # nothing to do -> no confirmation needed
    assert f2.requests == [] and len(reg["subscriptions"]) == 4


def test_partial_registry_only_creates_what_is_missing(tmp_path):
    client, _ = make(fake())
    seed(client, registry_path=tmp_path / "r.json", confirm=YES)
    reg = load_registry(tmp_path / "r.json")
    del reg["subscriptions"]["fitpass"]
    (tmp_path / "r.json").write_text(json.dumps(reg))
    assert plan_actions(load_registry(tmp_path / "r.json")) == ["create subscription to 'FitPass Gym' (needs buyer approval)"]
    f2 = FakePayPal().on("POST", "/v1/billing/subscriptions", SUBS[3])
    seed(make(f2)[0], registry_path=tmp_path / "r.json", confirm=YES)
    assert [(r.method, r.url.path) for r in f2.api_requests()] == [("POST", "/v1/billing/subscriptions")]


def test_progress_is_saved_even_if_a_later_step_fails(tmp_path):
    f = (FakePayPal().on("POST", "/v1/catalogs/products", (201, {"id": "PROD-1"}))
         .on("POST", "/v1/billing/plans", (201, {"id": "P-1"}), (422, {"name": "UNPROCESSABLE_ENTITY", "message": "bad plan"}))
         .on("POST", "/v1/billing/subscriptions", SUBS[0]))
    from subsweep.paypal import PayPalError
    with pytest.raises(PayPalError, match="bad plan"):
        seed(make(f)[0], registry_path=tmp_path / "r.json", confirm=YES)
    reg = load_registry(tmp_path / "r.json")
    assert reg["product_id"] == "PROD-1" and "streamly_monthly" in reg["plans"]


def test_registry_never_contains_secrets(tmp_path):
    client, _ = make(fake())
    seed(client, registry_path=tmp_path / "r.json", buyer_email="b@example.com", confirm=YES)
    text = (tmp_path / "r.json").read_text()
    assert "SUPER_SECRET_VALUE" not in text and "CLIENT_ID_123456" not in text and "tok-" not in text and "b@example.com" not in text


def test_registry_subscription_ids_helper(tmp_path):
    seed(make(fake())[0], registry_path=tmp_path / "r.json", confirm=YES)
    assert registry_subscription_ids(tmp_path / "r.json") == ["I-1", "I-2", "I-3", "I-4"]
    assert registry_subscription_ids(tmp_path / "missing.json") == []


def test_wait_until_active_polls_read_only_until_approved(tmp_path):
    reg = {"subscriptions": {"a": {"id": "I-1"}, "b": {"id": "I-2"}}}
    f = (FakePayPal().on("GET", "/v1/billing/subscriptions/I-1", {"id": "I-1", "status": "ACTIVE"})
         .on("GET", "/v1/billing/subscriptions/I-2", {"id": "I-2", "status": "APPROVAL_PENDING"}, {"id": "I-2", "status": "ACTIVE"}))
    client, clock = make(f)
    updates = []
    st = wait_until_active(client, reg, sleep=clock.sleep, clock=clock.now, on_update=updates.append)
    assert st == {"a": "ACTIVE", "b": "ACTIVE"} and len(updates) == 2 and clock.sleeps == [10]
    assert {r.method for r in f.api_requests()} == {"GET"}


def test_wait_times_out_with_current_statuses():
    reg = {"subscriptions": {"a": {"id": "I-1"}}}
    f = FakePayPal().on("GET", "/v1/billing/subscriptions/I-1", {"id": "I-1", "status": "APPROVAL_PENDING"})
    client, clock = make(f)

    def sleep(s):
        clock.t += 400
    st = wait_until_active(client, reg, timeout_s=600, sleep=sleep, clock=clock.now)
    assert st == {"a": "APPROVAL_PENDING"}


def test_demo_plans_support_the_planned_demo_story():
    names = [p.name for p in DEMO_PLANS]
    assert "Streamly Plus" in names and "Streamly Plus (Annual)" in names     # duplicate pair
    assert any(p.key == "fitpass" and "cancel" in p.note for p in DEMO_PLANS)  # mismatch demo


def test_plan_is_saved_even_if_its_subscription_fails(tmp_path):
    f = (FakePayPal().on("POST", "/v1/catalogs/products", (201, {"id": "PROD-1"}))
         .on("POST", "/v1/billing/plans", (201, {"id": "P-1"}))
         .on("POST", "/v1/billing/subscriptions", (422, {"name": "UNPROCESSABLE_ENTITY", "message": "bad subscriber"})))
    from subsweep.paypal import PayPalError
    with pytest.raises(PayPalError, match="bad subscriber"):
        seed(make(f)[0], registry_path=tmp_path / "r.json", confirm=YES)
    reg = load_registry(tmp_path / "r.json")
    assert reg["plans"]["streamly_monthly"]["id"] == "P-1" and reg["subscriptions"] == {}
