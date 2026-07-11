import asyncio
import logging
import pathlib
import subprocess

from discord.ext import commands

from modules.discord_poster import post_new_message_to_context
from modules.admin import owner_only
from utils.redaction import redact_text
from modules.dashboard_process import ProcessController

logger = logging.getLogger(__name__)

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
UPDATE_TIMEOUT_SEC = 300
OUTPUT_TAIL_LINES = 30
OUTPUT_MAX_CHARS = 6000


def _git_short_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_DIR), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()
    except Exception:
        return "unknown"


def _tail_lines(text: str, max_lines: int = OUTPUT_TAIL_LINES) -> str:
    lines = (text or "").splitlines()
    if not lines:
        return "(no output)"
    tail = lines[-max_lines:]
    result = "\n".join(tail)
    if len(result) > OUTPUT_MAX_CHARS:
        return "[truncated to final output characters]\n" + result[-OUTPUT_MAX_CHARS:]
    return result


class UpdateCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._update_lock = asyncio.Lock()

    @commands.command(
        name="update",
        aliases=["pull"],
        help="Run update.sh now to pull latest code and restart service.",
    )
    @owner_only()
    async def update_cmd(self, ctx: commands.Context) -> None:
        if self._update_lock.locked():
            await post_new_message_to_context(
                ctx,
                content="Update already in progress. Please wait.",
            )
            return

        await post_new_message_to_context(
            ctx,
            content="Starting update now (`bash update.sh`). Service may restart during this command.",
        )

        before_sha = _git_short_sha()

        async with self._update_lock:
            process = None
            try:
                managed = await ProcessController().start_update()
                if managed.get("ok"):
                    await post_new_message_to_context(
                        ctx,
                        content="Managed update started. The bot and dashboard will restart when it completes.",
                    )
                    return
                process = await asyncio.create_subprocess_exec(
                    "bash",
                    "update.sh",
                    cwd=str(REPO_DIR),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=UPDATE_TIMEOUT_SEC,
                )

                stdout_text = stdout_bytes.decode("utf-8", errors="replace")
                stderr_text = stderr_bytes.decode("utf-8", errors="replace")
                combined = stdout_text
                if stderr_text.strip():
                    combined = f"{combined}\n[stderr]\n{stderr_text}" if combined else f"[stderr]\n{stderr_text}"

                after_sha = _git_short_sha()
                exit_code = process.returncode if process.returncode is not None else -1

                status = "SUCCESS" if exit_code == 0 else "FAILED"
                summary = _tail_lines(redact_text(combined), OUTPUT_TAIL_LINES)
                summary = summary.replace("```", "` ` `")
                msg = (
                    f"Update {status}\n"
                    f"Exit code: `{exit_code}`\n"
                    f"Commit: `{before_sha}` -> `{after_sha}`\n"
                    f"Output tail:\n```text\n{summary}\n```"
                )
                await post_new_message_to_context(ctx, content=msg)
            except asyncio.TimeoutError:
                logger.error("!update timed out after %ss", UPDATE_TIMEOUT_SEC)
                if process and process.returncode is None:
                    process.kill()
                    try:
                        await process.communicate()
                    except Exception:
                        pass
                await post_new_message_to_context(
                    ctx,
                    content=(
                        f"Update timed out after {UPDATE_TIMEOUT_SEC}s and was terminated."
                    ),
                )
            except Exception as e:
                logger.error("!update failed unexpectedly: %s", e, exc_info=True)
                await post_new_message_to_context(
                    ctx,
                    content=f"Update failed unexpectedly: {e}",
                )


async def setup(bot: commands.Bot):
    await bot.add_cog(UpdateCog(bot))
    logger.info("cogs.update loaded")
