"""Small formatting helpers so every reason string shows exact amounts and dates."""
from __future__ import annotations

from datetime import date

_SYMBOLS = {"USD": "$", "INR": "₹", "EUR": "€", "GBP": "£"}


def money(amount: float, currency: str) -> str:
    sym = _SYMBOLS.get(currency)
    body = f"{abs(amount):,.2f}"
    return f"{sym}{body}" if sym else f"{currency} {body}"


def fdate(d: date) -> str:
    return f"{d.day} {d:%b %Y}"


CYCLE_ADJ = {"weekly": "weekly", "monthly": "monthly", "quarterly": "quarterly", "yearly": "yearly"}
CYCLE_NOUN = {"weekly": "week", "monthly": "month", "quarterly": "quarter", "yearly": "year"}
