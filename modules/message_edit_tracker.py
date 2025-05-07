"""
message_edit_tracker.py  •  football_tracker_bot

Logic for “upsert”‑style posting:
• If the bot has posted fewer than MAX_MESSAGES messages in the channel,
  update (edit) the most recent one.
• Once the cap is reached, start sending new messages again.

Exported coroutine
------------------
    upsert_message(channel: discord.TextChannel, embed: discord.Embed) -> discord.Message
"""

from __future__ import annotations

import asyncio
import logging
from typing import Sequence

import discord

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Tuning constants                                                           #
# --------------------------------------------------------------------------- #

MAX_MESSAGES = 30          # how many of *our own* messages to keep visible
FETCH_LIMIT  = 100         # how far back to look in channel history

# --------------------------------------------------------------------------- #
#  Helpers                                                                    #
# --------------------------------------------------------------------------- #


async def _get_own_messages(
    channel: discord.TextChannel,
) -> list[discord.Message]:
    """
    Return the bot’s own messages, newest → oldest, up to MAX_MESSAGES.

    We restrict the search to FETCH_LIMIT to avoid huge history scans.
    """
    me       = channel.guild.me
    messages = []

    async for m in channel.history(limit=FETCH_LIMIT, oldest_first=False):
        if m.author == me:
            messages.append(m)
            if len(messages) >= MAX_MESSAGES:
                break

    return messages


# --------------------------------------------------------------------------- #
#  Public API                                                                 #
# --------------------------------------------------------------------------- #


async def upsert_message(
    channel: discord.TextChannel,
    embed: discord.Embed,
    *,
    attachments: Sequence[discord.File] | None = None,
) -> discord.Message:
    """
    Edit the most recent bot message while we’re below MAX_MESSAGES;
    otherwise post a fresh one.

    Returns the `discord.Message` object that now contains the embed.
    """
    own_msgs = await _get_own_messages(channel)

    if own_msgs and len(own_msgs) < MAX_MESSAGES:
        last = own_msgs[0]
        log.debug(
            "Editing message %s (%d/%d in channel)",
            last.id,
            len(own_msgs),
            MAX_MESSAGES,
        )
        # ➜  EARLY RETURN ‑‑ stops the function here so we don’t fall through
        return await last.edit(embed=embed, attachments=attachments or [])

    log.debug(
        "Posting new message — %d existing (limit %d)",
        len(own_msgs),
        MAX_MESSAGES,
    )
    return await channel.send(embed=embed, files=attachments or [])


# --------------------------------------------------------------------------- #
#  Convenience wrapper (optional)                                             #
# --------------------------------------------------------------------------- #


async def safe_upsert(
    channel: discord.TextChannel,
    embed: discord.Embed,
    **kwargs,
) -> None:
    """
    Fire‑and‑forget wrapper for use inside endless background tasks.
    Catches and logs any exception instead of killing the loop.
    """
    try:
        await upsert_message(channel, embed, **kwargs)
    except Exception:
        log.exception("Failed to upsert message in #%s", channel)
        await asyncio.sleep(5)  # simple back‑off to avoid hot‑looping
