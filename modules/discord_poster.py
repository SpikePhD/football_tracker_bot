# modules/discord_poster.py
# New module to handle all Discord message posting and editing.

import logging
import discord # For discord.Client, discord.TextChannel, discord.Message, discord.Embed, discord.File, discord.Forbidden, discord.NotFound
from discord.ext import commands # For commands.Context in post_new_message_to_context
from typing import Sequence, Union # For type hinting, Union is not strictly needed if we use | for new Pythons

logger = logging.getLogger(__name__)

# --- Configuration for live update editing ---
# If messages SINCE bot's last live update post are LESS THAN this, bot will edit.
EDIT_MESSAGE_THRESHOLD = 30 
# How many messages to look through after the bot's last live update message
# to count intervening messages. Should be at least EDIT_MESSAGE_THRESHOLD.
HISTORY_LOOKUP_LIMIT = 50 

# --- Module-level state for live update message editing ---
# Stores the ID of the last LIVE update message posted or edited by post_live_update.
# This ID is specific to live updates to allow them to be edited.
# FT messages and Cog messages will use different functions and won't affect this.
_last_live_update_message_id: int | None = None


async def post_live_update(
    bot: discord.Client, 
    channel_id: int, 
    content: str | None = None,
    embed: discord.Embed | None = None,
    attachments: Sequence[discord.File] | None = None
) -> discord.Message | None:
    """
    Posts a live update to the specified channel.
    Attempts to edit the previous live update message if chat activity is low,
    otherwise sends a new message. Manages the _last_live_update_message_id state.

    Args:
        bot: The discord.Client or commands.Bot instance.
        channel_id: The ID of the channel to post to.
        content: The text content of the message. (Optional)
        embed: The discord.Embed object for the message. (Optional)
        attachments: A sequence of discord.File objects to attach. (Optional)

    Returns:
        The discord.Message object that was sent or edited, or None if an error occurred.
    """
    global _last_live_update_message_id

    # Validate that there's something to send
    if not content and not embed and (not attachments or len(attachments) == 0):
        logger.warning("DiscordPoster: post_live_update called with no content, embed, or attachments.")
        return None

    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel): # More specific type check
        logger.error(f"DiscordPoster: Channel ID {channel_id} did not yield a valid TextChannel for live update.")
        return None

    message_to_edit: discord.Message | None = None
    should_send_new = True # Default to sending a new message

    if _last_live_update_message_id:
        try:
            logger.debug(f"DiscordPoster: Attempting to fetch last live update message ID: {_last_live_update_message_id} in channel {channel.name}")
            message_to_edit = await channel.fetch_message(_last_live_update_message_id)
            
            message_count_after = 0
            # Count messages after the bot's last live update message
            async for _msg_after in channel.history(after=message_to_edit, limit=HISTORY_LOOKUP_LIMIT, oldest_first=True):
                message_count_after += 1
                if message_count_after >= EDIT_MESSAGE_THRESHOLD:
                    break 
            
            logger.debug(f"DiscordPoster: Found {message_count_after} messages after last live update message {_last_live_update_message_id}.")

            if message_count_after < EDIT_MESSAGE_THRESHOLD:
                should_send_new = False # Condition to edit is met
            else:
                logger.info(f"DiscordPoster: Live update in #{channel.name} - Message count ({message_count_after}) >= threshold ({EDIT_MESSAGE_THRESHOLD}). Will send new message.")

        except discord.NotFound:
            logger.warning(f"DiscordPoster: Last live update message ID {_last_live_update_message_id} not found in #{channel.name}. Will send a new message.")
            _last_live_update_message_id = None # Reset as it's no longer valid
            message_to_edit = None # Ensure we don't try to edit
        except discord.Forbidden:
            logger.error(f"DiscordPoster: Missing permissions to fetch message {_last_live_update_message_id} or read history in #{channel.name}.", exc_info=True)
            message_to_edit = None # Fallback to sending new for this attempt
        except Exception as e:
            logger.error(f"DiscordPoster: Unexpected error processing last live update message {_last_live_update_message_id} in #{channel.name}: {e}", exc_info=True)
            message_to_edit = None # Fallback to sending new

    # Ensure attachments is a list for discord.py methods
    current_attachments = attachments or []

    # Attempt to edit if conditions are met
    if not should_send_new and message_to_edit:
        try:
            logger.info(f"DiscordPoster: Editing live update message {message_to_edit.id} in #{channel.name}")
            # edit() can take content, embed, and attachments (replaces existing attachments)
            await message_to_edit.edit(content=content, embed=embed, attachments=current_attachments)
            # _last_live_update_message_id remains the same message ID
            return message_to_edit
        except discord.NotFound: 
            logger.warning(f"DiscordPoster: Failed to edit message {message_to_edit.id} in #{channel.name} (it was not found, possibly deleted). Sending new.", exc_info=True)
            _last_live_update_message_id = None # Reset as it's no longer valid
        except discord.Forbidden:
            logger.error(f"DiscordPoster: Missing permissions to edit message {message_to_edit.id} in #{channel.name}.", exc_info=True)
        except Exception as e:
            logger.error(f"DiscordPoster: Failed to edit message {message_to_edit.id} in #{channel.name}: {e}", exc_info=True)
        # If edit failed for any reason, fall through to send a new message
        logger.info("DiscordPoster: Edit failed, falling back to sending a new message for live update.")
        # Force should_send_new to true if it wasn't already, ensure message_to_edit is None
        should_send_new = True 
        message_to_edit = None # Clear this so we don't somehow re-evaluate the edit path
    
    # Send a new message if should_send_new is true (either initially, or after an edit failed)
    if should_send_new:
        try:
            logger.info(f"DiscordPoster: Sending new live update message to #{channel.name}")
            # send() takes content, embed, and files (for new attachments)
            new_message = await channel.send(content=content, embed=embed, files=current_attachments)
            _last_live_update_message_id = new_message.id # Store the new message ID for future edits
            return new_message
        except discord.Forbidden:
            logger.error(f"DiscordPoster: Missing permissions to send live update message in #{channel.name}.", exc_info=True)
        except Exception as e:
            logger.error(f"DiscordPoster: Failed to send new live update message in #{channel.name}: {e}", exc_info=True)

    return None # Return None if all attempts to send/edit failed


