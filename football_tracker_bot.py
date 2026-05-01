# football_tracker_bot.py
# line to manually test auto_update.sh. Test 1.

import logging
import sys # For directing to stdout

# Standard logging setup
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)-8s] [%(name)-20s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)

import os
import asyncio
from datetime import time

import discord
from discord.ext import commands, tasks
import aiohttp # Make sure aiohttp is imported

from config import BOT_TOKEN, CHANNEL_ID
from utils.personality import greet_message
from modules.power_manager import setup_power_management
from modules.scheduler import schedule_day
from modules.bot_mode import is_verbose, get_mode
from modules.discord_poster import post_new_general_message
from cogs.matches import build_combined_matches_message_from_api
from cogs.version import get_version_info
from utils.time_utils import italy_tz

logger = logging.getLogger(__name__)

# --- Intents & bot setup ---
intents = discord.Intents.default()
intents.message_content = True # Ensure this is enabled if you use message content
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
current_day_scheduler_task: asyncio.Task | None = None
_startup_completed = False

# --- HTTP Session Management ---
async def ensure_http_session(bot_instance: commands.Bot):
    """
    Ensures that an active aiohttp.ClientSession is available on the bot instance.
    Creates a new session if one doesn't exist or if the existing one is closed.
    """
    if not hasattr(bot_instance, 'http_session') or bot_instance.http_session is None or bot_instance.http_session.closed:
        bot_instance.http_session = aiohttp.ClientSession()
        logger.info("🚀 Global aiohttp.ClientSession (re)created.")
    return bot_instance.http_session

async def cleanup_sessions():
    """Closes the global aiohttp.ClientSession if it exists and is open."""
    if hasattr(bot, 'http_session') and bot.http_session is not None and not bot.http_session.closed:
        await bot.http_session.close()
        logger.info("💨 Global aiohttp.ClientSession closed.")

# --- Task Management for Scheduler ---
async def launch_daily_operations_manager(bot_instance: commands.Bot):
    """
    Manages the lifecycle of the schedule_day task, ensuring only one instance runs.
    """
    global current_day_scheduler_task

    # Ensure HTTP session is active before starting operations
    await ensure_http_session(bot_instance)

    if current_day_scheduler_task and not current_day_scheduler_task.done():
        logger.info("🔄 A daily operations schedule is already running. Attempting to cancel previous instance...")
        current_day_scheduler_task.cancel()
        try:
            await current_day_scheduler_task
        except asyncio.CancelledError:
            logger.info("👍 Previous daily operations schedule task successfully cancelled.")
        except Exception as e:
            logger.error(f"🚨 Error while awaiting cancellation of previous task: {e}", exc_info=True) # Added exc_info

    logger.info("🚀 Launching new daily operations schedule task...")
    current_day_scheduler_task = asyncio.create_task(schedule_day(bot_instance))

    try:
        await current_day_scheduler_task
    except asyncio.CancelledError:
        logger.info("📅 Daily operations schedule task was cancelled (likely during shutdown or restart).")
    except Exception as e:
        logger.error(f"💥 Daily operations schedule task failed: {e}", exc_info=True) # Added exc_info

@tasks.loop(time=time(hour=0, minute=1, tzinfo=italy_tz))
async def midnight_trigger():
    logger.info("🌙 00:01 AM (Europe/Rome) – Midnight trigger: restarting daily operations for new day.")
    await launch_daily_operations_manager(bot)

@tasks.loop(time=time(hour=11, minute=0, tzinfo=italy_tz))
async def eleven_am_daily_trigger():
    logger.info("⏰ 11:00 AM (Europe/Rome) – Triggering daily operations schedule manager.")
    await launch_daily_operations_manager(bot)

