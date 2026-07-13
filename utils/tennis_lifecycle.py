import re
from datetime import datetime, timedelta, timezone

from config import TENNIS_FINISHED_RETENTION_HOURS
from utils.time_utils import parse_provider_utc


def _tennis_start_utc(match: dict) -> datetime | None:
    start_time = match.get("start_time")
    if not start_time:
        return None
    try:
        if isinstance(start_time, datetime):
            parsed = start_time
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        return parse_provider_utc(str(start_time))
    except (TypeError, ValueError):
        return None


def tennis_final_within_retention(match: dict, now_utc: datetime) -> bool:
    """Return true when an FT match is recent enough for polling/announcement."""
    if match.get("status", {}).get("short") != "FT":
        return False

    start = _tennis_start_utc(match)
    if start is None:
        return False

    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    age = now_utc - start
    return timedelta(0) <= age <= timedelta(hours=TENNIS_FINISHED_RETENTION_HOURS)


def tennis_final_result_reason(match: dict) -> str | None:
    """Return a normalized exceptional terminal reason when ESPN supplies one."""
    status = match.get("status", {}) or {}
    result_text = " ".join(
        str(value)
        for value in (
            status.get("name"),
            status.get("detail"),
            status.get("description"),
            status.get("short_detail"),
        )
        if value
    ).lower()
    result_text = re.sub(r"[_-]+", " ", result_text)

    if re.search(r"\bwalk[\s-]?over\b|\bw/o\b", result_text):
        return "Walkover"
    if re.search(r"\bret(?:ired|irement)?\.?\b", result_text):
        return "Retirement"
    return None


def _is_completed_tennis_set(a, b) -> bool:
    try:
        a_score = int(a)
        b_score = int(b)
    except (TypeError, ValueError):
        return False

    high = max(a_score, b_score)
    low = min(a_score, b_score)

    if high == 6 and low <= 4:
        return True
    if high == 7 and low in (5, 6):
        return True
    if high >= 10 and high - low >= 2:
        return True
    return False


def tennis_final_data_ready(match: dict) -> bool:
    """Return true when an FT tennis payload is complete enough to announce."""
    if match.get("status", {}).get("short") != "FT":
        return False
    if not match.get("winner"):
        return False

    if tennis_final_result_reason(match):
        return True

    sets = match.get("sets") or []
    if not sets:
        return False

    return all(_is_completed_tennis_set(s.get("a"), s.get("b")) for s in sets)


def tennis_record_preference(match: dict) -> tuple:
    """Return the shared preference rank used to reconcile duplicate ESPN records.

    A complete final is authoritative. A live record deliberately outranks an
    incomplete final so a transient ESPN status flicker cannot erase live state.
    """
    status = match.get("status") or {}
    lifecycle = status.get("short")
    if lifecycle == "FT" and tennis_final_data_ready(match):
        lifecycle_rank = 4
    elif lifecycle == "LIVE":
        lifecycle_rank = 3
    elif lifecycle == "FT":
        lifecycle_rank = 2
    elif lifecycle == "NS":
        lifecycle_rank = 1
    else:
        lifecycle_rank = 0

    sets = match.get("sets") or []
    populated_sets = sum(
        1 for item in sets
        if isinstance(item, dict) and (item.get("a") is not None or item.get("b") is not None)
    )
    return (
        lifecycle_rank,
        1 if match.get("winner") else 0,
        populated_sets,
        len(sets),
        1 if status.get("detail") or status.get("short_detail") else 0,
        1 if match.get("round") else 0,
        1 if match.get("event_name") else 0,
    )
