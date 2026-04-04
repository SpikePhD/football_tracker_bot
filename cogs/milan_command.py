# cogs/milan_command.py
import logging
from discord.ext import commands

from config import AC_MILAN_TEAM_ID, AC_MILAN_ESPN_TEAM_ID, AC_MILAN_LEAGUE_SLUGS, LEAGUE_NAME_MAP
from utils.espn_client import fetch_next_team_fixture_espn
from utils.time_utils import parse_utc_to_italy
from modules.discord_poster import post_new_message_to_context

logger = logging.getLogger(__name__)


class MilanCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="milan",
        aliases=["nextmilan", "acmilan"],
        help="Shows AC Milan's next scheduled match."
    )
    async def milan_next_match(self, ctx: commands.Context):
        await ctx.typing()

        match = await fetch_next_team_fixture_espn(
            self.bot.http_session,
            AC_MILAN_ESPN_TEAM_ID,
            AC_MILAN_LEAGUE_SLUGS,
        )

        if not match:
            await post_new_message_to_context(
                ctx,
                content="Could not find AC Milan's next scheduled match. Try again later."
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

        is_home = "milan" in home_name.lower()
        opponent = away_name if is_home else home_name
        venue = "San Siro (Home)" if is_home else f"Away vs {home_name}"

        message = (
            f"🔴⚫ Next AC Milan Match ⚫🔴\n"
            f"-----------------------------------\n"
            f"🆚 **Opponent:** {opponent}\n"
            f"🗓️ **Date:** {match_dt.strftime('%A, %B %d, %Y')}\n"
            f"⏰ **Time:** {match_dt.strftime('%H:%M')} (Italy Time)\n"
            f"🏆 **Competition:** {competition}\n"
            f"🏟️ **Venue:** {venue}\n"
            f"-----------------------------------"
        )
        await post_new_message_to_context(ctx, content=message)


async def setup(bot: commands.Bot):
    await bot.add_cog(MilanCommand(bot))
    logger.info("✔ cogs.milan_command loaded")
