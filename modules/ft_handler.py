# modules/ft_handler.py

import logging
# MODIFIED: 'discord' import might become unnecessary if not used for other types.
# We'll re-evaluate after changes. For now, keep it if Pylance doesn't complain.
import discord 
from datetime import datetime, timedelta

from config import CHANNEL_ID
from utils.api_client import fetch_fixture_by_id
from utils.time_utils import italy_now
# MODIFIED: Import from the new discord_poster module
from modules.discord_poster import post_new_general_message

logger = logging.getLogger(__name__)

tracked_matches = {} # Stores matches being tracked for FT status

def track_match_for_ft(match_data: dict):
    """
    Registers a match to be checked for Full-Time status later.

    Args:
        match_data: The match dictionary object from the API.
    """
    try:
        match_id    = match_data['fixture']['id']
        kickoff_utc = match_data['fixture']['date']
        
        # Parse kickoff time and convert to Italy timezone
        kickoff     = datetime.fromisoformat(kickoff_utc.replace('Z', '+00:00'))
        kickoff     = kickoff.astimezone(italy_now().tzinfo)

        # Estimate when to start checking for FT (e.g., kickoff + 112 minutes)
        # This is just a heuristic to avoid checking too early.
        expected_ft_check_time = kickoff + timedelta(minutes=112) 
        
        tracked_matches[match_id] = {
            "exp_ft": expected_ft_check_time,
            # Storing initial score can be useful for context, though not used in current FT logic
            "initial_score_at_tracking":  match_data.get('goals', {'home': None, 'away': None}) 
        }

        home_team_name = match_data.get('teams', {}).get('home', {}).get('name', 'Home Team')
        away_team_name = match_data.get('teams', {}).get('away', {}).get('name', 'Away Team')
        logger.info(f"🆕 Tracking {home_team_name} vs {away_team_name} (ID: {match_id}) for FT. Expected check around {expected_ft_check_time.strftime('%H:%M')}")
    except KeyError as e:
        logger.error(f"Error tracking match for FT: Missing key {e} in match_data. Data: {str(match_data)[:200]}")
    except Exception as e:
        logger.error(f"Unexpected error in track_match_for_ft for data {str(match_data)[:200]}: {e}", exc_info=True)


async def fetch_and_post_ft(bot: discord.Client):
    """
    Iterates through tracked matches, checks if they are past their expected FT time,
    fetches their status, and if FT, posts the result via discord_poster.
    """
    current_time = italy_now()
    # Iterate over a copy of items in case the dictionary is modified during the loop (by del)
    for match_id, info in list(tracked_matches.items()):
        if current_time < info["exp_ft"]:
            continue # Not yet time to check this match

        logger.info(f"🔍 Checking FT status for match ID {match_id} (Expected FT check time: {info['exp_ft'].strftime('%H:%M')})")
        
        payload = await fetch_fixture_by_id(bot.http_session, match_id) 
        
        if not payload:
            logger.warning(f"⚠️ No payload received from API for FT check of match ID {match_id}. Will retry next cycle if still tracked.")
            continue

        api_response_list = payload.get('response')
        if not isinstance(api_response_list, list) or not api_response_list:
            logger.warning(f"⚠️ 'response' field missing, not a list, or empty in API payload for FT check of match ID {match_id}. Payload: {str(payload)[:200]}")
            continue
            
        match_details = api_response_list[0] # Expecting one match for the ID

        fixture_status_short = match_details.get('fixture', {}).get('status', {}).get('short')

        if fixture_status_short != "FT":
            logger.info(f"ℹ️ Match ID {match_id} status is '{fixture_status_short}', not 'FT'. Will re-check if still past expected FT.")
            # If match status indicates a permanent end other than FT (e.g., "CANC", "ABD"), remove from tracking.
            if fixture_status_short in ("PST", "CANC", "ABD", "AWD", "WO"): # Postponed, Cancelled, Abandoned, Awarded, WalkOver
                logger.info(f"Match ID {match_id} has permanently finished with non-FT status '{fixture_status_short}'. Removing from FT tracking.")
                del tracked_matches[match_id]
            continue

        # --- Match is FT, prepare and post message ---
        home_team = match_details.get('teams', {}).get('home', {}).get('name', 'Home Team')
        away_team = match_details.get('teams', {}).get('away', {}).get('name', 'Away Team')
        goals = match_details.get('goals', {'home': '?', 'away': '?'})
        events = match_details.get('events', [])

        detail_lines = []
        for e_event in events:
            minute = e_event.get('time', {}).get('elapsed', '?')
            player_info = e_event.get('player', {})
            player_name = player_info.get('name', 'N/A') if player_info else 'N/A'
            team_name_event = e_event.get('team', {}).get('name')

            side_tag = ""
            if team_name_event == home_team: side_tag = "(H)"
            elif team_name_event == away_team: side_tag = "(A)"
            
            event_type = e_event.get('type')
            event_detail_str = e_event.get('detail')

            if event_type == "Goal":
                extra_info = f" ({event_detail_str})" if event_detail_str and event_detail_str != "Normal Goal" else ""
                detail_lines.append(f"{minute}' – {player_name}{extra_info} {side_tag}")
            elif event_type == "Card" and event_detail_str == "Red Card":
                detail_lines.append(f"{minute}' – {player_name} {side_tag} (Red Card)")

        ft_message_content = f"FT: {home_team} {goals.get('home', '?')} – {goals.get('away', '?')} {away_team}"
        if detail_lines:
            ft_message_content += f" ({'; '.join(detail_lines)})"

        # MODIFIED: Call discord_poster to send the message
        logger.info(f"📢 Preparing to post FT result via DiscordPoster: {ft_message_content}")
        await post_new_general_message(bot, CHANNEL_ID, content=ft_message_content)
        # The actual "Sending new general message..." log will come from discord_poster.py

        # Successfully processed and posted FT, remove from tracking
        del tracked_matches[match_id]


