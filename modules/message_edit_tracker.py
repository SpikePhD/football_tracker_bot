# modules/message_edit_tracker.py

import pytz
import asyncio
from datetime import datetime, time as dt_time
from discord import TextChannel, Message
from discord.ext import tasks, commands
from modules.verbose_logger import log_info, log_warning, log_error

# ——— Configuration ———
ITALY_TZ    = pytz.timezone("Europe/Rome")
THRESHOLD   = 30    # number of user messages before we post a fresh one

# ——— Internal State ———
_last_bot_message: Message | None = None
_user_msg_count: int     = 0

# ——— Helpers ———
def _on_user_message(msg: Message):
    """Count every non-bot message to decide when to rotate updates."""
    global _user_msg_count
    if msg.author.bot:
        return
    _user_msg_count += 1

async def handle_update(channel: TextChannel, content: str):
    """
    Post or edit the last bot message in `channel` with `content`.
    - If <THRESHOLD user msgs have passed, send a NEW message.
    - Otherwise edit the previous one.
    """
    global _last_bot_message, _user_msg_count

    # If we have a “last message” and haven't hit the user-message threshold, try edit
    if _last_bot_message and _user_msg_count < THRESHOLD:
        try:
            await _last_bot_message.edit(content=content)
            log_info("✏️ Edited previous update")
            return
        except Exception as e:
            log_warning(f"Failed to edit message, sending new one: {e}")

    # Otherwise, send a fresh message
    try:
        _last_bot_message = await channel.send(content)
        log_info("🆕 Posted new update message")
        _user_msg_count = 0
    except Exception as e:
        log_error(f"Could not post update message: {e}")

# ——— Daily Reset ———
@tasks.loop(time=dt_time(hour=0, minute=0, tzinfo=ITALY_TZ))
async def _daily_reset():
    """Wipe out yesterday’s state at midnight Italy time."""
    global _last_bot_message, _user_msg_count
    _last_bot_message = None
    _user_msg_count   = 0
    log_info("🔄 Daily reset: cleared last message and user‐msg count")

# ——— Module Setup ———
async def setup(bot: commands.Bot):
    # 1) Listen to every message to count user activity
    bot.add_listener(_on_user_message, "on_message")
    # 2) Kick off the daily reset loop
    _daily_reset.start()
    log_info("✔ message_edit_tracker module loaded")
