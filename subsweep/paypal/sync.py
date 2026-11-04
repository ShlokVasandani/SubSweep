"""Merge uploaded statements with PayPal API transactions, and match detected subscriptions to live PayPal ones."""
from __future__ import annotations

import pandas as pd
from rapidfuzz import fuzz

from .subscriptions import LiveSubscription

DATE_WINDOW_DAYS = 3
NAME_THRESHOLD = 85
AMOUNT_TOLERANCE = 0.01   # 1%


def merge_sources(upload: pd.DataFrame, api: pd.DataFrame, window_days: int = DATE_WINDOW_DAYS) -> pd.DataFrame:
    """Combine the uploaded statement and PayPal API rows without double counting.

    * A PayPal API row whose transaction ID is already in the upload is dropped; the upload row is kept
      and marked confirmed_by_paypal_api.
    * A bank row that mentions PayPal (via_paypal) and matches an API row on currency, amount and a date within
      `window_days` is treated as the same payment: the bank row is kept, confirmed, and linked to the PayPal
      transaction. Matched bank rows are marked confirmed and cannot match again, so genuine repeats stay.
    * Every remaining API row is added with source "paypal_api", so the UI can show where each row came from.
    """
    need = ["id", "date", "merchant_clean", "merchant_raw", "amount", "currency", "source", "via_paypal", "source_ref",
            "possible_duplicate"]
    up = upload.reindex(columns=list(dict.fromkeys(need + list(upload.columns)))).copy()   # tolerate an empty/absent upload
    ap = api.reindex(columns=list(dict.fromkeys(need + list(api.columns)))).copy()
    for df in (up, ap):
        if "paypal_subscription_id" not in df:
            df["paypal_subscription_id"] = ""
        df["paypal_subscription_id"] = df["paypal_subscription_id"].fillna("")
    up["confirmed_by_paypal_api"] = False
    up["paypal_txn_id"] = ""
    ap["confirmed_by_paypal_api"] = False
    ap["paypal_txn_id"] = ap["source_ref"]

    drop_idx = []
    up_refs = {r: i for i, r in up["source_ref"].items() if isinstance(r, str) and r}
    for j, row in ap.iterrows():
        i = up_refs.get(row["source_ref"]) if row["source_ref"] else None
        if i is not None:
            up.loc[i, ["confirmed_by_paypal_api", "paypal_txn_id"]] = [True, row["source_ref"]]
            if row["paypal_subscription_id"]:
                up.loc[i, "paypal_subscription_id"] = row["paypal_subscription_id"]
            drop_idx.append(j)

    for j, row in ap.drop(index=drop_idx).sort_values("date", kind="stable").iterrows():
        cands = up[(up["via_paypal"].astype(bool)) & (~up["confirmed_by_paypal_api"]) & (up["currency"] == row["currency"])
                   & ((up["amount"] - row["amount"]).abs() <= 0.01)]
        best, best_gap = None, None
        for i, c in cands.iterrows():
            gap = abs((c["date"] - row["date"]).days)
            if gap <= window_days and (best_gap is None or gap < best_gap):
                best, best_gap = i, gap
        if best is not None:
            up.loc[best, ["confirmed_by_paypal_api", "paypal_txn_id"]] = [True, row["source_ref"]]
            if row["paypal_subscription_id"]:
                up.loc[best, "paypal_subscription_id"] = row["paypal_subscription_id"]
            drop_idx.append(j)

    merged = pd.concat([up, ap.drop(index=drop_idx)], ignore_index=True)
    merged = merged.sort_values("date", kind="stable").reset_index(drop=True)
    if len(merged):
        merged["possible_duplicate"] = merged["possible_duplicate"].fillna(False).astype(bool)
    return merged


def source_summary(df: pd.DataFrame) -> dict:
    """Counts by origin, for the UI badge 'from your upload' vs 'from PayPal'."""
    if df.empty:
        return {"upload": 0, "paypal_api": 0, "confirmed_by_paypal": 0}
    api = (df["source"] == "paypal_api").sum()
    return {"upload": int(len(df) - api), "paypal_api": int(api),
            "confirmed_by_paypal": int(df.get("confirmed_by_paypal_api", pd.Series(dtype=bool)).fillna(False).sum())}


def match_paypal_subscriptions(subs: pd.DataFrame, tx: pd.DataFrame, live: list[LiveSubscription]) -> dict[str, LiveSubscription]:
    """Link detected PayPal-billed subscriptions to their live PayPal record.

    1) exact: one of the subscription's transactions carries a PayPal subscription ID;
    2) fuzzy: PayPal-billed stream whose name resembles the plan name and whose price matches the plan price.
    """
    by_id = {l.id: l for l in live}
    out: dict[str, LiveSubscription] = {}
    tx_sub = {}
    if "paypal_subscription_id" in tx.columns:
        tx_sub = {i: s for i, s in zip(tx["id"], tx["paypal_subscription_id"].fillna("")) if s}
    for _, s in subs.iterrows():
        ids = [tx_sub[t] for t in s["txn_ids"] if t in tx_sub]
        if ids:
            sid = max(set(ids), key=ids.count)
            if sid in by_id:
                out[s["id"]] = by_id[sid]
                continue
        if not s["via_paypal"]:
            continue
        best, best_score = None, 0.0
        taken = {v.id for v in out.values()}
        for l in live:
            if l.id in taken or not l.plan_name:
                continue
            score = max(fuzz.token_set_ratio(s["merchant"].lower(), l.plan_name.lower()),
                        fuzz.token_set_ratio(s["merchant_key"], l.plan_name.lower()))
            price_ok = (l.plan_amount is not None and s["current_amount"] > 0
                        and abs(l.plan_amount - s["current_amount"]) / s["current_amount"] <= AMOUNT_TOLERANCE)
            if score >= NAME_THRESHOLD and price_ok and score > best_score:
                best, best_score = l, score
        if best:
            out[s["id"]] = best
    return out


def status_table(subs: pd.DataFrame, matches: dict[str, LiveSubscription]) -> list[dict]:
    """Rows for the 'PayPal live status' panel: one per matched subscription."""
    rows = []
    for _, s in subs.iterrows():
        l = matches.get(s["id"])
        if not l:
            continue
        rows.append({
            "subscription_id": s["id"], "merchant": s["merchant"], "paypal_subscription_id": l.id,
            "status": l.status, "plan_name": l.plan_name, "next_billing_time": l.next_billing_time,
            "last_payment_amount": l.last_payment_amount, "last_payment_currency": l.last_payment_currency,
            "last_payment_time": l.last_payment_time, "failed_payments_count": l.failed_payments_count,
            "status_update_time": l.status_update_time, "manage_url": l.manage_url,
        })
    return rows


def known_subscription_ids(tx: pd.DataFrame | None, registry_ids: list[str] | None = None) -> list[str]:
    """Subscription IDs worth looking up: those seeded by the demo script plus any seen on transactions.

    PayPal has no 'list my subscriptions' call for this API, so IDs come from our own registry and from
    Transaction Search rows whose paypal_reference_id_type is SUB.
    """
    ids = list(registry_ids or [])
    if tx is not None and "paypal_subscription_id" in tx.columns:
        ids += [s for s in tx["paypal_subscription_id"].fillna("") if s]
    return list(dict.fromkeys(ids))
