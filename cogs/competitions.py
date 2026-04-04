# cogs/competitions.py
import logging
from discord.ext import commands
from config import TRACKED_LEAGUE_IDS, LEAGUE_NAME_MAP
from modules.discord_poster import post_new_message_to_context

logger = logging.getLogger(__name__)

class Competitions(commands.Cog):
    """Show the list of competitions the bot is tracking."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="competitions",
        help="List the competitions I'm currently tracking."
    )
    async def competitions(self, ctx: commands.Context):
        names = [LEAGUE_NAME_MAP.get(lid, f"Unknown League (ID: {lid})")
                 for lid in TRACKED_LEAGUE_IDS]

        if not names:
            message_content = "I’m not currently configured to track any specific competitions."
        else:
            comp_list = ", ".join(sorted(names)) # Sorted for consistent output
            message_content = f"I’m tracking these competitions:\n{comp_list}"

        await post_new_message_to_context(ctx, content=message_content)

async def setup(bot: commands.Bot):
    await bot.add_cog(Competitions(bot))
    logger.info("✔ cogs.competitions loaded")
