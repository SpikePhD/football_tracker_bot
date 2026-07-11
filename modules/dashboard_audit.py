"""Rotating, redacted JSON-lines audit history for dashboard mutations."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from modules.storage import BOT_MEMORY_DIR
from utils.redaction import redact_text

AUDIT_PATH = BOT_MEMORY_DIR / "logs" / "dashboard_audit.jsonl"


class AuditLog:
    def __init__(self, path: Path = AUDIT_PATH, max_bytes: int = 512_000, backups: int = 3):
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self._lock = threading.Lock()

    def _rotate(self) -> None:
        if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
            return
        oldest = self.path.with_name(f"{self.path.name}.{self.backups}")
        oldest.unlink(missing_ok=True)
        for number in range(self.backups - 1, 0, -1):
            source = self.path.with_name(f"{self.path.name}.{number}")
            target = self.path.with_name(f"{self.path.name}.{number + 1}")
            if source.exists():
                os.replace(source, target)
        os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))

    def record(self, *, username: str, ip: str, action: str, paths: list[str] | None = None, result: str = "success") -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "administrator": redact_text(username)[:32],
            "ip": redact_text(ip)[:64],
            "action": redact_text(action)[:80],
            "paths": [redact_text(path)[:160] for path in (paths or [])][:100],
            "result": redact_text(result)[:160],
        }
        payload = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

    def recent(self, limit: int = 200) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        entries = []
        for line in reversed(lines):
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries
