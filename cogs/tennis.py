# cogs/tennis.py
import logging
from datetime import datetime

from discord.ext import commands

from config import TENNIS_UPCOMING_DAYS
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
        help="Show tracked tennis: live now, upcoming, and today's finished matches.",
    )
    async def tennis(self, ctx: commands.Context):
        live_matches = await api_provider.fetch_tennis_live(self.bot.http_session)
        upcoming_matches = await api_provider.fetch_tennis_upcoming(
            self.bot.http_session,
            horizon_days=TENNIS_UPCOMING_DAYS,
        )
        finished_today = await api_provider.fetch_tennis_finished_today(self.bot.http_session)

        live_sorted = sorted(live_matches, key=_sort_key_asc)
        upcoming_sorted = sorted(upcoming_matches, key=_sort_key_asc)
        finished_sorted = sorted(finished_today, key=_sort_key_desc, reverse=True)

        if not (live_sorted or upcoming_sorted or finished_sorted):
            await post_new_message_to_context(
                ctx,
                content="No tracked tennis matches live, upcoming, or finished today.",
            )
            return

        lines: list[str] = ["**Tracked tennis matches**"]

        if live_sorted:
            lines.append("\n**LIVE now**")
            for match in live_sorted:
                lines.append("\n" + format_tennis_live_message(match))

        if upcoming_sorted:
            lines.append(f"\n**Upcoming (next {TENNIS_UPCOMING_DAYS} days)**")
            for match in upcoming_sorted:
                lines.append("\n" + format_tennis_pre_message(match))

        if finished_sorted:
            lines.append("\n**Finished today**")
            for match in finished_sorted:
                lines.append("\n" + format_tennis_final_message(match))

        await post_new_message_to_context(ctx, content="\n".join(lines))


def _parse_start_time(match: dict) -> datetime:
    start = match.get("start_time") or ""
    try:
        return datetime.fromisoformat(start.replace("Z", "+00:00"))
    except Exception:
        return datetime.min


def _sort_key_asc(match: dict) -> datetime:
    return _parse_start_time(match)


def _sort_key_desc(match: dict) -> datetime:
    return _parse_start_time(match)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tennis(bot))
    logger.info("cogs.tennis loaded")
