# utils/espn_client.py
# ESPN public API client. No authentication required.
# Normalizes ESPN event data into the same dict shape used by api_client.py
# so the rest of the codebase can treat both sources identically.

import asyncio
import logging
import aiohttp

logger = logging.getLogger(__name__)

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ESPN_TIMEOUT = aiohttp.ClientTimeout(total=10)


# ── Status mapping ────────────────────────────────────────────────────────────

def _map_status(state: str, period: int, description: str, status_name: str) -> str:
    """Convert ESPN status fields to an API-Football-compatible status short code."""
    desc = description.lower()
    name = status_name.upper()

    if state == "pre":
        if "POSTPONED" in name:
            return "PST"
        if "CANCELED" in name or "CANCELLED" in name:
            return "CANC"
        return "NS"

    if state == "in":
        if "halftime" in desc or "half time" in desc:
            return "HT"
        if "extra time" in desc or "EXTRA_TIME" in name:
            return "ET"
        if "penalty" in desc or "SHOOTOUT" in name:
            return "PEN"
        if period == 1:
            return "1H"
        return "2H"

    if state == "post":
        if "POSTPONED" in name:
            return "PST"
        if "CANCELED" in name or "CANCELLED" in name:
            return "CANC"
        if "ABANDONED" in name or "SUSPENDED" in name:
            return "ABD"
        return "FT"

    return "NS"


# ── Event normalization ───────────────────────────────────────────────────────

def _normalize_details(details: list, team_id_to_name: dict) -> list:
    """Convert ESPN competition details (goals, cards) to API-Football event format."""
    events = []
    for detail in details:
        etype = detail.get("type", {}).get("text", "")
        athletes = detail.get("athletesInvolved", [])
        player_name = athletes[0].get("fullName", "N/A") if athletes else "N/A"
        clock_val = int(detail.get("clock", {}).get("value", 0)) // 60
        team_id = detail.get("team", {}).get("id")
        team_name = team_id_to_name.get(team_id, "Unknown")

        if etype == "Goal":
            events.append({
                "time": {"elapsed": clock_val},
                "player": {"name": player_name},
                "team": {"name": team_name},
                "type": "Goal",
                "detail": "Normal Goal",
            })
        elif etype in ("Penalty - Scored", "Penalty"):
            events.append({
                "time": {"elapsed": clock_val},
                "player": {"name": player_name},
                "team": {"name": team_name},
                "type": "Goal",
                "detail": "Penalty",
            })
        elif etype == "Own Goal":
            events.append({
                "time": {"elapsed": clock_val},
                "player": {"name": player_name},
                "team": {"name": team_name},
                "type": "Goal",
                "detail": "Own Goal",
            })
        elif etype == "Red Card":
            events.append({
                "time": {"elapsed": clock_val},
                "player": {"name": player_name},
                "team": {"name": team_name},
                "type": "Card",
                "detail": "Red Card",
            })
        # Yellow cards and substitutions are intentionally ignored.
    return events


# ── Event normalization ───────────────────────────────────────────────────────

def _normalize_event(espn_event: dict, league_id: int) -> dict | None:
    """Convert a single ESPN event dict to the normalized match format."""
    try:
        competitions = espn_event.get("competitions", [])
        if not competitions:
            return None
        competition = competitions[0]

        competitors = competition.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            return None

        # Build team ID → name lookup for detail resolution
        team_id_to_name = {
            c.get("team", {}).get("id"): (
                c.get("team", {}).get("displayName") or c.get("team", {}).get("name", "Unknown")
            )
            for c in competitors
        }

        home_team_name = (
            home.get("team", {}).get("displayName")
            or home.get("team", {}).get("name", "Home")
        )
        away_team_name = (
            away.get("team", {}).get("displayName")
            or away.get("team", {}).get("name", "Away")
        )

        # Status
        status_obj = espn_event.get("status", {})
        status_type = status_obj.get("type", {})
        state = status_type.get("state", "pre")
        period = status_obj.get("period", 1)
        description = status_type.get("description", "")
        status_name = status_type.get("name", "")
        status_short = _map_status(state, period, description, status_name)

        # Current match minute (live matches only)
        display_clock = status_obj.get("displayClock", "")
        try:
            elapsed_min = int(display_clock.split(":")[0]) if display_clock else int(status_obj.get("clock", 0)) // 60
        except (ValueError, IndexError):
            elapsed_min = int(status_obj.get("clock", 0)) // 60

        # Score (None before match starts)
        if state == "pre":
            home_score = None
            away_score = None
        else:
            try:
                home_score = int(home.get("score", 0) or 0)
                away_score = int(away.get("score", 0) or 0)
            except (ValueError, TypeError):
                home_score = 0
                away_score = 0

        # Events (goals and red cards)
        details = competition.get("details", [])
        events = _normalize_details(details, team_id_to_name)

        return {
            "fixture": {
                "id": espn_event.get("id"),   # string, e.g. "737084"
                "date": espn_event.get("date"),  # UTC ISO, e.g. "2026-04-04T13:00Z"
                "status": {"short": status_short, "elapsed": elapsed_min},
            },
            "teams": {
                "home": {"name": home_team_name},
                "away": {"name": away_team_name},
            },
            "goals": {
                "home": home_score,
                "away": away_score,
            },
            "events": events,
            "league": {"id": league_id},
        }

    except Exception as e:
        logger.warning(f"espn_client: Failed to normalize event {espn_event.get('id', '?')}: {e}")
        return None


