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
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

italy_tz = pytz.timezone("Europe/Rome")

@tasks.loop(time=time(hour=11, minute=0, tzinfo=italy_tz))
async def daily_scheduler():
    log_info("⏰ 11:00 – starting daily scheduler run")
    asyncio.create_task(schedule_day(bot))

@bot.event
async def on_ready():
    setup_power_management()
    log_info(f"✅ Logged in as {bot.user}")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(greet_message())

    # load cogs
    for fname in os.listdir("cogs"):
        if fname.endswith(".py") and fname != "__init__.py":
            await bot.load_extension(f"cogs.{fname[:-3]}")
            log_info(f"✔ loaded cog: cogs.{fname[:-3]}")

    # kick off today's schedule in background
    asyncio.create_task(schedule_day(bot))

    # start daily 11:00 loop
    if not daily_scheduler.is_running():
        daily_scheduler.start()

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
