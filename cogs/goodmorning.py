# cogs/goodmorning.py
# Configurable scheduled morning message.
# Usage: !goodmorning ON HH:MM GMT+N  |  !goodmorning OFF  |  !goodmorning (status)

import re
import logging
from datetime import datetime, timezone, timedelta

from discord.ext import commands, tasks

from modules.storage import load, save
from modules.bot_mode import is_verbose, get_mode
from modules import api_provider
from cogs.matches import build_matches_message
from utils.personality import get_greeting
from config import CHANNEL_ID

logger = logging.getLogger(__name__)

_STORAGE_FILE = "goodmorning.json"
_DEFAULTS = {
    "enabled": True,
    "hour": 6,
    "minute": 30,
    "tz_offset_minutes": 60,  # UTC+1 (Italy CET)
}


def _load() -> dict:
    return load(_STORAGE_FILE, _DEFAULTS)


def _save(cfg: dict) -> None:
    save(_STORAGE_FILE, cfg)


def _parse_time_and_tz(time_str: str, tz_str: str) -> tuple[int, int, int]:
    """
    Parse "HH:MM" (or "HH,MM") and "GMT+N" / "GMT-N" / "GMT+H:MM".
    Returns (hour, minute, tz_offset_minutes).
    Raises ValueError with a user-friendly message on bad input.
    """
    # Accept both colon and comma as time separators (Italian locale)
    parts = re.split(r"[:,]", time_str.strip())
    if len(parts) != 2:
        raise ValueError(f"Invalid time `{time_str}`. Use HH:MM (e.g. `7:00`).")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid time `{time_str}`. Hour and minute must be numbers.")
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Hour must be 0–23 and minute 0–59.")

    m = re.fullmatch(r"GMT([+-])(\d{1,2})(?::(\d{2}))?", tz_str.strip(), re.IGNORECASE)
    if not m:
        raise ValueError(f"Invalid timezone `{tz_str}`. Use GMT+N or GMT-N (e.g. `GMT+2`).")
    sign = 1 if m.group(1) == "+" else -1
    tz_h = int(m.group(2))
    tz_m = int(m.group(3) or 0)
    if tz_h > 14 or tz_m >= 60:
        raise ValueError("Timezone offset out of range (max ±14h).")
    offset_minutes = sign * (tz_h * 60 + tz_m)

    return hour, minute, offset_minutes


def _format_offset(offset_minutes: int) -> str:
    """Format offset in minutes as GMT+H or GMT+H:MM."""
    sign = "+" if offset_minutes >= 0 else "-"
    total = abs(offset_minutes)
    h, m = divmod(total, 60)
    return f"GMT{sign}{h}" if m == 0 else f"GMT{sign}{h}:{m:02d}"


class GoodMorning(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_sent_date: str | None = None
        self.morning_check.start()

    def cog_unload(self):
        self.morning_check.cancel()

    # ── Scheduled task ────────────────────────────────────────────────────────

    @tasks.loop(minutes=1)
    async def morning_check(self):
        cfg = _load()
        if not cfg.get("enabled", False):
            return

        tz = timezone(timedelta(minutes=cfg.get("tz_offset_minutes", 60)))
        now = datetime.now(tz)

        if now.hour != cfg["hour"] or now.minute != cfg["minute"]:
            return

        today = now.date().isoformat()
        if self._last_sent_date == today:
            return  # already fired today
        self._last_sent_date = today

        if not is_verbose():
            logger.info(f"📢 Scheduled morning message skipped (mode: {get_mode()}).")
            return

        channel = self.bot.get_channel(CHANNEL_ID)
        if not channel:
            logger.error(f"❌ GoodMorning: channel {CHANNEL_ID} not found.")
            return

        try:
            session = self.bot.http_session
            fixtures = await api_provider.fetch_day(session)
            fixtures = await api_provider.enrich_fixtures(session, fixtures)
            fixtures.sort(key=lambda m: m["fixture"]["date"])
            content = f"{get_greeting()}\n\n{build_matches_message(fixtures)}"
            await channel.send(content)
            logger.info(
                f"✅ Morning message sent at {now.strftime('%H:%M')} "
                f"({_format_offset(cfg.get('tz_offset_minutes', 60))})."
            )
        except Exception as e:
            logger.error(f"❌ GoodMorning: failed to send message: {e}", exc_info=True)

    @morning_check.before_loop
    async def before_morning_check(self):
        await self.bot.wait_until_ready()

    # ── Command ───────────────────────────────────────────────────────────────

    @commands.command(
        name="goodmorning",
        aliases=["gm"],
        help="Configure the scheduled morning message.\n"
             "  !goodmorning ON HH:MM GMT+N   — enable at given time/timezone\n"
             "  !goodmorning OFF              — disable\n"
             "  !goodmorning                  — show current setting",
    )
    async def goodmorning_cmd(
        self,
        ctx: commands.Context,
        action: str = None,
        time_str: str = None,
        tz_str: str = None,
    ):
        cfg = _load()

        # ── Status (no args) ────────────────────────────────────────────────
        if action is None:
            if cfg.get("enabled"):
                tz_label = _format_offset(cfg.get("tz_offset_minutes", 60))
                await ctx.send(
                    f"Morning message is **ON** — fires at "
                    f"**{cfg['hour']:02d}:{cfg['minute']:02d} {tz_label}**."
                )
            else:
                await ctx.send("Morning message is **OFF**.")
            return

        action = action.upper()

        # ── OFF ─────────────────────────────────────────────────────────────
        if action == "OFF":
            cfg["enabled"] = False
            _save(cfg)
            await ctx.send("Morning message **disabled**.")
            return

        # ── ON ──────────────────────────────────────────────────────────────
        if action == "ON":
            if not time_str or not tz_str:
                await ctx.send(
                    "Usage: `!goodmorning ON HH:MM GMT+N`\n"
                    "Example: `!goodmorning ON 7:00 GMT+2`"
                )
                return
            try:
                hour, minute, offset_minutes = _parse_time_and_tz(time_str, tz_str)
            except ValueError as e:
                await ctx.send(f"Error: {e}")
                return

            cfg["enabled"] = True
            cfg["hour"] = hour
            cfg["minute"] = minute
            cfg["tz_offset_minutes"] = offset_minutes
            _save(cfg)

            tz_label = _format_offset(offset_minutes)
            await ctx.send(
                f"Morning message **enabled** — will fire at **{hour:02d}:{minute:02d} {tz_label}**."
            )
            return

        await ctx.send(
            "Unknown action. Use `!goodmorning ON HH:MM GMT+N` or `!goodmorning OFF`."
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(GoodMorning(bot))
    logger.info("✔ cogs.goodmorning loaded")
