# modules/ft_handler.py

import logging
import discord
from datetime import datetime, timedelta

from config import CHANNEL_ID
from modules.bot_mode import is_silent
from utils.time_utils import italy_now
from utils.event_formatter import format_match_events, event_completeness_note
from modules.discord_poster import post_new_general_message

logger = logging.getLogger(__name__)

tracked_matches = {}  # {match_id: {"exp_ft": datetime, "initial_score_at_tracking": {...}}}
_already_announced_ft: set = set()  # match IDs that have already received a FT announcement


def clear_tracked_matches_today():
    global tracked_matches, _already_announced_ft
    logger.info("🔄 Clearing 'tracked_matches' dictionary for the new day.")
    tracked_matches.clear()
    _already_announced_ft.clear()


def seed_already_announced_ft(fixtures: list) -> None:
    """
    Pre-populate the announced-FT set with matches already finished when the
    scheduler starts. Prevents re-announcing results already visible in the
    startup/morning broadcast message.
    """
    global _already_announced_ft
    count = 0
    for match in fixtures:
        if match.get("fixture", {}).get("status", {}).get("short") == "FT":
            mid = match["fixture"].get("id")
            if mid:
                _already_announced_ft.add(mid)
                count += 1
    if count:
        logger.info(f"🌱 Seeded {count} already-FT match IDs (will not re-announce).")


def track_match_for_ft(match_data: dict):
    """Register a live match to be checked for Full-Time status."""
    try:
        match_id = match_data["fixture"]["id"]
        kickoff_utc = match_data["fixture"]["date"]

        kickoff = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
        kickoff = kickoff.astimezone(italy_now().tzinfo)
        expected_ft_check_time = kickoff + timedelta(minutes=112)

        tracked_matches[match_id] = {
            "exp_ft": expected_ft_check_time,
            "initial_score_at_tracking": match_data.get("goals", {"home": None, "away": None}),
        }

        home = match_data.get("teams", {}).get("home", {}).get("name", "Home Team")
        away = match_data.get("teams", {}).get("away", {}).get("name", "Away Team")
        logger.info(
            f"🆕 Tracking {home} vs {away} (ID: {match_id}) for FT. "
            f"Expected check around {expected_ft_check_time.strftime('%H:%M')}"
        )
    except KeyError as e:
        logger.error(f"Error tracking match for FT: Missing key {e} in match_data. Data: {str(match_data)[:200]}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error in track_match_for_ft: {e}", exc_info=True)


# ── Shared FT posting logic ───────────────────────────────────────────────────

async def _post_ft_from_data(bot: discord.Client, match_details: dict):
    """Build and send the FT result message from a normalized match dict."""
    home_team = match_details.get("teams", {}).get("home", {}).get("name", "Home Team")
    away_team = match_details.get("teams", {}).get("away", {}).get("name", "Away Team")
    goals = match_details.get("goals", {"home": "?", "away": "?"})
    events = match_details.get("events", [])

    detail_lines = format_match_events(events, home_team, away_team)

    ft_message = f"FT: {home_team} {goals.get('home', '?')} - {goals.get('away', '?')} {away_team}"
    if detail_lines:
        ft_message += f" ({'; '.join(detail_lines)})"

    note = event_completeness_note(goals, events)
    if note:
        ft_message += note
        logger.warning(
            f"⚠️ FT event mismatch for {home_team} vs {away_team}: "
            f"score total={int(goals.get('home',0) or 0) + int(goals.get('away',0) or 0)}, "
            f"goal events={sum(1 for e in events if e.get('type') == 'Goal')}."
        )

    logger.info(f"📢 Posting FT result: {ft_message}")
    await post_new_general_message(bot, CHANNEL_ID, content=ft_message)


# ── Main FT detection (dual-path) ─────────────────────────────────────────────

