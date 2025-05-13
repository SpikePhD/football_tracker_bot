# football_tracker_bot.py (Additions/Modifications)
import logging
import sys # For directing to stdout

logging.basicConfig(
    level=logging.INFO,  # Default level - INFO and above will be shown
    # Use logging.DEBUG when you need more detailed tracing during development
    format="[%(asctime)s] [%(levelname)-8s] [%(name)-20s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout, # Explicitly set stream to stdout for console output
)

import os
import asyncio
import pytz
from datetime import time

import discord
from discord.ext import commands, tasks
import aiohttp # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< NEW: Import aiohttp

from config import BOT_TOKEN, CHANNEL_ID
from utils.personality import greet_message
from modules.power_manager import setup_power_management
from modules.scheduler import schedule_day

logger = logging.getLogger(__name__) 
# Using __name__ is standard and will use the module's path as the logger name (e.g., "football_tracker_bot")
# Alternatively, you could use a fixed name like: logger = logging.getLogger("MarcoVanBotten")

# --- Intents & bot setup ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

italy_tz = pytz.timezone("Europe/Rome")
current_day_scheduler_task: asyncio.Task | None = None

# --- Function to manage schedule_day task (from previous step, assumed present) ---
async def launch_daily_operations_manager(bot_instance: commands.Bot):
    # ... (your existing robust manager function) ...
    global current_day_scheduler_task
    
    if current_day_scheduler_task and not current_day_scheduler_task.done():
        logger.info("🔄 A daily operations schedule is already running. Attempting to cancel previous instance...")
        current_day_scheduler_task.cancel()
        try:
            await current_day_scheduler_task
        except asyncio.CancelledError:
            logger.info("👍 Previous daily operations schedule task successfully cancelled.")
        except Exception as e:
            logger.error(f"🚨 Error while awaiting cancellation of previous task: {e}")
    
    logger.info("🚀 Launching new daily operations schedule task...")
    current_day_scheduler_task = asyncio.create_task(schedule_day(bot_instance)) # Pass bot_instance
    
    try:
        await current_day_scheduler_task
    except asyncio.CancelledError:
        logger.info("📅 Daily operations schedule task was cancelled.")
    except Exception as e:
        logger.error(f"💥 Daily operations schedule task failed: {e}")


@tasks.loop(time=time(hour=11, minute=0, tzinfo=italy_tz))
async def eleven_am_daily_trigger():
    logger.info("⏰ 11:00 AM (Europe/Rome) – Triggering daily operations schedule manager.")
    asyncio.create_task(launch_daily_operations_manager(bot))

# --- Bot Events ---
@bot.event
async def on_ready():
    # <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< NEW: Create ClientSession here
    if not hasattr(bot, 'http_session') or bot.http_session.closed:
        bot.http_session = aiohttp.ClientSession()
        logger.info("🚀 Global aiohttp.ClientSession created.")
    # >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

    setup_power_management()
    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(greet_message())
    else:
        logger.error(f"❌ Could not find channel with ID: {CHANNEL_ID}. Updates will not be sent.")

    # Load cogs (your existing logic)
    cogs_path = "cogs" 
    if os.path.exists(cogs_path) and os.path.isdir(cogs_path):
        loaded_cogs_count = 0
        for fname in os.listdir(cogs_path):
            if fname.endswith(".py") and fname != "__init__.py":
                try:
                    await bot.load_extension(f"{cogs_path}.{fname[:-3]}")
                    logger.info(f"✔ Loaded cog: {cogs_path}.{fname[:-3]}")
                    loaded_cogs_count += 1
                except commands.ExtensionError as e:
                    logger.error(f"❌ Failed to load cog {cogs_path}.{fname[:-3]}: {e}")
        if loaded_cogs_count == 0:
            logger.info("ℹ️ No cogs were loaded from the 'cogs' directory.")
    else:
        logger.info(f"ℹ️ Cogs directory '{cogs_path}' not found. No cogs loaded.")

    logger.info("🚀 Bot ready. Kicking off initial daily operations schedule manager.")
    asyncio.create_task(launch_daily_operations_manager(bot))

    if not eleven_am_daily_trigger.is_running():
        eleven_am_daily_trigger.start()
        logger.info("Task loop 'eleven_am_daily_trigger' has been started.")

# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< NEW: Ensure ClientSession is closed on bot shutdown
async def cleanup_sessions():
    if hasattr(bot, 'http_session') and not bot.http_session.closed:
        await bot.http_session.close()
        logger.info("💨 Global aiohttp.ClientSession closed.")

@bot.event
async def on_disconnect(): # This event is usually reliable for cleanup
    logger.info("🔌 Bot disconnected. Initiating session cleanup...")
    await cleanup_sessions()

# It's also good practice to attempt cleanup if the bot is explicitly closed.
# We can hook into the bot's close method.
original_bot_close = bot.close
async def new_bot_close():
    logger.info("🛑 Bot close initiated. Cleaning up sessions...")
    await cleanup_sessions()
    await original_bot_close()
bot.close = new_bot_close
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

if __name__ == "__main__":
    bot.run(BOT_TOKEN)