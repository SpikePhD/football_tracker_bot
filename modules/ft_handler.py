# modules/ft_handler.py

import logging
import discord
from datetime import datetime, timedelta

from config import CHANNEL_ID
from modules.bot_mode import is_silent
from modules.storage import load, save
from utils.time_utils import italy_now
from utils.event_formatter import format_match_events, event_completeness_note, normalize_api_football_events
from modules.discord_poster import post_new_general_message
from modules.football_memory import update_match_in_memory

logger = logging.getLogger(__name__)

tracked_matches = {}  # {match_id: {"exp_ft": datetime, "initial_score_at_tracking": {...}}}
_already_announced_ft: set = set()  # match IDs that have already received a FT announcement
_past_expected_live_logged: set[str] = set()
_FT_STATE_FILE = "ft_state.json"
_FT_STATE_DEFAULT = {
    "announced_ids": [],
    "last_reset_date": None,
}
_ft_state_loaded = False
_last_reset_date: str | None = None


def _load_ft_state_once() -> None:
    global _ft_state_loaded, _last_reset_date
    if _ft_state_loaded:
        return
    state = load(_FT_STATE_FILE, _FT_STATE_DEFAULT)
    _already_announced_ft.update(str(mid) for mid in state.get("announced_ids", []))
    _last_reset_date = state.get("last_reset_date")
    _ft_state_loaded = True


def _persist_ft_state() -> None:
    save(
        _FT_STATE_FILE,
        {
            "announced_ids": sorted(_already_announced_ft),
            "last_reset_date": _last_reset_date,
        },
    )


def _ensure_ft_state_current_date() -> None:
    global _last_reset_date
    _load_ft_state_once()
    today_str = italy_now().date().isoformat()
    if _last_reset_date == today_str:
        return
    _already_announced_ft.clear()
    _last_reset_date = today_str
    _persist_ft_state()
    logger.info("Cleared persisted FT announcement state for the new day.")


def mark_ft_announced(match_id) -> None:
    """Mark a Full-Time match as announced for today's Italy date."""
    if match_id is None:
        return
    _ensure_ft_state_current_date()
    mid = str(match_id)
    if mid in _already_announced_ft:
        return
    _already_announced_ft.add(mid)
    _persist_ft_state()


def clear_tracked_matches_today():
    global tracked_matches
    logger.info("ðŸ”„ Clearing 'tracked_matches' dictionary for the schedule cycle.")
    tracked_matches.clear()
    _past_expected_live_logged.clear()
    _ensure_ft_state_current_date()


def seed_already_announced_ft(fixtures: list) -> None:
    """
    Pre-populate the announced-FT set with matches already finished when the
    scheduler starts. Prevents re-announcing results already visible in the
    startup/morning broadcast message.
    """
    _ensure_ft_state_current_date()
    count = 0
    for match in fixtures:
        if match.get("fixture", {}).get("status", {}).get("short") == "FT":
            mid = match["fixture"].get("id")
            if mid:
                before_count = len(_already_announced_ft)
                mark_ft_announced(mid)
                if len(_already_announced_ft) == before_count:
                    continue
                count += 1
    if count:
        logger.info(f"ðŸŒ± Seeded {count} already-FT match IDs (will not re-announce).")


def track_match_for_ft(match_data: dict):
    """Register a live match to be checked for Full-Time status."""
    try:
        match_id = str(match_data["fixture"]["id"])
        if match_id in tracked_matches:
            return

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
            f"ðŸ†• Tracking {home} vs {away} (ID: {match_id}) for FT. "
            f"Expected check around {expected_ft_check_time.strftime('%H:%M')}"
        )
    except KeyError as e:
        logger.error(f"Error tracking match for FT: Missing key {e} in match_data. Data: {str(match_data)[:200]}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error in track_match_for_ft: {e}", exc_info=True)


def is_tracked_for_ft(match_id) -> bool:
    return str(match_id) in tracked_matches


