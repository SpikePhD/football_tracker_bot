# modules/scheduler.py

import asyncio
import logging
from datetime import datetime, timedelta

from modules import api_provider
from config import CHANNEL_ID
from modules.bot_mode import is_verbose
from modules.discord_poster import post_new_general_message
from modules.ft_handler import (
    clear_tracked_matches_today,
    fetch_and_post_ft,
    seed_already_announced_ft,
)
from modules.live_loop import clear_already_posted_today, run_live_loop, seed_already_posted
from modules.tennis_loop import clear_tennis_state_today, run_tennis_loop
from modules.football_memory import update_standings_only, update_team_info_only
from utils.time_utils import italy_now, parse_utc_to_italy

logger = logging.getLogger(__name__)

# Track daily/weekly memory updates to avoid duplicates
_last_standings_update_date: datetime | None = None
_last_team_info_update_date: datetime | None = None
_last_provider_was_espn: bool | None = None
_SLEEP_REFRESH_INTERVAL_SEC = 3600


def _first_football_start_today(fixtures: list[dict]) -> datetime | None:
    today = italy_now().date()
    starts: list[datetime] = []
    for match in fixtures:
        kickoff = match.get("fixture", {}).get("date")
        if not kickoff:
            continue
        try:
            kickoff_dt = parse_utc_to_italy(kickoff)
        except Exception:
            continue
        if kickoff_dt.date() == today:
            starts.append(kickoff_dt)
    return min(starts) if starts else None


def _first_tennis_start_today(matches: list[dict]) -> datetime | None:
    today = italy_now().date()
    starts: list[datetime] = []
    for match in matches:
        start_time = match.get("start_time")
        if not start_time:
            continue
        try:
            start_dt = parse_utc_to_italy(start_time)
        except Exception:
            continue
        if start_dt.date() == today:
            starts.append(start_dt)
    return min(starts) if starts else None


