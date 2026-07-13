"""Atomic cross-process bot health snapshot."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from modules.storage import BOT_MEMORY_DIR, save_json_path

HEALTH_PATH = BOT_MEMORY_DIR / "dashboard_health.json"


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def write_bot_health(
    *,
    commit: dict,
    provider: dict,
    tennis_provider: dict,
    football_scheduler: dict,
    tennis_scheduler: dict,
    mode: str,
) -> None:
    save_json_path(HEALTH_PATH, _json_safe({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "commit": commit,
        "provider": provider,
        "tennis_provider": tennis_provider,
        "football_scheduler": football_scheduler,
        "tennis_scheduler": tennis_scheduler,
        "mode": mode,
    }), ensure_ascii=False)


def read_bot_health(path: Path = HEALTH_PATH, stale_after_seconds: int = 120) -> dict:
    try:
        import json
        value = json.loads(path.read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(value["timestamp"].replace("Z", "+00:00"))
        age = max(0, (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds())
        value["age_seconds"] = round(age)
        value["stale"] = age > stale_after_seconds
        return value
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return {"timestamp": None, "age_seconds": None, "stale": True, "available": False}
