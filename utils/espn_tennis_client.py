# utils/espn_tennis_client.py
import asyncio
import logging
import time
from dataclasses import dataclass

import aiohttp

from config import TRACKED_TENNIS_PLAYERS

logger = logging.getLogger(__name__)

ESPN_TENNIS_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis"
ESPN_TENNIS_TOURS = ("atp", "wta")
ESPN_TIMEOUT = aiohttp.ClientTimeout(total=10)
_WARNING_INTERVAL_SEC = 30 * 60
_last_warning_at: dict[tuple[str, str, str], float] = {}


@dataclass(frozen=True)
class TennisSourceResult:
    tour: str
    date_str: str | None
    matches: tuple[dict, ...]
    ok: bool
    error_kind: str | None = None
    http_status: int | None = None


def _source_label(tour: str, date_str: str | None) -> str:
    return f"{tour}:{date_str or 'default'}"


def _warn_source_failure(
    tour: str,
    date_str: str | None,
    error_kind: str,
    message: str,
) -> None:
    key = (tour, date_str or "default", error_kind)
    now = time.monotonic()
    last = _last_warning_at.get(key)
    if last is None or now - last >= _WARNING_INTERVAL_SEC:
        logger.warning("espn_tennis_client: %s for %s", message, _source_label(tour, date_str))
        _last_warning_at[key] = now


def _normalize_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _is_tracked_player(name: str) -> bool:
    normalized = _normalize_name(name)
    return normalized in TRACKED_TENNIS_PLAYERS


def _map_status_short(status_type: dict, competitors: list[dict]) -> str:
    """
    Map ESPN tennis status to simplified NS/LIVE/FT with extra heuristics.
    ESPN tennis can be inconsistent across feeds, so we also inspect
    name/detail/completed/winner signals.
    """
    state = (status_type or {}).get("state", "pre")
    name = ((status_type or {}).get("name") or "").upper()
    detail = (
        (status_type or {}).get("detail")
        or (status_type or {}).get("description")
        or ""
    ).lower()
    completed = bool((status_type or {}).get("completed"))
    has_winner = any(c.get("winner") is True for c in (competitors or []))

    if state == "in" or "IN_PROGRESS" in name or "live" in detail:
        return "LIVE"
    if state == "post" or completed or "FINAL" in name or has_winner:
        return "FT"
    if state == "pre":
        return "NS"
    return "NS"


def _linescore_value(value) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _extract_sets(competitors: list[dict]) -> list[dict]:
    if len(competitors) < 2:
        return []

    a = competitors[0].get("linescores") or []
    b = competitors[1].get("linescores") or []
    max_len = max(len(a), len(b))

    sets: list[dict] = []
    for idx in range(max_len):
        a_set = a[idx] if idx < len(a) else {}
        b_set = b[idx] if idx < len(b) else {}
        sets.append({
            "set": idx + 1,
            "a": _linescore_value(a_set.get("value")),
            "b": _linescore_value(b_set.get("value")),
            "a_tb": a_set.get("tiebreak"),
            "b_tb": b_set.get("tiebreak"),
        })
    return sets


def _competition_to_match(event: dict, competition: dict, tour: str) -> dict | None:
    competitors = competition.get("competitors") or []
    if len(competitors) < 2:
        return None

    a = competitors[0]
    b = competitors[1]

    a_name = (a.get("athlete") or {}).get("displayName") or "Player A"
    b_name = (b.get("athlete") or {}).get("displayName") or "Player B"

    if not (_is_tracked_player(a_name) or _is_tracked_player(b_name)):
        return None

    status_type = (competition.get("status") or {}).get("type") or {}
    state = status_type.get("state", "pre")
    status_short = _map_status_short(status_type, competitors)

    comp_id = competition.get("id")
    event_id = event.get("id")
    match_id = f"tennis:{tour}:{event_id}:{comp_id}"

    winner = None
    if a.get("winner") is True:
        winner = a_name
    elif b.get("winner") is True:
        winner = b_name

    round_obj = competition.get("round") or {}
    round_name = (
        round_obj.get("displayName")
        or status_type.get("shortDetail")
        or status_type.get("detail")
        or status_type.get("description")
        or ""
    )

    return {
        "sport": "tennis",
        "match_id": match_id,
        "start_time": competition.get("date") or event.get("date"),
        "status": {
            "short": status_short,
            "state": state,
            "name": status_type.get("name") or "",
            "detail": status_type.get("detail") or "",
            "description": status_type.get("description") or "",
            "short_detail": status_type.get("shortDetail") or "",
            "completed": bool(status_type.get("completed")),
        },
        "event_name": event.get("shortName") or event.get("name") or "Tennis Event",
        "round": round_name,
        "tour": tour.upper(),
        "player_a": a_name,
        "player_b": b_name,
        "winner": winner,
        "sets": _extract_sets(competitors),
    }


async def _fetch_tour_scoreboard(
    session: aiohttp.ClientSession,
    tour: str,
    date_str: str | None = None,
) -> TennisSourceResult:
    url = f"{ESPN_TENNIS_BASE}/{tour}/scoreboard"
    if date_str:
        url += f"?dates={date_str}"

    try:
        async with session.get(url, timeout=ESPN_TIMEOUT) as response:
            if response.status != 200:
                _warn_source_failure(tour, date_str, "http", f"HTTP {response.status}")
                return TennisSourceResult(tour, date_str, (), False, "http", response.status)
            payload = await response.json(content_type=None)
    except asyncio.TimeoutError:
        _warn_source_failure(tour, date_str, "timeout", "timeout")
        return TennisSourceResult(tour, date_str, (), False, "timeout")
    except Exception as e:
        _warn_source_failure(tour, date_str, "other", f"error: {type(e).__name__}")
        return TennisSourceResult(tour, date_str, (), False, "other")

    matches: list[dict] = []
    for event in payload.get("events", []):
        for competition in event.get("competitions") or []:
            match = _competition_to_match(event, competition, tour)
            if match:
                matches.append(match)
        for grouping in event.get("groupings") or []:
            for competition in grouping.get("competitions") or []:
                match = _competition_to_match(event, competition, tour)
                if match:
                    matches.append(match)
    return TennisSourceResult(tour, date_str, tuple(matches), True)


async def fetch_tennis_sources(
    session: aiohttp.ClientSession,
    sources: list[tuple[str, str | None]],
) -> list[TennisSourceResult]:
    """Fetch distinct ESPN tour/date sources concurrently."""
    distinct = list(dict.fromkeys(sources))
    results = await asyncio.gather(
        *(_fetch_tour_scoreboard(session, tour, date_str) for tour, date_str in distinct),
        return_exceptions=True,
    )
    normalized: list[TennisSourceResult] = []
    for (tour, date_str), result in zip(distinct, results):
        if isinstance(result, TennisSourceResult):
            normalized.append(result)
        else:
            _warn_source_failure(tour, date_str, "other", "unexpected fetch failure")
            normalized.append(TennisSourceResult(tour, date_str, (), False, "other"))
    return normalized
