# football_tracker_bot.py

import os
import asyncio
import pytz
from datetime import time
import discord
from discord.ext import commands, tasks

from config import BOT_TOKEN, CHANNEL_ID
from utils.personality import greet_message
from modules.power_manager import setup_power_management
from modules.verbose_logger import log_info
from modules.scheduler import schedule_day

# ─── Intents & bot setup ─────────────────────────
intents = commands.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

italy_tz = pytz.timezone("Europe/Rome")


@tasks.loop(time=time(hour=11, minute=0, tzinfo=italy_tz))
async def daily_scheduler():
    """Runs every day at 11:00 🇮🇹 and triggers the normal schedule logic."""
    log_info("⏰ 11:00 – starting daily scheduler run")
    # fire & forget, since schedule_day will do its own sleeping/polling
    asyncio.create_task(schedule_day(bot))


@bot.event
async def on_ready():
    # 1) Prevent host from sleeping
    setup_power_management()

    # 2) Log & send “I’m alive!” in Discord
    log_info(f"✅ Logged in as {bot.user}")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(greet_message())

    # 3) Load all your cogs (so commands like !matches work immediately)
    for fname in os.listdir("cogs"):
        if not fname.endswith(".py") or fname == "__init__.py":
            continue
        mod_name = fname[:-3]
        await bot.load_extension(f"cogs.{mod_name}")
        log_info(f"✔ loaded cog: cogs.{mod_name}")

    # 4) Kick off today's scheduler **in the background** (non-blocking)
    asyncio.create_task(schedule_day(bot))

    # 5) Start the 11:00 daily job (if it isn’t already running)
    if not daily_scheduler.is_running():
        daily_scheduler.start()


if __name__ == "__main__":
    bot.run(BOT_TOKEN)
