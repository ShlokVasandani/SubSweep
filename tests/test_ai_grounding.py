import json
import pytest

from subsweep.ai.grounding import check, find_dates, numbers_in

PAYLOAD = {"old": 10.99, "new": 12.99, "pct": 18.2, "since": "4 Jul 2026", "year": 131.88, "count": 6, "text": "went up on 4 Jul 2026"}


@pytest.mark.parametrize("out", [
    "It went from $10.99 to $12.99 per month.",
    "That is +18.2% since 4 Jul 2026.",
    "About $131.88 a year.",
    "About $132 a year.",                         # whole-number rounding of a payload number
    "It has held for 6 charges.",
    "Jul 4, 2026 was the first charge at the new price.",
    "On 2026-07-04 the price changed.",
    "You were charged twice (2 charges) and once (1).",     # counts 1-3 are always fine
    "No figures at all, just words.",
])
def test_grounded_text_passes(out):
    assert check(out, PAYLOAD).ok, check(out, PAYLOAD)


@pytest.mark.parametrize("out,bad", [
    ("It went up to $14.99.", "14.99"),
    ("That is +25% more.", "25"),
    ("You paid $500.00 in total.", "500.00"),
    ("Started 5 Jul 2026.", None),
    ("Back on Oct 12, 2026.", None),
    ("It was 2026-01-01.", None),
    ("About $133 a year.", "133"),
    ("That is 7 charges.", "7"),
])
def test_invented_figures_fail(out, bad):
    g = check(out, PAYLOAD)
    assert not g.ok
    if bad:
        assert bad in g.ungrounded_numbers
    else:
        assert g.ungrounded_dates


def test_thousands_separators_and_trailing_zeros():
    assert check("That is $1,234.50 a year.", {"x": 1234.5}).ok
    assert check("That is $12.90.", {"x": "12.9"}).ok


def test_payload_can_be_a_string_and_dates_inside_it_count():
    assert check("Renews on 20 Oct 2026.", "renewal 20 Oct 2026").ok
    assert not check("Renews on 21 Oct 2026.", "renewal 20 Oct 2026").ok


def test_date_numbers_do_not_count_as_ungrounded_figures_when_the_date_is_allowed():
    g = check("On 4 Jul 2026 it rose.", {"since": "4 Jul 2026"})
    assert g.ok and g.ungrounded_numbers == []


def test_ordinal_and_full_month_names_are_understood():
    assert find_dates("on 4th July 2026 and September 9, 2026")[0] == {__import__("datetime").date(2026, 7, 4), __import__("datetime").date(2026, 9, 9)}


def test_numbers_in_ignores_digits_inside_words():
    assert numbers_in("abc123 and 4.50 and 1,000") == [4.5, 1000.0]


def test_impossible_dates_are_not_dates():
    assert find_dates("on 31 Feb 2026")[0] == set()


def test_float_noise_in_the_payload_does_not_cause_false_alarms():
    noisy = {"fee": 0.1 + 0.2}                                                    # 0.30000000000000004 once serialised
    assert "0.30000000000000004" in json.dumps(noisy)
    assert check("The fee is $0.30.", noisy).ok
    assert not check("The fee is $0.31.", noisy).ok
