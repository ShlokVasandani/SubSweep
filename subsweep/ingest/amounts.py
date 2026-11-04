"""Amount and currency parsing.

Handles currency symbols (₹ $ € £), ISO codes (INR, USD ...), "Rs", commas,
Indian lakh/crore grouping (1,23,456.00), European decimals (1.234,56 / 12,50),
parentheses and trailing/unicode minus for negatives, and CR/DR suffixes.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

SYMBOL_TO_CODE = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
KNOWN_CODES = {"INR", "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "SGD", "AED", "CHF"}
_WORD_TO_CODE = {"RS": "INR", "RS.": "INR", "INR": "INR", "US$": "USD", "USD": "USD"}

_MINUS_CHARS = "−‒–—﹣－"  # unicode minus / dashes
_CODE_RE = re.compile(r"\b(" + "|".join(sorted(KNOWN_CODES)) + r")\b", re.I)
_RS_RE = re.compile(r"\bRs\.?\s*", re.I)


def normalize_currency(value) -> str | None:
    """Map a symbol / code / word ('₹', 'inr', 'Rs') to an ISO code, else None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s in SYMBOL_TO_CODE:
        return SYMBOL_TO_CODE[s]
    up = s.upper()
    if up in KNOWN_CODES:
        return up
    if up in _WORD_TO_CODE:
        return _WORD_TO_CODE[up]
    for sym, code in SYMBOL_TO_CODE.items():
        if sym in s:
            return code
    m = _CODE_RE.search(s)
    return m.group(1).upper() if m else None


def _to_decimal_string(num: str) -> str:
    """Resolve thousands vs decimal separators in a digits/,/. string."""
    has_comma, has_dot = "," in num, "." in num
    if has_comma and has_dot:
        # Whichever comes last is the decimal separator.
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")  # 1.234,56
        else:
            num = num.replace(",", "")  # 1,234.56 or 1,23,456.00
    elif has_comma:
        parts = num.split(",")
        # "12,50" -> decimal comma. "1,234" / "1,23,456" -> grouping.
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            num = parts[0] + "." + parts[1]
        else:
            num = "".join(parts)
    elif has_dot and num.count(".") > 1:
        num = num.replace(".", "")  # 1.234.567 -> grouping dots
    return num


def parse_amount(raw) -> tuple[float | None, str | None]:
    """Parse a raw amount cell into (signed_value, currency_code).

    Returns (None, currency) when no number can be read. Sign conventions:
    "(45.00)", "-45", "45-", "−45", and "45 DR" are negative; "45 CR" positive.
    """
    if raw is None:
        return None, None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return (float(raw), None) if raw == raw else (None, None)  # NaN check
    s = str(raw).strip().replace(" ", " ")
    if not s or s.lower() in {"nan", "none", "null", "-", "--", "n/a"}:
        return None, None

    currency = normalize_currency(s) if re.search(r"[^\d\s.,()+\-]", s) else None
    if currency is None and _RS_RE.search(s):
        currency = "INR"
    # A 3-letter code only counts as currency when it is a known code; DR/CR handled below.
    for ch in _MINUS_CHARS:
        s = s.replace(ch, "-")

    negative = False
    upper = s.upper()
    if re.search(r"\bDR\.?$", upper.strip()) or re.search(r"^DR\b", upper.strip()):
        negative = True

    # Remove currency words/symbols and DR/CR markers, keep digits and separators.
    s = _RS_RE.sub("", s)
    s = _CODE_RE.sub("", s)
    s = re.sub(r"\b(?:DR|CR)\b\.?", "", s, flags=re.I)
    s = re.sub(r"US\$", "", s, flags=re.I)
    for sym in SYMBOL_TO_CODE:
        s = s.replace(sym, "")
    s = s.strip()

    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()
    if s.endswith("-"):
        negative = True
        s = s[:-1].strip()
    if s.startswith("-"):
        negative = True
        s = s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    if s.startswith("(") and s.endswith(")"):  # e.g. -(45)
        s = s[1:-1].strip()

    s = s.replace(" ", "").replace("'", "")
    if not re.fullmatch(r"[\d.,]+", s) or not re.search(r"\d", s):
        return None, currency
    try:
        value = Decimal(_to_decimal_string(s))
    except InvalidOperation:
        return None, currency
    if negative:
        value = -value
    return float(value), currency


def currency_from_header(header) -> str | None:
    """'Amount (INR)' / 'Debit ₹' / 'Amount USD' -> ISO code, else None."""
    h = str(header or "")
    for sym, code in SYMBOL_TO_CODE.items():
        if sym in h:
            return code
    m = _CODE_RE.search(h)
    return m.group(1).upper() if m else None
