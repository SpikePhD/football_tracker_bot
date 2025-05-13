# modules/scheduler.py

import asyncio
import logging # MODIFIED: Import standard logging
from datetime import datetime

from utils.api_client import fetch_day_fixtures
from utils.time_utils import italy_now, parse_utc_to_italy
# MODIFIED: Remove verbose_logger import
# from modules.verbose_logger import log_info 

from modules.live_loop import run_live_loop
from modules.ft_handler import fetch_and_post_ft, post_initial_fts

# MODIFIED: Get a logger instance for this module
logger = logging.getLogger(__name__)


async def schedule_day(bot):
    """Checks today’s fixtures, posts initial FTs, sleeps until the first KO if needed,
    then launches the 8-minute polling loop (live + FT)."""

    # ── 1. Get today’s fixtures (already filtered by league in new api_client) ───
    logger.info("📅 Fetching fixtures for today…") # MODIFIED
    fixtures = await fetch_day_fixtures(bot.http_session) 

    if not fixtures: 
        logger.info("📅 No tracked league fixtures found for today or API error. Scheduling will not proceed for this cycle.") # MODIFIED
        return

    # --- Call post_initial_fts for matches already at Full Time ---
    logger.info("ℹ️ Checking for any matches already at Full Time to post initial results...") # MODIFIED
    await post_initial_fts(fixtures, bot)
    # --- End of call for post_initial_fts ---

    tracked_for_ko_timing = [
        m for m in fixtures
        if m.get('fixture', {}).get('status', {}).get('short') in ("NS", "TBD")
    ]
    
    if not tracked_for_ko_timing:
        logger.info("📅 No 'Not Started' or 'TBD' tracked matches today to schedule specific KO waiting.") # MODIFIED
        logger.info("▶️ Performing an immediate live check for any ongoing matches...") # MODIFIED
        await run_live_loop(bot)
    else:
        tracked_for_ko_timing.sort(key=lambda m: m["fixture"]["date"])

        logger.info("--- Upcoming Matches to be Tracked Live (Not Started/TBD) ---") # MODIFIED
        for m_ko in tracked_for_ko_timing:
            ko_local = parse_utc_to_italy(m_ko["fixture"]["date"]).strftime("%H:%M")
            home = m_ko["teams"]["home"]["name"]
            away = m_ko["teams"]["away"]["name"]
            logger.info(f"🕒 {ko_local} — {home} vs {away} (ID: {m_ko['fixture']['id']})") # MODIFIED
        logger.info("-----------------------------------------------------------") # MODIFIED

        first_ko_details = tracked_for_ko_timing[0]
        first_ko_time = parse_utc_to_italy(first_ko_details["fixture"]["date"])
        current_italy_time = italy_now()

        if current_italy_time >= first_ko_time:
            logger.info(f"▶️ First KO ({first_ko_time:%H:%M}) for {first_ko_details['fixture']['id']} is past or now – launching live loop immediately.") # MODIFIED
            await run_live_loop(bot)
        else:
            delta_sec = (first_ko_time - current_italy_time).total_seconds()
            if delta_sec > 0:
                h, remainder = divmod(int(delta_sec), 3600)
                m_val = remainder // 60
                logger.info(f"⏳ Sleeping {h}h{m_val}m until first KO at {first_ko_time:%H:%M} for match ID {first_ko_details['fixture']['id']}.") # MODIFIED
                await asyncio.sleep(delta_sec)
            await run_live_loop(bot) 
            
    # ── 4. Continue polling every 8 minutes until midnight ───────
    current_day_date_italy = italy_now().date() 
    end_of_day = datetime.combine(current_day_date_italy, datetime.max.time()).replace(tzinfo=italy_now().tzinfo)
    logger.info(f"🚀 Polling active. Will run checks every 8 min until {end_of_day:%H:%M:%S} (Italy Time).") # MODIFIED

    counter = 1
    approx_total_cycles = 0
    if italy_now() < end_of_day:
        initial_remaining_seconds = (end_of_day - italy_now()).total_seconds()
        if initial_remaining_seconds > 0:
            approx_total_cycles = max(1, initial_remaining_seconds / 480) 

    while italy_now() < end_of_day:
        current_time_for_loop = italy_now()
        approx_remaining_cycles = 0
        if current_time_for_loop < end_of_day:
            remaining_seconds_in_loop = (end_of_day - current_time_for_loop).total_seconds()
            if remaining_seconds_in_loop > 0:
                approx_remaining_cycles = max(0, remaining_seconds_in_loop / 480)
        
        logger.info(f"[{counter} / ~{approx_total_cycles:.0f} | Rem: ~{approx_remaining_cycles:.0f}] 🔁 Live & FT check cycle.") # MODIFIED
        
        await run_live_loop(bot)
        await fetch_and_post_ft(bot)
        
        counter += 1
        await asyncio.sleep(480) 
    
    logger.info(f"🏁 End of day ({current_day_date_italy}) reached. Polling stopped.") # MODIFIED