from datetime import datetime, timedelta, timezone

from config import (
    FOOTBALL_EXPECTED_FT_MINUTES,
    FOOTBALL_FINISHED_RETENTION_HOURS,
    FOOTBALL_MAX_LIVE_DURATION_HOURS,
    FOOTBALL_PREMATCH_WINDOW_HOURS,
    FOOTBALL_STATE_RETENTION_HOURS,
)
from utils.time_utils import parse_provider_utc, to_bot_tz

LIVE_STATUSES = {"1H", "HT", "2H", "ET", "PEN"}
FT_STATUSES = {"FT", "AET", "PEN_DONE"}
TERMINAL_NON_FT_STATUSES = {"PST", "CANC", "ABD", "AWD", "WO"}
TERMINAL_STATUSES = FT_STATUSES | TERMINAL_NON_FT_STATUSES


def _coerce_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        return parse_provider_utc(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def fixture_identity(match: dict) -> str | None:
    fixture_id = match.get("fixture", {}).get("id")
    return str(fixture_id) if fixture_id is not None else None


def fixture_kickoff_utc(match: dict) -> datetime | None:
    date_value = match.get("fixture", {}).get("date")
    return _coerce_utc(date_value)


def local_display_date(match: dict) -> str | None:
    kickoff = fixture_kickoff_utc(match)
    return to_bot_tz(kickoff).date().isoformat() if kickoff else None


def status_short(match: dict | None) -> str | None:
    if not match:
        return None
    status = match.get("fixture", {}).get("status", {})
    short = status.get("short")
    if short == "PEN":
        status_text = " ".join(
            str(status.get(key) or "")
            for key in ("long", "detail", "description", "name")
        ).lower()
        if ("finished" in status_text or "final" in status_text) and "progress" not in status_text:
            return "PEN_DONE"
    return short


def is_live(match: dict | None) -> bool:
    return status_short(match) in LIVE_STATUSES


def is_ft(match: dict | None) -> bool:
    return status_short(match) in FT_STATUSES


def is_terminal(match: dict | None) -> bool:
    return status_short(match) in TERMINAL_STATUSES


def expected_ft_check_utc(match: dict) -> datetime | None:
    kickoff = fixture_kickoff_utc(match)
    if kickoff is None:
        return None
    return kickoff + timedelta(minutes=FOOTBALL_EXPECTED_FT_MINUTES)


def is_recently_finished(match: dict, now_utc: datetime) -> bool:
    if not is_terminal(match):
        return False
    kickoff = fixture_kickoff_utc(match)
    if kickoff is None:
        return False
    now_utc = _coerce_utc(now_utc) or now_utc
    return now_utc - kickoff <= timedelta(
        hours=FOOTBALL_MAX_LIVE_DURATION_HOURS + FOOTBALL_FINISHED_RETENTION_HOURS
    )


def should_track_fixture(match: dict, now_utc: datetime) -> bool:
    kickoff = fixture_kickoff_utc(match)
    if kickoff is None:
        return is_live(match)

    now_utc = _coerce_utc(now_utc) or now_utc
    if is_live(match):
        return now_utc - kickoff <= timedelta(hours=FOOTBALL_MAX_LIVE_DURATION_HOURS)
    if is_recently_finished(match, now_utc):
        return True
    if is_terminal(match):
        return False

    return (
        now_utc - timedelta(hours=FOOTBALL_PREMATCH_WINDOW_HOURS)
        <= kickoff
        <= now_utc + timedelta(hours=FOOTBALL_PREMATCH_WINDOW_HOURS)
    )


def provider_window(now_utc: datetime) -> tuple[datetime, datetime]:
    now_utc = _coerce_utc(now_utc) or now_utc
    return (
        now_utc - timedelta(hours=FOOTBALL_MATCH_LOOKBACK_HOURS()),
        now_utc + timedelta(hours=FOOTBALL_PREMATCH_WINDOW_HOURS),
    )


def FOOTBALL_MATCH_LOOKBACK_HOURS() -> int:
    return max(FOOTBALL_STATE_RETENTION_HOURS, FOOTBALL_MAX_LIVE_DURATION_HOURS + FOOTBALL_FINISHED_RETENTION_HOURS)


def state_is_prunable(fixture_state: dict, now_utc: datetime) -> bool:
    now_utc = _coerce_utc(now_utc) or now_utc
    status = fixture_state.get("last_status")
    kickoff = _coerce_utc(fixture_state.get("kickoff_utc"))
    last_seen = _coerce_utc(fixture_state.get("last_seen_utc"))
    terminal = _coerce_utc(fixture_state.get("terminal_utc"))

    if status in LIVE_STATUSES:
        if kickoff and now_utc - kickoff <= timedelta(hours=FOOTBALL_MAX_LIVE_DURATION_HOURS):
            return False
        if last_seen and now_utc - last_seen <= timedelta(hours=FOOTBALL_MAX_LIVE_DURATION_HOURS):
            return False

    if status in TERMINAL_STATUSES:
        terminal_ref = terminal or last_seen or kickoff
        if terminal_ref is None:
            return False
        return now_utc - terminal_ref > timedelta(hours=FOOTBALL_FINISHED_RETENTION_HOURS)

    ref = last_seen or kickoff
    if ref is None:
        return False
    return now_utc - ref > timedelta(hours=FOOTBALL_STATE_RETENTION_HOURS)
