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
        key = f"{match_id}_{score['home']}-{score['away']}"