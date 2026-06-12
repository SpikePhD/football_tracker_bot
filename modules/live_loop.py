# modules/live_loop.py

import logging
import asyncio
from datetime import timedelta

from config import CHANNEL_ID
from modules import api_provider, match_lifecycle, match_state
from modules.bot_mode import is_silent
from utils.time_utils import bot_now, utc_now
from utils.event_formatter import format_match_events, format_shootout_segments, event_completeness_note
from modules.ft_handler import is_tracked_for_ft, track_match_for_ft
from modules.discord_poster import upsert_live_message

logger = logging.getLogger(__name__)

# Track per-match live state in-memory (reset daily / on restart).
live_state_keys: dict[str, str] = {}
live_message_ids: dict[str, int] = {}
_live_loop_lock = asyncio.Lock()
_missing_since: dict[str, object] = {}
_last_observed: dict[str, dict] = {}
_regression_hold: dict[str, dict] = {}
_last_sent_content: dict[str, tuple[str, object]] = {}

_MISSING_GRACE_SEC = 180
_REGRESSION_CONFIRM_TICKS = 3


def _render_live_line(match: dict, home: str, away: str, score: dict, events: list, status_short: str) -> str:
    event_strings = format_match_events(events, home, away)
    completeness = event_completeness_note(score, events)

    if status_short == "PEN":
        line_content = f"⚽ Football LIVE [PEN]: {home} {score['home']} - {score['away']} {away}"
    else:
        line_content = f"⚽ Football LIVE: {home} {score['home']} - {score['away']} {away}"
    if event_strings:
        line_content += " (" + "; ".join(event_strings) + ")"
    shootout_segments = format_shootout_segments(match, final=False)
    if shootout_segments:
        line_content += " | " + " | ".join(shootout_segments)
    if completeness:
        line_content += completeness
    return line_content


def _regression_guard_context(
    match_id: str,
    previous_observed: dict,
    current_score: dict,
    current_elapsed: int | None,
    current_events_count: int,
    state_key: str,
    reason: str,
) -> str:
    """Return a compact previous/current snapshot for regression diagnostics."""
    prev_home = previous_observed.get("home")
    prev_away = previous_observed.get("away")
    curr_home = current_score.get("home")
    curr_away = current_score.get("away")
    return (
        f"match={match_id} reason={reason} "
        f"prev_score={prev_home}-{prev_away} curr_score={curr_home}-{curr_away} "
        f"prev_elapsed={previous_observed.get('elapsed')} curr_elapsed={current_elapsed} "
        f"prev_events={previous_observed.get('events_count')} curr_events={current_events_count} "
        f"state_key={state_key}"
    )


def prune_live_state(now_utc=None):
    now_utc = now_utc or utc_now()
    removed = match_state.prune_match_tracking_state(now_utc)
    if removed:
        for mid in removed:
            live_state_keys.pop(mid, None)
            live_message_ids.pop(mid, None)
            _missing_since.pop(mid, None)
            _last_observed.pop(mid, None)
            _regression_hold.pop(mid, None)
            _last_sent_content.pop(mid, None)
        logger.info("Pruned %d live state fixture(s).", len(removed))
    return removed


def _cleanup_missing_live_state(seen_live_ids: set[str], now) -> None:
    for mid in list(live_state_keys.keys()):
        if mid in seen_live_ids:
            continue
        first_missing = _missing_since.get(mid)
        if first_missing is None:
            _missing_since[mid] = now
            continue
        if (now - first_missing) < timedelta(seconds=_MISSING_GRACE_SEC):
            continue
        live_state_keys.pop(mid, None)
        live_message_ids.pop(mid, None)
        _missing_since.pop(mid, None)
        _last_observed.pop(mid, None)
        _regression_hold.pop(mid, None)
        _last_sent_content.pop(mid, None)


