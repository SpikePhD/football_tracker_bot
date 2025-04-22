# football_tracker_bot.py

import asyncio
import discord
from discord.ext import commands

from config import BOT_TOKEN, CHANNEL_ID
from utils.personality import get_greeting
from modules.power_manager import setup_power_management
from modules.verbose_logger import log_info
from modules.scheduler import schedule_day

# ─── Intents & bot setup ─────────────────────────
intents = discord.Intents.default()    # <— correct place for Intents
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    # 1) Prevent host from sleeping
    setup_power_management()

    # 2) Log and send “I’m alive!” in Discord
    log_info(f"✅ Logged in as {bot.user}")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(f"I am alive! {get_greeting()}")

    # 3) Hand off to scheduler (prints today’s fixtures, sleeps until KO, then
    #    starts your 8‑minute live loop + FT checks until midnight)
    await schedule_day(bot)


if __name__ == "__main__":
    bot.run(BOT_TOKEN)
