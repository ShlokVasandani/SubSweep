"""Regular-expression denial of service: a crafted description must never make the server work for more than a moment.

Found in Phase 8 (a flaky timing test turned out to be a real quadratic pattern): `clean_merchant("x" * 6000)` ran for over ten seconds, and the redactor
needed 17 seconds for 20,000 characters. A CSV cell can be about 128 KB, so one small upload could have pinned a CPU for minutes. The fixes are
(1) linear patterns (possessive quantifiers and start-of-run lookbehinds) and (2) hard caps on the length of any description or text sent for
redaction. These tests drive both layers with hostile input, each call guarded by a timer so a regression fails fast instead of hanging the suite.
"""
import re
import signal
import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from subsweep.ai import AIAssistant, AuditLog
from subsweep.ai import redact
from subsweep.api.app import create_app
from subsweep.ingest import merchants

pytestmark = pytest.mark.skipif(not hasattr(signal, "SIGALRM"), reason="needs SIGALRM to cut off a runaway pattern")

CHARS = ["x", "*", "•", "9", "a", " ", "-", ".", ":", "#", "/", "(", "@", "A1", "1 ", "x-", "9,", "1.", "0/", "1-"]
PREFIXES = ["", "card ", "a/c ", "acct ", "ref ", "txn ", "ending ", "tel ", "IBAN ", "+1 ", "PayPal *", "UPI/", "#"]


def nasty_inputs(n: int):
    for p in PREFIXES:
        for c in CHARS:
            yield f"{p!r}+{c!r}*{n // len(c)}", p + c * (n // len(c))


class Slow(Exception):
    pass


@contextmanager
def within(seconds: float):
    def fire(*_):
        raise Slow()
    old = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    t0 = time.perf_counter()
    try:
        yield lambda: time.perf_counter() - t0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def assert_fast(fn, text: str, label: str, limit: float):
    try:
        with within(limit):
            fn(text)
    except Slow:
        pytest.fail(f"{label}: still running after {limit}s on {len(text)} characters (regex denial of service)")


# ---------------------------------------------------------------- every redaction pattern is linear
@pytest.mark.parametrize("index", range(len(redact._PATTERNS)))
def test_each_redaction_pattern_is_fast_on_hostile_input(index):
    pat, token = redact._PATTERNS[index]
    for label, text in nasty_inputs(redact.MAX_REDACT_CHARS):
        assert_fast(lambda t: pat.sub(token, t), text, f"redact pattern #{index} {pat.pattern[:50]!r} on {label}", 1.0)


@pytest.mark.parametrize("index", range(len(redact._PATTERNS)))
def test_each_redaction_pattern_scales_linearly(index):
    """Absolute time depends on the machine, growth does not: 4x the input must cost about 4x (linear), never 16x (quadratic)."""
    pat, token = redact._PATTERNS[index]
    for c in CHARS:
        small, large = c * (10_000 // len(c)), c * (40_000 // len(c))
        try:
            with within(3.0) as elapsed_small:
                pat.sub(token, small)
                t_small = elapsed_small()
            with within(3.0) as elapsed_large:
                pat.sub(token, large)
                t_large = elapsed_large()
        except Slow:
            pytest.fail(f"redact pattern #{index} {pat.pattern[:50]!r} is far too slow on {c!r} * n")
        assert t_large <= 7 * t_small + 0.03, (
            f"redact pattern #{index} {pat.pattern[:50]!r} on {c!r}: {t_small:.3f}s for 10k chars but {t_large:.3f}s for 40k (quadratic growth)")


def test_each_merchant_pattern_is_fast_at_the_length_cap():
    pats = [(k, v) for k, v in vars(merchants).items() if isinstance(v, re.Pattern)]
    assert len(pats) >= 10
    for name, pat in pats:
        for label, text in nasty_inputs(merchants.MAX_DESCRIPTION_CHARS):
            assert_fast(lambda t: pat.sub(" ", t), text, f"merchants.{name} on {label}", 0.5)


# ---------------------------------------------------------------- end to end, at sizes a CSV cell can really reach
@pytest.mark.parametrize("unit", ["x", "*", "•", "9", "x-", "1 ", "a", " ", "a.", "1-"])
@pytest.mark.parametrize("prefix", ["", "card ", "a/c ", "ref ", "PayPal *"])
def test_clean_merchant_survives_a_huge_cell(unit, prefix):
    text = prefix + unit * (130_000 // len(unit))
    assert_fast(merchants.clean_merchant, text, f"clean_merchant({prefix!r} + {unit!r} * n)", 1.5)


@pytest.mark.parametrize("unit", ["x", "*", "•", "9", "x-", "1 ", "a", " ", "a.", "1-", "@", "a@"])
@pytest.mark.parametrize("prefix", ["", "card ", "a/c ", "tel "])
def test_redact_text_survives_a_huge_string(unit, prefix):
    text = prefix + unit * (200_000 // len(unit))
    assert_fast(redact.redact_text, text, f"redact_text({prefix!r} + {unit!r} * n)", 1.5)


def test_redaction_drops_everything_past_the_cap_instead_of_sending_it_on():
    secret = "4111 1111 1111 1111"
    out, n = redact.redact_text("hello " * 4000 + secret)           # the card number sits beyond the cap
    assert secret not in out and "4111" not in out and out.endswith("[TRUNCATED]") and n >= 1
    short, k = redact.redact_text("paid with 4111 1111 1111 1111 today")
    assert "4111" not in short and "[CARD]" in short and "[TRUNCATED]" not in short


def test_real_descriptions_are_unaffected_by_the_length_cap():
    long_but_real = "SQ *BLUE BOTTLE COFFEE 1355 MARKET ST SAN FRANCISCO CA 94103 REF 8841223 " * 2          # about 150 characters
    assert merchants.clean_merchant(long_but_real) == merchants.clean_merchant(long_but_real[: merchants.MAX_DESCRIPTION_CHARS])
    assert merchants.clean_merchant("NETFLIX.COM 08/12 LOS GATOS CA") == "Netflix"


@pytest.mark.parametrize("raw,fragment", [
    ("XXXX XXXX XXXX 1234", "1234"), ("card xxxx1234", "1234"), ("ACCT ****9876 Spotify", "9876"), ("card no: XXXX 1234", "1234"),
    ("a/c no: 0012-3456-7890", "3456"), ("card 4111-1111-1111-1111", "1111"),
])
def test_the_rewritten_patterns_still_redact_what_they_always_did(raw, fragment):
    assert fragment not in redact.redact_text(raw)[0]
    assert fragment not in merchants.clean_merchant("Spotify " + raw)


# ---------------------------------------------------------------- through the real API
def test_a_crafted_upload_is_processed_in_seconds_not_minutes(tmp_path):
    client = TestClient(create_app(tmp_path / "db.sqlite", AIAssistant(None, AuditLog(tmp_path / "audit.jsonl"))), raise_server_exceptions=False)
    rows = ["Date,Description,Amount"]
    for m in range(1, 9):
        rows.append(f"2026-{m:02d}-05,Netflix,-15.49")
    for i, unit in enumerate(["x", "*", "x-", "1 ", "card " + "1 "]):
        rows.append(f'2026-01-{10 + i:02d},"{unit * (100_000 // len(unit))}",-9.99')                 # hostile cells, each under the 128 KB csv field limit
    t0 = time.perf_counter()
    with within(20):
        r = client.post("/api/analyze", files={"file": ("s.csv", "\n".join(rows).encode(), "text/csv")})
    assert r.status_code in (200, 413, 422) and r.json().get("error", {}).get("code") != "internal"
    assert time.perf_counter() - t0 < 10
