"""Phase 3 tests: every finding rule has a fixture statement and a case that fails without the rule."""
from datetime import date, timedelta

import pytest

from subsweep.detect import detect_recurring, load_rules
from subsweep.findings import (
    ANNUAL_RENEWAL_SOON, DUPLICATE, POSSIBLY_FORGOTTEN, PRICE_HIKE, ZOMBIE, generate_findings,
)
from subsweep.findings.engine import compute_savings, forgotten_score
from subsweep.findings.money import fdate, money
from tests.helpers import series, tx

D = date


def analyse(rows, today=None, rules=None, **kw):
    rules = rules or load_rules()
    t = tx(rows)
    subs = detect_recurring(t, rules=rules).subscriptions
    return generate_findings(subs, t, rules=rules, today=today, **kw), subs, t


def of(rep, ftype):
    return [f for f in rep.findings if f.type == ftype]


# keep an anchor charge so every fixture statement ends on the same date
END = D(2026, 9, 30)
ANCHOR = [(END, "Anchor Store", -1.0)]


# ---------------------------------------------------------------- money / date formatting
@pytest.mark.parametrize("amt,cur,text", [(10.99, "USD", "$10.99"), (1234.5, "INR", "₹1,234.50"),
                                         (9, "EUR", "€9.00"), (5, "CAD", "CAD 5.00")])
def test_money_format(amt, cur, text):
    assert money(amt, cur) == text


def test_date_format():
    assert fdate(D(2026, 10, 5)) == "5 Oct 2026"


# ---------------------------------------------------------------- DUPLICATE
def spotify_monthly_and_annual():
    return (series("Spotify", D(2026, 1, 12), 30, [10.99] * 10)
            + series("Spotify", D(2024, 2, 10), 365, [109.99] * 3) + ANCHOR)


def test_monthly_plus_annual_is_a_duplicate_with_exact_amounts():
    rep, subs, _ = analyse(spotify_monthly_and_annual())
    (f,) = of(rep, DUPLICATE)
    assert f.severity == "high" and len(f.subscription_ids) == 2
    assert "$10.99 per month" in f.reason and "$109.99 per year" in f.reason
    assert "monthly and yearly" in f.reason
    assert f.potential_savings_annual == pytest.approx(109.99)   # keep the pricier-per-year monthly plan


def test_paypal_and_direct_duplicate_names_the_channels():
    rows = (series("Streamly", D(2026, 1, 5), 30, [9.99] * 9)
            + series("Streamly", D(2026, 1, 16), 30, [9.99] * 9, via_paypal=True) + ANCHOR)
    rep, *_ = analyse(rows)
    (f,) = of(rep, DUPLICATE)
    assert "One is billed through PayPal and another directly." in f.reason
    assert f.potential_savings_annual == pytest.approx(9.99 * 12)


def test_double_billing_same_day_is_a_duplicate():
    base = series("Streamly", D(2026, 1, 5), 30, [9.99] * 9)
    rep, *_ = analyse(base + base + ANCHOR)
    assert len(of(rep, DUPLICATE)) == 1


def test_duplicate_requires_both_streams_active():
    rows = (series("Spotify", D(2026, 1, 12), 30, [10.99] * 10)
            + series("Spotify", D(2023, 1, 1), 30, [10.99] * 6) + ANCHOR)   # second one ended in 2023
    assert of(analyse(rows)[0], DUPLICATE) == []


def test_different_services_are_not_duplicates():
    rows = series("Netflix", D(2026, 1, 5), 30, [15.49] * 9) + series("Hulu", D(2026, 1, 9), 30, [7.99] * 9) + ANCHOR
    assert of(analyse(rows)[0], DUPLICATE) == []


def test_duplicate_does_not_cross_currencies():
    rows = (series("Cloudly", D(2026, 1, 5), 30, [9.99] * 9)
            + series("Cloudly", D(2026, 1, 9), 30, [799.0] * 9, currency="INR") + ANCHOR)
    assert of(analyse(rows)[0], DUPLICATE) == []


def test_three_streams_keep_the_priciest_and_count_the_other_two():
    rows = (series("Streamly", D(2026, 1, 5), 30, [20.0] * 9) + series("Streamly", D(2026, 1, 12), 30, [10.0] * 9)
            + series("Streamly", D(2026, 1, 19), 30, [5.0] * 9) + ANCHOR)
    (f,) = of(analyse(rows)[0], DUPLICATE)
    assert f.potential_savings_annual == pytest.approx((10 + 5) * 12)
    assert len(f.evidence["streams"]) == 3


