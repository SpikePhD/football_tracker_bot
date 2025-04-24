# modules/scheduler.py
import asyncio
from datetime import datetime
from config import TRACKED_LEAGUE_IDS, CHANNEL_ID
from utils.api_client import fetch_day_fixtures
from utils.time_utils import italy_now, parse_utc_to_italy
from modules.verbose_logger import log_info
from modules.live_loop import run_live_loop
from modules.ft_handler import fetch_and_post_ft


def is_tracked(league_id: int) -> bool:
    return league_id in TRACKED_LEAGUE_IDS


async def schedule_day(bot):
    """Checks today’s fixtures, sleeps until the first KO if needed,
    then launches the 8-minute polling loop (live + FT)."""

    # ── 1. Get today’s fixtures ───────────────────────────────────
    log_info("📅 Fetching fixtures for today…")
    fixtures = await fetch_day_fixtures()

    tracked = [m for m in fixtures if is_tracked(m["league"]["id"])]

    if not tracked:
        log_info("📅 No tracked matches today")
        return

    # Sort by kick-off time (UTC strings)
    tracked.sort(key=lambda m: m["fixture"]["date"])

    # ── 2. Pretty-print today’s list ──────────────────────────────
    for m in tracked:
        ko_local = parse_utc_to_italy(m["fixture"]["date"]).strftime("%H:%M")
        home = m["teams"]["home"]["name"]
        away = m["teams"]["away"]["name"]
        print(f"🕒 {ko_local} — {home} vs {away}")

    # ── 3. Work out timing ───────────────────────────────────────
    first_ko = parse_utc_to_italy(tracked[0]["fixture"]["date"])
    now = italy_now()

    # If a match is already live, start immediately
    if now >= first_ko:
        log_info("▶️ Matches already live – launching live loop now")
        await run_live_loop(bot)
    else:
        delta_sec = (first_ko - now).total_seconds()
        h, remainder = divmod(int(delta_sec), 3600)
        m = remainder // 60
        log_info(f"⏳ Sleeping {h}h{m}m until first KO at {first_ko:%H:%M}")
        await asyncio.sleep(delta_sec)
        await run_live_loop(bot)

    # ── 4. Continue polling every 8 minutes until midnight ───────
    end_of_day = datetime.combine(now.date(), datetime.max.time()).replace(tzinfo=now.tzinfo)
    log_info(f"🚀 Polling until {end_of_day:%H:%M} every 8 min")

    counter = 1
    while italy_now() < end_of_day:
        log_info(f"[{counter}] 🔁 Live check")
        await run_live_loop(bot)          # live + goal / RC posts
        await fetch_and_post_ft(bot)      # FT verification
        counter += 1
        await asyncio.sleep(480)          # 8 minutes
