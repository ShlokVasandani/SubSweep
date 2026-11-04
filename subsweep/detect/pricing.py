"""Rule 3: amount stability and sustained price changes."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median


@dataclass
class Run:
    start: int
    end: int       # inclusive index
    level: float

    @property
    def length(self) -> int:
        return self.end - self.start + 1


def segment_runs(amounts: list[float], tol_pct: float) -> list[Run]:
    """Split a chronological amount series into runs of near-constant price.

    A charge stays in the current run while it is strictly less than tol_pct away from the
    run's median; otherwise it opens a new run.
    """
    tol = tol_pct / 100.0
    runs: list[list[int]] = []
    for i, a in enumerate(amounts):
        if runs:
            level = median(amounts[j] for j in runs[-1])
            if level > 0 and abs(a - level) / level < tol:
                runs[-1].append(i)
                continue
        runs.append([i])
    return [Run(r[0], r[-1], float(median(amounts[j] for j in r))) for r in runs]


def analyse_prices(amounts: list[float], dates: list, rules: dict, cycles_per_year: float | None = None) -> dict:
    """Return stability share, current level, confirmed price changes, and any pending change."""
    a_cfg = rules["amounts"]
    tol, min_pct, min_cycles = a_cfg["stable_tolerance_pct"], a_cfg["price_change_min_pct"], a_cfg["price_change_min_cycles"]
    runs = segment_runs(amounts, tol)
    confirmed = [r for r in runs if r.length >= min_cycles]
    stable_charges = sum(r.length for r in confirmed)
    if not confirmed:  # every charge differs: fall back to the overall median, count none as stable
        return {"stability": 0.0, "level": float(median(amounts)), "changes": [], "pending": None, "runs": runs}

    changes = []
    for prev, cur in zip(confirmed, confirmed[1:]):
        pct = (cur.level - prev.level) / prev.level * 100.0
        kind = None
        if pct >= min_pct - 1e-9:
            kind = "increase"
        elif pct <= -min_pct + 1e-9 and a_cfg.get("report_decreases", True):
            kind = "decrease"
        if kind:
            ch = {
                "kind": kind,
                "date": dates[cur.start],
                "old_amount": round(prev.level, 2),
                "new_amount": round(cur.level, 2),
                "pct": round(pct, 1),
                "charges_at_new_price": cur.length,
            }
            if cycles_per_year:
                ch["annual_impact"] = round((cur.level - prev.level) * cycles_per_year, 2)
            changes.append(ch)

    pending = None
    last = runs[-1]
    if last.length < min_cycles and last.start > confirmed[-1].end:
        pct = (last.level - confirmed[-1].level) / confirmed[-1].level * 100.0
        if abs(pct) >= min_pct - 1e-9:
            pending = {"date": dates[last.start], "old_amount": round(confirmed[-1].level, 2),
                       "new_amount": round(last.level, 2), "pct": round(pct, 1)}
    return {
        "stability": stable_charges / len(amounts),
        "level": confirmed[-1].level,
        "changes": changes,
        "pending": pending,
        "runs": runs,
    }
