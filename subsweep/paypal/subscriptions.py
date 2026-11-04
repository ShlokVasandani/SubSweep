"""PayPal Subscriptions API (read-only here): live status, next billing date, last payment.

Verified endpoints: GET /v1/billing/subscriptions/{id}, GET /v1/billing/plans/{id}, GET /v1/billing/plans.
Scope https://uri.paypal.com/services/subscriptions. Status values: APPROVAL_PENDING, APPROVED, ACTIVE,
SUSPENDED, CANCELLED, EXPIRED. SubSweep never cancels or suspends: it links the subscriber to PayPal instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .client import PayPalClient
from .errors import PayPalError, PayPalNotFound

SCOPE_SUBSCRIPTIONS = "https://uri.paypal.com/services/subscriptions"
ENDED_STATUSES = {"CANCELLED", "SUSPENDED", "EXPIRED"}


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass
class LiveSubscription:
    id: str
    status: str
    plan_id: str = ""
    plan_name: str = ""
    plan_amount: float | None = None
    plan_currency: str = ""
    plan_interval: str = ""            # e.g. "MONTH x1"
    status_update_time: datetime | None = None
    start_time: datetime | None = None
    next_billing_time: datetime | None = None
    last_payment_amount: float | None = None
    last_payment_currency: str = ""
    last_payment_time: datetime | None = None
    failed_payments_count: int = 0
    manage_url: str = ""

    @property
    def is_ended(self) -> bool:
        return self.status in ENDED_STATUSES

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def plan_summary(plan: dict) -> dict:
    cycles = plan.get("billing_cycles") or []
    reg = next((c for c in cycles if c.get("tenure_type") == "REGULAR"), cycles[0] if cycles else {})
    price = ((reg.get("pricing_scheme") or {}).get("fixed_price")) or {}
    freq = reg.get("frequency") or {}
    return {
        "id": plan.get("id", ""), "name": plan.get("name", ""), "status": plan.get("status", ""),
        "product_id": plan.get("product_id", ""),
        "amount": float(price["value"]) if price.get("value") not in (None, "") else None,
        "currency": price.get("currency_code", ""),
        "interval": f"{freq.get('interval_unit', '')} x{freq.get('interval_count', 1)}".strip(),
    }


def get_plan(client: PayPalClient, plan_id: str) -> dict:
    return plan_summary(client.get(f"/v1/billing/plans/{plan_id}", required_scope=SCOPE_SUBSCRIPTIONS))


def list_plans(client: PayPalClient, product_id: str | None = None, page_size: int = 20) -> list[dict]:
    out, page = [], 1
    while page <= 50:
        params = {"page_size": page_size, "page": page, "total_required": "true"}
        if product_id:
            params["product_id"] = product_id
        body = client.get("/v1/billing/plans", required_scope=SCOPE_SUBSCRIPTIONS, params=params)
        out += [plan_summary(p) for p in body.get("plans", [])]
        if page >= int(body.get("total_pages") or 1):
            break
        page += 1
    return out


def get_subscription(client: PayPalClient, subscription_id: str, plan_cache: dict | None = None) -> LiveSubscription:
    body = client.get(f"/v1/billing/subscriptions/{subscription_id}", required_scope=SCOPE_SUBSCRIPTIONS)
    bi = body.get("billing_info") or {}
    last = bi.get("last_payment") or {}
    last_amt = last.get("amount") or {}
    live = LiveSubscription(
        id=body.get("id", subscription_id), status=body.get("status", "UNKNOWN"), plan_id=body.get("plan_id", ""),
        status_update_time=parse_time(body.get("status_update_time")), start_time=parse_time(body.get("start_time")),
        next_billing_time=parse_time(bi.get("next_billing_time")),
        last_payment_amount=float(last_amt["value"]) if last_amt.get("value") not in (None, "") else None,
        last_payment_currency=last_amt.get("currency_code", ""), last_payment_time=parse_time(last.get("time")),
        failed_payments_count=int(bi.get("failed_payments_count") or 0),
        manage_url=client.config.subscriber_manage_url,
    )
    if live.plan_id:
        cache = plan_cache if plan_cache is not None else {}
        if live.plan_id not in cache:
            try:
                cache[live.plan_id] = get_plan(client, live.plan_id)
            except PayPalError:
                cache[live.plan_id] = {}
        p = cache[live.plan_id]
        live.plan_name, live.plan_amount = p.get("name", ""), p.get("amount")
        live.plan_currency, live.plan_interval = p.get("currency", ""), p.get("interval", "")
    return live


def live_status(client: PayPalClient, subscription_ids: list[str]) -> tuple[list[LiveSubscription], dict[str, str]]:
    """Look up many subscriptions; one bad ID never hides the rest. Returns (found, {id: friendly error})."""
    found, errors, cache = [], {}, {}
    for sid in dict.fromkeys(subscription_ids):
        try:
            found.append(get_subscription(client, sid, cache))
        except PayPalNotFound:
            errors[sid] = "PayPal doesn't know this subscription (it may belong to a different sandbox app)."
        except PayPalError as exc:
            errors[sid] = exc.user_message
    return found, errors
