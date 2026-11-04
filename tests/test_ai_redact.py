import json

import pytest

from subsweep.ai.redact import redact_obj, redact_text


@pytest.mark.parametrize("raw,fragment", [
    ("Paid with card 4111 1111 1111 1111 today", "4111"),
    ("card 4111-1111-1111-1111", "1111"),
    ("4111111111111111", "4111"),
    ("amex 3782 822463 10005", "10005"),
    ("card no: XXXX 1234 5678", "5678"),
    ("charged to XXXX1234", "1234"),
    ("charged to ****9876", "9876"),
    ("card ending in 4242", "4242"),
    ("account number 123456789012", "123456789012"),
    ("a/c no: 0012-3456-7890", "3456"),
    ("acct #99887766", "99887766"),
    ("IBAN GB82WEST12345698765432", "GB82"),
    ("IBAN GB82WEST12345698765432", "WEST"),
    ("Visa ending in 4242", "4242"),
    ("ending 9876 on the statement", "9876"),
    ("txn 1234567890123456", "1234567890123456"),
    ("ref 987654321", "987654321"),
    ("call +44 7700 900123", "900123"),
    ("phone: 415-555-0199", "555"),
    ("reach me at pat.buyer@example.com", "example.com"),
    ("UPI spotify@okaxis", "okaxis"),
    ("key sk-ant-api03-AbCdEf123456_xyz", "AbCdEf"),
])
def test_identifiers_are_removed(raw, fragment):
    clean, n = redact_text(raw)
    assert fragment not in clean and n >= 1


@pytest.mark.parametrize("raw", [
    "Netflix charged $1,234,567.89 on 2026-10-05", "It went up 18.2% to $12.99 on 4 Jul 2026", "₹1,23,456.00 a year",
    "billed 12 times since 5 Jan 2026", "credit card 2 months ago", "about $131.88 a year", "Streamly Plus (Annual) 99.99",
])
def test_amounts_dates_and_prose_are_left_alone(raw):
    assert redact_text(raw) == (raw, 0)


def test_nested_structures_are_redacted_and_counted():
    obj = {"a": ["card 4111 1111 1111 1111", {"b": "mail me@x.com"}], "n": 5, "f": 1.5, "t": ("XXXX1234",), "none": None}
    clean, n = redact_obj(obj)
    blob = json.dumps(clean)
    assert "4111" not in blob and "me@x.com" not in blob and "1234" not in blob and n == 3
    assert clean["n"] == 5 and clean["f"] == 1.5 and clean["none"] is None


def test_redaction_leaves_non_string_values_untouched():
    assert redact_obj(12345678901234) == (12345678901234, 0)


import time


@pytest.mark.parametrize("nasty", [
    "x" * 6000, "X" * 6000 + "!", "*" * 6000, "xx " * 3000, "x-" * 3000, "1 " * 3000, "-" * 6000, "9" * 6000,
    "a" * 6000, "a" * 6000 + "@", "card " + "1 " * 3000, "card " + "x" * 6000, "a/c " + "1-" * 3000, "+" + "1 " * 3000,
    "IBAN " + "A1" * 3000, "ending " + "in " * 2000, "(" * 6000, "•" * 6000,
])
def test_pathological_inputs_do_not_hang(nasty):
    t0 = time.monotonic()
    redact_text(nasty)
    assert time.monotonic() - t0 < 2.0
