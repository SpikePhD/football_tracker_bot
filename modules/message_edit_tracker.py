# modules/message_edit_tracker.py
from __future__ import annotations

import asyncio
import logging
from typing import Sequence, Union # Union is for type hinting content/embed

import discord

# Standard Python logger, matching your original partial usage
log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Configuration Constants                                                    #
# --------------------------------------------------------------------------- #

# Threshold for switching from editing to sending a new message.
# If messages SINCE bot's last post are LESS THAN this, bot will edit.
EDIT_MESSAGE_THRESHOLD = 30

# How many messages to look through after the bot's last message
# to count intervening messages. Should be at least EDIT_MESSAGE_THRESHOLD.
HISTORY_LOOKUP_LIMIT = 50 # Increased slightly beyond threshold

# --------------------------------------------------------------------------- #
#  Module-level state                                                         #
# --------------------------------------------------------------------------- #

# Stores the ID of the last message posted or edited by this module.
# This is a simple solution for a bot operating primarily in one channel for these updates.
# For multi-channel operations, this would need to be a dictionary mapping channel_id to message_id.
last_bot_message_id: int | None = None

# --------------------------------------------------------------------------- #
#  Public API                                                                 #
# --------------------------------------------------------------------------- #

async def upsert_message(
    channel: discord.TextChannel,
    *, # Enforce keyword-only arguments for content and embed
    content: str | None = None,
    embed: discord.Embed | None = None,
    attachments: Sequence[discord.File] | None = None, # Retaining attachments
) -> discord.Message | None:
    """
    Posts or edits a message in the specified channel.

    - If the bot's last tracked message in the channel has fewer than
      EDIT_MESSAGE_THRESHOLD messages after it, that message is edited.
    - Otherwise, a new message is sent.
    - Updates the ID of the last tracked message.

    Args:
        channel: The TextChannel to send/edit the message in.
        content: The text content of the message.
        embed: The embed for the message.
        attachments: A sequence of discord.File objects to attach.

    Returns:
        The discord.Message object that was sent or edited, or None if an error occurred.
    """
    global last_bot_message_id

    if not content and not embed and not attachments:
        log.warning("upsert_message called with no content, embed, or attachments.")
        return None

    message_to_edit: discord.Message | None = None
    should_send_new = True

    if last_bot_message_id:
        try:
            log.debug(f"Attempting to fetch last bot message ID: {last_bot_message_id}")
            message_to_edit = await channel.fetch_message(last_bot_message_id)
            
            message_count_after = 0
            # Count messages after the bot's last message
            async for _ in channel.history(after=message_to_edit, limit=HISTORY_LOOKUP_LIMIT, oldest_first=True):
                message_count_after += 1
                if message_count_after >= EDIT_MESSAGE_THRESHOLD:
                    break # Stop counting if threshold is met or exceeded
            
            log.debug(f"Found {message_count_after} messages after last bot message {last_bot_message_id}.")

            if message_count_after < EDIT_MESSAGE_THRESHOLD:
                should_send_new = False
            else:
                log.info(f"Message count ({message_count_after}) >= threshold ({EDIT_MESSAGE_THRESHOLD}). Sending new message.")

        except discord.NotFound:
            log.warning(f"Last bot message ID {last_bot_message_id} not found. Will send a new message.")
            last_bot_message_id = None # Reset as it's no longer valid
            message_to_edit = None # Ensure we don't try to edit
        except discord.Forbidden:
            log.error(f"Missing permissions to fetch message {last_bot_message_id} or read history in #{channel.name}.")
            # Not resetting last_bot_message_id here, as it might be a temporary permissions issue
            # or the message might still exist. Fallback to sending new for this attempt.
            message_to_edit = None
        except Exception as e:
            log.exception(f"Error processing last bot message {last_bot_message_id}: {e}")
            message_to_edit = None # Fallback to sending new

    # Prepare message arguments (handle attachments carefully for send vs edit)
    # discord.py's edit() can take `attachments` (replaces) or `files` (adds, deprecated for edit, use attachments).
    # discord.py's send() takes `files`.
    # For simplicity and consistency with your original, we'll use attachments for edit, files for send.
    # If attachments is None, use an empty list for operations that require it.
    current_attachments = attachments or []

    if not should_send_new and message_to_edit:
        try:
            log.info(f"Editing message {message_to_edit.id} in #{channel.name}")
            # When editing, ensure all parts are provided or reset if necessary
            # If new content/embed is None, original remains unless explicitly cleared (not done here)
            await message_to_edit.edit(content=content, embed=embed, attachments=current_attachments)
            # No need to update last_bot_message_id as it's the same message
            return message_to_edit
        except discord.NotFound: # Message might have been deleted between fetch and edit
            log.warning(f"Failed to edit message {message_to_edit.id} as it was not found. Sending new.")
            last_bot_message_id = None # Reset as it's no longer valid
        except discord.Forbidden:
            log.error(f"Missing permissions to edit message {message_to_edit.id} in #{channel.name}.")
        except Exception as e:
            log.exception(f"Failed to edit message {message_to_edit.id}: {e}")
    
    # Send a new message
    try:
        log.info(f"Sending new message to #{channel.name}")
        # For send, `files` is the correct parameter for `discord.File` objects
        new_message = await channel.send(content=content, embed=embed, files=current_attachments)
        last_bot_message_id = new_message.id # Store the new message ID
        return new_message
    except discord.Forbidden:
        log.error(f"Missing permissions to send messages in #{channel.name}.")
    except Exception as e:
        log.exception(f"Failed to send new message in #{channel.name}: {e}")

    return None


async def safe_upsert(
    channel: discord.TextChannel,
    *,
    content: str | None = None,
    embed: discord.Embed | None = None,
    attachments: Sequence[discord.File] | None = None,
) -> None:
    """
    Fire-and-forget wrapper for upsert_message.
    Catches and logs any exception instead of killing the loop.
    """
    try:
        await upsert_message(channel, content=content, embed=embed, attachments=attachments)
    except Exception:
        # The exception should ideally be caught and logged within upsert_message itself for specifics
        # This outer catch is a fallback.
        log.exception("An unexpected error occurred in safe_upsert for channel #%s", channel.name)
        # Removed asyncio.sleep(5) as it might hide rapid issues; caller should handle retry logic if needed