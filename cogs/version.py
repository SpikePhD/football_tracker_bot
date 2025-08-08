# cogs/version.py

import logging
import subprocess
import pathlib
from discord.ext import commands

logger = logging.getLogger(__name__)

# Resolve repo root (works whether this file is in cogs/ or repo root)
REPO_DIR = pathlib.Path(__file__).resolve().parent
if (REPO_DIR / ".git").is_dir():
    pass
else:
    REPO_DIR = REPO_DIR.parent

def get_commit_desc() -> str:
    """
    Return a human-ish git identifier. Tries `describe` (tags), falls back to short SHA.
    """
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_DIR), "describe", "--always", "--dirty", "--tags"],
            text=True, stderr=subprocess.STDOUT
        ).strip()
    except Exception:
        try:
            return subprocess.check_output(
                ["git", "-C", str(REPO_DIR), "rev-parse", "--short", "HEAD"],
                text=True, stderr=subprocess.STDOUT
            ).strip()
        except Exception:
            return "unknown"

class VersionCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="version",
        aliases=["ver", "commit"],
        help="Show the running bot version/commit."
    )
    async def version_cmd(self, ctx: commands.Context):
        commit = get_commit_desc()
        await ctx.send(f"Running commit: `{commit}`")

async def setup(bot: commands.Bot):
    await bot.add_cog(VersionCommand(bot))
    logger.info("✔ cogs.version_command loaded")
