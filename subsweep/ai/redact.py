"""Remove personal identifiers before ANY text goes to a model or the audit log.

Covers: email addresses (including UPI handles like name@bank), card numbers (13-19 digits, spaced or dashed),
masked cards (XXXX1234, ****1234, "ending in 1234"), IBANs, labelled account numbers, any other long digit run
(9+ digits), international phone numbers, and API keys. Amounts, percentages and dates are left alone.

Every pattern is written to avoid catastrophic backtracking (no nested unbounded quantifiers over the same characters);
tests feed it long pathological strings.
"""
from __future__ import annotations

import re
from typing import Any

_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "[SECRET]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+"), "[EMAIL]"),
    # A mangled address ("john.doe@ gmail", "name@") that lost its domain upstream is still personal.
    (re.compile(r"[A-Za-z0-9._%+\-]+@(?=\s|$)"), "[EMAIL]"),
    # Whole card numbers first (13-19 digits, spaces or dashes allowed), so no label pattern can leave a tail behind.
    (re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), "[CARD]"),
    # "card 4111-11..." / "card no: XXXX 1234" / "card ending in 1234": take the label and the whole digit run.
    (re.compile(r"(?i)\b(?:card|crd)\b[\s:#]*(?:no\.?|number|ending(?:\s+in)?)?[\s:#]*(?:[x*•]{2,}[\s-]*)?\d[\d -]{2,}\d"), "[CARD]"),
    (re.compile(r"(?i)[x*•]{2,}(?:[\s-]+[x*•]{2,})*[\s-]*\d{2,4}\b"), "[CARD]"),
    (re.compile(r"(?i)\bending(?:\s+in)?\s*\d{4}\b"), "[CARD]"),
    (re.compile(r"(?i)\b(?:a/c|acct|account|acc)\.?\s*(?:no\.?|number|#)?\s*[:\-]?\s*\d[\d\- ]{4,}\d"), "[ACCOUNT]"),
    (re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"), "[ACCOUNT]"),
    (re.compile(r"\+\d{1,3}[\s-]?\(?\d{2,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}\b"), "[PHONE]"),
    (re.compile(r"(?i)\b(?:tel|phone|mobile|ph)\.?[\s:]*\d[\d\s().-]{7,}\d"), "[PHONE]"),
    (re.compile(r"(?<![\d.,])\d{9,}(?![\d,]*\.\d)"), "[ACCOUNT]"),
]


def redact_text(text: str) -> tuple[str, int]:
    """Return (clean_text, number_of_redactions)."""
    count = 0
    for pat, token in _PATTERNS:
        text, n = pat.subn(token, text)
        count += n
    return text, count


def redact_obj(obj: Any) -> tuple[Any, int]:
    """Redact every string inside nested dict/list/tuple structures; other types pass through unchanged."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        total, out = 0, {}
        for k, v in obj.items():
            out[k], n = redact_obj(v)
            total += n
        return out, total
    if isinstance(obj, (list, tuple)):
        total, out = 0, []
        for v in obj:
            r, n = redact_obj(v)
            out.append(r)
            total += n
        return out, total
    return obj, 0
