# modules/discord_poster.py

import logging
import discord
from discord.ext import commands # For commands.Context
from typing import Sequence

logger = logging.getLogger(__name__)


async def post_live_update(
    bot: discord.Client,
    channel_id: int,
    content: str | None = None,
    embed: discord.Embed | None = None,
    attachments: Sequence[discord.File] | None = None
) -> discord.Message | None:
    """Post a live match update as a new message."""
    return await post_new_general_message(bot, channel_id, content=content, embed=embed, attachments=attachments)


async def post_new_general_message(
    bot: discord.Client,
    channel_id: int,
    content: str | None = None,
    embed: discord.Embed | None = None,
    attachments: Sequence[discord.File] | None = None
) -> discord.Message | None:
    """
    Sends a new message to the specified channel. (Unchanged, already sends new)
    Used for FT results or other announcements that should always be new.
    """
    if not content and not embed and (not attachments or len(attachments) == 0):
        logger.warning("DiscordPoster: post_new_general_message called with no content, embed, or attachments.")
        return None

    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        logger.error(f"DiscordPoster: Channel ID {channel_id} did not yield a valid TextChannel for new general message.")
        return None

    current_attachments = attachments or []
    try:
        logger.info(f"DiscordPoster: Sending new general message to #{channel.name}")
        new_message = await channel.send(content=content, embed=embed, files=current_attachments)
        return new_message
    except discord.Forbidden:
        logger.error(f"DiscordPoster: Missing permissions to send general message in #{channel.name}.", exc_info=True)
    except Exception as e:
        logger.error(f"DiscordPoster: Failed to send new general message in #{channel.name}: {e}", exc_info=True)

    return None

async def post_new_message_to_context(
    ctx: commands.Context,
    content: str | None = None,
    embed: discord.Embed | None = None,
    attachments: Sequence[discord.File] | None = None
) -> discord.Message | None:
    """
    Sends a new message in response to a command context. (Unchanged, already sends new)
    Used by cogs.
    """
    if not content and not embed and (not attachments or len(attachments) == 0):
        logger.warning("DiscordPoster: post_new_message_to_context called with no content, embed, or attachments.")
        return None

    current_attachments = attachments or []
    command_name = ctx.command.name if ctx.command else "unknown command"
    try:
        logger.info(f"DiscordPoster: Sending new message via context for command '{command_name}' in #{ctx.channel.name}")
        new_message = await ctx.send(content=content, embed=embed, files=current_attachments)
        return new_message
    except discord.Forbidden:
        logger.error(f"DiscordPoster: Missing permissions to send message via context for command '{command_name}' in #{ctx.channel.name}.", exc_info=True)
    except Exception as e:
        logger.error(f"DiscordPoster: Failed to send new message via context for command '{command_name}': {e}", exc_info=True)

    return None