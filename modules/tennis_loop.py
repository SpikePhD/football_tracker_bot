# modules/tennis_loop.py
import logging

from config import CHANNEL_ID
from modules import api_provider
from modules.bot_mode import is_silent
from modules.discord_poster import post_new_general_message, upsert_live_message
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

        if status_short == "NS":
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
                live_state_keys[match_id] = state_key
            continue

        if status_short == "FT":
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
