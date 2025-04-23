# football_tracker_bot.py

import asyncio
import discord
import os
from discord.ext import commands

from config import BOT_TOKEN, CHANNEL_ID
from utils.personality import get_greeting
from modules.power_manager import setup_power_management
from modules.verbose_logger import log_info
from modules.scheduler import schedule_day

# ─── Intents & bot setup ─────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    # 1) Prevent host from sleeping
    setup_power_management()

    # 2) Log and send greeting in Discord
    log_info(f"✅ Logged in as {bot.user}")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(f"{get_greeting()}")

    # 3) Load all cogs 
    for fname in os.listdir("cogs"):
        if not fname.endswith(".py") or fname == "__init__.py":
            continue
        mod_name = fname[:-3]
        await bot.load_extension(f"cogs.{mod_name}")
        log_info(f"✔ loaded cog: cogs.{mod_name}")

    # 4) Hand off to scheduler
    await schedule_day(bot)

if __name__ == "__main__":
    bot.run(BOT_TOKEN)
