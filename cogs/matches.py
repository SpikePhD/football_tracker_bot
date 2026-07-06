# cogs/matches.py
import logging
from collections import defaultdict
from datetime import timedelta
from discord.ext import commands
from config import LEAGUE_NAME_MAP, TENNIS_UPCOMING_DAYS
from modules import api_provider, football_memory, match_lifecycle
from utils.time_utils import to_bot_tz, bot_now, utc_now
from utils.event_formatter import (
    format_match_events,
    format_shootout_segments,
    event_completeness_note,
    is_counted_goal_event,
    normal_match_events,
    prune_goal_events_to_score,
)
from modules.discord_poster import post_new_message_to_context
from utils.tennis_formatter import format_tennis_snapshot_line
from utils.tennis_lifecycle import tennis_final_data_ready

logger = logging.getLogger(__name__)


def _football_sort_key(match: dict):
    return match.get("fixture", {}).get("date") or ""


def _football_local_date(match: dict):
    kickoff = match_lifecycle.fixture_kickoff_utc(match)
    return to_bot_tz(kickoff).date() if kickoff else None


def _goal_event_count(events: list) -> int:
    return sum(1 for event in normal_match_events(events or []) if is_counted_goal_event(event))


def _total_goals(match: dict) -> int | None:
    goals = match.get("goals", {}) or {}
    try:
        return int(goals.get("home", 0) or 0) + int(goals.get("away", 0) or 0)
    except (TypeError, ValueError):
        return None


def _events_are_better_for_display(match: dict, candidate_events: list) -> bool:
    current_events = match.get("events", []) or []
    candidate_match, _pruned = prune_goal_events_to_score({**match, "events": list(candidate_events or [])})
    candidate_events = candidate_match.get("events", [])
    candidate_goals = _goal_event_count(candidate_events)
    current_goals = _goal_event_count(current_events)
    if candidate_goals <= current_goals:
        return False

    total_goals = _total_goals(match)
    if total_goals is None:
        return True
    return abs(total_goals - candidate_goals) <= abs(total_goals - current_goals)


def _apply_persisted_ft_events(fixtures: list) -> list:
    if not any(match_lifecycle.is_ft(match) for match in fixtures):
        return fixtures

    try:
        persisted_matches = football_memory.load_memory().get("matches", {})
    except Exception as e:
        logger.warning("Could not load football memory for snapshot event reuse: %s", e)
        return fixtures

    merged = []
    for match in fixtures:
        fixture_id = match_lifecycle.fixture_identity(match)
        persisted = persisted_matches.get(str(fixture_id)) if fixture_id is not None else None
        persisted_events = persisted.get("events", []) if isinstance(persisted, dict) else []
        if (
            match_lifecycle.is_ft(match)
            and isinstance(persisted_events, list)
            and _events_are_better_for_display(match, persisted_events)
        ):
            sanitized, _pruned = prune_goal_events_to_score({**match, "events": list(persisted_events)})
            merged.append(sanitized)
        else:
            merged.append(match)
    return merged


def filter_football_for_local_matchday(fixtures: list, now_utc) -> list:
    """Public daily football snapshot: local today plus active/recent carry-over."""
    local_today = to_bot_tz(now_utc).date()
    filtered = []
    for match in fixtures:
        if match_lifecycle.is_live(match):
            filtered.append(match)
            continue

        local_date = _football_local_date(match)
        if local_date == local_today:
            filtered.append(match)
            continue

        if (
            local_date is not None
            and local_date < local_today
            and match_lifecycle.is_recently_finished(match, now_utc)
        ):
            filtered.append(match)

    return sorted(filtered, key=_football_sort_key)


def filter_upcoming_football_fixtures(fixtures: list, now_utc) -> list:
    """Future not-started football fixtures for the explicit upcoming view."""
    local_today = to_bot_tz(now_utc).date()
    upcoming = []
    for match in fixtures:
        if match_lifecycle.is_live(match) or match_lifecycle.is_terminal(match):
            continue
        local_date = _football_local_date(match)
        if local_date is not None and local_date > local_today:
            upcoming.append(match)
    return sorted(upcoming, key=_football_sort_key)


