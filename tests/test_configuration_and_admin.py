import asyncio
import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class ConfigurationTests(unittest.TestCase):

    def setUp(self):
        self.base = json.loads(Path("config.example.json").read_text(encoding="utf-8-sig"))

    def _write(self, path: Path, value: dict) -> None:
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_local_override_deep_merges_and_replaces_arrays(self):
        from modules import configuration

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_path = root / "config.json"
            local_path = root / "config.local.json"
            self._write(default_path, self.base)
            self._write(local_path, {
                "bot": {"name": "Local Bot"},
                "discord": {"channel_id": 999},
                "tracking": {"tennis_players": ["new player"]},
            })
            with patch.dict(os.environ, {"CHANNEL_ID": "111"}):
                effective = configuration.load_effective_config(default_path, local_path)

        self.assertEqual(effective["bot"]["name"], "Local Bot")
        self.assertEqual(effective["discord"]["channel_id"], 999)
        self.assertEqual(effective["tracking"]["tennis_players"], ["new player"])
        self.assertIn("tracked_league_ids", effective["tracking"])

    def test_legacy_environment_channel_is_migration_fallback(self):
        from modules import configuration

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_path = root / "config.json"
            self._write(default_path, self.base)
            with patch.dict(os.environ, {"CHANNEL_ID": "777"}):
                effective = configuration.load_effective_config(
                    default_path,
                    root / "missing.local.json",
                )
        self.assertEqual(effective["discord"]["channel_id"], 777)

    def test_unknown_override_key_is_rejected(self):
        from modules import configuration

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_path = root / "config.json"
            local_path = root / "config.local.json"
            self._write(default_path, self.base)
            self._write(local_path, {"operations": {"typo_interval": 5}})
            with self.assertRaisesRegex(configuration.ConfigurationError, "typo_interval"):
                configuration.load_effective_config(default_path, local_path)

    def test_duplicate_owner_bad_timezone_and_bad_range_are_rejected(self):
        from modules import configuration

        duplicate = deepcopy(self.base)
        duplicate["administration"]["owner_users"] = [
            {"id": 10, "label": "One"},
            {"id": 10, "label": "Two"},
        ]
        with self.assertRaisesRegex(configuration.ConfigurationError, "Duplicate"):
            configuration.validate_config(duplicate)

        bad_timezone = deepcopy(self.base)
        bad_timezone["operations"]["timezone"] = "Mars/Olympus"
        with self.assertRaisesRegex(configuration.ConfigurationError, "timezone"):
            configuration.validate_config(bad_timezone)

        bad_range = deepcopy(self.base)
        bad_range["operations"]["live_update_edit_window_messages"] = 0
        with self.assertRaisesRegex(configuration.ConfigurationError, "live_update"):
            configuration.validate_config(bad_range)

    def test_invalid_override_does_not_replace_existing_local_file(self):
        from modules import configuration

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_path = root / "config.json"
            local_path = root / "config.local.json"
            self._write(default_path, self.base)
            self._write(local_path, {"bot": {"name": "Existing"}})
            before = local_path.read_text(encoding="utf-8")
            with self.assertRaises(configuration.ConfigurationError):
                configuration.write_local_overrides(
                    {"operations": {"live_update_edit_window_messages": 0}},
                    default_path,
                    local_path,
                )
            self.assertEqual(local_path.read_text(encoding="utf-8"), before)

    def test_failed_atomic_override_write_preserves_existing_file(self):
        from modules import configuration

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            default_path = root / "config.json"
            local_path = root / "config.local.json"
            self._write(default_path, self.base)
            self._write(local_path, {"bot": {"name": "Existing"}})
            before = local_path.read_text(encoding="utf-8")
            with patch.object(configuration, "save_json_path", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    configuration.write_local_overrides(
                        {"bot": {"name": "Replacement"}},
                        default_path,
                        local_path,
                    )
            self.assertEqual(local_path.read_text(encoding="utf-8"), before)

    def test_secret_helpers_mask_replace_and_never_return_full_values(self):
        from modules import configuration

        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("OTHER=value\nBOT_TOKEN=old-secret-value\n", encoding="utf-8")
            status = configuration.secret_status(env_path)
            self.assertEqual(status["BOT_TOKEN"]["masked"], "***alue")
            self.assertNotIn("old-secret-value", repr(status))

            configuration.replace_secret("BOT_TOKEN", "new token # safe", env_path)
            text = env_path.read_text(encoding="utf-8")
            self.assertIn("OTHER=value", text)
            self.assertNotIn("old-secret-value", text)
            parsed = configuration.dotenv_values(env_path)
            self.assertEqual(parsed["BOT_TOKEN"], "new token # safe")
            if os.name != "nt":
                self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)

    def test_catalog_labels_live_message_window_and_secrets(self):
        from modules import configuration

        fields = {field["path"]: field for field in configuration.configuration_catalog(self.base)}
        live = fields["operations.live_update_edit_window_messages"]
        self.assertIn("fresh live update", live["description"])
        self.assertTrue(live["restart_required"])
        self.assertTrue(fields["secrets.BOT_TOKEN"]["secret"])

    def test_configuration_snapshot_never_contains_full_secrets(self):
        from modules import configuration

        with patch.dict(os.environ, {
            "BOT_TOKEN": "full-bot-secret",
            "API_KEY": "full-api-secret",
            "LLM_API_KEY": "full-llm-secret",
        }):
            snapshot = configuration.configuration_snapshot()
        rendered = repr(snapshot)
        self.assertNotIn("full-bot-secret", rendered)
        self.assertNotIn("full-api-secret", rendered)
        self.assertNotIn("full-llm-secret", rendered)


