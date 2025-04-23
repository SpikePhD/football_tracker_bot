# cogs/competitions.py
from discord.ext import commands
from config import TRACKED_LEAGUE_IDS

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
    1168:"Intercontinental Cup",
    15:  "Club World Cup",
    1:   "FIFA World Cup",
    4:   "UEFA EURO"
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
        # Grab only those IDs you actually track today:
        names = [LEAGUE_NAME_MAP.get(lid, f"ID {lid}") 
                 for lid in TRACKED_LEAGUE_IDS]
        # Format as a nice comma-separated list
        comp_list = ", ".join(names)
        await ctx.send(f"I’m tracking these competitions:\n{comp_list}")

async def setup(bot: commands.Bot):
    await bot.add_cog(Competitions(bot))
    print("✔ cogs.competitions loaded")
