# modules/scheduler.py

import asyncio
from datetime import datetime
# config import for TRACKED_LEAGUE_IDS is no longer needed here if api_client filters
from utils.api_client import fetch_day_fixtures
from utils.time_utils import italy_now, parse_utc_to_italy
from modules.verbose_logger import log_info # Ensure log_error is imported if used, or standardize logger
# is_tracked import is no longer needed if api_client filters
# from modules.track_leagues import is_tracked 

from modules.live_loop import run_live_loop
# MODIFIED: Add post_initial_fts to the import
from modules.ft_handler import fetch_and_post_ft, post_initial_fts


async def schedule_day(bot):
    """Checks today’s fixtures, posts initial FTs, sleeps until the first KO if needed,
    then launches the 8-minute polling loop (live + FT)."""

    # ── 1. Get today’s fixtures (already filtered by league in new api_client) ───
    log_info("📅 Fetching fixtures for today…")
    fixtures = await fetch_day_fixtures(bot.http_session) 

    if not fixtures: 
        log_info("📅 No tracked league fixtures found for today or API error. Scheduling will not proceed for this cycle.")
        return

    # --- NEW: Call post_initial_fts for matches already at Full Time ---
    log_info("ℹ️ Checking for any matches already at Full Time to post initial results...")
    await post_initial_fts(fixtures, bot)
    # --- End of new call for post_initial_fts ---

    # Filter for matches that are 'Not Started' or 'Time To Be Defined' to determine KO waiting time.
    tracked_for_ko_timing = [
        m for m in fixtures
        if m.get('fixture', {}).get('status', {}).get('short') in ("NS", "TBD")
    ]
    
    if not tracked_for_ko_timing:
        log_info("📅 No 'Not Started' or 'TBD' tracked matches today to schedule specific KO waiting.")
        log_info("▶️ Performing an immediate live check for any ongoing matches...")
        await run_live_loop(bot)
    else:
        tracked_for_ko_timing.sort(key=lambda m: m["fixture"]["date"])

        log_info("--- Upcoming Matches to be Tracked Live (Not Started/TBD) ---")
        for m_ko in tracked_for_ko_timing:
            ko_local = parse_utc_to_italy(m_ko["fixture"]["date"]).strftime("%H:%M")
            home = m_ko["teams"]["home"]["name"]
            away = m_ko["teams"]["away"]["name"]
            log_info(f"🕒 {ko_local} — {home} vs {away} (ID: {m_ko['fixture']['id']})")
        log_info("-----------------------------------------------------------")

        first_ko_details = tracked_for_ko_timing[0]
        first_ko_time = parse_utc_to_italy(first_ko_details["fixture"]["date"])
        current_italy_time = italy_now()

        if current_italy_time >= first_ko_time:
            log_info(f"▶️ First KO ({first_ko_time:%H:%M}) for {first_ko_details['fixture']['id']} is past or now – launching live loop immediately.")
            await run_live_loop(bot)
        else:
            delta_sec = (first_ko_time - current_italy_time).total_seconds()
            if delta_sec > 0:
                h, remainder = divmod(int(delta_sec), 3600)
                m_val = remainder // 60
                log_info(f"⏳ Sleeping {h}h{m_val}m until first KO at {first_ko_time:%H:%M} for match ID {first_ko_details['fixture']['id']}.")
                await asyncio.sleep(delta_sec)
            await run_live_loop(bot) 
            
    # ── 4. Continue polling every 8 minutes until midnight ───────
    current_day_date_italy = italy_now().date() 
    end_of_day = datetime.combine(current_day_date_italy, datetime.max.time()).replace(tzinfo=italy_now().tzinfo)
    log_info(f"🚀 Polling active. Will run checks every 8 min until {end_of_day:%H:%M:%S} (Italy Time).")

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
        
        log_info(f"[{counter} / ~{approx_total_cycles:.0f} | Rem: ~{approx_remaining_cycles:.0f}] 🔁 Live & FT check cycle.")
        
        await run_live_loop(bot)
        await fetch_and_post_ft(bot)
        
        counter += 1
        await asyncio.sleep(480) 
    
    log_info(f"🏁 End of day ({current_day_date_italy}) reached. Polling stopped.")