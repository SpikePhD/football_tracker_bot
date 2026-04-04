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

def _git(*args) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_DIR), *args],
            text=True, stderr=subprocess.STDOUT
        ).strip()
    except Exception:
        return "unknown"


def get_version_info() -> dict:
    short_sha  = _git("rev-parse", "--short", "HEAD")
    commit_msg = _git("log", "-1", "--pretty=format:%s")
    commit_date = _git("log", "-1", "--pretty=format:%ci")   # e.g. 2026-04-04 19:07:31 +0200
    # Trim to just date + time, drop timezone offset for readability
    if commit_date != "unknown":
        commit_date = commit_date[:16]   # "2026-04-04 19:07"
    return {"sha": short_sha, "message": commit_msg, "date": commit_date}


class VersionCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="version",
        aliases=["ver", "commit"],
        help="Show the running bot version and last update."
    )
    async def version_cmd(self, ctx: commands.Context):
        info = get_version_info()
        msg = (
            f"🤖 **Marco Van Botten**\n"
            f"Commit: `{info['sha']}`\n"
            f"Last update: `{info['date']}`\n"
            f"Message: {info['message']}"
        )
        from modules.discord_poster import post_new_message_to_context
        await post_new_message_to_context(ctx, content=msg)

async def setup(bot: commands.Bot):
    await bot.add_cog(VersionCommand(bot))
    logger.info("✔ cogs.version_command loaded")
