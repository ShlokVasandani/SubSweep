"""One analysis = upload (or demo) -> optional PayPal data -> detection -> findings. Held in memory per session."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import pandas as pd

from subsweep.ai.analyses import AnalysisContext, spend_by_category
from subsweep.detect import DetectionResult, detect_recurring, load_rules
from subsweep.findings import FindingsReport, generate_findings
from subsweep.ingest import StatementResult
from subsweep.paypal import PayPalClient, PayPalConfig, PayPalError, PayPalView, current_mode, get_paypal_view
from subsweep.paypal.seed import registry_subscription_ids
from subsweep.paypal.sync import match_paypal_subscriptions, merge_sources, source_summary, status_table

from .serialize import finding_json, jsonable, subscription_json


@dataclass
class Analysis:
    id: str
    source: dict
    transactions: pd.DataFrame
    detection: DetectionResult
    report: FindingsReport
    paypal_view: PayPalView | None = None
    paypal_notice: str = ""
    matches: dict = field(default_factory=dict)
    rules: dict = field(default_factory=dict)
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    ai_calls: int = 0

    @property
    def subscriptions(self) -> pd.DataFrame:
        return self.detection.subscriptions

    def refresh(self, dismissals: dict[str, str]) -> None:
        """Recompute findings after the user dismisses or restores one (cheap; detection is unchanged)."""
        self.report = generate_findings(self.subscriptions, self.transactions, self.rules, dismissed=dismissals.keys(),
                                        paypal_status=self.matches)

    def context(self) -> AnalysisContext:
        t = self.transactions
        return AnalysisContext(self.subscriptions, t, self.report, max(t["date"]) if len(t) else date.today())

    # ---------------------------------------------------------------- JSON views
    def paypal_json(self) -> dict:
        v = self.paypal_view
        if v is None:
            return {"mode": "off", "notice": self.paypal_notice, "rows": [], "errors": {}, "sources": source_summary(self.transactions)}
        return jsonable({
            "mode": v.mode, "reason": v.reason, "notice": self.paypal_notice or (v.notes[0] if v.is_mock and v.notes else ""),
            "rows": status_table(self.subscriptions, self.matches), "errors": v.errors, "sources": source_summary(self.transactions),
        })

    def overview_json(self, dismissals: dict[str, str]) -> dict:
        flags: dict[str, list[str]] = {}
        for f in self.report.open():
            for sid in f.subscription_ids:
                flags.setdefault(sid, []).append(f.type)
        pp = {r["subscription_id"]: r for r in status_table(self.subscriptions, self.matches)}
        subs = [subscription_json(s, flags.get(s["id"], []), jsonable(pp.get(s["id"]))) for _, s in self.subscriptions.iterrows()]
        cats = spend_by_category(self.context()).table
        return {
            "id": self.id, "created": self.created, "source": self.source, "summary": jsonable(self.report.summary),
            "findings": [finding_json(f, dismissals) for f in self.report.findings], "subscriptions": subs,
            "excluded": jsonable(self.detection.excluded[["merchant", "n_charges", "rule", "reason"]].to_dict("records"))
            if len(self.detection.excluded) else [],
            "categories": jsonable(cats.to_dict("records")) if len(cats) else [], "paypal": self.paypal_json(),
        }

    def history_json(self, sub_id: str) -> dict | None:
        row = self.subscriptions[self.subscriptions["id"] == sub_id]
        if row.empty:
            return None
        s = row.iloc[0]
        t = self.transactions
        d = t[t["id"].isin(set(s["txn_ids"]))].sort_values("date")
        conf = d["confirmed_by_paypal_api"].fillna(False).astype(bool) if "confirmed_by_paypal_api" in d else pd.Series(False, index=d.index)
        points = [{"date": r["date"].isoformat(), "amount": round(-float(r["amount"]), 2), "source": str(r["source"]),
                   "confirmed_by_paypal": bool(c)} for (_, r), c in zip(d.iterrows(), conf)]
        return {"subscription_id": sub_id, "merchant": s["merchant"], "currency": s["currency"], "points": points,
                "price_changes": jsonable(s["price_changes"])}


# ---------------------------------------------------------------------------------------------- building
def _paypal_for(statement: pd.DataFrame, demo: bool) -> tuple[PayPalView | None, str]:
    """Choose PayPal data: mock if PAYPAL_MODE=mock (or demo with nothing configured), live if configured, else none."""
    start, end = min(statement["date"]), max(statement["date"])
    mode = current_mode()
    if mode == "mock":
        return get_paypal_view(None, start, end), ""
    cfg = PayPalConfig.from_env(require=False)
    if cfg is None:
        if demo:
            return get_paypal_view(None, start, end, environ={"PAYPAL_MODE": "mock"}), ""
        return None, "PayPal isn't connected. Set PAYPAL_MODE=mock for the demo data, or add sandbox credentials to use live status."
    try:
        client = PayPalClient(cfg)
        return get_paypal_view(client, start, end, registry_ids=registry_subscription_ids()), ""
    except PayPalError as exc:
        return None, f"Couldn't reach PayPal: {exc.user_message}"


def build_analysis(analysis_id: str, statement: StatementResult, source: dict, dismissals: dict[str, str], *, demo: bool = False,
                   use_paypal: bool = True) -> Analysis:
    rules = load_rules()
    tx = statement.transactions
    view, notice = (_paypal_for(tx, demo) if use_paypal and len(tx) else (None, ""))
    merged = merge_sources(tx, view.transactions) if view is not None and len(view.transactions) else tx
    det = detect_recurring(merged, rules)
    matches = match_paypal_subscriptions(det.subscriptions, merged, view.live) if view is not None and len(det.subscriptions) else {}
    report = generate_findings(det.subscriptions, merged, rules, dismissed=dismissals.keys(), paypal_status=matches)
    a = Analysis(analysis_id, source, merged, det, report, view, notice, matches, rules)
    return a
