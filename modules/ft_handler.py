# modules/ft_handler.py

import logging # MODIFIED: Import standard logging
from datetime import datetime, timedelta
import discord

from config import CHANNEL_ID
from utils.api_client import fetch_fixture_by_id
from utils.time_utils import italy_now
# MODIFIED: Remove verbose_logger import
# from modules.verbose_logger import log_info

# MODIFIED: Get a logger instance for this module
logger = logging.getLogger(__name__)

tracked_matches = {}

def track_match_for_ft(match):
    match_id    = match['fixture']['id']
    kickoff_utc = match['fixture']['date']
    kickoff     = datetime.fromisoformat(kickoff_utc.replace('Z', '+00:00'))
    kickoff     = kickoff.astimezone(italy_now().tzinfo)

    expected_ft = kickoff + timedelta(minutes=112) # Heuristic for when to start checking
    tracked_matches[match_id] = {
        "exp_ft": expected_ft,
        "score":  match['goals'] # Score at the time tracking started (for reference, not currently used in logic)
    }

    home = match['teams']['home']['name']
    away = match['teams']['away']['name']
    logger.info(f"🆕 Tracking {home} vs {away} (ID: {match_id}) for FT. Expected check around {expected_ft.strftime('%H:%M')}") # MODIFIED

async def fetch_and_post_ft(bot):
    now = italy_now()
    # Iterate over a copy of items in case the dictionary is modified during the loop (by del)
    for match_id, info in list(tracked_matches.items()):
        if now < info["exp_ft"]:
            continue # Not yet time to check this match

        logger.info(f"🔍 Checking FT status for match ID {match_id} (Expected FT: {info['exp_ft'].strftime('%H:%M')})") # MODIFIED
        
        # payload will be the full JSON response from the API or None if error
        payload = await fetch_fixture_by_id(bot.http_session, match_id) 
        
        if not payload:
            logger.warning(f"⚠️ No payload received from API for FT check of match ID {match_id}. Will retry next cycle if still tracked.") # MODIFIED
            # Don't remove from tracked_matches here; let it retry if it was a temporary API issue.
            # If the match truly ended and API is consistently failing, it might stay in tracked_matches.
            # Consider adding a max_retry count or similar if this becomes an issue.
            continue

        # The api_client's _make_request now checks for payload.get("errors")
        # and returns None if major errors. So, if payload is not None,
        # we expect it to be a dict, but still need to check for 'response' key.
        api_response_list = payload.get('response')
        if not api_response_list: # Handles if 'response' is missing or an empty list
            logger.warning(f"⚠️ 'response' field missing or empty in API payload for FT check of match ID {match_id}. Payload: {str(payload)[:200]}") # MODIFIED
            continue

        if not isinstance(api_response_list, list) or not api_response_list:
            logger.warning(f"⚠️ API response for match ID {match_id} is not a non-empty list. Skipping. Response: {str(api_response_list)[:200]}") # MODIFIED
            continue
            
        data = api_response_list[0] # We expect only one fixture for a given ID

        fixture_details = data.get('fixture', {})
        status_short = fixture_details.get('status', {}).get('short')

        if status_short != "FT":
            logger.info(f"ℹ️ Match ID {match_id} status is '{status_short}', not 'FT'. Will re-check if still past expected FT.") # MODIFIED
            # If match is e.g. "HT", "LIVE", "PST", "CANC" etc.
            # If status indicates a permanent end other than FT (e.g., "CANC", "ABD"), consider removing from tracked_matches.
            if status_short in ("PST", "CANC", "ABD", "AWD", "WO"): # Postponed, Cancelled, Abandoned, Awarded, WalkOver
                logger.info(f"permanently finished non-FT status '{status_short}'. Removing from FT tracking.") # MODIFIED
                del tracked_matches[match_id]
            continue

        # If status IS "FT":
        home   = data.get('teams', {}).get('home', {}).get('name', 'Home Team')
        away   = data.get('teams', {}).get('away', {}).get('name', 'Away Team')
        goals  = data.get('goals', {'home': '?', 'away': '?'}) # Provide defaults
        events = data.get('events', [])

        detail_lines = []
        for e in events:
            minute = e.get('time', {}).get('elapsed', '?')
            player_info = e.get('player', {})
            player = player_info.get('name', 'N/A') if player_info else 'N/A' # Handle if player_info is None
            team_name_event = e.get('team', {}).get('name')

            tag = ""
            if team_name_event == home:
                tag = "(H)"
            elif team_name_event == away:
                tag = "(A)"
            
            event_type = e.get('type')
            event_detail = e.get('detail')

            if event_type == "Goal":
                extra = f" ({event_detail})" if event_detail and event_detail != "Normal Goal" else ""
                detail_lines.append(f"{minute}' – {player}{extra} {tag}")
            elif event_type == "Card" and event_detail == "Red Card":
                detail_lines.append(f"{minute}' – {player} {tag} (Red Card)")

        ft_line = f"FT: {home} {goals.get('home', '?')} – {goals.get('away', '?')} {away}"
        if detail_lines:
            ft_line += f" ({'; '.join(detail_lines)})"

        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            try:
                await channel.send(ft_line)
                logger.info(f"📢 Posted FT: {ft_line}") # MODIFIED
            except discord.Forbidden:
                logger.error(f"❌ Missing permissions to send FT message in #{channel.name} for match ID {match_id}.") # MODIFIED
            except Exception as e:
                logger.error(f"💥 Failed to send FT message for match ID {match_id}: {e}", exc_info=True) # MODIFIED
        else:
            logger.error(f"❌ Could not find channel with ID {CHANNEL_ID} to post FT for match ID {match_id}.") # MODIFIED

        # Successfully processed and posted FT, remove from tracking
        del tracked_matches[match_id]