# ---------------------------------------------------------------- PRICE_HIKE
def test_price_hike_finding_shows_old_new_and_annual_impact():
    rows = series("Streamly", D(2026, 1, 5), 30, [10.99] * 5 + [12.99] * 4) + ANCHOR
    (f,) = of(analyse(rows)[0], PRICE_HIKE)
    assert "from $10.99 to $12.99 per month" in f.reason and "+18.2%" in f.reason
    assert "adds $24.00 a year" in f.reason
    assert f.severity == "medium" and f.potential_savings_annual == pytest.approx(24.0)
    assert f.evidence["since"] == D(2026, 1, 5) + timedelta(days=150)


def test_big_hike_is_high_severity():
    rows = series("Streamly", D(2026, 1, 5), 30, [10.0] * 5 + [14.0] * 4) + ANCHOR
    assert of(analyse(rows)[0], PRICE_HIKE)[0].severity == "high"


def test_severity_threshold_is_configurable():
    rows = series("Streamly", D(2026, 1, 5), 30, [10.0] * 5 + [14.0] * 4) + ANCHOR
    rules = load_rules(overrides={"findings": {"price_hike": {"high_if_pct_at_least": 50}}})
    assert of(analyse(rows, rules=rules)[0], PRICE_HIKE)[0].severity == "medium"


def test_decrease_and_pending_change_are_not_price_hike_findings():
    down = series("Streamly", D(2026, 1, 5), 30, [15.0] * 5 + [10.0] * 5) + ANCHOR
    assert len(analyse(down)[2]) == 11 and detect_recurring(analyse(down)[2]).subscriptions.iloc[0].active
    pending = series("Streamly", D(2026, 1, 5), 30, [10.0] * 8 + [12.0]) + ANCHOR
    assert of(analyse(down)[0], PRICE_HIKE) == [] and of(analyse(pending)[0], PRICE_HIKE) == []


def test_multiple_hikes_mention_the_count():
    rows = series("Streamly", D(2025, 10, 5), 30, [10.0] * 4 + [12.0] * 4 + [15.0] * 4) + ANCHOR
    (f,) = of(analyse(rows)[0], PRICE_HIKE)
    assert "latest of 2 increases" in f.reason and f.evidence["new_amount"] == 15.0


def test_ended_subscription_gets_no_price_hike():
    rows = series("Streamly", D(2025, 1, 5), 30, [10.0] * 4 + [14.0] * 4) + ANCHOR
    assert of(analyse(rows)[0], PRICE_HIKE) == []


# ---------------------------------------------------------------- POSSIBLY_FORGOTTEN
def test_long_run_discretionary_autopay_is_flagged_as_a_guess_with_visible_reasons():
    rows = series("Netflix", D(2026, 1, 5), 30, [15.49] * 10) + ANCHOR
    (f,) = of(analyse(rows)[0], POSSIBLY_FORGOTTEN)
    assert f.is_guess and f.severity == "medium"
    assert [h["rule"] for h in f.heuristics] == ["discretionary_category", "long_run", "identical_amounts"]
    assert sum(h["points"] for h in f.heuristics) == f.evidence["score"] == 5
    assert "guess" in f.reason and "still using" in f.reason and "$15.49 per month" in f.reason
    assert "$185.88 a year" in f.reason
    assert f.potential_savings_annual == pytest.approx(185.88)


def test_short_history_is_not_flagged():
    assert of(analyse(series("Netflix", D(2026, 7, 5), 30, [15.49] * 4) + ANCHOR)[0], POSSIBLY_FORGOTTEN) == []


def test_non_discretionary_software_is_not_flagged_on_history_alone():
    assert of(analyse(series("Adobe", D(2026, 1, 5), 30, [54.99] * 10) + ANCHOR)[0], POSSIBLY_FORGOTTEN) == []


def test_unknown_merchants_are_not_guessed_about():
    assert of(analyse(series("Mystery Box Co", D(2026, 1, 5), 30, [12.0] * 10) + ANCHOR)[0], POSSIBLY_FORGOTTEN) == []


