# cogs/matches.py
import logging
from collections import defaultdict
from discord.ext import commands
from config import LEAGUE_NAME_MAP
from modules import api_provider
from utils.time_utils import parse_utc_to_italy, italy_now
from modules.discord_poster import post_new_message_to_context

logger = logging.getLogger(__name__)


def build_matches_message(fixtures: list) -> str:
    """
    Format a list of fixture dicts into a grouped-by-competition Discord message.
    Returns the full message string (without a trailing newline).
    """
    current_time = italy_now()

    if not fixtures:
        return f"**Today's tracked matches ({current_time.strftime('%Y-%m-%d')}):**\nNo tracked matches today."

    # Group by league ID
    groups: dict[int, list] = defaultdict(list)
    for m in fixtures:
        league_id = m.get('league', {}).get('id', 0)
        groups[league_id].append(m)

    # Sort groups by earliest KO in each group
    def group_sort_key(league_id):
        return min(m['fixture']['date'] for m in groups[league_id])

    sorted_league_ids = sorted(groups.keys(), key=group_sort_key)

    lines = [f"**Today's tracked matches ({current_time.strftime('%Y-%m-%d')}):**"]

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
                ft_event_parts = []
                for e in m.get('events', []):
                    if e['type'] == 'Goal':
                        tag = f" ({e['detail']})" if e['detail'] != "Normal Goal" else ""
                        side = "(H)" if e['team']['name'] == home else "(A)"
                        ft_event_parts.append(f"{e['time']['elapsed']}' - {e['player']['name']}{tag} {side}")
                    elif e['type'] == 'Card' and e['detail'] == 'Red Card':
                        side = "(H)" if e['team']['name'] == home else "(A)"
                        ft_event_parts.append(f"{e['time']['elapsed']}' - {e['player']['name']} (Red Card) {side}")
                score_str = f"{home} {goals.get('home', '?')}-{goals.get('away', '?')} {away}"
                if ft_event_parts:
                    lines.append(f"• FT: {score_str} ({'; '.join(ft_event_parts)})")
                else:
                    lines.append(f"• FT: {score_str}")
            else:
                elapsed = m.get('fixture', {}).get('status', {}).get('elapsed')
                if status == "HT":
                    minute_str = "HT"
                elif elapsed:
                    minute_str = f"{elapsed}'"
                else:
                    minute_str = status

                event_parts = []
                for e in m.get('events', []):
                    if e['type'] == 'Goal':
                        tag = f" ({e['detail']})" if e['detail'] != "Normal Goal" else ""
                        side = "(H)" if e['team']['name'] == home else "(A)"
                        event_parts.append(f"{e['time']['elapsed']}' - {e['player']['name']}{tag} {side}")
                    elif e['type'] == 'Card' and e['detail'] == 'Red Card':
                        side = "(H)" if e['team']['name'] == home else "(A)"
                        event_parts.append(f"{e['time']['elapsed']}' - {e['player']['name']} (Red Card) {side}")

                score_str = f"{home} {goals.get('home', '?')}-{goals.get('away', '?')} {away}"
                if event_parts:
                    lines.append(f"• LIVE [{minute_str}]: {score_str} ({'; '.join(event_parts)})")
                else:
                    lines.append(f"• LIVE [{minute_str}]: {score_str}")

    return "\n".join(lines)


class Matches(commands.Cog):
    """Show today's tracked fixtures, grouped by competition."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="matches",
        help="List today's tracked fixtures grouped by competition."
    )
    async def matches(self, ctx: commands.Context):
        fixtures = await api_provider.fetch_day(self.bot.http_session)
        fixtures.sort(key=lambda m: m['fixture']['date'])
        await post_new_message_to_context(ctx, content=build_matches_message(fixtures))


async def setup(bot: commands.Bot):
    await bot.add_cog(Matches(bot))
    logger.info("✔ cogs.matches loaded")
