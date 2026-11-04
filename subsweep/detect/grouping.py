"""Rule 1: group merchant names with rapidfuzz token-set similarity."""
from __future__ import annotations

import re
from collections import Counter

from rapidfuzz import fuzz


def normalize_name(name: str, rules: dict) -> str:
    """Lower-case, drop punctuation, apply aliases, drop ignorable tokens (geo/legal noise)."""
    g = rules["grouping"]
    ignore = {t.lower() for t in g.get("ignore_tokens", [])}
    aliases = {k.lower(): v.lower() for k, v in g.get("aliases", {}).items()}
    toks = re.sub(r"[^a-z0-9]+", " ", str(name).lower()).split()
    toks = [aliases.get(t, t) for t in toks]
    kept = [t for t in toks if t not in ignore and not t.isdigit()]
    return " ".join(kept or toks)


def group_merchants(names: list[str], rules: dict) -> dict[str, str]:
    """Map each raw clean-name to a group key (the most frequent normalised form in its group).

    Greedy by frequency: each normalised name joins the first existing group whose
    representative scores >= similarity_threshold on token_set_ratio, else starts a new group.
    Note: token-set ratio scores a subset as 100 ("amazon prime" vs "amazon"); the detector
    later splits such groups into separate streams by amount and cycle.
    """
    threshold = rules["grouping"]["similarity_threshold"]
    norm = {n: normalize_name(n, rules) for n in set(names)}
    counts = Counter(norm[n] for n in names)
    reps: list[str] = []
    assigned: dict[str, str] = {}
    for form, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        match = next((r for r in reps if form == r or fuzz.token_set_ratio(form, r) >= threshold), None)
        if match is None:
            reps.append(form)
            match = form
        assigned[form] = match
    return {n: assigned[norm[n]] for n in norm}
