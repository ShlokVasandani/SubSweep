"""Facts sent to the model, the template fallbacks, and the pandas analyses behind the Q&A."""
import json
import re
from datetime import date

import pytest

from subsweep.ai import analyses as an
from subsweep.ai.facts import (
    ACCOUNT_PLACEHOLDER, DRAFTABLE, NAME_PLACEHOLDER, ORDER_PLACEHOLDER, finding_facts, template_draft, template_explanation,
)
from subsweep.ai.grounding import check
from subsweep.detect import detect_recurring
from subsweep.findings import (
    ANNUAL_RENEWAL_SOON, DUPLICATE, PAYPAL_MISMATCH, POSSIBLY_FORGOTTEN, PRICE_HIKE, ZOMBIE, generate_findings,
)
from tests.ai_helpers import D, TODAY, find, make_ctx, scenario_rows
from tests.helpers import series, tx

CTX = make_ctx()
ALL_TYPES = [DUPLICATE, PRICE_HIKE, POSSIBLY_FORGOTTEN, ANNUAL_RENEWAL_SOON]


def ctx_with_zombie_and_mismatch():
    t = tx(series("Gymly", D(2026, 1, 5), 30, [29.0] * 9) + [(D(2026, 9, 30), "Anchor", -1.0)])
    subs = detect_recurring(t).subscriptions
    rep = generate_findings(subs, t, usage_signals={"gymly": {"last_used": D(2026, 2, 1)}})
    return rep


# ---------------------------------------------------------------- facts
@pytest.mark.parametrize("ftype", ALL_TYPES)
def test_facts_contain_only_whitelisted_non_identifying_data(ftype):
    f = find(CTX, ftype)
    blob = json.dumps(finding_facts(f))
    assert f.id not in blob and all(sid not in blob for sid in f.subscription_ids)
    assert "txn_" not in blob and "sub_" not in blob and "@" not in blob and "fnd_" not in blob


def test_dates_in_facts_are_preformatted_strings():
    facts = finding_facts(find(CTX, PRICE_HIKE))
    assert re.fullmatch(r"\d{1,2} [A-Z][a-z]{2} \d{4}", facts["new_price_since"])
    assert facts["old_price"] == 13.0 and facts["new_price"] == 15.49 and facts["finding_type"] == PRICE_HIKE


def test_duplicate_facts_name_the_channel_but_not_the_ids():
    facts = finding_facts(find(CTX, DUPLICATE))
    assert sorted(s["paid_via"] for s in facts["streams"]) == ["PayPal", "direct"]


def test_forgotten_facts_carry_the_visible_reasons_and_the_guess_flag():
    facts = finding_facts(find(CTX, POSSIBLY_FORGOTTEN))
    assert facts["is_a_guess"] is True and facts["why_flagged"] and all(isinstance(t, str) for t in facts["why_flagged"])


def test_zombie_and_renewal_facts():
    z = find(type("C", (), {"report": ctx_with_zombie_and_mismatch()}), ZOMBIE)
    assert finding_facts(z)["last_used"] == "1 Feb 2026"
    r = finding_facts(find(CTX, ANNUAL_RENEWAL_SOON))
    assert r["renewal_date"] == "19 Oct 2026" and r["days_until_renewal"] == 19 and r["price"] == 49.0


# ---------------------------------------------------------------- templates
@pytest.mark.parametrize("ftype", ALL_TYPES)
def test_template_explanations_are_short_calm_and_grounded_in_their_own_facts(ftype):
    f = find(CTX, ftype)
    text = template_explanation(f)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    assert 2 <= len(sentences) <= 3, text
    assert check(text, finding_facts(f)).ok, check(text, finding_facts(f)).problems
    assert not re.search(r"!|urgent|immediately|warning", text, re.I)


def test_zombie_template_is_grounded():
    z = find(type("C", (), {"report": ctx_with_zombie_and_mismatch()}), ZOMBIE)
    assert check(template_explanation(z), finding_facts(z)).ok


def test_forgotten_template_says_it_is_a_guess():
    assert "guess" in template_explanation(find(CTX, POSSIBLY_FORGOTTEN)).lower()


@pytest.mark.parametrize("ftype", [DUPLICATE, PRICE_HIKE, POSSIBLY_FORGOTTEN])
def test_template_drafts_have_placeholders_subject_and_real_figures(ftype):
    f = find(CTX, ftype)
    subject, body = template_draft(f)
    assert subject and ACCOUNT_PLACEHOLDER in body and ORDER_PLACEHOLDER in body and NAME_PLACEHOLDER in body
    assert len(body.split()) < 160 and f.merchant in body
    assert check(body, finding_facts(f)).ok, check(body, finding_facts(f)).problems
    assert not re.search(r"\bsent\b", body, re.I)


def test_draftable_types_match_the_spec():
    assert DRAFTABLE == {DUPLICATE, PRICE_HIKE, POSSIBLY_FORGOTTEN}


