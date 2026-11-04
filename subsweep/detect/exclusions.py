"""Rule 5: false-positive exclusions driven by config/rules.yaml."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


@dataclass
class Category:
    name: str | None
    excluded: bool = False
    subscription_like: bool = False
    discretionary: bool = False
    keyword: str | None = None
    protected_by: str | None = None  # never_exclude phrase that saved it


def _phrase_re(phrase: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(phrase.lower()) + r"(?![a-z0-9])")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def categorize(names: list[str], rules: dict) -> Category:
    """Match merchant names against category keyword lists (whole words / phrases)."""
    text = " | ".join(_norm(n) for n in names)
    protected = next((p for p in rules["exclusions"].get("never_exclude", []) if _phrase_re(_norm(p)).search(text)), None)
    for name, cfg in rules["categories"].items():
        for kw in cfg.get("keywords", []):
            if _phrase_re(_norm(kw)).search(text):
                excluded = bool(cfg.get("excluded")) and protected is None
                return Category(
                    name=name, excluded=excluded,
                    subscription_like=bool(cfg.get("subscription_like")) or (protected is not None),
                    discretionary=bool(cfg.get("discretionary")), keyword=kw, protected_by=protected,
                )
    return Category(name=None, subscription_like=protected is not None, protected_by=protected)


def find_burst_indices(ordinals: list[int], amounts: list[float], rules: dict) -> set[int]:
    """Indices of charges inside a burst: >= burst_min_charges within burst_window_days with varying amounts."""
    ex = rules["exclusions"]
    window, min_n = ex["burst_window_days"], ex["burst_min_charges"]
    tol = rules["amounts"]["stable_tolerance_pct"] / 100.0
    marked: set[int] = set()
    n = len(ordinals)
    for i in range(n):
        j = i
        while j + 1 < n and ordinals[j + 1] - ordinals[i] <= window:
            j += 1
        idx = list(range(i, j + 1))
        if len(idx) >= min_n:
            vals = [amounts[k] for k in idx]
            lo, hi = min(vals), max(vals)
            if lo > 0 and (hi - lo) / lo >= tol:   # varying amounts => habit/one-off spend
                marked.update(idx)
    return marked