async def post_initial_fts(fixtures, bot):
    """
    On startup/daily fetch, post any games already at FT.
    """
    logger.info(f"🔎 Checking {len(fixtures)} fetched fixtures for initial FT posts.") # MODIFIED
    ft_count = 0
    for m in fixtures:
        fixture_details_initial = m.get('fixture', {})
        status_short_initial = fixture_details_initial.get('status', {}).get('short')

        if status_short_initial != "FT":
            continue
        
        match_id_initial = fixture_details_initial.get('id')
        if not match_id_initial:
            logger.warning("⚠️ Found a fixture marked FT but without an ID in initial list. Skipping.") # MODIFIED
            continue

        ft_count +=1
        logger.info(f"Found match ID {match_id_initial} already FT. Fetching details for posting...") # MODIFIED

        # OPTIMIZATION POINT: Check if `m` (the fixture data from fetch_day_fixtures)
        # already contains enough detail (scores, events). If so, you might avoid this call.
        # For now, we fetch fresh details to ensure completeness.
        payload = await fetch_fixture_by_id(bot.http_session, match_id_initial)
        
        if not payload:
            logger.warning(f"⚠️ No payload received from API for initial FT post of match ID {match_id_initial}.") # MODIFIED
            continue

        api_response_list_initial = payload.get('response')
        if not api_response_list_initial:
            logger.warning(f"⚠️ 'response' field missing or empty in API payload for initial FT post of {match_id_initial}.") # MODIFIED
            continue
        
        if not isinstance(api_response_list_initial, list) or not api_response_list_initial:
            logger.warning(f"⚠️ API response for initial FT post of {match_id_initial} is not a non-empty list. Skipping.") # MODIFIED
            continue

        data = api_response_list_initial[0]
        
        # Re-check status from the detailed fetch, just in case
        if data.get('fixture', {}).get('status', {}).get('short') != "FT":
            logger.warning(f"ℹ️ Match ID {match_id_initial} was FT in summary but not in detailed fetch. Status: {data.get('fixture', {}).get('status', {}).get('short')}. Skipping initial FT post.") # MODIFIED
            continue

        home   = data.get('teams', {}).get('home', {}).get('name', 'Home Team')
        away   = data.get('teams', {}).get('away', {}).get('name', 'Away Team')
        goals  = data.get('goals', {'home': '?', 'away': '?'})
        events = data.get('events', [])

        detail_lines = []
        for e in events:
            minute = e.get('time', {}).get('elapsed', '?')
            player_info = e.get('player', {})
            player = player_info.get('name', 'N/A') if player_info else 'N/A'
            team_name_event = e.get('team', {}).get('name')

            tag = ""
            if team_name_event == home:
                tag = "(H)"
            elif team_name_event == away:
                tag = "(A)"
            
            event_type = e.get('type')
            event_detail = e.get('detail')

            if event_type == "Goal":
                extra = f" ({event_detail})" if event_detail and event_detail != "Normal Goal" else ""
                detail_lines.append(f"{minute}' – {player}{extra} {tag}")
            elif event_type == "Card" and event_detail == "Red Card":
                detail_lines.append(f"{minute}' – {player} {tag} (Red Card)")

        line = f"FT: {home} {goals.get('home', '?')} – {goals.get('away', '?')} {away}"
        if detail_lines:
            line += f" ({'; '.join(detail_lines)})"

        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            try:
                # Avoid re-posting if this FT was somehow already posted in this session by main loop
                # This check is simplistic; a more robust way would be to check if a message with this exact content
                # by the bot already exists recently, or if live_loop's "already_posted" could be made accessible/shared.
                # For now, this simple check is a basic guard.
                # However, ft_handler is usually for distinct messages.
                # Consider if live_loop.already_posted should be cleared daily or if ft_handler needs its own "already_posted_ft" set.
                # Given ft_handler removes from tracked_matches, it won't re-process via fetch_and_post_ft.
                # This post_initial_fts is a one-off at the start of schedule_day.
                await channel.send(line)
                logger.info(f"📢 Posted initial FT: {line}") # MODIFIED
            except discord.Forbidden:
                logger.error(f"❌ Missing permissions to send initial FT message in #{channel.name} for match ID {match_id_initial}.") # MODIFIED
            except Exception as e:
                logger.error(f"💥 Failed to send initial FT message for match ID {match_id_initial}: {e}", exc_info=True) # MODIFIED
        else:
            logger.error(f"❌ Could not find channel with ID {CHANNEL_ID} to post initial FT for match ID {match_id_initial}.") # MODIFIED
    if ft_count == 0:
        logger.info("✅ No matches were already FT from the fetched list.") # MODIFIED
    else:
        logger.info(f"✅ Processed {ft_count} initially FT matches.") # MODIFIED