def _format_football_fixture_line(match: dict) -> str:
    match, pruned_goal_events = prune_goal_events_to_score(match)
    if pruned_goal_events:
        logger.info(
            "Pruned %d surplus goal event(s) before football display render for fixture %s.",
            pruned_goal_events,
            match_lifecycle.fixture_identity(match),
        )
    ko_dt = to_bot_tz(match["fixture"]["date"])
    status = match_lifecycle.status_short(match) or "N/A"
    home = match.get("teams", {}).get("home", {}).get("name", "Home Team")
    away = match.get("teams", {}).get("away", {}).get("name", "Away Team")
    goals = match.get("goals", {"home": "?", "away": "?"})

    if status == "NS":
        return f"- {ko_dt.strftime('%H:%M')} - {home} vs {away}"
    if match_lifecycle.is_ft(match):
        events = match.get("events", [])
        ft_event_parts = format_match_events(events, home, away)
        score_str = f"{home} {goals.get('home', '?')}-{goals.get('away', '?')} {away}"
        completeness = api_provider.event_completeness_status(match)
        note = event_completeness_note(
            goals,
            events,
            show_warning=completeness["status"] == api_provider.EVENTS_EXHAUSTED_MISSING,
        )
        shootout_segments = format_shootout_segments(match, final=True)
        if ft_event_parts:
            line = f"- FT: {score_str} ({'; '.join(ft_event_parts)})"
        else:
            line = f"- FT: {score_str}"
        if shootout_segments:
            line += " | " + " | ".join(shootout_segments)
        return f"{line}{note}"

    elapsed = match.get("fixture", {}).get("status", {}).get("elapsed")
    if status == "HT":
        minute_str = "HT"
    elif elapsed:
        minute_str = f"{elapsed}'"
    else:
        minute_str = status

    events = match.get("events", [])
    event_parts = format_match_events(events, home, away)
    score_str = f"{home} {goals.get('home', '?')}-{goals.get('away', '?')} {away}"
    completeness = api_provider.event_completeness_status(match)
    note = event_completeness_note(
        goals,
        events,
        show_warning=completeness["status"] == api_provider.EVENTS_EXHAUSTED_MISSING,
    )
    shootout_segments = format_shootout_segments(match, final=False)
    if event_parts:
        line = f"- LIVE [{minute_str}]: {score_str} ({'; '.join(event_parts)})"
    else:
        line = f"- LIVE [{minute_str}]: {score_str}"
    if shootout_segments:
        line += " | " + " | ".join(shootout_segments)
    return f"{line}{note}"


def build_football_section(
    fixtures: list,
    *,
    empty_message: str = "No tracked football matches today.",
    group_by_local_date: bool = False,
    title: str = "**Football**",
) -> str:
    """
    Format football fixtures into a grouped-by-competition Discord section.
    Returns the full message string (without a trailing newline).
    """
    if not fixtures:
        return f"{title}\n{empty_message}"

    if group_by_local_date:
        lines = [title]
        by_date: dict[str, list] = defaultdict(list)
        for match in fixtures:
            local_date = _football_local_date(match)
            by_date[local_date.isoformat() if local_date else "Date unknown"].append(match)

        for local_date in sorted(by_date):
            lines.append(f"\n**{local_date}**")
            lines.extend(_build_football_competition_lines(by_date[local_date]))
        return "\n".join(lines)

    lines = [title]
    lines.extend(_build_football_competition_lines(fixtures))
    return "\n".join(lines)


def _build_football_competition_lines(fixtures: list) -> list[str]:
    # Group by league ID
    groups: dict[int, list] = defaultdict(list)
    for m in fixtures:
        league_id = m.get('league', {}).get('id', 0)
        groups[league_id].append(m)

    # Sort groups by earliest KO in each group
    def group_sort_key(league_id):
        return min(m['fixture']['date'] for m in groups[league_id])

    sorted_league_ids = sorted(groups.keys(), key=group_sort_key)

    lines = []

    for league_id in sorted_league_ids:
        league_name = LEAGUE_NAME_MAP.get(league_id, f"League {league_id}")
        lines.append(f"\n**{league_name}:**")

        group_fixtures = sorted(groups[league_id], key=_football_sort_key)
        for m in group_fixtures:
            lines.append(_format_football_fixture_line(m))

    return lines


def build_upcoming_football_message(fixtures: list) -> str:
    return build_football_section(
        fixtures,
        empty_message="No upcoming tracked football fixtures in the display window.",
        group_by_local_date=True,
        title="**Upcoming football fixtures:**",
    )


