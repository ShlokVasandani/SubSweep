"""Check that a model's text only uses numbers and dates that were in the data we gave it.

This is how "the AI never invents amounts or dates" is enforced rather than hoped for. Every number in the
output must appear in the payload (commas and trailing zeros ignored; whole-number rounding of a payload number is
allowed, so "$132" for 131.88 passes). Dates written as "5 Oct 2026", "Oct 5, 2026" or ISO must match a date in the
payload exactly. Small counts 1-3 are always allowed ("one", "twice" style wording). Month names without a day, and
words, are not checked: this guards figures, not prose.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON}\s+(\d{{4}})\b", re.I)
_MDY = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
SMALL_OK = {1.0, 2.0, 3.0}


@dataclass
class Grounding:
    ok: bool
    ungrounded_numbers: list[str] = field(default_factory=list)
    ungrounded_dates: list[str] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        return self.ungrounded_numbers + self.ungrounded_dates


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str) -> tuple[set[date], str]:
    """Return (dates found, text with those date spans blanked out)."""
    found: set[date] = set()

    def sub_dmy(m):
        d = _mk(int(m.group(3)), _MONTHS[m.group(2).lower()[:3]], int(m.group(1)))
        if d:
            found.add(d)
        return " "

    def sub_mdy(m):
        d = _mk(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
        if d:
            found.add(d)
        return " "

    def sub_iso(m):
        d = _mk(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.add(d)
        return " "

    text = _DMY.sub(sub_dmy, text)
    text = _MDY.sub(sub_mdy, text)
    text = _ISO.sub(sub_iso, text)
    return found, text


def _to_float(tok: str) -> float | None:
    try:
        return float(tok.replace(",", ""))
    except ValueError:
        return None


def numbers_in(text: str) -> list[float]:
    return [v for v in (_to_float(t) for t in _NUM.findall(text)) if v is not None]


def check(output: str, payload: Any) -> Grounding:
    """Is every figure in `output` supported by `payload` (anything JSON-serialisable)?"""
    blob = payload if isinstance(payload, str) else json.dumps(payload, default=str, ensure_ascii=False)
    allowed_dates, blob_wo_dates = find_dates(blob)
    allowed = set(numbers_in(blob))
    allowed |= {float(round(a)) for a in allowed}
    out_dates, out_wo_dates = find_dates(output)
    bad_dates = sorted(d.isoformat() for d in out_dates if d not in allowed_dates)
    bad_nums = []
    for tok in _NUM.findall(out_wo_dates):
        v = _to_float(tok)
        if v is None or v in SMALL_OK:
            continue
        if v not in allowed and not any(abs(v - a) < 0.005 for a in allowed):
            bad_nums.append(tok)
    return Grounding(ok=not bad_dates and not bad_nums, ungrounded_numbers=bad_nums, ungrounded_dates=bad_dates)
