"""Phase 2 tests. Each rule has a case that fails if that rule is removed or switched off."""
from datetime import date, timedelta

import pandas as pd
import pytest

from subsweep.detect import detect_recurring, load_rules
from subsweep.detect.grouping import group_merchants, normalize_name
from subsweep.detect.pricing import analyse_prices
from subsweep.detect.cycles import fit_cycle, gap_fit
from tests.helpers import monthly, series, tx

D = date
R = load_rules()


def run(rows, **kw):
    return detect_recurring(tx(rows), rules=kw.pop("rules", None) or load_rules(), **kw)


def one(res):
    assert len(res.subscriptions) == 1, res.subscriptions[["merchant", "cycle"]].to_dict("records")
    return res.subscriptions.iloc[0]


# ------------------------------------------------------------------ rule 1: grouping
def test_name_variants_group_together():
    g = group_merchants(["Netflix", "Netflix India", "Netflix Com", "Hulu"], R)
    assert g["Netflix"] == g["Netflix India"] == g["Netflix Com"] != g["Hulu"]


def test_alias_merges_processor_spelling():
    g = group_merchants(["Spotify", "Spotifyab"], R)
    assert g["Spotify"] == g["Spotifyab"]


def test_unrelated_merchants_not_merged():
    g = group_merchants(["Netflix", "Notion", "Adobe", "Apple Music"], R)
    assert len(set(g.values())) == 4


def test_normalize_drops_geo_and_legal_noise():
    assert normalize_name("Netflix India Pvt Ltd", R) == "netflix"


def test_threshold_is_configurable():
    strict = load_rules(overrides={"grouping": {"similarity_threshold": 100, "aliases": {"spotifyab": "spotifyab"}}})
    g = group_merchants(["Spotify", "Spotifyab"], strict)
    assert g["Spotify"] != g["Spotifyab"]


def test_detection_depends_on_grouping_variants_form_one_subscription():
    rows = []
    for i, (name, d) in enumerate(zip(["Netflix", "Netflix India", "Netflix Com", "Netflix", "Netflix India", "Netflix Com"],
                                      [D(2026, m, 5) for m in range(5, 11)])):
        rows.append((d, name, -15.49))
    s = one(run(rows))
    assert s.n_charges == 6 and s.cycle == "monthly"
    # With grouping effectively off, name variants fragment into groups too small to detect.
    off = load_rules(overrides={"grouping": {"similarity_threshold": 101, "ignore_tokens": []}})
    assert len(run(rows, rules=off).subscriptions) == 0


# ------------------------------------------------------------------ rule 2: cycles
@pytest.mark.parametrize("cycle,gap,count", [("weekly", 7, 6), ("monthly", 30, 5), ("quarterly", 91, 4), ("yearly", 365, 3)])
def test_each_cycle_detected(cycle, gap, count):
    s = one(run(series("Cloudly", D(2024, 1, 3), gap, [9.99] * count)))
    assert s.cycle == cycle


@pytest.mark.parametrize("gap,expected", [(6, 1), (8, 1), (28, 1), (33, 1), (85, 1), (95, 1), (355, 1), (375, 1)])
def test_window_edges_inclusive(gap, expected):
    cycle = {6: "weekly", 8: "weekly", 28: "monthly", 33: "monthly", 85: "quarterly", 95: "quarterly",
             355: "yearly", 375: "yearly"}[gap]
    assert gap_fit(gap, R["cycles"][cycle], R)[:2] == (expected, "on_cycle")


def test_gap_between_windows_does_not_fit():
    assert all(gap_fit(45, R["cycles"][c], R)[0] == 0 for c in ("weekly", "monthly", "quarterly", "yearly"))


def test_date_drift_within_window_is_regular():
    s = one(run(series("Cloudly", D(2026, 1, 31), [28, 31, 30, 33, 29], [9.99] * 6)))
    assert s.cycle == "monthly" and s.regularity == 1.0


def test_one_missed_cycle_still_monthly():
    s = one(run(series("Cloudly", D(2026, 1, 5), [30, 31, 61, 30], [9.99] * 5)))
    assert s.cycle == "monthly" and s.missed_cycles == 1
    assert s.regularity < 1.0


def test_two_missed_cycles_still_monthly():
    s = one(run(series("Cloudly", D(2026, 1, 5), [30, 30, 61, 30, 61, 30], [9.99] * 7)))
    assert s.cycle == "monthly" and s.missed_cycles == 2