@pytest.mark.parametrize("rule", ["discretionary_category", "long_run", "identical_amounts"])
def test_each_heuristic_is_load_bearing(rule):
    rows = series("Netflix", D(2026, 1, 5), 30, [15.49] * 10) + ANCHOR
    zero = load_rules(overrides={"findings": {"forgotten": {"points": {rule: 0}, "min_score_to_flag": 5}}})
    assert of(analyse(rows, rules=zero)[0], POSSIBLY_FORGOTTEN) == []
    assert len(of(analyse(rows, rules=load_rules(overrides={"findings": {"forgotten": {"min_score_to_flag": 5}}}))[0],
                  POSSIBLY_FORGOTTEN)) == 1


def test_overlapping_services_add_a_point():
    rows = (series("Netflix", D(2026, 6, 5), 30, [15.49] * 5) + series("Hulu", D(2026, 6, 9), 30, [7.99] * 5)
            + series("Disney Plus", D(2026, 6, 13), 30, [10.99] * 5) + ANCHOR)
    rep, subs, _ = analyse(rows)
    assert len(of(rep, POSSIBLY_FORGOTTEN)) == 3
    assert any(h["rule"] == "overlapping_services" and "3 streaming services" in h["text"]
               for h in of(rep, POSSIBLY_FORGOTTEN)[0].heuristics)
    alone = analyse(series("Netflix", D(2026, 6, 5), 30, [15.49] * 5) + ANCHOR)[0]
    assert of(alone, POSSIBLY_FORGOTTEN) == []     # same history, no overlap -> below threshold


def test_score_function_is_inspectable():
    rows = series("Netflix", D(2026, 1, 5), 30, [15.49] * 10) + ANCHOR
    _, subs, _ = analyse(rows)
    active = subs[subs.active]
    score, parts = forgotten_score(active.iloc[0], active, load_rules())
    assert score == 5 and len(parts) == 3


def test_changing_price_removes_the_autopay_point():
    rows = series("Netflix", D(2026, 1, 5), 30, [13.0] * 5 + [15.49] * 5) + ANCHOR
    rep, *_ = analyse(rows)
    f = of(rep, POSSIBLY_FORGOTTEN)
    assert f == [] or "identical_amounts" not in [h["rule"] for h in f[0].heuristics]


def test_still_using_dismissal_clears_it_from_savings_and_health():
    rows = series("Netflix", D(2026, 1, 5), 30, [15.49] * 10) + ANCHOR
    before = analyse(rows)[0]
    fid = of(before, POSSIBLY_FORGOTTEN)[0].id
    after = analyse(rows, dismissed=[fid])[0]
    f = of(after, POSSIBLY_FORGOTTEN)[0]
    assert f.status == "dismissed" and after.summary["open_findings"] == 0
    assert before.summary["potential_savings_annual"] > 0 == after.summary["potential_savings_annual"]
    assert after.summary["health"]["score"] > before.summary["health"]["score"]


# ---------------------------------------------------------------- ZOMBIE
def test_zombie_after_last_use():
    rows = series("Gymly", D(2026, 1, 5), 30, [29.0] * 10) + ANCHOR
    rep, *_ = analyse(rows, usage_signals={"gymly": {"last_used": D(2026, 3, 1)}})
    (f,) = of(rep, ZOMBIE)
    assert f.severity == "high" and "last used Gymly on 1 Mar 2026" in f.reason
    assert f.evidence["charges_after"] == 8 and f.evidence["total_after"] == pytest.approx(232.0)


def test_zombie_after_trial_end():
    rows = series("Appy", D(2026, 8, 1), 30, [9.99] * 3) + ANCHOR
    rep, *_ = analyse(rows, usage_signals={"appy": {"trial_end": D(2026, 8, 8)}})
    (f,) = of(rep, ZOMBIE)
    assert "trial ended on 8 Aug 2026" in f.reason and f.evidence["charges_after"] == 2


def test_zombie_needs_a_signal_and_enough_gap():
    rows = series("Gymly", D(2026, 1, 5), 30, [29.0] * 10) + ANCHOR
    assert of(analyse(rows)[0], ZOMBIE) == []
    recent = analyse(rows, usage_signals={"gymly": {"last_used": D(2026, 10, 20)}})[0]
    assert of(recent, ZOMBIE) == []


def test_zombie_min_days_is_configurable():
    rows = series("Gymly", D(2026, 1, 5), 30, [29.0] * 10) + ANCHOR
    sig = {"gymly": {"last_used": D(2026, 9, 20)}}
    assert of(analyse(rows, usage_signals=sig)[0], ZOMBIE) == []
    loose = load_rules(overrides={"findings": {"zombie": {"min_days_after_last_use": 10}}})
    assert len(of(analyse(rows, usage_signals=sig, rules=loose)[0], ZOMBIE)) == 1