def test_duplicate_draft_asks_to_cancel_the_cheaper_stream():
    f = find(CTX, DUPLICATE)
    cheaper = min(f.evidence["streams"], key=lambda s: s["annual_cost"])
    assert f"${cheaper['amount']:.2f}" in template_draft(f)[1]


# ---------------------------------------------------------------- analyses
def test_total_spend_uses_current_prices_of_active_subscriptions():
    r = an.total_spend(CTX)
    assert not r.empty
    row = r.table.iloc[0]
    expected_year = 15.49 * 12 + 10.99 * 12 + 9.99 * 12 * 2 + 49.0
    assert row["yearly_spend"] == pytest.approx(round(expected_year, 2)) and row["active_subscriptions"] == 5
    assert "a month" in r.text and "$" in r.text


def test_spend_by_category_filters_and_labels():
    r = an.spend_by_category(CTX, category="streaming")
    assert list(r.table["category"]) == ["streaming"] and r.table.iloc[0]["monthly_spend"] == 15.49
    assert "Netflix" in r.text
    assert an.spend_by_category(CTX, category="gaming").empty
    allc = an.spend_by_category(CTX)
    assert set(allc.table["category"]) >= {"streaming", "music and audio", "other"}


def test_largest_price_increase_this_year_filters_by_year():
    r = an.largest_price_increase(CTX, period="this_year")
    assert r.table.iloc[0]["subscription"] == "Netflix" and r.table.iloc[0]["old_price"] == 13.0
    assert "Netflix went up the most in 2026" in r.text and "19.2%" in r.text
    later = make_ctx(today=date(2027, 3, 1))
    assert an.largest_price_increase(later, period="this_year").empty and not an.largest_price_increase(later, period="all").empty


def test_most_expensive_orders_by_yearly_cost_and_limits():
    r = an.most_expensive(CTX, n=2)
    assert list(r.table["subscription"]) == ["Netflix", "Spotify"]
    assert list(r.table["yearly_cost"]) == [185.88, 131.88]


def test_list_subscriptions_and_cycle_filter():
    assert len(an.list_subscriptions(CTX).table) == 5
    y = an.list_subscriptions(CTX, cycle="yearly")
    assert list(y.table["subscription"]) == ["Domainly"] and an.list_subscriptions(CTX, cycle="weekly").empty


def test_savings_summary_lists_open_findings_with_savings():
    r = an.savings_summary(CTX)
    assert set(r.table["finding"]) >= {"DUPLICATE", "PRICE_HIKE"} and "a year" in r.text
    assert an.savings_summary(make_ctx(rows=series("Mystery Co", D(2026, 1, 5), 30, [5.0] * 9) + [(D(2026, 9, 30), "Anchor", -1.0)])).empty


def test_findings_overview_by_type():
    r = an.findings_overview(CTX, type="DUPLICATE")
    assert list(r.table["finding"]) == ["DUPLICATE"] and "Streamly" in r.text and an.findings_overview(CTX, type="ZOMBIE").empty


def test_upcoming_renewals_window():
    r = an.upcoming_renewals(CTX, days=30)
    assert "Domainly" in list(r.table["subscription"]) and r.table[r.table["subscription"] == "Domainly"].iloc[0]["expected_on"] == D(2026, 10, 19)
    assert "19 Oct 2026" in r.text
    assert "Domainly" not in list(an.upcoming_renewals(CTX, days=10).table.get("subscription", []))


def test_merchant_total_sums_debits_only():
    r = an.merchant_total(CTX, merchant="Spotify")
    row = r.table.iloc[0]
    assert row["charges"] == 9 and row["total_paid"] == pytest.approx(10.99 * 9) and "Spotify" in r.text
    assert an.merchant_total(CTX, merchant="Nonexistent Corp").empty and an.merchant_total(CTX, merchant="").empty


def test_empty_context_gives_empty_results_not_errors():
    ctx = make_ctx(rows=[(D(2026, 9, 30), "Coffee", -3.0)])
    for name in an.INTENTS:
        assert an.run(name, ctx, {"merchant": "Nobody Inc"} if name == "merchant_total" else {}).empty, name


def test_table_records_format_dates_and_round_floats():
    rows = an.table_records(an.largest_price_increase(CTX).table)
    assert re.fullmatch(r"\d{1,2} [A-Z][a-z]{2} \d{4}", rows[0]["since"]) and isinstance(rows[0]["percent_increase"], float)
    json.dumps(rows)


def test_table_records_limit():
    assert len(an.table_records(an.list_subscriptions(CTX).table, limit=2)) == 2


