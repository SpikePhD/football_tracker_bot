# modules/bot_mode.py
# Shared runtime flag for silent/verbose mode.
# Silent mode suppresses automatic broadcasts (startup message, morning fixture list)
# but never affects live match updates or command responses.

_silent = False


def is_silent() -> bool:
    return _silent


def set_silent(value: bool):
    global _silent
    _silent = value
