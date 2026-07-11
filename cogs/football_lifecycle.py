import logging
from datetime import datetime, timezone
from typing import Any

from discord.ext import commands

from config import (
    FOOTBALL_DISPLAY_LOOKUP_WINDOW_HOURS,
    FOOTBALL_EXPECTED_FT_MINUTES,
    FOOTBALL_FINISHED_RETENTION_HOURS,
    FOOTBALL_MAX_LIVE_DURATION_HOURS,
    FOOTBALL_PREMATCH_WINDOW_HOURS,
    FOOTBALL_STATE_RETENTION_HOURS,
    OPERATIONS_TIMEZONE,
)
from modules import api_provider, match_lifecycle, match_state, scheduler
from modules.discord_poster import post_new_message_to_context
from modules.admin import operator_only
from utils.time_utils import parse_provider_utc, to_bot_tz, utc_now

logger = logging.getLogger(__name__)

DISCORD_SAFE_LIMIT = 1900
LIST_LIMIT = 10


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parse_provider_utc(value)
    except Exception:
        return None


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    return str(value)


def _fmt_score(score: dict | None) -> str:
    score = score or {}
    return f"{_fmt(score.get('home'))}-{_fmt(score.get('away'))}"


def _fmt_local(value: str | None) -> str:
    if not value:
        return "n/a"
    try:
        local = to_bot_tz(value)
    except Exception:
        return "invalid"
    return f"{local.strftime('%Y-%m-%d %H:%M')} {OPERATIONS_TIMEZONE}"


