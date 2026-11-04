"""Reproducible demo statement: about ten subscriptions with planted findings, plus everyday spending that must NOT be flagged.

`demo_statement_csv(seed=42)` returns a generic-bank CSV (Date,Description,Amount; debits negative) covering 24 months
ending 2026-10-31. (24 months, not 12: an annual renewal can only be detected from two yearly charges ~365 days apart,
so a 12-month window could never contain a yearly charge AND show it coming due.)

Planted, in bank-statement wording that exercises the cleaner:
  DUPLICATE            Streamly Plus billed monthly ($9.99) AND yearly ($99.99), both through PayPal
  PRICE_HIKE           Netflix $15.49 -> $17.99 from April 2026
  POSSIBLY_FORGOTTEN   Hulu, $7.99 every month for 24 months, identical, discretionary
  ANNUAL_RENEWAL_SOON  Namecheap domain, $13.98 yearly, next charge around 12 Nov 2026
  PAYPAL_MISMATCH      FitPass Gym keeps charging $24.00 after PayPal shows it CANCELLED (needs PayPal data; see data/paypal_mock.json)
Healthy subscriptions: Adobe Creative Cloud, iCloud+, CloudBox Storage, Spotify (newer, 5 months).
Never flagged: groceries, fuel, coffee, rideshare, marketplace shopping, salary.
"""
from __future__ import annotations

import calendar
import random
from datetime import date, timedelta

END = date(2026, 10, 31)
MONTHS = 24


def _month_iter(end: date, months: int):
    y, m = end.year, end.month
    out = []
    for _ in range(months):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def _day(y: int, m: int, d: int) -> date:
    return date(y, m, min(d, calendar.monthrange(y, m)[1]))


def generate_demo_rows(seed: int = 42, end: date = END, months: int = MONTHS) -> list[tuple[date, str, float]]:
    rng = random.Random(seed)
    rows: list[tuple[date, str, float]] = []
    start = _day(*_month_iter(end, months)[0], 1)

    def add(d: date, desc: str, amount: float):
        if start <= d <= end:
            rows.append((d, desc, round(amount, 2)))

    for y, m in _month_iter(end, months):
        jitter = lambda base: _day(y, m, base) + timedelta(days=rng.choice([0, 0, 0, 1, -1]))   # real billers drift a day
        # --- subscriptions
        add(jitter(15), f"NETFLIX.COM LOS GATOS CA", -(17.99 if (y, m) >= (2026, 4) else 15.49))
        add(jitter(20), f"HULU 877-8244858 CA", -7.99)
        add(_day(y, m, 5), "PAYPAL *STREAMLY 402-935-7733", -9.99)
        add(jitter(3), "PAYPAL *CLOUDBOX 4029357733", -2.99)
        add(_day(y, m, 10), "PAYPAL *FITPASS GYM 4029357733", -24.00)
        add(_day(y, m, 22), "ADOBE *CREATIVE CLOUD 800-833-6687 CA", -54.99)
        add(_day(y, m, 8), "APPLE.COM/BILL 866-712-7753 CA", -2.99)
        if (y, m) >= (2026, 6):
            add(_day(y, m, 12), "SPOTIFY P3A8E1 STOCKHOLM SE", -10.99)
        # --- income
        add(_day(y, m, 1), "PAYROLL DEPOSIT ACME CORP", 3200.00)
        # --- everyday spending (must not be flagged)
        for wk, base in enumerate((3, 10, 17, 24)):
            add(_day(y, m, base) + timedelta(days=rng.randint(0, 2)), f"KROGER #4411 CINCINNATI OH", -rng.uniform(38, 96))
        for base in (6, 21):
            add(_day(y, m, base) + timedelta(days=rng.randint(0, 3)), f"SHELL OIL 57444 HOUSTON TX", -rng.uniform(31, 52))
        for _ in range(rng.randint(5, 9)):
            add(_day(y, m, rng.randint(1, 28)), rng.choice(["STARBUCKS STORE 12044 SEATTLE WA", "BLUE BOTTLE COFFEE", "SQ *CORNER CAFE"]), -rng.uniform(3.8, 9.5))
        for _ in range(rng.randint(1, 3)):
            add(_day(y, m, rng.randint(1, 28)), "UBER *TRIP HELP.UBER.COM", -rng.uniform(8, 34))
        for _ in range(rng.randint(0, 2)):
            add(_day(y, m, rng.randint(1, 28)), "AMZN Mktp US*2K4LM9 Amzn.com/bill WA", -rng.uniform(9, 140))
    # --- yearly charges
    for yr in (2024, 2025):
        add(date(yr, 11, 12), "NAMECHEAP.COM PHOENIX AZ", -13.98)         # next due ~12 Nov 2026
    for yr in (2025, 2026):
        add(date(yr, 10, 8), "PAYPAL *STREAMLY ANNUAL 4029357733", -99.99)  # duplicates the monthly plan
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def demo_statement_csv(seed: int = 42, end: date = END, months: int = MONTHS) -> str:
    lines = ["Date,Description,Amount"]
    for d, desc, amt in generate_demo_rows(seed, end, months):
        lines.append(f'{d.isoformat()},"{desc}",{amt:.2f}')
    return "\n".join(lines) + "\n"
