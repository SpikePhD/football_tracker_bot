# modules/bot_mode.py
# Three-mode broadcast system, persisted in bot_memory/state.json.
#
# verbose — everything: startup message, morning broadcast, live updates, FT results, commands
# normal  — match updates only: live events, FT results, commands (no startup/morning broadcasts)
# silent  — commands only: bot never posts automatically

from modules.storage import load, save

_STATE_FILE = "state.json"
_DEFAULTS = {"mode": "verbose"}
_VALID_MODES = ("verbose", "normal", "silent")


def get_mode() -> str:
    return load(_STATE_FILE, _DEFAULTS).get("mode", "verbose")


def set_mode(mode: str) -> None:
    if mode not in _VALID_MODES:
        raise ValueError(f"Invalid mode: {mode!r}. Must be one of {_VALID_MODES}.")
    state = load(_STATE_FILE, _DEFAULTS)
    state["mode"] = mode
    save(_STATE_FILE, state)


def is_verbose() -> bool:
    return get_mode() == "verbose"


def is_silent() -> bool:
    return get_mode() == "silent"
