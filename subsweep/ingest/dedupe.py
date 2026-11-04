"""Deduplication and stable IDs.

Policy (important for duplicate-billing detection):
  * Within one upload, rows are only collapsed when they share the same
    non-empty transaction/reference ID. Identical rows WITHOUT an ID are kept,
    because two genuine same-day charges look exactly alike.
  * When several uploads are merged (e.g. overlapping exports), identical
    charges are matched as a multiset: we keep max(count) per charge, never the
    sum, so an overlap is removed but a true same-day repeat survives.
  * Repeated identical same-day charges are flagged `possible_duplicate`.
"""
from __future__ import annotations

import hashlib

import pandas as pd

KEY_COLS = ["source", "date", "merchant_raw", "amount", "currency"]


def _key_frame(df: pd.DataFrame) -> pd.DataFrame:
    k = df[KEY_COLS].copy()
    k["merchant_raw"] = k["merchant_raw"].str.lower().str.strip()
    k["amount"] = k["amount"].round(2)
    return k


def drop_repeated_refs(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse rows that share a non-empty source_ref within the same source."""
    if df.empty:
        return df
    has_ref = df["source_ref"].fillna("").str.strip() != ""
    dup = has_ref & df.duplicated(subset=["source", "source_ref", "amount", "currency"], keep="first")
    return df[~dup]


def flag_same_day_repeats(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if df.empty:
        df["possible_duplicate"] = pd.Series(dtype=bool)
        return df
    key = _key_frame(df)
    df["possible_duplicate"] = key.duplicated(keep=False).to_numpy()
    return df


def merge_multiset(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Combine frames, removing overlap but keeping genuine repeats (max count)."""
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame()
    parts = []
    for f in frames:
        f = f.copy()
        key = _key_frame(f)
        f["_ord"] = key.groupby(KEY_COLS, sort=False).cumcount().to_numpy()
        parts.append(f)
    allf = pd.concat(parts, ignore_index=True)
    key = _key_frame(allf)
    key["_ord"] = allf["_ord"]
    # Same source_ref across frames is also an overlap.
    has_ref = allf["source_ref"].fillna("").str.strip() != ""
    ref_dup = has_ref & allf.duplicated(subset=["source", "source_ref", "amount", "currency"], keep="first")
    allf = allf[~ref_dup.to_numpy()]
    key = key[~ref_dup.to_numpy()]
    keep = ~key.duplicated(subset=KEY_COLS + ["_ord"], keep="first").to_numpy()
    return allf[keep].drop(columns=["_ord"]).reset_index(drop=True)


def assign_ids(df: pd.DataFrame) -> pd.DataFrame:
    """Deterministic IDs: identical input always yields identical IDs."""
    df = df.copy()
    if df.empty:
        df.insert(0, "id", pd.Series(dtype=str))
        return df
    key = _key_frame(df)
    ordinal = key.groupby(KEY_COLS, sort=False).cumcount().to_numpy()
    ids = []
    for (_, r), o in zip(df.iterrows(), ordinal):
        raw = f"{r['source']}|{r['date']}|{r['merchant_raw']}|{r['amount']:.2f}|{r['currency']}|{r['source_ref']}|{o}"
        ids.append("txn_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12])
    if "id" in df.columns:
        df = df.drop(columns=["id"])
    df.insert(0, "id", ids)
    return df
