# cogs/next_command.py
import logging
from discord.ext import commands

from config import LEAGUE_NAME_MAP, LEAGUE_SLUG_MAP, DOMESTIC_SLUG_GROUPS, INTERNATIONAL_SLUGS
from utils.espn_client import search_team_espn, fetch_next_team_fixture_espn
from utils.time_utils import parse_utc_to_italy
from modules.discord_poster import post_new_message_to_context

logger = logging.getLogger(__name__)

_TRACKED_SLUGS: set = set(LEAGUE_SLUG_MAP.values())


def _build_slugs(primary_slug: str) -> list:
    domestic = DOMESTIC_SLUG_GROUPS.get(primary_slug, [primary_slug])
    return list(dict.fromkeys(domestic + INTERNATIONAL_SLUGS))


class NextCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="next",
        help="Show any team's next scheduled match. Usage: !next <team name>"
    )
    async def next_match(self, ctx: commands.Context, *, team_name: str):
        await ctx.typing()

        result = await search_team_espn(self.bot.http_session, team_name, _TRACKED_SLUGS)
        if result is None:
            await post_new_message_to_context(
                ctx,
                content=(
                    f'Could not find a tracked team matching "{team_name}". '
                    'Check the spelling or try a more specific name (e.g. "Manchester United", "Juventus").'
                )
            )
            return

        espn_team_id, primary_slug = result
        slugs = _build_slugs(primary_slug)

        match = await fetch_next_team_fixture_espn(self.bot.http_session, espn_team_id, slugs)
        if match is None:
            await post_new_message_to_context(
                ctx,
                content=f'No upcoming match found for "{team_name.title()}" in the next 14 days.'
            )
            return

        fixture = match.get("fixture", {})
        teams = match.get("teams", {})
        league_id = match.get("league", {}).get("id", 0)

        date_str = fixture.get("date")
        if not date_str:
            await post_new_message_to_context(ctx, content="Found a match but could not parse its date.")
            return

        match_dt = parse_utc_to_italy(date_str)
        home_name = teams.get("home", {}).get("name", "?")
        away_name = teams.get("away", {}).get("name", "?")
        competition = LEAGUE_NAME_MAP.get(league_id, f"League {league_id}")

        query_lower = team_name.lower()
        is_home = query_lower in home_name.lower()
        is_away = query_lower in away_name.lower()

        if is_home or is_away:
            team_display = home_name if is_home else away_name
            opponent = away_name if is_home else home_name
            venue_label = "Home" if is_home else f"Away vs {home_name}"
            message = (
                f"Next match for **{team_display}**\n"
                f"-----------------------------------\n"
                f"🆚 **Opponent:** {opponent}\n"
                f"🗓️ **Date:** {match_dt.strftime('%A, %B %d, %Y')}\n"
                f"⏰ **Time:** {match_dt.strftime('%H:%M')} (Italy Time)\n"
                f"🏆 **Competition:** {competition}\n"
                f"🏟️ **Venue:** {venue_label}\n"
                f"-----------------------------------"
            )
        else:
            # Substring match failed (e.g. abbreviated query) — show neutral format
            message = (
                f"Next match: **{home_name} vs {away_name}**\n"
                f"-----------------------------------\n"
                f"🗓️ **Date:** {match_dt.strftime('%A, %B %d, %Y')}\n"
                f"⏰ **Time:** {match_dt.strftime('%H:%M')} (Italy Time)\n"
                f"🏆 **Competition:** {competition}\n"
                f"-----------------------------------"
            )

        await post_new_message_to_context(ctx, content=message)


async def setup(bot: commands.Bot):
    await bot.add_cog(NextCommand(bot))
    logger.info("✔ cogs.next_command loaded")
