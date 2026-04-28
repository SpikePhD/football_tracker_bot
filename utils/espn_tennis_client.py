# utils/espn_tennis_client.py
import asyncio
import logging
from datetime import datetime

import aiohttp

from config import TRACKED_TENNIS_PLAYERS

logger = logging.getLogger(__name__)

ESPN_TENNIS_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis"
ESPN_TENNIS_TOURS = ("atp", "wta")
ESPN_TIMEOUT = aiohttp.ClientTimeout(total=10)


def _normalize_name(name: str) -> str:
    return " ".join((name or "").strip().lower().split())


def _is_tracked_player(name: str) -> bool:
    normalized = _normalize_name(name)
    return normalized in TRACKED_TENNIS_PLAYERS


def _map_status_short(state: str) -> str:
    if state == "pre":
        return "NS"
    if state == "in":
        return "LIVE"
    if state == "post":
        return "FT"
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
    status_short = _map_status_short(state)

    comp_id = competition.get("id")
    event_id = event.get("id")
    match_id = f"tennis:{tour}:{event_id}:{comp_id}"

    winner = None
    if a.get("winner") is True:
        winner = a_name
    elif b.get("winner") is True:
        winner = b_name

    round_name = status_type.get("shortDetail") or status_type.get("detail") or status_type.get("description") or ""

    return {
        "sport": "tennis",
        "match_id": match_id,
        "start_time": competition.get("date") or event.get("date"),
        "status": {
            "short": status_short,
            "state": state,
            "detail": status_type.get("detail") or "",
            "description": status_type.get("description") or "",
        },
        "event_name": event.get("shortName") or event.get("name") or "Tennis Event",
        "round": round_name,
        "tour": tour.upper(),
        "player_a": a_name,
        "player_b": b_name,
        "winner": winner,
        "sets": _extract_sets(competitors),
    }


def _dedup_key(match: dict) -> str:
    """
    Canonical dedup key independent of ATP/WTA feed label.
    Uses normalized sorted player pair + competition date + event name.
    """
    players = sorted([
        _normalize_name(match.get("player_a", "")),
        _normalize_name(match.get("player_b", "")),
    ])
    start_time = match.get("start_time") or ""
    date_part = ""
    if start_time:
        try:
            date_part = datetime.fromisoformat(start_time.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            date_part = start_time[:10]
    event_name = _normalize_name(match.get("event_name", ""))
    return f"{players[0]}|{players[1]}|{date_part}|{event_name}"


def _match_richness_score(match: dict) -> tuple:
    """
    Prefer records with richer score/status metadata when duplicates collide.
    """
    sets = match.get("sets") or []
    status = match.get("status") or {}
    round_text = match.get("round") or ""
    return (
        len(sets),
        1 if status.get("detail") else 0,
        1 if round_text else 0,
        1 if match.get("winner") else 0,
    )


async def _fetch_tour_scoreboard(session: aiohttp.ClientSession, tour: str, date_str: str | None = None) -> dict:
    url = f"{ESPN_TENNIS_BASE}/{tour}/scoreboard"
    if date_str:
        url += f"?dates={date_str}"

    try:
        async with session.get(url, timeout=ESPN_TIMEOUT) as response:
            if response.status != 200:
                logger.warning(f"espn_tennis_client: HTTP {response.status} for {tour} scoreboard")
                return {}
            return await response.json(content_type=None)
    except asyncio.TimeoutError:
        logger.warning(f"espn_tennis_client: timeout for {tour} scoreboard")
        return {}
    except Exception as e:
        logger.warning(f"espn_tennis_client: error for {tour} scoreboard: {e}")
        return {}


async def fetch_tracked_tennis_matches(
    session: aiohttp.ClientSession,
    date_str: str | None = None,
) -> list[dict]:
    """
    Fetch tracked tennis matches from ESPN ATP/WTA scoreboards.
    date_str must be YYYYMMDD when provided.
    """
    results = await asyncio.gather(
        *(_fetch_tour_scoreboard(session, tour, date_str) for tour in ESPN_TENNIS_TOURS),
        return_exceptions=True,
    )

    matches: list[dict] = []
    for tour, payload in zip(ESPN_TENNIS_TOURS, results):
        if isinstance(payload, Exception) or not isinstance(payload, dict):
            continue

        for event in payload.get("events", []):
            groupings = event.get("groupings") or []
            for grouping in groupings:
                for competition in grouping.get("competitions") or []:
                    match = _competition_to_match(event, competition, tour)
                    if match:
                        matches.append(match)

    deduped: dict[str, dict] = {}
    for match in matches:
        key = _dedup_key(match)
        existing = deduped.get(key)
        if existing is None or _match_richness_score(match) > _match_richness_score(existing):
            deduped[key] = match

    final_matches = list(deduped.values())
    final_matches.sort(key=lambda m: m.get("start_time") or "")
    return final_matches
