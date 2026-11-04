"""Date parsing with per-column day-first / month-first inference.

Supported inputs: ISO (2026-10-12, 2026-10-12T10:00:00Z, 2026/10/12),
numeric dd/mm/yyyy or mm/dd/yyyy (with / . -), two-digit years, compact
20261012, and named months ("12 Oct 2026", "Oct 12, 2026", "12-Oct-26",
"12 October 2026").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"),
            ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
            ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"),
            ("nov", "november"), ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}

_ISO = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T\s].*)?$")
_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_NUMERIC = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})(?:[T,\s].*)?$")
_DMY_NAMED = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?[\s\-/.,]+([A-Za-z]{3,9})\.?[\s\-/.,]+(\d{2}|\d{4})(?:[T,\s].*)?$")
_MDY_NAMED = re.compile(r"^([A-Za-z]{3,9})\.?[\s\-/.]+(\d{1,2})(?:st|nd|rd|th)?,?[\s\-/.]+(\d{2}|\d{4})(?:[T,\s].*)?$")


@dataclass(frozen=True)
class _Parsed:
    kind: str  # "ymd" (resolved) or "ab" (numeric, order unknown)
    a: int = 0
    b: int = 0
    y: int = 0
    m: int = 0
    d: int = 0


def _year(y: str) -> int:
    n = int(y)
    if len(y) == 2:
        n += 2000 if n < 70 else 1900
    return n


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _parse_one(raw) -> _Parsed | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.lower() in {"nan", "nat", "none"}:
        return None
    if m := _ISO.match(s):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        return _Parsed("ymd", y=y, m=mo, d=d) if _valid(y, mo, d) else None
    if m := _COMPACT.match(s):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        return _Parsed("ymd", y=y, m=mo, d=d) if _valid(y, mo, d) else None
    if m := _DMY_NAMED.match(s):
        mo = _MONTHS.get(m[2].lower())
        if mo:
            y, d = _year(m[3]), int(m[1])
            return _Parsed("ymd", y=y, m=mo, d=d) if _valid(y, mo, d) else None
    if m := _MDY_NAMED.match(s):
        mo = _MONTHS.get(m[1].lower())
        if mo:
            y, d = _year(m[3]), int(m[2])
            return _Parsed("ymd", y=y, m=mo, d=d) if _valid(y, mo, d) else None
    if m := _NUMERIC.match(s):
        return _Parsed("ab", a=int(m[1]), b=int(m[2]), y=_year(m[3]))
    return None


def _resolve(p: _Parsed, dayfirst: bool) -> date | None:
    if p.kind == "ymd":
        return _valid(p.y, p.m, p.d)
    d, m = (p.a, p.b) if dayfirst else (p.b, p.a)
    return _valid(p.y, m, d)


def infer_dayfirst(values, default: bool = False) -> tuple[bool, str]:
    """Decide day-first vs month-first from a column of numeric dates.

    Returns (dayfirst, reason). Evidence: any first field > 12 means day-first;
    any second field > 12 means month-first; conflicting or absent evidence
    falls back to `default`.
    """
    first_gt12 = second_gt12 = numeric = 0
    for v in values:
        p = _parse_one(v)
        if p and p.kind == "ab":
            numeric += 1
            first_gt12 += p.a > 12
            second_gt12 += p.b > 12
    if numeric == 0:
        return default, "no numeric dates; order irrelevant"
    if first_gt12 and not second_gt12:
        return True, f"day-first: {first_gt12} value(s) have a first field > 12"
    if second_gt12 and not first_gt12:
        return False, f"month-first: {second_gt12} value(s) have a second field > 12"
    if first_gt12 and second_gt12:
        return default, "conflicting evidence; fell back to default order"
    return default, "ambiguous (all fields <= 12); fell back to default order"


def parse_date(raw, dayfirst: bool = False) -> date | None:
    """Parse a single value. `dayfirst` only matters for ambiguous numeric dates."""
    p = _parse_one(raw)
    if p is None:
        return None
    if p.kind == "ab":
        # Single-value convenience: use evidence in the value itself first.
        if p.a > 12 and p.b <= 12:
            dayfirst = True
        elif p.b > 12 and p.a <= 12:
            dayfirst = False
    return _resolve(p, dayfirst)


def parse_date_column(values, dayfirst: bool | None = None, default_dayfirst: bool = False):
    """Parse a whole column consistently.

    Returns (list[date | None], dayfirst_used, reason). If `dayfirst` is None
    the order is inferred from the column.
    """
    values = list(values)
    if dayfirst is None:
        dayfirst, reason = infer_dayfirst(values, default_dayfirst)
    else:
        reason = "set by caller"
    out = []
    for v in values:
        p = _parse_one(v)
        out.append(_resolve(p, dayfirst) if p else None)
    return out, dayfirst, reason
