# modules/live_loop.py

import logging
from config import CHANNEL_ID
from utils.api_client import fetch_live_fixtures
from utils.time_utils import italy_now
from modules.ft_handler import track_match_for_ft
from modules.message_edit_tracker import safe_upsert
logger = logging.getLogger(__name__)
already_posted = set()

async def run_live_loop(bot):
    """
    Poll /fixtures?live=all, post/edit any new goals or red cards,
    and register each match for later FT checking.
    """
    now = italy_now()
    logger.info(f"[{now.strftime('%H:%M')}] 🌐 Querying live endpoint…")

    matches = await fetch_live_fixtures(bot.http_session) 
    if not matches: 
        logger.info(f"[{now.strftime('%H:%M')}] 😕 No live fixtures returned or error in fetch.") 
        return

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        logger.error(f"[{now.strftime('%H:%M')}] ❌ Cannot find channel with ID {CHANNEL_ID}") 
        return

    for match in matches:
        match_id = match['fixture']['id']
        home = match['teams']['home']['name']
        away = match['teams']['away']['name']
        score = match['goals']
        key = f"{match_id}_{score['home']}-{score['away']}"# modules/live_loop.py (Temporary version for debugging - direct send)

import logging 

from config import CHANNEL_ID # TRACKED_LEAGUE_IDS import removed as filtering is done by api_client
from utils.api_client import fetch_live_fixtures
from utils.time_utils import italy_now
from modules.ft_handler import track_match_for_ft
# MODIFIED: Comment out or remove message_edit_tracker import
# from modules.message_edit_tracker import safe_upsert 

logger = logging.getLogger(__name__)
already_posted = set()

async def run_live_loop(bot):
    """
    Poll /fixtures?live=all, POST any new goals or red cards (debugging direct send),
    and register each match for later FT checking.
    """
    now = italy_now()
    logger.info(f"[{now.strftime('%H:%M')}] 🌐 Querying live endpoint…")

    matches = await fetch_live_fixtures(bot.http_session) 
    if not matches: 
        logger.info(f"[{now.strftime('%H:%M')}] 😕 No live fixtures returned or error in fetch.")
        return

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        logger.error(f"[{now.strftime('%H:%M')}] ❌ Cannot find channel with ID {CHANNEL_ID}")
        return

    for match in matches:
        # Redundant league filtering already removed, which is good.

        match_id = match['fixture']['id']
        home = match['teams']['home']['name']
        away = match['teams']['away']['name']
        score = match['goals']
        key = f"{match_id}_{score['home']}-{score['away']}"

        if key in already_posted:
            continue
        
        events = match.get('events', [])
        event_strings = []
        
        for e in events:
            minute = e['time']['elapsed']
            player = e['player']['name']
            side = "(H)" if e['team']['name'] == home else "(A)"

            if e['type'] == 'Goal':
                detail = e['detail']
                tag = f" ({detail})" if detail != "Normal Goal" else ""
                event_strings.append(f"{minute}' - {player}{tag} {side}")
            elif e['type'] == 'Card' and e['detail'] == 'Red Card':
                event_strings.append(f"{minute}' - {player} (Red Card) {side}")
        
        already_posted.add(key)
        track_match_for_ft(match)

        line = f"{home} {score['home']} - {score['away']} {away}"
        if event_strings:
            line += " (" + "; ".join(event_strings) + ")"

        # MODIFIED: Revert to channel.send() for debugging
        try:
            if channel: # Ensure channel is still valid
                await channel.send(line)
                logger.info(f"📢 Posted (direct send for debug) live update: {line}") # MODIFIED log message
            else:
                logger.error(f"[{now.strftime('%H:%M')}] ❌ Channel became invalid before sending for match {match_id}")
        except discord.Forbidden:
            logger.error(f"❌ Missing permissions to send message in #{channel.name} for match {match_id}.")
        except Exception as e:
            logger.error(f"💥 Failed to send message for match {match_id}: {e}", exc_info=True)