# modules/tennis_loop.py
import logging
from datetime import timedelta

from config import CHANNEL_ID
from modules import api_provider
from modules.bot_mode import is_silent
from modules.discord_poster import post_new_general_message, upsert_live_message
from modules.storage import load, save
from utils.time_utils import bot_now, to_bot_tz
from utils.tennis_formatter import (
    format_tennis_final_message,
    format_tennis_live_message,
    tennis_live_state_key,
)

logger = logging.getLogger(__name__)

pre_announced_ids: set[str] = set()
final_announced_ids: set[str] = set()
live_message_ids: dict[str, int] = {}
live_state_keys: dict[str, str] = {}
_TENNIS_STATE_FILE = "tennis_state.json"
_TENNIS_STATE_DEFAULT = {
    "pre_announced_ids": [],
    "final_announced_ids": [],
    "last_reset_date": None,
}
_state_loaded = False
_last_reset_date: str | None = None


def _is_tennis_local_today(start_time: str | None) -> bool:
    if not start_time:
        return False
    try:
        return to_bot_tz(start_time).date() == bot_now().date()
    except Exception:
        return False


def _is_tennis_local_tomorrow(start_time: str | None) -> bool:
    """Check if a match is scheduled for tomorrow in Bot Timezone."""
    if not start_time:
        return False
    try:
        match_date = to_bot_tz(start_time).date()
        tomorrow = bot_now().date() + timedelta(days=1)
        return match_date == tomorrow
    except Exception:
        return False


def _is_within_window(start_time: str | None, hours: int = 48) -> bool:
    """Check if a match start time is within the given hours from now."""
    if not start_time:
        return False
    try:
        match_dt = to_bot_tz(start_time)
        now = bot_now()
        return now <= match_dt <= now + timedelta(hours=hours)
    except Exception:
        return False


def _load_state_once() -> None:
    global _state_loaded, _last_reset_date
    if _state_loaded:
        return
    state = load(_TENNIS_STATE_FILE, _TENNIS_STATE_DEFAULT)
    pre_announced_ids.update(str(mid) for mid in state.get("pre_announced_ids", []))
    final_announced_ids.update(str(mid) for mid in state.get("final_announced_ids", []))
    _last_reset_date = state.get("last_reset_date")
    _state_loaded = True


def _persist_state() -> None:
    save(
        _TENNIS_STATE_FILE,
        {
            "pre_announced_ids": sorted(pre_announced_ids),
            "final_announced_ids": sorted(final_announced_ids),
            "last_reset_date": _last_reset_date,
        },
    )


def clear_tennis_state_today() -> None:
    global _last_reset_date
    _load_state_once()
    today_str = bot_now().date().isoformat()
    if _last_reset_date == today_str:
        logger.info("Tennis state already prepared for the local day; keeping dedup IDs.")
        return
    pre_announced_ids.clear()
    final_announced_ids.clear()
    live_message_ids.clear()
    live_state_keys.clear()
    _last_reset_date = today_str
    _persist_state()
    logger.info("Cleared tennis tracking state for the new day.")


async def run_tennis_loop(bot) -> None:
    if is_silent():
        return
    _load_state_once()

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
        track_id = str(match.get("canonical_id") or match_id)

        status_short = match.get("status", {}).get("short")
        start_time = match.get("start_time")

        # Only process matches that are today, tomorrow, or currently live/finished
        # This prevents posting matches from days ago or far in the future
        is_relevant = (
            status_short in ("LIVE", "FT") or
            _is_tennis_local_today(start_time) or
            _is_tennis_local_tomorrow(start_time) or
            _is_within_window(start_time, hours=48)
        )
        
        if not is_relevant:
            logger.debug(f"Skipping tennis match {match_id} - not in relevant time window")
            continue

        if status_short == "NS":
            # Pre-announcements intentionally disabled:
            # tennis fixtures are still shown in daily snapshot/!matches.
            continue

        if status_short == "LIVE":
            if track_id in final_announced_ids:
                # Ignore stale LIVE payloads that arrive after FT was already posted.
                continue
            live_ids_seen.add(track_id)
            state_key = tennis_live_state_key(match)
            if live_state_keys.get(track_id) == state_key:
                continue

            msg = await upsert_live_message(
                bot=bot,
                channel_id=CHANNEL_ID,
                message_id=live_message_ids.get(track_id),
                content=format_tennis_live_message(match),
            )
            if msg is not None:
                live_message_ids[track_id] = msg.id
                old_key = live_state_keys.get(track_id)
                live_state_keys[track_id] = state_key
                if old_key:
                    logger.info(f"Tennis live state changed for {track_id}: {old_key} -> {state_key}")
                else:
                    logger.info(f"Tennis live tracking started for {track_id}: {state_key}")
            continue

        if status_short == "FT":
            # Only post FT results for matches that started today or very recently
            # This prevents re-posting old FT matches after restart
            if not _is_tennis_local_today(start_time):
                logger.debug(f"Skipping FT match {match_id} - not started today")
                continue
            if track_id not in final_announced_ids:
                await post_new_general_message(
                    bot,
                    CHANNEL_ID,
                    content=format_tennis_final_message(match),
                )
                final_announced_ids.add(track_id)
                _persist_state()

            live_message_ids.pop(track_id, None)
            live_state_keys.pop(track_id, None)

    stale_live = [mid for mid in live_state_keys if mid not in live_ids_seen]
    for mid in stale_live:
        live_state_keys.pop(mid, None)
        live_message_ids.pop(mid, None)