def test_missed_cycle_rule_is_what_allows_it():
    rows = series("Cloudly", D(2026, 1, 5), [30, 31, 61, 30], [9.99] * 5)
    strict = load_rules(overrides={"cycles": {"max_missed_cycles": 0, "min_fit_share": 0.9}})
    res = run(rows, rules=strict)
    assert len(res.subscriptions) == 0 and res.excluded.iloc[0].rule == "no_cycle"


def test_shifted_charge_just_outside_window_gets_partial_credit():
    fit = fit_cycle([0, 30, 55 + 0, 85, 115], R)  # 25-day gap is 3 days short of the 28-day window
    assert fit.cycle == "monthly" and "shifted" in fit.kinds and fit.regularity < 1.0


def test_irregular_gaps_are_not_recurring():
    res = run(series("Random Shop", D(2026, 1, 5), [10, 45, 17, 70], [9.99] * 5))
    assert len(res.subscriptions) == 0
    assert res.excluded.iloc[0].rule == "no_cycle"


def test_quarterly_not_mistaken_for_monthly_with_missed_cycles():
    assert one(run(series("Magazine", D(2025, 1, 5), 90, [20] * 4))).cycle == "quarterly"


def test_min_occurrences_per_cycle():
    res = run(series("Cloudly", D(2026, 1, 5), 30, [9.99] * 2))
    assert len(res.subscriptions) == 0 and res.excluded.iloc[0].rule == "too_few_charges"


def test_two_yearly_charges_are_enough_but_low_confidence():
    s = one(run(series("Domainly", D(2025, 3, 1), 365, [49.0] * 2)))
    assert s.cycle == "yearly" and s.confidence <= 0.65


# ------------------------------------------------------------------ rule 3: amounts
def test_stable_amounts_within_five_percent():
    s = one(run(series("Cloudly", D(2026, 1, 5), 30, [10.00, 10.20, 9.80, 10.40, 9.70])))
    assert s.amount_stability == 1.0 and s.price_changes == []


def test_price_hike_persisting_two_cycles_is_flagged():
    s = one(run(series("Streamly", D(2026, 1, 5), 30, [10.99] * 4 + [12.99] * 3)))
    assert s.has_price_hike
    ch = s.price_changes[0]
    assert (ch["old_amount"], ch["new_amount"], ch["kind"]) == (10.99, 12.99, "increase")
    assert ch["pct"] == pytest.approx(18.2, abs=0.1) and ch["annual_impact"] == pytest.approx(24.0)
    assert ch["date"] == D(2026, 1, 5) + timedelta(days=120)


def test_one_charge_at_new_price_is_pending_not_a_hike():
    s = one(run(series("Streamly", D(2026, 1, 5), 30, [10.99] * 5 + [12.99])))
    assert not s.has_price_hike and s.price_changes == []
    assert s.pending_price_change["new_amount"] == 12.99


def test_one_off_spike_is_not_a_hike():
    s = one(run(series("Streamly", D(2026, 1, 5), 30, [10.0, 10.0, 10.0, 16.0, 10.0, 10.0])))
    assert not s.has_price_hike and s.price_changes == []
    assert s.amount_stability < 1.0   # the spike is counted as unstable


def test_hike_threshold_is_five_percent_inclusive():
    exact = analyse_prices([10.0] * 3 + [10.5] * 3, [None] * 6, R)
    below = analyse_prices([10.0] * 3 + [10.49] * 3, [None] * 6, R)
    assert [c["kind"] for c in exact["changes"]] == ["increase"]
    assert below["changes"] == []


def test_min_cycles_for_price_change_is_configurable():
    rules = load_rules(overrides={"amounts": {"price_change_min_cycles": 3}})
    a = analyse_prices([10.0] * 3 + [12.0] * 2, [None] * 5, rules)
    assert a["changes"] == [] and a["pending"] is None or a["changes"] == []
    b = analyse_prices([10.0] * 3 + [12.0] * 2, [None] * 5, R)
    assert len(b["changes"]) == 1


def test_sustained_decrease_recorded_but_not_a_hike():
    s = one(run(series("Streamly", D(2026, 1, 5), 30, [15.0] * 3 + [10.0] * 3)))
    assert s.price_changes[0]["kind"] == "decrease" and not s.has_price_hike


def test_price_hike_does_not_destroy_stability():
    s = one(run(series("Streamly", D(2026, 1, 5), 30, [10.0] * 4 + [13.0] * 4)))
    assert s.amount_stability == 1.0


