import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from config import (
    CHANNEL_ID,
    FOOTBALL_MAX_LIVE_DURATION_HOURS,
    FOOTBALL_PREMATCH_WINDOW_HOURS,
    TENNIS_PRE_ANNOUNCE_HOURS,
)
from modules import api_provider, match_lifecycle
from modules.bot_mode import is_verbose
from modules.discord_poster import post_new_general_message
from modules.ft_handler import fetch_and_post_ft
from modules.live_loop import prune_live_state, run_live_loop
from modules.match_state import expected_ft_due_fixture_ids, prune_match_tracking_state
from modules import match_state, tennis_loop
from modules.football_memory import update_standings_only, update_team_info_only
from utils.time_utils import to_bot_tz, utc_now

logger = logging.getLogger(__name__)

_last_standings_update_date: date | None = None
_last_team_info_update_date: date | None = None
_last_provider_was_espn: bool | None = None
_FOOTBALL_SLEEP_REFRESH_SEC = 21600
_TENNIS_INTERVAL_SEC = 60
_TENNIS_SLEEP_REFRESH_SEC = 21600
_TENNIS_POST_START_WATCH_HOURS = 4
_football_scheduler_state = {
    "mode": "sleeping",
    "next_football_check_utc": None,
    "next_schedule_refresh_utc": None,
    "next_planned_kickoff_utc": None,
    "next_planned_wake_utc": None,
    "wake_reason": None,
    "wake_reason_detail": None,
    "sleep_reason": None,
    "sleep_reason_detail": None,
}
_last_logged_football_state: tuple | None = None
_tennis_scheduler_state = {
    "mode": "sleeping",
    "next_tennis_check_utc": None,
    "next_schedule_refresh_utc": None,
    "next_planned_start_utc": None,
    "next_planned_wake_utc": None,
    "wake_reason": None,
    "wake_reason_detail": None,
    "sleep_reason": None,
    "sleep_reason_detail": None,
}
_last_logged_tennis_state: tuple | None = None


def _set_football_scheduler_state(
    *,
    mode: str,
    next_football_check_utc: datetime | None,
    next_schedule_refresh_utc: datetime | None = None,
    next_planned_kickoff_utc: datetime | None = None,
    next_planned_wake_utc: datetime | None = None,
    wake_reason: str | None = None,
    wake_reason_detail: str | None = None,
    sleep_reason: str | None = None,
    sleep_reason_detail: str | None = None,
) -> None:
    global _last_logged_football_state
    _football_scheduler_state.update(
        {
            "mode": mode,
            "next_football_check_utc": next_football_check_utc,
            "next_schedule_refresh_utc": next_schedule_refresh_utc,
            "next_planned_kickoff_utc": next_planned_kickoff_utc,
            "next_planned_wake_utc": next_planned_wake_utc,
            "wake_reason": wake_reason,
            "wake_reason_detail": wake_reason_detail,
            "sleep_reason": sleep_reason,
            "sleep_reason_detail": sleep_reason_detail,
        }
    )
    snapshot = (
        mode,
        next_schedule_refresh_utc,
        next_planned_kickoff_utc,
        next_planned_wake_utc,
        wake_reason,
        wake_reason_detail,
        sleep_reason,
        sleep_reason_detail,
    )
    if snapshot != _last_logged_football_state:
        logger.info(
            "Football scheduler %s; next check=%s, schedule refresh=%s, planned kickoff=%s, "
            "planned wake=%s, wake reason=%s, wake detail=%s, sleep reason=%s, sleep detail=%s.",
            mode,
            next_football_check_utc.isoformat() if next_football_check_utc else "n/a",
            next_schedule_refresh_utc.isoformat() if next_schedule_refresh_utc else "n/a",
            next_planned_kickoff_utc.isoformat() if next_planned_kickoff_utc else "n/a",
            next_planned_wake_utc.isoformat() if next_planned_wake_utc else "n/a",
            wake_reason or "n/a",
            wake_reason_detail or "n/a",
            sleep_reason or "n/a",
            sleep_reason_detail or "n/a",
        )
        _last_logged_football_state = snapshot


def get_football_scheduler_status() -> dict:
    return dict(_football_scheduler_state)


