"""Build the MINIMAL facts a model may see for a finding, and the deterministic template text built from them.

Only whitelisted fields leave this module: type, severity, merchant, currency, amounts, dates (pre-formatted like
"4 Jul 2026"), cycle, and payment channel. No subscription or transaction IDs, no raw bank descriptions, no emails.
The same facts feed the template fallback, so the fallback is exactly as grounded as a model answer must be.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from subsweep.findings import (
    ANNUAL_RENEWAL_SOON, DUPLICATE, PAYPAL_MISMATCH, POSSIBLY_FORGOTTEN, PRICE_HIKE, ZOMBIE, Finding,
)
from subsweep.findings.money import CYCLE_NOUN, fdate, money

DRAFTABLE = {DUPLICATE, PRICE_HIKE, POSSIBLY_FORGOTTEN}
ACCOUNT_PLACEHOLDER = "[ACCOUNT NAME]"
ORDER_PLACEHOLDER = "[ORDER / TRANSACTION DETAILS]"
NAME_PLACEHOLDER = "[YOUR NAME]"


def _d(v: Any) -> Any:
    if isinstance(v, (date, datetime)):
        return fdate(v if isinstance(v, date) and not isinstance(v, datetime) else v.date())
    return v


def _num(v: float | None) -> float | None:
    return None if v is None else round(float(v), 2)


def finding_facts(f: Finding) -> dict:
    """Facts for one finding. Everything here may be sent to the model (after redaction)."""
    cur, ev = f.currency, f.evidence
    facts: dict[str, Any] = {
        "finding_type": f.type, "severity": f.severity, "merchant": f.merchant, "currency": cur,
        "yearly_cost": _num(f.annual_cost), "potential_yearly_savings": _num(f.potential_savings_annual),
        "is_a_guess": f.is_guess,
    }
    if f.type == DUPLICATE:
        facts["streams"] = [{
            "billing_cycle": s["cycle"], "amount": _num(s["amount"]), "paid_via": "PayPal" if s["via_paypal"] else "direct",
            "last_charge": _d(s["last_charge"]), "yearly_cost": _num(s["annual_cost"])} for s in ev["streams"]]
    elif f.type == PRICE_HIKE:
        facts.update(old_price=_num(ev["old_amount"]), new_price=_num(ev["new_amount"]), percent_increase=ev["pct"],
                     new_price_since=_d(ev["since"]), charges_at_new_price=ev["charges_at_new_price"],
                     extra_cost_per_year=_num(ev["annual_impact"]), billing_cycle=ev["cycle"])
    elif f.type == POSSIBLY_FORGOTTEN:
        facts.update(price=_num(ev["amount"]), billing_cycle=ev["cycle"], charges=ev["charges"],
                     first_charge=_d(ev["first_charge"]), last_charge=_d(ev["last_charge"]),
                     why_flagged=[h["text"] for h in f.heuristics])
    elif f.type == ZOMBIE:
        facts.update({k: _d(v) for k, v in ev.items() if k in ("last_used", "trial_end", "charges_after", "total_after", "days_after")})
        facts.update(price=_num(ev["amount"]), billing_cycle=ev["cycle"])
    elif f.type == ANNUAL_RENEWAL_SOON:
        facts.update(renewal_date=_d(ev["renewal_date"]), days_until_renewal=ev["days_until"], price=_num(ev["amount"]),
                     last_charge=_d(ev["last_charge"]))
    elif f.type == PAYPAL_MISMATCH:
        if ev["kind"] == "cancelled_but_charged":
            facts.update(paypal_status=ev["paypal_status"], status_since=_d(ev["status_since"]), charges_after=ev["charges_after"],
                         total_charged_after=_num(ev["total_after"]), latest_charge=_d(ev["latest_charge"]))
        else:
            facts.update(paypal_plan_price=_num(ev["plan_amount"]), statement_price=_num(ev["statement_amount"]))
    facts["computed_summary"] = f.reason          # the engine's own plain-English reading of these same facts
    return facts


# ---------------------------------------------------------------------------------------------- templates
def template_explanation(f: Finding) -> str:
    """2-3 calm, plain sentences using only the finding's own figures (the offline / fallback explanation)."""
    c, ev, m = f.currency, f.evidence, f.merchant
    if f.type == DUPLICATE:
        s = ev["streams"]
        parts = " and ".join(f"{money(x['amount'], c)} per {CYCLE_NOUN[x['cycle']]} ({'via PayPal' if x['via_paypal'] else 'direct'})" for x in s)
        return (f"You seem to be paying for {m} {len(s)} times: {parts}. If you only need one, cancelling the extra "
                f"{'ones' if len(s) > 2 else 'one'} would save about {money(f.potential_savings_annual, c)} a year. "
                "It's worth checking which one you actually use before deciding.")
    if f.type == PRICE_HIKE:
        return (f"{m} went from {money(ev['old_amount'], c)} to {money(ev['new_amount'], c)} per {CYCLE_NOUN[ev['cycle']]} "
                f"(+{ev['pct']:.1f}%) starting {_d(ev['since'])}. That adds about {money(ev['annual_impact'], c)} a year. "
                "You could ask whether a cheaper plan is available, or decide it's still worth it.")
    if f.type == POSSIBLY_FORGOTTEN:
        return (f"{m} has charged you {ev['charges']} times since {_d(ev['first_charge'])}, most recently on {_d(ev['last_charge'])}, "
                f"at {money(ev['amount'], c)} per {CYCLE_NOUN[ev['cycle']]}. This is only a guess from your statement: we can't see whether "
                f"you still use it. If you don't, cancelling would save about {money(f.annual_cost, c)} a year.")
    if f.type == ZOMBIE:
        what = (f"your trial ended on {_d(ev['trial_end'])}" if "trial_end" in ev else f"you last used it on {_d(ev['last_used'])}")
        return (f"{m} is still charging you: {what}, but {ev['charges_after']} charge(s) totalling {money(ev['total_after'], c)} have gone "
                f"through since. At this rate it costs about {money(f.annual_cost, c)} a year. You may want to cancel it.")
    if f.type == ANNUAL_RENEWAL_SOON:
        return (f"{m} renews around {_d(ev['renewal_date'])}, in {ev['days_until']} days, for {money(ev['amount'], c)}. "
                f"Your last charge was on {_d(ev['last_charge'])}. If you don't want to renew, now is the time to cancel or switch plans.")
    if f.type == PAYPAL_MISMATCH:
        if ev["kind"] == "cancelled_but_charged":
            return (f"PayPal shows your {m} subscription as {ev['paypal_status'].lower()} since {_d(ev['status_since'])}, but your statement still "
                    f"has {ev['charges_after']} charge(s) after that, {money(ev['total_after'], c)} in total, the latest on {_d(ev['latest_charge'])}. "
                    "It's worth checking your PayPal account or contacting the merchant.")
        return (f"PayPal lists the {m} plan at {money(ev['plan_amount'], c)}, but your statement shows {money(ev['statement_amount'], c)}. "
                "That can be tax or a plan change, so it's worth a quick look.")
    return f.reason


