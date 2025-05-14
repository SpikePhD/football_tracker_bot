# modules/ft_handler.py

import logging
import discord # For type hinting bot: discord.Client
from datetime import datetime, timedelta

from config import CHANNEL_ID
from utils.api_client import fetch_fixture_by_id # Still needed if post_initial_fts optimization is reverted
from utils.time_utils import italy_now
# MODIFIED: Import from the new discord_poster module
from modules.discord_poster import post_new_general_message

logger = logging.getLogger(__name__)

tracked_matches = {} # Stores matches being tracked for FT status

def clear_tracked_matches_today(): # NEW FUNCTION
    global tracked_matches
    logger.info("🔄 Clearing 'tracked_matches' dictionary for the new day.")
    tracked_matches.clear()

def track_match_for_ft(match_data: dict):
    """
    Registers a match to be checked for Full-Time status later.

    Args:
        match_data: The match dictionary object from the API.
    """
    try:
        match_id    = match_data['fixture']['id']
        kickoff_utc = match_data['fixture']['date']
        
        kickoff     = datetime.fromisoformat(kickoff_utc.replace('Z', '+00:00'))
        kickoff     = kickoff.astimezone(italy_now().tzinfo)

        expected_ft_check_time = kickoff + timedelta(minutes=112) 
        
        tracked_matches[match_id] = {
            "exp_ft": expected_ft_check_time,
            "initial_score_at_tracking":  match_data.get('goals', {'home': None, 'away': None}) 
        }

        home_team_name = match_data.get('teams', {}).get('home', {}).get('name', 'Home Team')
        away_team_name = match_data.get('teams', {}).get('away', {}).get('name', 'Away Team')
        logger.info(f"🆕 Tracking {home_team_name} vs {away_team_name} (ID: {match_id}) for FT. Expected check around {expected_ft_check_time.strftime('%H:%M')}")
    except KeyError as e:
        logger.error(f"Error tracking match for FT: Missing key {e} in match_data. Data: {str(match_data)[:200]}", exc_info=True)
    except Exception as e:
        logger.error(f"Unexpected error in track_match_for_ft for data {str(match_data)[:200]}: {e}", exc_info=True)


async def fetch_and_post_ft(bot: discord.Client):
    """
    Iterates through tracked matches, checks if they are past their expected FT time,
    fetches their status, and if FT, posts the result via discord_poster.
    """
    current_time = italy_now()
    for match_id, info in list(tracked_matches.items()): # Iterate over a copy
        if current_time < info["exp_ft"]:
            continue

        logger.info(f"🔍 Checking FT status for match ID {match_id} (Expected FT check time: {info['exp_ft'].strftime('%H:%M')})")
        
        payload = await fetch_fixture_by_id(bot.http_session, match_id) 
        
        if not payload:
            logger.warning(f"⚠️ No payload received from API for FT check of match ID {match_id}. Will retry next cycle if still tracked.")
            continue

        api_response_list = payload.get('response')
        if not isinstance(api_response_list, list) or not api_response_list:
            logger.warning(f"⚠️ 'response' field missing, not a list, or empty in API payload for FT check of match ID {match_id}. Payload: {str(payload)[:200]}")
            continue
            
        match_details = api_response_list[0]

        fixture_status_short = match_details.get('fixture', {}).get('status', {}).get('short')

        if fixture_status_short != "FT":
            logger.info(f"ℹ️ Match ID {match_id} status is '{fixture_status_short}', not 'FT'. Will re-check if still past expected FT.")
            if fixture_status_short in ("PST", "CANC", "ABD", "AWD", "WO"): 
                logger.info(f"Match ID {match_id} has permanently finished with non-FT status '{fixture_status_short}'. Removing from FT tracking.")
                del tracked_matches[match_id]
            continue

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

        logger.info(f"📢 Preparing to post FT result via DiscordPoster: {ft_message_content}")
        # MODIFIED: Call discord_poster to send the message
        await post_new_general_message(bot, CHANNEL_ID, content=ft_message_content)
        
        del tracked_matches[match_id]


async def post_initial_fts(fixtures_list: list, bot: discord.Client):
    """
    On startup/daily fetch, posts results for any games in the list that are already at FT.
    Uses discord_poster to send messages.
    This version attempts to use data directly from fixtures_list to save API calls.
    """
    logger.info(f"🔎 Checking {len(fixtures_list)} fetched fixtures for initial FT posts.")
    ft_posted_count = 0
    for initial_match_data in fixtures_list: # This is 'm' from scheduler.py
        fixture_details = initial_match_data.get('fixture', {})
        status_short = fixture_details.get('status', {}).get('short')

        if status_short != "FT":
            continue
        
        match_id = fixture_details.get('id')
        if not match_id:
            logger.warning("⚠️ Found a fixture marked FT in initial list but without an ID. Skipping.")
            continue

        # Check if this FT match was already processed by the main fetch_and_post_ft loop
        # (e.g., if schedule_day was called multiple times or bot restarted quickly)
        # This is a simple check; a more robust solution might involve a set of posted_initial_ft_ids.
        if match_id not in tracked_matches: # If it's still in tracked_matches, fetch_and_post_ft will handle it.
                                            # If not, it means it was either never tracked or already processed by fetch_and_post_ft.
                                            # We only want to post here if it's genuinely an *initial* FT not yet handled.
                                            # However, if a match finished *while bot was down* and wasn't live when bot restarted,
                                            # it wouldn't be in tracked_matches. This is the primary case this function handles.
            pass # Proceed to post


        ft_posted_count +=1
        logger.info(f"Found match ID {match_id} already FT in initial list. Preparing to post details directly from fetched data...")

        # --- Use data directly from initial_match_data ---
        data_to_use = initial_match_data 
        
        home_team = data_to_use.get('teams', {}).get('home', {}).get('name', 'Home Team')
        away_team = data_to_use.get('teams', {}).get('away', {}).get('name', 'Away Team')
        goals = data_to_use.get('goals', {'home': '?', 'away': '?'})
        # IMPORTANT: Check if 'events' are typically present and detailed enough in the 
        # `fetch_day_fixtures` response for already FT games.
        events = data_to_use.get('events', []) 

        if not events: # If events are missing in the summary, this FT post might be less detailed.
            logger.warning(f"ℹ️ Match ID {match_id} (FT in initial list) has no 'events' data in the summary. FT post will not include event details.")


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
        
        logger.info(f"📢 Preparing to post initial FT result via DiscordPoster: {initial_ft_message_content}")
        # MODIFIED: Call discord_poster to send the message
        await post_new_general_message(bot, CHANNEL_ID, content=initial_ft_message_content)

    if ft_posted_count == 0:
        logger.info("✅ No matches were already FT from the fetched list for initial posting.")
    else:
        logger.info(f"✅ Processed and posted {ft_posted_count} initially FT matches.")
