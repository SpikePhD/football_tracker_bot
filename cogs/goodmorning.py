# cogs/goodmorning.py
# Configurable scheduled morning message.
# Usage: !goodmorning ON HH:MM  |  !goodmorning OFF  |  !goodmorning (status)

import logging
import re

from discord.ext import commands, tasks

from cogs.matches import build_combined_matches_message_from_api
from config import CHANNEL_ID
from modules.bot_mode import get_mode, is_verbose
from modules.discord_poster import post_new_general_message, post_new_message_to_context
from modules.storage import load, save
from utils.personality import get_greeting
from utils.time_utils import italy_now

logger = logging.getLogger(__name__)

_STORAGE_FILE = "goodmorning.json"
_DEFAULTS = {
    "enabled": True,
    "hour": 6,
    "minute": 30,
    "timezone": "Europe/Rome",
}


def _load() -> dict:
    return load(_STORAGE_FILE, _DEFAULTS)


def _save(cfg: dict) -> None:
    save(_STORAGE_FILE, cfg)


def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse HH:MM or HH,MM in Europe/Rome time."""
    parts = re.split(r"[:,]", time_str.strip())
    if len(parts) != 2:
        raise ValueError(f"Invalid time `{time_str}`. Use HH:MM, for example `7:00`.")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid time `{time_str}`. Hour and minute must be numbers.")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Hour must be 0-23 and minute 0-59.")
    return hour, minute


def _can_manage(ctx: commands.Context) -> bool:
    perms = getattr(getattr(ctx, "author", None), "guild_permissions", None)
    return bool(getattr(perms, "manage_guild", False))


class GoodMorning(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_sent_date: str | None = None
        self.morning_check.start()

    def cog_unload(self):
        self.morning_check.cancel()

    @tasks.loop(minutes=1)
    async def morning_check(self):
        cfg = _load()
        if not cfg.get("enabled", False):
            return

        now = italy_now()
        if now.hour != cfg["hour"] or now.minute != cfg["minute"]:
            return

        today = now.date().isoformat()
        if self._last_sent_date == today:
            return
        self._last_sent_date = today

        if not is_verbose():
            logger.info(f"Scheduled morning message skipped (mode: {get_mode()}).")
            return

        try:
            content = (
                f"{get_greeting()}\n\n"
                f"{await build_combined_matches_message_from_api(self.bot.http_session)}"
            )
            await post_new_general_message(self.bot, CHANNEL_ID, content=content)
            logger.info(f"Morning message sent at {now.strftime('%H:%M')} Europe/Rome.")
        except Exception as e:
            logger.error(f"GoodMorning: failed to send message: {e}", exc_info=True)

    @morning_check.before_loop
    async def before_morning_check(self):
        await self.bot.wait_until_ready()

    @commands.command(
        name="goodmorning",
        aliases=["gm"],
        help=(
            "Configure the scheduled morning message.\n"
            "  !goodmorning ON HH:MM - enable at given Europe/Rome time\n"
            "  !goodmorning OFF - disable\n"
            "  !goodmorning - show current setting"
        ),
    )
    async def goodmorning_cmd(
        self,
        ctx: commands.Context,
        action: str = None,
        time_str: str = None,
        tz_str: str = None,
    ):
        cfg = _load()

        if action is None:
            if cfg.get("enabled"):
                await post_new_message_to_context(
                    ctx,
                    content=(
                        f"Morning message is **ON** - fires at "
                        f"**{cfg['hour']:02d}:{cfg['minute']:02d} Europe/Rome**."
                    ),
                )
            else:
                await post_new_message_to_context(ctx, content="Morning message is **OFF**.")
            return

        if not _can_manage(ctx):
            await post_new_message_to_context(
                ctx,
                content="You need the `Manage Server` permission to change the morning schedule.",
            )
            return

        action = action.upper()
        if action == "OFF":
            cfg["enabled"] = False
            _save(cfg)
            await post_new_message_to_context(ctx, content="Morning message **disabled**.")
            return

        if action == "ON":
            if not time_str:
                await post_new_message_to_context(
                    ctx,
                    content="Usage: `!goodmorning ON HH:MM`\nExample: `!goodmorning ON 7:00`",
                )
                return
            if tz_str and tz_str.lower() not in {"europe/rome", "italy", "rome"}:
                await post_new_message_to_context(
                    ctx,
                    content="Timezone is fixed to `Europe/Rome`; use `!goodmorning ON HH:MM`.",
                )
                return
            try:
                hour, minute = _parse_time(time_str)
            except ValueError as e:
                await post_new_message_to_context(ctx, content=f"Error: {e}")
                return

            cfg["enabled"] = True
            cfg["hour"] = hour
            cfg["minute"] = minute
            cfg["timezone"] = "Europe/Rome"
            cfg.pop("tz_offset_minutes", None)
            _save(cfg)

            await post_new_message_to_context(
                ctx,
                content=(
                    f"Morning message **enabled** - will fire at "
                    f"**{hour:02d}:{minute:02d} Europe/Rome**."
                ),
            )
            return

        await post_new_message_to_context(
            ctx,
            content="Unknown action. Use `!goodmorning ON HH:MM` or `!goodmorning OFF`.",
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(GoodMorning(bot))
    logger.info("cogs.goodmorning loaded")