def test_two_hikes_both_reported():
    s = one(run(series("Streamly", D(2026, 1, 5), 30, [10.0] * 3 + [12.0] * 3 + [15.0] * 3)))
    assert [c["new_amount"] for c in s.price_changes] == [12.0, 15.0]


# ------------------------------------------------------------------ rule 4: confidence
def test_more_history_raises_confidence():
    short = one(run(series("Cloudly", D(2026, 1, 5), 30, [9.99] * 3)))
    long_ = one(run(series("Cloudly", D(2025, 1, 5), 30, [9.99] * 12)))
    assert long_.confidence > short.confidence


def test_irregular_timing_lowers_confidence():
    clean = one(run(series("Cloudly", D(2026, 1, 5), 30, [9.99] * 6)))
    messy = one(run(series("Cloudly", D(2026, 1, 5), [30, 61, 25, 61, 30], [9.99] * 6)))
    assert messy.confidence < clean.confidence


def test_unstable_amounts_lower_confidence_for_known_subscription_merchants():
    steady = one(run(series("Netflix", D(2026, 1, 5), 30, [15.49] * 6)))
    wobbly = one(run(series("Netflix", D(2026, 1, 5), 30, [15.49, 22.0, 15.49, 31.0, 15.49, 19.0])))
    assert wobbly.confidence < steady.confidence


def test_occurrence_cap_applies_with_few_charges():
    perfect_but_few = one(run(series("Domainly", D(2025, 3, 1), 365, [49.0] * 2)))
    uncapped = load_rules(overrides={"confidence": {"max_by_occurrences": {2: 1.0, 3: 1.0}}})
    s2 = one(run(series("Domainly", D(2025, 3, 1), 365, [49.0] * 2), rules=uncapped))
    assert perfect_but_few.confidence == 0.65 and s2.confidence > 0.65


def test_reason_text_explains_the_score():
    s = one(run(series("Cloudly", D(2026, 1, 5), 30, [9.99] * 6)))
    assert "6 monthly charges" in s.confidence_reason and "regularity 100%" in s.confidence_reason
    assert 0 <= s.confidence <= 1


def test_low_confidence_is_filtered_by_threshold():
    rows = series("Cloudly", D(2026, 1, 5), [30, 61, 25], [9.99] * 4)
    hi = load_rules(overrides={"confidence": {"min_confidence_to_report": 0.99}})
    res = run(rows, rules=hi)
    assert len(res.subscriptions) == 0 and res.excluded.iloc[0].rule == "low_confidence"


# ------------------------------------------------------------------ rule 5: exclusions
def test_groceries_excluded_by_category():
    res = run(series("Kroger", D(2026, 1, 3), 7, [54.2, 61.0, 47.9, 72.5, 58.0, 66.3]))
    assert len(res.subscriptions) == 0 and res.excluded.iloc[0].rule == "category:groceries"


def test_fuel_excluded_even_with_regular_gaps_and_equal_amounts():
    res = run(series("Shell Oil", D(2026, 1, 3), 30, [40.0] * 6))
    assert len(res.subscriptions) == 0 and res.excluded.iloc[0].rule == "category:fuel"


def test_category_exclusion_is_what_removes_fuel():
    rows = series("Shell Oil", D(2026, 1, 3), 30, [40.0] * 6)
    off = load_rules(overrides={"categories": {"fuel": {"excluded": False}}})
    assert len(run(rows, rules=off).subscriptions) == 1


def test_whole_word_matching_avoids_false_exclusion():
    # "Shellfish Club" must not match the fuel keyword "shell".
    assert len(run(series("Shellfish Club", D(2026, 1, 3), 30, [25.0] * 5)).subscriptions) == 1


def test_daily_coffee_style_weekly_spend_excluded():
    res = run(series("Local Coffee", D(2026, 1, 3), 7, [4.5] * 8))
    assert len(res.subscriptions) == 0 and res.excluded.iloc[0].rule == "category:dining_and_delivery"


def test_never_exclude_protects_memberships():
    rows = series("Amazon Prime", D(2026, 1, 3), 30, [14.99] * 6)
    assert one(run(rows)).merchant == "Amazon Prime"
    plain = series("Amazon", D(2026, 1, 3), 30, [14.99] * 6)
    assert len(run(plain).subscriptions) == 0
    assert run(plain).excluded.iloc[0].rule == "category:retail_marketplace"


def test_weekly_with_changing_amounts_is_habit_spending():
    res = run(series("Corner Stall", D(2026, 1, 3), 7, [4.0, 9.5, 6.2, 12.0, 5.1, 8.8]))
    assert len(res.subscriptions) == 0 and res.excluded.iloc[0].rule == "weekly_unstable_amounts"


