# modules/tennis_loop.py
import logging
from datetime import timedelta

from config import CHANNEL_ID, TENNIS_FINISHED_RETENTION_HOURS, TENNIS_PRE_ANNOUNCE_HOURS
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
from utils.tennis_lifecycle import tennis_final_data_ready, tennis_final_within_retention

logger = logging.getLogger(__name__)

start_watch_prepared_ids: set[str] = set()
final_announced_ids: set[str] = set()
live_message_ids: dict[str, int] = {}
live_state_keys: dict[str, str] = {}
tennis_match_records: dict[str, dict] = {}
_TENNIS_STATE_FILE = "tennis_state.json"
_TENNIS_STATE_DEFAULT = {
    "version": 2,
    "matches": {},
}
_state_loaded = False


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


def should_prepare_tennis_start_watch(match: dict, now=None) -> bool:
    """Return true when an NS tennis match should keep tennis polling awake."""
    if match.get("status", {}).get("short") != "NS":
        return False

    start_time = match.get("start_time")
    if not start_time:
        return False

    try:
        match_dt = to_bot_tz(start_time)
        current = now or bot_now()
        return current <= match_dt <= current + timedelta(hours=TENNIS_PRE_ANNOUNCE_HOURS)
    except Exception:
        return False


def _load_state_once() -> None:
    global _state_loaded
    if _state_loaded:
        return
    state = load(_TENNIS_STATE_FILE, _TENNIS_STATE_DEFAULT)
    if not isinstance(state, dict):
        logger.error("Tennis state root is not an object; migrating it to an empty version 2 state.")
        state = {}
    matches = state.get("matches")
    migrated = not (state.get("version") == 2 and isinstance(matches, dict))
    normalized_dirty = False
    loaded_at = bot_now().isoformat()

    if migrated:
        matches = {}
        prepared_ids = {
            str(mid) for mid in state.get("start_watch_prepared_ids", [])
        }
        # Legacy key from when this state incorrectly implied a Discord post happened.
        prepared_ids.update(str(mid) for mid in state.get("pre_announced_ids", []))
        final_ids = {str(mid) for mid in state.get("final_announced_ids", [])}
        for track_id in prepared_ids | final_ids:
            matches[track_id] = {
                "start_watch_prepared": track_id in prepared_ids,
                "final_announced": track_id in final_ids,
                "live_message_id": None,
                "migrated_at": loaded_at,
            }

    for raw_track_id, raw_record in matches.items():
        if not isinstance(raw_record, dict):
            continue
        track_id = str(raw_track_id)
        record = dict(raw_record)
        record["start_watch_prepared"] = bool(record.get("start_watch_prepared"))
        record["final_announced"] = bool(record.get("final_announced"))
        message_id = record.get("live_message_id")
        try:
            record["live_message_id"] = int(message_id) if message_id is not None else None
        except (TypeError, ValueError):
            record["live_message_id"] = None
        if (
            record["final_announced"]
            and not record.get("start_time")
            and not record.get("last_seen_at")
            and not record.get("migrated_at")
        ):
            record["migrated_at"] = loaded_at
            normalized_dirty = True
        tennis_match_records[track_id] = record
        if record["start_watch_prepared"]:
            start_watch_prepared_ids.add(track_id)
        if record["final_announced"]:
            final_announced_ids.add(track_id)
        if record["live_message_id"] is not None and not record["final_announced"]:
            live_message_ids[track_id] = record["live_message_id"]

    if migrated or normalized_dirty:
        _persist_state()
    if migrated:
        logger.info("Migrated tennis state to version 2 (%d match records).", len(matches))
    elif normalized_dirty:
        logger.info("Added retention metadata to existing tennis state records.")
    _state_loaded = True


def ensure_tennis_state_loaded() -> None:
    """Load persisted tennis dedupe/message state before scheduler decisions."""
    _load_state_once()


def _remember_match(track_id: str, match: dict, status_short: str | None) -> dict:
    record = tennis_match_records.setdefault(track_id, {})
    record["match_id"] = str(match.get("match_id") or track_id)
    record["start_time"] = match.get("start_time")
    record["last_status"] = status_short
    record["last_seen_at"] = bot_now().isoformat()
    return record


