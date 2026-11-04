"""Guardrails for the Q&A box: refuse non-financial requests and any personalised investment advice.

These run BEFORE any model call (so a refused question costs nothing and sends nothing) and again on model output.
"""
from __future__ import annotations

import re

INVESTMENT_MSG = ("I can't give investment advice. I can tell you what you spend on subscriptions and where you "
                  "could save, based on your statement.")
OFF_TOPIC_MSG = ("I can only help with the subscriptions and spending in your statement. Try asking what you spend on "
                 "streaming, which subscription went up the most, or what you could save.")
UNKNOWN_MSG = ("I couldn't find that in your data. I can answer questions about your subscriptions, such as what you spend "
               "on streaming, which price went up the most, upcoming renewals, or how much you could save.")
INJECTION_MSG = ("I can only answer questions about your subscriptions and spending, so I can't follow that instruction.")

_INVEST = [
    r"\bshould i (?:buy|sell|invest|trade|hold|short)\b",
    r"\b(?:where|how) (?:should|can|do) i invest\b",
    r"\b(?:which|what|best) (?:stocks?|shares?|funds?|etfs?|mutual funds?|crypto(?:currency)?|coins?|bonds?|index funds?)\b",
    r"\b(?:buy|sell|short) (?:some )?(?:stocks?|shares?|bitcoin|ethereum|crypto|etfs?|options)\b",
    r"\b(?:invest(?:ing|ment)?|portfolio|retirement (?:fund|account|plan)|401\s?k|roth|ira|forex|day trading|hedge fund)\b",
    r"\b(?:stock|share|crypto|bitcoin|market) (?:tip|pick|prediction|advice|recommendation)s?\b",
    r"\bmutual funds?\b",
    r"\b(?:bitcoin|ethereum|dogecoin|crypto(?:currency|currencies)?|nfts?|altcoins?)\b",
]
_INJECTION = [
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier) (?:instructions|rules|prompts?)",
    r"\b(?:system|developer) prompt\b", r"\bjailbreak\b", r"\bpretend (?:you are|to be)\b", r"\byou are now\b",
    r"\breveal (?:your|the) (?:instructions|prompt|rules)\b",
]
_FINANCE = re.compile(
    r"\b(?:subscriptions?|subs|spend(?:ing)?|spent|cost|costs|price|prices|charge[sd]?|billed|billing|bill|paid|pay|"
    r"payment|cancel(?:led|ling)?|renew(?:al|s)?|refund|sav(?:e|ing|ings)|duplicate|monthly|annual|yearly|weekly|quarterly|"
    r"per (?:month|year)|expensive|cheap(?:est)?|total|how much|statement|paypal|trial|plans?|membership|money|budget|wallet|expenses?|afford|worth it|overpay(?:ing)?|forgotten|unused|"
    r"streaming|music|software|cloud|news|fitness|gym|gaming|apps?|services?|went up|increase|raised|hike|"
    r"health score|\$|₹|€|£)",
    re.I)
_ADVICE_OUT = re.compile(
    r"\b(?:you should (?:invest|buy|sell)|i recommend (?:buying|investing|selling)|consider investing|"
    r"(?:buy|sell) (?:shares|stocks|bitcoin|crypto)|good investment|put (?:it|your money) (?:in|into) (?:stocks|funds|crypto))\b", re.I)


def _any(patterns, text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def pre_check(question: str) -> tuple[str | None, str | None]:
    """Return (blocked_reason, user_message). (None, None) means the question may proceed to routing."""
    q = (question or "").strip()
    if not q:
        return "empty", "Ask me something about your subscriptions or spending."
    if len(q) > 500:
        return "too_long", "Please keep your question under 500 characters."
    if _any(_INVESTMENT_PATTERNS, q):
        return "investment_advice", INVESTMENT_MSG
    if _any(_INJECTION, q):
        return "prompt_injection", INJECTION_MSG
    return None, None


_INVESTMENT_PATTERNS = _INVEST


def looks_financial(question: str) -> bool:
    return bool(_FINANCE.search(question or ""))


def output_is_advice(text: str) -> bool:
    return bool(_ADVICE_OUT.search(text or ""))
