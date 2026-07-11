# cogs/goodmorning.py
# Configurable scheduled morning message.
# Usage: !goodmorning ON HH:MM  |  !goodmorning OFF  |  !goodmorning (status)

import logging
import re

from discord.ext import commands, tasks

from cogs.matches import fetch_combined_matches_snapshot
from config import CHANNEL_ID, OPERATIONS_TIMEZONE
from modules.bot_mode import get_mode, is_verbose
from modules.discord_poster import post_new_general_message, post_new_message_to_context
from modules.admin import is_operator
from modules.ft_handler import seed_already_announced_ft
from modules.live_loop import seed_already_posted
from modules.runtime_settings import get_morning_schedule, set_morning_schedule
from utils.personality import get_greeting
from utils.time_utils import bot_now

logger = logging.getLogger(__name__)

def _load() -> dict:
    return get_morning_schedule()


def _save(cfg: dict) -> None:
    set_morning_schedule(
        enabled=cfg["enabled"],
        hour=cfg["hour"],
        minute=cfg["minute"],
        timezone=cfg.get("timezone") or OPERATIONS_TIMEZONE,
    )


def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse HH:MM or HH,MM in configured bot time."""
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

        now = bot_now()
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
            football_fixtures, _, snapshot = await fetch_combined_matches_snapshot(self.bot.http_session)
            content = (
                f"{get_greeting()}\n\n"
                f"{snapshot}"
            )
            sent = await post_new_general_message(self.bot, CHANNEL_ID, content=content)
            if sent is not None:
                seed_already_announced_ft(football_fixtures)
                seed_already_posted(football_fixtures)
            logger.info(f"Morning message sent at {now.strftime('%H:%M')} {OPERATIONS_TIMEZONE}.")
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
            "  !goodmorning ON HH:MM - enable at given configured bot time\n"
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
                        f"**{cfg['hour']:02d}:{cfg['minute']:02d} {OPERATIONS_TIMEZONE}**."
                    ),
                )
            else:
                await post_new_message_to_context(ctx, content="Morning message is **OFF**.")
            return

        if not await is_operator(ctx):
            await post_new_message_to_context(
                ctx,
                content="You need bot-owner or `Manage Server` access to change the morning schedule.",
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
            if tz_str and tz_str != OPERATIONS_TIMEZONE:
                await post_new_message_to_context(
                    ctx,
                    content=(
                        f"Timezone is configured globally as `{OPERATIONS_TIMEZONE}`; "
                        "use `!goodmorning ON HH:MM`."
                    ),
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
            cfg["timezone"] = OPERATIONS_TIMEZONE
            cfg.pop("tz_offset_minutes", None)
            _save(cfg)

            await post_new_message_to_context(
                ctx,
                content=(
                    f"Morning message **enabled** - will fire at "
                    f"**{hour:02d}:{minute:02d} {OPERATIONS_TIMEZONE}**."
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