def _persist_state() -> None:
    all_ids = (
        set(tennis_match_records)
        | start_watch_prepared_ids
        | final_announced_ids
        | set(live_message_ids)
    )
    matches: dict[str, dict] = {}
    for track_id in sorted(all_ids):
        record = dict(tennis_match_records.get(track_id, {}))
        record["start_watch_prepared"] = track_id in start_watch_prepared_ids
        record["final_announced"] = track_id in final_announced_ids
        record["live_message_id"] = live_message_ids.get(track_id)
        matches[track_id] = record
    tennis_match_records.clear()
    tennis_match_records.update(matches)
    save(
        _TENNIS_STATE_FILE,
        {
            "version": 2,
            "matches": matches,
        },
    )


def prune_tennis_state(now=None) -> int:
    """Remove terminal records once they can no longer produce a retry announcement."""
    _load_state_once()
    current = now or bot_now()
    stale_ids: list[str] = []
    for track_id, record in tennis_match_records.items():
        if not record.get("final_announced"):
            continue
        start_time = record.get("start_time")
        if start_time:
            terminal = {"start_time": start_time, "status": {"short": "FT"}}
            if not tennis_final_within_retention(terminal, current):
                stale_ids.append(track_id)
            continue
        reference = record.get("last_seen_at") or record.get("migrated_at")
        if not reference:
            continue
        try:
            reference_dt = to_bot_tz(reference)
        except Exception:
            continue
        if current - reference_dt > timedelta(hours=TENNIS_FINISHED_RETENTION_HOURS):
            stale_ids.append(track_id)

    for track_id in stale_ids:
        tennis_match_records.pop(track_id, None)
        start_watch_prepared_ids.discard(track_id)
        final_announced_ids.discard(track_id)
        live_message_ids.pop(track_id, None)
        live_state_keys.pop(track_id, None)
    if stale_ids:
        _persist_state()
        logger.info("Pruned %d expired tennis lifecycle record(s).", len(stale_ids))
    return len(stale_ids)


def clear_tennis_state_today() -> None:
    """Legacy scheduler hook; durable tennis lifecycle state is rolling, not daily."""
    _load_state_once()
    live_state_keys.clear()
    logger.info("Kept rolling tennis lifecycle state across the local-day boundary.")


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
            _is_within_window(start_time, hours=max(48, TENNIS_PRE_ANNOUNCE_HOURS)) or
            should_prepare_tennis_start_watch(match)
        )
        
        if not is_relevant:
            logger.debug(f"Skipping tennis match {match_id} - not in relevant time window")
            continue

        if status_short == "NS":
            if track_id in start_watch_prepared_ids or not should_prepare_tennis_start_watch(match):
                continue
            _remember_match(track_id, match, status_short)
            start_watch_prepared_ids.add(track_id)
            _persist_state()
            logger.info(f"Tennis start-watch prepared for {track_id}.")
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
                _remember_match(track_id, match, status_short)
                old_key = live_state_keys.get(track_id)
                live_state_keys[track_id] = state_key
                _persist_state()
                if old_key:
                    logger.info(f"Tennis live state changed for {track_id}: {old_key} -> {state_key}")
                else:
                    logger.info(f"Tennis live tracking started for {track_id}: {state_key}")
            continue

        if status_short == "FT":
            if not tennis_final_within_retention(match, bot_now()):
                logger.debug(f"Skipping FT match {match_id} - outside finished retention window")
                continue
            if not tennis_final_data_ready(match):
                logger.info(
                    "Deferring tennis FT for %s because final data is incomplete "
                    "(winner=%r, sets=%r).",
                    track_id,
                    match.get("winner"),
                    match.get("sets"),
                )
                continue
            if track_id not in final_announced_ids:
                sent = await post_new_general_message(
                    bot,
                    CHANNEL_ID,
                    content=format_tennis_final_message(match),
                )
                if sent is None:
                    logger.warning(
                        "Tennis FT announcement failed for %s; leaving it pending for retry.",
                        track_id,
                    )
                    continue
                _remember_match(track_id, match, status_short)
                final_announced_ids.add(track_id)
                live_message_ids.pop(track_id, None)
                live_state_keys.pop(track_id, None)
                _persist_state()

            else:
                before = dict(tennis_match_records.get(track_id, {}))
                _remember_match(track_id, match, status_short)
                if tennis_match_records.get(track_id) != before:
                    _persist_state()

            live_state_keys.pop(track_id, None)

    stale_live = [mid for mid in live_state_keys if mid not in live_ids_seen]
    for mid in stale_live:
        live_state_keys.pop(mid, None)
    prune_tennis_state(bot_now())
