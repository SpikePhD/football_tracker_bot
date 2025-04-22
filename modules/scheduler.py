# modules/scheduler.py

import asyncio
from datetime import datetime
from config import TRACKED_LEAGUE_IDS
from utils.api_client import fetch_day_fixtures
from utils.time_utils import italy_now, parse_utc_to_italy
from modules.verbose_logger import log_info
from modules.live_loop import run_live_loop
from modules.ft_handler import post_initial_fts, fetch_and_post_ft

def is_tracked(league_id):
    return league_id in TRACKED_LEAGUE_IDS

async def schedule_day(bot):
    log_info("📅 Fetching fixtures for today…")
    fixtures = await fetch_day_fixtures()

    # 1) post any already-FTs
    await post_initial_fts(fixtures, bot)

    # 2) filter today’s tracked matches
    today = [m for m in fixtures if is_tracked(m['league']['id'])]
    if not today:
        log_info("📅 No tracked matches today")
        return

    today.sort(key=lambda m: m['fixture']['date'])
    for m in today:
        ko   = parse_utc_to_italy(m['fixture']['date']).strftime("%H:%M")
        home = m['teams']['home']['name']
        away = m['teams']['away']['name']
        print(f"🕒 {ko} — {home} vs {away}")

    now   = italy_now()
    kicks = [
        parse_utc_to_italy(m['fixture']['date'])
        for m in today
        if parse_utc_to_italy(m['fixture']['date']) > now
    ]

    if not kicks:
        log_info("⏸ No upcoming KOs—launching live loop")
        await run_live_loop(bot)
    else:
        first = min(kicks)
        delta = (first - now).total_seconds()
        h, rem = divmod(int(delta), 3600)
        m = rem // 60
        log_info(f"⏳ Sleeping {h}h{m}m until first KO at {first.strftime('%H:%M')}")
        await asyncio.sleep(delta)
        log_info("🚀 Starting live polling loop")

    # 3) continuous polling until midnight
    end = datetime.combine(now.date(), datetime.max.time()).replace(tzinfo=now.tzinfo)
    while italy_now() < end:
        log_info("🔁 Live check")
        await run_live_loop(bot)
        await fetch_and_post_ft(bot)
        await asyncio.sleep(8 * 60)
