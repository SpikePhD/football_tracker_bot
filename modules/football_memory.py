# modules/football_memory.py
# Centralized memory management for football data (standings, teams, players, matches).
# All data is sourced from ESPN API to ensure consistency.

import json
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Any

from config import (
    TRACKED_LEAGUE_IDS,
    LEAGUE_SLUG_MAP,
    LEAGUE_NAME_MAP,
    MEMORY_STALE_THRESHOLD_DAYS,
    ESPN_CACHE_TTL_SEC,
)
from utils.time_utils import italy_now

logger = logging.getLogger(__name__)

# --- Paths and Constants ---
MEMORY_PATH = Path("bot_memory/football_memory.json")
MATCH_RETENTION_DAYS = 30  # Keep matches for last 30 days only

# --- ESPN Cache (12h TTL) ---
_espn_cache: Dict[str, Any] = {}
_espn_cache_ts: Dict[str, datetime] = {}


def _default_memory() -> Dict[str, Any]:
    """Return empty memory structure."""
    return {
        "metadata": {
            "last_full_update": None,
            "last_standings_update": None,
            "last_team_info_update": None,
        },
        "leagues": {},   # {league_id: {"name": str, "standings": list, "last_updated": str}}
        "teams": {},     # {team_id: {"name": str, "coach": str, "players": {player_name: {...}}, "stats": {...}, "last_updated": str}}
        "matches": {},   # {match_id: {"league_id": int, "home": {...}, "away": {...}, "score": {...}, "status": str, "date": str, "events": list}}
    }


def load_memory() -> Dict[str, Any]:
    """Load memory from disk. Returns default if file doesn't exist or is corrupted."""
    if not MEMORY_PATH.exists():
        logger.info("No football memory found. Initializing empty memory.")
        return _default_memory()
    try:
        with open(MEMORY_PATH, "r", encoding="utf-8") as f:
            memory = json.load(f)
        logger.info("Football memory loaded successfully.")
        return memory
    except Exception as e:
        logger.error(f"Failed to load football memory: {e}")
        return _default_memory()


def save_memory(memory: Dict[str, Any]) -> None:
    """Save memory to disk. Creates directory if needed."""
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(memory, f, indent=2, ensure_ascii=False)
        logger.info("Football memory saved successfully.")
    except Exception as e:
        logger.error(f"Failed to save football memory: {e}")


# --- ESPN Cache Helpers ---
def _get_espn_cache(key: str) -> Optional[Any]:
    """Get cached ESPN response if still valid (TTL: ESPN_CACHE_TTL_SEC)."""
    if key not in _espn_cache or key not in _espn_cache_ts:
        return None
    if (italy_now() - _espn_cache_ts[key]).total_seconds() > ESPN_CACHE_TTL_SEC:
        del _espn_cache[key]
        del _espn_cache_ts[key]
        return None
    return _espn_cache[key]


def _set_espn_cache(key: str, data: Any) -> None:
    """Cache ESPN response with current timestamp."""
    _espn_cache[key] = data
    _espn_cache_ts[key] = italy_now()


# --- Staleness Checks ---
def check_memory_staleness(memory: Dict[str, Any]) -> Optional[str]:
    """
    Check if memory is stale (older than MEMORY_STALE_THRESHOLD_DAYS).
    Returns a warning message if stale, else None.
    """
    last_update_str = memory["metadata"].get("last_full_update")
    if not last_update_str:
        return "⚠️ Memory not initialized. Use !refresh_memory."
    try:
        last_update = datetime.fromisoformat(last_update_str.replace("Z", "+00:00"))
        if (italy_now() - last_update) > timedelta(days=MEMORY_STALE_THRESHOLD_DAYS):
            return f"⚠️ Memory outdated (last updated {last_update.strftime('%Y-%m-%d')})."
    except Exception as e:
        logger.warning(f"Failed to parse memory update timestamp: {e}")
    return None


