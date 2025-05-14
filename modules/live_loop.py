# modules/live_loop.py

import logging 

# MODIFIED: TRACKED_LEAGUE_IDS import removed as api_client filters.
# 'discord' import is also removed as this module no longer sends directly.
from config import CHANNEL_ID 
from utils.api_client import fetch_live_fixtures
from utils.time_utils import italy_now
from modules.ft_handler import track_match_for_ft
# MODIFIED: Import from the new discord_poster module
from modules.discord_poster import post_live_update 

logger = logging.getLogger(__name__)

# keep track of which live scores we've already posted this session
already_posted = set()

async def run_live_loop(bot):
    """
    Polls /fixtures?live=all, prepares update strings, and tells discord_poster
    to handle the posting/editing of live updates. Also registers matches for FT checking.
    """
    now = italy_now()
    logger.info(f"[{now.strftime('%H:%M')}] 🌐 Querying live endpoint…")

    matches = await fetch_live_fixtures(bot.http_session) 
    if not matches: 
        logger.info(f"[{now.strftime('%H:%M')}] 😕 No live fixtures returned or error in fetch.")
        return

    # The channel object itself is not strictly needed here anymore, 
    # as discord_poster.py will get it using CHANNEL_ID and the bot object.
    # However, having a check here can be an early exit if the CHANNEL_ID is misconfigured.
    # Let's keep a lightweight check or rely on discord_poster's error handling.
    # For now, we'll pass CHANNEL_ID and let discord_poster resolve it.

    for match in matches:
        # Redundant league filtering (if league_id not in TRACKED_LEAGUE_IDS)
        # should have been removed in a previous step, as api_client.fetch_live_fixtures now filters.

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
        track_match_for_ft(match) # This remains important

        # Prepare the content string for the update
        line_content = f"{home} {score['home']} - {score['away']} {away}"
        if event_strings:
            line_content += " (" + "; ".join(event_strings) + ")"

        # MODIFIED: Call the new discord_poster function
        # It will handle getting the channel object, deciding to edit/send new, and actual sending.
        logger.info(f"📢 Preparing to post/edit live update via DiscordPoster: {line_content}")
        await post_live_update(bot, CHANNEL_ID, content=line_content)
        # The actual "Editing message..." or "Sending new message..." log will now come from discord_poster.py
