"""Append-only local audit log of every AI interaction (JSON Lines).

One line per interaction, including ones where no model was called (guardrail refusals, offline templates), so the
file shows exactly what was asked, what data was sent (already redacted), and what came back. Secrets and personal
identifiers are scrubbed again on write. Default path: logs/ai_audit.jsonl (git-ignored); override with SUBSWEEP_AI_LOG.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .redact import redact_obj

DEFAULT_PATH = Path("logs/ai_audit.jsonl")
MAX_BYTES = 5 * 1024 * 1024


class AuditLog:
    def __init__(self, path: str | Path | None = None, max_bytes: int = MAX_BYTES):
        self.path = Path(path or os.environ.get("SUBSWEEP_AI_LOG") or DEFAULT_PATH)
        self.max_bytes = max_bytes
        self._lock = threading.Lock()

    def record(self, **entry) -> dict:
        entry = {"id": uuid.uuid4().hex[:12], "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
        key = os.environ.get("ANTHROPIC_API_KEY")
        if key:        # scrub the exact key FIRST, before generic redaction can chop it into unrecognisable pieces
            entry = json.loads(json.dumps(entry, default=str, ensure_ascii=False).replace(json.dumps(key)[1:-1], "[SECRET]"))
        clean, _ = redact_obj(entry)
        line = json.dumps(clean, default=str, ensure_ascii=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size + len(line) > self.max_bytes:
                self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))     # keep one rotated file
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return clean

    def read(self, limit: int = 100) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(l) for l in lines if l.strip()]
