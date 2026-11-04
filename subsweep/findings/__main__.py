"""CLI: python -m subsweep.findings statement.csv [--today YYYY-MM-DD] [--rules path]"""
import argparse
import textwrap
from datetime import date
from pathlib import Path

from subsweep.detect import detect_recurring, load_rules
from subsweep.ingest import FormatDetectionError, load_statement

from . import generate_findings
from .money import money


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Find duplicate, price-hiked, forgotten and soon-renewing subscriptions.")
    ap.add_argument("path")
    ap.add_argument("--rules", default=None)
    ap.add_argument("--currency", default="USD")
    ap.add_argument("--today", type=date.fromisoformat, default=None, help="date for renewal checks (default: statement end)")
    a = ap.parse_args(argv)
    try:
        st = load_statement(Path(a.path), default_currency=a.currency)
    except FormatDetectionError as e:
        print(f"Could not read columns: {e}")
        return 2
    rules = load_rules(a.rules)
    det = detect_recurring(st.transactions, rules)
    rep = generate_findings(det.subscriptions, st.transactions, rules, today=a.today)
    s, cur = rep.summary, rep.summary["currency"]
    if not cur:
        print("No active subscriptions found.")
        return 0
    print(f"Active subscriptions: {s['active_subscriptions']}   "
          f"Monthly: {money(s['monthly_spend'], cur)}   Annual: {money(s['annual_spend'], cur)}   "
          f"Potential savings: {money(s['potential_savings_annual'], cur)}/year")
    h = s["health"]
    print(f"Health score: {h['score']}/100 ({h['label']})\n  {h['formula']}\n")
    for f in rep.findings:
        print(f"[{f.severity.upper()}] {f.type}{' (guess)' if f.is_guess else ''}{' [dismissed]' if f.status != 'open' else ''}: {f.title}")
        print(textwrap.indent(textwrap.fill(f.reason, 100), "    "))
        for p in f.heuristics:
            print(f"      +{p['points']}  {p['text']}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
