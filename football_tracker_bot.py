# football_tracker_bot.py
# line to manually test auto_update.sh. Test 1.

import logging
import sys # For directing to stdout
from pathlib import Path
from logging.handlers import RotatingFileHandler

import os
import asyncio
from datetime import time

import discord
from discord.ext import commands, tasks
import aiohttp # Make sure aiohttp is imported

from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    LOG_FILE_BACKUP_COUNT,
    LOG_FILE_MAX_BYTES,
    LOG_FILE_PATH,
)
from utils.personality import greet_message
from modules.power_manager import setup_power_management
from modules.scheduler import schedule_day
from modules.bot_mode import is_verbose, get_mode
from modules.discord_poster import post_new_general_message, post_new_message_to_context
from modules.ft_handler import seed_already_announced_ft
from cogs.matches import fetch_combined_matches_snapshot
from cogs.version import get_version_info
from utils.time_utils import italy_tz

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    """Configure root logger to stdout + rotating file."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)-8s] [%(name)-20s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    log_path = Path(LOG_FILE_PATH)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        filename=log_path,
        maxBytes=LOG_FILE_MAX_BYTES,
        backupCount=LOG_FILE_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root.addHandler(stdout_handler)
    root.addHandler(file_handler)


_configure_logging()

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

def _unwrap_command_error(error: commands.CommandError | Exception) -> Exception:
    """Return the original exception behind discord.py command wrappers."""
    if isinstance(error, commands.CommandInvokeError) and error.original is not None:
        return error.original
    return error


def _format_command_error_context(ctx: commands.Context) -> str:
    """Build a single-line command context for grep-friendly diagnostics."""
    command = getattr(ctx, "command", None)
    message = getattr(ctx, "message", None)
    author = getattr(ctx, "author", None)
    channel = getattr(ctx, "channel", None)
    guild = getattr(ctx, "guild", None)
    cog = getattr(command, "cog", None) if command is not None else None
    attachments = getattr(message, "attachments", []) if message is not None else []

    command_name = getattr(command, "name", None) or "unknown"
    qualified_name = getattr(command, "qualified_name", None) or command_name
    cog_name = getattr(cog, "qualified_name", None) or (cog.__class__.__name__ if cog else "none")
    content = getattr(message, "content", "") if message is not None else ""

    return " ".join(
        [
            f"command={command_name}",
            f"qualified={qualified_name}",
            f"cog={cog_name}",
            f"author_id={getattr(author, 'id', None)}",
            f"author_name={getattr(author, 'name', None)}",
            f"author_display={getattr(author, 'display_name', None)}",
            f"channel_id={getattr(channel, 'id', None)}",
            f"channel_name={getattr(channel, 'name', None)}",
            f"guild_id={getattr(guild, 'id', None)}",
            f"guild_name={getattr(guild, 'name', None)}",
            f"message_id={getattr(message, 'id', None)}",
            f"attachments={len(attachments or [])}",
            f"content={content!r}",
        ]
    )


def _command_error_action(error: commands.CommandError | Exception) -> dict:
    """Classify command errors for logging and optional user replies."""
    original = _unwrap_command_error(error)

    if isinstance(original, commands.CommandNotFound):
        return {
            "ignore": True,
            "log_level": "debug",
            "log_traceback": False,
            "user_message": None,
        }

    if isinstance(original, commands.CommandOnCooldown):
        return {
            "ignore": False,
            "log_level": "warning",
            "log_traceback": False,
            "user_message": f"Command is on cooldown. Try again in {original.retry_after:.0f}s.",
        }

    if isinstance(original, commands.MissingPermissions):
        return {
            "ignore": False,
            "log_level": "warning",
            "log_traceback": False,
            "user_message": "You do not have permission to use that command.",
        }

    if isinstance(original, commands.NotOwner):
        return {
            "ignore": False,
            "log_level": "warning",
            "log_traceback": False,
            "user_message": "Only the bot owner can use that command.",
        }

    if isinstance(original, commands.MissingRequiredArgument):
        param_name = getattr(getattr(original, "param", None), "name", "unknown")
        return {
            "ignore": False,
            "log_level": "warning",
            "log_traceback": False,
            "user_message": f"Missing required argument: `{param_name}`.",
        }

    if isinstance(original, commands.BadArgument):
        return {
            "ignore": False,
            "log_level": "warning",
            "log_traceback": False,
            "user_message": "Invalid command argument. Check the command format and try again.",
        }

    return {
        "ignore": False,
        "log_level": "error",
        "log_traceback": True,
        "user_message": "Command failed unexpectedly. Check runtime logs for details.",
    }


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
                football_fixtures, _, snapshot = await fetch_combined_matches_snapshot(bot.http_session)
                content = f"{greet_message()}\n\n{snapshot}"
                sent = await post_new_general_message(bot, CHANNEL_ID, content=content)
                if sent is not None:
                    seed_already_announced_ft(football_fixtures)
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
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Log command failures with enough context to diagnose them from exported logs."""
    original = _unwrap_command_error(error)
    action = _command_error_action(error)
    context = _format_command_error_context(ctx)

    if action["ignore"]:
        logger.debug(
            "Ignored command error: %s error_type=%s error=%s",
            context,
            type(original).__name__,
            original,
        )
        return

    log_message = (
        f"Command error: {context} "
        f"error_type={type(original).__name__} error={original!r}"
    )
    if action["log_level"] == "warning":
        logger.warning(log_message)
    else:
        exc_info = (type(original), original, original.__traceback__) if action["log_traceback"] else None
        logger.error(log_message, exc_info=exc_info)

    if action["user_message"]:
        await post_new_message_to_context(ctx, content=action["user_message"])


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
