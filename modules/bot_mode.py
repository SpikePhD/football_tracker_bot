# modules/bot_mode.py
# Persistent silent/verbose flag, backed by bot_memory/state.json.
# Silent mode suppresses automatic broadcasts (startup message, morning fixture list)
# but never affects live match updates or command responses.

from modules.storage import load, save

_STATE_FILE = "state.json"
_DEFAULTS = {"silent": False}


def _load() -> dict:
    return load(_STATE_FILE, _DEFAULTS)


def is_silent() -> bool:
    return _load().get("silent", False)


def set_silent(value: bool) -> None:
    state = _load()
    state["silent"] = value
    save(_STATE_FILE, state)
