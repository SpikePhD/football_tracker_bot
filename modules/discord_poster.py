# modules/discord_poster.py

import logging
import discord
from discord.ext import commands # For commands.Context
from typing import Sequence

logger = logging.getLogger(__name__)

_DISCORD_CONTENT_LIMIT = 1900


def _split_content(content: str, max_len: int = _DISCORD_CONTENT_LIMIT) -> list[str]:
    """
    Split message content into chunks that fit Discord limits.
    Prefer line-boundary splits; fall back to hard splitting long lines.
    """
    if not content:
        return [content]
    if len(content) <= max_len:
        return [content]

    chunks: list[str] = []
    current = ""

    for line in content.splitlines(keepends=True):
        if len(line) > max_len:
            if current:
                chunks.append(current)
                current = ""
            start = 0
            while start < len(line):
                chunks.append(line[start:start + max_len])
                start += max_len
            continue

        if len(current) + len(line) <= max_len:
            current += line
        else:
            if current:
                chunks.append(current)
            current = line

    if current:
        chunks.append(current)

    return chunks if chunks else [content[:max_len]]


async def post_live_update(
    bot: discord.Client,
    channel_id: int,
    content: str | None = None,
    embed: discord.Embed | None = None,
    attachments: Sequence[discord.File] | None = None
) -> discord.Message | None:
    """Post a live match update as a new message."""
    return await post_new_general_message(bot, channel_id, content=content, embed=embed, attachments=attachments)

async def upsert_live_message(
    bot: discord.Client,
    channel_id: int,
    message_id: int | None,
    content: str,
) -> discord.Message | None:
    """
    Upsert a live match message.
    If message_id is valid, edit that message; otherwise send a new message.
    """
    if not content:
        logger.warning("DiscordPoster: upsert_live_message called with empty content.")
        return None

    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        logger.error(f"DiscordPoster: Channel ID {channel_id} did not yield a valid TextChannel for live upsert.")
        return None

    try:
        if message_id is not None:
            try:
                existing = await channel.fetch_message(message_id)
                await existing.edit(content=content, suppress=True)
                logger.info(f"DiscordPoster: Edited live message {message_id} in #{channel.name}")
                return existing
            except discord.NotFound:
                logger.warning(f"DiscordPoster: Live message {message_id} not found in #{channel.name}; sending new message.")

        created = await channel.send(content=content, suppress_embeds=True)
        logger.info(f"DiscordPoster: Created new live message {created.id} in #{channel.name}")
        return created
    except discord.Forbidden:
        logger.error(f"DiscordPoster: Missing permissions to upsert live message in #{channel.name}.", exc_info=True)
    except Exception as e:
        logger.error(f"DiscordPoster: Failed to upsert live message in #{channel.name}: {e}", exc_info=True)

    return None


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

        content_chunks = _split_content(content) if content else [None]
        new_message = None
        for idx, chunk in enumerate(content_chunks):
            include_payloads = idx == 0
            new_message = await channel.send(
                content=chunk,
                embed=embed if include_payloads else None,
                files=current_attachments if include_payloads else None,
                suppress_embeds=True,
            )
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
        content_chunks = _split_content(content) if content else [None]
        new_message = None
        for idx, chunk in enumerate(content_chunks):
            include_payloads = idx == 0
            new_message = await ctx.send(
                content=chunk,
                embed=embed if include_payloads else None,
                files=current_attachments if include_payloads else None,
                suppress_embeds=True,
            )
        return new_message
    except discord.Forbidden:
        logger.error(f"DiscordPoster: Missing permissions to send message via context for command '{command_name}' in #{ctx.channel.name}.", exc_info=True)
    except Exception as e:
        logger.error(f"DiscordPoster: Failed to send new message via context for command '{command_name}': {e}", exc_info=True)

    return None
