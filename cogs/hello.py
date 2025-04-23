# cogs/hello.py
from discord.ext import commands
from utils.personality import get_greeting

class Hello(commands.Cog):
    """A small cog to let users say hi to the bot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="hi", 
        aliases=["hello"], 
        help="Say hi and get a random greeting back!"
    )
    async def hi(self, ctx: commands.Context):
        greeting = get_greeting()
        await ctx.send(greeting)

async def setup(bot: commands.Bot):
    await bot.add_cog(Hello(bot))
    print("[INFO ] hello cog loaded")