def draft_purpose(f: Finding) -> str:
    return {
        DUPLICATE: "cancel the extra duplicate subscription and, if it is eligible, refund the most recent duplicate charge",
        PRICE_HIKE: "ask why the price went up, ask about a cheaper plan or a price lock, and say you may cancel if not",
        POSSIBLY_FORGOTTEN: "cancel the subscription and, if the latest charge was unused and eligible, ask for a refund of it",
    }[f.type]


def template_draft(f: Finding) -> tuple[str, str]:
    """(subject, body) for DUPLICATE / PRICE_HIKE / POSSIBLY_FORGOTTEN. Placeholders are literal; nothing is sent."""
    c, ev, m = f.currency, f.evidence, f.merchant
    head = f"Hello {m} support,\n\nAccount: {ACCOUNT_PLACEHOLDER}\nOrder / transaction details: {ORDER_PLACEHOLDER}\n\n"
    tail = f"\nThank you for your help.\n\nKind regards,\n{NAME_PLACEHOLDER}"
    if f.type == DUPLICATE:
        s = sorted(ev["streams"], key=lambda x: x["annual_cost"])
        extra = s[0]
        body = (f"I'm being billed for two {m} subscriptions: "
                + " and ".join(f"{money(x['amount'], c)} per {CYCLE_NOUN[x['cycle']]}" for x in ev["streams"])
                + f". I only need one. Please cancel the {money(extra['amount'], c)} per {CYCLE_NOUN[extra['cycle']]} subscription, "
                  f"last charged on {_d(extra['last_charge'])}, and, if you can, refund that most recent charge. Please confirm once it's done.\n")
        return f"Please cancel my duplicate {m} subscription", head + body + tail
    if f.type == PRICE_HIKE:
        body = (f"I noticed my {m} price rose from {money(ev['old_amount'], c)} to {money(ev['new_amount'], c)} per {CYCLE_NOUN[ev['cycle']]} "
                f"starting {_d(ev['since'])}. Could you explain the change, and tell me whether a cheaper plan or a price lock is available? "
                "If not, I may need to cancel.\n")
        return f"Question about my {m} price increase", head + body + tail
    body = (f"I'd like to cancel my {m} subscription, currently {money(ev['amount'], c)} per {CYCLE_NOUN[ev['cycle']]}. "
            f"My most recent charge was on {_d(ev['last_charge'])}; if I haven't used the service since, I'd appreciate a refund of it. "
            "Please confirm the cancellation in writing.\n")
    return f"Please cancel my {m} subscription", head + body + tail
