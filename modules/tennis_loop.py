# modules/tennis_loop.py
import logging
from datetime import timedelta

from config import CHANNEL_ID
from modules import api_provider
from modules.bot_mode import is_silent
from modules.discord_poster import post_new_general_message, upsert_live_message
from utils.time_utils import italy_now, parse_utc_to_italy
from utils.tennis_formatter import (
    format_tennis_final_message,
    format_tennis_live_message,
    format_tennis_pre_message,
    tennis_live_state_key,
)

logger = logging.getLogger(__name__)

pre_announced_ids: set[str] = set()
final_announced_ids: set[str] = set()
live_message_ids: dict[str, int] = {}
live_state_keys: dict[str, str] = {}


def _is_today_italy(start_time: str | None) -> bool:
    if not start_time:
        return False
    try:
        return parse_utc_to_italy(start_time).date() == italy_now().date()
    except Exception:
        return False


def _is_tomorrow_italy(start_time: str | None) -> bool:
    """Check if a match is scheduled for tomorrow in Italy timezone."""
    if not start_time:
        return False
    try:
        match_date = parse_utc_to_italy(start_time).date()
        tomorrow = italy_now().date() + timedelta(days=1)
        return match_date == tomorrow
    except Exception:
        return False


def _is_within_window(start_time: str | None, hours: int = 48) -> bool:
    """Check if a match start time is within the given hours from now."""
    if not start_time:
        return False
    try:
        match_dt = parse_utc_to_italy(start_time)
        now = italy_now()
        return now <= match_dt <= now + timedelta(hours=hours)
    except Exception:
        return False


def clear_tennis_state_today() -> None:
    pre_announced_ids.clear()
    final_announced_ids.clear()
    live_message_ids.clear()
    live_state_keys.clear()
    logger.info("Cleared tennis tracking state for the new day.")


async def run_tennis_loop(bot) -> None:
    if is_silent():
        return

    try:
        matches = await api_provider.fetch_tennis_day(bot.http_session)
    except Exception as e:
        logger.warning(f"Tennis loop fetch error: {e}", exc_info=True)
        return

    if not matches:
        return

    live_ids_seen: set[str] = set()

    for match in matches:
        match_id = match.get("match_id")
        if not match_id:
            continue

        status_short = match.get("status", {}).get("short")
        start_time = match.get("start_time")

        # Only process matches that are today, tomorrow, or currently live/finished
        # This prevents posting matches from days ago or far in the future
        is_relevant = (
            status_short in ("LIVE", "FT") or
            _is_today_italy(start_time) or
            _is_tomorrow_italy(start_time) or
            _is_within_window(start_time, hours=48)
        )
        
        if not is_relevant:
            logger.debug(f"Skipping tennis match {match_id} - not in relevant time window")
            continue

        if status_short == "NS":
            # Only announce upcoming matches that are today or tomorrow
            if not (_is_today_italy(start_time) or _is_tomorrow_italy(start_time)):
                continue
            if match_id not in pre_announced_ids:
                await post_new_general_message(
                    bot,
                    CHANNEL_ID,
                    content=format_tennis_pre_message(match),
                )
                pre_announced_ids.add(match_id)
            continue

        if status_short == "LIVE":
            live_ids_seen.add(match_id)
            state_key = tennis_live_state_key(match)
            if live_state_keys.get(match_id) == state_key:
                continue

            msg = await upsert_live_message(
                bot=bot,
                channel_id=CHANNEL_ID,
                message_id=live_message_ids.get(match_id),
                content=format_tennis_live_message(match),
            )
            if msg is not None:
                live_message_ids[match_id] = msg.id
                old_key = live_state_keys.get(match_id)
                live_state_keys[match_id] = state_key
                if old_key:
                    logger.info(f"Tennis live state changed for {match_id}: {old_key} -> {state_key}")
                else:
                    logger.info(f"Tennis live tracking started for {match_id}: {state_key}")
            continue

        if status_short == "FT":
            # Only post FT results for matches that started today or very recently
            # This prevents re-posting old FT matches after restart
            if not _is_today_italy(start_time):
                logger.debug(f"Skipping FT match {match_id} - not started today")
                continue
            if match_id not in final_announced_ids:
                await post_new_general_message(
                    bot,
                    CHANNEL_ID,
                    content=format_tennis_final_message(match),
                )
                final_announced_ids.add(match_id)

            live_message_ids.pop(match_id, None)
            live_state_keys.pop(match_id, None)

    stale_live = [mid for mid in live_state_keys if mid not in live_ids_seen]
    for mid in stale_live:
        live_state_keys.pop(mid, None)
        live_message_ids.pop(mid, None)
