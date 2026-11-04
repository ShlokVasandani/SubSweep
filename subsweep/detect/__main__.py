"""CLI: python -m subsweep.detect statement.csv [--rules config/rules.yaml] [--excluded]"""
import argparse
from pathlib import Path

import pandas as pd

from subsweep.ingest import FormatDetectionError, load_statement

from . import detect_recurring, load_rules


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Detect recurring charges in a statement CSV.")
    ap.add_argument("path")
    ap.add_argument("--rules", default=None)
    ap.add_argument("--currency", default="USD")
    ap.add_argument("--excluded", action="store_true", help="also show what was excluded and why")
    a = ap.parse_args(argv)
    try:
        st = load_statement(Path(a.path), default_currency=a.currency)
    except FormatDetectionError as e:
        print(f"Could not read columns: {e}")
        return 2
    res = detect_recurring(st.transactions, load_rules(a.rules))
    cols = ["merchant", "cycle", "n_charges", "avg_amount", "currency", "first_date", "last_date", "confidence"]
    with pd.option_context("display.width", 200, "display.max_columns", 30, "display.max_colwidth", 60):
        print(f"{len(st.transactions)} transactions -> {len(res.subscriptions)} recurring subscriptions\n")
        if len(res.subscriptions):
            print(res.subscriptions[cols].to_string(index=False))
            for _, s in res.subscriptions.iterrows():
                print(f"\n{s.merchant}: {s.confidence_reason}")
                for c in s.price_changes:
                    print(f"  price {c['kind']}: {c['old_amount']} -> {c['new_amount']} ({c['pct']:+.1f}%) from {c['date']}")
        if a.excluded and len(res.excluded):
            print("\nExcluded:")
            print(res.excluded[["merchant", "n_charges", "rule"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
