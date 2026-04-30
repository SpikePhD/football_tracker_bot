# utils/espn_client.py
# ESPN public API client. No authentication required.
# Normalizes ESPN event data into the same dict shape used by api_client.py
# so the rest of the codebase can treat both sources identically.

import asyncio
import logging
import aiohttp

logger = logging.getLogger(__name__)

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ESPN_SEARCH_BASE = "https://site.api.espn.com/apis/common/v3/search"
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
    seen: set = set()  # deduplicate ESPN duplicate entries by (minute, player, type)

    for detail in details:
        etype = detail.get("type", {}).get("text", "")
        athletes = detail.get("athletesInvolved", [])
        player_name = athletes[0].get("fullName", "N/A") if athletes else "N/A"
        clock_val = int(detail.get("clock", {}).get("value", 0)) // 60
        team_id = detail.get("team", {}).get("id")
        team_name = team_id_to_name.get(team_id, "Unknown")

        dedup_key = (clock_val, player_name, etype)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

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


# ── Team search ───────────────────────────────────────────────────────────────

async def search_team_espn(
    session: aiohttp.ClientSession,
    team_name: str,
    tracked_slugs: set,
) -> tuple | None:
    """
    Search ESPN for a soccer team by name.
    Returns (espn_team_id: str, primary_league_slug: str) for the first result
    whose defaultLeagueSlug is in tracked_slugs, or None if not found.
    """
    params = {"query": team_name, "sport": "soccer", "limit": 5}
    try:
        async with session.get(ESPN_SEARCH_BASE, params=params, timeout=ESPN_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"espn_client: search HTTP {resp.status} for '{team_name}'")
                return None
            data = await resp.json(content_type=None)
    except Exception as e:
        logger.warning(f"espn_client: search failed for '{team_name}': {e}")
        return None

    for item in data.get("items", []):
        slug = item.get("defaultLeagueSlug", "")
        if slug in tracked_slugs:
            return str(item["id"]), slug

    return None


# ── Team schedule ─────────────────────────────────────────────────────────────

async def fetch_next_team_fixture_espn(
    session: aiohttp.ClientSession,
    espn_team_id: int | str,
    slugs: list[str],
) -> dict | None:
    """
    Find the next upcoming fixture for a team by scanning scoreboards for the
    next 14 days across all provided league slugs.

    The team schedule endpoint only returns past matches, so we scan the
    scoreboard by date instead and filter for events involving espn_team_id.
    Returns a normalised match dict or None.
    """
    from datetime import timedelta, timezone
    from utils.time_utils import italy_now
    now = italy_now().astimezone(timezone.utc)
    team_id_str = str(espn_team_id)

    # Build reverse slug→league_id map
    slug_to_league: dict = {}
    try:
        from config import LEAGUE_SLUG_MAP
        slug_to_league = {v: k for k, v in LEAGUE_SLUG_MAP.items()}
    except ImportError:
        pass

    # Dates to scan: today through +13 days
    dates = [(now + timedelta(days=d)).strftime("%Y%m%d") for d in range(14)]

    async def _fetch(slug: str, date_str: str) -> tuple:
        return slug, await fetch_scoreboard(session, slug, date_str)

    tasks = [_fetch(slug, d) for slug in slugs for d in dates]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    upcoming: list[tuple] = []  # (datetime, event_dict, league_id)

    for result in results:
        if isinstance(result, Exception):
            continue
        slug, events = result
        league_id = slug_to_league.get(slug, 0)
        for event in events:
            # Filter: must involve our team
            competitions = event.get("competitions", [])
            if not competitions:
                continue
            competitors = competitions[0].get("competitors", [])
            team_ids = {c.get("team", {}).get("id") for c in competitors}
            if team_id_str not in team_ids:
                continue

            # Must be a future pre-match
            date_str_event = event.get("date", "")
            if not date_str_event:
                continue
            try:
                event_dt = datetime.fromisoformat(date_str_event.replace("Z", "+00:00"))
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


# ── Standings ─────────────────────────────────────────────────────────────────

