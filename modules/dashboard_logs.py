"""Bounded, redacted application-log reads for the dashboard."""

from __future__ import annotations

import re
from pathlib import Path

from utils.redaction import redact_text

LEVEL_RE = re.compile(r"\[(WARNING|ERROR|CRITICAL)\s*\]")
MODULE_RE = re.compile(r"^[A-Za-z0-9_.-]{2,80}$")


def read_logs(path: Path, *, mode: str = "recent", module: str | None = None, limit: int = 300) -> dict:
    limit = max(1, min(int(limit), 1000))
    if mode not in {"recent", "errors", "module"}:
        raise ValueError("Unsupported log mode.")
    if mode == "module" and (not module or not MODULE_RE.fullmatch(module)):
        raise ValueError("Invalid module filter.")
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return {"lines": [], "missing": True}
    if mode == "errors":
        lines = [line for line in lines if LEVEL_RE.search(line)]
    elif mode == "module":
        lines = [line for line in lines if f"[{module}" in line]
    return {"lines": [redact_text(line) for line in lines[-limit:]], "missing": False}