# ── Team schedule ─────────────────────────────────────────────────────────────

async def fetch_next_team_fixture_espn(
    session: aiohttp.ClientSession,
    espn_team_id: int | str,
    slugs: list[str],
) -> dict | None:
    """
    Find the next upcoming fixture for a team across a list of league slugs.
    Returns a normalised match dict (same shape as fetch_all_leagues) or None.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    async def _fetch_schedule(slug: str) -> list[dict]:
        url = f"{ESPN_BASE}/{slug}/teams/{espn_team_id}/schedule"
        try:
            async with session.get(url, timeout=ESPN_TIMEOUT) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json(content_type=None)
                return data.get("events", [])
        except Exception as e:
            logger.warning(f"espn_client: schedule fetch failed for {slug}: {e}")
            return []

    tasks = [_fetch_schedule(slug) for slug in slugs]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    upcoming: list[tuple] = []  # (datetime, event_dict, league_id)
    slug_to_league = {}  # built lazily — we only need it for normalization

    # Build reverse slug→league_id map from LEAGUE_SLUG_MAP if available
    try:
        from config import LEAGUE_SLUG_MAP
        slug_to_league = {v: k for k, v in LEAGUE_SLUG_MAP.items()}
    except ImportError:
        pass

    for slug, events in zip(slugs, results):
        if isinstance(events, Exception) or not events:
            continue
        league_id = slug_to_league.get(slug, 0)
        for event in events:
            date_str = event.get("date", "")
            if not date_str:
                continue
            try:
                event_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            status_state = event.get("status", {}).get("type", {}).get("state", "pre")
            if event_dt > now and status_state == "pre":
                upcoming.append((event_dt, event, league_id))

    if not upcoming:
        return None

    upcoming.sort(key=lambda x: x[0])
    event_dt, event, league_id = upcoming[0]
    return _normalize_event(event, league_id)


# ── HTTP requests ─────────────────────────────────────────────────────────────

async def fetch_scoreboard(
    session: aiohttp.ClientSession,
    slug: str,
    date_str: str | None = None,
) -> list[dict]:
    """
    Fetch raw ESPN event dicts for a single league.
    date_str: YYYYMMDD format. Omit for today's matches.
    Returns empty list on any error.
    """
    url = f"{ESPN_BASE}/{slug}/scoreboard"
    if date_str:
        url += f"?dates={date_str}"

    try:
        async with session.get(url, timeout=ESPN_TIMEOUT) as response:
            if response.status != 200:
                logger.warning(f"espn_client: HTTP {response.status} for {slug} scoreboard.")
                return []
            data = await response.json(content_type=None)
            return data.get("events", [])
    except asyncio.TimeoutError:
        logger.warning(f"espn_client: Timeout fetching {slug} scoreboard.")
        return []
    except Exception as e:
        logger.warning(f"espn_client: Error fetching {slug} scoreboard: {e}")
        return []


async def fetch_all_leagues(
    session: aiohttp.ClientSession,
    slug_map: dict,  # {league_id: slug}
    date_str: str | None = None,
) -> list[dict]:
    """
    Concurrently fetch scoreboards for all leagues in slug_map.
    Returns a combined list of normalized match dicts.
    Raises no exceptions — errors per-league are logged and skipped.
    """
    tasks = [
        fetch_scoreboard(session, slug, date_str)
        for slug in slug_map.values()
    ]
    league_ids = list(slug_map.keys())

    results = await asyncio.gather(*tasks, return_exceptions=True)

    normalized: list[dict] = []
    for league_id, result in zip(league_ids, results):
        if isinstance(result, Exception):
            logger.warning(f"espn_client: Exception for league {league_id}: {result}")
            continue
        for event in result:
            match = _normalize_event(event, league_id)
            if match is not None:
                normalized.append(match)

    return normalized
