"""Shared, atomic runtime settings used by Discord commands and the dashboard."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from modules.bot_mode import get_mode, set_mode
from modules.configuration import load_effective_config
from modules.storage import load, save

MORNING_FILE = "goodmorning.json"
def _operations_timezone() -> str:
    return load_effective_config()["operations"]["timezone"]


MORNING_DEFAULTS = {
    "enabled": True,
    "hour": 6,
    "minute": 30,
    "timezone": "Europe/Rome",
}


def get_runtime_settings() -> dict:
    return {"mode": get_mode(), "morning": get_morning_schedule()}


def set_runtime_mode(mode: str) -> dict:
    set_mode(mode)
    return get_runtime_settings()


def get_morning_schedule() -> dict:
    value = load(MORNING_FILE, MORNING_DEFAULTS)
    return {
        "enabled": bool(value.get("enabled", True)),
        "hour": int(value.get("hour", 6)),
        "minute": int(value.get("minute", 30)),
        "timezone": str(value.get("timezone") or _operations_timezone()),
    }


def set_morning_schedule(*, enabled: bool, hour: int, minute: int, timezone: str | None = None) -> dict:
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    if not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23:
        raise ValueError("hour must be between 0 and 23")
    if not isinstance(minute, int) or isinstance(minute, bool) or not 0 <= minute <= 59:
        raise ValueError("minute must be between 0 and 59")
    timezone = timezone or _operations_timezone()
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("timezone must be a valid IANA timezone") from exc
    value = {"enabled": enabled, "hour": hour, "minute": minute, "timezone": timezone}
    save(MORNING_FILE, value)
    return value