async def schedule_day(bot):
    """Daily orchestration for football + tennis checks."""

    logger.info("New schedule_day cycle starting. Clearing daily states...")
    clear_already_posted_today()
    clear_tracked_matches_today()
    clear_tennis_state_today()
    logger.info("Daily states cleared.")

    logger.info("Computing first tracked football kickoff for today...")
    try:
        football_fixtures = await api_provider.fetch_day(bot.http_session)
    except Exception as e:
        logger.error(f"[Scheduler] Failed to fetch initial football fixtures: {e}", exc_info=True)
        football_fixtures = []
    football_wake_at = _first_football_start_today(football_fixtures)
    football_active = football_wake_at is not None and italy_now() >= football_wake_at

    logger.info("Computing first tracked tennis start for today...")
    try:
        tennis_matches = await api_provider.fetch_tennis_day(bot.http_session)
    except Exception as e:
        logger.error(f"[Scheduler] Failed to fetch initial tennis matches: {e}", exc_info=True)
        tennis_matches = []
    tennis_wake_at = _first_tennis_start_today(tennis_matches)
    tennis_active = tennis_wake_at is not None and italy_now() >= tennis_wake_at

    current_day_date_italy = italy_now().date()
    end_of_day = datetime.combine(current_day_date_italy, datetime.max.time()).replace(
        tzinfo=italy_now().tzinfo
    )

    # --- Memory Updates ---
    # Daily standings update at midnight (Italy time)
    global _last_standings_update_date
    if _last_standings_update_date is None or _last_standings_update_date.date() < current_day_date_italy:
        try:
            await update_standings_only(bot.http_session)
            _last_standings_update_date = italy_now()
            logger.info("Daily standings memory update completed.")
        except Exception as e:
            logger.error(f"Failed to update standings memory: {e}")

    # Weekly team info update on Sunday midnight (Italy time)
    global _last_team_info_update_date
    if italy_now().weekday() == 6:  # Sunday
        if _last_team_info_update_date is None or _last_team_info_update_date.date() < current_day_date_italy:
            try:
                await update_team_info_only(bot.http_session)
                _last_team_info_update_date = italy_now()
                logger.info("Weekly team info memory update completed.")
            except Exception as e:
                logger.error(f"Failed to update team info memory: {e}")

    football_interval = api_provider.get_poll_interval()
    tennis_interval = 60

    if football_wake_at is None:
        logger.info("Football scheduler: sleeping all day (no tracked football starts today).")
    elif football_active:
        logger.info(f"Football scheduler: active now (wake time {football_wake_at:%H:%M}).")
    else:
        logger.info(f"Football scheduler: sleeping until {football_wake_at:%H:%M}.")

    if tennis_wake_at is None:
        logger.info("Tennis scheduler: sleeping all day (no tracked tennis starts today).")
    elif tennis_active:
        logger.info(f"Tennis scheduler: active now (wake time {tennis_wake_at:%H:%M}).")
    else:
        logger.info(f"Tennis scheduler: sleeping until {tennis_wake_at:%H:%M}.")

    counter = 1
    now_ref = italy_now()
    approx_total_cycles = max(1, (end_of_day - now_ref).total_seconds() / tennis_interval) if now_ref < end_of_day else 0

    next_football_due = now_ref if football_active else None
    next_tennis_due = now_ref if tennis_active else None
    next_football_refresh_due = now_ref + timedelta(seconds=_SLEEP_REFRESH_INTERVAL_SEC) if not football_active else None
    next_tennis_refresh_due = now_ref + timedelta(seconds=_SLEEP_REFRESH_INTERVAL_SEC) if not tennis_active else None

    while italy_now() < end_of_day:
        now = italy_now()
        global _last_provider_was_espn

        if football_active:
            current_provider_is_espn = api_provider.is_espn_healthy()
            if _last_provider_was_espn is None:
                _last_provider_was_espn = current_provider_is_espn
            elif _last_provider_was_espn != current_provider_is_espn:
                if is_verbose():
                    if current_provider_is_espn:
                        notice = "Data provider recovered: back to ESPN primary."
                    else:
                        notice = "Data provider degraded: using API-Football fallback until ESPN recovers."
                    await post_new_general_message(bot, CHANNEL_ID, content=notice)
                _last_provider_was_espn = current_provider_is_espn

        approx_remaining_cycles = 0
        if now < end_of_day:
            remaining_seconds = (end_of_day - now).total_seconds()
            if remaining_seconds > 0:
                approx_remaining_cycles = max(0, remaining_seconds / tennis_interval)

        provider_label = "ESPN" if api_provider.is_espn_healthy() else "FALLBACK"
        football_state = "ACTIVE" if football_active else "SLEEP"
        tennis_state = "ACTIVE" if tennis_active else "SLEEP"
        logger.info(
            f"[{counter} / ~{approx_total_cycles:.0f} | Rem: ~{approx_remaining_cycles:.0f} | "
            f"FB:{football_state} TN:{tennis_state} {provider_label}] Polling scheduler tick"
        )

        if not football_active and next_football_refresh_due and now >= next_football_refresh_due:
            try:
                football_fixtures = await api_provider.fetch_day(bot.http_session)
                football_wake_at = _first_football_start_today(football_fixtures)
                if football_wake_at is None:
                    logger.info("Football sleep refresh: still no tracked football starts today.")
                else:
                    logger.info(f"Football sleep refresh: wake time now {football_wake_at:%H:%M}.")
            except Exception as e:
                logger.error(f"[Scheduler] Football sleep refresh failed: {e}", exc_info=True)
            next_football_refresh_due = italy_now() + timedelta(seconds=_SLEEP_REFRESH_INTERVAL_SEC)

        if not tennis_active and next_tennis_refresh_due and now >= next_tennis_refresh_due:
            try:
                tennis_matches = await api_provider.fetch_tennis_day(bot.http_session)
                tennis_wake_at = _first_tennis_start_today(tennis_matches)
                if tennis_wake_at is None:
                    logger.info("Tennis sleep refresh: still no tracked tennis starts today.")
                else:
                    logger.info(f"Tennis sleep refresh: wake time now {tennis_wake_at:%H:%M}.")
            except Exception as e:
                logger.error(f"[Scheduler] Tennis sleep refresh failed: {e}", exc_info=True)
            next_tennis_refresh_due = italy_now() + timedelta(seconds=_SLEEP_REFRESH_INTERVAL_SEC)

        if not football_active and football_wake_at is not None and now >= football_wake_at:
            football_active = True
            next_football_refresh_due = None
            _last_provider_was_espn = api_provider.is_espn_healthy()
            logger.info(f"Football scheduler activated at {now:%H:%M} (wake target {football_wake_at:%H:%M}).")
            try:
                activation_fixtures = await api_provider.fetch_day(bot.http_session)
                if activation_fixtures:
                    seed_already_announced_ft(activation_fixtures)
                    seed_already_posted(activation_fixtures)
            except Exception as e:
                logger.error(f"[Scheduler] Football activation seeding failed: {e}", exc_info=True)
            next_football_due = italy_now()

        if not tennis_active and tennis_wake_at is not None and now >= tennis_wake_at:
            tennis_active = True
            next_tennis_refresh_due = None
            logger.info(f"Tennis scheduler activated at {now:%H:%M} (wake target {tennis_wake_at:%H:%M}).")
            next_tennis_due = italy_now()

        if football_active and next_football_due and now >= next_football_due:
            try:
                await run_live_loop(bot)
                await fetch_and_post_ft(bot)
            except Exception as e:
                logger.error(f"[Scheduler] Unexpected error in football cycle {counter}: {e}", exc_info=True)
            football_interval = api_provider.get_poll_interval()
            next_football_due = italy_now() + timedelta(seconds=football_interval)

        if tennis_active and next_tennis_due and now >= next_tennis_due:
            try:
                await run_tennis_loop(bot)
            except Exception as e:
                logger.error(f"[Scheduler] Unexpected error in tennis cycle {counter}: {e}", exc_info=True)
            next_tennis_due = italy_now() + timedelta(seconds=tennis_interval)

        counter += 1
        due_candidates = [end_of_day]
        if next_football_due is not None:
            due_candidates.append(next_football_due)
        if next_tennis_due is not None:
            due_candidates.append(next_tennis_due)
        if next_football_refresh_due is not None:
            due_candidates.append(next_football_refresh_due)
        if next_tennis_refresh_due is not None:
            due_candidates.append(next_tennis_refresh_due)

        next_due = min(due_candidates)
        await asyncio.sleep(max(1, (next_due - italy_now()).total_seconds()))

    logger.info(f"End of day ({current_day_date_italy}) reached. Polling stopped.")
