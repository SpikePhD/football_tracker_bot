import asyncio
import importlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests.regression_helpers import espn_match

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class UtcFootballLifecycleTests(unittest.TestCase):

    def test_time_helpers_use_configured_timezone_for_display_only(self):
        import config
        from utils import time_utils

        self.assertEqual(config.OPERATIONS_TIMEZONE, "Europe/Rome")
        self.assertEqual(time_utils.bot_tz.key, "Europe/Rome")

        utc_dt = time_utils.parse_provider_utc("2026-06-03T21:30:00Z")
        self.assertEqual(utc_dt.tzinfo, timezone.utc)
        self.assertEqual(time_utils.to_bot_tz(utc_dt).strftime("%Y-%m-%d %H:%M"), "2026-06-03 23:30")

    def test_invalid_configured_timezone_fails_clearly(self):
        import config

        with self.assertRaisesRegex(RuntimeError, "operations.timezone"):
            config._validate_timezone_name("Not/A_Real_Zone")

    def test_provider_team_aliases_normalize_and_validate(self):
        import config

        aliases = config._load_provider_team_aliases({
            "U.S.A.": "United States",
            "South Korea": "Korea Republic",
        })

        self.assertEqual(aliases["u s a"], "united states")
        self.assertEqual(aliases["south korea"], "korea republic")
        with self.assertRaisesRegex(RuntimeError, "provider_team_aliases"):
            config._load_provider_team_aliases({"South Korea": 123})

    def test_display_lookup_window_config_requires_new_key(self):
        import config

        self.assertEqual(
            config._load_display_lookup_window_hours(
                {"football_display_lookup_window_hours": 72}
            ),
            72,
        )
        with self.assertRaisesRegex(RuntimeError, "football_display_lookup_window_hours"):
            config._load_display_lookup_window_hours(
                {"football_match_lookup_window_hours": 48}
            )
        self.assertFalse(hasattr(config, "FOOTBALL_MATCH_LOOKUP_WINDOW_HOURS"))

    def test_no_active_production_imports_use_italy_time_helpers(self):
        repo_root = Path(__file__).resolve().parents[1]
        offenders = []
        forbidden = (
            "italy_now",
            "italy_tz",
            "parse_utc_to_italy",
            "get_italy_date_string",
        )
        for folder in ("modules", "cogs", "utils"):
            for path in (repo_root / folder).glob("*.py"):
                text = path.read_text(encoding="utf-8")
                if path.name == "time_utils.py":
                    active_text = "\n".join(
                        line for line in text.splitlines()
                        if not line.strip().startswith("#")
                    )
                else:
                    active_text = text
                for name in forbidden:
                    if name in active_text:
                        offenders.append(f"{path.relative_to(repo_root)} uses {name}")
        self.assertEqual(offenders, [])

    def test_cross_midnight_live_fixture_remains_trackable_by_utc_window(self):
        from modules import match_lifecycle

        match = espn_match(fixture_id="cross-1")
        match["fixture"]["date"] = "2026-06-03T21:30:00Z"
        match["fixture"]["status"] = {"short": "2H", "elapsed": 74}
        now_utc = datetime(2026, 6, 3, 22, 15, tzinfo=timezone.utc)

        self.assertTrue(match_lifecycle.is_live(match))
        self.assertEqual(match_lifecycle.fixture_identity(match), "cross-1")
        self.assertEqual(match_lifecycle.fixture_kickoff_utc(match), datetime(2026, 6, 3, 21, 30, tzinfo=timezone.utc))
        self.assertEqual(match_lifecycle.expected_ft_check_utc(match), datetime(2026, 6, 3, 23, 22, tzinfo=timezone.utc))
        self.assertTrue(match_lifecycle.should_track_fixture(match, now_utc))

    def test_pruning_retains_live_and_recent_terminal_fixtures_only(self):
        from modules import match_state

        now_utc = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            match_state.save_match_state(
                {
                    "version": 1,
                    "fixtures": {
                        "live": {
                            "fixture_id": "live",
                            "kickoff_utc": "2026-06-03T21:30:00+00:00",
                            "last_status": "2H",
                            "last_seen_utc": "2026-06-04T00:55:00+00:00",
                        },
                        "recent-ft": {
                            "fixture_id": "recent-ft",
                            "kickoff_utc": "2026-06-03T20:00:00+00:00",
                            "last_status": "FT",
                            "terminal_utc": "2026-06-04T00:00:00+00:00",
                        },
                        "old-ft": {
                            "fixture_id": "old-ft",
                            "kickoff_utc": "2026-06-02T20:00:00+00:00",
                            "last_status": "FT",
                            "terminal_utc": "2026-06-02T23:00:00+00:00",
                        },
                    },
                },
                memory_dir=memory_dir,
            )

            pruned = match_state.prune_match_tracking_state(now_utc, memory_dir=memory_dir)
            state = match_state.load_match_state(memory_dir=memory_dir)

        self.assertEqual(pruned, ["old-ft"])
        self.assertEqual(set(state["fixtures"]), {"live", "recent-ft"})

    def test_pruning_retains_ft_dedupe_state_while_fixture_remains_in_provider_window(self):
        from modules import match_state

        now_utc = datetime(2026, 6, 12, 17, 57, 32, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            match_state.save_match_state(
                {
                    "version": 1,
                    "fixtures": {
                        "760414": {
                            "fixture_id": "760414",
                            "kickoff_utc": "2026-06-12T02:00:00+00:00",
                            "expected_ft_utc": "2026-06-12T03:52:00+00:00",
                            "last_status": "FT",
                            "terminal_utc": "2026-06-12T05:57:19+00:00",
                            "ft_announced": True,
                            "memory_updated": True,
                        },
                    },
                },
                memory_dir=memory_dir,
            )

            pruned = match_state.prune_match_tracking_state(now_utc, memory_dir=memory_dir)
            state = match_state.load_match_state(memory_dir=memory_dir)

        self.assertEqual(pruned, [])
        self.assertTrue(state["fixtures"]["760414"]["ft_announced"])
        self.assertTrue(state["fixtures"]["760414"]["memory_updated"])

    def test_terminal_non_ft_fixtures_are_not_expected_ft_due(self):
        from modules import match_state

        now_utc = datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            match_state.save_match_state(
                {
                    "version": 1,
                    "fixtures": {
                        "abandoned": {
                            "fixture_id": "abandoned",
                            "kickoff_utc": "2026-06-03T20:00:00+00:00",
                            "expected_ft_utc": "2026-06-03T21:52:00+00:00",
                            "last_status": "ABD",
                            "terminal_utc": "2026-06-03T20:30:00+00:00",
                            "ft_announced": False,
                            "memory_updated": False,
                        },
                        "finished": {
                            "fixture_id": "finished",
                            "kickoff_utc": "2026-06-03T20:00:00+00:00",
                            "expected_ft_utc": "2026-06-03T21:52:00+00:00",
                            "last_status": "FT",
                            "terminal_utc": "2026-06-03T22:00:00+00:00",
                            "ft_announced": False,
                            "memory_updated": True,
                        },
                    },
                },
                memory_dir=memory_dir,
            )

            due = match_state.expected_ft_due_fixture_ids(now_utc, memory_dir=memory_dir)
            state = match_state.load_match_state(memory_dir=memory_dir)

        self.assertEqual(due, ["finished"])
        self.assertFalse(state["fixtures"]["abandoned"]["ft_announced"])
        self.assertFalse(state["fixtures"]["abandoned"]["memory_updated"])

    def test_match_state_save_is_atomic_and_preserves_existing_file_on_replace_failure(self):
        from modules import match_state

        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            match_state.save_match_state({"version": 1, "fixtures": {"old": {"fixture_id": "old"}}}, memory_dir=memory_dir)

            with patch.object(match_state.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    match_state.save_match_state(
                        {"version": 1, "fixtures": {"new": {"fixture_id": "new"}}},
                        memory_dir=memory_dir,
                    )

            state = match_state.load_match_state(memory_dir=memory_dir)
            tmp_files = list(memory_dir.glob("match_state.json.*.tmp"))

        self.assertEqual(set(state["fixtures"]), {"old"})
        self.assertEqual(tmp_files, [])

    def test_corrupt_match_state_loads_defaults_without_overwriting_file(self):
        from modules import match_state

        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            path = memory_dir / "match_state.json"
            path.write_text("{not valid json", encoding="utf-8")

            with self.assertLogs("modules.match_state", level="ERROR") as logs:
                state = match_state.load_match_state(memory_dir=memory_dir)

            self.assertEqual(state["fixtures"], {})
            self.assertEqual(path.read_text(encoding="utf-8"), "{not valid json")
            self.assertTrue(any("is corrupt" in line for line in logs.output))

    def test_ft_state_migration_is_best_effort_and_keeps_legacy_file(self):
        from modules import match_state

        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            (memory_dir / "ft_state.json").write_text(
                json.dumps({"announced_ids": ["old-1"], "last_reset_date": "2026-06-03"}),
                encoding="utf-8",
            )

            migrated = match_state.migrate_ft_state_if_needed(memory_dir=memory_dir)
            state = match_state.load_match_state(memory_dir=memory_dir)
            legacy_still_exists = (memory_dir / "ft_state.json").exists()

        self.assertTrue(migrated)
        self.assertTrue(state["fixtures"]["old-1"]["ft_announced"])
        self.assertTrue(legacy_still_exists)

    def test_ft_and_memory_flags_retry_independently(self):
        from modules import ft_handler
        from modules import match_state

        match = espn_match(fixture_id="independent-1")
        match["fixture"]["status"]["short"] = "FT"
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run(memory_dir: Path):
            with (
                patch.object(ft_handler, "post_new_general_message", AsyncMock(return_value=object())) as post_msg,
                patch.object(ft_handler, "update_match_in_memory", AsyncMock(side_effect=RuntimeError("memory down"))) as update_memory,
            ):
                await ft_handler.process_terminal_fixture(fake_bot, match, memory_dir=memory_dir)
                first = match_state.get_fixture_state("independent-1", memory_dir=memory_dir)

            with (
                patch.object(ft_handler, "post_new_general_message", AsyncMock(return_value=None)) as post_msg_retry,
                patch.object(
                    ft_handler,
                    "update_match_in_memory",
                    AsyncMock(return_value={"updated": True, "reason": "updated"}),
                ) as update_memory_retry,
            ):
                await ft_handler.process_terminal_fixture(fake_bot, match, memory_dir=memory_dir)
                second = match_state.get_fixture_state("independent-1", memory_dir=memory_dir)

            return first, second, post_msg, update_memory, post_msg_retry, update_memory_retry

        with tempfile.TemporaryDirectory() as tmp:
            first, second, post_msg, update_memory, post_msg_retry, update_memory_retry = asyncio.run(run(Path(tmp)))

        self.assertTrue(first["ft_announced"])
        self.assertFalse(first["memory_updated"])
        self.assertTrue(second["ft_announced"])
        self.assertTrue(second["memory_updated"])
        post_msg.assert_awaited_once()
        update_memory.assert_awaited_once()
        post_msg_retry.assert_not_awaited()
        update_memory_retry.assert_awaited_once()

    def test_provider_window_fetch_dedupes_dates_and_fixture_ids(self):
        from modules import api_provider

        first = espn_match(fixture_id="dup")
        first["fixture"]["date"] = "2026-06-03T21:30:00Z"
        second = espn_match(fixture_id="dup")
        second["fixture"]["date"] = "2026-06-03T21:30:00Z"
        third = espn_match(fixture_id="next")
        third["fixture"]["date"] = "2026-06-04T19:00:00Z"

        async def run():
            api_provider._football_scoreboard_cache.clear()
            api_provider._cache = []
            api_provider._cache_date = None
            api_provider._cache_ts = None
            with patch.object(
                api_provider.espn_client,
                "fetch_all_leagues_with_summary",
                AsyncMock(side_effect=[
                    {"matches": [first], "success_count": 1, "failure_count": 0, "succeeded_league_ids": [135], "failed_league_ids": []},
                    {"matches": [second, third], "success_count": 1, "failure_count": 0, "succeeded_league_ids": [135], "failed_league_ids": []},
                    {"matches": [], "success_count": 1, "failure_count": 0, "succeeded_league_ids": [135], "failed_league_ids": []},
                ]),
            ) as fetch:
                matches = await api_provider.fetch_football_window(
                    None,
                    datetime(2026, 6, 3, 20, 0, tzinfo=timezone.utc),
                    datetime(2026, 6, 4, 22, 0, tzinfo=timezone.utc),
                )
                return matches, fetch

        matches, fetch = asyncio.run(run())

        self.assertEqual([m["fixture"]["id"] for m in matches], ["dup", "next"])
        self.assertEqual(fetch.await_count, 3)

    def test_fetch_football_window_uses_injected_now_for_recent_finished_filter(self):
        from modules import api_provider

        finished = espn_match(fixture_id="recent-ft")
        finished["fixture"]["date"] = "2026-06-03T20:00:00Z"
        finished["fixture"]["status"] = {"short": "FT", "elapsed": 90}

        async def run():
            api_provider._football_scoreboard_cache.clear()
            api_provider._cache = []
            api_provider._cache_date = None
            api_provider._cache_ts = None
            with patch.object(
                api_provider.espn_client,
                "fetch_all_leagues_with_summary",
                AsyncMock(return_value={
                    "matches": [finished],
                    "success_count": 1,
                    "failure_count": 0,
                    "succeeded_league_ids": [135],
                    "failed_league_ids": [],
                }),
            ):
                return await api_provider.fetch_football_window(
                    None,
                    datetime(2026, 6, 4, 20, 0, tzinfo=timezone.utc),
                    datetime(2026, 6, 4, 21, 0, tzinfo=timezone.utc),
                    now_utc=datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc),
                )

        matches = asyncio.run(run())

        self.assertEqual([m["fixture"]["id"] for m in matches], ["recent-ft"])

    def test_fetch_relevant_football_uses_lifecycle_provider_window(self):
        from modules import api_provider, match_lifecycle

        now_utc = datetime(2026, 6, 3, 22, 15, tzinfo=timezone.utc)
        expected_start, expected_end = match_lifecycle.provider_window(now_utc)

        async def run():
            with patch.object(api_provider, "fetch_football_window", AsyncMock(return_value=[])) as fetch_window:
                await api_provider.fetch_relevant_football(None, now_utc)
                return fetch_window

        fetch_window = asyncio.run(run())
        _, start_utc, end_utc = fetch_window.await_args.args
        self.assertEqual(start_utc, expected_start)
        self.assertEqual(end_utc, expected_end)

    def test_fetch_display_football_uses_display_lookup_window(self):
        from modules import api_provider

        now_utc = datetime(2026, 6, 3, 22, 15, tzinfo=timezone.utc)

        async def run():
            with (
                patch.object(api_provider, "FOOTBALL_DISPLAY_LOOKUP_WINDOW_HOURS", 36),
                patch.object(api_provider, "fetch_football_window", AsyncMock(return_value=[])) as fetch_window,
            ):
                await api_provider.fetch_display_football(None, now_utc)
                return fetch_window

        fetch_window = asyncio.run(run())
        _, start_utc, end_utc = fetch_window.await_args.args
        self.assertEqual(start_utc, now_utc - timedelta(hours=36))
        self.assertEqual(end_utc, now_utc + timedelta(hours=36))

    def test_fetch_upcoming_football_schedule_uses_future_display_horizon(self):
        from modules import api_provider

        now_utc = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)

        async def run():
            with patch.object(api_provider, "fetch_football_window", AsyncMock(return_value=[])) as fetch_window:
                await api_provider.fetch_upcoming_football_schedule(None, now_utc, horizon_hours=10)
                return fetch_window

        fetch_window = asyncio.run(run())
        _, start_utc, end_utc = fetch_window.await_args.args
        self.assertEqual(start_utc, now_utc)
        self.assertEqual(end_utc, now_utc + timedelta(hours=10))

    def test_fetch_relevant_default_window_keeps_provider_dates_bounded(self):
        from modules import api_provider

        live_match = espn_match(fixture_id="late-live")
        live_match["fixture"]["date"] = "2026-06-03T21:30:00Z"
        live_match["fixture"]["status"] = {"short": "2H", "elapsed": 74}

        async def run():
            api_provider._football_scoreboard_cache.clear()
            api_provider._cache = []
            api_provider._cache_date = None
            api_provider._cache_ts = None
            seen_dates = []

            async def fake_scoreboard(_session, _slug_map, date_str):
                seen_dates.append(date_str)
                provider_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                return {
                    "matches": [live_match] if provider_date == "2026-06-03" else [],
                    "success_count": 1,
                    "failure_count": 0,
                    "succeeded_league_ids": [135],
                    "failed_league_ids": [],
                }

            with patch.object(api_provider.espn_client, "fetch_all_leagues_with_summary", AsyncMock(side_effect=fake_scoreboard)):
                matches = await api_provider.fetch_relevant_football(
                    None,
                    datetime(2026, 6, 3, 22, 15, tzinfo=timezone.utc),
                )
                return matches, seen_dates

        matches, seen_dates = asyncio.run(run())

        self.assertEqual([m["fixture"]["id"] for m in matches], ["late-live"])
        self.assertEqual(seen_dates, ["20260603", "20260604"])

    def test_fetch_upcoming_football_schedule_dedupes_fixture_ids(self):
        from modules import api_provider

        first = espn_match(fixture_id="future-1")
        first["fixture"]["date"] = "2026-06-03T16:00:00Z"
        duplicate = espn_match(fixture_id="future-1")
        duplicate["fixture"]["date"] = "2026-06-03T16:00:00Z"

        async def run():
            with patch.object(api_provider, "fetch_football_window", AsyncMock(return_value=[first, duplicate])):
                return await api_provider.fetch_upcoming_football_schedule(
                    None,
                    datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc),
                    horizon_hours=10,
                )

        matches = asyncio.run(run())
        self.assertEqual([m["fixture"]["id"] for m in matches], ["future-1"])

    def test_fetch_live_merges_api_football_live_endpoint_on_fallback(self):
        from modules import api_provider

        date_live = espn_match(fixture_id="date-live")
        date_live["fixture"]["date"] = "2026-06-03T21:30:00Z"
        date_live["fixture"]["status"] = {"short": "2H", "elapsed": 74}
        direct_live = espn_match(fixture_id="direct-live")
        direct_live["fixture"]["date"] = "2026-06-03T22:00:00Z"
        direct_live["fixture"]["status"] = {"short": "1H", "elapsed": 23}
        duplicate_direct = espn_match(fixture_id="date-live")
        duplicate_direct["fixture"]["date"] = "2026-06-03T21:30:00Z"
        duplicate_direct["fixture"]["status"] = {"short": "2H", "elapsed": 75}

        async def run():
            api_provider._api_live_fixtures_cache = None
            api_provider._api_live_fixtures_cache_ts = None
            with (
                patch.object(api_provider, "_espn_healthy", False),
                patch.object(api_provider, "_retry_after", datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)),
                patch.object(api_provider, "utc_now", return_value=datetime(2026, 6, 3, 22, 15, tzinfo=timezone.utc)),
                patch.object(api_provider, "fetch_relevant_football", AsyncMock(return_value=[date_live])),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider.api_client, "fetch_live_fixtures", AsyncMock(return_value=[duplicate_direct, direct_live])) as live_fetch,
            ):
                matches = await api_provider.fetch_live(None)
                return matches, live_fetch

        matches, live_fetch = asyncio.run(run())
        self.assertEqual([m["fixture"]["id"] for m in matches], ["date-live", "direct-live"])
        live_fetch.assert_awaited_once()

    def test_fetch_live_sees_previous_date_fixture_after_local_midnight(self):
        from modules import api_provider

        match = espn_match(fixture_id="late-live")
        match["fixture"]["date"] = "2026-06-03T21:30:00Z"
        match["fixture"]["status"] = {"short": "2H", "elapsed": 74}

        async def run():
            with (
                patch.object(api_provider, "utc_now", return_value=datetime(2026, 6, 3, 22, 15, tzinfo=timezone.utc)),
                patch.object(api_provider, "fetch_relevant_football", AsyncMock(return_value=[match])) as relevant,
            ):
                live = await api_provider.fetch_live(None)
                return live, relevant

        live, relevant = asyncio.run(run())
        self.assertEqual([m["fixture"]["id"] for m in live], ["late-live"])
        relevant.assert_awaited_once()

    def test_fetch_live_excludes_stale_provider_live_without_persisted_unresolved_state(self):
        from modules import api_provider

        stale_live = espn_match(fixture_id="stale-provider-live")
        stale_live["fixture"]["date"] = "2026-06-03T12:00:00Z"
        stale_live["fixture"]["status"] = {"short": "2H", "elapsed": 73}
        now_utc = datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc)

        async def run():
            with patch.object(api_provider, "fetch_relevant_football", AsyncMock(return_value=[stale_live])):
                return await api_provider.fetch_live(None, now_utc=now_utc)

        self.assertEqual(asyncio.run(run()), [])

    def test_fetch_live_keeps_stale_provider_live_when_persisted_unresolved_within_retention(self):
        from modules import api_provider, match_state

        stale_live = espn_match(fixture_id="persisted-unresolved-live")
        stale_live["fixture"]["date"] = "2026-06-03T12:00:00Z"
        stale_live["fixture"]["status"] = {"short": "2H", "elapsed": 73}
        now_utc = datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc)

        async def run():
            with (
                tempfile.TemporaryDirectory() as tmp,
                patch.object(api_provider, "fetch_relevant_football", AsyncMock(return_value=[stale_live])),
                patch.object(match_state, "BOT_MEMORY_DIR", Path(tmp)),
            ):
                match_state.save_match_state(
                    {
                        "version": 1,
                        "fixtures": {
                            "persisted-unresolved-live": {
                                "fixture_id": "persisted-unresolved-live",
                                "kickoff_utc": "2026-06-03T12:00:00+00:00",
                                "last_status": "2H",
                                "last_seen_utc": "2026-06-03T20:00:00+00:00",
                            }
                        },
                    }
                )
                return await api_provider.fetch_live(None, now_utc=now_utc)

        live = asyncio.run(run())
        self.assertEqual([m["fixture"]["id"] for m in live], ["persisted-unresolved-live"])

    def test_fetch_live_excludes_persisted_unresolved_live_after_retention_horizon(self):
        from modules import api_provider, match_state

        stale_live = espn_match(fixture_id="old-persisted-live")
        stale_live["fixture"]["date"] = "2026-06-02T12:00:00Z"
        stale_live["fixture"]["status"] = {"short": "2H", "elapsed": 73}
        now_utc = datetime(2026, 6, 4, 13, 0, tzinfo=timezone.utc)

        async def run():
            with (
                tempfile.TemporaryDirectory() as tmp,
                patch.object(api_provider, "fetch_relevant_football", AsyncMock(return_value=[stale_live])),
                patch.object(match_state, "BOT_MEMORY_DIR", Path(tmp)),
            ):
                match_state.save_match_state(
                    {
                        "version": 1,
                        "fixtures": {
                            "old-persisted-live": {
                                "fixture_id": "old-persisted-live",
                                "kickoff_utc": "2026-06-02T12:00:00+00:00",
                                "last_status": "2H",
                                "last_seen_utc": "2026-06-04T12:59:00+00:00",
                            }
                        },
                    }
                )
                return await api_provider.fetch_live(None, now_utc=now_utc)

        self.assertEqual(asyncio.run(run()), [])

    def test_scheduler_midnight_routine_does_not_clear_football_state(self):
        from modules import scheduler

        async def run():
            with (
                patch.object(scheduler, "update_standings_only", AsyncMock()) as standings,
                patch.object(scheduler, "update_team_info_only", AsyncMock()) as teams,
                patch.object(scheduler, "prune_match_tracking_state") as prune,
            ):
                await scheduler.run_local_daily_routines(None, datetime(2026, 6, 4, 0, 1, tzinfo=timezone.utc))
                return standings, teams, prune

        standings, teams, prune = asyncio.run(run())
        standings.assert_awaited_once()
        teams.assert_not_awaited()
        prune.assert_called_once()

    def test_scheduler_wakes_for_fallback_live_endpoint_when_date_window_empty(self):
        from modules import api_provider, scheduler

        live_match = espn_match(fixture_id="fallback-live")
        live_match["fixture"]["date"] = "2026-06-03T22:00:00Z"
        live_match["fixture"]["status"] = {"short": "1H", "elapsed": 12}
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            api_provider._api_live_fixtures_cache = None
            api_provider._api_live_fixtures_cache_ts = None
            with (
                patch.object(scheduler, "expected_ft_due_fixture_ids", return_value=[]),
                patch.object(api_provider, "_espn_healthy", False),
                patch.object(api_provider, "_retry_after", datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc)),
                patch.object(api_provider, "fetch_relevant_football", AsyncMock(return_value=[])) as relevant,
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider.api_client, "fetch_live_fixtures", AsyncMock(return_value=[live_match])) as live_fetch,
            ):
                needed = await scheduler._football_poll_needed(
                    fake_bot,
                    datetime(2026, 6, 3, 22, 15, tzinfo=timezone.utc),
                )
                return needed, relevant, live_fetch

        needed, relevant, live_fetch = asyncio.run(run())

        self.assertTrue(needed)
        relevant.assert_awaited_once()
        live_fetch.assert_awaited_once()

    def test_scheduler_does_not_wake_for_fully_resolved_terminal_ft_fixture(self):
        from modules import match_state, scheduler

        terminal = espn_match(fixture_id="resolved-ft")
        terminal["fixture"]["date"] = "2026-06-03T21:00:00Z"
        terminal["fixture"]["status"] = {"short": "FT", "long": "Full Time"}
        now_utc = datetime(2026, 6, 3, 23, 30, tzinfo=timezone.utc)
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            with (
                patch.object(scheduler, "expected_ft_due_fixture_ids", return_value=[]),
                patch.object(scheduler.api_provider, "fetch_relevant_football", AsyncMock(return_value=[terminal])),
                patch.object(scheduler.api_provider, "has_live_football", AsyncMock(return_value=False)) as live_check,
                patch.object(
                    match_state,
                    "get_fixture_state",
                    return_value={"fixture_id": "resolved-ft", "ft_announced": True, "memory_updated": True},
                ),
            ):
                needed = await scheduler._football_poll_needed(fake_bot, now_utc)
                return needed, live_check

        needed, live_check = asyncio.run(run())

        self.assertFalse(needed)
        live_check.assert_awaited_once()

    def test_scheduler_wakes_for_unresolved_terminal_ft_fixture(self):
        from modules import match_state, scheduler

        terminal = espn_match(fixture_id="unresolved-ft")
        terminal["fixture"]["date"] = "2026-06-03T21:00:00Z"
        terminal["fixture"]["status"] = {"short": "FT", "long": "Full Time"}
        now_utc = datetime(2026, 6, 3, 23, 30, tzinfo=timezone.utc)
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            with (
                patch.object(scheduler, "expected_ft_due_fixture_ids", return_value=[]),
                patch.object(scheduler.api_provider, "fetch_relevant_football", AsyncMock(return_value=[terminal])),
                patch.object(scheduler.api_provider, "has_live_football", AsyncMock(return_value=False)) as live_check,
                patch.object(
                    match_state,
                    "get_fixture_state",
                    return_value={"fixture_id": "unresolved-ft", "ft_announced": True, "memory_updated": False},
                ),
            ):
                needed = await scheduler._football_poll_needed(fake_bot, now_utc)
                return needed, live_check

        needed, live_check = asyncio.run(run())

        self.assertTrue(needed)
        live_check.assert_not_awaited()

    def test_scheduler_wakes_for_terminal_ft_fixture_without_persisted_state(self):
        from modules import match_state, scheduler

        terminal = espn_match(fixture_id="unknown-ft")
        terminal["fixture"]["date"] = "2026-06-03T21:00:00Z"
        terminal["fixture"]["status"] = {"short": "FT", "long": "Full Time"}
        now_utc = datetime(2026, 6, 3, 23, 30, tzinfo=timezone.utc)
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            with (
                patch.object(scheduler, "expected_ft_due_fixture_ids", return_value=[]),
                patch.object(scheduler.api_provider, "fetch_relevant_football", AsyncMock(return_value=[terminal])),
                patch.object(scheduler.api_provider, "has_live_football", AsyncMock(return_value=False)) as live_check,
                patch.object(match_state, "get_fixture_state", return_value=None),
            ):
                needed = await scheduler._football_poll_needed(fake_bot, now_utc)
                return needed, live_check

        needed, live_check = asyncio.run(run())

        self.assertTrue(needed)
        live_check.assert_not_awaited()

    def test_scheduler_does_not_wake_for_terminal_non_ft_fixture(self):
        from modules import match_state, scheduler

        terminal = espn_match(fixture_id="cancelled")
        terminal["fixture"]["date"] = "2026-06-03T21:00:00Z"
        terminal["fixture"]["status"] = {"short": "CANC", "long": "Cancelled"}
        now_utc = datetime(2026, 6, 3, 23, 30, tzinfo=timezone.utc)
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            with (
                patch.object(scheduler, "expected_ft_due_fixture_ids", return_value=[]),
                patch.object(scheduler.api_provider, "fetch_relevant_football", AsyncMock(return_value=[terminal])),
                patch.object(scheduler.api_provider, "has_live_football", AsyncMock(return_value=False)) as live_check,
                patch.object(match_state, "get_fixture_state", return_value=None),
            ):
                needed = await scheduler._football_poll_needed(fake_bot, now_utc)
                return needed, live_check

        needed, live_check = asyncio.run(run())

        self.assertFalse(needed)
        live_check.assert_awaited_once()

    def test_scheduler_sleep_plan_refreshes_before_distant_kickoff(self):
        from modules import scheduler

        future = espn_match(fixture_id="future-11h")
        future["fixture"]["date"] = "2026-06-03T23:00:00Z"
        now_utc = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            with patch.object(
                scheduler.api_provider,
                "fetch_upcoming_football_schedule",
                AsyncMock(return_value=[future]),
            ):
                return await scheduler._plan_sleep_until_next_fixture(fake_bot, now_utc)

        next_check = asyncio.run(run())

        self.assertEqual(next_check, now_utc + timedelta(hours=6))
        status = scheduler.get_football_scheduler_status()
        self.assertEqual(status["mode"], "sleeping")
        self.assertEqual(status["next_planned_kickoff_utc"], datetime(2026, 6, 3, 23, 0, tzinfo=timezone.utc))
        self.assertEqual(status["next_planned_wake_utc"], datetime(2026, 6, 3, 21, 0, tzinfo=timezone.utc))

    def test_scheduler_sleep_plan_wakes_at_prematch_window(self):
        from modules import scheduler

        future = espn_match(fixture_id="future-3h")
        future["fixture"]["date"] = "2026-06-03T15:00:00Z"
        now_utc = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            with patch.object(
                scheduler.api_provider,
                "fetch_upcoming_football_schedule",
                AsyncMock(return_value=[future]),
            ):
                return await scheduler._plan_sleep_until_next_fixture(fake_bot, now_utc)

        next_check = asyncio.run(run())

        self.assertEqual(next_check, datetime(2026, 6, 3, 13, 0, tzinfo=timezone.utc))
        self.assertEqual(
            scheduler.get_football_scheduler_status()["next_planned_wake_utc"],
            datetime(2026, 6, 3, 13, 0, tzinfo=timezone.utc),
        )

    def test_scheduler_sleep_plan_is_utc_not_local_midnight_bounded(self):
        from modules import scheduler

        late_local = espn_match(fixture_id="late-local")
        late_local["fixture"]["date"] = "2026-06-03T21:30:00Z"
        now_utc = datetime(2026, 6, 3, 20, 0, tzinfo=timezone.utc)

        kickoff, wake = scheduler._next_scheduled_football_wake([late_local], now_utc)

        self.assertEqual(kickoff, datetime(2026, 6, 3, 21, 30, tzinfo=timezone.utc))
        self.assertEqual(wake, now_utc)

    def test_scheduler_sleep_plan_without_future_match_refreshes_in_six_hours(self):
        from modules import scheduler

        now_utc = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            with patch.object(
                scheduler.api_provider,
                "fetch_upcoming_football_schedule",
                AsyncMock(return_value=[]),
            ):
                return await scheduler._plan_sleep_until_next_fixture(fake_bot, now_utc)

        next_check = asyncio.run(run())

        self.assertEqual(next_check, now_utc + timedelta(hours=6))
        self.assertIsNone(scheduler.get_football_scheduler_status()["next_planned_kickoff_utc"])

    def test_scheduler_ft_due_forces_awake_without_schedule_discovery(self):
        from modules import scheduler

        now_utc = datetime(2026, 6, 3, 23, 30, tzinfo=timezone.utc)
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            with (
                patch.object(scheduler, "expected_ft_due_fixture_ids", return_value=["due-1"]),
                patch.object(scheduler.api_provider, "fetch_relevant_football", AsyncMock()) as relevant,
                patch.object(scheduler.api_provider, "fetch_upcoming_football_schedule", AsyncMock()) as schedule_fetch,
            ):
                needed = await scheduler._football_poll_needed(fake_bot, now_utc)
                return needed, relevant, schedule_fetch

        needed, relevant, schedule_fetch = asyncio.run(run())

        self.assertTrue(needed)
        relevant.assert_not_awaited()
        schedule_fetch.assert_not_awaited()

    def test_football_scheduler_logs_only_meaningful_state_changes(self):
        from modules import scheduler

        scheduler._last_logged_football_state = None

        with self.assertLogs("modules.scheduler", level="INFO") as logs:
            scheduler._set_football_scheduler_state(
                mode="awake",
                next_football_check_utc=datetime(2026, 6, 3, 21, 1, tzinfo=timezone.utc),
            )

        self.assertEqual(len(logs.output), 1)
        self.assertIn("Football scheduler awake", logs.output[0])
        self.assertEqual(
            scheduler.get_football_scheduler_status()["next_football_check_utc"],
            datetime(2026, 6, 3, 21, 1, tzinfo=timezone.utc),
        )

        with self.assertNoLogs("modules.scheduler", level="INFO"):
            scheduler._set_football_scheduler_state(
                mode="awake",
                next_football_check_utc=datetime(2026, 6, 3, 21, 2, tzinfo=timezone.utc),
            )

        self.assertEqual(
            scheduler.get_football_scheduler_status()["next_football_check_utc"],
            datetime(2026, 6, 3, 21, 2, tzinfo=timezone.utc),
        )

        with self.assertLogs("modules.scheduler", level="INFO") as logs:
            scheduler._set_football_scheduler_state(
                mode="sleeping",
                next_football_check_utc=datetime(2026, 6, 4, 3, 0, tzinfo=timezone.utc),
                next_schedule_refresh_utc=datetime(2026, 6, 4, 3, 0, tzinfo=timezone.utc),
                next_planned_kickoff_utc=datetime(2026, 6, 4, 8, 0, tzinfo=timezone.utc),
                next_planned_wake_utc=datetime(2026, 6, 4, 6, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(len(logs.output), 1)
        self.assertIn("Football scheduler sleeping", logs.output[0])


if __name__ == "__main__":
    unittest.main()
