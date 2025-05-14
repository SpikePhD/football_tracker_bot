# cogs/hello.py
import logging
from discord.ext import commands
from utils.personality import get_greeting # For the greeting message
# MODIFIED: Import from the new discord_poster module
from modules.discord_poster import post_new_message_to_context

logger = logging.getLogger(__name__)

class Hello(commands.Cog):
    """A small cog to let users say hi to the bot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="hi", 
        aliases=["hello"], 
        help="Say hi and get a random greeting back!"
    )
    async def hi(self, ctx: commands.Context):
        greeting = get_greeting()
        # MODIFIED: Use discord_poster
        await post_new_message_to_context(ctx, content=greeting)

async def setup(bot: commands.Bot):
    await bot.add_cog(Hello(bot))
    logger.info("✔ cogs.hello loaded")
