# modules/discord_poster.py
# MODIFIED: Simplified to always send new messages for live updates.

import logging
import discord
from discord.ext import commands # For commands.Context
from typing import Sequence

logger = logging.getLogger(__name__)

# --- Configuration Constants (EDIT_MESSAGE_THRESHOLD and HISTORY_LOOKUP_LIMIT are no longer needed for live updates) ---

# --- Module-level state for live update message editing (No longer needed for this simplified version) ---
# _last_live_update_message_id: int | None = None # REMOVED/COMMENTED OUT

# def reset_last_live_update_message_id_for_new_day(): # REMOVED/COMMENTED OUT
#     global _last_live_update_message_id
#     logger.info("🔄 Resetting '_last_live_update_message_id' (no longer used for live updates).")
#     _last_live_update_message_id = None


async def post_live_update(
    bot: discord.Client, 
    channel_id: int, 
    content: str | None = None,
    embed: discord.Embed | None = None,
    attachments: Sequence[discord.File] | None = None
) -> discord.Message | None:
    """
    MODIFIED: Always posts a new message for live updates.
    The editing logic has been suspended based on new requirements.
    """
    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        logger.error(f"DiscordPoster: Channel ID {channel_id} did not yield a valid TextChannel for live update.")
        return None

    if not content and not embed and (not attachments or len(attachments) == 0):
        logger.warning("DiscordPoster: post_live_update called with no content, embed, or attachments.")
        return None

    current_attachments = attachments or []

    # Always send a new message for live updates now
    try:
        logger.info(f"DiscordPoster: Sending new live update message to #{channel.name} (editing suspended).")
        new_message = await channel.send(content=content, embed=embed, files=current_attachments)
        # _last_live_update_message_id = new_message.id # No longer needed to store this for editing
        return new_message
    except discord.Forbidden:
        logger.error(f"DiscordPoster: Missing permissions to send live update message in #{channel.name}.", exc_info=True)
    except Exception as e:
        logger.error(f"DiscordPoster: Failed to send new live update message in #{channel.name}: {e}", exc_info=True)
    
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
    try:
        command_name = ctx.command.name if ctx.command else "unknown command"
        logger.info(f"DiscordPoster: Sending new message via context for command '{command_name}' in #{ctx.channel.name}")
        new_message = await ctx.send(content=content, embed=embed, files=current_attachments)
        return new_message
    except discord.Forbidden:
        logger.error(f"DiscordPoster: Missing permissions to send message via context for command '{command_name}' in #{ctx.channel.name}.", exc_info=True)
    except Exception as e:
        logger.error(f"DiscordPoster: Failed to send new message via context for command '{command_name}': {e}", exc_info=True)
        
    return None