_LIVE_STATUSES = {"1H", "HT", "2H", "ET", "PEN"}
_TERMINAL_NON_FT_STATUSES = {"PST", "CANC", "ABD", "AWD", "WO"}


def _fixture_status_short(match: dict | None) -> str | None:
    if not match:
        return None
    return match.get("fixture", {}).get("status", {}).get("short")


def _is_live_status(status_short: str | None) -> bool:
    return status_short in _LIVE_STATUSES


def _is_terminal_non_ft_status(status_short: str | None) -> bool:
    return status_short in _TERMINAL_NON_FT_STATUSES


# â”€â”€ Shared FT posting logic â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def _post_ft_from_data(bot: discord.Client, match_details: dict):
    """Build and send the FT result message from a normalized match dict."""
    from modules import api_provider  # late import to avoid circular dependency
    match_details = await api_provider.enrich_fixture_events(bot.http_session, match_details)

    # Update football memory with FT match data
    try:
        fixture = match_details.get("fixture", {})
        teams = match_details.get("teams", {})
        league = match_details.get("league", {})
        has_required_memory_keys = (
            fixture.get("id") is not None
            and fixture.get("status", {}).get("short") == "FT"
            and teams.get("home", {}).get("id") is not None
            and teams.get("away", {}).get("id") is not None
            and league.get("id") is not None
        )
        if has_required_memory_keys:
            await update_match_in_memory(bot.http_session, match_details)
            logger.info(f"Updated football memory with FT match: {fixture['id']}")
        else:
            logger.warning(
                "Skipping football memory update for FT post because normalized match payload "
                "is missing one or more required IDs."
            )
    except Exception as e:
        logger.error(f"Failed to update football memory for match {match_details.get('fixture', {}).get('id')}: {e}")
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
            f"Warning: FT event mismatch for {home_team} vs {away_team}: "
            f"score total={int(goals.get('home',0) or 0) + int(goals.get('away',0) or 0)}, "
            f"goal events={sum(1 for e in events if e.get('type') == 'Goal')}."
        )

    logger.info(f"ðŸ“¢ Posting FT result: {ft_message}")
    sent = await post_new_general_message(bot, CHANNEL_ID, content=ft_message)
    return sent is not None


