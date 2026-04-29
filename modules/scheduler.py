# modules/scheduler.py

import asyncio
import logging
from datetime import datetime

from modules import api_provider
from modules.ft_handler import (
    clear_tracked_matches_today,
    fetch_and_post_ft,
    seed_already_announced_ft,
)
from modules.live_loop import clear_already_posted_today, run_live_loop, seed_already_posted
from modules.tennis_loop import clear_tennis_state_today, run_tennis_loop
from utils.time_utils import italy_now

logger = logging.getLogger(__name__)


async def schedule_day(bot):
    """Daily orchestration for football + tennis checks."""

    logger.info("New schedule_day cycle starting. Clearing daily states...")
    clear_already_posted_today()
    clear_tracked_matches_today()
    clear_tennis_state_today()
    logger.info("Daily states cleared.")

    logger.info("Fetching football fixtures for today...")
    fixtures = await api_provider.fetch_day(bot.http_session)

    if not fixtures:
        logger.info("No tracked football fixtures found or API error. Continuing with tennis checks.")
        fixtures = []

    if fixtures:
        seed_already_announced_ft(fixtures)
        seed_already_posted(fixtures)

    # Start polling immediately. Football live checks are cheap/cache-backed, and
    # tennis must not wait behind the first football kickoff.
    try:
        await run_live_loop(bot)
        await fetch_and_post_ft(bot)
        await run_tennis_loop(bot)
    except Exception as e:
        logger.error(f"[Scheduler] Unexpected error in initial polling cycle: {e}", exc_info=True)

    current_day_date_italy = italy_now().date()
    end_of_day = datetime.combine(current_day_date_italy, datetime.max.time()).replace(
        tzinfo=italy_now().tzinfo
    )

    interval = api_provider.get_poll_interval()
    logger.info(
        f"Polling active. Every {interval}s "
        f"({'ESPN' if api_provider.is_espn_healthy() else 'API-Football fallback'}) "
        f"until {end_of_day:%H:%M:%S} (Italy Time)."
    )

    counter = 1
    now_ref = italy_now()
    approx_total_cycles = max(1, (end_of_day - now_ref).total_seconds() / interval) if now_ref < end_of_day else 0

    while italy_now() < end_of_day:
        interval = api_provider.get_poll_interval()

        current_time_for_loop = italy_now()
        approx_remaining_cycles = 0
        if current_time_for_loop < end_of_day:
            remaining_seconds = (end_of_day - current_time_for_loop).total_seconds()
            if remaining_seconds > 0:
                approx_remaining_cycles = max(0, remaining_seconds / interval)

        provider_label = "ESPN" if api_provider.is_espn_healthy() else "FALLBACK"
        logger.info(
            f"[{counter} / ~{approx_total_cycles:.0f} | Rem: ~{approx_remaining_cycles:.0f} | {provider_label}] "
            "Live/FT/Tennis cycle"
        )

        try:
            await run_live_loop(bot)
            await fetch_and_post_ft(bot)
            await run_tennis_loop(bot)
        except Exception as e:
            logger.error(f"[Scheduler] Unexpected error in polling cycle {counter}: {e}", exc_info=True)

        counter += 1
        await asyncio.sleep(interval)

    logger.info(f"End of day ({current_day_date_italy}) reached. Polling stopped.")
