# cogs/api_status.py
# !api command — shows which data API is currently active and its health status.

import logging
from discord.ext import commands
from modules import api_provider
from modules.discord_poster import post_new_message_to_context
from utils.time_utils import italy_now

logger = logging.getLogger(__name__)


class ApiStatus(commands.Cog):
    """Show which football data API is currently active."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="api",
        aliases=["apistatus", "provider"],
        help="Show which football data API is currently active (ESPN or API-Football fallback)."
    )
    async def api_status(self, ctx: commands.Context):
        status = api_provider.get_status()

        espn_healthy = status["espn_healthy"]
        failures = status["consecutive_failures"]
        retry_after = status["retry_after"]   # Italy-localized datetime or None
        interval = status["poll_interval"]

        if espn_healthy and failures == 0:
            # All good, ESPN primary
            lines = [
                "📡 **API Status**",
                f"Provider:  🟢 ESPN (primary)",
                f"Interval:  every {interval}s",
                f"Failures:  0",
            ]

        elif espn_healthy and failures > 0:
            # ESPN recovered but had some failures — still healthy
            lines = [
                "📡 **API Status**",
                f"Provider:  🟢 ESPN (primary, recovering)",
                f"Interval:  every {interval}s",
                f"Failures:  {failures} (below threshold — still on ESPN)",
            ]

        else:
            # Fallback mode
            if retry_after:
                now = italy_now()
                remaining = retry_after - now
                remaining_sec = max(0, int(remaining.total_seconds()))
                rem_min, rem_sec = divmod(remaining_sec, 60)
                retry_str = retry_after.strftime("%H:%M")

                if remaining_sec > 0:
                    provider_line = f"Provider:  🔴 API-Football (fallback — ESPN retry in {rem_min}m {rem_sec}s)"
                else:
                    provider_line = f"Provider:  🟡 API-Football (fallback — ESPN retry pending)"

                lines = [
                    "📡 **API Status**",
                    provider_line,
                    f"Interval:  every {interval}s",
                    f"Failures:  {failures}",
                    f"ESPN retry: {retry_str} (Italy time)",
                ]
            else:
                lines = [
                    "📡 **API Status**",
                    f"Provider:  🔴 API-Football (fallback)",
                    f"Interval:  every {interval}s",
                    f"Failures:  {failures}",
                ]

        await post_new_message_to_context(ctx, content="\n".join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(ApiStatus(bot))
    logger.info("✔ cogs.api_status loaded")