# ---------------------------------------------------------------- ANNUAL_RENEWAL_SOON
def yearly(merchant="Domainly", last=D(2025, 11, 10), n=3, amount=49.0):
    return series(merchant, last - timedelta(days=365 * (n - 1)), 365, [amount] * n)


def test_renewal_within_thirty_days_is_flagged():
    rows = yearly(last=D(2025, 11, 20)) + ANCHOR           # next ~ 20 Nov 2026, statement ends 31 Oct -> use today
    rep, *_ = analyse(rows, today=D(2026, 10, 31))
    (f,) = of(rep, ANNUAL_RENEWAL_SOON)
    assert f.severity == "medium" and f.potential_savings_annual == 0
    assert "renews around 20 Nov 2026 (20 days from 31 Oct 2026) for $49.00" in f.reason
    assert f.evidence["days_until"] == 20


def test_renewal_in_a_week_is_high():
    rep, *_ = analyse(yearly(last=D(2025, 11, 5)) + ANCHOR, today=D(2026, 10, 31))
    assert of(rep, ANNUAL_RENEWAL_SOON)[0].severity == "high"


def test_renewal_outside_window_not_flagged():
    rep, *_ = analyse(yearly(last=D(2025, 12, 20)) + ANCHOR, today=D(2026, 10, 31))
    assert of(rep, ANNUAL_RENEWAL_SOON) == []


def test_window_is_configurable():
    rules = load_rules(overrides={"findings": {"annual_renewal": {"window_days": 60}}})
    rep, *_ = analyse(yearly(last=D(2025, 12, 20)) + ANCHOR, today=D(2026, 10, 31), rules=rules)
    assert len(of(rep, ANNUAL_RENEWAL_SOON)) == 1


def test_monthly_subscriptions_never_get_renewal_finding():
    rep, *_ = analyse(series("Cloudly", D(2026, 1, 5), 30, [9.99] * 10) + ANCHOR)
    assert of(rep, ANNUAL_RENEWAL_SOON) == []


def test_renewal_already_past_is_not_flagged():
    rep, *_ = analyse(yearly(last=D(2025, 10, 10)) + ANCHOR, today=D(2026, 10, 31))
    assert of(rep, ANNUAL_RENEWAL_SOON) == []


def test_today_defaults_to_statement_end():
    rep, *_ = analyse(yearly(last=D(2025, 10, 20)) + ANCHOR)
    assert len(of(rep, ANNUAL_RENEWAL_SOON)) == 1


# ---------------------------------------------------------------- totals
def test_monthly_and_annual_spend_use_current_price_of_active_subs_only():
    rows = (series("Cloudly", D(2026, 1, 5), 30, [10.0] * 8 + [12.0] * 2)   # now 12/mo
            + series("Magly", D(2024, 11, 3), 365, [120.0] * 2)           # yearly 120
            + series("Gone", D(2025, 1, 5), 30, [50.0] * 4) + ANCHOR)      # ended, excluded
    rep, *_ = analyse(rows)
    s = rep.summary
    assert s["active_subscriptions"] == 2 and s["currency"] == "USD"
    assert s["annual_spend"] == pytest.approx(12 * 12 + 120) and s["monthly_spend"] == pytest.approx((144 + 120) / 12)


def test_savings_count_each_subscription_once():
    # Netflix: hike AND forgotten -> counted once at full annual cost, not cost + hike impact.
    rows = series("Netflix", D(2025, 11, 5), 30, [13.0] * 6 + [15.49] * 6) + ANCHOR
    rep, *_ = analyse(rows)
    assert len(of(rep, PRICE_HIKE)) == 1
    forgotten = of(rep, POSSIBLY_FORGOTTEN)
    if forgotten:
        assert rep.summary["potential_savings_annual"] == pytest.approx(forgotten[0].annual_cost)


def test_savings_split_by_currency():
    rows = (series("Netflix", D(2026, 1, 5), 30, [15.49] * 10) + series("Hotstar", D(2026, 1, 9), 30, [299.0] * 10, currency="INR")
            + ANCHOR)
    rep, *_ = analyse(rows)
    by = rep.summary["by_currency"]
    assert set(by) == {"USD", "INR"} and by["INR"]["potential_savings_annual"] == pytest.approx(299 * 12)