async def post_new_general_message(
    bot: discord.Client, 
    channel_id: int, 
    content: str | None = None,
    embed: discord.Embed | None = None,
    attachments: Sequence[discord.File] | None = None
) -> discord.Message | None:
    """
    Sends a new message to the specified channel.
    Used for FT results or other announcements that should always be new and
    should not affect the _last_live_update_message_id.

    Args:
        bot: The discord.Client or commands.Bot instance.
        channel_id: The ID of the channel to post to.
        content: The text content of the message. (Optional)
        embed: The discord.Embed object for the message. (Optional)
        attachments: A sequence of discord.File objects to attach. (Optional)

    Returns:
        The discord.Message object that was sent, or None if an error occurred.
    """
    # Validate that there's something to send
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
    Sends a new message in response to a command context.
    Used by cogs. This message is always new and does not affect _last_live_update_message_id.

    Args:
        ctx: The discord.ext.commands.Context object.
        content: The text content of the message. (Optional)
        embed: The discord.Embed object for the message. (Optional)
        attachments: A sequence of discord.File objects to attach. (Optional)

    Returns:
        The discord.Message object that was sent, or None if an error occurred.
    """
    # Validate that there's something to send
    if not content and not embed and (not attachments or len(attachments) == 0):
        logger.warning("DiscordPoster: post_new_message_to_context called with no content, embed, or attachments.")
        return None

    current_attachments = attachments or []
    try:
        # Use ctx.command.name for more specific logging if available
        command_name = ctx.command.name if ctx.command else "unknown command"
        logger.info(f"DiscordPoster: Sending new message via context for command '{command_name}' in #{ctx.channel.name}")
        new_message = await ctx.send(content=content, embed=embed, files=current_attachments)
        return new_message
    except discord.Forbidden:
        logger.error(f"DiscordPoster: Missing permissions to send message via context for command '{command_name}' in #{ctx.channel.name}.", exc_info=True)
    except Exception as e:
        logger.error(f"DiscordPoster: Failed to send new message via context for command '{command_name}': {e}", exc_info=True)
        
    return None
