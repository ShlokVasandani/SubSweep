"""Fixture helpers: build clean-transaction tables directly (Phase 1 output shape)."""
from datetime import date, timedelta

import pandas as pd


def tx(rows):
    """rows: iterable of (date, merchant, amount[, via_paypal[, currency]])."""
    recs = []
    for i, r in enumerate(rows):
        d, m, a = r[0], r[1], r[2]
        pp = r[3] if len(r) > 3 else False
        cur = r[4] if len(r) > 4 else "USD"
        recs.append({
            "id": f"t{i:04d}", "date": d, "merchant_clean": m, "merchant_raw": m.upper(),
            "amount": a, "currency": cur,
            "source": "upload:test", "via_paypal": pp, "source_ref": "", "possible_duplicate": False,
        })
    return pd.DataFrame(recs)


def series(merchant, start, gaps, amounts, **kw):
    """Charges starting at `start`, each next one `gaps[i]` days later. Amounts are positive spend."""
    d, out = start, []
    for i, a in enumerate(amounts):
        if i:
            d = d + timedelta(days=gaps[i - 1] if isinstance(gaps, list) else gaps)
        out.append((d, merchant, -abs(a), kw.get("via_paypal", False), kw.get("currency", "USD")))
    return out


def monthly(merchant, start, amounts, day_drift=None, **kw):
    """Calendar-monthly charges (same day each month, optional per-month drift in days)."""
    out = []
    for i, a in enumerate(amounts):
        y, m = start.year + (start.month - 1 + i) // 12, (start.month - 1 + i) % 12 + 1
        d = date(y, m, start.day) + timedelta(days=(day_drift[i] if day_drift else 0))
        out.append((d, merchant, -abs(a), kw.get("via_paypal", False), kw.get("currency", "USD")))
    return out