def _set_tennis_scheduler_state(
    *,
    mode: str,
    next_tennis_check_utc: datetime | None,
    next_schedule_refresh_utc: datetime | None = None,
    next_planned_start_utc: datetime | None = None,
    next_planned_wake_utc: datetime | None = None,
    wake_reason: str | None = None,
    wake_reason_detail: str | None = None,
    sleep_reason: str | None = None,
    sleep_reason_detail: str | None = None,
) -> None:
    global _last_logged_tennis_state
    _tennis_scheduler_state.update(
        {
            "mode": mode,
            "next_tennis_check_utc": next_tennis_check_utc,
            "next_schedule_refresh_utc": next_schedule_refresh_utc,
            "next_planned_start_utc": next_planned_start_utc,
            "next_planned_wake_utc": next_planned_wake_utc,
            "wake_reason": wake_reason,
            "wake_reason_detail": wake_reason_detail,
            "sleep_reason": sleep_reason,
            "sleep_reason_detail": sleep_reason_detail,
        }
    )
    snapshot = (
        mode,
        next_planned_start_utc,
        next_planned_wake_utc,
        wake_reason,
        sleep_reason,
    )
    if snapshot != _last_logged_tennis_state:
        logger.info(
            "Tennis scheduler %s; next check=%s, schedule refresh=%s, planned start=%s, "
            "planned wake=%s, wake reason=%s, wake detail=%s, sleep reason=%s, sleep detail=%s.",
            mode,
            next_tennis_check_utc.isoformat() if next_tennis_check_utc else "n/a",
            next_schedule_refresh_utc.isoformat() if next_schedule_refresh_utc else "n/a",
            next_planned_start_utc.isoformat() if next_planned_start_utc else "n/a",
            next_planned_wake_utc.isoformat() if next_planned_wake_utc else "n/a",
            wake_reason or "n/a",
            wake_reason_detail or "n/a",
            sleep_reason or "n/a",
            sleep_reason_detail or "n/a",
        )
        _last_logged_tennis_state = snapshot


def get_tennis_scheduler_status() -> dict:
    return dict(_tennis_scheduler_state)


def _next_scheduled_football_wake(matches: list[dict], now_utc: datetime) -> tuple[datetime, datetime] | tuple[None, None]:
    now_utc = now_utc.astimezone(timezone.utc)
    candidates = []
    for match in matches:
        if match_lifecycle.is_terminal(match):
            continue
        kickoff = match_lifecycle.fixture_kickoff_utc(match)
        if kickoff is None:
            continue
        kickoff = kickoff.astimezone(timezone.utc)
        if kickoff < now_utc:
            if now_utc - kickoff > timedelta(hours=FOOTBALL_MAX_LIVE_DURATION_HOURS):
                continue
            wake = now_utc
        else:
            wake = kickoff - timedelta(hours=FOOTBALL_PREMATCH_WINDOW_HOURS)
        if wake < now_utc:
            wake = now_utc
        candidates.append((kickoff, wake))
    if not candidates:
        return None, None
    return min(candidates, key=lambda item: item[0])


def _short_fixture_list(fixture_ids: list[str]) -> str:
    shown = [str(fixture_id) for fixture_id in fixture_ids[:5]]
    if len(fixture_ids) > 5:
        shown.append(f"+{len(fixture_ids) - 5} more")
    return ",".join(shown)


def _fixture_poll_reason_detail(match: dict) -> str:
    fixture_id = match_lifecycle.fixture_identity(match) or "unknown"
    status = match_lifecycle.status_short(match) or "unknown"
    kickoff = match_lifecycle.fixture_kickoff_utc(match)
    kickoff_text = kickoff.astimezone(timezone.utc).isoformat() if kickoff else "n/a"
    return f"fixture={fixture_id} status={status} kickoff={kickoff_text}"


def _football_sleep_reason_detail(
    *,
    reason: str,
    schedule_refresh: datetime,
    kickoff: datetime | None,
    wake: datetime | None,
    next_ft_check: datetime | None,
) -> str:
    if reason == "unresolved_ft_due" and next_ft_check:
        return f"expected_ft={next_ft_check.astimezone(timezone.utc).isoformat()}"
    if reason == "next_fixture_wake" and kickoff and wake:
        return (
            f"kickoff={kickoff.astimezone(timezone.utc).isoformat()} "
            f"wake={wake.astimezone(timezone.utc).isoformat()}"
        )
    return f"next_refresh={schedule_refresh.astimezone(timezone.utc).isoformat()}"


async def _plan_sleep_until_next_fixture(bot, now_utc: datetime) -> datetime:
    schedule_refresh = now_utc + timedelta(seconds=_FOOTBALL_SLEEP_REFRESH_SEC)
    matches = await api_provider.fetch_upcoming_football_schedule(bot.http_session, now_utc)
    kickoff, wake = _next_scheduled_football_wake(matches, now_utc)
    next_ft_check = match_state.next_unresolved_expected_ft_utc(now_utc)
    next_check_candidates = [schedule_refresh]
    if wake:
        next_check_candidates.append(wake)
    if next_ft_check:
        next_check_candidates.append(next_ft_check)
    next_check = min(next_check_candidates)
    planned_wake = min((value for value in (wake, next_ft_check) if value), default=None)
    if next_ft_check and next_check == next_ft_check:
        sleep_reason = "unresolved_ft_due"
    elif wake and next_check == wake:
        sleep_reason = "next_fixture_wake"
    else:
        sleep_reason = "schedule_refresh"
    _set_football_scheduler_state(
        mode="sleeping",
        next_football_check_utc=next_check,
        next_schedule_refresh_utc=schedule_refresh,
        next_planned_kickoff_utc=kickoff,
        next_planned_wake_utc=planned_wake,
        sleep_reason=sleep_reason,
        sleep_reason_detail=_football_sleep_reason_detail(
            reason=sleep_reason,
            schedule_refresh=schedule_refresh,
            kickoff=kickoff,
            wake=wake,
            next_ft_check=next_ft_check,
        ),
    )
    return next_check


