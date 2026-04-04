# cogs/mode.py
import logging
from discord.ext import commands
from modules.bot_mode import is_silent, set_silent
from modules.discord_poster import post_new_message_to_context

logger = logging.getLogger(__name__)


class Mode(commands.Cog):
    """Toggle automatic broadcast messages on or off."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="silent",
        aliases=["Silent", "SILENT"],
        help="Pause automatic broadcasts (startup message, morning fixture list). Live match updates still post."
    )
    async def silent(self, ctx: commands.Context):
        set_silent(True)
        logger.info("🔇 Silent mode enabled.")
        await post_new_message_to_context(
            ctx,
            content="🔇 **Silent mode ON.** Automatic broadcasts paused. Live match updates and commands still work."
        )

    @commands.command(
        name="verbose",
        aliases=["Verbose", "VERBOSE"],
        help="Resume automatic broadcasts (startup message, morning fixture list)."
    )
    async def verbose(self, ctx: commands.Context):
        set_silent(False)
        logger.info("🔔 Verbose mode enabled.")
        await post_new_message_to_context(
            ctx,
            content="🔔 **Verbose mode ON.** Automatic broadcasts resumed."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Mode(bot))
    logger.info("✔ cogs.mode loaded")
