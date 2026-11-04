"""PayPal Transaction Search -> clean transactions table.

Endpoint (verified in PayPal's OpenAPI schema): GET /v1/reporting/transactions
  required: start_date, end_date (RFC 3339, seconds required); maximum range 31 days per call;
  page_size 1..500; fields=all returns every section; scope https://uri.paypal.com/services/reporting/search/read;
  transactions can take up to 3 hours to appear; history goes back 3 years.
Statuses: S success, P pending, D denied, V reversed. Event codes T00xx are payments sent/received.

PERSPECTIVE: PayPal's APIs are merchant-side. In the sandbox the app's business account RECEIVES the buyer's
subscription payments, so amounts arrive positive. SubSweep analyses what the *subscriber* pays, so by default
(perspective="payer") payments are flipped to negative debits. Use perspective="merchant" to keep PayPal's signs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from subsweep.ingest import dedupe
from subsweep.ingest.merchants import clean_merchant

from .client import PayPalClient

SCOPE_SEARCH = "https://uri.paypal.com/services/reporting/search/read"
PATH = "/v1/reporting/transactions"
WINDOW_DAYS = 31            # PayPal's maximum is 31 days; each window is start + 31d - 1s, strictly inside it
MAX_PAGES = 200
KEEP_STATUSES = {"S", "P"}
PAYPAL_COLUMNS = [
    "id", "date", "merchant_clean", "merchant_raw", "amount", "currency", "source", "via_paypal", "source_ref",
    "possible_duplicate", "paypal_subscription_id",
]


@dataclass
class TransactionBatch:
    details: list[dict]
    windows: int = 0
    pages: int = 0
    last_refreshed: str | None = None
    notes: list[str] = field(default_factory=list)


def _utc(d: date | datetime, end: bool) -> datetime:
    if isinstance(d, datetime):
        return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)
    return datetime(d.year, d.month, d.day, 23 if end else 0, 59 if end else 0, 59 if end else 0, tzinfo=timezone.utc)


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def split_windows(start: datetime, end: datetime, days: int = WINDOW_DAYS) -> list[tuple[datetime, datetime]]:
    out, cur = [], start
    while cur <= end:
        nxt = min(cur + timedelta(days=days) - timedelta(seconds=1), end)
        out.append((cur, nxt))
        cur = nxt + timedelta(seconds=1)
    return out


def fetch_transactions(client: PayPalClient, start: date | datetime, end: date | datetime,
                       *, page_size: int = 500, now: datetime | None = None) -> TransactionBatch:
    """Pull every transaction in [start, end], splitting into <=30-day windows and following pages."""
    s, e = _utc(start, False), _utc(end, True)
    now = now or datetime.now(timezone.utc)
    notes: list[str] = []
    if e > now:
        e = now.replace(microsecond=0)
        notes.append("The end date was in the future, so we searched up to now.")
    if s > e:
        raise ValueError("The start date needs to be before the end date.")
    if (now - s).days > 365 * 3:
        notes.append("PayPal only keeps three years of transaction history, so older transactions can't be returned.")
    batch = TransactionBatch(details=[], notes=notes)
    seen: set[str] = set()
    for ws, we in split_windows(s, e):
        batch.windows += 1
        page = 1
        while page <= MAX_PAGES:
            body = client.get(PATH, required_scope=SCOPE_SEARCH, params={
                "start_date": fmt(ws), "end_date": fmt(we), "fields": "all", "page_size": page_size, "page": page})
            batch.pages += 1
            batch.last_refreshed = body.get("last_refreshed_datetime") or batch.last_refreshed
            for d in body.get("transaction_details", []):
                tid = (d.get("transaction_info") or {}).get("transaction_id")
                if tid and tid in seen:
                    continue
                seen.add(tid)
                batch.details.append(d)
            if page >= int(body.get("total_pages") or 1):
                break
            page += 1
    batch.notes.append("PayPal can take up to 3 hours to show new transactions.")
    return batch


def _label(detail: dict, perspective: str) -> str:
    info = detail.get("transaction_info") or {}
    if perspective == "merchant":
        pn = ((detail.get("payer_info") or {}).get("payer_name")) or {}
        full = pn.get("alternate_full_name") or " ".join(x for x in (pn.get("given_name"), pn.get("surname")) if x)
        if full:
            return full
    items = ((detail.get("cart_info") or {}).get("item_details")) or []
    for it in items:
        if it.get("item_name"):
            return it["item_name"]
    return info.get("transaction_subject") or info.get("transaction_note") or "PayPal payment"


def to_transactions(details: list[dict], perspective: str = "payer", source: str = "paypal_api") -> tuple[pd.DataFrame, dict]:
    """Convert Transaction Search details into the clean-transactions shape (plus paypal_subscription_id)."""
    if perspective not in ("payer", "merchant"):
        raise ValueError("perspective must be 'payer' or 'merchant'")
    recs, dropped = [], {}

    def drop(reason: str):
        dropped[reason] = dropped.get(reason, 0) + 1

    for d in details:
        info = d.get("transaction_info") or {}
        code = str(info.get("transaction_event_code") or "")
        status = str(info.get("transaction_status") or "")
        amt = info.get("transaction_amount") or {}
        when = str(info.get("transaction_initiation_date") or "")[:10]
        if not code.startswith("T00"):
            drop("not a payment (transfer, withdrawal, conversion or hold)")
            continue
        if status not in KEEP_STATUSES:
            drop("denied or reversed")
            continue
        try:
            value, day = float(amt["value"]), date.fromisoformat(when)
        except (KeyError, ValueError, TypeError):
            drop("unreadable amount or date")
            continue
        if perspective == "payer":
            value = -value
        raw = _label(d, perspective)
        sub_id = info.get("paypal_reference_id") if info.get("paypal_reference_id_type") == "SUB" else ""
        recs.append({
            "date": day, "merchant_raw": raw, "merchant_clean": clean_merchant(raw) or raw, "amount": round(value, 2),
            "currency": amt.get("currency_code", "USD"), "source": source, "via_paypal": True,
            "source_ref": info.get("transaction_id", ""), "paypal_subscription_id": sub_id or "",
        })
    df = pd.DataFrame(recs, columns=["date", "merchant_raw", "merchant_clean", "amount", "currency", "source",
                                     "via_paypal", "source_ref", "paypal_subscription_id"])
    df = df.sort_values("date", kind="stable").reset_index(drop=True)
    df = dedupe.flag_same_day_repeats(df)
    df = dedupe.assign_ids(df)
    return df[PAYPAL_COLUMNS], dropped
