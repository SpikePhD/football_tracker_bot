# modules/live_loop.py

import logging

# 'discord' import is also removed as this module no longer sends directly.
from config import CHANNEL_ID
from modules import api_provider
from modules.bot_mode import is_silent
from utils.time_utils import italy_now
from utils.event_formatter import format_match_events
from modules.ft_handler import track_match_for_ft
from modules.discord_poster import post_live_update

logger = logging.getLogger(__name__)

# keep track of which live scores we've already posted this session
already_posted = set()

def clear_already_posted_today():
    global already_posted
    logger.info("🔄 Clearing 'already_posted' set for the new day.")
    already_posted.clear()

async def run_live_loop(bot):
    """
    Polls /fixtures?live=all, prepares update strings, and tells discord_poster
    to handle the posting/editing of live updates. Also registers matches for FT checking.
    """
    if is_silent():
        return

    now = italy_now()
    logger.info(f"[{now.strftime('%H:%M')}] 🌐 Querying live endpoint…")

    matches = await api_provider.fetch_live(bot.http_session)
    if not matches:
        logger.info(f"[{now.strftime('%H:%M')}] 😕 No live fixtures returned or error in fetch.")
        return

    # The channel object itself is not strictly needed here anymore,
    # as discord_poster.py will get it using CHANNEL_ID and the bot object.
    # However, having a check here can be an early exit if the CHANNEL_ID is misconfigured.
    # Let's keep a lightweight check or rely on discord_poster's error handling.
    # For now, we'll pass CHANNEL_ID and let discord_poster resolve it.

    for match in matches:
        match_id = match['fixture']['id']
        home = match['teams']['home']['name']
        away = match['teams']['away']['name']
        score = match['goals']
        events = match.get('events', [])
        key = f"{match_id}_{score['home']}-{score['away']}_{len(events)}"

        if key in already_posted:
            continue

        event_strings = format_match_events(events, home, away)

        already_posted.add(key)
        track_match_for_ft(match)

        line_content = f"{home} {score['home']} - {score['away']} {away}"
        if event_strings:
            line_content += " (" + "; ".join(event_strings) + ")"

        logger.info(f"📢 Posting live update: {line_content}")
        await post_live_update(bot, CHANNEL_ID, content=line_content)