async def post_initial_fts(fixtures_list: list, bot: discord.Client):
    """
    On startup/daily fetch, posts results for any games in the list that are already at FT.
    Uses discord_poster to send messages.
    """
    logger.info(f"🔎 Checking {len(fixtures_list)} fetched fixtures for initial FT posts.")
    ft_posted_count = 0
    for initial_match_data in fixtures_list:
        fixture_details = initial_match_data.get('fixture', {})
        status_short = fixture_details.get('status', {}).get('short')

        if status_short != "FT":
            continue
        
        match_id = fixture_details.get('id')
        if not match_id:
            logger.warning("⚠️ Found a fixture marked FT in initial list but without an ID. Skipping.")
            continue

        ft_posted_count +=1
        logger.info(f"Found match ID {match_id} already FT in initial list. Preparing to post details...")

        # --- Match is FT, prepare and post message using data from initial_match_data ---
        # OPTIMIZATION: We are using the data directly from `initial_match_data` (from fetch_day_fixtures)
        # This assumes `fetch_day_fixtures` provides enough detail for FT posts.
        # If not, a call to `fetch_fixture_by_id` would be needed here, as was previously done.
        
        home_team = initial_match_data.get('teams', {}).get('home', {}).get('name', 'Home Team')
        away_team = initial_match_data.get('teams', {}).get('away', {}).get('name', 'Away Team')
        goals = initial_match_data.get('goals', {'home': '?', 'away': '?'})
        events = initial_match_data.get('events', []) # Check if 'events' are present in fetch_day_fixtures for FT games

        detail_lines = []
        for e_event in events:
            minute = e_event.get('time', {}).get('elapsed', '?')
            player_info = e_event.get('player', {})
            player_name = player_info.get('name', 'N/A') if player_info else 'N/A'
            team_name_event = e_event.get('team', {}).get('name')

            side_tag = ""
            if team_name_event == home_team: side_tag = "(H)"
            elif team_name_event == away_team: side_tag = "(A)"
            
            event_type = e_event.get('type')
            event_detail_str = e_event.get('detail')

            if event_type == "Goal":
                extra_info = f" ({event_detail_str})" if event_detail_str and event_detail_str != "Normal Goal" else ""
                detail_lines.append(f"{minute}' – {player_name}{extra_info} {side_tag}")
            elif event_type == "Card" and event_detail_str == "Red Card":
                detail_lines.append(f"{minute}' – {player_name} {side_tag} (Red Card)")

        initial_ft_message_content = f"FT: {home_team} {goals.get('home', '?')} – {goals.get('away', '?')} {away_team}"
        if detail_lines:
            initial_ft_message_content += f" ({'; '.join(detail_lines)})"
        
        # MODIFIED: Call discord_poster to send the message
        logger.info(f"📢 Preparing to post initial FT result via DiscordPoster: {initial_ft_message_content}")
        await post_new_general_message(bot, CHANNEL_ID, content=initial_ft_message_content)

    if ft_posted_count == 0:
        logger.info("✅ No matches were already FT from the fetched list for initial posting.")
    else:
        logger.info(f"✅ Processed and posted {ft_posted_count} initially FT matches.")

