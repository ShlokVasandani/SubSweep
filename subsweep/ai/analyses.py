"""Whitelisted pandas analyses for the spending Q&A.

The model never sees raw data and never computes: a question is routed to ONE of these functions, pandas produces a
small result table, and only that table goes to the model. Each analysis also returns a deterministic sentence (`text`)
that is used as the fallback answer and as the grounded baseline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
from rapidfuzz import fuzz

from subsweep.detect.detector import CYCLES_PER_YEAR
from subsweep.findings import FindingsReport
from subsweep.findings.engine import CATEGORY_LABEL, annual_cost
from subsweep.findings.money import CYCLE_NOUN, fdate, money

FINDING_TYPES = ["DUPLICATE", "PRICE_HIKE", "POSSIBLY_FORGOTTEN", "ZOMBIE", "ANNUAL_RENEWAL_SOON", "PAYPAL_MISMATCH"]
CATEGORIES = list(CATEGORY_LABEL)
CYCLES = list(CYCLES_PER_YEAR)


@dataclass
class AnalysisContext:
    subscriptions: pd.DataFrame
    transactions: pd.DataFrame
    report: FindingsReport
    today: date

    def active(self) -> pd.DataFrame:
        s = self.subscriptions
        return s[s["active"]] if len(s) else s

    def merchant_names(self) -> list[str]:
        names = set(self.subscriptions["merchant"]) if len(self.subscriptions) else set()
        if len(self.transactions):
            names |= set(self.transactions["merchant_clean"])
        return sorted((n for n in names if n and len(n) >= 3), key=len, reverse=True)


@dataclass
class AnalysisResult:
    intent: str
    title: str
    table: pd.DataFrame
    text: str
    params: dict = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return self.table is None or len(self.table) == 0


def _empty(intent: str, title: str, text: str, params: dict) -> AnalysisResult:
    return AnalysisResult(intent, title, pd.DataFrame(), text, params)


def table_records(df: pd.DataFrame, limit: int = 25) -> list[dict]:
    """Rows as plain dicts with dates pre-formatted and floats rounded (this exact form is what the model sees)."""
    out = []
    for rec in df.head(limit).to_dict("records"):
        row = {}
        for k, v in rec.items():
            if isinstance(v, date):
                v = fdate(v)
            elif isinstance(v, float):
                v = round(v, 2)
            elif hasattr(v, "item"):
                v = v.item()
            row[k] = v
        out.append(row)
    return out


# ------------------------------------------------------------------------------------------------ analyses
def total_spend(ctx: AnalysisContext, **_) -> AnalysisResult:
    a = ctx.active()
    if a.empty:
        return _empty("total_spend", "Subscription spend", "", {})
    rows = []
    for cur, g in a.groupby("currency"):
        yearly = sum(annual_cost(r) for _, r in g.iterrows())
        rows.append({"currency": cur, "active_subscriptions": len(g), "monthly_spend": round(yearly / 12, 2), "yearly_spend": round(yearly, 2)})
    df = pd.DataFrame(rows)
    text = " ".join(f"In {r['currency']} you spend {money(r['monthly_spend'], r['currency'])} a month ({money(r['yearly_spend'], r['currency'])} a year) "
                    f"across {r['active_subscriptions']} active subscriptions." for r in rows)
    return AnalysisResult("total_spend", "Subscription spend", df, text)


def spend_by_category(ctx: AnalysisContext, category: str | None = None, **_) -> AnalysisResult:
    a = ctx.active().copy()
    params = {"category": category}
    if a.empty:
        return _empty("spend_by_category", "Spend by category", "", params)
    a["label"] = a["category"].map(lambda c: CATEGORY_LABEL.get(c, "other") if c else "other")
    a["yearly"] = a.apply(annual_cost, axis=1)
    if category:
        a = a[a["category"] == category]
        if a.empty:
            return _empty("spend_by_category", f"Spend on {CATEGORY_LABEL.get(category, category)}", "", params)
    df = (a.groupby(["label", "currency"]).agg(subscriptions=("id", "count"), yearly_spend=("yearly", "sum")).reset_index()
          .assign(monthly_spend=lambda d: (d["yearly_spend"] / 12).round(2)).round({"yearly_spend": 2})
          .rename(columns={"label": "category"}).sort_values("yearly_spend", ascending=False))
    df = df[["category", "currency", "subscriptions", "monthly_spend", "yearly_spend"]]
    if category:
        r = df.iloc[0]
        names = ", ".join(sorted(a["merchant"]))
        text = (f"You spend {money(r['monthly_spend'], r['currency'])} a month ({money(r['yearly_spend'], r['currency'])} a year) on "
                f"{r['category']}, across {int(r['subscriptions'])} subscription(s): {names}.")
    else:
        text = "; ".join(f"{r['category']}: {money(r['monthly_spend'], r['currency'])} a month" for _, r in df.iterrows()) + "."
    return AnalysisResult("spend_by_category", f"Spend on {CATEGORY_LABEL.get(category, 'each category')}", df, text, params)


def largest_price_increase(ctx: AnalysisContext, period: str = "all", **_) -> AnalysisResult:
    params = {"period": period}
    rows = []
    for _, s in ctx.subscriptions.iterrows():
        for ch in s["price_changes"]:
            if ch["kind"] != "increase":
                continue
            if period == "this_year" and ch["date"].year != ctx.today.year:
                continue
            rows.append({"subscription": s["merchant"], "currency": s["currency"], "old_price": ch["old_amount"], "new_price": ch["new_amount"],
                         "percent_increase": ch["pct"], "since": ch["date"], "extra_cost_per_year": ch.get("annual_impact")})
    if not rows:
        return _empty("largest_price_increase", "Price increases", "", params)
    df = pd.DataFrame(rows).sort_values(["percent_increase", "extra_cost_per_year"], ascending=False).reset_index(drop=True)
    top = df.iloc[0]
    when = f" in {ctx.today.year}" if period == "this_year" else ""
    text = (f"{top['subscription']} went up the most{when}: from {money(top['old_price'], top['currency'])} to {money(top['new_price'], top['currency'])} "
            f"(+{top['percent_increase']:.1f}%) starting {fdate(top['since'])}, about {money(top['extra_cost_per_year'], top['currency'])} more a year.")
    return AnalysisResult("largest_price_increase", "Price increases", df, text, params)


def most_expensive(ctx: AnalysisContext, n: int = 3, **_) -> AnalysisResult:
    a = ctx.active().copy()
    params = {"n": n}
    if a.empty:
        return _empty("most_expensive", "Most expensive subscriptions", "", params)
    a["yearly_cost"] = a.apply(annual_cost, axis=1).round(2)
    df = a.sort_values("yearly_cost", ascending=False).head(n)[["merchant", "cycle", "current_amount", "currency", "yearly_cost"]]
    df = df.rename(columns={"merchant": "subscription", "current_amount": "price"}).reset_index(drop=True)
    top = df.iloc[0]
    text = f"Your most expensive subscription is {top['subscription']} at {money(top['price'], top['currency'])} per {CYCLE_NOUN[top['cycle']]} ({money(top['yearly_cost'], top['currency'])} a year)."
    return AnalysisResult("most_expensive", "Most expensive subscriptions", df, text, params)


def list_subscriptions(ctx: AnalysisContext, cycle: str | None = None, **_) -> AnalysisResult:
    a = ctx.active()
    params = {"cycle": cycle}
    if cycle:
        a = a[a["cycle"] == cycle]
    if a.empty:
        return _empty("list_subscriptions", "Your subscriptions", "", params)
    df = a.sort_values("merchant")[["merchant", "cycle", "current_amount", "currency", "last_date"]].rename(
        columns={"merchant": "subscription", "current_amount": "price", "last_date": "last_charge"}).reset_index(drop=True)
    text = f"You have {len(df)} active {cycle + ' ' if cycle else ''}subscription(s): " + ", ".join(df["subscription"]) + "."
    return AnalysisResult("list_subscriptions", "Your subscriptions", df, text, params)


def savings_summary(ctx: AnalysisContext, **_) -> AnalysisResult:
    open_f = ctx.report.open()
    if not open_f:
        return _empty("savings_summary", "Potential savings", "", {})
    df = pd.DataFrame([{"finding": f.type, "subscription": f.merchant, "severity": f.severity, "currency": f.currency,
                        "potential_yearly_savings": round(f.potential_savings_annual, 2)} for f in open_f])
    s = ctx.report.summary
    text = (f"There are {len(open_f)} open findings. Potential savings if you act on them: {money(s['potential_savings_annual'], s['currency'])} a year "
            f"({money(s['potential_savings_monthly'], s['currency'])} a month).") if s.get("currency") else f"There are {len(open_f)} open findings."
    return AnalysisResult("savings_summary", "Potential savings", df, text)


def findings_overview(ctx: AnalysisContext, type: str | None = None, **_) -> AnalysisResult:
    fs = [f for f in ctx.report.open() if not type or f.type == type]
    params = {"type": type}
    if not fs:
        return _empty("findings_overview", "Findings", "", params)
    df = pd.DataFrame([{"finding": f.type, "subscription": f.merchant, "severity": f.severity, "currency": f.currency,
                        "potential_yearly_savings": round(f.potential_savings_annual, 2)} for f in fs])
    text = f"There {'is' if len(fs) == 1 else 'are'} {len(fs)} open {type.replace('_', ' ').lower() + ' ' if type else ''}finding(s): " + "; ".join(f.title for f in fs) + "."
    return AnalysisResult("findings_overview", "Findings", df, text, params)


def upcoming_renewals(ctx: AnalysisContext, days: int = 30, **_) -> AnalysisResult:
    a = ctx.active()
    params = {"days": days}
    if a.empty:
        return _empty("upcoming_renewals", "Upcoming charges", "", params)
    end = ctx.today + timedelta(days=days)
    r = a[(a["next_expected_date"] >= ctx.today) & (a["next_expected_date"] <= end)].sort_values("next_expected_date")
    if r.empty:
        return _empty("upcoming_renewals", "Upcoming charges", "", params)
    df = r[["merchant", "cycle", "current_amount", "currency", "next_expected_date"]].rename(
        columns={"merchant": "subscription", "current_amount": "price", "next_expected_date": "expected_on"}).reset_index(drop=True)
    text = f"In the next {days} days: " + "; ".join(f"{x['subscription']} on {fdate(x['expected_on'])} ({money(x['price'], x['currency'])})" for _, x in df.iterrows()) + "."
    return AnalysisResult("upcoming_renewals", "Upcoming charges", df, text, params)


def merchant_total(ctx: AnalysisContext, merchant: str = "", **_) -> AnalysisResult:
    params = {"merchant": merchant}
    t = ctx.transactions
    if not merchant or t.empty:
        return _empty("merchant_total", "Spend with a merchant", "", params)
    m = merchant.lower()
    mask = t["merchant_clean"].map(lambda n: m in n.lower() or n.lower() in m or fuzz.token_set_ratio(m, n.lower()) >= 90)
    d = t[mask & (t["amount"] < 0)]
    if d.empty:
        return _empty("merchant_total", f"Spend with {merchant}", "", params)
    rows = []
    for cur, g in d.groupby("currency"):
        rows.append({"merchant": merchant, "currency": cur, "charges": len(g), "total_paid": round(-g["amount"].sum(), 2),
                     "first_charge": g["date"].min(), "last_charge": g["date"].max()})
    df = pd.DataFrame(rows)
    text = " ".join(f"You have paid {r['merchant']} {money(r['total_paid'], r['currency'])} across {r['charges']} charges, from {fdate(r['first_charge'])} "
                    f"to {fdate(r['last_charge'])}." for r in rows)
    return AnalysisResult("merchant_total", f"Spend with {merchant}", df, text, params)


INTENTS = {
    "total_spend": total_spend, "spend_by_category": spend_by_category, "largest_price_increase": largest_price_increase,
    "most_expensive": most_expensive, "list_subscriptions": list_subscriptions, "savings_summary": savings_summary,
    "findings_overview": findings_overview, "upcoming_renewals": upcoming_renewals, "merchant_total": merchant_total,
}

# parameter validators: name -> (type, check)
PARAM_RULES = {
    "total_spend": {}, "savings_summary": {}, "list_subscriptions": {"cycle": lambda v: v in CYCLES},
    "spend_by_category": {"category": lambda v: v in CATEGORIES},
    "largest_price_increase": {"period": lambda v: v in ("this_year", "all")},
    "most_expensive": {"n": lambda v: isinstance(v, int) and 1 <= v <= 20},
    "findings_overview": {"type": lambda v: v in FINDING_TYPES},
    "upcoming_renewals": {"days": lambda v: isinstance(v, int) and 1 <= v <= 365},
    "merchant_total": {"merchant": lambda v: isinstance(v, str) and 2 <= len(v) <= 60},
}


def clean_params(intent: str, params: dict | None) -> dict | None:
    """Validate router-supplied params against the whitelist. Returns cleaned params, or None if anything is off."""
    rules = PARAM_RULES.get(intent)
    if rules is None:
        return None
    out = {}
    for k, v in (params or {}).items():
        if v is None:
            continue
        if k not in rules or not rules[k](v):
            return None
        out[k] = v
    return out


def run(intent: str, ctx: AnalysisContext, params: dict | None = None) -> AnalysisResult:
    return INTENTS[intent](ctx, **(params or {}))


# ------------------------------------------------------------------------------------------------ rule-based router
_CAT_WORDS = {
    "streaming": "streaming", "video": "streaming", "tv": "streaming", "movies": "streaming", "music": "music_and_audio",
    "audio": "music_and_audio", "podcast": "music_and_audio", "software": "software_and_cloud", "cloud": "software_and_cloud",
    "storage": "software_and_cloud", "news": "news_and_learning", "learning": "news_and_learning", "courses": "news_and_learning",
    "fitness": "fitness_and_wellness", "gym": "fitness_and_wellness", "wellness": "fitness_and_wellness",
    "gaming": "gaming", "games": "gaming",
}


def _days(q: str) -> int:
    m = re.search(r"(\d+)\s*(day|week|month)s?", q)
    if m:
        n = int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)]
        return max(1, min(n, 365))
    return 365 if re.search(r"\b(this|next) year\b", q) else 30


def route_rules(question: str, ctx: AnalysisContext) -> tuple[str, dict] | None:
    q = question.lower()
    if re.search(r"went up|gone up|price (?:hike|increase|rise)|increase[ds]?\b|raised|rose\b|more expensive than", q):
        return "largest_price_increase", {"period": "this_year" if "this year" in q else "all"}
    if re.search(r"\brenew|coming up|due soon|next charge|upcoming", q):
        return "upcoming_renewals", {"days": _days(q)}
    if re.search(r"cancel+ed? (?:but|yet)|still (?:being )?charged|mismatch|after i cancel", q):
        return "findings_overview", {"type": "PAYPAL_MISMATCH"}
    if re.search(r"duplicate|twice|double[- ]?(?:billed|charged)|paying for .* twice", q):
        return "findings_overview", {"type": "DUPLICATE"}
    if re.search(r"forgotten|unused|not using|stopped using|don'?t use", q):
        return "findings_overview", {"type": "POSSIBLY_FORGOTTEN"}
    if re.search(r"\bsav(?:e|ing|ings)\b|\bcancel\b|\bcut\b", q):
        return "savings_summary", {}
    merchant = next((n for n in ctx.merchant_names() if re.search(rf"(?<![a-z0-9]){re.escape(n.lower())}(?![a-z0-9])", q)), None)
    if merchant and re.search(r"how much|total|paid|spent|spend|cost", q):
        return "merchant_total", {"merchant": merchant}
    cat = next((c for w, c in _CAT_WORDS.items() if re.search(rf"\b{w}\b", q)), None)
    if cat:
        return "spend_by_category", {"category": cat}
    if re.search(r"most expensive|priciest|costliest|biggest|highest|largest", q):
        m = re.search(r"top (\d+)", q)
        return "most_expensive", {"n": min(int(m.group(1)), 20) if m else 3}
    if re.search(r"\blist\b|\bshow\b|what subscriptions|which subscriptions|all my subscriptions|subscribed to|what am i paying", q):
        return "list_subscriptions", {}
    if re.search(r"\btotal\b|altogether|per month|a month|per year|a year|monthly|annual|yearly|how much do i spend|how much am i spending|health score", q):
        return "total_spend", {}
    return None