# â”€â”€ Main FT detection (dual-path) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async def fetch_and_post_ft(bot: discord.Client):
    """
    Check tracked matches for Full-Time status and post results.

    ESPN mode (primary): reads finished matches from the cached scoreboard â€” no extra API call.
    Fallback mode: calls API-Football per tracked match after expected FT time.
    """
    if is_silent():
        return

    from modules import api_provider  # late import to avoid circular dependency

    _ensure_ft_state_current_date()
    current_time = italy_now()

    if api_provider.is_espn_healthy():
        # â”€â”€ ESPN path â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        all_matches = await api_provider.fetch_day(bot.http_session)
        matches_by_id = {str(m["fixture"]["id"]): m for m in all_matches}
        finished = [m for m in all_matches if _fixture_status_short(m) == "FT"]
        finished_by_id = {str(m["fixture"]["id"]): m for m in finished}

        for match_id, info in list(tracked_matches.items()):
            if current_time < info["exp_ft"]:
                continue  # too early to expect FT

            match_snapshot = matches_by_id.get(match_id)
            status_short = _fixture_status_short(match_snapshot)

            if status_short == "FT":
                match_details = finished_by_id[match_id]
                if await _post_ft_from_data(bot, match_details):
                    mark_ft_announced(match_id)
                del tracked_matches[match_id]
                _past_expected_live_logged.discard(match_id)
                continue

            if _is_live_status(status_short):
                if status_short in {"ET", "PEN"} and match_id not in _past_expected_live_logged:
                    logger.info(
                        f"Match ID {match_id} is past expected FT but still live "
                        f"with ESPN status '{status_short}'. Keeping FT tracking active."
                    )
                    _past_expected_live_logged.add(match_id)
                continue

            if _is_terminal_non_ft_status(status_short):
                logger.info(
                    f"Match ID {match_id} ended with terminal non-FT status "
                    f"'{status_short}'. Dropping from FT tracking."
                )
                del tracked_matches[match_id]
                _past_expected_live_logged.discard(match_id)
                continue

            if match_id not in matches_by_id or status_short is None:
                # Match not visible with a useful status; check if it's been suspiciously long.
                elapsed = (current_time - info["exp_ft"]).total_seconds()
                if elapsed > 1800:  # 30 min past expected FT with no result
                    logger.warning(
                        f"Warning: Match ID {match_id} is 30+ min past expected FT but not showing as FT "
                        f"in ESPN scoreboard. Dropping from tracking."
                    )
                    del tracked_matches[match_id]
                    _past_expected_live_logged.discard(match_id)
                continue

            elapsed = (current_time - info["exp_ft"]).total_seconds()
            if elapsed > 1800:
                logger.warning(
                    f"Warning: Match ID {match_id} is 30+ min past expected FT but has "
                    f"unexpected ESPN status '{status_short}'. Dropping from tracking."
                )
                del tracked_matches[match_id]
                _past_expected_live_logged.discard(match_id)

        # Orphan FT detection: matches that reached FT without being seen as LIVE
        # (e.g. bot was offline, or match finished before scheduler started polling).
        for match in finished:
            mid = str(match["fixture"]["id"])
            if mid in tracked_matches or mid in _already_announced_ft:
                continue
            logger.info(f"ðŸ†• [Orphan FT] Announcing untracked FT match {mid}.")
            if await _post_ft_from_data(bot, match):
                mark_ft_announced(mid)

    else:
        # â”€â”€ API-Football fallback path â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for match_id, info in list(tracked_matches.items()):
            if current_time < info["exp_ft"]:
                continue

            logger.info(f"ðŸ” [Fallback] Checking FT status for match ID {match_id}")

            payload = await api_provider.fetch_fixture(bot.http_session, match_id)
            if not payload:
                logger.warning(f"Warning: No payload for FT check of match ID {match_id}. Retrying next cycle.")
                continue

            api_response_list = payload.get("response")
            if not isinstance(api_response_list, list) or not api_response_list:
                logger.warning(f"Warning: Empty or invalid response for FT check of match ID {match_id}.")
                continue

            match_details_raw = api_response_list[0]
            fixture_status_short = match_details_raw.get("fixture", {}).get("status", {}).get("short")

            if fixture_status_short != "FT":
                logger.info(f"Match ID {match_id} status is '{fixture_status_short}', not FT yet.")
                if fixture_status_short in ("PST", "CANC", "ABD", "AWD", "WO"):
                    logger.info(f"Match ID {match_id} permanently finished as '{fixture_status_short}'. Dropping.")
                    del tracked_matches[match_id]
                continue

            # Convert raw API-Football response to normalized format for _post_ft_from_data
            home_team = match_details_raw.get("teams", {}).get("home", {}).get("name", "Home Team")
            away_team = match_details_raw.get("teams", {}).get("away", {}).get("name", "Away Team")
            raw_events = match_details_raw.get("events", [])

            normalized_events = normalize_api_football_events(raw_events)

            normalized = {
                "fixture": {
                    "id": match_details_raw.get("fixture", {}).get("id"),
                    "date": match_details_raw.get("fixture", {}).get("date"),
                    "status": {"short": fixture_status_short},
                },
                "league": {
                    "id": match_details_raw.get("league", {}).get("id"),
                },
                "teams": {
                    "home": {
                        "id": match_details_raw.get("teams", {}).get("home", {}).get("id"),
                        "name": home_team,
                    },
                    "away": {
                        "id": match_details_raw.get("teams", {}).get("away", {}).get("id"),
                        "name": away_team,
                    },
                },
                "goals": match_details_raw.get("goals", {"home": "?", "away": "?"}),
                "events": normalized_events,
            }
            if await _post_ft_from_data(bot, normalized):
                mark_ft_announced(match_id)
            del tracked_matches[match_id]



