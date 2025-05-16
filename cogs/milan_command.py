# cogs/milan_command.py

import logging
import discord
from discord.ext import commands

from config import AC_MILAN_TEAM_ID 
from utils.api_client import fetch_next_team_fixture
from utils.time_utils import parse_utc_to_italy 
from modules.discord_poster import post_new_message_to_context 

logger = logging.getLogger(__name__)

class MilanCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="milan",
        aliases=["nextmilan", "acmilan"],
        help="Shows AC Milan's next scheduled match."
    )
    async def milan_next_match(self, ctx: commands.Context):
        if not hasattr(self.bot, 'http_session') or self.bot.http_session.closed:
            # This check ensures the session is available, crucial if the bot recently resumed.
            # Your main bot file's ensure_http_session should handle this, but an extra check here is safe.
            # Alternatively, trust that ensure_http_session in on_ready/on_resumed has run.
            # For simplicity, we'll proceed, assuming the main bot manages the session.
            # If issues persist, you might need to call await self.bot.ensure_http_session(self.bot) here.
            # However, this requires ensure_http_session to be part of the bot object or imported.
            # Let's assume bot.http_session is valid due to on_ready/on_resumed.
            logger.info("Attempting !milan command, relying on existing http_session.")


        team_id_to_check = AC_MILAN_TEAM_ID
        if not team_id_to_check:
            await post_new_message_to_context(ctx, content="AC Milan Team ID is not configured.")
            logger.error("AC_MILAN_TEAM_ID is not set in config.py for !milan command.")
            return

        try:
            await ctx.typing() # Show "Bot is typing..."
            next_fixture = await fetch_next_team_fixture(self.bot.http_session, team_id_to_check)

            if next_fixture:
                fixture_data = next_fixture.get('fixture', {})
                teams_data = next_fixture.get('teams', {})
                league_data = next_fixture.get('league', {})

                date_utc_str = fixture_data.get('date')
                if not date_utc_str:
                    await post_new_message_to_context(ctx, content="Could not retrieve date for the next match.")
                    return

                match_dt_italy = parse_utc_to_italy(date_utc_str)
                
                home_team = teams_data.get('home', {}).get('name', 'N/A')
                away_team = teams_data.get('away', {}).get('name', 'N/A')
                competition_name = league_data.get('name', 'N/A')
                round_name = league_data.get('round', '')

                # Determine opponent
                opponent = away_team if home_team.lower() == "ac milan" else home_team
                venue = "San Siro (Home)" if home_team.lower() == "ac milan" else f"Away at {home_team}" if away_team.lower() == "ac milan" else "Venue N/A"


                message = (
                    f"🔴⚫ Next AC Milan Match ⚫🔴\n"
                    f"-----------------------------------\n"
                    f"🆚 **Opponent:** {opponent}\n"
                    f"🗓️ **Date:** {match_dt_italy.strftime('%A, %B %d, %Y')}\n"
                    f"⏰ **Time:** {match_dt_italy.strftime('%H:%M')} (Italy Time)\n"
                    f"🏆 **Competition:** {competition_name} ({round_name})\n"
                    f"🏟️ **Venue:** {venue}\n"
                    f"-----------------------------------"
                )
                await post_new_message_to_context(ctx, content=message)
            else:
                await post_new_message_to_context(ctx, content="Could not find AC Milan's next scheduled match in the current season, or an API error occurred.")

        except Exception as e:
            logger.error(f"Error in !milan command: {e}", exc_info=True)
            await post_new_message_to_context(ctx, content="An error occurred while fetching Milan's next match. Please try again later.")

async def setup(bot: commands.Bot):
    await bot.add_cog(MilanCommand(bot))
    logger.info("✔ cogs.milan_command loaded")