def build_tennis_section(matches: list) -> str:
    lines = ["**Tennis**"]
    if not matches:
        lines.append("No tracked tennis matches live, upcoming, or finished today.")
        return "\n".join(lines)

    # Only include matches that are actually today (live, upcoming today, or finished today)
    live = sorted(
        [m for m in matches if m.get("status", {}).get("short") == "LIVE"],
        key=lambda m: m.get("start_time") or "",
    )
    # Only show upcoming matches that are scheduled for TODAY
    upcoming = sorted(
        [
            m for m in matches
            if m.get("status", {}).get("short") == "NS"
            and _is_tennis_today(m)
        ],
        key=lambda m: m.get("start_time") or "",
    )
    finished_today = sorted(
        [
            m for m in matches
            if m.get("status", {}).get("short") == "FT"
            and _is_tennis_today(m)
            and tennis_final_data_ready(m)
        ],
        key=lambda m: m.get("start_time") or "",
        reverse=True,
    )

    if not (live or upcoming or finished_today):
        lines.append("No tracked tennis matches live, upcoming, or finished today.")
        return "\n".join(lines)

    for match in live:
        lines.append(format_tennis_snapshot_line(match))
    for match in upcoming:
        lines.append(format_tennis_snapshot_line(match))
    for match in finished_today:
        lines.append(format_tennis_snapshot_line(match))

    return "\n".join(lines)


def build_combined_matches_message(football_fixtures: list, tennis_matches: list, now_utc=None) -> str:
    current_time = to_bot_tz(now_utc) if now_utc else bot_now()
    return "\n\n".join([
        f"**Tracked sports ({current_time.strftime('%Y-%m-%d')}):**",
        build_football_section(football_fixtures),
        build_tennis_section(tennis_matches),
    ])


async def build_combined_matches_message_from_api(session) -> str:
    _, _, content = await fetch_combined_matches_snapshot(session)
    return content


async def fetch_combined_matches_snapshot(session) -> tuple[list, list, str]:
    now = utc_now()
    football_fixtures = []
    tennis_matches = []

    try:
        football_fixtures = filter_football_for_local_matchday(
            await api_provider.fetch_day(session),
            now,
        )
        football_fixtures = await api_provider.enrich_fixtures(session, football_fixtures)
        football_fixtures = _apply_persisted_ft_events(football_fixtures)
    except Exception as e:
        logger.error(f"Failed to fetch football snapshot: {e}", exc_info=True)

    try:
        tennis_matches = await api_provider.fetch_tennis_day(session)
    except Exception as e:
        logger.error(f"Failed to fetch tennis snapshot: {e}", exc_info=True)

    return (
        football_fixtures,
        tennis_matches,
        build_combined_matches_message(football_fixtures, tennis_matches, now_utc=now),
    )


async def build_upcoming_football_message_from_api(session) -> str:
    now = utc_now()
    fixtures = await api_provider.fetch_day(session)
    return build_upcoming_football_message(filter_upcoming_football_fixtures(fixtures, now))


def _is_tennis_today(match: dict) -> bool:
    start = match.get("start_time")
    if not start:
        return False
    try:
        return to_bot_tz(start).date() == bot_now().date()
    except Exception:
        return False


def _is_tennis_upcoming(match: dict, horizon_days: int) -> bool:
    start = match.get("start_time")
    if not start:
        return False
    try:
        dt = to_bot_tz(start)
    except Exception:
        return False
    now = bot_now()
    return now < dt <= now + timedelta(days=horizon_days)


class Matches(commands.Cog):
    """Show tracked fixtures, grouped by competition."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="matches",
        help="List tracked football and tennis events."
    )
    async def matches(self, ctx: commands.Context):
        content = await build_combined_matches_message_from_api(self.bot.http_session)
        await post_new_message_to_context(ctx, content=content)

    @commands.command(
        name="upcoming",
        help="List upcoming tracked football fixtures grouped by local date."
    )
    async def upcoming(self, ctx: commands.Context):
        content = await build_upcoming_football_message_from_api(self.bot.http_session)
        await post_new_message_to_context(ctx, content=content)


async def setup(bot: commands.Bot):
    await bot.add_cog(Matches(bot))
    logger.info("cogs.matches loaded")