def test_renewal_reminders_do_not_inflate_savings():
    rep, *_ = analyse(yearly(last=D(2025, 11, 20)) + ANCHOR, today=D(2026, 10, 31))
    assert rep.summary["potential_savings_annual"] == 0


def test_empty_statement_gives_clean_summary():
    rep = generate_findings(detect_recurring(tx([])).subscriptions)
    assert rep.findings == [] and rep.summary["health"]["score"] == 100


# ---------------------------------------------------------------- health score
def test_clean_statement_scores_100():
    rep, *_ = analyse(series("Mystery Box Co", D(2026, 1, 5), 30, [12.0] * 10) + ANCHOR)
    assert rep.summary["health"]["score"] == 100


def test_health_formula_matches_documented_terms():
    # One forgotten Netflix (medium = 5 pts) that is 100% of spend: A = 50, B = 5 -> 45.
    rep, *_ = analyse(series("Netflix", D(2026, 1, 5), 30, [15.49] * 10) + ANCHOR)
    h = rep.summary["health"]
    assert h["breakdown"] == {"money_at_risk": 50.0, "open_findings": 5.0, "subscription_sprawl": 0.0}
    assert h["score"] == 45 and h["label"] == "Needs attention" and "100 - A - B - C" in h["formula"]


def test_health_money_term_scales_with_share_at_risk():
    rows = series("Netflix", D(2026, 1, 5), 30, [10.0] * 10) + series("Mystery Box Co", D(2026, 1, 9), 30, [10.0] * 10) + ANCHOR
    h = analyse(rows)[0].summary["health"]
    assert h["breakdown"]["money_at_risk"] == pytest.approx(25.0)


def test_health_severity_term_is_capped():
    cap = load_rules()["findings"]["health"]["finding_cap"]
    rows = []
    for i, name in enumerate(["Alpha Hub", "Bravo Cloud", "Delta Desk", "Echo Works", "Foxtrot Labs", "Golf Suite"]):
        base = series(name, D(2026, 1, 5 + i), 30, [5.0 + i] * 9)   # six duplicate pairs -> 60 raw points
        rows += base + base
    rep, *_ = analyse(rows + ANCHOR)
    assert rep.summary["health"]["breakdown"]["open_findings"] == cap


def test_health_sprawl_penalty():
    names = ["Alpha Tools", "Bravo Cloud", "Delta Desk", "Echo Works", "Foxtrot Labs", "Golf Suite",
             "Hotel Base", "India Mesh", "Juliet Pro", "Kilo Stack"]
    rows = []
    for i, n in enumerate(names):
        rows += series(n, D(2026, 1, 3 + i), 30, [5.0 + i] * 9)
    rep, *_ = analyse(rows + ANCHOR)
    assert rep.summary["active_subscriptions"] == 10
    assert rep.summary["health"]["breakdown"]["subscription_sprawl"] == 4.0 and rep.summary["health"]["score"] == 96


def test_health_clamped_at_zero_and_never_negative():
    rules = load_rules(overrides={"findings": {"health": {"savings_weight": 500}}})
    rep, *_ = analyse(series("Netflix", D(2026, 1, 5), 30, [15.49] * 10) + ANCHOR, rules=rules)
    assert rep.summary["health"]["score"] == 0


# ---------------------------------------------------------------- ordering, ids, dismissal
def test_findings_sorted_high_first_and_ids_stable():
    rows = (spotify_monthly_and_annual() + series("Netflix", D(2026, 1, 5), 30, [15.49] * 10))
    a = analyse(rows)[0]
    b = analyse(rows)[0]
    sev = [f.severity for f in a.findings]
    assert sev == sorted(sev, key=lambda s: {"high": 0, "medium": 1, "low": 2}[s])
    assert [f.id for f in a.findings] == [f.id for f in b.findings]


def test_dismissed_duplicate_leaves_savings_and_stays_listed():
    base = series("Streamly", D(2026, 1, 5), 30, [9.99] * 9)
    rows = base + base + ANCHOR
    rep = analyse(rows)[0]
    fid = of(rep, DUPLICATE)[0].id
    after = analyse(rows, dismissed=[fid])[0]
    assert of(after, DUPLICATE)[0].status == "dismissed" and after.findings[-1].id == fid   # dismissed sorts last
    assert rep.summary["potential_savings_annual"] == pytest.approx(9.99 * 12)
    assert after.summary["potential_savings_annual"] == 0 and after.summary["health"]["score"] == 100