async def fetch_and_post_ft(bot: discord.Client):
    """
    Check tracked matches for Full-Time status and post results.

    ESPN mode (primary): reads finished matches from the cached scoreboard — no extra API call.
    Fallback mode: calls API-Football per tracked match after expected FT time.
    """
    if is_silent():
        return

    from modules import api_provider  # late import to avoid circular dependency

    current_time = italy_now()

    if api_provider.is_espn_healthy():
        # ── ESPN path ──────────────────────────────────────────────────────────
        finished = await api_provider.fetch_finished_today(bot.http_session)
        finished_by_id = {m["fixture"]["id"]: m for m in finished}

        for match_id, info in list(tracked_matches.items()):
            if current_time < info["exp_ft"]:
                continue  # too early to expect FT

            if match_id not in finished_by_id:
                # Match not yet in FT — check if it's been suspiciously long
                elapsed = (current_time - info["exp_ft"]).total_seconds()
                if elapsed > 1800:  # 30 min past expected FT with no result
                    logger.warning(
                        f"⚠️ Match ID {match_id} is 30+ min past expected FT but not showing as FT "
                        f"in ESPN scoreboard. Dropping from tracking."
                    )
                    del tracked_matches[match_id]
                continue

            match_details = finished_by_id[match_id]
            await _post_ft_from_data(bot, match_details)
            _already_announced_ft.add(match_id)
            del tracked_matches[match_id]

        # Orphan FT detection: matches that reached FT without being seen as LIVE
        # (e.g. bot was offline, or match finished before scheduler started polling).
        for match in finished:
            mid = match["fixture"]["id"]
            if mid in tracked_matches or mid in _already_announced_ft:
                continue
            logger.info(f"🆕 [Orphan FT] Announcing untracked FT match {mid}.")
            await _post_ft_from_data(bot, match)
            _already_announced_ft.add(mid)

    else:
        # ── API-Football fallback path ─────────────────────────────────────────
        from utils.api_client import fetch_fixture_by_id

        for match_id, info in list(tracked_matches.items()):
            if current_time < info["exp_ft"]:
                continue

            logger.info(f"🔍 [Fallback] Checking FT status for match ID {match_id}")

            payload = await fetch_fixture_by_id(bot.http_session, match_id)
            if not payload:
                logger.warning(f"⚠️ No payload for FT check of match ID {match_id}. Retrying next cycle.")
                continue

            api_response_list = payload.get("response")
            if not isinstance(api_response_list, list) or not api_response_list:
                logger.warning(f"⚠️ Empty or invalid response for FT check of match ID {match_id}.")
                continue

            match_details_raw = api_response_list[0]
            fixture_status_short = match_details_raw.get("fixture", {}).get("status", {}).get("short")

            if fixture_status_short != "FT":
                logger.info(f"ℹ️ Match ID {match_id} status is '{fixture_status_short}', not FT yet.")
                if fixture_status_short in ("PST", "CANC", "ABD", "AWD", "WO"):
                    logger.info(f"Match ID {match_id} permanently finished as '{fixture_status_short}'. Dropping.")
                    del tracked_matches[match_id]
                continue

            # Convert raw API-Football response to normalized format for _post_ft_from_data
            home_team = match_details_raw.get("teams", {}).get("home", {}).get("name", "Home Team")
            away_team = match_details_raw.get("teams", {}).get("away", {}).get("name", "Away Team")
            raw_events = match_details_raw.get("events", [])

            normalized_events = []
            for e in raw_events:
                normalized_events.append({
                    "time": {"elapsed": e.get("time", {}).get("elapsed", "?")},
                    "player": {"name": e.get("player", {}).get("name", "N/A")},
                    "team": {"name": e.get("team", {}).get("name")},
                    "type": e.get("type"),
                    "detail": e.get("detail"),
                })

            normalized = {
                "teams": {"home": {"name": home_team}, "away": {"name": away_team}},
                "goals": match_details_raw.get("goals", {"home": "?", "away": "?"}),
                "events": normalized_events,
            }
            await _post_ft_from_data(bot, normalized)
            del tracked_matches[match_id]

