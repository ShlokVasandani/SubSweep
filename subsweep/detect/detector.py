"""Explainable recurring-charge detector. Plain Python rules; no ML.

Pipeline per merchant group (see README "How detection works"):
  1 group merchants (token-set similarity)      -> grouping.py
  2 drop one-off bursts, check category rules   -> exclusions.py
  3 split into streams (channel, currency, amount cluster, same-day rank, interleaved accounts)
  4 fit a cycle to the gaps                     -> cycles.py
  5 amount stability + price changes            -> pricing.py
  6 confidence + reason                          -> confidence.py
Everything that is dropped is returned in `excluded` with the rule that dropped it.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import groupby

import pandas as pd

from . import confidence as conf
from .cycles import CycleFit, fit_cycle, gap_fit
from .exclusions import categorize, find_burst_indices
from .grouping import group_merchants
from .pricing import analyse_prices
from .rules import load_rules

CYCLES_PER_YEAR = {"weekly": 52, "monthly": 12, "quarterly": 4, "yearly": 1}

SUBSCRIPTION_COLUMNS = [
    "id", "merchant", "merchant_key", "cycle", "cycle_days", "n_charges", "avg_amount", "current_amount",
    "currency", "first_date", "last_date", "next_expected_date", "active", "confidence",
    "confidence_reason", "regularity", "amount_stability", "missed_cycles", "price_changes",
    "pending_price_change", "has_price_hike", "via_paypal", "category", "discretionary",
    "same_day_extra_charges", "burst_charges_ignored", "txn_ids",
]
EXCLUDED_COLUMNS = ["merchant", "merchant_key", "n_charges", "rule", "reason", "first_date", "last_date", "txn_ids"]


@dataclass
class DetectionResult:
    subscriptions: pd.DataFrame
    excluded: pd.DataFrame
    as_of: date
    rules: dict


@dataclass
class _Charge:
    ordinal: int
    amount: float
    txn_id: str
    name: str
    date: date


def _display_name(charges: list[_Charge]) -> str:
    return Counter(c.name for c in charges).most_common(1)[0][0]


def _amount_clusters(charges: list[_Charge], ratio: float) -> list[list[_Charge]]:
    """Split charges into price tiers: a jump larger than `ratio` between sorted amounts starts a new plan."""
    srt = sorted(charges, key=lambda c: c.amount)
    clusters = [[srt[0]]]
    for c in srt[1:]:
        prev = clusters[-1][-1].amount
        if prev > 0 and c.amount / prev > ratio:
            clusters.append([c])
        else:
            clusters[-1].append(c)
    return [sorted(cl, key=lambda c: (c.ordinal, c.txn_id)) for cl in clusters]


def _rank_streams(charges: list[_Charge]) -> list[list[_Charge]]:
    """First charge per date -> stream 0, second charge on the same date -> stream 1, ..."""
    streams: list[list[_Charge]] = []
    for _, same_day in groupby(charges, key=lambda c: c.ordinal):
        for rank, ch in enumerate(same_day):
            if rank == len(streams):
                streams.append([])
            streams[rank].append(ch)
    return streams


def _split_interleaved(charges: list[_Charge], rules: dict) -> list[list[_Charge]] | None:
    """Try to explain one irregular series as two interleaved subscriptions (e.g. two accounts)."""
    cfgs = {n: rules["cycles"][n] for n in ("monthly", "quarterly", "yearly")}
    streams: list[list[_Charge]] = []
    for ch in charges:
        best, best_dev = None, None
        for s in streams:
            gap = ch.ordinal - s[-1].ordinal
            for name, cfg in cfgs.items():
                k, kind, _ = gap_fit(gap, cfg, rules)
                if k == 1:
                    dev = abs(gap - (cfg["min_days"] + cfg["max_days"]) / 2)
                    if best_dev is None or dev < best_dev:
                        best, best_dev = s, dev
        if best is not None:
            best.append(ch)
        elif len(streams) < 2:
            streams.append([ch])
        else:
            return None
    return streams if len(streams) == 2 else None


class _Detector:
    def __init__(self, rules: dict, as_of: date):
        self.rules = rules
        self.as_of = as_of
        self.subs: list[dict] = []
        self.excluded: list[dict] = []

    # -- helpers ----------------------------------------------------------
    def _exclude(self, charges: list[_Charge], key: str, rule: str, reason: str):
        self.excluded.append({
            "merchant": _display_name(charges), "merchant_key": key, "n_charges": len(charges),
            "rule": rule, "reason": reason,
            "first_date": min(c.date for c in charges), "last_date": max(c.date for c in charges),
            "txn_ids": [c.txn_id for c in charges],
        })

    # -- one candidate stream ----------------------------------------------
    def _evaluate(self, stream: list[_Charge], key: str, ctx: dict, extras: int, quiet: bool, fit: CycleFit | None):
        r = self.rules
        n = len(stream)
        if fit is None:
            if not quiet:
                self._exclude(stream, key, "no_cycle",
                              "The gaps between charges don't match a weekly, monthly, quarterly or yearly pattern.")
            return
        cyc = r["cycles"][fit.cycle]
        if n < cyc["min_occurrences"]:
            if not quiet:
                self._exclude(stream, key, "too_few_charges",
                              f"Looks {fit.cycle}, but {n} charges is below the {cyc['min_occurrences']} needed to be sure.")
            return
        amounts = [c.amount for c in stream]
        dates = [c.date for c in stream]
        prices = analyse_prices(amounts, dates, r, CYCLES_PER_YEAR[fit.cycle])
        cat = ctx["category"]

        if fit.cycle == "weekly" and prices["stability"] < r["exclusions"]["weekly_requires_stable_share"]:
            if not quiet:
                self._exclude(stream, key, "weekly_unstable_amounts",
                              "Weekly charges with changing amounts look like habit spending, not a subscription.")
            return
        if (not cat.subscription_like) and prices["stability"] < r["exclusions"]["variable_amount"]["min_stable_share"]:
            if not quiet:
                self._exclude(stream, key, "variable_amounts",
                              "Amounts vary too much for a fixed-price subscription (looks like usage or shopping).")
            return

        score, reason, parts = conf.score(fit.regularity, prices["stability"], n, fit.cycle, r)
        if score < r["confidence"]["min_confidence_to_report"]:
            if not quiet:
                self._exclude(stream, key, "low_confidence", f"Confidence {score:.2f} is below the reporting minimum. {reason}")
            return

        last = stream[-1].date
        cycle_days = fit.cycle_days or (cyc["min_days"] + cyc["max_days"]) / 2
        active = (self.as_of - last).days <= r["confidence"]["active_within_cycles"] * cycle_days
        sid = "sub_" + hashlib.sha1(f"{key}|{ctx['currency']}|{ctx['via_paypal']}|{stream[0].txn_id}".encode()).hexdigest()[:10]
        self.subs.append({
            "id": sid, "merchant": _display_name(stream), "merchant_key": key, "cycle": fit.cycle,
            "cycle_days": round(cycle_days, 1), "n_charges": n,
            "avg_amount": round(sum(amounts) / n, 2), "current_amount": round(prices["level"], 2),
            "currency": ctx["currency"], "first_date": stream[0].date, "last_date": last,
            "next_expected_date": last + timedelta(days=round(cycle_days)), "active": bool(active),
            "confidence": score, "confidence_reason": reason, "regularity": round(fit.regularity, 2),
            "amount_stability": round(prices["stability"], 2), "missed_cycles": fit.missed_cycles,
            "price_changes": prices["changes"], "pending_price_change": prices["pending"],
            "has_price_hike": any(c["kind"] == "increase" for c in prices["changes"]),
            "via_paypal": ctx["via_paypal"], "category": cat.name, "discretionary": cat.discretionary,
            "same_day_extra_charges": extras, "burst_charges_ignored": ctx["bursts"],
            "txn_ids": [c.txn_id for c in stream],
        })

    # -- one (merchant group, currency, channel) unit ------------------------
    def process_unit(self, charges: list[_Charge], key: str, currency: str, via_paypal: bool):
        r = self.rules
        charges = sorted(charges, key=lambda c: (c.ordinal, c.txn_id))
        cat = categorize([c.name for c in charges], r)
        if cat.excluded:
            self._exclude(charges, key, f"category:{cat.name}",
                          f"Matches '{cat.keyword}' ({cat.name.replace('_', ' ')}); these are everyday purchases, not subscriptions."
                          " Edit config/rules.yaml to change this.")
            return

        burst = find_burst_indices([c.ordinal for c in charges], [c.amount for c in charges], r)
        kept = [c for i, c in enumerate(charges) if i not in burst]
        if len(kept) < 2:
            if burst:
                self._exclude(charges, key, "one_off_burst",
                              f"{len(burst)} purchases within {r['exclusions']['burst_window_days']} days with varying amounts: one-off repeat buying.")
            return
        ctx = {"category": cat, "currency": currency, "via_paypal": via_paypal, "bursts": len(burst)}

        for cluster in self._plan_streams(kept):
            streams = _rank_streams(cluster)
            main = streams[0]
            extras = sum(len(s) for s in streams[1:])
            fit = fit_cycle([c.ordinal for c in main], r)
            if fit is None and r["cycles"].get("try_split_interleaved") and len(main) >= 4:
                split = _split_interleaved(main, r)
                if split:
                    fits = [fit_cycle([c.ordinal for c in s], r) for s in split]
                    if all(f and len(s) >= r["cycles"][f.cycle]["min_occurrences"] for f, s in zip(fits, split)):
                        for s, f in zip(split, fits):
                            self._evaluate(s, key, ctx, 0, False, f)
                        continue
            self._evaluate(main, key, ctx, extras, False, fit)
            for s in streams[1:]:      # repeated same-day charges: a second subscription only if it also cycles
                if len(s) >= 2:
                    self._evaluate(s, key, ctx, 0, True, fit_cycle([c.ordinal for c in s], r))

    def _plan_streams(self, charges: list[_Charge]) -> list[list[_Charge]]:
        """Split a merchant into separate plans only when amount tiers form concurrent cycles of their own."""
        r = self.rules
        clusters = _amount_clusters(charges, r["cycles"]["amount_split_ratio"])
        if len(clusters) >= 2:
            fitted = []
            for cl in clusters:
                if len(cl) < 2:
                    continue
                main = _rank_streams(cl)[0]
                f = fit_cycle([c.ordinal for c in main], r)
                if f and len(main) >= r["cycles"][f.cycle]["min_occurrences"]:
                    fitted.append(cl)
            spans = [(cl[0].ordinal, cl[-1].ordinal) for cl in fitted]
            concurrent = any(a[0] <= b[1] and b[0] <= a[1] for i, a in enumerate(spans) for b in spans[i + 1:])
            if len(fitted) >= 2 and concurrent:
                return fitted
        return [sorted(charges, key=lambda c: (c.ordinal, c.txn_id))]


def detect_recurring(transactions: pd.DataFrame, rules: dict | None = None, as_of: date | None = None) -> DetectionResult:
    """Detect recurring subscriptions in a clean transactions table (Phase 1 output)."""
    rules = rules or load_rules()
    if transactions is None or transactions.empty or "amount" not in transactions.columns:
        return DetectionResult(pd.DataFrame(columns=SUBSCRIPTION_COLUMNS), pd.DataFrame(columns=EXCLUDED_COLUMNS),
                               as_of or date.today(), rules)
    tx = transactions[transactions["amount"] < 0]
    if as_of is None:
        as_of = max(transactions["date"]) if len(transactions) else date.today()
    det = _Detector(rules, as_of)
    if len(tx):
        names = tx["merchant_clean"].fillna("").astype(str).tolist()
        group_of = group_merchants(names, rules)
        rows = pd.DataFrame({
            "key": [group_of[n] for n in names], "currency": tx["currency"].to_numpy(),
            "pp": tx["via_paypal"].astype(bool).to_numpy(),
            "c": [_Charge(d.toordinal(), -float(a), i, n, d)
                  for d, a, i, n in zip(tx["date"], tx["amount"], tx["id"], names)],
        })
        for (key, cur, pp), g in rows.groupby(["key", "currency", "pp"], sort=True):
            if len(g) >= 2:
                det.process_unit(list(g["c"]), key, cur, bool(pp))
    subs = pd.DataFrame(det.subs, columns=SUBSCRIPTION_COLUMNS)
    if len(subs):
        subs = subs.sort_values(["confidence", "avg_amount"], ascending=False, kind="stable").reset_index(drop=True)
    excl = pd.DataFrame(det.excluded, columns=EXCLUDED_COLUMNS)
    return DetectionResult(subs, excl, as_of, rules)