def _tennis_track_id(match: dict) -> str | None:
    match_id = match.get("match_id")
    if not match_id:
        return None
    return str(match.get("canonical_id") or match_id)


def _tennis_start_utc(match: dict) -> datetime | None:
    start_time = match.get("start_time")
    if not start_time:
        return None
    try:
        return to_bot_tz(start_time).astimezone(timezone.utc)
    except Exception:
        return None


def _tennis_started_local_today(match: dict, now_utc: datetime) -> bool:
    start = _tennis_start_utc(match)
    if start is None:
        return False
    return to_bot_tz(start).date() == to_bot_tz(now_utc).date()


def _next_scheduled_tennis_wake(matches: list[dict], now_utc: datetime) -> tuple[datetime, datetime] | tuple[None, None]:
    now_utc = now_utc.astimezone(timezone.utc)
    candidates = []
    for match in matches:
        if match.get("status", {}).get("short") == "FT":
            continue
        start = _tennis_start_utc(match)
        if start is None or start < now_utc:
            continue
        wake = start - timedelta(hours=TENNIS_PRE_ANNOUNCE_HOURS)
        if wake < now_utc:
            wake = now_utc
        candidates.append((start, wake))
    if not candidates:
        return None, None
    return min(candidates, key=lambda item: item[0])


def _tennis_in_start_watch_window(match: dict, now_utc: datetime) -> bool:
    start = _tennis_start_utc(match)
    if start is None:
        return False
    now_utc = now_utc.astimezone(timezone.utc)
    start = start.astimezone(timezone.utc)
    wake = start - timedelta(hours=TENNIS_PRE_ANNOUNCE_HOURS)
    stale_at = start + timedelta(hours=_TENNIS_POST_START_WATCH_HOURS)
    return wake <= now_utc <= stale_at


def _tennis_poll_reason_detail(match: dict) -> str:
    track_id = _tennis_track_id(match) or "unknown"
    status = match.get("status", {}).get("short") or "unknown"
    start = _tennis_start_utc(match)
    start_text = start.astimezone(timezone.utc).isoformat() if start else "n/a"
    return f"fixture={track_id} status={status} start={start_text}"


def _tennis_sleep_reason_detail(
    *,
    reason: str,
    schedule_refresh: datetime,
    start: datetime | None,
    wake: datetime | None,
) -> str:
    if reason == "next_tennis_wake" and start and wake:
        return (
            f"start={start.astimezone(timezone.utc).isoformat()} "
            f"wake={wake.astimezone(timezone.utc).isoformat()}"
        )
    if reason == "no_relevant_tennis":
        return "no tracked future tennis match"
    return f"next_refresh={schedule_refresh.astimezone(timezone.utc).isoformat()}"


async def _plan_tennis_sleep_until_next_match(bot, now_utc: datetime) -> datetime:
    schedule_refresh = now_utc + timedelta(seconds=_TENNIS_SLEEP_REFRESH_SEC)
    matches = await api_provider.fetch_upcoming_tennis_schedule(bot.http_session, now_utc)
    start, wake = _next_scheduled_tennis_wake(matches, now_utc)
    next_check = min(schedule_refresh, wake) if wake else schedule_refresh
    sleep_reason = "next_tennis_wake" if wake else "schedule_refresh"
    if next_check <= now_utc:
        next_check = now_utc + timedelta(seconds=_TENNIS_INTERVAL_SEC)
    _set_tennis_scheduler_state(
        mode="sleeping",
        next_tennis_check_utc=next_check,
        next_schedule_refresh_utc=schedule_refresh,
        next_planned_start_utc=start,
        next_planned_wake_utc=wake,
        sleep_reason=sleep_reason,
        sleep_reason_detail=_tennis_sleep_reason_detail(
            reason=sleep_reason,
            schedule_refresh=schedule_refresh,
            start=start,
            wake=wake,
        ),
    )
    return next_check


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
    needed, _reason, _detail = await _football_poll_decision(bot, now_utc)
    return needed


