"""Rule 4: confidence score (0-1) with a human-readable reason."""
from __future__ import annotations


def score(regularity: float, stability: float, n: int, cycle: str, rules: dict) -> tuple[float, str, dict]:
    c = rules["confidence"]
    w = c["weights"]
    full = rules["cycles"][cycle]["full_confidence_occurrences"]
    occ = min(1.0, n / full)
    raw = w["regularity"] * regularity + w["stability"] * stability + w["occurrences"] * occ
    cap = None
    for k in sorted(int(x) for x in c.get("max_by_occurrences", {})):
        if n <= k:
            cap = c["max_by_occurrences"][k]
            break
    final = min(raw, cap) if cap is not None else raw
    parts = {"regularity": round(regularity, 2), "stability": round(stability, 2), "occurrences": round(occ, 2)}
    reason = (
        f"{n} {cycle} charges. Timing regularity {regularity:.0%}, amount stability {stability:.0%}, "
        f"history credit {occ:.0%} ({n} charges seen, {full} earns full credit)"
        f" -> {raw:.2f}"
        + (f", capped at {cap:.2f} because only {n} charges were seen" if cap is not None and raw > cap else "")
        + "."
    )
    return round(final, 2), reason, parts
