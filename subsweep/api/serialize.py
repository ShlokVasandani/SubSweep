"""JSON-safe views of the analysis objects (dates as ISO strings, numpy types as plain Python)."""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

import pandas as pd

from subsweep.detect.detector import CYCLES_PER_YEAR
from subsweep.findings.engine import CATEGORY_LABEL, Finding, annual_cost

SOURCE_LABELS = {"paypal_api": "PayPal (live sandbox)", "paypal_mock": "PayPal (demo data)"}


def jsonable(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        obj = obj.item()
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    return obj


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, "Your upload")


def finding_json(f: Finding, dismissals: dict[str, str]) -> dict:
    d = jsonable(f.to_dict())
    d["dismissed_reason"] = dismissals.get(f.id)
    d["can_draft"] = f.type in ("DUPLICATE", "PRICE_HIKE", "POSSIBLY_FORGOTTEN")
    d["can_still_use"] = f.type == "POSSIBLY_FORGOTTEN"
    return d


def subscription_json(s: pd.Series, flags: list[str], paypal: dict | None) -> dict:
    cost = annual_cost(s)
    out = {
        "id": s["id"], "merchant": s["merchant"], "merchant_key": s["merchant_key"], "cycle": s["cycle"],
        "current_amount": float(s["current_amount"]), "avg_amount": float(s["avg_amount"]), "currency": s["currency"],
        "n_charges": int(s["n_charges"]), "first_date": s["first_date"], "last_date": s["last_date"],
        "next_expected_date": s["next_expected_date"], "active": bool(s["active"]), "confidence": float(s["confidence"]),
        "confidence_reason": s["confidence_reason"], "regularity": float(s["regularity"]), "amount_stability": float(s["amount_stability"]),
        "price_changes": s["price_changes"], "pending_price_change": s["pending_price_change"], "via_paypal": bool(s["via_paypal"]),
        "category": CATEGORY_LABEL.get(s["category"], None) if s["category"] else None, "discretionary": bool(s["discretionary"]),
        "annual_cost": round(cost, 2), "monthly_cost": round(cost / 12, 2), "flags": flags, "paypal": paypal,
        "cycles_per_year": CYCLES_PER_YEAR[s["cycle"]],
    }
    return jsonable(out)
