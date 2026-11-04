"""Load detection rules from config/rules.yaml (with optional overrides for tests)."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "rules.yaml"
CYCLE_NAMES = ("weekly", "monthly", "quarterly", "yearly")


def _deep_merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def load_rules(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    with open(path or DEFAULT_RULES_PATH, "r", encoding="utf-8") as fh:
        rules = yaml.safe_load(fh)
    for section in ("grouping", "cycles", "amounts", "confidence", "exclusions", "categories", "findings"):
        if section not in rules:
            raise ValueError(f"rules.yaml is missing the '{section}' section")
    for c in CYCLE_NAMES:
        if c not in rules["cycles"]:
            raise ValueError(f"rules.yaml cycles is missing '{c}'")
    if overrides:
        rules = _deep_merge(copy.deepcopy(rules), overrides)
    return rules
