# cogs/matches.py
import logging
from discord.ext import commands
from modules import api_provider
from utils.time_utils import parse_utc_to_italy, italy_now # For time formatting
from datetime import datetime
from modules.discord_poster import post_new_message_to_context

logger = logging.getLogger(__name__)

class Matches(commands.Cog):
    """Show today’s tracked fixtures, with status."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="matches",
        help="List today's tracked fixtures (upcoming, live or final)."
    )
    async def matches(self, ctx: commands.Context):
        fixtures = await api_provider.fetch_day(self.bot.http_session)
        if not fixtures:
            await post_new_message_to_context(ctx, content="📭 No tracked matches today.")
            return

        # sort by UTC date string
        fixtures.sort(key=lambda m: m['fixture']['date'])
        current_time = italy_now() # Renamed from 'now' to avoid conflict if 'now' is a fixture variable

        lines = []
        for m in fixtures:
            # localize
            ko_dt = parse_utc_to_italy(m['fixture']['date'])
            status = m.get('fixture', {}).get('status', {}).get('short', 'N/A') # Added default for status
            home = m.get('teams', {}).get('home', {}).get('name', 'Home Team')
            away = m.get('teams', {}).get('away', {}).get('name', 'Away Team')
            goals = m.get('goals', {'home': '?', 'away': '?'}) # Added default for goals

            if status == "NS":
                # not started
                time_str = ko_dt.strftime("%H:%M")
                lines.append(f"{time_str} — {home} vs {away}")
            elif status == "FT":
                # full-time
                lines.append(f"FT: {home} {goals.get('home', '?')}-{goals.get('away', '?')} {away}")
            else:
                # any other status we treat as live (e.g., LIVE, HT, 1H, 2H, ET, PEN, BT)
                lines.append(f"LIVE: {home} {goals.get('home', '?')}-{goals.get('away', '?')} {away} ({status})")


        header = f"**Today's tracked matches ({current_time.strftime('%Y-%m-%d')}):**"
        message_content = "\n".join([header, *lines])

        await post_new_message_to_context(ctx, content=message_content)

async def setup(bot: commands.Bot):
    await bot.add_cog(Matches(bot))
    logger.info("✔ cogs.matches loaded")
