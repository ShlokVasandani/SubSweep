"""CLI for the AI layer.

  python -m subsweep.ai statement.csv --explain
  python -m subsweep.ai statement.csv --draft 1 --ask "What do I spend on streaming?" --show-data
  python -m subsweep.ai mock --explain --ask "Which subscription went up the most this year?"   # PayPal demo data

Without ANTHROPIC_API_KEY (or with SUBSWEEP_AI=off) everything still works using built-in templates, and each result
says whether it came from the model or a template. Drafts are only printed: nothing is ever sent.
"""
import argparse
import json
from datetime import date
from pathlib import Path

from subsweep.detect import detect_recurring, load_rules
from subsweep.findings import generate_findings

from . import AIAssistant, AnalysisContext, build_default_llm


def _load(path: str):
    if path == "mock":
        from subsweep.paypal import get_paypal_view
        from subsweep.paypal.sync import match_paypal_subscriptions

        v = get_paypal_view(None, environ={"PAYPAL_MODE": "mock"})
        subs = detect_recurring(v.transactions).subscriptions
        return v.transactions, subs, match_paypal_subscriptions(subs, v.transactions, v.live)
    from subsweep.ingest import load_statement

    t = load_statement(Path(path)).transactions
    return t, detect_recurring(t).subscriptions, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="statement CSV, or 'mock' for the bundled PayPal demo data")
    ap.add_argument("--explain", action="store_true", help="explain every finding")
    ap.add_argument("--draft", type=int, metavar="N", help="draft a message for the Nth finding (1-based)")
    ap.add_argument("--ask", action="append", default=[], metavar="QUESTION")
    ap.add_argument("--show-data", action="store_true", help="also print the exact data sent to the model")
    ap.add_argument("--today", type=date.fromisoformat, default=None)
    a = ap.parse_args(argv)

    tx, subs, paypal = _load(a.path)
    rep = generate_findings(subs, tx, load_rules(), today=a.today, paypal_status=paypal)
    ctx = AnalysisContext(subs, tx, rep, a.today or (max(tx["date"]) if len(tx) else date.today()))
    ai = AIAssistant(build_default_llm())
    print(f"AI mode: {ai.mode}" + (f" ({ai.llm.model})" if ai.llm else " (built-in templates; set ANTHROPIC_API_KEY for the model)"))
    print(f"{rep.summary['active_subscriptions']} active subscriptions, {len(rep.findings)} findings\n")

    def show(data):
        if a.show_data:
            print("  data used:", json.dumps(data, default=str)[:1500], "\n")

    if a.explain:
        for f in rep.findings:
            e = ai.explain(f)
            print(f"[{f.severity.upper()}] {f.title}  ({e.generated_by})\n  {e.text}\n")
            show(e.data_used)
    if a.draft:
        if not 1 <= a.draft <= len(rep.findings):
            print(f"There is no finding number {a.draft}; there are {len(rep.findings)}.")
        else:
            f = rep.findings[a.draft - 1]
            try:
                d = ai.draft(f)
            except ValueError as exc:
                print(f"Can't draft a message for '{f.title}': {exc}\n")
            else:
                print(f"Draft for '{f.title}' ({d.generated_by}; nothing is sent):\nSubject: {d.subject}\n\n{d.body}\n")
                show(d.data_used)
    for q in a.ask:
        r = ai.ask(q, ctx)
        print(f"Q: {q}\nA: {r.answer}\n   [{r.status}; {r.generated_by}" + (f"; {r.intent} via {r.routed_by}" if r.intent else "") + "]\n")
        show(r.data_used)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
