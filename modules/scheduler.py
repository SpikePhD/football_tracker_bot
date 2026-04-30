# modules/scheduler.py

import asyncio
import logging
from datetime import datetime, timedelta

from modules import api_provider
from modules.ft_handler import (
    clear_tracked_matches_today,
    fetch_and_post_ft,
    seed_already_announced_ft,
)
from modules.live_loop import clear_already_posted_today, run_live_loop, seed_already_posted
from modules.tennis_loop import clear_tennis_state_today, run_tennis_loop
from modules.football_memory import update_standings_only, update_team_info_only
from utils.time_utils import italy_now

logger = logging.getLogger(__name__)

# Track daily/weekly memory updates to avoid duplicates
_last_standings_update_date: datetime | None = None
_last_team_info_update_date: datetime | None = None


async def schedule_day(bot):
    """Daily orchestration for football + tennis checks."""

    logger.info("New schedule_day cycle starting. Clearing daily states...")
    clear_already_posted_today()
    clear_tracked_matches_today()
    clear_tennis_state_today()
    logger.info("Daily states cleared.")

    logger.info("Fetching football fixtures for today...")
    try:
        fixtures = await api_provider.fetch_day(bot.http_session)
    except Exception as e:
        logger.error(f"[Scheduler] Failed to fetch initial football fixtures: {e}", exc_info=True)
        fixtures = []

    if not fixtures:
        logger.info("No tracked football fixtures found or API error. Continuing with tennis checks.")
        fixtures = []

    if fixtures:
        seed_already_announced_ft(fixtures)
        seed_already_posted(fixtures)

    # Start both sports immediately, with isolated error handling.
    try:
        await run_live_loop(bot)
        await fetch_and_post_ft(bot)
    except Exception as e:
        logger.error(f"[Scheduler] Unexpected error in initial football cycle: {e}", exc_info=True)

    try:
        await run_tennis_loop(bot)
    except Exception as e:
        logger.error(f"[Scheduler] Unexpected error in initial tennis cycle: {e}", exc_info=True)

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
            logger.info("📊 Daily standings memory update completed.")
        except Exception as e:
            logger.error(f"⚠️ Failed to update standings memory: {e}")

    # Weekly team info update on Sunday midnight (Italy time)
    global _last_team_info_update_date
    if italy_now().weekday() == 6:  # Sunday
        if _last_team_info_update_date is None or _last_team_info_update_date.date() < current_day_date_italy:
            try:
                await update_team_info_only(bot.http_session)
                _last_team_info_update_date = italy_now()
                logger.info("👥 Weekly team info memory update completed.")
            except Exception as e:
                logger.error(f"⚠️ Failed to update team info memory: {e}")

    football_interval = api_provider.get_poll_interval()
    tennis_interval = 60
    logger.info(
        f"Polling active. Football every {football_interval}s "
        f"({'ESPN' if api_provider.is_espn_healthy() else 'API-Football fallback'}) "
        f"and tennis every {tennis_interval}s "
        f"until {end_of_day:%H:%M:%S} (Italy Time)."
    )

    counter = 1
    now_ref = italy_now()
    approx_total_cycles = max(1, (end_of_day - now_ref).total_seconds() / tennis_interval) if now_ref < end_of_day else 0
    next_football_due = now_ref + timedelta(seconds=football_interval)
    next_tennis_due = now_ref + timedelta(seconds=tennis_interval)

    while italy_now() < end_of_day:
        now = italy_now()
        approx_remaining_cycles = 0
        if now < end_of_day:
            remaining_seconds = (end_of_day - now).total_seconds()
            if remaining_seconds > 0:
                approx_remaining_cycles = max(0, remaining_seconds / tennis_interval)

        provider_label = "ESPN" if api_provider.is_espn_healthy() else "FALLBACK"
        logger.info(
            f"[{counter} / ~{approx_total_cycles:.0f} | Rem: ~{approx_remaining_cycles:.0f} | {provider_label}] "
            "Polling scheduler tick"
        )

        if now >= next_football_due:
            try:
                await run_live_loop(bot)
                await fetch_and_post_ft(bot)
            except Exception as e:
                logger.error(f"[Scheduler] Unexpected error in football cycle {counter}: {e}", exc_info=True)
            football_interval = api_provider.get_poll_interval()
            next_football_due = italy_now() + timedelta(seconds=football_interval)

        if now >= next_tennis_due:
            try:
                await run_tennis_loop(bot)
            except Exception as e:
                logger.error(f"[Scheduler] Unexpected error in tennis cycle {counter}: {e}", exc_info=True)
            next_tennis_due = italy_now() + timedelta(seconds=tennis_interval)

        counter += 1
        next_due = min(next_football_due, next_tennis_due, end_of_day)
        await asyncio.sleep(max(1, (next_due - italy_now()).total_seconds()))

    logger.info(f"End of day ({current_day_date_italy}) reached. Polling stopped.")