def seed_already_posted(fixtures: list) -> None:
    """
    Pre-populate live dedupe state with the current snapshot of any in-progress
    matches. Prevents run_live_loop from re-posting updates already shown in
    startup or morning snapshot messages.
    """
    now = bot_now()
    count = 0
    for match in fixtures:
        if not match_lifecycle.is_live(match):
            continue
        match_id = str(match["fixture"]["id"])
        status_short = match_lifecycle.status_short(match)
        score = match.get("goals", {})
        events = match.get("events", [])
        home = match.get("teams", {}).get("home", {}).get("name", "Home Team")
        away = match.get("teams", {}).get("away", {}).get("name", "Away Team")
        key = f"{match_id}_{status_short}_{score.get('home')}-{score.get('away')}_{len(events)}"
        live_state_keys[match_id] = key
        _last_sent_content[match_id] = (
            _render_live_line(match, home, away, score, events, status_short),
            now,
        )
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

        now = bot_now()
        now_utc = utc_now()
        logger.info(f"[{now.strftime('%H:%M')}] Querying live endpoint...")

        matches = await api_provider.fetch_live(bot.http_session)
        if not matches:
            logger.info(f"[{now.strftime('%H:%M')}] No live fixtures returned or fetch error.")
            _cleanup_missing_live_state(set(), now)
            prune_live_state(now_utc)
            return

        seen_live_ids: set[str] = set()

        for match in matches:
            match_id = str(match["fixture"]["id"])
            seen_live_ids.add(match_id)
            _missing_since.pop(match_id, None)
            if not is_tracked_for_ft(match_id):
                track_match_for_ft(match, now_utc=now_utc)
            else:
                match_state.upsert_fixture_from_match(match, now_utc, source="espn")

            # Enrich first so dedup key and outgoing message reflect final data.
            enriched = await api_provider.enrich_fixture_events(bot.http_session, match)

            home = enriched["teams"]["home"]["name"]
            away = enriched["teams"]["away"]["name"]
            score = enriched["goals"]
            events = enriched.get("events", [])
            elapsed = enriched.get("fixture", {}).get("status", {}).get("elapsed")
            status_short = match_lifecycle.status_short(enriched)
            state_key = f"{match_id}_{status_short}_{score['home']}-{score['away']}_{len(events)}"

            previous_observed = _last_observed.get(match_id)
            if previous_observed:
                prev_home = previous_observed["home"]
                prev_away = previous_observed["away"]
                prev_elapsed = previous_observed.get("elapsed")
                curr_home = int(score.get("home") or 0)
                curr_away = int(score.get("away") or 0)
                prev_total = prev_home + prev_away
                curr_total = curr_home + curr_away
                elapsed_regressed = (
                    isinstance(elapsed, int)
                    and isinstance(prev_elapsed, int)
                    and elapsed + 2 < prev_elapsed
                )
                score_regressed = curr_home < prev_home or curr_away < prev_away
                if score_regressed or (elapsed_regressed and curr_total <= prev_total):
                    reason_parts = []
                    if score_regressed:
                        reason_parts.append("score")
                    if elapsed_regressed and curr_total <= prev_total:
                        reason_parts.append("elapsed")
                    regression_context = _regression_guard_context(
                        match_id=match_id,
                        previous_observed=previous_observed,
                        current_score={"home": curr_home, "away": curr_away},
                        current_elapsed=elapsed if isinstance(elapsed, int) else None,
                        current_events_count=len(events),
                        state_key=state_key,
                        reason=",".join(reason_parts),
                    )
                    hold = _regression_hold.get(match_id)
                    if hold and hold.get("state_key") == state_key:
                        hold["ticks"] += 1
                    else:
                        hold = {"state_key": state_key, "ticks": 1}
                    _regression_hold[match_id] = hold

                    if hold["ticks"] < _REGRESSION_CONFIRM_TICKS:
                        logger.info(
                            f"Regression guard: holding match {match_id} state "
                            f"{state_key} (tick {hold['ticks']}/{_REGRESSION_CONFIRM_TICKS}); "
                            f"{regression_context}."
                        )
                        continue

                    logger.warning(
                        f"Regression guard: accepting repeated regressive state for match {match_id} "
                        f"after {hold['ticks']} ticks; {regression_context}."
                    )
                else:
                    _regression_hold.pop(match_id, None)

            previous_state = live_state_keys.get(match_id)
            if previous_state == state_key:
                _last_observed[match_id] = {
                    "home": int(score.get("home") or 0),
                    "away": int(score.get("away") or 0),
                    "elapsed": elapsed if isinstance(elapsed, int) else None,
                    "events_count": len(events),
                }
                continue

            line_content = _render_live_line(enriched, home, away, score, events, status_short)

            last_sent = _last_sent_content.get(match_id)
            if last_sent:
                last_content, sent_at = last_sent
                if last_content == line_content:
                    live_state_keys[match_id] = state_key
                    _last_observed[match_id] = {
                        "home": int(score.get("home") or 0),
                        "away": int(score.get("away") or 0),
                        "elapsed": elapsed if isinstance(elapsed, int) else None,
                        "events_count": len(events),
                    }
                    logger.info(
                        f"Text dedupe: suppressed repeated live content for match {match_id}; "
                        f"last sent at {sent_at.strftime('%H:%M:%S')}."
                    )
                    continue

            message_id = live_message_ids.get(match_id)
            if message_id is None:
                stored_state = match_state.get_fixture_state(match_id) or {}
                message_id = stored_state.get("live_message_id")

            updated_message = await upsert_live_message(
                bot=bot,
                channel_id=CHANNEL_ID,
                message_id=message_id,
                content=line_content,
            )

            if updated_message is not None:
                live_message_ids[match_id] = updated_message.id
                match_state.update_live_message_id(match_id, updated_message.id)
                live_state_keys[match_id] = state_key
                _last_sent_content[match_id] = (line_content, now)
                _last_observed[match_id] = {
                    "home": int(score.get("home") or 0),
                    "away": int(score.get("away") or 0),
                    "elapsed": elapsed if isinstance(elapsed, int) else None,
                    "events_count": len(events),
                }
                safe_line_content = line_content.encode("ascii", "backslashreplace").decode("ascii")
                logger.info(f"Live update upserted for match {match_id}: {safe_line_content}")

        # Clean volatile live maps when a match is no longer live, after grace window.
        _cleanup_missing_live_state(seen_live_ids, now)
        prune_live_state(now_utc)
