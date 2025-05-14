# cogs/competitions.py
import logging
from discord.ext import commands
from config import TRACKED_LEAGUE_IDS # For getting the list of leagues
# MODIFIED: Import from the new discord_poster module
from modules.discord_poster import post_new_message_to_context

logger = logging.getLogger(__name__)

# A simple ID→name map for all your leagues
LEAGUE_NAME_MAP = {
    135: "Serie A",
    137: "Coppa Italia",
    547: "Supercoppa Italiana",
    39:  "Premier League",
    45:  "FA Cup",
    48:  "Carabao Cup",
    528: "Community Shield",
    140: "La Liga",
    143: "Copa del Rey",
    556: "Supercopa de España",
    2:   "Champions League",
    3:   "Europa League",
    848: "Conference League",
    531: "UEFA Super Cup",
    1168:"Intercontinental Cup", # Note: This might be an older name/ID for FIFA Club World Cup
    15:  "Club World Cup", # FIFA Club World Cup
    1:   "FIFA World Cup", # National teams
    4:   "UEFA EURO" # National teams
}

class Competitions(commands.Cog):
    """Show the list of competitions the bot is tracking."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="competitions",
        help="List the competitions I'm currently tracking."
    )
    async def competitions(self, ctx: commands.Context):
        names = [LEAGUE_NAME_MAP.get(lid, f"Unknown League (ID: {lid})") 
                 for lid in TRACKED_LEAGUE_IDS]
        
        if not names:
            message_content = "I’m not currently configured to track any specific competitions."
        else:
            comp_list = ", ".join(sorted(names)) # Sorted for consistent output
            message_content = f"I’m tracking these competitions:\n{comp_list}"
        
        # MODIFIED: Use discord_poster
        await post_new_message_to_context(ctx, content=message_content)

async def setup(bot: commands.Bot):
    await bot.add_cog(Competitions(bot))
    logger.info("✔ cogs.competitions loaded")
