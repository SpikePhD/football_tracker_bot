"""Central Discord command authorization policy."""

from discord.ext import commands

from config import BOT_OWNER_IDS, CHANNEL_ID


class WrongCommandChannel(commands.CheckFailure):
    pass


class OwnerRequired(commands.CheckFailure):
    pass


class OperatorRequired(commands.CheckFailure):
    pass


async def command_channel_check(ctx: commands.Context) -> bool:
    if getattr(getattr(ctx, "channel", None), "id", None) != CHANNEL_ID:
        raise WrongCommandChannel("Commands are restricted to the configured Discord channel.")
    return True


async def is_owner(ctx: commands.Context) -> bool:
    author_id = getattr(getattr(ctx, "author", None), "id", None)
    if BOT_OWNER_IDS:
        return author_id in BOT_OWNER_IDS
    # Migration-safe bootstrap: discord.py resolves the application owner/team.
    return bool(await ctx.bot.is_owner(ctx.author))


async def is_operator(ctx: commands.Context) -> bool:
    if await is_owner(ctx):
        return True
    permissions = getattr(getattr(ctx, "author", None), "guild_permissions", None)
    return bool(getattr(permissions, "manage_guild", False))


def owner_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not await is_owner(ctx):
            raise OwnerRequired("Configured bot owner access is required.")
        return True
    return commands.check(predicate)


def operator_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not await is_operator(ctx):
            raise OperatorRequired("Bot owner or Manage Server access is required.")
        return True
    return commands.check(predicate)
