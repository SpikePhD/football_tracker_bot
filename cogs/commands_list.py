# cogs/commands_list.py

import logging
from discord.ext import commands
from modules.discord_poster import post_new_message_to_context

logger = logging.getLogger(__name__)


class CommandsList(commands.Cog):
    """List all available bot commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(
        name="commands",
        aliases=["cmds", "help"],
        help="List all available bot commands."
    )
    async def commands_list(self, ctx: commands.Context):
        visible = sorted(
            (c for c in self.bot.commands if not c.hidden),
            key=lambda c: c.name
        )

        lines = ["**Available commands:**\n"]
        for cmd in visible:
            aliases = f" ({', '.join(f'!{a}' for a in cmd.aliases)})" if cmd.aliases else ""
            description = cmd.help or "No description."
            lines.append(f"`!{cmd.name}`{aliases} — {description}")

        await post_new_message_to_context(ctx, content="\n".join(lines))


async def setup(bot: commands.Bot):
    await bot.add_cog(CommandsList(bot))
    logger.info("✔ cogs.commands_list loaded")
