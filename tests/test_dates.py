from datetime import date
import pytest
from subsweep.ingest.dates import parse_date, parse_date_column, infer_dayfirst


@pytest.mark.parametrize("raw,expected", [
    ("2026-10-12", date(2026, 10, 12)),
    ("2026-10-12T10:11:12Z", date(2026, 10, 12)),
    ("2026-10-12 10:11:12", date(2026, 10, 12)),
    ("2026/10/12", date(2026, 10, 12)),
    ("20261012", date(2026, 10, 12)),
    ("12 Oct 2026", date(2026, 10, 12)),
    ("12 October 2026", date(2026, 10, 12)),
    ("Oct 12, 2026", date(2026, 10, 12)),
    ("October 12 2026", date(2026, 10, 12)),
    ("12-Oct-26", date(2026, 10, 12)),
    ("1st Sep 2026", date(2026, 9, 1)),
    ("13/10/2026", date(2026, 10, 13)),     # unambiguous -> day-first
    ("10/13/2026", date(2026, 10, 13)),     # unambiguous -> month-first
    ("13.10.2026", date(2026, 10, 13)),
    ("13-10-26", date(2026, 10, 13)),
    ("10/13/2026 14:03:55", date(2026, 10, 13)),
])
def test_parse_date_formats(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "nan", "32/13/2026", "2026-02-30", "not a date", "Foo 12 2026"])
def test_parse_date_invalid(raw):
    assert parse_date(raw) is None


def test_ambiguous_uses_dayfirst_flag():
    assert parse_date("03/04/2026", dayfirst=True) == date(2026, 4, 3)
    assert parse_date("03/04/2026", dayfirst=False) == date(2026, 3, 4)


def test_infer_dayfirst_from_column():
    vals = ["03/04/2026", "25/04/2026", "01/05/2026"]
    df, why = infer_dayfirst(vals)
    assert df is True and "day-first" in why


def test_infer_monthfirst_from_column():
    vals = ["03/04/2026", "04/25/2026"]
    df, why = infer_dayfirst(vals, default=True)
    assert df is False and "month-first" in why


def test_ambiguous_column_falls_back_to_default():
    vals = ["03/04/2026", "05/06/2026"]
    assert infer_dayfirst(vals, default=True)[0] is True
    assert infer_dayfirst(vals, default=False)[0] is False


def test_column_parse_applies_one_order_consistently():
    # 01/02 alone is ambiguous, but 20/02 in the same column forces day-first for all rows.
    out, dayfirst, _ = parse_date_column(["01/02/2026", "20/02/2026"])
    assert dayfirst is True
    assert out == [date(2026, 2, 1), date(2026, 2, 20)]


def test_column_parse_returns_none_for_bad_cells():
    out, _, _ = parse_date_column(["2026-10-01", "oops", ""])
    assert out[0] == date(2026, 10, 1) and out[1] is None and out[2] is None


def test_two_digit_year_pivot():
    assert parse_date("01/02/99", dayfirst=True) == date(1999, 2, 1)
    assert parse_date("01/02/26", dayfirst=True) == date(2026, 2, 1)
