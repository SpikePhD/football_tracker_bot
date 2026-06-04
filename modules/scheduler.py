import asyncio
import logging
from datetime import date, datetime, timedelta

from config import CHANNEL_ID
from modules import api_provider, match_lifecycle
from modules.bot_mode import is_verbose
from modules.discord_poster import post_new_general_message
from modules.ft_handler import fetch_and_post_ft
from modules.live_loop import prune_live_state, run_live_loop
from modules.match_state import expected_ft_due_fixture_ids, prune_match_tracking_state
from modules.tennis_loop import run_tennis_loop
from modules.football_memory import update_standings_only, update_team_info_only
from utils.time_utils import to_bot_tz, utc_now

logger = logging.getLogger(__name__)

_last_standings_update_date: date | None = None
_last_team_info_update_date: date | None = None
_last_provider_was_espn: bool | None = None
_FOOTBALL_SLEEP_REFRESH_SEC = 900
_TENNIS_INTERVAL_SEC = 60


async def run_local_daily_routines(bot, now_utc: datetime | None = None) -> None:
    now_utc = now_utc or utc_now()
    local_now = to_bot_tz(now_utc)
    local_day = local_now.date()
    session = getattr(bot, "http_session", None)

    global _last_standings_update_date
    if _last_standings_update_date != local_day:
        await update_standings_only(session)
        _last_standings_update_date = local_day
        logger.info("Daily standings memory update completed for %s.", local_day)

    global _last_team_info_update_date
    if local_now.weekday() == 6 and _last_team_info_update_date != local_day:
        await update_team_info_only(session)
        _last_team_info_update_date = local_day
        logger.info("Weekly team info memory update completed for %s.", local_day)

    prune_match_tracking_state(now_utc)


async def _football_poll_needed(bot, now_utc: datetime) -> bool:
    due_ids = expected_ft_due_fixture_ids(now_utc)
    if due_ids:
        return True

    matches = await api_provider.fetch_relevant_football(bot.http_session, now_utc)
    if any(match_lifecycle.should_track_fixture(match, now_utc) for match in matches):
        return True

    return await api_provider.has_live_football(
        bot.http_session,
        now_utc=now_utc,
        relevant_matches=matches,
    )


async def run_football_cycle(bot, now_utc: datetime | None = None) -> None:
    now_utc = now_utc or utc_now()
    await run_live_loop(bot)
    await fetch_and_post_ft(bot)
    prune_live_state(now_utc)


async def run_operations_loop(bot) -> None:
    global _last_provider_was_espn

    logger.info("Starting UTC-first operations loop.")
    next_football_check = utc_now()
    next_tennis_check = utc_now()
    next_daily_check = utc_now()

    while True:
        now = utc_now()

        if now >= next_daily_check:
            try:
                await run_local_daily_routines(bot, now)
            except Exception as e:
                logger.error("[Scheduler] Local daily routines failed: %s", e, exc_info=True)
            next_daily_check = now + timedelta(minutes=1)

        current_provider_is_espn = api_provider.is_espn_healthy()
        if _last_provider_was_espn is None:
            _last_provider_was_espn = current_provider_is_espn
        elif _last_provider_was_espn != current_provider_is_espn:
            if is_verbose():
                notice = (
                    "Data provider recovered: back to ESPN primary."
                    if current_provider_is_espn
                    else "Data provider degraded: using API-Football fallback until ESPN recovers."
                )
                await post_new_general_message(bot, CHANNEL_ID, content=notice)
            _last_provider_was_espn = current_provider_is_espn

        if now >= next_football_check:
            try:
                if await _football_poll_needed(bot, now):
                    await run_football_cycle(bot, now)
                    next_football_check = utc_now() + timedelta(seconds=api_provider.get_poll_interval())
                else:
                    next_football_check = utc_now() + timedelta(seconds=_FOOTBALL_SLEEP_REFRESH_SEC)
            except Exception as e:
                logger.error("[Scheduler] Football cycle failed: %s", e, exc_info=True)
                next_football_check = utc_now() + timedelta(seconds=api_provider.get_poll_interval())

        if now >= next_tennis_check:
            try:
                await run_tennis_loop(bot)
            except Exception as e:
                logger.error("[Scheduler] Tennis cycle failed: %s", e, exc_info=True)
            next_tennis_check = utc_now() + timedelta(seconds=_TENNIS_INTERVAL_SEC)

        next_due = min(next_daily_check, next_football_check, next_tennis_check)
        await asyncio.sleep(max(1, (next_due - utc_now()).total_seconds()))