class AdministrativePolicyTests(unittest.TestCase):

    def _ctx(self, *, user_id=1, channel_id=123, manage_guild=False, app_owner=False):
        author = SimpleNamespace(
            id=user_id,
            guild_permissions=SimpleNamespace(manage_guild=manage_guild),
        )
        bot = SimpleNamespace(is_owner=AsyncMock(return_value=app_owner))
        return SimpleNamespace(
            author=author,
            channel=SimpleNamespace(id=channel_id),
            bot=bot,
        )

    def test_configured_owner_and_manage_server_tiers(self):
        from modules import admin

        configured = self._ctx(user_id=10)
        manager = self._ctx(user_id=20, manage_guild=True)
        public = self._ctx(user_id=30)
        with patch.object(admin, "BOT_OWNER_IDS", frozenset({10})):
            self.assertTrue(asyncio.run(admin.is_owner(configured)))
            self.assertFalse(asyncio.run(admin.is_owner(manager)))
            self.assertTrue(asyncio.run(admin.is_operator(manager)))
            self.assertFalse(asyncio.run(admin.is_operator(public)))

    def test_application_owner_fallback_only_when_owner_list_empty(self):
        from modules import admin

        app_owner = self._ctx(user_id=40, app_owner=True)
        with patch.object(admin, "BOT_OWNER_IDS", frozenset()):
            self.assertTrue(asyncio.run(admin.is_owner(app_owner)))
        with patch.object(admin, "BOT_OWNER_IDS", frozenset({99})):
            self.assertFalse(asyncio.run(admin.is_owner(app_owner)))

    def test_wrong_channel_and_dm_are_rejected(self):
        from modules import admin

        with patch.object(admin, "CHANNEL_ID", 123):
            self.assertTrue(asyncio.run(admin.command_channel_check(self._ctx(channel_id=123))))
            with self.assertRaises(admin.WrongCommandChannel):
                asyncio.run(admin.command_channel_check(self._ctx(channel_id=456)))
            dm = self._ctx(channel_id=123)
            dm.channel = SimpleNamespace()
            with self.assertRaises(admin.WrongCommandChannel):
                asyncio.run(admin.command_channel_check(dm))

    def test_sensitive_and_operator_commands_have_checks(self):
        from cogs.ask import Ask
        from cogs.football_lifecycle import FootballLifecycle
        from cogs.log import LogCog
        from cogs.mode import Mode
        from cogs.update import UpdateCog

        for command in (
            UpdateCog.update_cmd,
            LogCog.log_export,
            Ask.refresh_memory,
            Ask.dump_memory,
            Mode.verbose,
            Mode.normal,
            Mode.silent,
            FootballLifecycle.match_state_command,
            FootballLifecycle.football_lifecycle_command,
        ):
            self.assertTrue(command.checks, command.name)

    def test_shared_redaction_removes_environment_and_labeled_secrets(self):
        from utils.redaction import redact_text

        with patch.dict(os.environ, {"BOT_TOKEN": "actual.bot.token-value"}):
            result = redact_text(
                "actual.bot.token-value token=another-secret "
                "abcdefghijklmnopqrstuvwxyz123456"
            )
        self.assertNotIn("actual.bot.token-value", result)
        self.assertNotIn("another-secret", result)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", result)

    def test_updater_output_tail_is_character_bounded(self):
        from cogs import update

        with patch.object(update, "OUTPUT_MAX_CHARS", 100):
            result = update._tail_lines("x" * 1000, max_lines=30)
        self.assertIn("[truncated", result)
        self.assertLessEqual(len(result), 150)

    def test_commands_list_hides_commands_that_fail_authorization(self):
        from cogs import commands_list

        class FakeCommand:
            def __init__(self, name, allowed):
                self.name = name
                self.aliases = []
                self.help = f"{name} help"
                self.hidden = False
                self.allowed = allowed

            async def can_run(self, _ctx):
                if not self.allowed:
                    from discord.ext import commands
                    raise commands.CheckFailure("denied")
                return True

        bot = SimpleNamespace(commands=[
            FakeCommand("matches", True),
            FakeCommand("update", False),
        ])

        async def run():
            with patch.object(commands_list, "post_new_message_to_context", AsyncMock()) as post:
                cog = commands_list.CommandsList(bot)
                await cog.commands_list.callback(cog, SimpleNamespace())
                return post.await_args.kwargs["content"]

        content = asyncio.run(run())
        self.assertIn("!matches", content)
        self.assertNotIn("!update", content)


if __name__ == "__main__":
    unittest.main()
