"""Findings engine: turns detected subscriptions into plain-English, severity-ranked findings.

Finding types: DUPLICATE, PRICE_HIKE, POSSIBLY_FORGOTTEN, ZOMBIE, ANNUAL_RENEWAL_SOON.
All thresholds live in config/rules.yaml under `findings`. Nothing here talks to a model or
to PayPal; the AI layer later receives these computed results and explains them.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

import pandas as pd

from subsweep.detect.detector import CYCLES_PER_YEAR
from subsweep.detect.rules import load_rules

from .money import CYCLE_NOUN, fdate, money

DUPLICATE = "DUPLICATE"
PRICE_HIKE = "PRICE_HIKE"
POSSIBLY_FORGOTTEN = "POSSIBLY_FORGOTTEN"
ZOMBIE = "ZOMBIE"
ANNUAL_RENEWAL_SOON = "ANNUAL_RENEWAL_SOON"
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

CATEGORY_LABEL = {
    "streaming": "streaming", "music_and_audio": "music and audio", "software_and_cloud": "software and cloud",
    "news_and_learning": "news and learning", "fitness_and_wellness": "fitness and wellness", "gaming": "gaming",
}


@dataclass
class Finding:
    id: str
    type: str
    severity: str
    title: str
    reason: str
    merchant: str
    currency: str
    subscription_ids: list[str]
    annual_cost: float                    # yearly cost of the subscription(s) this finding is about
    potential_savings_annual: float       # what acting on it could save (0 if informational)
    evidence: dict = field(default_factory=dict)   # exact figures behind the reason (fed to the AI layer)
    is_guess: bool = False                # True when the finding is a heuristic, not a fact
    status: str = "open"                  # open | dismissed
    heuristics: list[dict] = field(default_factory=list)   # visible score components (POSSIBLY_FORGOTTEN)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class FindingsReport:
    findings: list[Finding]
    summary: dict

    def open(self) -> list[Finding]:
        return [f for f in self.findings if f.status == "open"]

    def of_type(self, t: str) -> list[Finding]:
        return [f for f in self.findings if f.type == t]


def annual_cost(sub) -> float:
    return float(sub["current_amount"]) * CYCLES_PER_YEAR[sub["cycle"]]


def _fid(ftype: str, sub_ids: Iterable[str]) -> str:
    return "fnd_" + hashlib.sha1((ftype + "|" + ",".join(sorted(sub_ids))).encode()).hexdigest()[:10]


def _price_text(sub) -> str:
    return f"{money(sub['current_amount'], sub['currency'])} per {CYCLE_NOUN[sub['cycle']]}"


def _channel(sub) -> str:
    return "through PayPal" if sub["via_paypal"] else "directly"


# ---------------------------------------------------------------------------
# DUPLICATE
# ---------------------------------------------------------------------------
def _duplicates(active: pd.DataFrame, rules: dict) -> list[Finding]:
    out: list[Finding] = []
    for (key, cur), grp in active.groupby(["merchant_key", "currency"], sort=True):
        if len(grp) < 2:
            continue
        subs = [r for _, r in grp.iterrows()]
        subs.sort(key=lambda s: (-annual_cost(s), s["id"]))
        keep, extras = subs[0], subs[1:]
        saving = sum(annual_cost(s) for s in extras)
        name = min((s["merchant"] for s in subs), key=lambda n: (len(n), n))   # "Spotify", not "Spotifyab"
        if len({s["via_paypal"] for s in subs}) == 2:
            how = "One is billed through PayPal and another directly."
        elif len({s["cycle"] for s in subs}) > 1:
            how = "They bill on different cycles (" + " and ".join(sorted({s["cycle"] for s in subs})) + ")."
        else:
            how = "They are billed separately on different dates."
        parts = "; ".join(
            f"{_price_text(s)} {_channel(s)}, last charged {fdate(s['last_date'])}" for s in subs)
        reason = (
            f"You appear to be paying for {name} {len(subs)} times: {parts}. {how} "
            f"If you only need one, keeping the most expensive per year ({money(annual_cost(keep), cur)}/year) "
            f"and cancelling the rest would save {money(saving, cur)} a year."
        )
        out.append(Finding(
            id=_fid(DUPLICATE, [s["id"] for s in subs]), type=DUPLICATE,
            severity=rules["findings"]["duplicate"]["severity"],
            title=f"{name}: {len(subs)} active subscriptions",
            reason=reason, merchant=name, currency=cur,
            subscription_ids=[s["id"] for s in subs],
            annual_cost=round(sum(annual_cost(s) for s in subs), 2),
            potential_savings_annual=round(saving, 2),
            evidence={"streams": [{
                "subscription_id": s["id"], "cycle": s["cycle"], "amount": float(s["current_amount"]),
                "via_paypal": bool(s["via_paypal"]), "last_charge": s["last_date"],
                "annual_cost": round(annual_cost(s), 2)} for s in subs],
                "kept_subscription_id": keep["id"]},
        ))
    return out


# ---------------------------------------------------------------------------
# PRICE_HIKE
# ---------------------------------------------------------------------------
def _price_hikes(active: pd.DataFrame, rules: dict) -> list[Finding]:
    cfg = rules["findings"]["price_hike"]
    out = []
    for _, s in active.iterrows():
        hikes = [c for c in s["price_changes"] if c["kind"] == "increase"]
        if not hikes:
            continue
        last = hikes[-1]
        cur = s["currency"]
        impact = float(last.get("annual_impact") or (last["new_amount"] - last["old_amount"]) * CYCLES_PER_YEAR[s["cycle"]])
        reason = (
            f"{s['merchant']} went from {money(last['old_amount'], cur)} to {money(last['new_amount'], cur)} per "
            f"{CYCLE_NOUN[s['cycle']]} (+{last['pct']:.1f}%) starting {fdate(last['date'])}, and the new price has held for "
            f"{last['charges_at_new_price']} charges. That adds {money(impact, cur)} a year."
        )
        if len(hikes) > 1:
            first = hikes[0]
            reason += (f" This is the latest of {len(hikes)} increases; it started at "
                       f"{money(first['old_amount'], cur)}.")
        out.append(Finding(
            id=_fid(PRICE_HIKE, [s["id"]]), type=PRICE_HIKE,
            severity="high" if last["pct"] >= cfg["high_if_pct_at_least"] else "medium",
            title=f"{s['merchant']}: price up {last['pct']:.0f}%", reason=reason, merchant=s["merchant"], currency=cur,
            subscription_ids=[s["id"]], annual_cost=round(annual_cost(s), 2),
            potential_savings_annual=round(impact, 2),
            evidence={"old_amount": last["old_amount"], "new_amount": last["new_amount"], "pct": last["pct"],
                      "since": last["date"], "charges_at_new_price": last["charges_at_new_price"],
                      "annual_impact": round(impact, 2), "cycle": s["cycle"], "all_increases": hikes},
        ))
    return out


# ---------------------------------------------------------------------------
# POSSIBLY_FORGOTTEN (heuristic - an honest guess)
# ---------------------------------------------------------------------------
def forgotten_score(sub, active: pd.DataFrame, rules: dict) -> tuple[int, list[dict]]:
    cfg = rules["findings"]["forgotten"]
    pts = cfg["points"]
    parts: list[dict] = []
    if sub["discretionary"]:
        label = CATEGORY_LABEL.get(sub["category"], "this kind of")
        parts.append({"rule": "discretionary_category", "points": pts["discretionary_category"],
                      "text": f"{label.capitalize()} subscriptions are usually optional extras."})
    span_months = (sub["last_date"] - sub["first_date"]).days / 30.44
    if span_months >= cfg["long_run_months"]:
        parts.append({"rule": "long_run", "points": pts["long_run"],
                      "text": f"It has been billed for {span_months:.0f} months ({sub['n_charges']} charges) "
                              f"since {fdate(sub['first_date'])}."})
    if sub["n_charges"] >= 2 and sub["amount_stability"] >= 0.99 and not sub["price_changes"]:
        parts.append({"rule": "identical_amounts", "points": pts["identical_amounts"],
                      "text": "The amount never changes, which is typical of autopay that nobody revisits."})
    if sub["discretionary"] and sub["category"]:
        same = active[(active["category"] == sub["category"]) & (active["id"] != sub["id"])]
        if len(same) + 1 >= cfg["overlap_min_services"]:
            parts.append({"rule": "overlapping_services", "points": pts["overlapping_services"],
                          "text": f"You pay for {len(same) + 1} {CATEGORY_LABEL.get(sub['category'], 'similar')} "
                                  "services at once."})
    return sum(p["points"] for p in parts), parts


def _forgotten(active: pd.DataFrame, rules: dict) -> list[Finding]:
    cfg = rules["findings"]["forgotten"]
    out = []
    for _, s in active.iterrows():
        score, parts = forgotten_score(s, active, rules)
        if score < cfg["min_score_to_flag"]:
            continue
        cur = s["currency"]
        cost = annual_cost(s)
        why = " ".join(p["text"] for p in parts)
        reason = (
            f"{s['merchant']} ({_price_text(s)}, about {money(cost, cur)} a year) might be one you've stopped using. {why} "
            "This is a guess from your statement alone: SubSweep can't see whether you use it. "
            "If you still do, mark it as \"still using\" and it will be dismissed."
        )
        out.append(Finding(
            id=_fid(POSSIBLY_FORGOTTEN, [s["id"]]), type=POSSIBLY_FORGOTTEN,
            severity="medium" if score >= cfg["medium_if_score_at_least"] else "low",
            title=f"{s['merchant']}: still using it?", reason=reason, merchant=s["merchant"], currency=cur,
            subscription_ids=[s["id"]], annual_cost=round(cost, 2), potential_savings_annual=round(cost, 2),
            evidence={"score": score, "min_score_to_flag": cfg["min_score_to_flag"], "category": s["category"],
                      "charges": int(s["n_charges"]), "first_charge": s["first_date"], "last_charge": s["last_date"],
                      "amount": float(s["current_amount"]), "cycle": s["cycle"]},
            is_guess=True, heuristics=parts,
        ))
    return out


# ---------------------------------------------------------------------------
# ZOMBIE (needs a user-supplied usage signal)
# ---------------------------------------------------------------------------
def _charge_dates(tx: pd.DataFrame | None, ids: list[str]) -> list[tuple[date, float]]:
    if tx is None or tx.empty:
        return []
    sub = tx[tx["id"].isin(set(ids))].sort_values("date")
    return [(d, -float(a)) for d, a in zip(sub["date"], sub["amount"])]


def _zombies(active: pd.DataFrame, tx: pd.DataFrame | None, signals: dict, rules: dict) -> list[Finding]:
    cfg = rules["findings"]["zombie"]
    out = []
    for _, s in active.iterrows():
        sig = signals.get(s["merchant_key"]) or signals.get(s["id"])
        if not sig:
            continue
        charges = _charge_dates(tx, list(s["txn_ids"]))
        cur, cost = s["currency"], annual_cost(s)
        reason, ev = None, {}
        trial_end = sig.get("trial_end")
        last_used = sig.get("last_used")
        if trial_end:
            after = [(d, a) for d, a in charges if d > trial_end]
            if after:
                total = sum(a for _, a in after)
                reason = (f"Your {s['merchant']} trial ended on {fdate(trial_end)}, but you have been charged "
                          f"{len(after)} time(s) since, {money(total, cur)} in total (latest {fdate(after[-1][0])}).")
                ev = {"trial_end": trial_end, "charges_after": len(after), "total_after": round(total, 2)}
        if reason is None and last_used:
            after = [(d, a) for d, a in charges if d > last_used]
            gap = (s["last_date"] - last_used).days
            if after and gap >= cfg["min_days_after_last_use"]:
                total = sum(a for _, a in after)
                reason = (f"You told us you last used {s['merchant']} on {fdate(last_used)}, yet {len(after)} charge(s) "
                          f"({money(total, cur)} in total) have gone through since, the latest on {fdate(s['last_date'])}, "
                          f"{gap} days later.")
                ev = {"last_used": last_used, "charges_after": len(after), "total_after": round(total, 2), "days_after": gap}
        if reason:
            out.append(Finding(
                id=_fid(ZOMBIE, [s["id"]]), type=ZOMBIE, severity="high", title=f"{s['merchant']}: charges after you stopped",
                reason=reason + f" At {_price_text(s)}, that is about {money(cost, cur)} a year.", merchant=s["merchant"], currency=cur,
                subscription_ids=[s["id"]], annual_cost=round(cost, 2), potential_savings_annual=round(cost, 2),
                evidence={**ev, "amount": float(s["current_amount"]), "cycle": s["cycle"]},
            ))
    return out


# ---------------------------------------------------------------------------
# ANNUAL_RENEWAL_SOON
# ---------------------------------------------------------------------------
def _renewals(active: pd.DataFrame, today: date, rules: dict) -> list[Finding]:
    cfg = rules["findings"]["annual_renewal"]
    out = []
    for _, s in active.iterrows():
        if s["cycle"] != "yearly":
            continue
        due = s["next_expected_date"]
        days = (due - today).days
        if not (0 <= days <= cfg["window_days"]):
            continue
        cur = s["currency"]
        reason = (f"{s['merchant']} renews around {fdate(due)} ({days} day{'s' if days != 1 else ''} from {fdate(today)}) "
                  f"for {money(s['current_amount'], cur)}, based on your last charge on {fdate(s['last_date'])}. "
                  "Cancelling or switching plans before then avoids the charge.")
        out.append(Finding(
            id=_fid(ANNUAL_RENEWAL_SOON, [s["id"]]), type=ANNUAL_RENEWAL_SOON,
            severity="high" if days <= cfg["high_if_days_at_most"] else "medium",
            title=f"{s['merchant']} renews in {days} days", reason=reason, merchant=s["merchant"], currency=cur,
            subscription_ids=[s["id"]], annual_cost=round(annual_cost(s), 2), potential_savings_annual=0.0,
            evidence={"renewal_date": due, "days_until": days, "amount": float(s["current_amount"]),
                      "last_charge": s["last_date"], "today": today},
        ))
    return out


# ---------------------------------------------------------------------------
# totals and health score
# ---------------------------------------------------------------------------
def compute_savings(findings: list[Finding], active: pd.DataFrame) -> dict[str, float]:
    """Potential yearly savings per currency from OPEN findings, counting each subscription once.

    A subscription flagged for cancellation (duplicate extra, forgotten, zombie) counts its full annual cost;
    one only flagged for a price hike counts the annual impact of the hike. Renewal reminders carry no savings.
    """
    per_sub: dict[str, tuple[str, float]] = {}
    annual = {r["id"]: (r["currency"], annual_cost(r)) for _, r in active.iterrows()}
    # The stream an open DUPLICATE tells you to keep is not also counted as "forgotten" savings:
    # we assume you keep at least one. A ZOMBIE (you said you stopped using it) still counts.
    kept = {f.evidence["kept_subscription_id"] for f in findings if f.type == DUPLICATE and f.status == "open"}
    for f in findings:
        if f.status != "open":
            continue          # (renewal reminders carry no savings and fall through every branch below)
        if f.type == DUPLICATE:
            keep = f.evidence["kept_subscription_id"]
            for sid in f.subscription_ids:
                if sid != keep and sid in annual:
                    per_sub[sid] = (f.currency, annual[sid][1])
        elif f.type in (POSSIBLY_FORGOTTEN, ZOMBIE):
            sid = f.subscription_ids[0]
            if f.type == POSSIBLY_FORGOTTEN and sid in kept:
                continue
            per_sub[sid] = (f.currency, annual[sid][1])
        elif f.type == PRICE_HIKE:
            sid = f.subscription_ids[0]
            if sid not in per_sub:
                per_sub[sid] = (f.currency, f.potential_savings_annual)
    totals: dict[str, float] = defaultdict(float)
    for cur, amt in per_sub.values():
        totals[cur] += amt
    return {k: round(v, 2) for k, v in totals.items()}


def health_score(open_findings: list[Finding], active: pd.DataFrame, savings_primary: float,
                 spend_primary: float, rules: dict) -> dict:
    h = rules["findings"]["health"]
    ratio = min(1.0, savings_primary / spend_primary) if spend_primary > 0 else 0.0
    a = h["savings_weight"] * ratio
    b = min(h["finding_cap"], sum(h["severity_points"][f.severity] for f in open_findings))
    c = min(h["sprawl_cap"], h["sprawl_points"] * max(0, len(active) - h["sprawl_free"]))
    score = int(round(max(0.0, min(100.0, 100.0 - a - b - c))))
    return {
        "score": score,
        "breakdown": {"money_at_risk": round(a, 1), "open_findings": float(b), "subscription_sprawl": float(c)},
        "formula": (f"100 - A - B - C. A = {h['savings_weight']} x (potential savings / subscription spend) = {a:.1f}; "
                    f"B = open findings, {', '.join(f'{k} {v}' for k, v in h['severity_points'].items())} points each, "
                    f"capped at {h['finding_cap']} = {b}; "
                    f"C = {h['sprawl_points']} points per active subscription beyond {h['sprawl_free']}, "
                    f"capped at {h['sprawl_cap']} = {c}."),
        "label": "Healthy" if score >= 80 else "Worth a look" if score >= 55 else "Needs attention",
    }


def generate_findings(
    subscriptions: pd.DataFrame,
    transactions: pd.DataFrame | None = None,
    rules: dict | None = None,
    today: date | None = None,
    dismissed: Iterable[str] = (),
    usage_signals: dict | None = None,
) -> FindingsReport:
    """Classify detected subscriptions into findings and compute spend, savings and health score.

    today          date used for "renewal soon" (defaults to the latest charge in `transactions`)
    dismissed      finding IDs the user dismissed or marked "still using"; they stay listed as dismissed
    usage_signals  {merchant_key | subscription_id: {"last_used": date, "trial_end": date}} for ZOMBIE
    """
    rules = rules or load_rules()
    dismissed = set(dismissed)
    if today is None:
        today = max(transactions["date"]) if transactions is not None and len(transactions) else date.today()
    empty_summary = {"currency": None, "monthly_spend": 0.0, "annual_spend": 0.0, "potential_savings_annual": 0.0,
                     "potential_savings_monthly": 0.0, "active_subscriptions": 0, "open_findings": 0,
                     "by_currency": {}, "health": health_score([], subscriptions.iloc[0:0], 0, 0, rules)}
    if subscriptions is None or subscriptions.empty:
        return FindingsReport([], empty_summary)

    active = subscriptions[subscriptions["active"]].reset_index(drop=True)
    findings: list[Finding] = []
    if len(active):
        findings += _duplicates(active, rules)
        findings += _price_hikes(active, rules)
        findings += _forgotten(active, rules)
        findings += _zombies(active, transactions, usage_signals or {}, rules)
        findings += _renewals(active, today, rules)
    for f in findings:
        if f.id in dismissed:
            f.status = "dismissed"
    findings.sort(key=lambda f: (f.status != "open", SEVERITY_ORDER[f.severity], -f.potential_savings_annual, f.id))

    savings = compute_savings(findings, active)
    by_cur: dict[str, dict] = {}
    for cur, grp in active.groupby("currency"):
        annual = sum(annual_cost(r) for _, r in grp.iterrows())
        by_cur[cur] = {"annual_spend": round(annual, 2), "monthly_spend": round(annual / 12, 2),
                       "potential_savings_annual": savings.get(cur, 0.0),
                       "potential_savings_monthly": round(savings.get(cur, 0.0) / 12, 2),
                       "active_subscriptions": int(len(grp))}
    primary = max(by_cur, key=lambda c: by_cur[c]["annual_spend"]) if by_cur else None
    p = by_cur.get(primary, {})
    open_f = [f for f in findings if f.status == "open"]
    summary = {
        "currency": primary, "monthly_spend": p.get("monthly_spend", 0.0), "annual_spend": p.get("annual_spend", 0.0),
        "potential_savings_annual": p.get("potential_savings_annual", 0.0),
        "potential_savings_monthly": p.get("potential_savings_monthly", 0.0),
        "active_subscriptions": int(len(active)), "open_findings": len(open_f), "by_currency": by_cur,
        "health": health_score(open_f, active, p.get("potential_savings_annual", 0.0), p.get("annual_spend", 0.0), rules),
        "health_note": "Scored on the main currency; other currencies still add to the open-findings count."
        if len(by_cur) > 1 else "",
    }
    return FindingsReport(findings, summary)