def test_every_finding_reason_is_plain_text_with_numbers():
    rows = (spotify_monthly_and_annual() + series("Netflix", D(2025, 11, 5), 30, [13.0] * 6 + [15.49] * 6))
    rep = analyse(rows)[0]
    assert rep.findings
    for f in rep.findings:
        assert "$" in f.reason and len(f.reason) > 60 and f.evidence


# ---------------------------------------------------------------- end to end through the real importer
def test_end_to_end_from_csv_text():
    from subsweep.ingest import load_statement
    lines = ["Date,Description,Amount"]
    for m in range(1, 11):
        lines.append(f"2026-{m:02d}-05,NETFLIX.COM LOS GATOS CA,-{15.49 if m < 6 else 17.99}")
        lines.append(f"2026-{m:02d}-12,SPOTIFY P3A8E1 STOCKHOLM SE,-10.99")
        lines.append(f"2026-{m:02d}-13,PAYPAL *SPOTIFYAB 402-935-7733,-10.99")
        lines.append(f"2026-{m:02d}-20,SHELL OIL 57444 HOUSTON TX,-{38 + m}.00")
    st = load_statement("\n".join(lines))
    subs = detect_recurring(st.transactions).subscriptions
    rep = generate_findings(subs, st.transactions)
    kinds = {f.type for f in rep.findings}
    assert {DUPLICATE, PRICE_HIKE} <= kinds
    assert "Shell" not in " ".join(f.merchant for f in rep.findings)
    assert rep.summary["potential_savings_annual"] > 0


def test_cancel_saving_is_not_overwritten_by_a_later_smaller_hike_saving():
    # ZOMBIE (high) is processed before PRICE_HIKE (medium); the sub must still count its full annual cost once.
    rows = series("Adobe", D(2025, 10, 5), 30, [50.0] * 6 + [59.99] * 6) + ANCHOR   # not "forgotten": not discretionary
    rep, *_ = analyse(rows, usage_signals={"adobe": {"last_used": D(2026, 1, 1)}})
    assert [f.type for f in rep.findings] == [ZOMBIE, PRICE_HIKE]
    assert rep.summary["potential_savings_annual"] == pytest.approx(59.99 * 12)


def test_dismissed_findings_sort_after_open_ones():
    base = series("Streamly", D(2026, 1, 5), 30, [9.99] * 9)
    rows = base + base + series("Hikely", D(2026, 1, 9), 30, [10.0] * 5 + [12.0] * 5) + ANCHOR
    rep = analyse(rows)[0]
    dup = of(rep, DUPLICATE)[0]
    assert rep.findings[0].id == dup.id            # high severity first
    after = analyse(rows, dismissed=[dup.id])[0]
    assert [f.status for f in after.findings] == ["open", "dismissed"] and after.findings[0].type == PRICE_HIKE


def test_duplicate_title_uses_the_brand_not_the_processor_spelling():
    rows = (series("Spotify", D(2026, 1, 12), 30, [10.99] * 10) + series("Spotifyab", D(2026, 1, 13), 30, [10.99] * 10, via_paypal=True) + ANCHOR)
    (f,) = of(analyse(rows)[0], DUPLICATE)
    assert f.title == "Spotify: 2 active subscriptions" and f.merchant == "Spotify"


def test_kept_duplicate_stream_is_not_also_counted_as_forgotten_savings():
    rows = (series("Spotify", D(2026, 1, 12), 30, [10.99] * 10) + series("Spotifyab", D(2026, 1, 13), 30, [10.99] * 10, via_paypal=True) + ANCHOR)
    rep, *_ = analyse(rows)
    assert len(of(rep, POSSIBLY_FORGOTTEN)) == 2                         # both look unused...
    assert rep.summary["potential_savings_annual"] == pytest.approx(10.99 * 12)   # ...but we assume you keep one


def test_zombie_on_the_kept_stream_still_counts_in_full():
    rows = (series("Spotify", D(2026, 1, 12), 30, [10.99] * 10) + series("Spotifyab", D(2026, 1, 13), 30, [10.99] * 10, via_paypal=True) + ANCHOR)
    rep, *_ = analyse(rows, usage_signals={"spotify": {"last_used": D(2026, 1, 1)}})
    assert rep.summary["potential_savings_annual"] == pytest.approx(10.99 * 12 * 2)
