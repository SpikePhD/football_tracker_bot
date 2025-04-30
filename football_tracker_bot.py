# ───────────────────────── football_tracker_bot.py ─────────────────────────
"""
Entry-point for “Marco Van Botten” (Football Tracker Bot).

• Loads all cogs in /cogs
• Greets on startup and prints today’s fixtures
• Calls schedule_day(bot) on boot  ➜  sleeps until first KO / tracks / FT
• Runs schedule_day(bot) again every day at 11:00 Italy time
"""

import asyncio
import os
from datetime import datetime, time as dt_time, timedelta

import pytz
import discord
from discord.ext import commands

# ─── project modules ────────────────────────────────────────────────────────
from config import BOT_TOKEN, CHANNEL_ID
from utils.personality import get_greeting
from modules.verbose_logger import log_info, log_error
from modules.power_manager import setup_power_management
from modules.scheduler import schedule_day          # (prints list + launches loops)
# ────────────────────────────────────────────────────────────────────────────

italy_tz = pytz.timezone("Europe/Rome")

# ─── Discord bot setup ──────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ────────────────────────────────────────────────────────────────────────────
# Helper: run schedule_day() at 11:00 🇮🇹 every day
# ────────────────────────────────────────────────────────────────────────────
async def daily_scheduler_loop():
    """
    Background task: sleeps until the next 11:00 Europe/Rome, then
    executes schedule_day(bot).  Loops forever.
    """
    while True:
        now = datetime.now(italy_tz)

        target = datetime.combine(
            now.date(), dt_time(hour=11, minute=0, tzinfo=italy_tz)
        )
        if now >= target:                # it’s already after 11:00 → next day
            target += timedelta(days=1)

        delta = target - now
        log_info(
            f"🕚 Daily loop sleeping {delta.seconds//3600}h{(delta.seconds//60)%60}m "
            f"until {target.strftime('%d %b %H:%M')}"
        )
        await asyncio.sleep(delta.total_seconds())

        # 11:00 reached
        log_info("🕚 Running daily schedule_day()")
        try:
            await schedule_day(bot)
        except Exception as exc:         # don’t let the helper die silently
            log_error(f"Exception in daily schedule_day • {exc}")

# ────────────────────────────────────────────────────────────────────────────
# Helper: greet & print today’s fixtures (via schedule_day) once on boot
# ────────────────────────────────────────────────────────────────────────────
async def first_boot_sequence():
    # power-save tweaks
    setup_power_management()

    # greeting in Discord
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(get_greeting())

    # run morning logic once immediately
    await schedule_day(bot)


# ─── Discord events ────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    log_info(f"✅ Logged in as {bot.user}")

    # start background loop only once
    if not getattr(bot, "_daily_task_started", False):
        bot.loop.create_task(daily_scheduler_loop())
        bot._daily_task_started = True

    # run first-boot logic
    await first_boot_sequence()


# ─── Cog loader (optional) ─────────────────────────────────────────────────
def load_all_cogs():
    cogs_dir = os.path.join(os.path.dirname(__file__), "cogs")
    if not os.path.isdir(cogs_dir):
        return
    for fname in os.listdir(cogs_dir):
        if fname.endswith(".py") and fname != "__init__.py":
            try:
                bot.load_extension(f"cogs.{fname[:-3]}")
                log_info(f"✔ loaded cog: cogs.{fname[:-3]}")
            except Exception as exc:
                log_error(f"Failed loading cog {fname}: {exc}")


# ─── main ──────────────────────────────────────────────────────────────────
def main() -> None:
    load_all_cogs()
    try:
        bot.run(BOT_TOKEN)
    except KeyboardInterrupt:
        log_info("🛑 Bot stopped via Ctrl-C")

if __name__ == "__main__":
    main()
# ────────────────────────────────────────────────────────────────────────────
