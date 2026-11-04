"""CLI: python -m subsweep.ingest statement.csv [--dayfirst|--monthfirst] [--currency INR]"""
import argparse
import sys

import pandas as pd

from . import FormatDetectionError, load_statement


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Parse and clean a statement CSV.")
    ap.add_argument("path")
    ap.add_argument("--currency", default="USD", help="default currency when the file has none")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dayfirst", action="store_true", default=None)
    g.add_argument("--monthfirst", dest="dayfirst", action="store_false")
    ap.add_argument("--rows", type=int, default=25)
    a = ap.parse_args(argv)
    try:
        res = load_statement(__import__("pathlib").Path(a.path), default_currency=a.currency, dayfirst=a.dayfirst)
    except FormatDetectionError as e:
        print(f"Could not detect columns: {e}\nheaders: {e.headers}\nbest guesses: {e.suggestions}", file=sys.stderr)
        return 2
    print(res.summary())
    with pd.option_context("display.width", 200, "display.max_columns", 20, "display.max_colwidth", 38):
        print(res.transactions.drop(columns=["source", "source_ref"]).head(a.rows).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
