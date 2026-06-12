import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class CommandErrorsAndHygieneTests(unittest.TestCase):

    def test_production_python_sources_do_not_contain_mojibake_markers(self):
        repo_root = Path(__file__).resolve().parents[1]
        production_paths = [
            repo_root / "football_tracker_bot.py",
            *(repo_root / "modules").glob("*.py"),
            *(repo_root / "cogs").glob("*.py"),
            *(repo_root / "utils").glob("*.py"),
        ]
        mojibake_markers = ("ðŸ", "â€”", "â€“", "â„", "â", "âš", "âœ", "ï¸", "�")
        offenders = []

        for path in production_paths:
            text = path.read_text(encoding="utf-8")
            for marker in mojibake_markers:
                if marker in text:
                    offenders.append(f"{path.relative_to(repo_root)} contains {marker!r}")

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

    def test_command_error_context_includes_full_content_and_discord_ids(self):
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
        self.assertIn("content='!ask full command text with token=abc123'", context)

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
