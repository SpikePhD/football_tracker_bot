import asyncio
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class CommandErrorsAndHygieneTests(unittest.TestCase):

    def test_git_tracked_text_files_do_not_contain_mojibake_markers(self):
        repo_root = Path(__file__).resolve().parents[1]
        tracked_files = subprocess.check_output(
            ["git", "ls-files"],
            cwd=repo_root,
            text=True,
            encoding="utf-8",
        ).splitlines()
        mojibake_markers = ("ðŸ", "â€”", "â€“", "â„", "â", "âš", "âœ", "ï¸", "�")
        offenders = []

        for relative_path in tracked_files:
            path = repo_root / relative_path
            if not path.exists():
                # A tracked file may be intentionally deleted in the working tree.
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if (
                    relative_path == "tests/test_command_errors_and_hygiene.py"
                    and "mojibake_markers =" in line
                ):
                    continue
                for marker in mojibake_markers:
                    if marker in line:
                        offenders.append(f"{relative_path}:{line_number} contains {marker!r}")

        self.assertEqual(offenders, [])

    def test_tennis_pre_announce_config_has_production_callsite(self):
        repo_root = Path(__file__).resolve().parents[1]
        tennis_loop = repo_root / "modules" / "tennis_loop.py"
        text = tennis_loop.read_text(encoding="utf-8")

        self.assertIn("TENNIS_PRE_ANNOUNCE_HOURS", text)
        self.assertIn("timedelta(hours=TENNIS_PRE_ANNOUNCE_HOURS)", text)

    def test_old_football_lookup_config_constant_is_not_imported_by_production_modules(self):
        repo_root = Path(__file__).resolve().parents[1]
        production_paths = [
            *(repo_root / "modules").glob("*.py"),
            *(repo_root / "cogs").glob("*.py"),
            *(repo_root / "utils").glob("*.py"),
            repo_root / "football_tracker_bot.py",
        ]
        offenders = []
        for path in production_paths:
            text = path.read_text(encoding="utf-8")
            if "FOOTBALL_MATCH_LOOKUP_WINDOW_HOURS" in text:
                offenders.append(str(path.relative_to(repo_root)))

        self.assertEqual(offenders, [])

    def test_committed_configs_do_not_use_old_football_lookup_key(self):
        repo_root = Path(__file__).resolve().parents[1]
        offenders = []
        for filename in ("config.json", "config.example.json"):
            text = (repo_root / filename).read_text(encoding="utf-8-sig")
            if "football_match_lookup_window_hours" in text:
                offenders.append(filename)

        self.assertEqual(offenders, [])

    def test_secondary_api_key_is_not_in_active_config_contract(self):
        repo_root = Path(__file__).resolve().parents[1]
        checked_paths = [
            repo_root / "config.py",
            repo_root / ".env.example",
            repo_root / "README.md",
            *(repo_root / "modules").glob("*.py"),
            *(repo_root / "cogs").glob("*.py"),
            *(repo_root / "utils").glob("*.py"),
        ]
        offenders = []
        for path in checked_paths:
            text = path.read_text(encoding="utf-8")
            if "SECONDARY_API_KEY" in text:
                offenders.append(str(path.relative_to(repo_root)))

        self.assertEqual(offenders, [])

    def test_production_logs_do_not_use_misleading_empty_live_fetch_error_phrase(self):
        repo_root = Path(__file__).resolve().parents[1]
        checked_paths = [
            repo_root / "football_tracker_bot.py",
            *(repo_root / "modules").glob("*.py"),
            *(repo_root / "cogs").glob("*.py"),
            *(repo_root / "utils").glob("*.py"),
        ]
        offenders = []
        for path in checked_paths:
            text = path.read_text(encoding="utf-8")
            if "No live fixtures returned or fetch error" in text:
                offenders.append(str(path.relative_to(repo_root)))

        self.assertEqual(offenders, [])

    def test_log_export_uses_default_line_limit_after_filtering(self):
        from cogs import log as log_cog

        lines = [f"[2026-07-07 12:00:0{i}] [INFO    ] [tests] line {i}\n" for i in range(5)]

        with (
            patch.object(log_cog, "LOG_EXPORT_DEFAULT_LINES", 2),
            patch.object(log_cog, "LOG_EXPORT_MAX_LINES", 10),
        ):
            payload, truncated = log_cog._build_export(
                lines=lines,
                mode="today",
                value=None,
                max_bytes=10000,
            )

        self.assertFalse(truncated)
        self.assertNotIn("line 0", payload)
        self.assertNotIn("line 2", payload)
        self.assertIn("line 3", payload)
        self.assertIn("line 4", payload)

    def test_log_export_max_lines_caps_default_line_limit(self):
        from cogs import log as log_cog

        lines = [f"[2026-07-07 12:00:0{i}] [ERROR   ] [tests] line {i}\n" for i in range(5)]

        with (
            patch.object(log_cog, "LOG_EXPORT_DEFAULT_LINES", 5),
            patch.object(log_cog, "LOG_EXPORT_MAX_LINES", 3),
        ):
            payload, truncated = log_cog._build_export(
                lines=lines,
                mode="errors",
                value=None,
                max_bytes=10000,
            )

        self.assertFalse(truncated)
        self.assertNotIn("line 0", payload)
        self.assertNotIn("line 1", payload)
        self.assertIn("line 2", payload)
        self.assertIn("line 3", payload)
        self.assertIn("line 4", payload)

    def test_log_export_still_truncates_at_byte_limit(self):
        from cogs import log as log_cog

        lines = [
            "[2026-07-07 12:00:00] [INFO    ] [tests] " + ("x" * 200) + "\n",
            "[2026-07-07 12:00:01] [INFO    ] [tests] " + ("y" * 200) + "\n",
        ]

        payload, truncated = log_cog._build_export(
            lines=lines,
            mode="today",
            value=None,
            max_bytes=160,
        )

        self.assertTrue(truncated)
        self.assertIn("[truncated: export hit byte limit]", payload)

    def test_log_export_header_uses_configured_bot_name(self):
        from cogs import log as log_cog

        with patch.object(log_cog, "BOT_NAME", "Configured Bot", create=True):
            payload, _ = log_cog._build_export(
                lines=[],
                mode="today",
                value=None,
                max_bytes=10000,
            )

        self.assertIn("Configured Bot Log Export", payload)
        self.assertNotIn("Marco Van Botten Log Export", payload)

    def test_version_command_header_uses_configured_bot_name(self):
        from cogs import version

        async def run():
            with (
                patch.object(version, "BOT_NAME", "Configured Bot"),
                patch.object(
                    version,
                    "get_version_info",
                    return_value={
                        "sha": "abc123",
                        "date": "2026-07-07 12:00",
                        "message": "test commit",
                    },
                ),
                patch.object(version, "post_new_message_to_context", AsyncMock()) as post_message,
            ):
                cog = version.VersionCommand(bot=None)
                await cog.version_cmd.callback(cog, SimpleNamespace())
                return post_message.await_args.kwargs["content"]

        content = asyncio.run(run())

        self.assertIn("🤖 **Configured Bot**", content)
        self.assertNotIn("🤖 **Marco Van Botten**", content)

    def test_daily_log_collection_script_and_runbook_are_present(self):
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "collect_daily_logs.sh"
        operations = (repo_root / "OPERATIONS.md").read_text(encoding="utf-8")

        self.assertTrue(script.exists())
        text = script.read_text(encoding="utf-8")
        self.assertIn("journalctl", text)
        self.assertIn("bot_memory/logs/bot.log", text)
        self.assertIn("bot_memory/log_exports/daily", text)
        self.assertIn("marco_van_botten", text)
        self.assertIn("RETENTION_DAYS=\"${RETENTION_DAYS:-30}\"", text)
        self.assertIn("logs_*.tar.gz", text)
        self.assertIn("tail -n +$((RETENTION_DAYS + 1))", text)
        self.assertIn("app_warning_error_count=", text)
        self.assertIn("journal_warning_error_count=", text)
        self.assertIn("app_error_count=", text)
        self.assertIn("app_warning_count=", text)
        self.assertIn("journal_error_count=", text)
        self.assertIn("journal_warning_count=", text)
        self.assertNotIn("printf 'warning_error_count=", text)
        self.assertIn("APP_WARNING_RE=", text)
        self.assertIn("APP_ERROR_RE=", text)
        self.assertIn("sort \"$APP_EXPORT_TMP\" > \"$APP_EXPORT\"", text)
        self.assertNotIn("grep -Eih 'ERROR|CRITICAL|Traceback|Exception' \"$APP_EXPORT\"", text)
        self.assertIn("systemd journal may duplicate app output", text)
        self.assertIn("collect_daily_logs.sh", operations)
        self.assertIn("Daily log rotation", operations)
        self.assertIn("0 6 * * *", operations)
        self.assertIn("keeps the newest 30 daily archives", operations)
        self.assertIn("app_warning_error_count", operations)
        self.assertIn("journal_warning_error_count", operations)
        self.assertIn("severity labels", operations)
        self.assertIn("chronological order", operations)

    def test_daily_log_collection_sorts_app_lines_and_counts_severity_labels(self):
        if os.name == "nt":
            self.skipTest("Bash integration test is exercised on POSIX deployment hosts.")
        if not shutil.which("bash") or not shutil.which("tar"):
            self.skipTest("bash/tar not available")

        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "collect_daily_logs.sh"
        target_date = "2026-06-17"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "bot_memory" / "logs"
            log_dir.mkdir(parents=True)
            (log_dir / "bot.log").write_text(
                "\n".join([
                    "[2026-06-17 23:59:50] [INFO    ] [modules.live_loop] later line",
                    "[2026-06-17 00:00:41] [INFO    ] [modules.live_loop] No live fixtures returned or fetch error.",
                ]) + "\n",
                encoding="utf-8",
            )
            (log_dir / "bot.log.1").write_text(
                "\n".join([
                    "[2026-06-17 06:00:00] [WARNING ] [modules.test] warning line",
                    "[2026-06-17 07:00:00] [ERROR   ] [modules.test] error line",
                ]) + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                ["bash", str(script), target_date],
                cwd=repo_root,
                env={**os.environ, "ROOT_DIR": str(root), "SERVICE_NAME": "missing_test_service"},
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            export_dir = root / "bot_memory" / "log_exports" / "daily" / target_date
            app_export = export_dir / f"bot_app_{target_date}.log"
            summary = export_dir / f"summary_{target_date}.txt"

            lines = app_export.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines, sorted(lines))

            summary_text = summary.read_text(encoding="utf-8")
            self.assertIn("app_warning_error_count=2", summary_text)
            self.assertIn("app_error_count=1", summary_text)
            self.assertIn("app_warning_count=1", summary_text)

    def test_command_error_context_omits_content_and_includes_discord_ids(self):
        import football_tracker_bot

        ctx = SimpleNamespace(
            command=SimpleNamespace(
                name="ask",
                qualified_name="ask",
                cog=SimpleNamespace(qualified_name="AskCog"),
            ),
            author=SimpleNamespace(id=111, name="Luca", display_name="Luca Display"),
            channel=SimpleNamespace(id=222, name="bot-test"),
            guild=SimpleNamespace(id=333, name="Guild Name"),
            message=SimpleNamespace(
                id=444,
                content="!ask full command text with token=abc123",
                attachments=[object(), object()],
            ),
        )

        context = football_tracker_bot._format_command_error_context(ctx)

        self.assertIn("command=ask", context)
        self.assertIn("qualified=ask", context)
        self.assertIn("cog=AskCog", context)
        self.assertIn("author_id=111", context)
        self.assertIn("author_name=Luca", context)
        self.assertIn("channel_id=222", context)
        self.assertIn("channel_name=bot-test", context)
        self.assertIn("guild_id=333", context)
        self.assertIn("guild_name=Guild Name", context)
        self.assertIn("message_id=444", context)
        self.assertIn("attachments=2", context)
        self.assertIn("content_length=40", context)
        self.assertNotIn("token=abc123", context)
        self.assertNotIn("full command text", context)

    def test_command_error_unwraps_command_invoke_error(self):
        from discord.ext import commands
        import football_tracker_bot

        original = ValueError("boom")
        wrapped = commands.CommandInvokeError(original)

        self.assertIs(football_tracker_bot._unwrap_command_error(wrapped), original)

    def test_command_error_action_classifies_expected_errors_as_warnings(self):
        from discord.ext import commands
        import football_tracker_bot

        action = football_tracker_bot._command_error_action(commands.BadArgument("bad input"))

        self.assertFalse(action["ignore"])
        self.assertEqual(action["log_level"], "warning")
        self.assertFalse(action["log_traceback"])
        self.assertIn("Invalid command argument", action["user_message"])

    def test_command_error_action_ignores_unknown_commands(self):
        from discord.ext import commands
        import football_tracker_bot

        action = football_tracker_bot._command_error_action(commands.CommandNotFound("missing"))

        self.assertTrue(action["ignore"])
        self.assertIsNone(action["user_message"])
        self.assertFalse(action["log_traceback"])

    def test_command_error_action_classifies_unexpected_errors_for_traceback_logging(self):
        import football_tracker_bot

        action = football_tracker_bot._command_error_action(RuntimeError("boom"))

        self.assertFalse(action["ignore"])
        self.assertEqual(action["log_level"], "error")
        self.assertTrue(action["log_traceback"])
        self.assertIn("Command failed unexpectedly", action["user_message"])

    def test_regression_guard_context_includes_previous_and_current_state(self):
        from modules import live_loop

        detail = live_loop._regression_guard_context(
            match_id="m1",
            previous_observed={"home": 2, "away": 1, "elapsed": 75, "events_count": 3},
            current_score={"home": 1, "away": 1},
            current_elapsed=60,
            current_events_count=2,
            state_key="m1_1-1_2",
            reason="score,elapsed",
        )

        self.assertIn("match=m1", detail)
        self.assertIn("reason=score,elapsed", detail)
        self.assertIn("prev_score=2-1", detail)
        self.assertIn("curr_score=1-1", detail)
        self.assertIn("prev_elapsed=75", detail)
        self.assertIn("curr_elapsed=60", detail)
        self.assertIn("prev_events=3", detail)
        self.assertIn("curr_events=2", detail)
        self.assertIn("state_key=m1_1-1_2", detail)



if __name__ == "__main__":
    unittest.main()
