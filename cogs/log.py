import logging
import re
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import commands

from config import (
    BOT_NAME,
    LOG_EXPORT_DEFAULT_LINES,
    LOG_EXPORT_MAX_BYTES,
    LOG_EXPORT_MAX_LINES,
    LOG_FILE_PATH,
)
from modules.discord_poster import post_new_message_to_context
from utils.time_utils import bot_now

logger = logging.getLogger(__name__)

_LEVEL_PATTERN = re.compile(r"\[(WARNING|ERROR|CRITICAL)\s*\]")
_MODULE_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{2,80}$")
_LINE_TS_PATTERN = re.compile(r"^\[(\d{4}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}\]")
_SECRET_PATTERN = re.compile(
    r"(?i)\b("
    r"api[_-]?key|token|secret|password|authorization|bearer"
    r")\b\s*[:=]\s*([^\s,;]+)"
)
_LONG_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9_\-]{24,}\b")


def _iter_log_paths(base_path: Path) -> list[Path]:
    """
    Return log files in chronological order:
    bot.log.N (highest N -> oldest first) ... bot.log.1 ... bot.log (current).
    """
    parent = base_path.parent
    base_name = base_path.name
    candidates = [p for p in parent.glob(f"{base_name}*") if p.is_file()]

    rotated: list[tuple[int, Path]] = []
    current: list[Path] = []
    for path in candidates:
        if path.name == base_name:
            current.append(path)
            continue
        m = re.match(rf"^{re.escape(base_name)}\.(\d+)$", path.name)
        if m:
            rotated.append((int(m.group(1)), path))

    rotated.sort(key=lambda t: t[0], reverse=True)
    ordered = [p for _, p in rotated] + current
    return ordered


def _read_today_lines(base_path: Path, today_str: str) -> list[str]:
    lines: list[str] = []
    for path in _iter_log_paths(base_path):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = _LINE_TS_PATTERN.match(line)
                    if not m:
                        continue
                    if m.group(1) == today_str:
                        lines.append(line)
        except Exception:
            continue
    return lines


def _redact_line(line: str) -> str:
    line = _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}=***REDACTED***", line)

    # Redact suspicious long tokens but avoid timestamps/known numeric IDs.
    def _mask_if_token(match: re.Match) -> str:
        value = match.group(0)
        if value.isdigit():
            return value
        return "***REDACTED_TOKEN***"

    return _LONG_TOKEN_PATTERN.sub(_mask_if_token, line)


def _build_export(
    lines: list[str],
    mode: str,
    value: str | None,
    max_bytes: int,
) -> tuple[str, bool]:
    header = [
        f"{BOT_NAME} Log Export",
        f"generated_at={datetime.now().isoformat(timespec='seconds')}",
        f"mode={mode}",
        f"filter={value or '-'}",
        "",
    ]
    output = "\n".join(header)

    truncated = False
    max_lines = min(LOG_EXPORT_DEFAULT_LINES, LOG_EXPORT_MAX_LINES)
    export_lines = lines[-max_lines:] if max_lines > 0 else []
    for raw in export_lines:
        clean = _redact_line(raw.rstrip("\n"))
        candidate = f"{output}{clean}\n"
        if len(candidate.encode("utf-8")) > max_bytes:
            truncated = True
            break
        output = candidate

    if truncated:
        output += "\n[truncated: export hit byte limit]\n"
    return output, truncated


class LogCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log_path = Path(LOG_FILE_PATH)
        self.export_dir = Path("bot_memory/log_exports")
        self.export_dir.mkdir(parents=True, exist_ok=True)

    @commands.command(
        name="log",
        help="Export runtime logs: !log | !log errors | !log module <module_name>",
    )
    async def log_export(
        self,
        ctx: commands.Context,
        mode: str = "today",
        *,
        value: str | None = None,
    ) -> None:
        mode = mode.lower().strip()
        value = (value or "").strip() or None

        if mode not in {"today", "recent", "errors", "module"}:
            await post_new_message_to_context(
                ctx,
                content="Usage: `!log`, `!log today`, `!log errors`, `!log module <module_name>`",
            )
            return

        if mode == "module":
            if not value:
                await post_new_message_to_context(
                    ctx,
                    content="Usage: `!log module <module_name>` (example: `modules.api_provider`)",
                )
                return
            if not _MODULE_PATTERN.match(value):
                await post_new_message_to_context(
                    ctx,
                    content="Invalid module filter. Use letters, digits, `_`, `-`, `.` only.",
                )
                return

        if not self.log_path.exists():
            await post_new_message_to_context(
                ctx,
                content=f"Log file not found at `{self.log_path}`.",
            )
            return

        try:
            today_str = bot_now().date().isoformat()
            raw_lines = _read_today_lines(self.log_path, today_str)

            if mode in {"today", "recent"}:
                selected = raw_lines
            elif mode == "errors":
                selected = [line for line in raw_lines if _LEVEL_PATTERN.search(line)]
            else:
                selected = [line for line in raw_lines if f"[{value}" in line]

            if not selected:
                await post_new_message_to_context(
                    ctx,
                    content=f"No matching log entries found for {today_str} (bot local date).",
                )
                return

            payload, _ = _build_export(
                lines=selected,
                mode=mode,
                value=value,
                max_bytes=LOG_EXPORT_MAX_BYTES,
            )

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = value.replace(".", "_") if value else mode
            export_path = self.export_dir / f"log_export_{suffix}_{stamp}.txt"
            export_path.write_text(payload, encoding="utf-8")

            try:
                await post_new_message_to_context(
                    ctx,
                    attachments=[discord.File(export_path, filename=export_path.name)],
                )
            finally:
                export_path.unlink(missing_ok=True)

        except Exception as e:
            logger.error(f"Failed to export logs via !log: {e}", exc_info=True)
            await post_new_message_to_context(
                ctx,
                content="Failed to export logs. Check runtime logs for details.",
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(LogCog(bot))
    logger.info("cogs.log loaded")
