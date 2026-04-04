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

_LIVE_STATUSES = {"1H", "HT", "2H", "ET", "PEN"}

def clear_already_posted_today():
    global already_posted
    logger.info("🔄 Clearing 'already_posted' set for the new day.")
    already_posted.clear()

def seed_already_posted(fixtures: list) -> None:
    """
    Pre-populate already_posted with the current snapshot of any in-progress
    matches from today's fixture list. Prevents the first run_live_loop call
    after startup from re-posting updates already shown in the startup message.
    """
    count = 0
    for match in fixtures:
        if match.get("fixture", {}).get("status", {}).get("short") not in _LIVE_STATUSES:
            continue
        match_id = match["fixture"]["id"]
        score = match.get("goals", {})
        events = match.get("events", [])
        key = f"{match_id}_{score.get('home')}-{score.get('away')}_{len(events)}"
        already_posted.add(key)
        count += 1
    if count:
        logger.info(f"🌱 Seeded {count} in-progress match snapshot(s) into already_posted.")

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