async def fetch_standings_espn(
    session: aiohttp.ClientSession,
    slug: str,
) -> list[dict] | None:
    """
    Fetch league standings from ESPN.
    ESPN endpoint: https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/standings
    Returns list of team standings (normalized) or None on failure.
    """
    url = f"{ESPN_BASE}/{slug}/standings"
    try:
        async with session.get(url, timeout=ESPN_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"ESPN standings HTTP {resp.status} for {slug}")
                return None
            data = await resp.json(content_type=None)
            return _normalize_standings(data)
    except Exception as e:
        logger.warning(f"Failed to fetch standings for {slug}: {e}")
        return None


def _normalize_standings(data: dict) -> list[dict]:
    """
    Convert ESPN standings response to our normalized format.
    ESPN response structure:
    {
        "standings": [
            {
                "team": {"id": "123", "displayName": "AC Milan"},
                "stats": [
                    {"name": "rank", "value": "1"},
                    {"name": "points", "value": "60"},
                    ...
                ]
            },
            ...
        ]
    }
    """
    standings = []
    for entry in data.get("standings", []):
        team = entry.get("team", {})
        stats_list = entry.get("stats", [])
        stats = {s["name"]: s["value"] for s in stats_list}

        try:
            standing_entry = {
                "team_id": team.get("id", ""),
                "name": team.get("displayName") or team.get("name", "Unknown"),
                "position": int(stats.get("rank", 0)),
                "points": int(stats.get("points", 0)),
                "played": int(stats.get("matchesPlayed", 0)),
                "won": int(stats.get("wins", 0)),
                "drawn": int(stats.get("draws", 0)),
                "lost": int(stats.get("losses", 0)),
                "goals_for": int(stats.get("goalsFor", 0)),
                "goals_against": int(stats.get("goalsAgainst", 0)),
                "goal_difference": int(stats.get("goalDifferential", 0)),
            }
            standings.append(standing_entry)
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse standing entry for {team.get('displayName', '?')}: {e}")
            continue

    return standings


# ── Team Roster ───────────────────────────────────────────────────────────────

async def fetch_team_roster_espn(
    session: aiohttp.ClientSession,
    team_id: str,
    slug: str,
) -> dict | None:
    """
    Fetch team roster (players + coach) from ESPN.
    ESPN endpoint: https://site.api.espn.com/apis/site/v2/sports/soccer/teams/{team_id}
    Returns dict with team name, coach, and players (by name) or None on failure.
    """
    url = f"{ESPN_BASE}/teams/{team_id}"
    try:
        async with session.get(url, timeout=ESPN_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"ESPN team roster HTTP {resp.status} for {team_id}")
                return None
            data = await resp.json(content_type=None)
            return _normalize_roster(data)
    except Exception as e:
        logger.warning(f"Failed to fetch roster for {team_id}: {e}")
        return None


def _normalize_roster(data: dict) -> dict:
    """
    Convert ESPN team response to our normalized format.
    ESPN response structure:
    {
        "team": {"id": "123", "displayName": "AC Milan"},
        "athletes": [
            {
                "id": "456",
                "fullName": "Olivier Giroud",
                "position": {"name": "F"},
                "jersey": "9"
            },
            ...
        ],
        "staff": [
            {
                "position": {"name": "Head Coach"},
                "fullName": "Stefano Pioli"
            },
            ...
        ]
    }
    """
    team = data.get("team", {})
    athletes = data.get("athletes", [])
    staff = data.get("staff", [])

    # Extract coach
    coach = "Unknown"
    for person in staff:
        if person.get("position", {}).get("name") == "Head Coach":
            coach = person.get("fullName", "Unknown")
            break

    # Extract players (by name, as per user preference)
    players = {}
    for athlete in athletes:
        player_name = athlete.get("fullName", "Unknown")
        if not player_name:
            continue
        players[player_name] = {
            "position": athlete.get("position", {}).get("name", "Unknown"),
            "number": athlete.get("jersey", "N/A"),
            "goals": 0,
            "assists": 0,
            "yellow_cards": 0,
            "red_cards": 0,
        }

    return {
        "name": team.get("displayName") or team.get("name", "Unknown"),
        "coach": coach,
        "players": players,
    }


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
