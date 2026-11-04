"""Fixtures for the AI-layer tests: a scripted fake model and a realistic analysis context."""
from datetime import date

from subsweep.ai import AnalysisContext, LLMResult
from subsweep.detect import detect_recurring
from subsweep.findings import generate_findings
from tests.helpers import series, tx

D = date
TODAY = D(2026, 9, 30)


class FakeLLM:
    """script: a callable(system, user, feature) -> str | Exception, a list of those, or a plain string."""

    def __init__(self, script="OK", model="fake-model"):
        self.script, self.model, self.calls = script, model, []

    def complete(self, *, system, user, max_tokens=1024, feature=""):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens, "feature": feature})
        s = self.script
        if isinstance(s, list):
            s = s.pop(0) if len(s) > 1 else s[0]
        if callable(s):
            s = s(system=system, user=user, feature=feature)
        if isinstance(s, Exception):
            raise s
        return LLMResult(text=s, model=self.model, stop_reason="end_turn", usage={"input_tokens": 10, "output_tokens": 5},
                         request_id="req_fake", latency_ms=7)

    @property
    def last_user(self):
        return self.calls[-1]["user"]


def scenario_rows():
    return (
        series("Netflix", D(2025, 10, 5), 30, [13.0] * 6 + [15.49] * 6)                       # hike, streaming, forgotten-ish
        + series("Spotify", D(2026, 1, 5), 30, [10.99] * 9)                                   # music
        + series("Streamly", D(2026, 1, 5), 30, [9.99] * 9)                                   # duplicate pair (direct + PayPal)
        + series("Streamly", D(2026, 1, 16), 30, [9.99] * 9, via_paypal=True)
        + series("Domainly", D(2023, 10, 20), 365, [49.0] * 3)                                # yearly, renews 19 Oct 2026 (leap year drift)
        + [(D(2026, 9, 30), "Anchor Store", -1.0)]
    )


def make_ctx(rows=None, today=TODAY, **findings_kw):
    t = tx(rows if rows is not None else scenario_rows())
    subs = detect_recurring(t).subscriptions
    rep = generate_findings(subs, t, today=today, **findings_kw)
    return AnalysisContext(subs, t, rep, today)


def find(ctx, ftype, merchant=None):
    return next(f for f in ctx.report.findings if f.type == ftype and (merchant is None or f.merchant == merchant))