@pytest.mark.parametrize("intent,params,ok", [
    ("spend_by_category", {"category": "streaming"}, True), ("spend_by_category", {"category": "rocket fuel"}, False),
    ("most_expensive", {"n": 5}, True), ("most_expensive", {"n": 500}, False), ("most_expensive", {"n": "5"}, False),
    ("upcoming_renewals", {"days": 30}, True), ("upcoming_renewals", {"days": 0}, False),
    ("findings_overview", {"type": "DUPLICATE"}, True), ("findings_overview", {"type": "DROP TABLE"}, False),
    ("largest_price_increase", {"period": "all"}, True), ("largest_price_increase", {"period": "last decade"}, False),
    ("merchant_total", {"merchant": "Netflix"}, True), ("merchant_total", {"merchant": "x"}, False),
    ("total_spend", {}, True), ("total_spend", {"evil": 1}, False), ("nonsense", {}, False), ("list_subscriptions", {"cycle": None}, True),
])
def test_router_params_are_validated_against_the_whitelist(intent, params, ok):
    assert (an.clean_params(intent, params) is not None) is ok


@pytest.mark.parametrize("q,expected", [
    ("What do I spend on streaming?", ("spend_by_category", {"category": "streaming"})),
    ("how much on music", ("spend_by_category", {"category": "music_and_audio"})),
    ("Which subscription went up the most this year?", ("largest_price_increase", {"period": "this_year"})),
    ("did any price increase", ("largest_price_increase", {"period": "all"})),
    ("what renews in the next 2 weeks", ("upcoming_renewals", {"days": 14})),
    ("anything renewing soon", ("upcoming_renewals", {"days": 30})),
    ("do I have duplicates", ("findings_overview", {"type": "DUPLICATE"})),
    ("am I paying for anything twice", ("findings_overview", {"type": "DUPLICATE"})),
    ("which are forgotten", ("findings_overview", {"type": "POSSIBLY_FORGOTTEN"})),
    ("still charged after I cancelled", ("findings_overview", {"type": "PAYPAL_MISMATCH"})),
    ("how can I save money", ("savings_summary", {})),
    ("how much have I paid Netflix", ("merchant_total", {"merchant": "Netflix"})),
    ("what is my most expensive subscription", ("most_expensive", {"n": 3})),
    ("show top 5 most expensive", ("most_expensive", {"n": 5})),
    ("list my subscriptions", ("list_subscriptions", {})),
    ("what's my total per month", ("total_spend", {})),
])
def test_rule_router(q, expected):
    assert an.route_rules(q, CTX) == expected


@pytest.mark.parametrize("q", ["what is the capital of France", "write a poem", "tell me a joke", "how tall is Everest"])
def test_rule_router_returns_none_for_unrelated_questions(q):
    assert an.route_rules(q, CTX) is None


def richer_ctx():
    rows = (series("Netflix", D(2025, 10, 5), 30, [13.0] * 6 + [15.49] * 6)                       # +19.2%
            + series("Hikely", D(2025, 10, 9), 30, [10.0] * 6 + [15.0] * 6)                       # +50%
            + series("Cheapo", D(2025, 10, 12), 30, [20.0] * 6 + [12.0] * 6)                      # a decrease
            + series("Oldgym", D(2024, 1, 5), 30, [30.0] * 6)                                      # ended long ago
            + series("Lagging", D(2026, 2, 22), 30, [8.0] * 7)                                     # last charge 40 days before today (21 Aug)
            + series("Spotify", D(2026, 1, 5), 30, [10.99] * 9)
            + [(D(2026, 8, 20), "Spotify", 10.99), (D(2026, 9, 30), "Anchor", -1.0)])             # a refund (credit) from Spotify
    return make_ctx(rows)


RICH = richer_ctx()


def test_price_increases_are_ranked_by_percent_and_exclude_decreases():
    t = an.largest_price_increase(RICH, period="all").table
    assert list(t["subscription"]) == ["Hikely", "Netflix"]                  # biggest percent first; Cheapo's drop is not an increase
    assert "Cheapo" not in set(t["subscription"]) and list(t["percent_increase"]) == [50.0, 19.2]
    assert an.largest_price_increase(RICH, period="all").text.startswith("Hikely went up the most")


def test_ended_subscriptions_are_not_counted_as_current_spend():
    assert "Oldgym" not in set(RICH.subscriptions[RICH.subscriptions.active]["merchant"]) and not RICH.subscriptions[RICH.subscriptions.merchant == "Oldgym"].iloc[0]["active"]
    assert "Oldgym" not in set(an.list_subscriptions(RICH).table["subscription"])
    assert an.total_spend(RICH).table.iloc[0]["active_subscriptions"] == len(RICH.active())
    assert "Oldgym" not in set(an.most_expensive(RICH, n=20).table["subscription"])


def test_refunds_are_not_counted_as_spend_with_a_merchant():
    r = an.merchant_total(RICH, merchant="Spotify").table.iloc[0]
    assert r["charges"] == 9 and r["total_paid"] == pytest.approx(10.99 * 9)


def test_renewals_that_are_already_overdue_are_not_upcoming():
    lag = RICH.subscriptions[RICH.subscriptions.merchant == "Lagging"].iloc[0]
    assert lag["active"] and lag["next_expected_date"] < RICH.today                 # still active, but its expected date has passed
    assert "Lagging" not in set(an.upcoming_renewals(RICH, days=365).table.get("subscription", []))
