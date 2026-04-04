# modules/scheduler.py

import asyncio
import logging
from datetime import datetime
from modules import api_provider
from utils.time_utils import italy_now, parse_utc_to_italy
from modules.live_loop import run_live_loop, clear_already_posted_today, seed_already_posted
from modules.ft_handler import fetch_and_post_ft, clear_tracked_matches_today, seed_already_announced_ft

logger = logging.getLogger(__name__)

async def schedule_day(bot):
    """Checks today's fixtures, posts initial FTs, sleeps until the first KO if needed,
    then launches the polling loop (60s ESPN / 480s fallback)."""

    logger.info("🌅 New 'schedule_day' cycle starting. Clearing daily states...")
    clear_already_posted_today()
    clear_tracked_matches_today()
    logger.info("✅ Daily states cleared.")

    # ── 1. Get today's fixtures ───────────────────────────────────────────────
    logger.info("📅 Fetching fixtures for today…")
    fixtures = await api_provider.fetch_day(bot.http_session)

    if not fixtures:
        logger.info("📅 No tracked league fixtures found for today or API error. Scheduling will not proceed for this cycle.")
        return

    # Pre-seed sets so already-finished/in-progress matches aren't re-announced on startup
    seed_already_announced_ft(fixtures)
    seed_already_posted(fixtures)

    tracked_for_ko_timing = [
        m for m in fixtures
        if m.get("fixture", {}).get("status", {}).get("short") in ("NS", "TBD")
    ]

    if not tracked_for_ko_timing:
        logger.info("📅 No 'Not Started' or 'TBD' tracked matches today to schedule specific KO waiting.")
        logger.info("▶️ Performing an immediate live check for any ongoing matches...")
        await run_live_loop(bot)
    else:
        tracked_for_ko_timing.sort(key=lambda m: m["fixture"]["date"])

        logger.info("--- Upcoming Matches to be Tracked Live (Not Started/TBD) ---")
        for m_ko in tracked_for_ko_timing:
            ko_local = parse_utc_to_italy(m_ko["fixture"]["date"]).strftime("%H:%M")
            home = m_ko["teams"]["home"]["name"]
            away = m_ko["teams"]["away"]["name"]
            logger.info(f"🕒 {ko_local} — {home} vs {away} (ID: {m_ko['fixture']['id']})")
        logger.info("-----------------------------------------------------------")

        first_ko_details = tracked_for_ko_timing[0]
        first_ko_time = parse_utc_to_italy(first_ko_details["fixture"]["date"])
        current_italy_time = italy_now()

        if current_italy_time >= first_ko_time:
            logger.info(f"▶️ First KO ({first_ko_time:%H:%M}) for {first_ko_details['fixture']['id']} is past or now – launching live loop immediately.")
            await run_live_loop(bot)
        else:
            delta_sec = (first_ko_time - current_italy_time).total_seconds()
            if delta_sec > 0:
                h, remainder = divmod(int(delta_sec), 3600)
                m_val = remainder // 60
                logger.info(f"⏳ Sleeping {h}h{m_val}m until first KO at {first_ko_time:%H:%M} for match ID {first_ko_details['fixture']['id']}.")
                await asyncio.sleep(delta_sec)
            await run_live_loop(bot)

    # ── 3. Poll loop until midnight ───────────────────────────────────────────
    current_day_date_italy = italy_now().date()
    end_of_day = datetime.combine(current_day_date_italy, datetime.max.time()).replace(tzinfo=italy_now().tzinfo)

    interval = api_provider.get_poll_interval()
    logger.info(
        f"🚀 Polling active. Checking every {interval}s "
        f"({'ESPN' if api_provider.is_espn_healthy() else 'API-Football fallback'}) "
        f"until {end_of_day:%H:%M:%S} (Italy Time)."
    )

    counter = 1
    _now = italy_now()
    approx_total_cycles = max(1, (end_of_day - _now).total_seconds() / interval) if _now < end_of_day else 0

    while italy_now() < end_of_day:
        interval = api_provider.get_poll_interval()

        current_time_for_loop = italy_now()
        approx_remaining_cycles = 0
        if current_time_for_loop < end_of_day:
            remaining_seconds_in_loop = (end_of_day - current_time_for_loop).total_seconds()
            if remaining_seconds_in_loop > 0:
                approx_remaining_cycles = max(0, remaining_seconds_in_loop / interval)

        provider_label = "ESPN" if api_provider.is_espn_healthy() else "FALLBACK"
        logger.info(
            f"[{counter} / ~{approx_total_cycles:.0f} | Rem: ~{approx_remaining_cycles:.0f} | {provider_label}] "
            f"🔁 Live & FT check cycle."
        )

        try:
            await run_live_loop(bot)
            await fetch_and_post_ft(bot)
        except Exception as e:
            logger.error(f"[Scheduler] Unexpected error in polling cycle {counter}: {e}", exc_info=True)

        counter += 1
        await asyncio.sleep(interval)

    logger.info(f"🏁 End of day ({current_day_date_italy}) reached. Polling stopped.")
