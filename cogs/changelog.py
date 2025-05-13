# cogs/changelog.py
import logging
logger = logging.getLogger(__name__)
import pathlib
from discord.ext import commands

class Changelog(commands.Cog):
    """Read & post your CHANGELOG.md file on command."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # assume your project layout is:
        #  /football_tracker_bot.py
        #  /CHANGELOG.md
        #  /cogs/changelog.py
        self.changelog_path = pathlib.Path(__file__).parent.parent / "CHANGELOG.md"

    @commands.command(
        name="changelog",
        help="Show the contents of CHANGELOG.md"
    )
    async def changelog(self, ctx: commands.Context):
        if not self.changelog_path.exists():
            return await ctx.send("❌ CHANGELOG.md not found.")
        text = self.changelog_path.read_text(encoding="utf-8").strip()
        # Discord has a 2000‐char limit per message, so we may need to split:
        chunks = []
        while text:
            chunk, text = text[:1990], text[1990:]
            chunks.append(f"```md\n{chunk}\n```")
        for chunk in chunks:
            await ctx.send(chunk)

async def setup(bot: commands.Bot):
    await bot.add_cog(Changelog(bot))
    logger.info("✔ cogs.changelog loaded")
