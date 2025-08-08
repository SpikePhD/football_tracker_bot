# football_tracker_bot.py

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
import pytz
from datetime import time

import discord
from discord.ext import commands, tasks
import aiohttp # Make sure aiohttp is imported

from config import BOT_TOKEN, CHANNEL_ID
from utils.personality import greet_message
from modules.power_manager import setup_power_management
from modules.scheduler import schedule_day
import subprocess, pathlib

logger = logging.getLogger(__name__)

REPO_DIR = pathlib.Path(__file__).resolve().parent
if not (REPO_DIR / ".git").is_dir():
    REPO_DIR = REPO_DIR.parent

def current_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_DIR), "describe", "--always", "--dirty", "--tags"],
            text=True, stderr=subprocess.STDOUT
        ).strip()
    except Exception:
        try:
            return subprocess.check_output(
                ["git", "-C", str(REPO_DIR), "rev-parse", "--short", "HEAD"],
                text=True, stderr=subprocess.STDOUT
            ).strip()
        except Exception:
            return "unknown"

# --- Intents & bot setup ---
intents = discord.Intents.default()
intents.message_content = True # Ensure this is enabled if you use message content
bot = commands.Bot(command_prefix="!", intents=intents)

italy_tz = pytz.timezone("Europe/Rome")
current_day_scheduler_task: asyncio.Task | None = None

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

@tasks.loop(time=time(hour=11, minute=0, tzinfo=italy_tz))
async def eleven_am_daily_trigger():
    logger.info("⏰ 11:00 AM (Europe/Rome) – Triggering daily operations schedule manager.")
    asyncio.create_task(launch_daily_operations_manager(bot))

# --- Bot Events ---
@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    logger.info(f"🚀 Running bot from commit: {current_git_commit()}")
    await ensure_http_session(bot) # Ensure session is ready
    setup_power_management()

    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        try:
            await channel.send(greet_message())
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
    logger.info(f"🔄 Discord session RESUMED. Commit: {current_git_commit()}")

@bot.event
async def on_disconnect():
    """Called when the bot has disconnected from Discord.
    This could be for a number of reasons. Reconnects are usually automatic.
    """
    logger.warning("🔌 Bot disconnected. Initiating session cleanup. Reconnection will be attempted by discord.py.")

original_bot_close = bot.close
async def new_bot_close():
    logger.info("🛑 Bot close initiated. Cleaning up tasks and sessions...")
    if eleven_am_daily_trigger.is_running():
        eleven_am_daily_trigger.cancel()
        logger.info("Task loop 'eleven_am_daily_trigger' cancelled.")

    global current_day_scheduler_task
    if current_day_scheduler_task and not current_day_scheduler_task.done():
        current_day_scheduler_task.cancel()
        logger.info("Current 'schedule_day' task cancelled.")
        try:
            await current_day_scheduler_task # Allow it to process cancellation
        except asyncio.CancelledError:
            logger.info("'schedule_day' task processed cancellation.")
        except Exception as e:
            logger.error(f"Error during 'schedule_day' task cleanup: {e}", exc_info=True)


    await cleanup_sessions()
    await original_bot_close()
    logger.info("👋 Bot has been shut down gracefully.")

bot.close = new_bot_close


if __name__ == "__main__":
    try:
        bot.run(BOT_TOKEN)
    except Exception as e:
        logger.critical(f"💥 Unhandled exception at bot.run() level: {e}", exc_info=True)
    finally:
        if asyncio.get_event_loop().is_running():
             asyncio.get_event_loop().run_until_complete(cleanup_sessions())
             logger.info("Final cleanup_sessions call completed from __main__ finally block.")
        else:
            logger.info("Event loop not running in __main__ finally block. Cleanup relied on bot.close().")