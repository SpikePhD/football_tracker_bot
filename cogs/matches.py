# cogs/matches.py
import logging
from collections import defaultdict
from datetime import timedelta
from discord.ext import commands
from config import LEAGUE_NAME_MAP, TENNIS_UPCOMING_DAYS
from modules import api_provider
from utils.time_utils import parse_utc_to_italy, italy_now
from utils.event_formatter import format_match_events, event_completeness_note
from modules.discord_poster import post_new_message_to_context
from utils.tennis_formatter import format_tennis_snapshot_line

logger = logging.getLogger(__name__)


def build_football_section(fixtures: list) -> str:
    """
    Format football fixtures into a grouped-by-competition Discord section.
    Returns the full message string (without a trailing newline).
    """
    if not fixtures:
        return "**⚽ Football**\nNo tracked football matches today."

    # Group by league ID
    groups: dict[int, list] = defaultdict(list)
    for m in fixtures:
        league_id = m.get('league', {}).get('id', 0)
        groups[league_id].append(m)

    # Sort groups by earliest KO in each group
    def group_sort_key(league_id):
        return min(m['fixture']['date'] for m in groups[league_id])

    sorted_league_ids = sorted(groups.keys(), key=group_sort_key)

    lines = ["**⚽ Football**"]

    for league_id in sorted_league_ids:
        league_name = LEAGUE_NAME_MAP.get(league_id, f"League {league_id}")
        lines.append(f"\n**{league_name}:**")

        group_fixtures = sorted(groups[league_id], key=lambda m: m['fixture']['date'])
        for m in group_fixtures:
            ko_dt = parse_utc_to_italy(m['fixture']['date'])
            status = m.get('fixture', {}).get('status', {}).get('short', 'N/A')
            home = m.get('teams', {}).get('home', {}).get('name', 'Home Team')
            away = m.get('teams', {}).get('away', {}).get('name', 'Away Team')
            goals = m.get('goals', {'home': '?', 'away': '?'})

            if status == "NS":
                time_str = ko_dt.strftime("%H:%M")
                lines.append(f"• {time_str} — {home} vs {away}")
            elif status == "FT":
                events = m.get('events', [])
                ft_event_parts = format_match_events(events, home, away)
                score_str = f"{home} {goals.get('home', '?')}-{goals.get('away', '?')} {away}"
                note = event_completeness_note(goals, events)
                if ft_event_parts:
                    lines.append(f"• FT: {score_str} ({'; '.join(ft_event_parts)}){note}")
                else:
                    lines.append(f"• FT: {score_str}{note}")
            else:
                elapsed = m.get('fixture', {}).get('status', {}).get('elapsed')
                if status == "HT":
                    minute_str = "HT"
                elif elapsed:
                    minute_str = f"{elapsed}'"
                else:
                    minute_str = status

                events = m.get('events', [])
                event_parts = format_match_events(events, home, away)
                score_str = f"{home} {goals.get('home', '?')}-{goals.get('away', '?')} {away}"
                note = event_completeness_note(goals, events)
                if event_parts:
                    lines.append(f"• LIVE [{minute_str}]: {score_str} ({'; '.join(event_parts)}){note}")
                else:
                    lines.append(f"• LIVE [{minute_str}]: {score_str}{note}")

    return "\n".join(lines)


def build_tennis_section(matches: list) -> str:
    lines = ["**🎾 Tennis**"]
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


def build_combined_matches_message(football_fixtures: list, tennis_matches: list) -> str:
    current_time = italy_now()
    return "\n\n".join([
        f"**Today's tracked sports ({current_time.strftime('%Y-%m-%d')}):**",
        build_football_section(football_fixtures),
        build_tennis_section(tennis_matches),
    ])


async def build_combined_matches_message_from_api(session) -> str:
    football_fixtures = []
    tennis_matches = []

    try:
        football_fixtures = await api_provider.fetch_day(session)
        football_fixtures = await api_provider.enrich_fixtures(session, football_fixtures)
        football_fixtures.sort(key=lambda m: m["fixture"]["date"])
    except Exception as e:
        logger.error(f"Failed to fetch football snapshot: {e}", exc_info=True)

    try:
        tennis_matches = await api_provider.fetch_tennis_day(session)
    except Exception as e:
        logger.error(f"Failed to fetch tennis snapshot: {e}", exc_info=True)

    return build_combined_matches_message(football_fixtures, tennis_matches)


def _is_tennis_today(match: dict) -> bool:
    start = match.get("start_time")
    if not start:
        return False
    try:
        return parse_utc_to_italy(start).date() == italy_now().date()
    except Exception:
        return False


def _is_tennis_upcoming(match: dict, horizon_days: int) -> bool:
    start = match.get("start_time")
    if not start:
        return False
    try:
        dt = parse_utc_to_italy(start)
    except Exception:
        return False
    now = italy_now()
    return now < dt <= now + timedelta(days=horizon_days)


class Matches(commands.Cog):
    """Show today's tracked fixtures, grouped by competition."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="matches",
        help="List today's tracked football and tennis events."
    )
    async def matches(self, ctx: commands.Context):
        content = await build_combined_matches_message_from_api(self.bot.http_session)
        await post_new_message_to_context(ctx, content=content)


async def setup(bot: commands.Bot):
    await bot.add_cog(Matches(bot))
    logger.info("✔ cogs.matches loaded")
