#!/usr/bin/env python3
"""Write the reproducible demo statement to sample_data/demo_statement.csv (seed 42 unless --seed is given)."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from subsweep.demo import demo_statement_csv  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="sample_data/demo_statement.csv")
    a = ap.parse_args()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(demo_statement_csv(a.seed), encoding="utf-8")
    print(f"Wrote {out} ({len(out.read_text().splitlines()) - 1} transactions, seed {a.seed})")
