# cogs/matches.py
from discord.ext import commands
from utils.api_client import fetch_day_fixtures
from utils.time_utils import parse_utc_to_italy, italy_now
from datetime import datetime

class Matches(commands.Cog):
    """Show today’s tracked fixtures, with status."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="matches",
        help="List today's tracked fixtures (upcoming, live or final)."
    )
    async def matches(self, ctx: commands.Context):
        fixtures = await fetch_day_fixtures(self.bot.http_session)
        if not fixtures:
            return await ctx.send("📭 No tracked matches today.")

        # sort by UTC date string
        fixtures.sort(key=lambda m: m['fixture']['date'])
        now = italy_now()

        lines = []
        for m in fixtures:
            # localize
            ko_dt = parse_utc_to_italy(m['fixture']['date'])
            status = m['fixture']['status']['short']
            home = m['teams']['home']['name']
            away = m['teams']['away']['name']
            goals = m['goals']

            if status == "NS":
                # not started
                time_str = ko_dt.strftime("%H:%M")
                lines.append(f"{time_str} — {home} vs {away}")
            elif status == "FT":
                # full-time
                lines.append(f"FT: {home} {goals['home']}-{goals['away']} {away}")
            else:
                # any other status we treat as live
                lines.append(f"LIVE: {home} {goals['home']}-{goals['away']} {away}")

        # send in one go
        header = f"**Today's tracked matches ({now.strftime('%Y-%m-%d')}):**"
        await ctx.send("\n".join([header, *lines]))

async def setup(bot: commands.Bot):
    await bot.add_cog(Matches(bot))
    print("✔ cogs.matches loaded")
