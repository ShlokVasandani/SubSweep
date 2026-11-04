import pytest
from subsweep.ingest.amounts import parse_amount, normalize_currency, currency_from_header


@pytest.mark.parametrize("raw,value,cur", [
    ("$1,234.56", 1234.56, "USD"),
    ("1234.56", 1234.56, None),
    ("(45.00)", -45.0, None),
    ("(€9.99)", -9.99, "EUR"),
    ("-45", -45.0, None),
    ("45.00-", -45.0, None),
    ("−12.50", -12.5, None),          # unicode minus
    ("+12", 12.0, None),
    ("  $ 7.00  ", 7.0, "USD"),
    ("1,23,456.00", 123456.0, None),        # Indian grouping
    ("-₹1,23,456.00", -123456.0, "INR"),
    ("₹12,34,56,789.50", 123456789.5, "INR"),
    ("Rs. 499", 499.0, "INR"),
    ("Rs 1,299.00", 1299.0, "INR"),
    ("INR 2,000", 2000.0, "INR"),
    ("USD 12.00", 12.0, "USD"),
    ("€12,50", 12.5, "EUR"),                # decimal comma
    ("1.234,56 €", 1234.56, "EUR"),         # European grouping
    ("1.234.567", 1234567.0, None),
    ("12,345", 12345.0, None),              # 3 digits after comma = grouping
    ("500 DR", -500.0, None),
    ("500 Dr.", -500.0, None),
    ("1,000.00 CR", 1000.0, None),
    (" $15.49 ", 15.49, "USD"),   # non-breaking spaces
    ("0.00", 0.0, None),
    (15, 15.0, None),
    (-3.5, -3.5, None),
])
def test_parse_amount_valid(raw, value, cur):
    got, c = parse_amount(raw)
    assert got == pytest.approx(value)
    assert c == cur


@pytest.mark.parametrize("raw", ["", "   ", "abc", "N/A", "-", "--", None, "nan", "12.3.4.x"])
def test_parse_amount_unreadable(raw):
    assert parse_amount(raw)[0] is None


@pytest.mark.parametrize("raw,code", [
    ("₹", "INR"), ("$", "USD"), ("€", "EUR"), ("inr", "INR"), ("Rs", "INR"), (" usd ", "USD"),
    ("", None), ("???", None),
])
def test_normalize_currency(raw, code):
    assert normalize_currency(raw) == code


@pytest.mark.parametrize("header,code", [
    ("Amount (INR)", "INR"), ("Debit ₹", "INR"), ("Amount USD", "USD"), ("Amount", None),
])
def test_currency_from_header(header, code):
    assert currency_from_header(header) == code
