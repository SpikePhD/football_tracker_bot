# cogs/tennis.py
import logging

from discord.ext import commands

from modules import api_provider
from modules.discord_poster import post_new_message_to_context
from utils.tennis_formatter import (
    format_tennis_final_message,
    format_tennis_live_message,
    format_tennis_pre_message,
)

logger = logging.getLogger(__name__)


class Tennis(commands.Cog):
    """Show today's tracked tennis matches."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="tennis",
        help="List today's tracked tennis matches for configured players.",
    )
    async def tennis(self, ctx: commands.Context):
        matches = await api_provider.fetch_tennis_day(self.bot.http_session)
        if not matches:
            await post_new_message_to_context(ctx, content="No tracked tennis matches found today.")
            return

        lines = ["**Tracked tennis matches today:**"]
        for match in matches:
            status = match.get("status", {}).get("short")
            if status == "NS":
                lines.append("\n" + format_tennis_pre_message(match))
            elif status == "LIVE":
                lines.append("\n" + format_tennis_live_message(match))
            elif status == "FT":
                lines.append("\n" + format_tennis_final_message(match))
            else:
                lines.append(
                    f"\n🎾 {match.get('player_a')} vs {match.get('player_b')} "
                    f"({match.get('event_name')} - {match.get('tour')}) [{status}]"
                )

        await post_new_message_to_context(ctx, content="\n".join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(Tennis(bot))
    logger.info("cogs.tennis loaded")
