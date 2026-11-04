"""End-to-end statement loading: bytes/text -> clean transactions DataFrame."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import dedupe
from .amounts import currency_from_header, normalize_currency, parse_amount
from .dates import parse_date_column
from .formats import (
    CARD_SPLIT, GENERIC, MANUAL, PAYPAL, ColumnMapping, Detection, decode_bytes,
    detect_format, norm_header, read_rows, sniff_delimiter,
)
from .merchants import clean_merchant, is_paypal_description

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 50_000

OUTPUT_COLUMNS = [
    "id", "date", "merchant_clean", "merchant_raw", "amount", "currency", "source",
    "via_paypal", "source_ref", "possible_duplicate",
]

_PAYPAL_BAD_STATUS = {"denied", "failed", "voided", "canceled", "cancelled", "expired", "declined", "refused", "removed"}
_PAYPAL_NON_SPEND_TYPE = re.compile(
    r"currency conversion|\bhold\b|release|bank deposit|add funds|withdraw|transfer to|"
    r"credit card deposit|authorization|^order\b|reserve|general credit card|account hold|"
    r"funding|top[- ]?up",
    re.I,
)
_FOOTER_RE = re.compile(r"^\s*(?:total|totals|closing balance|opening balance|balance|grand total|summary)\b", re.I)
_DEBIT_WORDS = {"debit", "dr", "d", "withdrawal", "purchase", "sale", "payment", "debited"}
_CREDIT_WORDS = {"credit", "cr", "c", "deposit", "refund", "credited"}


class FormatDetectionError(ValueError):
    """Raised when columns cannot be auto-detected. Carries data for a mapping UI."""

    def __init__(self, message: str, headers: list[str], suggestions: dict, header_row: int = 0, delimiter: str = ","):
        super().__init__(message)
        self.headers = headers
        self.suggestions = suggestions
        self.header_row = header_row
        self.delimiter = delimiter


class StatementTooLargeError(ValueError):
    pass


@dataclass
class StatementResult:
    transactions: pd.DataFrame
    format: str
    mapping: ColumnMapping
    confidence: float = 0.0
    date_order: str = ""
    date_order_reason: str = ""
    rows_read: int = 0
    dropped: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        t = self.transactions
        lines = [
            f"format: {self.format} (confidence {self.confidence:.2f})",
            f"date order: {self.date_order} ({self.date_order_reason})",
            f"rows read: {self.rows_read}, kept: {len(t)}",
        ]
        if self.dropped:
            lines.append("dropped: " + ", ".join(f"{k}={v}" for k, v in self.dropped.items()))
        lines += [f"warning: {w}" for w in self.warnings]
        return "\n".join(lines)


def _as_text(source, max_bytes: int) -> str:
    if isinstance(source, (os.PathLike,)):
        p = Path(source)
        if p.stat().st_size > max_bytes:
            raise StatementTooLargeError(f"File is larger than {max_bytes // (1024 * 1024)} MB.")
        return decode_bytes(p.read_bytes())
    if isinstance(source, (bytes, bytearray)):
        if len(source) > max_bytes:
            raise StatementTooLargeError(f"File is larger than {max_bytes // (1024 * 1024)} MB.")
        return decode_bytes(bytes(source))
    if isinstance(source, str):
        if len(source.encode("utf-8")) > max_bytes:
            raise StatementTooLargeError(f"File is larger than {max_bytes // (1024 * 1024)} MB.")
        return source.lstrip("﻿")
    raise TypeError("source must be bytes, str (CSV text) or a path")


def _col_index(headers: list[str]) -> dict[str, int]:
    idx: dict[str, int] = {}
    for i, h in enumerate(headers):
        idx.setdefault(h, i)
        idx.setdefault(h.strip(), i)
        idx.setdefault(norm_header(h), i)
    return idx


def _cell(row: list[str], idx: dict[str, int], name: str | None) -> str:
    if not name:
        return ""
    i = idx.get(name, idx.get(name.strip(), idx.get(norm_header(name))))
    if i is None or i >= len(row):
        return ""
    return str(row[i]).strip()


def _signed_from_type(value: float, type_cell: str) -> float:
    t = type_cell.strip().lower().rstrip(".")
    if t in _DEBIT_WORDS:
        return -abs(value)
    if t in _CREDIT_WORDS:
        return abs(value)
    return value


def load_statement(
    source,
    mapping: ColumnMapping | dict | None = None,
    *,
    default_currency: str = "USD",
    dayfirst: bool | None = None,
    default_dayfirst: bool = False,
    source_label: str | None = None,
    header_row: int | None = None,
    delimiter: str | None = None,
    max_bytes: int = MAX_BYTES,
    max_rows: int = MAX_ROWS,
) -> StatementResult:
    """Parse a statement CSV into the clean transactions table.

    `mapping` (manual column mapping) overrides auto-detection of columns.
    Raises FormatDetectionError when detection fails and no usable mapping is
    given; the exception carries the headers and best guesses for a mapper UI.
    """
    text = _as_text(source, max_bytes)
    delim = delimiter or sniff_delimiter(text)
    rows = read_rows(text, delim)
    rows = [r for r in rows]
    if not any(any(c.strip() for c in r) for r in rows):
        raise FormatDetectionError("That file looks empty.", [], {}, 0, delim)

    detection: Detection = detect_format(rows, delim)
    if isinstance(mapping, dict):
        mapping = ColumnMapping.from_dict(mapping)

    if mapping is not None:
        fmt = MANUAL
        hdr_row = header_row if header_row is not None else detection.header_row
        confidence = 1.0
        if not mapping.is_complete():
            raise FormatDetectionError(
                "The column mapping needs a date, a description and an amount (or debit/credit) column.",
                detection.headers, detection.suggestions, hdr_row, delim,
            )
    else:
        if not detection.ok:
            raise FormatDetectionError(
                "We couldn't tell which columns hold the date, description and amount. "
                "Pick them below and we'll take it from there.",
                detection.headers, detection.suggestions, detection.header_row, delim,
            )
        fmt, mapping, confidence = detection.format, detection.mapping, detection.confidence
        hdr_row = header_row if header_row is not None else detection.header_row

    headers = [h.strip() for h in rows[hdr_row]]
    idx = _col_index(headers)
    for field_name in ("date", "description", "amount", "debit", "credit"):
        col = getattr(mapping, field_name)
        if col and col not in idx and col.strip() not in idx and norm_header(col) not in idx:
            raise FormatDetectionError(
                f"The column '{col}' isn't in this file. Please pick it again.",
                headers, detection.suggestions, hdr_row, delim,
            )

    # A currency named in the amount header ("Amount (INR)") beats the caller's default.
    for col in (mapping.amount, mapping.debit, mapping.credit):
        if col and (hc := currency_from_header(col)):
            default_currency = hc
            break

    body = [r for r in rows[hdr_row + 1:] if any(c.strip() for c in r)]
    if len(body) > max_rows:
        raise StatementTooLargeError(f"This file has more than {max_rows:,} rows, which is over our limit.")

    # PayPal: fall back to other description-ish columns when Name is blank.
    fallback_cols = []
    if fmt == PAYPAL:
        fallback_cols = [h for h in headers if norm_header(h) in {"item title", "subject", "note", "description"}]

    result_warnings: list[str] = []
    dropped: dict[str, int] = {}

    def drop(reason: str):
        dropped[reason] = dropped.get(reason, 0) + 1

    dates, used_dayfirst, reason = parse_date_column(
        [_cell(r, idx, mapping.date) for r in body], dayfirst, default_dayfirst
    )

    recs = []
    for n, (row, d) in enumerate(zip(body, dates)):
        line_no = hdr_row + 2 + n
        desc = re.sub(r"\s+", " ", _cell(row, idx, mapping.description)).strip()
        if not desc:
            for fc in fallback_cols:
                desc = re.sub(r"\s+", " ", _cell(row, idx, fc)).strip()
                if desc:
                    break

        if fmt == PAYPAL:
            status = _cell(row, idx, mapping.status).lower()
            ttype = _cell(row, idx, mapping.txn_type)
            if status in _PAYPAL_BAD_STATUS:
                drop(f"paypal status '{status}'")
                continue
            if _PAYPAL_NON_SPEND_TYPE.search(ttype):
                drop("paypal non-spend row (conversion/funding/hold)")
                continue

        amount = None
        cur_from_amount = None
        if mapping.debit or mapping.credit:
            dv, dc = parse_amount(_cell(row, idx, mapping.debit)) if mapping.debit else (None, None)
            cv, cc = parse_amount(_cell(row, idx, mapping.credit)) if mapping.credit else (None, None)
            if dv is None and cv is None:
                amount = None
            else:
                amount = abs(cv or 0.0) - abs(dv or 0.0)
            cur_from_amount = dc or cc
            if amount is None and mapping.amount:
                amount, cur_from_amount = parse_amount(_cell(row, idx, mapping.amount))
        else:
            amount, cur_from_amount = parse_amount(_cell(row, idx, mapping.amount))
            if amount is not None and mapping.type:
                amount = _signed_from_type(amount, _cell(row, idx, mapping.type))
        if amount is not None and mapping.invert_sign:
            amount = -amount

        if d is None and (_FOOTER_RE.match(desc) or _FOOTER_RE.match(_cell(row, idx, mapping.date))):
            drop("blank or footer row")
            continue
        if d is None or amount is None:
            if d is None and amount is None:
                drop("blank or footer row")  # totals, balances, disclaimers
            else:
                drop("unreadable date" if d is None else "unreadable amount")
                result_warnings.append(f"Skipped line {line_no}: couldn't read the {'date' if d is None else 'amount'}.")
            continue

        cur = normalize_currency(_cell(row, idx, mapping.currency)) or cur_from_amount or default_currency
        raw = desc or "(no description)"
        recs.append({
            "date": d,
            "merchant_raw": raw,
            "merchant_clean": clean_merchant(raw) or raw,
            "amount": round(float(amount), 2),
            "currency": cur,
            "source": f"upload:{source_label or fmt}",
            "via_paypal": fmt == PAYPAL or is_paypal_description(raw),
            "source_ref": _cell(row, idx, mapping.ref),
        })

    df = pd.DataFrame(recs, columns=[
        "date", "merchant_raw", "merchant_clean", "amount", "currency", "source", "via_paypal", "source_ref",
    ])
    kept_before_dedupe = len(df)
    df = dedupe.drop_repeated_refs(df)
    if len(df) < kept_before_dedupe:
        dropped["exact duplicate (same transaction ID)"] = kept_before_dedupe - len(df)
    df = df.sort_values("date", kind="stable").reset_index(drop=True)
    df = dedupe.flag_same_day_repeats(df)
    df = dedupe.assign_ids(df)
    df = df[OUTPUT_COLUMNS]

    if len(df) >= 5 and (df["amount"] > 0).mean() > 0.95:
        result_warnings.append(
            "Almost every amount is positive. If these are purchases, set 'invert sign' so charges count as negative."
        )
    if len(df) == 0:
        result_warnings.append("No usable transactions were found in this file.")

    return StatementResult(
        transactions=df,
        format=fmt,
        mapping=mapping,
        confidence=confidence,
        date_order="day-first (dd/mm)" if used_dayfirst else "month-first (mm/dd)",
        date_order_reason=reason,
        rows_read=len(body),
        dropped=dropped,
        warnings=result_warnings,
    )


def merge_statements(results: list[StatementResult] | list[pd.DataFrame]) -> pd.DataFrame:
    """Merge several loaded statements; overlap removed, same-day repeats kept."""
    frames = [r.transactions if isinstance(r, StatementResult) else r for r in results]
    merged = dedupe.merge_multiset(frames)
    if merged.empty:
        return merged
    merged = merged.drop(columns=["possible_duplicate", "id"], errors="ignore")
    merged = merged.sort_values("date", kind="stable").reset_index(drop=True)
    merged = dedupe.flag_same_day_repeats(merged)
    merged = dedupe.assign_ids(merged)
    return merged[OUTPUT_COLUMNS]