# --- Bot Events ---
@bot.event
async def on_ready():
    global _startup_completed
    if _startup_completed:
        logger.info("on_ready fired again after reconnect; skipping startup bootstrap.")
        return
    _startup_completed = True

    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"🚀 Running bot from commit: {get_version_info()['sha']}")
    await ensure_http_session(bot) # Ensure session is ready
    setup_power_management()

    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        if not is_verbose():
            logger.info(f"📢 Startup broadcast skipped (mode: {get_mode()}).")
        else:
            try:
                content = f"{greet_message()}\n\n{await build_combined_matches_message_from_api(bot.http_session)}"
                await post_new_general_message(bot, CHANNEL_ID, content=content)
            except discord.Forbidden:
                logger.error(f"❌ Missing permissions to send greeting message to channel ID: {CHANNEL_ID}.")
            except Exception as e:
                logger.error(f"❌ Failed to send greeting message: {e}", exc_info=True)
    else:
        logger.error(f"❌ Could not find channel with ID: {CHANNEL_ID}. Greeting and updates will not be sent.")

    # Load cogs
    cogs_path = "cogs"
    if os.path.exists(cogs_path) and os.path.isdir(cogs_path):
        loaded_cogs_count = 0
        for fname in os.listdir(cogs_path):
            if fname.endswith(".py") and fname != "__init__.py":
                try:
                    await bot.load_extension(f"{cogs_path}.{fname[:-3]}")
                    loaded_cogs_count += 1
                except commands.ExtensionError as e:
                    logger.error(f"❌ Failed to load cog {cogs_path}.{fname[:-3]}: {e}", exc_info=True)
        if loaded_cogs_count == 0:
            logger.warning("ℹ️ No cogs were loaded from the 'cogs' directory.") # Changed to warning
    else:
        logger.warning(f"ℹ️ Cogs directory '{cogs_path}' not found. No cogs loaded.") # Changed to warning

    logger.info("🚀 Bot ready. Kicking off initial daily operations schedule manager.")
    asyncio.create_task(launch_daily_operations_manager(bot))

    if not midnight_trigger.is_running():
        try:
            midnight_trigger.start()
            logger.info("Task loop 'midnight_trigger' has been started.")
        except RuntimeError as e:
            logger.error(f"❌ Failed to start 'midnight_trigger': {e}.", exc_info=True)

    if not eleven_am_daily_trigger.is_running():
        try:
            eleven_am_daily_trigger.start()
            logger.info("Task loop 'eleven_am_daily_trigger' has been started.")
        except RuntimeError as e:
             logger.error(f"❌ Failed to start 'eleven_am_daily_trigger': {e}. It might already be running or bot is closing.", exc_info=True)


@bot.event
async def on_resumed():
    """Called when the bot successfully resumes a session after a disconnection."""
    logger.info("🔄 Discord session RESUMED. Ensuring HTTP session is active.")
    await ensure_http_session(bot)
    logger.info(f"🔄 Discord session RESUMED. Commit: {get_version_info()['sha']}")

@bot.event
async def on_disconnect():
    """Called when the bot has disconnected from Discord.
    This could be for a number of reasons. Reconnects are usually automatic.
    """
    logger.warning("🔌 Bot disconnected. Initiating session cleanup. Reconnection will be attempted by discord.py.")

async def main():
    try:
        async with bot:
            await bot.start(BOT_TOKEN)
    finally:
        logger.info("🛑 Shutting down. Cleaning up tasks and sessions...")
        if midnight_trigger.is_running():
            midnight_trigger.cancel()
            logger.info("Task loop 'midnight_trigger' cancelled.")
        if eleven_am_daily_trigger.is_running():
            eleven_am_daily_trigger.cancel()
            logger.info("Task loop 'eleven_am_daily_trigger' cancelled.")
        if current_day_scheduler_task and not current_day_scheduler_task.done():
            current_day_scheduler_task.cancel()
            logger.info("Current 'schedule_day' task cancelled.")
            try:
                await current_day_scheduler_task
            except asyncio.CancelledError:
                logger.info("'schedule_day' task processed cancellation.")
            except Exception as e:
                logger.error(f"Error during 'schedule_day' task cleanup: {e}", exc_info=True)
        await cleanup_sessions()
        logger.info("👋 Bot has been shut down gracefully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.critical(f"💥 Unhandled exception: {e}", exc_info=True)
