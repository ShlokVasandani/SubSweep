"""Statement format detection from CSV headers, plus manual column mapping.

Three profiles are recognised automatically:
  * paypal_activity     - PayPal "Activity" export (Gross/Fee/Net, Transaction ID, Status, Type)
  * credit_card_split   - separate Debit and Credit (or Withdrawal / Deposit) columns
  * generic_bank        - date + description + one signed amount column

Detection scans the first rows for the best header row, so bank exports with
preamble lines (account holder, date range, etc.) still work.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field, asdict

PAYPAL = "paypal_activity"
CARD_SPLIT = "credit_card_split"
GENERIC = "generic_bank"
MANUAL = "manual"

HEADER_SCAN_ROWS = 30


def norm_header(h) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(h or "").lower())).strip()


_SYNONYMS: dict[str, set[str]] = {
    "date": {
        "date", "transaction date", "trans date", "txn date", "posting date", "post date",
        "posted date", "date posted", "value date", "booking date", "tran date", "trans dt",
    },
    "description": {
        "description", "narration", "details", "particulars", "memo", "payee", "merchant",
        "transaction description", "transaction details", "original description",
        "remarks", "transaction remarks", "name", "merchant name", "reference", "item title",
    },
    "amount": {"amount", "amt", "transaction amount", "txn amount", "value", "amount in rupees"},
    "debit": {
        "debit", "debits", "debit amount", "debit amt", "withdrawal", "withdrawals",
        "withdrawal amt", "withdrawal amount", "dr", "dr amount", "money out", "paid out",
        "charges", "amount debited",
    },
    "credit": {
        "credit", "credits", "credit amount", "credit amt", "deposit", "deposits",
        "deposit amt", "deposit amount", "cr", "cr amount", "money in", "paid in",
        "payments", "amount credited",
    },
    "currency": {"currency", "ccy", "curr", "currency code", "cur"},
    "type": {
        "type", "transaction type", "txn type", "dr cr", "cr dr", "debit credit",
        "credit debit", "dr cr indicator",
    },
    "gross": {"gross", "gross amount"},
    "fee": {"fee", "fees"},
    "net": {"net", "net amount"},
    "ref": {"transaction id", "txn id", "reference id", "ref no", "reference number", "transaction reference"},
    "status": {"status"},
}

# Order matters for prefix matching below ("amount inr" -> amount).
_PREFIX_FIELDS = [("amount", "amount"), ("currency", "currency")]


def classify_header(h) -> str | None:
    n = norm_header(h)
    if not n:
        return None
    for field_name, syns in _SYNONYMS.items():
        if n in syns:
            return field_name
    # "Amount (INR)", "Amount USD", "Debit (₹)" -> strip trailing currency noise
    first = n.split(" ")[0]
    if n.startswith("amount ") and first == "amount":
        return "amount"
    if n.startswith(("debit ", "withdrawal ")):
        return "debit"
    if n.startswith(("credit ", "deposit ")):
        return "credit"
    return None


@dataclass
class ColumnMapping:
    """Column names (as they appear in the header) for each logical field."""
    date: str | None = None
    description: str | None = None
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    currency: str | None = None
    type: str | None = None  # Dr/Cr indicator for generic_bank
    ref: str | None = None  # transaction / reference id
    status: str | None = None  # PayPal status
    txn_type: str | None = None  # PayPal type
    fee: str | None = None
    invert_sign: bool = False

    def is_complete(self) -> bool:
        return bool(self.date and self.description and (self.amount or self.debit or self.credit))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ColumnMapping":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class Detection:
    format: str
    header_row: int
    delimiter: str
    headers: list[str]
    mapping: ColumnMapping
    confidence: float
    suggestions: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.format != "unknown" and self.mapping.is_complete()


def sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:HEADER_SCAN_ROWS])
    counts = {d: sample.count(d) for d in [",", ";", "\t", "|"]}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def read_rows(text: str, delimiter: str) -> list[list[str]]:
    return [r for r in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _row_signals(row: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for cell in row:
        f = classify_header(cell)
        if f and f not in found:
            found[f] = cell
    return found


def _profile_for(found: dict[str, str]) -> tuple[str, float]:
    has = found.keys()
    if "gross" in has and ("net" in has or "fee" in has or "ref" in has) and "date" in has:
        extras = sum(k in has for k in ("fee", "net", "ref", "status", "type", "currency"))
        return PAYPAL, min(1.0, 0.6 + 0.08 * extras)
    if "date" in has and "description" in has and "debit" in has and "credit" in has:
        return CARD_SPLIT, 0.9
    if "date" in has and "description" in has and "amount" in has:
        return GENERIC, 0.85 if "currency" in has or "type" in has else 0.8
    if "date" in has and "description" in has and ("debit" in has or "credit" in has):
        return CARD_SPLIT, 0.6
    return "unknown", 0.0


def detect_format(rows: list[list[str]], delimiter: str = ",") -> Detection:
    best_idx, best_score, best_found = 0, -1, {}
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        found = _row_signals(row)
        # Prefer rows whose cells are mostly recognised headers.
        score = len(found) * 10 - max(0, len([c for c in row if c.strip()]) - len(found))
        if score > best_score:
            best_idx, best_score, best_found = i, score, found
    headers = rows[best_idx] if rows else []
    fmt, conf = _profile_for(best_found)
    f = best_found
    mapping = ColumnMapping(
        date=f.get("date"),
        description=f.get("description"),
        amount=f.get("gross") if fmt == PAYPAL else f.get("amount"),
        debit=f.get("debit") if fmt == CARD_SPLIT else None,
        credit=f.get("credit") if fmt == CARD_SPLIT else None,
        currency=f.get("currency"),
        type=f.get("type") if fmt == GENERIC else None,
        ref=f.get("ref"),
        status=f.get("status") if fmt == PAYPAL else None,
        txn_type=f.get("type") if fmt == PAYPAL else None,
        fee=f.get("fee") if fmt == PAYPAL else None,
    )
    if fmt == PAYPAL:
        # PayPal's merchant is in "Name"; fall back to item title / subject / note.
        for cell in headers:
            if norm_header(cell) == "name":
                mapping.description = cell
                break
    suggestions = {k: v for k, v in f.items() if k in ("date", "description", "amount", "debit", "credit", "currency")}
    return Detection(fmt, best_idx, delimiter, list(headers), mapping, conf, suggestions)