def _fmt_utc_value(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _expected_ft_due(fixture: dict, now_utc: datetime) -> bool:
    expected = _parse_dt(fixture.get("expected_ft_utc"))
    if expected is None:
        return False
    if fixture.get("ft_announced") and fixture.get("memory_updated"):
        return False
    return expected <= now_utc.astimezone(timezone.utc)


def _is_awaiting_ft_post(fixture: dict, now_utc: datetime) -> bool:
    if fixture.get("ft_announced"):
        return False
    if fixture.get("last_status") in match_lifecycle.FT_STATUSES:
        return True
    return _expected_ft_due(fixture, now_utc)


def _is_awaiting_memory(fixture: dict, now_utc: datetime) -> bool:
    if fixture.get("memory_updated"):
        return False
    if fixture.get("last_status") in match_lifecycle.FT_STATUSES:
        return True
    return _expected_ft_due(fixture, now_utc)


def _truncate(content: str) -> str:
    if len(content) <= DISCORD_SAFE_LIMIT:
        return content
    return content[: DISCORD_SAFE_LIMIT - 30].rstrip() + "\n... output truncated"


def build_match_state_detail(fixture: dict, now_utc: datetime) -> str:
    fixture_id = fixture.get("fixture_id", "unknown")
    prunable = match_lifecycle.state_is_prunable(fixture, now_utc)
    due = _expected_ft_due(fixture, now_utc)
    lines = [
        f"**Fixture `{fixture_id}` lifecycle state**",
        f"Provider: {fixture.get('provider', 'unknown')}",
        f"Kickoff UTC: {fixture.get('kickoff_utc') or 'n/a'}",
        f"Kickoff local: {_fmt_local(fixture.get('kickoff_utc'))}",
        f"Expected FT UTC: {fixture.get('expected_ft_utc') or 'n/a'}",
        f"Last status: {fixture.get('last_status') or 'n/a'}",
        f"Last score: {_fmt_score(fixture.get('last_score'))}",
        f"Last seen UTC: {fixture.get('last_seen_utc') or 'n/a'}",
        f"Terminal UTC: {fixture.get('terminal_utc') or 'n/a'}",
        f"FT announced: {'yes' if fixture.get('ft_announced') else 'no'}",
        f"Memory updated: {'yes' if fixture.get('memory_updated') else 'no'}",
        f"Live message ID: {fixture.get('live_message_id') or 'n/a'}",
        f"Prunable now: {'yes' if prunable else 'no'}",
        f"Expected FT due: {'yes' if due else 'no'}",
    ]
    return _truncate("\n".join(lines))


def build_match_state_list(state: dict, now_utc: datetime, limit: int = LIST_LIMIT) -> str:
    fixtures = state.get("fixtures", {}) or {}
    lines = [f"**Tracked fixture state: {len(fixtures)} fixture(s)**"]
    if not fixtures:
        lines.append("No persisted football fixture state.")
        return "\n".join(lines)

    sorted_fixtures = sorted(
        fixtures.values(),
        key=lambda item: (
            item.get("kickoff_utc") or "",
            item.get("fixture_id") or "",
        ),
    )
    for fixture in sorted_fixtures[:limit]:
        flags = []
        if _expected_ft_due(fixture, now_utc):
            flags.append("FT due")
        if match_lifecycle.state_is_prunable(fixture, now_utc):
            flags.append("prunable")
        if fixture.get("ft_announced"):
            flags.append("FT posted")
        if fixture.get("memory_updated"):
            flags.append("memory")
        suffix = f" ({', '.join(flags)})" if flags else ""
        lines.append(
            f"- `{fixture.get('fixture_id', 'unknown')}` "
            f"{fixture.get('provider', 'unknown')} "
            f"{fixture.get('last_status', 'n/a')} "
            f"{_fmt_score(fixture.get('last_score'))}{suffix}"
        )

    if len(sorted_fixtures) > limit:
        lines.append(f"... showing first {limit}; use `!match_state <fixture_id>` for details.")
    return _truncate("\n".join(lines))


def build_lifecycle_summary(state: dict, now_utc: datetime) -> str:
    fixtures = list((state.get("fixtures", {}) or {}).values())
    status = api_provider.get_status()
    provider = "ESPN primary" if status.get("espn_healthy") else "API-Football fallback"
    active = sum(1 for fixture in fixtures if fixture.get("last_status") in match_lifecycle.LIVE_STATUSES)
    awaiting_ft = sum(1 for fixture in fixtures if _is_awaiting_ft_post(fixture, now_utc))
    awaiting_memory = sum(1 for fixture in fixtures if _is_awaiting_memory(fixture, now_utc))
    prunable = sum(1 for fixture in fixtures if match_lifecycle.state_is_prunable(fixture, now_utc))
    lookback = match_lifecycle.FOOTBALL_MATCH_LOOKBACK_HOURS()
    scheduler_status = scheduler.get_football_scheduler_status()
    tennis_scheduler_status = scheduler.get_tennis_scheduler_status()
    espn_requests = status.get("espn_league_requests_today", {})

    lines = [
        "**Football Lifecycle Health**",
        f"Tracked fixtures: {len(fixtures)}",
        f"Active/live: {active}",
        f"Awaiting FT post: {awaiting_ft}",
        f"Awaiting memory: {awaiting_memory}",
        f"Prunable/stale: {prunable}",
        f"Provider: {provider}",
        f"Poll interval: {status.get('poll_interval')}s",
        (
            "ESPN league requests this run (resets daily): "
            f"{espn_requests.get('total', 0)} "
            f"(active {espn_requests.get('active_refresh', 0)}, "
            f"discovery {espn_requests.get('full_discovery', 0)})"
        ),
        f"Scheduler: {scheduler_status.get('mode', 'unknown')}",
        f"Next football check: {_fmt_utc_value(scheduler_status.get('next_football_check_utc'))}",
        f"Next schedule refresh: {_fmt_utc_value(scheduler_status.get('next_schedule_refresh_utc'))}",
        f"Next planned kickoff: {_fmt_utc_value(scheduler_status.get('next_planned_kickoff_utc'))}",
        f"Next planned wake: {_fmt_utc_value(scheduler_status.get('next_planned_wake_utc'))}",
        f"Wake reason: {scheduler_status.get('wake_reason') or 'n/a'}",
        f"Wake detail: {scheduler_status.get('wake_reason_detail') or 'n/a'}",
        f"Sleep reason: {scheduler_status.get('sleep_reason') or 'n/a'}",
        f"Sleep detail: {scheduler_status.get('sleep_reason_detail') or 'n/a'}",
        f"Tennis scheduler: {tennis_scheduler_status.get('mode', 'unknown')}",
        f"Next tennis check: {_fmt_utc_value(tennis_scheduler_status.get('next_tennis_check_utc'))}",
        f"Next tennis schedule refresh: {_fmt_utc_value(tennis_scheduler_status.get('next_schedule_refresh_utc'))}",
        f"Next tennis planned start: {_fmt_utc_value(tennis_scheduler_status.get('next_planned_start_utc'))}",
        f"Next tennis planned wake: {_fmt_utc_value(tennis_scheduler_status.get('next_planned_wake_utc'))}",
        f"Tennis wake reason: {tennis_scheduler_status.get('wake_reason') or 'n/a'}",
        f"Tennis wake detail: {tennis_scheduler_status.get('wake_reason_detail') or 'n/a'}",
        f"Tennis sleep reason: {tennis_scheduler_status.get('sleep_reason') or 'n/a'}",
        f"Tennis sleep detail: {tennis_scheduler_status.get('sleep_reason_detail') or 'n/a'}",
        f"Timezone: {OPERATIONS_TIMEZONE}",
        f"Display lookup: +/-{FOOTBALL_DISPLAY_LOOKUP_WINDOW_HOURS}h",
        (
            "Lifecycle windows: "
            f"prematch {FOOTBALL_PREMATCH_WINDOW_HOURS}h, "
            f"lookback {lookback}h, "
            f"finished retention {FOOTBALL_FINISHED_RETENTION_HOURS}h, "
            f"state retention {FOOTBALL_STATE_RETENTION_HOURS}h, "
            f"expected FT {FOOTBALL_EXPECTED_FT_MINUTES}m, "
            f"max live {FOOTBALL_MAX_LIVE_DURATION_HOURS}h"
        ),
    ]
    return _truncate("\n".join(lines))


class FootballLifecycle(commands.Cog):
    """Admin diagnostics for the UTC-first football lifecycle."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="match_state",
        aliases=["matchstate"],
        help="Admin: inspect persisted football fixture lifecycle state.",
    )
    @operator_only()
    async def match_state_command(self, ctx: commands.Context, fixture_id: str | None = None):
        state = match_state.load_match_state()
        now = utc_now()
        if fixture_id:
            fixture = state.get("fixtures", {}).get(str(fixture_id))
            if fixture is None:
                content = f"No match_state fixture found for `{fixture_id}`."
            else:
                content = build_match_state_detail(fixture, now)
        else:
            content = build_match_state_list(state, now)
        await post_new_message_to_context(ctx, content=content)

    @commands.command(
        name="football_lifecycle",
        aliases=["footballlife", "lifecycle"],
        help="Admin: summarize UTC-first football lifecycle health.",
    )
    @operator_only()
    async def football_lifecycle_command(self, ctx: commands.Context):
        content = build_lifecycle_summary(match_state.load_match_state(), utc_now())
        await post_new_message_to_context(ctx, content=content)


async def setup(bot: commands.Bot):
    await bot.add_cog(FootballLifecycle(bot))
    logger.info("cogs.football_lifecycle loaded")
