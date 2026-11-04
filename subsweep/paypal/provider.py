"""Chooses between the live PayPal sandbox and bundled demo ("mock") data.

Mock mode exists so the demo works without any PayPal setup or approval:
  * PAYPAL_MODE=mock           -> always use data/paypal_mock.json (no network, no credentials needed);
  * PAYPAL_MODE=live (default) -> use the sandbox, but if any seeded subscription is still APPROVAL_PENDING
                                  (the buyer has not approved it yet) the whole view switches to mock data.
The switch is all-or-nothing on purpose: mixing live statuses with demo charges would be misleading.
Mock rows are labelled source="paypal_mock" and subscriptions data_source="mock", and the view carries a notice,
so demo data can never be mistaken for live PayPal data. The seed script and approval flow are unchanged.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .client import PayPalClient
from .config import SANDBOX_MANAGE_URL
from .errors import PayPalNotConfigured
from .subscriptions import LiveSubscription, live_status, parse_time
from .sync import known_subscription_ids
from .transactions import fetch_transactions, to_transactions

DEFAULT_MOCK_PATH = Path(__file__).resolve().parents[2] / "data" / "paypal_mock.json"
MOCK_SOURCE = "paypal_mock"
PENDING = "APPROVAL_PENDING"
MOCK_NOTICE = "Showing bundled demo data (PayPal mock mode). Nothing here comes from a live PayPal account."


@dataclass
class PayPalView:
    mode: str                                  # "live" or "mock"
    reason: str                                # why mock was used (empty for live)
    transactions: pd.DataFrame
    live: list[LiveSubscription]
    errors: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def is_mock(self) -> bool:
        return self.mode == "mock"


def current_mode(environ=None) -> str:
    env = os.environ if environ is None else environ
    value = (env.get("PAYPAL_MODE") or "live").strip().lower() or "live"
    if value not in ("live", "mock"):
        raise PayPalNotConfigured("PAYPAL_MODE must be either 'live' or 'mock'.")
    return value


@dataclass
class MockData:
    subscriptions: list[LiveSubscription]
    details: list[dict]


def _money(block: dict | None) -> tuple[float | None, str]:
    block = block or {}
    amount = block.get("amount")
    return (float(amount) if amount not in (None, "") else None), block.get("currency", "")


def load_mock(path: str | Path | None = None, environ=None) -> MockData:
    env = os.environ if environ is None else environ
    p = Path(path or env.get("PAYPAL_MOCK_FILE") or DEFAULT_MOCK_PATH)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PayPalNotConfigured(f"The PayPal demo data file wasn't found ({p.name}). Restore data/paypal_mock.json.") from None
    except (OSError, ValueError):
        raise PayPalNotConfigured(f"The PayPal demo data file ({p.name}) couldn't be read. It must be valid JSON.") from None
    manage = env.get("PAYPAL_MANAGE_URL") or SANDBOX_MANAGE_URL
    subs = []
    for s in doc.get("subscriptions", []):
        amt, cur = _money(s.get("last_payment"))
        subs.append(LiveSubscription(
            id=s["id"], status=s.get("status", "ACTIVE"), plan_id=s.get("plan_id", ""), plan_name=s.get("plan_name", ""),
            plan_amount=s.get("plan_amount"), plan_currency=s.get("plan_currency", ""), plan_interval=s.get("plan_interval", ""),
            status_update_time=parse_time(s.get("status_update_time")), start_time=parse_time(s.get("start_time")),
            next_billing_time=parse_time(s.get("next_billing_time")), last_payment_amount=amt, last_payment_currency=cur,
            last_payment_time=parse_time((s.get("last_payment") or {}).get("time")),
            failed_payments_count=int(s.get("failed_payments_count") or 0), manage_url=manage, data_source="mock"))
    return MockData(subs, list(doc.get("transactions", [])))


def _in_range(detail: dict, start: date | None, end: date | None) -> bool:
    """ISO dates sort as text, so no parsing is needed (a malformed date is dropped later by to_transactions)."""
    when = str((detail.get("transaction_info") or {}).get("transaction_initiation_date") or "")[:10]
    return (start is None or when >= start.isoformat()) and (end is None or when <= end.isoformat())


def _mock_view(reason: str, start, end, perspective: str, mock_path, environ) -> PayPalView:
    data = load_mock(mock_path, environ)
    details = [d for d in data.details if _in_range(d, start, end)]
    df, _ = to_transactions(details, perspective, source=MOCK_SOURCE)
    return PayPalView("mock", reason, df, data.subscriptions, {}, [MOCK_NOTICE])


def get_paypal_view(
    client: PayPalClient | None,
    start: date | None = None,
    end: date | None = None,
    *,
    registry_ids=(),
    perspective: str = "payer",
    environ=None,
    mock_path: str | Path | None = None,
) -> PayPalView:
    """Transactions plus live subscription status from the sandbox, or demo data (see module docstring)."""
    if current_mode(environ) == "mock":
        return _mock_view("PAYPAL_MODE is set to mock.", start, end, perspective, mock_path, environ)
    if client is None:
        raise PayPalNotConfigured("PayPal isn't connected yet. Add PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET to your .env "
                                  "file, or set PAYPAL_MODE=mock to use the bundled demo data.")
    end = end or date.today()
    start = start or end - timedelta(days=90)
    ids, tx, notes = list(registry_ids), None, []
    if not ids:                                   # nothing seeded: discover subscription IDs from transactions
        batch = fetch_transactions(client, start, end)
        tx, _ = to_transactions(batch.details, perspective)
        notes += batch.notes
        ids = known_subscription_ids(tx, [])
    live, errors = live_status(client, ids) if ids else ([], {})
    pending = [l for l in live if l.status == PENDING]
    if pending:
        return _mock_view(
            f"{len(pending)} of {len(live)} live sandbox subscriptions are still waiting for buyer approval "
            f"({PENDING}), so the bundled demo data is shown instead.", start, end, perspective, mock_path, environ)
    if tx is None:
        batch = fetch_transactions(client, start, end)
        tx, _ = to_transactions(batch.details, perspective)
        notes += batch.notes
    return PayPalView("live", "", tx, live, errors, notes)
