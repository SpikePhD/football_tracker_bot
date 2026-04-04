# cogs/mode.py
import logging
from discord.ext import commands
from modules.bot_mode import get_mode, set_mode
from modules.discord_poster import post_new_message_to_context

logger = logging.getLogger(__name__)


class Mode(commands.Cog):
    """Control the bot's broadcast mode."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="verbose",
        aliases=["Verbose", "VERBOSE"],
        help="Enable verbose mode: startup message, morning broadcast, live updates, FT results."
    )
    async def verbose(self, ctx: commands.Context):
        set_mode("verbose")
        logger.info("🔔 Verbose mode enabled.")
        await post_new_message_to_context(
            ctx,
            content="🔔 **Verbose mode.** Startup message, morning broadcast, live updates and FT results all active."
        )

    @commands.command(
        name="normal",
        aliases=["Normal", "NORMAL"],
        help="Enable normal mode: live match updates and FT results only. No startup or morning broadcasts."
    )
    async def normal(self, ctx: commands.Context):
        set_mode("normal")
        logger.info("⚽ Normal mode enabled.")
        await post_new_message_to_context(
            ctx,
            content="⚽ **Normal mode.** Live match updates and FT results active. Startup and morning broadcasts paused."
        )

    @commands.command(
        name="silent",
        aliases=["Silent", "SILENT"],
        help="Enable silent mode: bot never posts automatically. Commands still work."
    )
    async def silent(self, ctx: commands.Context):
        set_mode("silent")
        logger.info("🔇 Silent mode enabled.")
        await post_new_message_to_context(
            ctx,
            content="🔇 **Silent mode.** Bot will not post anything automatically. Commands still work."
        )

    @commands.command(
        name="mode",
        help="Show the current broadcast mode."
    )
    async def mode(self, ctx: commands.Context):
        current = get_mode()
        descriptions = {
            "verbose": "🔔 **Verbose** — startup message, morning broadcast, live updates, FT results",
            "normal":  "⚽ **Normal** — live updates and FT results only",
            "silent":  "🔇 **Silent** — commands only, no automatic posts",
        }
        await post_new_message_to_context(ctx, content=f"Current mode: {descriptions[current]}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Mode(bot))
    logger.info("✔ cogs.mode loaded")
