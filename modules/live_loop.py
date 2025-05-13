# modules/live_loop.py

import logging # MODIFIED: Import standard logging

from config import TRACKED_LEAGUE_IDS, CHANNEL_ID # TRACKED_LEAGUE_IDS can be removed if you removed the redundant check below
from utils.api_client import fetch_live_fixtures
from utils.time_utils import italy_now
# MODIFIED: Remove verbose_logger import
# from modules.verbose_logger import log_info, log_error 
from modules.ft_handler import track_match_for_ft
from modules.message_edit_tracker import safe_upsert

# MODIFIED: Get a logger instance for this module
logger = logging.getLogger(__name__)

# keep track of which live scores we've already posted this session
already_posted = set()

async def run_live_loop(bot):
    """
    Poll /fixtures?live=all, post/edit any new goals or red cards,
    and register each match for later FT checking.
    """
    now = italy_now()
    logger.info(f"[{now.strftime('%H:%M')}] 🌐 Querying live endpoint…") # MODIFIED

    matches = await fetch_live_fixtures(bot.http_session) 
    if not matches: 
        logger.info(f"[{now.strftime('%H:%M')}] 😕 No live fixtures returned or error in fetch.") # MODIFIED
        return

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        logger.error(f"[{now.strftime('%H:%M')}] ❌ Cannot find channel with ID {CHANNEL_ID}") # MODIFIED
        return

    for match in matches:
        # league_id = match['league']['id'] # This line and the following IF block are now redundant
        # if league_id not in TRACKED_LEAGUE_IDS: # api_client.fetch_live_fixtures already filters
        #     continue                           # You confirmed you removed this, which is good.

        match_id = match['fixture']['id']
        home = match['teams']['home']['name']
        away = match['teams']['away']['name']
        score = match['goals']
        key = f"{match_id}_{score['home']}-{score['away']}"