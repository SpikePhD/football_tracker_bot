# modules/live_loop.py

import logging
import asyncio

from config import CHANNEL_ID
from modules import api_provider
from modules.bot_mode import is_silent
from utils.time_utils import italy_now
from utils.event_formatter import format_match_events, event_completeness_note
from modules.ft_handler import is_tracked_for_ft, track_match_for_ft
from modules.discord_poster import upsert_live_message

logger = logging.getLogger(__name__)

# Track per-match live state in-memory (reset daily / on restart).
live_state_keys: dict[str, str] = {}
live_message_ids: dict[str, int] = {}
_live_loop_lock = asyncio.Lock()

_LIVE_STATUSES = {"1H", "HT", "2H", "ET", "PEN"}


def clear_already_posted_today():
    logger.info("Clearing live update state maps for the new day.")
    live_state_keys.clear()
    live_message_ids.clear()


def seed_already_posted(fixtures: list) -> None:
    """
    Pre-populate live_state_keys with the current snapshot of any in-progress
    matches from today's fixture list. Prevents the first run_live_loop call
    after startup from re-posting updates already shown in the startup message.
    """
    count = 0
    for match in fixtures:
        if match.get("fixture", {}).get("status", {}).get("short") not in _LIVE_STATUSES:
            continue
        match_id = str(match["fixture"]["id"])
        score = match.get("goals", {})
        events = match.get("events", [])
        key = f"{match_id}_{score.get('home')}-{score.get('away')}_{len(events)}"
        live_state_keys[match_id] = key
        count += 1
    if count:
        logger.info(f"Seeded {count} in-progress match snapshot(s) into live_state_keys.")


async def run_live_loop(bot):
    """
    Polls live fixtures, enriches event data before dedup checks, and then
    upserts one live message per match. Also registers matches for FT checking.
    """
    async with _live_loop_lock:
        if is_silent():
            return

        now = italy_now()
        logger.info(f"[{now.strftime('%H:%M')}] Querying live endpoint...")

        matches = await api_provider.fetch_live(bot.http_session)
        if not matches:
            logger.info(f"[{now.strftime('%H:%M')}] No live fixtures returned or fetch error.")
            return

        seen_live_ids: set[str] = set()

        for match in matches:
            match_id = str(match["fixture"]["id"])
            seen_live_ids.add(match_id)
            if not is_tracked_for_ft(match_id):
                track_match_for_ft(match)

            # Enrich first so dedup key and outgoing message reflect final data.
            enriched = await api_provider.enrich_fixture_events(bot.http_session, match)

            home = enriched["teams"]["home"]["name"]
            away = enriched["teams"]["away"]["name"]
            score = enriched["goals"]
            events = enriched.get("events", [])
            state_key = f"{match_id}_{score['home']}-{score['away']}_{len(events)}"

            previous_state = live_state_keys.get(match_id)
            if previous_state == state_key:
                continue

            event_strings = format_match_events(events, home, away)
            completeness = event_completeness_note(score, events)

            line_content = f"⚽ Football LIVE: {home} {score['home']} - {score['away']} {away}"
            if event_strings:
                line_content += " (" + "; ".join(event_strings) + ")"
            if completeness:
                line_content += completeness

            updated_message = await upsert_live_message(
                bot=bot,
                channel_id=CHANNEL_ID,
                message_id=live_message_ids.get(match_id),
                content=line_content,
            )

            if updated_message is not None:
                live_message_ids[match_id] = updated_message.id
                live_state_keys[match_id] = state_key
                logger.info(f"Live update upserted for match {match_id}: {line_content}")

        # Clean stale map entries when a match is no longer live.
        stale_ids = [mid for mid in live_state_keys if mid not in seen_live_ids]
        for mid in stale_ids:
            live_state_keys.pop(mid, None)
            live_message_ids.pop(mid, None)