async def _football_poll_decision(bot, now_utc: datetime) -> tuple[bool, str, str]:
    due_ids = expected_ft_due_fixture_ids(now_utc)
    if due_ids:
        return True, "ft_due", f"fixtures={_short_fixture_list(due_ids)}"

    matches = await api_provider.fetch_relevant_football(bot.http_session, now_utc)
    for match in matches:
        if _fixture_requires_football_poll(match, now_utc):
            return True, "lifecycle_fixture", _fixture_poll_reason_detail(match)

    has_fallback_live = await api_provider.has_live_football(
        bot.http_session,
        now_utc=now_utc,
        relevant_matches=matches,
    )
    if has_fallback_live:
        return True, "fallback_live", "provider_live_endpoint=true"
    return False, "no_relevant_fixture", f"due=0 relevant={len(matches)} fallback_live=false"


def _fixture_requires_football_poll(match: dict, now_utc: datetime) -> bool:
    if match_lifecycle.is_terminal(match):
        if not match_lifecycle.is_ft(match):
            return False

        fixture_id = match_lifecycle.fixture_identity(match)
        if fixture_id is None:
            return False

        fixture_state = match_state.get_fixture_state(fixture_id)
        if fixture_state is None:
            return True
        return not (
            fixture_state.get("ft_announced") is True
            and fixture_state.get("memory_updated") is True
        )

    return match_lifecycle.should_track_fixture(match, now_utc)


async def run_football_cycle(bot, now_utc: datetime | None = None) -> None:
    now_utc = now_utc or utc_now()
    await run_live_loop(bot)
    await fetch_and_post_ft(bot)
    prune_live_state(now_utc)


async def _tennis_poll_needed(bot, now_utc: datetime) -> bool:
    needed, _reason, _detail = await _tennis_poll_decision(bot, now_utc)
    return needed


async def _tennis_poll_decision(bot, now_utc: datetime) -> tuple[bool, str, str]:
    matches = await api_provider.fetch_tennis_day(bot.http_session)
    for match in matches:
        track_id = _tennis_track_id(match)
        if not track_id:
            continue
        status = match.get("status", {}).get("short")
        if status == "LIVE":
            if track_id not in tennis_loop.final_announced_ids:
                return True, "tennis_live", _tennis_poll_reason_detail(match)
            continue
        if status == "FT":
            if track_id not in tennis_loop.final_announced_ids and _tennis_started_local_today(match, now_utc):
                return True, "tennis_ft_due", _tennis_poll_reason_detail(match)
            continue
        if status == "NS":
            if _tennis_in_start_watch_window(match, now_utc):
                return True, "tennis_start_watch", _tennis_poll_reason_detail(match)
    return False, "no_relevant_tennis", f"matches={len(matches)}"


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
                football_needed, wake_reason, wake_reason_detail = await _football_poll_decision(bot, now)
                if football_needed:
                    await run_football_cycle(bot, now)
                    next_football_check = utc_now() + timedelta(seconds=api_provider.get_poll_interval())
                    _set_football_scheduler_state(
                        mode="awake",
                        next_football_check_utc=next_football_check,
                        wake_reason=wake_reason,
                        wake_reason_detail=wake_reason_detail,
                    )
                else:
                    next_football_check = await _plan_sleep_until_next_fixture(bot, utc_now())
            except Exception as e:
                logger.error("[Scheduler] Football cycle failed: %s", e, exc_info=True)
                next_football_check = utc_now() + timedelta(seconds=api_provider.get_poll_interval())
                _set_football_scheduler_state(
                    mode="awake",
                    next_football_check_utc=next_football_check,
                    wake_reason="error_recovery",
                    wake_reason_detail=str(e),
                )

        if now >= next_tennis_check:
            try:
                tennis_needed, wake_reason, wake_reason_detail = await _tennis_poll_decision(bot, now)
                if tennis_needed:
                    await tennis_loop.run_tennis_loop(bot)
                    next_tennis_check = utc_now() + timedelta(seconds=_TENNIS_INTERVAL_SEC)
                    _set_tennis_scheduler_state(
                        mode="awake",
                        next_tennis_check_utc=next_tennis_check,
                        wake_reason=wake_reason,
                        wake_reason_detail=wake_reason_detail,
                    )
                else:
                    next_tennis_check = await _plan_tennis_sleep_until_next_match(bot, utc_now())
            except Exception as e:
                logger.error("[Scheduler] Tennis cycle failed: %s", e, exc_info=True)
                next_tennis_check = utc_now() + timedelta(seconds=_TENNIS_INTERVAL_SEC)
                _set_tennis_scheduler_state(
                    mode="awake",
                    next_tennis_check_utc=next_tennis_check,
                    wake_reason="error_recovery",
                    wake_reason_detail=str(e),
                )

        next_due = min(next_daily_check, next_football_check, next_tennis_check)
        await asyncio.sleep(max(1, (next_due - utc_now()).total_seconds()))
