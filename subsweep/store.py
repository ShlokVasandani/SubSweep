"""Tiny SQLite store (data/subsweep.db). It holds only the user's "dismissed / still using" decisions, keyed by finding id.

Statements and transactions are never written to disk: analyses live in memory and vanish on restart. Finding ids are
deterministic from the subscription's content, so a decision survives re-uploading the same statement.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path("data/subsweep.db")
_SCHEMA = "CREATE TABLE IF NOT EXISTS dismissals (finding_id TEXT PRIMARY KEY, reason TEXT NOT NULL, at TEXT NOT NULL)"


class Store:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("SUBSWEEP_DB") or DEFAULT_DB)
        self._lock = threading.Lock()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def dismiss(self, finding_id: str, reason: str = "dismissed") -> None:
        if reason not in ("dismissed", "still_using"):
            raise ValueError("reason must be 'dismissed' or 'still_using'")
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO dismissals VALUES (?, ?, ?)",
                               (finding_id, reason, datetime.now(timezone.utc).isoformat(timespec="seconds")))
            self._conn.commit()

    def restore(self, finding_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM dismissals WHERE finding_id = ?", (finding_id,))
            self._conn.commit()

    def dismissals(self) -> dict[str, str]:
        with self._lock:
            return dict(self._conn.execute("SELECT finding_id, reason FROM dismissals").fetchall())

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM dismissals")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()
