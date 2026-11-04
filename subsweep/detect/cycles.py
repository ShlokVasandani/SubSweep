"""Rule 2: classify the gaps between charges as weekly / monthly / quarterly / yearly."""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from .rules import CYCLE_NAMES


@dataclass
class CycleFit:
    cycle: str
    gaps: list[int]
    ks: list[int]          # cycles spanned by each gap (0 = does not fit)
    kinds: list[str]       # on_cycle | shifted | missed | none
    regularity: float      # mean per-gap credit, 0..1
    fit_share: float       # share of gaps that fit at all
    missed_cycles: int
    cycle_days: float      # observed days per cycle (median of gap / k over fitting gaps)
    credits: list[float] = field(default_factory=list)


def gap_fit(gap: int, cfg: dict, rules: dict) -> tuple[int, str, float]:
    """Return (k, kind, credit) for the best interpretation of `gap` under one cycle, or (0,'none',0)."""
    c = rules["cycles"]
    credits = rules["confidence"]["gap_credit"]
    lo, hi = cfg["min_days"], cfg["max_days"]
    nominal, half = (lo + hi) / 2, (hi - lo) / 2
    if lo <= gap <= hi:
        return 1, "on_cycle", credits["on_cycle"]
    s = c["shift_tolerance_days"]
    if lo - s <= gap <= hi + s:
        return 1, "shifted", credits["shifted"]
    if not cfg.get("allow_missed", True):
        return 0, "none", 0.0
    for k in range(2, c["max_missed_cycles"] + 2):
        tol = half + c["extra_days_per_missed_cycle"] * (k - 1)
        if abs(gap - k * nominal) <= tol:
            return k, "missed", max(0.0, credits["on_cycle"] - credits["per_missed_cycle_penalty"] * (k - 1))
    return 0, "none", 0.0


def fit_cycle(ordinals: list[int], rules: dict, allow: tuple[str, ...] = CYCLE_NAMES) -> CycleFit | None:
    """Pick the cycle that best explains the gaps between consecutive (distinct) charge dates."""
    gaps = [b - a for a, b in zip(ordinals, ordinals[1:])]
    if not gaps:
        return None
    best: CycleFit | None = None
    for name in allow:
        cfg = rules["cycles"][name]
        res = [gap_fit(g, cfg, rules) for g in gaps]
        ks = [r[0] for r in res]
        credits = [r[2] for r in res]
        fitted = [(g, k) for g, k in zip(gaps, ks) if k]
        fit = CycleFit(
            cycle=name, gaps=gaps, ks=ks, kinds=[r[1] for r in res],
            regularity=sum(credits) / len(gaps), fit_share=len(fitted) / len(gaps),
            missed_cycles=sum(k - 1 for k in ks if k), credits=credits,
            cycle_days=float(median(g / k for g, k in fitted)) if fitted else 0.0,
        )
        if best is None or fit.regularity > best.regularity:
            best = fit
    if best is None or best.fit_share < rules["cycles"]["min_fit_share"]:
        return None
    return best