def test_weekly_rule_is_what_removes_it():
    rows = series("Corner Stall", D(2026, 1, 3), 7, [4.0, 9.5, 6.2, 12.0, 5.1, 8.8])
    off = load_rules(overrides={"exclusions": {"weekly_requires_stable_share": 0.0,
                                               "variable_amount": {"min_stable_share": 0.0}}})
    assert len(run(rows, rules=off).subscriptions) == 1


def test_variable_amounts_excluded_for_unknown_merchants():
    res = run(series("Gadget Hub", D(2026, 1, 3), 30, [12.0, 80.0, 33.0, 145.0, 20.0, 64.0]))
    assert len(res.subscriptions) == 0 and res.excluded.iloc[0].rule == "variable_amounts"


def test_variable_amounts_kept_for_subscription_like_merchants():
    s = one(run(series("Spotify", D(2026, 1, 3), 30, [12.0, 80.0, 33.0, 145.0, 20.0, 64.0])))
    assert s.category == "music_and_audio" and s.confidence < 0.8


def test_one_off_burst_excluded():
    res = run(series("Gadget Hub", D(2026, 1, 3), [2, 3, 2], [30.0, 75.0, 12.0, 140.0]))
    assert len(res.subscriptions) == 0 and res.excluded.iloc[0].rule == "one_off_burst"


def test_burst_does_not_hide_a_real_subscription():
    rows = series("Cloudly", D(2026, 1, 5), 30, [9.99] * 5)
    rows += [(D(2026, 3, 20 + i), "Cloudly", -a) for i, a in enumerate([41.0, 17.0, 88.0, 23.0])]
    s = one(run(rows))
    assert s.cycle == "monthly" and s.burst_charges_ignored == 4 and s.n_charges == 5
    off = load_rules(overrides={"exclusions": {"burst_min_charges": 99}})
    assert len(run(rows, rules=off).subscriptions) == 0     # without the burst rule the noise hides it


def test_exclusions_are_loaded_from_yaml_file(tmp_path):
    p = tmp_path / "rules.yaml"
    import yaml
    from subsweep.detect.rules import DEFAULT_RULES_PATH
    cfg = yaml.safe_load(open(DEFAULT_RULES_PATH))
    cfg["categories"]["streaming"]["excluded"] = True
    p.write_text(yaml.safe_dump(cfg))
    rules = load_rules(p)
    assert len(run(series("Netflix", D(2026, 1, 3), 30, [15.49] * 5), rules=rules).subscriptions) == 0
    assert len(run(series("Netflix", D(2026, 1, 3), 30, [15.49] * 5)).subscriptions) == 1


