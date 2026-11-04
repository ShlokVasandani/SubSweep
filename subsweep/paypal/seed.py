"""Seed the PayPal sandbox with demo billing plans and subscriptions (state-changing: needs confirmation).

Endpoints (verified in PayPal's OpenAPI schemas, scope https://uri.paypal.com/services/subscriptions):
  POST /v1/catalogs/products   required: name, type (PHYSICAL | DIGITAL | SERVICE)
  POST /v1/billing/plans       required: product_id, name, billing_cycles, payment_preferences
  POST /v1/billing/subscriptions  plan_id, subscriber, application_context -> response links[rel=approve]
A subscription only becomes ACTIVE after the BUYER approves it in a browser (sandbox personal account), which
PayPal does not allow through the API. The script prints the approval links; `--wait` polls until approved.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .client import PayPalClient
from .errors import ConfirmationRequired, PayPalError
from .subscriptions import SCOPE_SUBSCRIPTIONS, get_subscription

REGISTRY_DEFAULT = Path("data/paypal_seed.json")
PRODUCT_NAME = "SubSweep Demo Services"


@dataclass(frozen=True)
class PlanSpec:
    key: str
    name: str
    amount: str
    unit: str           # DAY | WEEK | MONTH | YEAR
    note: str = ""


# Fictional services. Streamly appears twice (monthly + annual) so the duplicate finding has a real PayPal pair;
# cancel FitPass as the buyer to demo "cancelled at PayPal but still charged on the statement".
DEMO_PLANS = [
    PlanSpec("streamly_monthly", "Streamly Plus", "9.99", "MONTH"),
    PlanSpec("streamly_annual", "Streamly Plus (Annual)", "99.99", "YEAR", "duplicate of the monthly plan"),
    PlanSpec("cloudbox", "CloudBox Storage", "2.99", "MONTH"),
    PlanSpec("fitpass", "FitPass Gym", "24.00", "MONTH", "cancel it as the buyer to demo a mismatch"),
]


def request_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


def plan_body(product_id: str, spec: PlanSpec) -> dict:
    return {
        "product_id": product_id, "name": spec.name, "description": f"SubSweep demo plan: {spec.name}",
        "billing_cycles": [{
            "frequency": {"interval_unit": spec.unit, "interval_count": 1}, "tenure_type": "REGULAR", "sequence": 1,
            "total_cycles": 0,      # 0 = bill until cancelled
            "pricing_scheme": {"fixed_price": {"value": spec.amount, "currency_code": "USD"}},
        }],
        "payment_preferences": {"auto_bill_outstanding": True, "setup_fee_failure_action": "CONTINUE",
                                "payment_failure_threshold": 3},
    }


def subscription_body(plan_id: str, buyer_email: str | None, return_url: str, cancel_url: str) -> dict:
    body = {"plan_id": plan_id, "application_context": {
        "brand_name": "SubSweep Demo", "locale": "en-US", "shipping_preference": "NO_SHIPPING",
        "user_action": "SUBSCRIBE_NOW", "return_url": return_url, "cancel_url": cancel_url}}
    if buyer_email:
        body["subscriber"] = {"email_address": buyer_email}
    return body


def load_registry(path: str | Path = REGISTRY_DEFAULT) -> dict:
    p = Path(path)
    if not p.is_file():
        return {"product_id": None, "plans": {}, "subscriptions": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def save_registry(reg: dict, path: str | Path = REGISTRY_DEFAULT) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def registry_subscription_ids(path: str | Path = REGISTRY_DEFAULT) -> list[str]:
    return [s["id"] for s in load_registry(path).get("subscriptions", {}).values()]


def plan_actions(reg: dict, specs=DEMO_PLANS) -> list[str]:
    """Human-readable list of what a run would create (used for the confirmation prompt and --dry-run)."""
    acts = [] if reg.get("product_id") else [f"create product '{PRODUCT_NAME}'"]
    for s in specs:
        if s.key not in reg.get("plans", {}):
            acts.append(f"create plan '{s.name}' (${s.amount}/{s.unit.lower()})")
        if s.key not in reg.get("subscriptions", {}):
            acts.append(f"create subscription to '{s.name}' (needs buyer approval)")
    return acts


def seed(client: PayPalClient, *, registry_path: str | Path = REGISTRY_DEFAULT, buyer_email: str | None = None,
         return_url: str = "https://example.com/subsweep/return", cancel_url: str = "https://example.com/subsweep/cancel",
         confirm: Callable[[list[str]], bool] | None = None, specs=DEMO_PLANS) -> dict:
    """Create whatever is missing. Re-running is safe: existing items in the registry are reused."""
    reg = load_registry(registry_path)
    reg.setdefault("plans", {}), reg.setdefault("subscriptions", {})
    actions = plan_actions(reg, specs)
    if not actions:
        return reg
    if confirm is None or not confirm(actions):
        raise ConfirmationRequired("; ".join(actions))

    def post(path: str, body: dict, rid: str) -> dict:
        return client.request("POST", path, json=body, confirmed=True, required_scope=SCOPE_SUBSCRIPTIONS,
                              headers={"PayPal-Request-Id": rid, "Prefer": "return=representation"})

    if not reg.get("product_id"):
        prod = post("/v1/catalogs/products", {"name": PRODUCT_NAME, "type": "SERVICE",
                                               "description": "Fictional services for SubSweep demo data"},
                    request_id("product", PRODUCT_NAME))
        reg["product_id"] = prod["id"]
        save_registry(reg, registry_path)
    for s in specs:
        if s.key not in reg["plans"]:
            plan = post("/v1/billing/plans", plan_body(reg["product_id"], s), request_id("plan", reg["product_id"], s.key))
            reg["plans"][s.key] = {"id": plan["id"], "name": s.name}
            save_registry(reg, registry_path)
        if s.key not in reg["subscriptions"]:
            plan_id = reg["plans"][s.key]["id"]
            sub = post("/v1/billing/subscriptions", subscription_body(plan_id, buyer_email, return_url, cancel_url),
                       request_id("sub", plan_id, buyer_email or ""))
            approve = next((l["href"] for l in sub.get("links", []) if l.get("rel") == "approve"), "")
            reg["subscriptions"][s.key] = {"id": sub["id"], "plan_id": plan_id, "name": s.name, "approve_url": approve}
            save_registry(reg, registry_path)
    return reg


def wait_until_active(client: PayPalClient, reg: dict, *, timeout_s: int = 600, interval_s: int = 10,
                      sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                      on_update: Callable[[dict], None] | None = None) -> dict[str, str]:
    """Poll (read-only) until every registry subscription is ACTIVE or the timeout passes."""
    deadline = clock() + timeout_s
    statuses: dict[str, str] = {}
    while True:
        for key, s in reg["subscriptions"].items():
            try:
                statuses[key] = get_subscription(client, s["id"]).status
            except PayPalError as exc:
                statuses[key] = f"error: {exc.user_message}"
        if on_update:
            on_update(dict(statuses))
        if all(v == "ACTIVE" for v in statuses.values()) or clock() >= deadline:
            return statuses
        sleep(interval_s)
