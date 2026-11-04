"""AIAssistant: finding explainer, message drafter and spending Q&A, with grounding, guardrails and an audit trail.

Every feature follows the same pipeline:  facts computed in Python/pandas  ->  minimal payload  ->  redaction  ->
model (optional)  ->  grounding + safety checks  ->  deterministic template if anything fails.  Every interaction is
appended to the audit log, and every result carries `data_used`: exactly what was sent to the model (after redaction).
Nothing here sends a message to anyone: drafts are returned as text for the person to send themselves.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from subsweep.findings import Finding
from subsweep.findings.money import fdate

from . import grounding, guardrails, prompts
from .analyses import AnalysisContext, clean_params, route_rules, run, table_records
from .audit import AuditLog
from .facts import (
    ACCOUNT_PLACEHOLDER, DRAFTABLE, NAME_PLACEHOLDER, ORDER_PLACEHOLDER, draft_purpose, finding_facts,
    template_draft, template_explanation,
)
from .llm import LLM, LLMRefused, LLMUnavailable
from .redact import redact_obj, redact_text

_MD = re.compile(r"(\*\*|__|^#+\s|^\s*[-*]\s)", re.M)
_SENT_CLAIM = re.compile(r"\b(?:has|have) (?:been|already) sent\b|\bi(?:'ve| have) (?:sent|emailed|contacted)\b", re.I)


@dataclass
class AIText:
    text: str
    generated_by: str                      # "model" or "template"
    status: str                            # ok | template | grounding_violation | llm_unavailable | refused | blocked
    data_used: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    model: str | None = None
    audit_id: str | None = None


@dataclass
class Draft:
    subject: str
    body: str
    generated_by: str
    status: str
    data_used: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    model: str | None = None
    audit_id: str | None = None
    sent: bool = False                     # always False: SubSweep never sends anything


@dataclass
class QAAnswer:
    question: str
    answer: str
    status: str                            # answered | no_data | refused | unsupported
    intent: str | None = None
    routed_by: str | None = None           # rules | model | None
    generated_by: str = "none"             # model | template | none
    columns: list[str] = field(default_factory=list)
    table: list[dict] = field(default_factory=list)
    data_used: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    model: str | None = None
    audit_id: str | None = None


def _dump(payload) -> str:
    return json.dumps(payload, indent=2, default=str, ensure_ascii=False)


class AIAssistant:
    def __init__(self, llm: LLM | None = None, audit: AuditLog | None = None, *, cache: bool = True):
        self.llm = llm
        self.audit = audit or AuditLog()
        self._cache: dict[str, object] | None = {} if cache else None
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        return "model" if self.llm is not None else "offline"

    def clear_cache(self) -> None:
        with self._lock:
            if self._cache is not None:
                self._cache.clear()

    # ------------------------------------------------------------------ plumbing
    def _key(self, feature: str, payload) -> str:
        raw = feature + "|" + (getattr(self.llm, "model", "") or "offline") + "|" + json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cached(self, key: str):
        if self._cache is None:
            return None
        with self._lock:
            return self._cache.get(key)

    def _store(self, key: str, value, status: str) -> None:
        if self._cache is not None and status != "llm_unavailable":
            with self._lock:
                self._cache[key] = value

    def _log(self, feature: str, status: str, **extra) -> str:
        return self.audit.record(feature=feature, status=status, mode=self.mode, model=getattr(self.llm, "model", None), **extra)["id"]

    def _call(self, feature: str, system: str, label: str, payload: dict, max_tokens: int, check_with: dict | None = None,
              validate=None, audit_extra: dict | None = None):
        """Redact -> model -> validate. Returns (text|None, status, notes, audit_id, redacted_payload).

        `check_with` is what output figures are verified against (defaults to the payload itself).
        """
        clean, n_red = redact_obj(payload)
        user = f"{label}:\n{_dump(clean)}"
        extra = dict(audit_extra or {})
        base = {"request": {"system": system, "user": user}, "redactions": n_red, **extra}
        if self.llm is None:
            aid = self._log(feature, "offline_template", note="No model configured; used the built-in template.", **base)
            return None, "template", ["AI is off (no ANTHROPIC_API_KEY), so this is the built-in explanation."], aid, clean
        try:
            res = self.llm.complete(system=system, user=user, max_tokens=max_tokens, feature=feature)
        except LLMUnavailable as exc:
            aid = self._log(feature, "llm_unavailable", note=exc.user_message, kind=exc.kind, **base)
            return None, "llm_unavailable", [exc.user_message], aid, clean
        except LLMRefused as exc:
            aid = self._log(feature, "refused", note=f"model declined ({exc.category})", **base)
            return None, "refused", ["The AI declined this one, so the built-in text is shown instead."], aid, clean
        except Exception as exc:                                    # never let a model problem break the app
            aid = self._log(feature, "llm_error", note=type(exc).__name__, **base)
            return None, "llm_unavailable", ["The AI had a problem, so the built-in text is shown instead."], aid, clean
        text = _MD.sub("", res.text).strip()
        g = grounding.check(text, check_with if check_with is not None else clean)
        problems, notes, status = [], [], "ok"
        if not text:
            problems.append("empty reply")
        if not g.ok:
            problems.append("figures not in the data: " + ", ".join(g.problems))
        if guardrails.output_is_advice(text):
            problems.append("investment advice")
        if validate and not problems:
            problems += validate(text)
        meta = {"response": res.text, "usage": res.usage, "request_id": res.request_id, "latency_ms": res.latency_ms,
                "model_used": res.model, "grounding": {"ok": g.ok, "problems": g.problems}}
        if problems:
            aid = self._log(feature, "rejected_output", note="; ".join(problems), **base, **meta)
            return None, "grounding_violation", ["The AI's wording didn't match your data exactly, so the built-in text is shown instead."], aid, clean
        aid = self._log(feature, "ok", **base, **meta)
        return text, status, notes, aid, clean

    # ------------------------------------------------------------------ 1. finding explainer
    def explain(self, finding: Finding) -> AIText:
        facts = finding_facts(finding)
        key = self._key("explain", facts)
        if (hit := self._cached(key)) is not None:
            return hit
        text, status, notes, aid, clean = self._call("explain", prompts.EXPLAIN_SYSTEM, "FINDING", facts, 1500,
                                                     audit_extra={"finding_type": finding.type})
        out = (AIText(text, "model", "ok", clean, notes, getattr(self.llm, "model", None), aid) if text
               else AIText(template_explanation(finding), "template", "template" if status == "template" else status, clean, notes, None, aid))
        self._store(key, out, out.status)
        return out

    def explain_all(self, findings: list[Finding], workers: int = 4) -> dict[str, AIText]:
        if self.llm is None or len(findings) < 2:
            return {f.id: self.explain(f) for f in findings}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            return dict(zip((f.id for f in findings), pool.map(self.explain, findings)))

    # ------------------------------------------------------------------ 2. message drafter (never sends)
    def draft(self, finding: Finding) -> Draft:
        if finding.type not in DRAFTABLE:
            raise ValueError("Drafts are available for duplicate, price-increase and possibly-forgotten findings.")
        facts = finding_facts(finding)
        payload = {"finding": facts, "purpose": draft_purpose(finding),
                   "placeholders_to_include": [ACCOUNT_PLACEHOLDER, ORDER_PLACEHOLDER, NAME_PLACEHOLDER]}
        key = self._key("draft", payload)
        if (hit := self._cached(key)) is not None:
            return hit

        def validate(text: str) -> list[str]:
            probs = []
            if _parse_draft(text) is None:
                probs.append("missing 'Subject:' line")
            if len(text.split()) > 220:
                probs.append("too long")
            if _SENT_CLAIM.search(text):
                probs.append("claims it was sent")
            return probs

        text, status, notes, aid, clean = self._call("draft", prompts.DRAFT_SYSTEM, "FINDING AND PURPOSE", payload, 2000,
                                                     validate=validate, audit_extra={"finding_type": finding.type})
        if text:
            subject, body = _parse_draft(text)                      # validated above
            body, repaired = _ensure_placeholders(body)
            if repaired:
                notes = notes + ["Placeholders were added for you to fill in before sending."]
            out = Draft(subject, body, "model", "ok", clean, notes, getattr(self.llm, "model", None), aid)
        else:
            subject, body = template_draft(finding)
            out = Draft(subject, body, "template", "template" if status == "template" else status, clean, notes, None, aid)
        self._store(key, out, out.status)
        return out

    # ------------------------------------------------------------------ 3. spending Q&A
    def _route_with_llm(self, question: str, ctx: AnalysisContext):
        payload = {"question": question, "known_merchants": ctx.merchant_names()[:30]}
        clean, n = redact_obj(payload)
        user = f"QUESTION:\n{_dump(clean)}"
        base = {"request": {"system": prompts.ROUTER_SYSTEM, "user": user}, "redactions": n}
        try:
            res = self.llm.complete(system=prompts.ROUTER_SYSTEM, user=user, max_tokens=800, feature="qa_route")
        except (LLMUnavailable, LLMRefused) as exc:
            self._log("qa_route", "llm_unavailable", note=getattr(exc, "user_message", "declined"), **base)
            return None
        except Exception as exc:
            self._log("qa_route", "llm_error", note=type(exc).__name__, **base)
            return None
        parsed = None
        m = re.search(r"\{.*\}", res.text, re.S)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except ValueError:
                parsed = None
        intent = parsed.get("intent") if isinstance(parsed, dict) else None
        params = None
        if intent in ("unknown", "out_of_scope"):
            params = {}
        elif intent:
            params = clean_params(intent, parsed.get("params") if isinstance(parsed.get("params"), dict) else {})
        status = "ok" if params is not None else "rejected_output"
        self._log("qa_route", status, response=res.text, usage=res.usage, latency_ms=res.latency_ms, parsed={"intent": intent, "params": params}, **base)
        return (intent, params) if params is not None else None

    def ask(self, question: str, ctx: AnalysisContext) -> QAAnswer:
        q_clean, _ = redact_text((question or "").strip())
        blocked, msg = guardrails.pre_check(question)
        if blocked:
            aid = self._log("qa", f"blocked:{blocked}", request={"question": q_clean}, response=msg)
            return QAAnswer(q_clean, msg, "refused", notes=[f"blocked: {blocked}"], audit_id=aid)

        route, routed_by = route_rules(question, ctx), "rules"
        if route is None and self.llm is not None and guardrails.looks_financial(question):
            route, routed_by = self._route_with_llm(q_clean, ctx), "model"       # unrelated questions never cost a model call
        if route is None or route[0] in ("unknown", "out_of_scope"):
            off_topic = (route is not None and route[0] == "out_of_scope") or (route is None and not guardrails.looks_financial(question))
            msg = guardrails.OFF_TOPIC_MSG if off_topic else guardrails.UNKNOWN_MSG
            aid = self._log("qa", "refused:off_topic" if off_topic else "unsupported", request={"question": q_clean}, response=msg)
            return QAAnswer(q_clean, msg, "refused" if off_topic else "unsupported", routed_by=routed_by if route else None, audit_id=aid)

        intent, params = route
        result = run(intent, ctx, params)
        if result.empty:
            msg = f"I can't find anything in your data for that ({result.title.lower()}), so I'd rather not guess."
            aid = self._log("qa", "no_data", request={"question": q_clean}, intent=intent, params=params, response=msg)
            return QAAnswer(q_clean, msg, "no_data", intent, routed_by, "none", audit_id=aid)

        rows = table_records(result.table, 25)
        payload = {"question": q_clean, "analysis": {"title": result.title, "as_of": fdate(ctx.today), "columns": list(result.table.columns),
                                                     "rows": rows, "rows_shown": len(rows), "rows_total": len(result.table)}}
        key = self._key("qa", payload)
        if (hit := self._cached(key)) is not None:
            return hit
        text, status, notes, aid, clean = self._call("qa", prompts.ANSWER_SYSTEM, "QUESTION AND DATA", payload, 1500,
                                                     audit_extra={"intent": intent, "params": params})
        full = table_records(result.table, 100)
        ans = QAAnswer(q_clean, text or result.text, "answered", intent, routed_by, "model" if text else "template",
                       list(result.table.columns), full, clean, notes, getattr(self.llm, "model", None) if text else None, aid)
        self._store(key, ans, status)
        return ans


# ---------------------------------------------------------------------------------------------- draft helpers
def _parse_draft(text: str) -> tuple[str, str] | None:
    lines = text.strip().splitlines()
    if not lines or not lines[0].lower().startswith("subject:"):
        return None
    subject, body = lines[0].split(":", 1)[1].strip(), "\n".join(lines[1:]).strip()
    return (subject, body) if subject and body else None


def _ensure_placeholders(body: str) -> tuple[str, bool]:
    repaired = False
    if ACCOUNT_PLACEHOLDER not in body or ORDER_PLACEHOLDER not in body:
        body = f"Account: {ACCOUNT_PLACEHOLDER}\nOrder / transaction details: {ORDER_PLACEHOLDER}\n\n" + body
        repaired = True
    if NAME_PLACEHOLDER not in body:
        body = body.rstrip() + f"\n\nKind regards,\n{NAME_PLACEHOLDER}"
        repaired = True
    return body, repaired