def test_missing_rules_section_is_a_clear_error(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("grouping: {}\n")
    with pytest.raises(ValueError, match="missing"):
        load_rules(p)


def test_every_exclusion_is_explained():
    rows = series("Kroger", D(2026, 1, 3), 7, [50.0] * 5) + series("Random", D(2026, 1, 5), [10, 45, 17], [5.0] * 4)
    ex = run(rows).excluded
    assert set(ex.rule) == {"category:groceries", "no_cycle"} and ex.reason.str.len().min() > 20


# ------------------------------------------------------------------ streams
def test_monthly_and_annual_plans_for_same_service_are_two_subscriptions():
    rows = series("Spotify", D(2025, 1, 5), 30, [10.99] * 6) + series("Spotify", D(2024, 6, 9), 365, [109.99] * 3)
    subs = run(rows).subscriptions.sort_values("cycle")
    assert subs.cycle.tolist() == ["monthly", "yearly"]


def test_paypal_and_direct_billing_are_separate_streams():
    rows = series("Spotify", D(2026, 1, 5), 30, [10.99] * 5) + series("Spotify", D(2026, 1, 15), 30, [10.99] * 5, via_paypal=True)
    subs = run(rows).subscriptions
    assert len(subs) == 2 and set(subs.via_paypal) == {True, False}


def test_currencies_do_not_mix():
    rows = series("Cloudly", D(2026, 1, 5), 30, [9.99] * 4) + series("Cloudly", D(2026, 1, 20), 30, [799.0] * 4, currency="INR")
    assert set(run(rows).subscriptions.currency) == {"USD", "INR"}


def test_double_billing_each_month_gives_two_streams():
    base = series("Streamly", D(2026, 1, 5), 30, [10.0] * 5)
    rows = base + base
    assert len(run(rows).subscriptions) == 2


def test_occasional_same_day_double_is_noted_not_split():
    rows = series("Streamly", D(2026, 1, 5), 30, [10.0] * 5) + [(D(2026, 2, 4), "Streamly", -10.0)]
    s = one(run(rows))
    assert s.same_day_extra_charges == 1 and s.n_charges == 5


def test_two_accounts_interleaved_are_split():
    rows = series("Streamly", D(2026, 1, 5), 30, [10.0] * 5) + series("Streamly", D(2026, 1, 19), 30, [10.0] * 5)
    assert len(run(rows).subscriptions) == 2


def test_interleaved_split_is_what_finds_them():
    rows = series("Streamly", D(2026, 1, 5), 30, [10.0] * 5) + series("Streamly", D(2026, 1, 19), 30, [10.0] * 5)
    off = load_rules(overrides={"cycles": {"try_split_interleaved": False}})
    assert len(run(rows, rules=off).subscriptions) == 0


# ------------------------------------------------------------------ output
def test_credits_are_ignored():
    rows = series("Cloudly", D(2026, 1, 5), 30, [9.99] * 4) + [(D(2026, 2, 6), "Cloudly", 9.99)]
    assert one(run(rows)).n_charges == 4


def test_output_table_fields():
    s = one(run(series("Cloudly", D(2026, 1, 5), 30, [9.99, 10.01, 9.99, 10.0])))
    assert s.merchant == "Cloudly" and s.cycle == "monthly" and s.avg_amount == pytest.approx(10.0)
    assert (s.first_date, s.last_date) == (D(2026, 1, 5), D(2026, 4, 5))
    assert s.next_expected_date == D(2026, 4, 5) + timedelta(days=30)
    assert len(s.txn_ids) == 4 and s.id.startswith("sub_")


def test_active_flag_uses_statement_end():
    rows = series("Cloudly", D(2025, 1, 5), 30, [9.99] * 4) + [(D(2026, 1, 5), "Other", -1.0)]
    old = one(run(rows))
    assert not old.active
    assert one(run(series("Cloudly", D(2026, 1, 5), 30, [9.99] * 4))).active


def test_empty_and_credit_only_inputs():
    assert run([(D(2026, 1, 1), "Pay", 100.0)]).subscriptions.empty
    assert detect_recurring(tx([])).subscriptions.empty


def test_detection_is_deterministic():
    rows = series("Cloudly", D(2026, 1, 5), 30, [9.99] * 5) + series("Streamly", D(2026, 1, 9), 30, [5.0] * 5)
    a, b = run(rows).subscriptions, run(rows).subscriptions
    assert a.id.tolist() == b.id.tolist()


def test_weekly_never_accepts_missed_weeks():
    # 14-day gaps are not "weekly with a skipped week".
    assert gap_fit(14, R["cycles"]["weekly"], R)[0] == 0
    allow = load_rules(overrides={"cycles": {"weekly": {"allow_missed": True}}})
    assert gap_fit(14, allow["cycles"]["weekly"], allow)[0] == 2


def test_big_price_jump_is_one_subscription_not_two_plans():
    s = one(run(series("Streamly", D(2026, 1, 5), 30, [9.0] * 4 + [24.0] * 4)))
    assert s.n_charges == 8 and s.price_changes[0]["new_amount"] == 24.0


def test_irregular_amounts_with_regular_timing_stay_one_subscription():
    s = one(run(series("Spotify", D(2026, 1, 5), 30, [10.0, 30.0, 12.0, 45.0, 11.0])))
    assert s.n_charges == 5


def test_typo_variants_match_by_similarity_alone():
    g = group_merchants(["Netflix", "Netflx"], R)
    assert g["Netflix"] == g["Netflx"]
    rows = [(D(2026, m, 5), "Netflix" if m % 2 else "Netflx", -15.49) for m in range(1, 7)]
    assert one(run(rows)).n_charges == 6


def test_alias_matches_where_similarity_alone_would_not():
    # token_set_ratio("ytb", "youtube") is only 60, far below the 85 threshold; the alias is what joins them.
    g = group_merchants(["Ytb", "Youtube"], R)
    assert g["Ytb"] == g["Youtube"]
    no_alias = load_rules(overrides={"grouping": {"aliases": {"ytb": "ytb"}}})
    g2 = group_merchants(["Ytb", "Youtube"], no_alias)
    assert g2["Ytb"] != g2["Youtube"]