# --- Memory Updates ---
async def update_league_standings(
    session: Any, league_id: int, slug: str
) -> Optional[Dict[str, Any]]:
    """
    Fetch standings from ESPN for a league and return normalized dict.
    Uses cache (12h TTL). On failure, returns None (caller should keep old memory).
    """
    cache_key = f"standings_{slug}"
    cached = _get_espn_cache(cache_key)
    if cached is not None:
        logger.info(f"Using cached standings for {slug}.")
        return cached

    try:
        from utils.espn_client import fetch_standings_espn
        standings = await fetch_standings_espn(session, slug)
        if standings is None:
            logger.warning(f"No standings data returned for {slug}.")
            return None
        result = {
            "name": LEAGUE_NAME_MAP.get(league_id, f"League {league_id}"),
            "standings": standings,
            "last_updated": italy_now().isoformat(),
        }
        _set_espn_cache(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Failed to update standings for league {league_id} ({slug}): {e}")
        return None


async def update_team_info(
    session: Any, team_id: str, slug: str
) -> Optional[Dict[str, Any]]:
    """
    Fetch team roster (players + coach) from ESPN and return normalized dict.
    Uses cache (12h TTL). On failure, returns None.
    """
    cache_key = f"team_info_{team_id}"
    cached = _get_espn_cache(cache_key)
    if cached is not None:
        logger.info(f"Using cached team info for {team_id}.")
        return cached

    try:
        from utils.espn_client import fetch_team_roster_espn
        roster = await fetch_team_roster_espn(session, team_id, slug)
        if roster is None:
            logger.warning(f"No roster data returned for team {team_id}.")
            return None
        result = {
            "name": roster.get("name", f"Team {team_id}"),
            "coach": roster.get("coach", "Unknown"),
            "players": roster.get("players", {}),
            "last_updated": italy_now().isoformat(),
        }
        _set_espn_cache(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Failed to update team info for {team_id}: {e}")
        return None


async def update_match_data(
    session: Any, match: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Process a Full-Time match and return updates for memory.
    Returns: {"match": {...}, "home_team": {...}, "away_team": {...}}
    """
    match_id = str(match["fixture"]["id"])
    home_id = str(match["teams"]["home"]["id"])
    away_id = str(match["teams"]["away"]["id"])
    league_id = match["league"]["id"]

    # --- Match Data ---
    match_data = {
        "league_id": league_id,
        "home": {"id": home_id, "name": match["teams"]["home"]["name"]},
        "away": {"id": away_id, "name": match["teams"]["away"]["name"]},
        "score": {
            "home": match["goals"]["home"],
            "away": match["goals"]["away"],
        },
        "status": match["fixture"]["status"]["short"],
        "date": match["fixture"]["date"],
        "events": match.get("events", []),
    }

    # --- Team Stats Updates ---
    home_goals = match["goals"]["home"] or 0
    away_goals = match["goals"]["away"] or 0

    home_team_update = {
        "stats": {
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_for": home_goals,
            "goals_against": away_goals,
        }
    }
    away_team_update = {
        "stats": {
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_for": away_goals,
            "goals_against": home_goals,
        }
    }

    if home_goals > away_goals:
        home_team_update["stats"]["wins"] = 1
        away_team_update["stats"]["losses"] = 1
    elif home_goals == away_goals:
        home_team_update["stats"]["draws"] = 1
        away_team_update["stats"]["draws"] = 1
    else:
        home_team_update["stats"]["losses"] = 1
        away_team_update["stats"]["wins"] = 1

    # --- Player Stats Updates (by name) ---
    player_stats_updates = {}
    for event in match.get("events", []):
        event_type = event.get("type")
        player_name = event.get("player", {}).get("name")
        team_id = event.get("team", {}).get("id")

        if not player_name or not team_id:
            continue

        if team_id not in player_stats_updates:
            player_stats_updates[team_id] = {}
        if player_name not in player_stats_updates[team_id]:
            player_stats_updates[team_id][player_name] = {
                "goals": 0,
                "assists": 0,
                "yellow_cards": 0,
                "red_cards": 0,
            }

        if event_type == "Goal":
            player_stats_updates[team_id][player_name]["goals"] += 1
        elif event_type == "Card":
            detail = event.get("detail", "")
            if "Yellow" in detail:
                player_stats_updates[team_id][player_name]["yellow_cards"] += 1
            elif "Red" in detail:
                player_stats_updates[team_id][player_name]["red_cards"] += 1
        # Note: Assists are not always available in ESPN events

    return {
        "match": match_data,
        "home_team": home_team_update,
        "away_team": away_team_update,
        "player_stats": player_stats_updates,
    }


# --- Public Update Functions ---
async def update_all_memory(session: Any) -> None:
    """
    Force update all memory data (standings, teams, recent matches).
    Called by !refresh_memory or at startup.
    """
    memory = load_memory()
    tasks = []

    # Update standings for all tracked leagues
    for league_id in TRACKED_LEAGUE_IDS:
        slug = LEAGUE_SLUG_MAP.get(league_id)
        if slug:
            tasks.append(
                (league_id, update_league_standings(session, league_id, slug))
            )

    # Execute standings updates
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for league_id, result in results:
        if isinstance(result, dict):
            memory["leagues"][str(league_id)] = result

    # Update team info for all teams in tracked leagues
    # First, collect all unique team IDs from existing matches and leagues
    team_ids_to_update = set()
    for league_data in memory.get("leagues", {}).values():
        for team in league_data.get("standings", []):
            team_id = team.get("team_id")
            if team_id:
                team_ids_to_update.add(str(team_id))

    # Also include teams from existing matches
    for match_data in memory.get("matches", {}).values():
        team_ids_to_update.add(str(match_data["home"]["id"]))
        team_ids_to_update.add(str(match_data["away"]["id"]))

    # Update team info (roster + coach)
    team_tasks = []
    for team_id in team_ids_to_update:
        # Use Serie A slug as default (most teams will be in Serie A)
        slug = LEAGUE_SLUG_MAP.get(135, "ita.1")
        team_tasks.append((team_id, update_team_info(session, team_id, slug)))

    team_results = await asyncio.gather(*team_tasks, return_exceptions=True)
    for team_id, result in team_results:
        if isinstance(result, dict):
            memory["teams"][team_id] = result

    # Prune old matches (keep last 30 days)
    now = italy_now()
    pruned_matches = {}
    for match_id, match_data in memory.get("matches", {}).items():
        match_date_str = match_data.get("date")
        if match_date_str:
            try:
                match_date = datetime.fromisoformat(match_date_str.replace("Z", "+00:00"))
                if (now - match_date) <= timedelta(days=MATCH_RETENTION_DAYS):
                    pruned_matches[match_id] = match_data
            except Exception:
                pruned_matches[match_id] = match_data
        else:
            pruned_matches[match_id] = match_data
    memory["matches"] = pruned_matches

    # Update metadata
    memory["metadata"] = {
        "last_full_update": italy_now().isoformat(),
        "last_standings_update": italy_now().isoformat(),
        "last_team_info_update": italy_now().isoformat(),
    }

    save_memory(memory)
    logger.info("All football memory updated successfully.")


async def update_standings_only(session: Any) -> None:
    """Update only league standings (called daily at midnight)."""
    memory = load_memory()
    tasks = []

    for league_id in TRACKED_LEAGUE_IDS:
        slug = LEAGUE_SLUG_MAP.get(league_id)
        if slug:
            tasks.append(
                (league_id, update_league_standings(session, league_id, slug))
            )

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for league_id, result in results:
        if isinstance(result, dict):
            memory["leagues"][str(league_id)] = result

    memory["metadata"]["last_standings_update"] = italy_now().isoformat()
    save_memory(memory)
    logger.info("League standings updated successfully.")


async def update_team_info_only(session: Any) -> None:
    """Update only team info (called weekly on Sunday)."""
    memory = load_memory()
    team_ids_to_update = set()

    # Collect team IDs from leagues and matches
    for league_data in memory.get("leagues", {}).values():
        for team in league_data.get("standings", []):
            team_id = team.get("team_id")
            if team_id:
                team_ids_to_update.add(str(team_id))

    for match_data in memory.get("matches", {}).values():
        team_ids_to_update.add(str(match_data["home"]["id"]))
        team_ids_to_update.add(str(match_data["away"]["id"]))

    # Update team info
    tasks = []
    for team_id in team_ids_to_update:
        slug = LEAGUE_SLUG_MAP.get(135, "ita.1")  # Default to Serie A
        tasks.append((team_id, update_team_info(session, team_id, slug)))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for team_id, result in results:
        if isinstance(result, dict):
            memory["teams"][team_id] = result

    memory["metadata"]["last_team_info_update"] = italy_now().isoformat()
    save_memory(memory)
    logger.info("Team info updated successfully.")


async def update_match_in_memory(session: Any, match: Dict[str, Any]) -> None:
    """
    Update memory with a Full-Time match.
    Called by ft_handler when a match reaches FT status.
    """
    memory = load_memory()

    # Skip if not FT
    if match["fixture"]["status"]["short"] != "FT":
        return

    # Process match data
    update_result = await update_match_data(session, match)
    if not update_result:
        return

    match_id = str(match["fixture"]["id"])
    home_id = str(match["teams"]["home"]["id"])
    away_id = str(match["teams"]["away"]["id"])

    # Store match
    memory["matches"][match_id] = update_result["match"]

    # Initialize teams if not present
    if home_id not in memory["teams"]:
        memory["teams"][home_id] = {
            "name": match["teams"]["home"]["name"],
            "coach": "Unknown",
            "players": {},
            "stats": {"wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0},
            "last_updated": italy_now().isoformat(),
        }
    if away_id not in memory["teams"]:
        memory["teams"][away_id] = {
            "name": match["teams"]["away"]["name"],
            "coach": "Unknown",
            "players": {},
            "stats": {"wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0},
            "last_updated": italy_now().isoformat(),
        }

    # Update team stats
    for stat in ["wins", "draws", "losses", "goals_for", "goals_against"]:
        memory["teams"][home_id]["stats"][stat] += update_result["home_team"]["stats"][stat]
        memory["teams"][away_id]["stats"][stat] += update_result["away_team"]["stats"][stat]

    # Update player stats
    for team_id, players in update_result.get("player_stats", {}).items():
        if team_id not in memory["teams"]:
            continue
        for player_name, stats in players.items():
            if player_name not in memory["teams"][team_id]["players"]:
                memory["teams"][team_id]["players"][player_name] = {
                    "goals": 0,
                    "assists": 0,
                    "yellow_cards": 0,
                    "red_cards": 0,
                }
            for stat in ["goals", "assists", "yellow_cards", "red_cards"]:
                memory["teams"][team_id]["players"][player_name][stat] += stats[stat]

    save_memory(memory)
    logger.info(f"Updated memory with FT match: {match_id}")


# --- Query Helpers ---
def get_league_standings(league_id: int) -> Optional[List[Dict[str, Any]]]:
    """Get standings for a league from memory."""
    memory = load_memory()
    league_data = memory.get("leagues", {}).get(str(league_id))
    if league_data:
        return league_data.get("standings")
    return None


def get_team_info(team_id: str) -> Optional[Dict[str, Any]]:
    """Get team info (roster + stats) from memory."""
    memory = load_memory()
    return memory.get("teams", {}).get(team_id)


def get_team_stats(team_id: str) -> Optional[Dict[str, Any]]:
    """Get team stats (W/D/L, goals) from memory."""
    team_info = get_team_info(team_id)
    if team_info:
        return team_info.get("stats")
    return None


def get_player_stats(team_id: str, player_name: str) -> Optional[Dict[str, Any]]:
    """Get player stats from memory."""
    team_info = get_team_info(team_id)
    if team_info and player_name in team_info.get("players", {}):
        return team_info["players"][player_name]
    return None


def get_recent_matches(team_id: str, limit: int = 5) -> List[Dict[str, Any]]:
    """Get recent matches for a team from memory."""
    memory = load_memory()
    matches = []
    for match_id, match_data in memory.get("matches", {}).items():
        if match_data["home"]["id"] == team_id or match_data["away"]["id"] == team_id:
            matches.append(match_data)
    # Sort by date (newest first) and limit
    matches.sort(key=lambda x: x.get("date", ""), reverse=True)
    return matches[:limit]
