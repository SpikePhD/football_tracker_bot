import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from tests.regression_helpers import espn_match, shootout_match

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class FtAndLiveLoopTests(unittest.TestCase):

    def test_terminal_non_ft_fixture_updates_state_without_posting(self):
        from modules import ft_handler, match_state
        from modules import api_provider

        match = espn_match(fixture_id="abd-1")
        match["fixture"]["status"]["short"] = "ABD"
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run(memory_dir: Path):
            with (
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(ft_handler, "post_new_general_message", AsyncMock()) as post_msg,
                patch.object(ft_handler, "update_match_in_memory", AsyncMock()) as update_memory,
            ):
                await ft_handler.process_terminal_fixture(fake_bot, match, memory_dir=memory_dir)
                state = match_state.get_fixture_state("abd-1", memory_dir=memory_dir)
                return state, post_msg, update_memory

        with tempfile.TemporaryDirectory() as tmp:
            state, post_msg, update_memory = asyncio.run(run(Path(tmp)))

        self.assertEqual(state["last_status"], "ABD")
        self.assertFalse(state.get("ft_announced", False))
        post_msg.assert_not_awaited()
        update_memory.assert_not_awaited()

    def test_ft_post_after_penalties_includes_winner_score_and_not_shootout_as_goals(self):
        from modules import ft_handler

        match = shootout_match()
        match["fixture"]["status"]["short"] = "FT"
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 123})()

        async def run():
            with patch.object(ft_handler, "post_new_general_message", AsyncMock(return_value=fake_message)) as post_msg:
                result = await ft_handler._post_ft_from_data(fake_bot, match)
                return result, post_msg

        result, post_msg = asyncio.run(run())
        content = post_msg.await_args.kwargs["content"]

        self.assertTrue(result)
        self.assertIn("FT: Home 1 - 1 Away", content)
        self.assertIn("Home win 4 - 3 on penalties", content)
        self.assertIn("Pens scored: Home: H1, H2, H3, H4; Away: A1, A2, A3", content)
        self.assertIn("5' - Home Goal", content)
        self.assertNotIn("120' - H1", content)

    def test_api_football_terminal_penalty_status_posts_and_updates_memory_once(self):
        from modules import api_provider, ft_handler, match_state

        match = shootout_match()
        match["fixture"]["status"] = {
            "short": "PEN",
            "long": "Match Finished",
            "elapsed": 120,
        }
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 123})()

        async def run(memory_dir: Path):
            with (
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(ft_handler, "post_new_general_message", AsyncMock(return_value=fake_message)) as post_msg,
                patch.object(
                    ft_handler,
                    "update_match_in_memory",
                    AsyncMock(return_value={"updated": True, "reason": "updated"}),
                ) as update_memory,
            ):
                await ft_handler.process_terminal_fixture(fake_bot, match, memory_dir=memory_dir)
                await ft_handler.process_terminal_fixture(fake_bot, match, memory_dir=memory_dir)
                state = match_state.get_fixture_state("shootout-1", memory_dir=memory_dir)
                return state, post_msg, update_memory

        with tempfile.TemporaryDirectory() as tmp:
            state, post_msg, update_memory = asyncio.run(run(Path(tmp)))

        self.assertEqual(state["last_status"], "PEN_DONE")
        self.assertTrue(state["ft_announced"])
        self.assertTrue(state["memory_updated"])
        post_msg.assert_awaited_once()
        update_memory.assert_awaited_once()

    def test_api_football_terminal_penalty_result_updates_real_memory_before_flag(self):
        from modules import api_provider, football_memory, ft_handler, match_state

        match = shootout_match()
        match["fixture"]["status"] = {
            "short": "PEN",
            "long": "Match Finished",
            "elapsed": 120,
        }
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 123})()

        async def run(memory_dir: Path, memory_path: Path):
            with (
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(ft_handler, "post_new_general_message", AsyncMock(return_value=fake_message)),
                patch.object(football_memory, "MEMORY_PATH", memory_path),
            ):
                await ft_handler.process_terminal_fixture(fake_bot, match, memory_dir=memory_dir)
                state = match_state.get_fixture_state("shootout-1", memory_dir=memory_dir)
                return state

        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            memory_path = memory_dir / "football_memory.json"
            state = asyncio.run(run(memory_dir, memory_path))
            memory = json.loads(memory_path.read_text(encoding="utf-8"))

        self.assertTrue(state["memory_updated"])
        self.assertTrue(state["ft_announced"])
        self.assertEqual(memory["matches"]["shootout-1"]["status"], "PEN_DONE")
        self.assertEqual(memory["teams"]["100"]["stats"]["draws"], 1)

    def test_memory_skip_keeps_memory_updated_false_for_retry(self):
        from modules import api_provider, ft_handler, match_state

        match = shootout_match()
        match["fixture"]["status"] = {
            "short": "PEN",
            "long": "Match Finished",
            "elapsed": 120,
        }
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 123})()

        async def run(memory_dir: Path):
            with (
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(ft_handler, "post_new_general_message", AsyncMock(return_value=fake_message)),
                patch.object(
                    ft_handler,
                    "update_match_in_memory",
                    AsyncMock(return_value={"updated": False, "reason": "missing_required_data"}),
                ),
            ):
                await ft_handler.process_terminal_fixture(fake_bot, match, memory_dir=memory_dir)
                return match_state.get_fixture_state("shootout-1", memory_dir=memory_dir)

        with tempfile.TemporaryDirectory() as tmp:
            state = asyncio.run(run(Path(tmp)))

        self.assertTrue(state["ft_announced"])
        self.assertFalse(state["memory_updated"])

    def test_ft_pending_missing_events_posts_without_warning_and_defers_memory(self):
        from modules import api_provider, ft_handler, match_state

        match = espn_match(fixture_id="pending-ft-events")
        match["fixture"]["status"] = {"short": "FT", "long": "Full Time", "elapsed": 90}
        match["goals"] = {"home": 2, "away": 0}
        match["events"] = []
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 321})()

        async def run(memory_dir: Path):
            with (
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(api_provider, "event_completeness_status", return_value={
                    "status": api_provider.EVENTS_PENDING_ENRICHMENT,
                    "missing_goals": 2,
                    "score_key": "pending-ft-events:2:0",
                }),
                patch.object(ft_handler, "post_new_general_message", AsyncMock(return_value=fake_message)) as post_msg,
                patch.object(ft_handler, "update_match_in_memory", AsyncMock()) as update_memory,
            ):
                await ft_handler.process_terminal_fixture(fake_bot, match, memory_dir=memory_dir)
                state = match_state.get_fixture_state("pending-ft-events", memory_dir=memory_dir)
                return state, post_msg, update_memory

        with tempfile.TemporaryDirectory() as tmp:
            state, post_msg, update_memory = asyncio.run(run(Path(tmp)))

        self.assertTrue(state["ft_announced"])
        self.assertFalse(state["memory_updated"])
        self.assertEqual(state["ft_message_id"], 321)
        self.assertNotIn("missing from event data", post_msg.await_args.kwargs["content"])
        update_memory.assert_not_awaited()

    def test_ft_existing_message_is_edited_when_enrichment_later_completes(self):
        from modules import api_provider, ft_handler, match_state

        pending = espn_match(fixture_id="editable-ft-events")
        pending["fixture"]["status"] = {"short": "FT", "long": "Full Time", "elapsed": 90}
        pending["goals"] = {"home": 1, "away": 0}
        pending["events"] = []
        complete = {**pending, "events": [
            {
                "type": "Goal",
                "detail": "Normal Goal",
                "player": {"name": "Late Scorer"},
                "team": {"id": "50", "name": "Parma"},
                "time": {"elapsed": 88},
            }
        ]}
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 654})()

        async def run(memory_dir: Path):
            with (
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(side_effect=[pending, complete])),
                patch.object(api_provider, "event_completeness_status", side_effect=[
                    {
                        "status": api_provider.EVENTS_PENDING_ENRICHMENT,
                        "missing_goals": 1,
                        "score_key": "editable-ft-events:1:0",
                    },
                    {
                        "status": api_provider.EVENTS_COMPLETE,
                        "missing_goals": 0,
                        "score_key": "editable-ft-events:1:0",
                    },
                ]),
                patch.object(ft_handler, "post_new_general_message", AsyncMock(return_value=fake_message)) as post_msg,
                patch.object(ft_handler, "edit_general_message", AsyncMock(return_value=fake_message)) as edit_msg,
                patch.object(
                    ft_handler,
                    "update_match_in_memory",
                    AsyncMock(return_value={"updated": True, "reason": "updated"}),
                ) as update_memory,
            ):
                await ft_handler.process_terminal_fixture(fake_bot, pending, memory_dir=memory_dir)
                await ft_handler.process_terminal_fixture(fake_bot, pending, memory_dir=memory_dir)
                state = match_state.get_fixture_state("editable-ft-events", memory_dir=memory_dir)
                return state, post_msg, edit_msg, update_memory

        with tempfile.TemporaryDirectory() as tmp:
            state, post_msg, edit_msg, update_memory = asyncio.run(run(Path(tmp)))

        post_msg.assert_awaited_once()
        edit_msg.assert_awaited_once()
        self.assertIn("Late Scorer", edit_msg.await_args.kwargs["content"])
        self.assertTrue(state["ft_announced"])
        self.assertTrue(state["memory_updated"])
        self.assertEqual(update_memory.await_count, 1)

    def test_ft_exhausted_missing_events_edits_stored_message_with_warning(self):
        from modules import api_provider, ft_handler, match_state

        match = espn_match(fixture_id="exhausted-ft-events")
        match["fixture"]["status"] = {"short": "FT", "long": "Full Time", "elapsed": 90}
        match["goals"] = {"home": 2, "away": 0}
        match["events"] = []
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 987})()

        async def run(memory_dir: Path):
            with (
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(api_provider, "event_completeness_status", side_effect=[
                    {
                        "status": api_provider.EVENTS_PENDING_ENRICHMENT,
                        "missing_goals": 2,
                        "score_key": "exhausted-ft-events:2:0",
                    },
                    {
                        "status": api_provider.EVENTS_EXHAUSTED_MISSING,
                        "missing_goals": 2,
                        "score_key": "exhausted-ft-events:2:0",
                    },
                ]),
                patch.object(ft_handler, "post_new_general_message", AsyncMock(return_value=fake_message)) as post_msg,
                patch.object(ft_handler, "edit_general_message", AsyncMock(return_value=fake_message)) as edit_msg,
                patch.object(
                    ft_handler,
                    "update_match_in_memory",
                    AsyncMock(return_value={"updated": True, "reason": "updated"}),
                ) as update_memory,
            ):
                await ft_handler.process_terminal_fixture(fake_bot, match, memory_dir=memory_dir)
                await ft_handler.process_terminal_fixture(fake_bot, match, memory_dir=memory_dir)
                state = match_state.get_fixture_state("exhausted-ft-events", memory_dir=memory_dir)
                return state, post_msg, edit_msg, update_memory

        with tempfile.TemporaryDirectory() as tmp:
            state, post_msg, edit_msg, update_memory = asyncio.run(run(Path(tmp)))

        post_msg.assert_awaited_once()
        edit_msg.assert_awaited_once()
        self.assertIn("missing from event data", edit_msg.await_args.kwargs["content"])
        self.assertTrue(state["ft_announced"])
        self.assertTrue(state["memory_updated"])
        self.assertEqual(update_memory.await_count, 1)

    def test_fully_resolved_exhausted_ft_message_can_be_edited_after_late_enrichment(self):
        from modules import api_provider, ft_handler, match_state

        base = espn_match(fixture_id="late-complete-ft")
        base["fixture"]["status"] = {"short": "FT", "long": "Full Time", "elapsed": 90}
        base["goals"] = {"home": 1, "away": 0}
        base["events"] = []
        complete = {**base, "events": [
            {
                "type": "Goal",
                "detail": "Normal Goal",
                "player": {"name": "Late Complete Scorer"},
                "team": {"id": "50", "name": "Parma"},
                "time": {"elapsed": 88},
            }
        ]}
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 4321})()

        async def run(memory_dir: Path):
            match_state.save_match_state(
                {
                    "version": 1,
                    "fixtures": {
                        "late-complete-ft": {
                            "fixture_id": "late-complete-ft",
                            "ft_announced": True,
                            "memory_updated": True,
                            "ft_message_id": 4321,
                            "ft_message_content": (
                                "FT: Parma 1 - 0 Sassuolo "
                                "⚠️ 1 goal(s) missing from event data"
                            ),
                            "event_completeness_key": "late-complete-ft:1:0",
                            "event_completeness_status": api_provider.EVENTS_EXHAUSTED_MISSING,
                            "event_missing_goal_count": 1,
                        },
                    },
                },
                memory_dir=memory_dir,
            )
            with (
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=complete)),
                patch.object(api_provider, "event_completeness_status", return_value={
                    "status": api_provider.EVENTS_COMPLETE,
                    "missing_goals": 0,
                    "score_key": "late-complete-ft:1:0",
                }),
                patch.object(ft_handler, "post_new_general_message", AsyncMock()) as post_msg,
                patch.object(ft_handler, "edit_general_message", AsyncMock(return_value=fake_message)) as edit_msg,
                patch.object(ft_handler, "update_match_in_memory", AsyncMock()) as update_memory,
            ):
                await ft_handler.process_terminal_fixture(fake_bot, base, memory_dir=memory_dir)
                state = match_state.get_fixture_state("late-complete-ft", memory_dir=memory_dir)
                return state, post_msg, edit_msg, update_memory

        with tempfile.TemporaryDirectory() as tmp:
            state, post_msg, edit_msg, update_memory = asyncio.run(run(Path(tmp)))

        post_msg.assert_not_awaited()
        update_memory.assert_not_awaited()
        edit_msg.assert_awaited_once()
        edited_content = edit_msg.await_args.kwargs["content"]
        self.assertIn("Late Complete Scorer", edited_content)
        self.assertNotIn("missing from event data", edited_content)
        self.assertTrue(state["ft_announced"])
        self.assertTrue(state["memory_updated"])
        self.assertEqual(state["event_completeness_status"], api_provider.EVENTS_COMPLETE)

    def test_fetch_and_post_ft_skips_fully_resolved_terminal_fixture(self):
        from modules import api_provider, ft_handler, match_state

        match = espn_match(fixture_id="resolved-ft")
        match["fixture"]["date"] = "2026-06-03T20:00:00Z"
        match["fixture"]["status"] = {"short": "FT", "long": "Full Time", "elapsed": 90}
        fake_bot = type("FakeBot", (), {"http_session": None})()
        now_utc = datetime(2026, 6, 3, 23, 0, tzinfo=timezone.utc)

        async def run(memory_dir: Path):
            match_state.save_match_state(
                {
                    "version": 1,
                    "migrated_from_ft_state": True,
                    "fixtures": {
                        "resolved-ft": {
                            "fixture_id": "resolved-ft",
                            "kickoff_utc": "2026-06-03T20:00:00+00:00",
                            "expected_ft_utc": "2026-06-03T21:52:00+00:00",
                            "last_status": "FT",
                            "terminal_utc": "2026-06-03T22:00:00+00:00",
                            "ft_announced": True,
                            "memory_updated": True,
                        },
                    },
                },
                memory_dir=memory_dir,
            )
            with (
                patch.object(match_state, "BOT_MEMORY_DIR", memory_dir),
                patch.object(ft_handler, "utc_now", return_value=now_utc),
                patch.object(api_provider, "fetch_relevant_football", AsyncMock(return_value=[match])),
                patch.object(ft_handler, "process_terminal_fixture", AsyncMock()) as process_terminal,
            ):
                await ft_handler.fetch_and_post_ft(fake_bot)
                return process_terminal

        with tempfile.TemporaryDirectory() as tmp:
            process_terminal = asyncio.run(run(Path(tmp)))

        process_terminal.assert_not_awaited()

    def test_fetch_and_post_ft_allows_fully_resolved_exhausted_fixture_for_message_repair(self):
        from modules import api_provider, ft_handler, match_state

        match = espn_match(fixture_id="resolved-exhausted-ft")
        match["fixture"]["date"] = "2026-06-03T20:00:00Z"
        match["fixture"]["status"] = {"short": "FT", "long": "Full Time", "elapsed": 90}
        fake_bot = type("FakeBot", (), {"http_session": None})()
        now_utc = datetime(2026, 6, 3, 23, 0, tzinfo=timezone.utc)

        async def run(memory_dir: Path):
            match_state.save_match_state(
                {
                    "version": 1,
                    "fixtures": {
                        "resolved-exhausted-ft": {
                            "fixture_id": "resolved-exhausted-ft",
                            "kickoff_utc": "2026-06-03T20:00:00+00:00",
                            "expected_ft_utc": "2026-06-03T21:52:00+00:00",
                            "last_status": "FT",
                            "terminal_utc": "2026-06-03T22:00:00+00:00",
                            "ft_announced": True,
                            "memory_updated": True,
                            "ft_message_id": 765,
                            "ft_message_content": (
                                "FT: Parma 1 - 0 Sassuolo "
                                "⚠️ 1 goal(s) missing from event data"
                            ),
                            "event_completeness_key": "resolved-exhausted-ft:1:0",
                            "event_completeness_status": api_provider.EVENTS_EXHAUSTED_MISSING,
                            "event_missing_goal_count": 1,
                        },
                    },
                },
                memory_dir=memory_dir,
            )
            with (
                patch.object(match_state, "BOT_MEMORY_DIR", memory_dir),
                patch.object(ft_handler, "utc_now", return_value=now_utc),
                patch.object(api_provider, "fetch_relevant_football", AsyncMock(return_value=[match])),
                patch.object(ft_handler, "process_terminal_fixture", AsyncMock()) as process_terminal,
            ):
                await ft_handler.fetch_and_post_ft(fake_bot)
                return process_terminal

        with tempfile.TemporaryDirectory() as tmp:
            process_terminal = asyncio.run(run(Path(tmp)))

        process_terminal.assert_awaited_once()

    def test_process_terminal_fixture_skips_mapped_fallback_when_canonical_is_resolved(self):
        from modules import api_provider, ft_handler, match_state

        api_match = espn_match(fixture_id="1489379", league_id=1)
        api_match["canonical_fixture_id"] = "760429"
        api_match["provider"] = "api_football"
        api_match["provider_fixture_id"] = "1489379"
        api_match["fixture"]["status"] = {"short": "FT", "long": "Match Finished"}
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run(memory_dir: Path):
            match_state.save_match_state(
                {
                    "version": 1,
                    "fixtures": {
                        "760429": {
                            "fixture_id": "760429",
                            "provider_ids": {"espn": "760429", "api_football": "1489379"},
                            "ft_announced": True,
                            "memory_updated": True,
                            "last_status": "FT",
                        },
                    },
                },
                memory_dir=memory_dir,
            )
            with (
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=api_match)) as enrich,
                patch.object(ft_handler, "post_new_general_message", AsyncMock()) as post_msg,
                patch.object(ft_handler, "update_match_in_memory", AsyncMock()) as update_memory,
            ):
                await ft_handler.process_terminal_fixture(fake_bot, api_match, memory_dir=memory_dir)
                return enrich, post_msg, update_memory

        with tempfile.TemporaryDirectory() as tmp:
            enrich, post_msg, update_memory = asyncio.run(run(Path(tmp)))

        enrich.assert_not_awaited()
        post_msg.assert_not_awaited()
        update_memory.assert_not_awaited()

    def test_fetch_and_post_ft_uses_provider_alias_for_due_direct_fetch(self):
        from modules import api_provider, ft_handler, match_state

        fake_bot = type("FakeBot", (), {"http_session": None})()
        now_utc = datetime(2026, 6, 16, 0, 0, tzinfo=timezone.utc)

        async def run(memory_dir: Path):
            match_state.save_match_state(
                {
                    "version": 1,
                    "fixtures": {
                        "760429": {
                            "fixture_id": "760429",
                            "provider_ids": {"espn": "760429", "api_football": "1489379"},
                            "kickoff_utc": "2026-06-15T22:00:00+00:00",
                            "expected_ft_utc": "2026-06-15T23:52:00+00:00",
                            "last_status": "2H",
                            "ft_announced": False,
                            "memory_updated": False,
                        },
                    },
                },
                memory_dir=memory_dir,
            )
            payload = {"response": []}
            with (
                patch.object(match_state, "BOT_MEMORY_DIR", memory_dir),
                patch.object(ft_handler, "utc_now", return_value=now_utc),
                patch.object(api_provider, "fetch_relevant_football", AsyncMock(return_value=[])),
                patch.object(api_provider, "fetch_fixture", AsyncMock(return_value=payload)) as fetch_fixture,
            ):
                await ft_handler.fetch_and_post_ft(fake_bot)
                return fetch_fixture

        with tempfile.TemporaryDirectory() as tmp:
            fetch_fixture = asyncio.run(run(Path(tmp)))

        fetch_fixture.assert_awaited_once_with(None, "1489379")

    def test_unmapped_api_football_terminal_fixture_does_not_post_or_update_memory(self):
        from modules import api_provider, ft_handler

        api_match = espn_match(fixture_id="1539002", league_id=1)
        api_match["provider"] = "api_football"
        api_match["provider_fixture_id"] = "1539002"
        api_match["fixture"]["status"] = {"short": "FT", "long": "Match Finished"}
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run(memory_dir: Path):
            with (
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=api_match)),
                patch.object(ft_handler, "post_new_general_message", AsyncMock()) as post_msg,
                patch.object(ft_handler, "update_match_in_memory", AsyncMock()) as update_memory,
            ):
                await ft_handler.process_terminal_fixture(fake_bot, api_match, memory_dir=memory_dir)
                return post_msg, update_memory

        with tempfile.TemporaryDirectory() as tmp:
            post_msg, update_memory = asyncio.run(run(Path(tmp)))

        post_msg.assert_not_awaited()
        update_memory.assert_not_awaited()

    def test_incomplete_mapped_api_football_terminal_fixture_posts_without_warning_and_waits_for_memory(self):
        from modules import api_provider, ft_handler, match_state

        api_match = espn_match(fixture_id="1489379", league_id=1)
        api_match["canonical_fixture_id"] = "760429"
        api_match["provider"] = "api_football"
        api_match["provider_fixture_id"] = "1489379"
        api_match["provider_ids"] = {"espn": "760429", "api_football": "1489379"}
        api_match["fixture"]["status"] = {"short": "FT", "long": "Match Finished"}
        api_match["goals"] = {"home": 1, "away": 1}
        api_match["events"] = []
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 444})()

        async def run(memory_dir: Path):
            match_state.save_match_state(
                {
                    "version": 1,
                    "fixtures": {
                        "760429": {
                            "fixture_id": "760429",
                            "provider_ids": {"espn": "760429", "api_football": "1489379"},
                            "ft_announced": False,
                            "memory_updated": False,
                            "last_status": "2H",
                        },
                    },
                },
                memory_dir=memory_dir,
            )
            with (
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=api_match)),
                patch.object(ft_handler, "post_new_general_message", AsyncMock(return_value=fake_message)) as post_msg,
                patch.object(ft_handler, "update_match_in_memory", AsyncMock()) as update_memory,
            ):
                await ft_handler.process_terminal_fixture(fake_bot, api_match, memory_dir=memory_dir)
                state = match_state.get_fixture_state("760429", memory_dir=memory_dir)
                return state, post_msg, update_memory

        with tempfile.TemporaryDirectory() as tmp:
            state, post_msg, update_memory = asyncio.run(run(Path(tmp)))

        self.assertTrue(state["ft_announced"])
        self.assertFalse(state["memory_updated"])
        post_msg.assert_awaited_once()
        self.assertNotIn("missing from event data", post_msg.await_args.kwargs["content"])
        update_memory.assert_not_awaited()

    def test_live_penalty_update_includes_penalty_score(self):
        from modules import live_loop
        from modules import api_provider

        match = shootout_match()
        match["fixture"]["status"] = {"short": "PEN", "elapsed": 120}
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 456})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            with (
                patch.object(api_provider, "fetch_live", AsyncMock(return_value=[match])),
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(live_loop, "is_tracked_for_ft", return_value=True),
                patch.object(live_loop.match_state, "get_fixture_state", return_value={}),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop.match_state, "update_live_message_id"),
                patch.object(live_loop, "prune_live_state", return_value=[]),
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=fake_message)) as upsert_live,
            ):
                await live_loop.run_live_loop(fake_bot)
                return upsert_live

        upsert_live = asyncio.run(run())
        content = upsert_live.await_args.kwargs["content"]

        self.assertIn("Football LIVE [PEN]: Home 1 - 1 Away", content)
        self.assertIn("Penalties: Home 4 - 3 Away", content)
        self.assertIn("Pens scored: Home: H1, H2, H3, H4; Away: A1, A2, A3", content)
        self.assertNotIn("120' - H1", content)

    def test_live_penalty_status_change_is_not_suppressed_by_score_event_dedupe(self):
        from modules import live_loop
        from modules import api_provider

        match = shootout_match()
        match["fixture"]["status"] = {"short": "PEN", "elapsed": 120}
        match["events"] = match["events"][:2]
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 789})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            live_loop.live_state_keys["shootout-1"] = "shootout-1_1-1_2"
            with (
                patch.object(api_provider, "fetch_live", AsyncMock(return_value=[match])),
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(live_loop, "is_tracked_for_ft", return_value=True),
                patch.object(live_loop.match_state, "get_fixture_state", return_value={}),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop.match_state, "update_live_message_id"),
                patch.object(live_loop, "prune_live_state", return_value=[]),
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=fake_message)) as upsert_live,
            ):
                await live_loop.run_live_loop(fake_bot)
                return upsert_live

        upsert_live = asyncio.run(run())

        self.assertTrue(upsert_live.await_count)
        self.assertIn("Football LIVE [PEN]: Home 1 - 1 Away", upsert_live.await_args.kwargs["content"])

    def test_live_score_and_event_changes_use_edit_window_upsert(self):
        from modules import live_loop
        from modules import api_provider

        base = espn_match(fixture_id="live-feed-1")
        base["fixture"]["status"] = {"short": "1H", "elapsed": 9}
        base["goals"] = {"home": 0, "away": 0}

        goal = espn_match(fixture_id="live-feed-1")
        goal["fixture"]["status"] = {"short": "1H", "elapsed": 10}
        goal["goals"] = {"home": 1, "away": 0}
        goal["events"] = [
            {
                "type": "Goal",
                "detail": "Normal Goal",
                "player": {"name": "Scorer"},
                "team": {"id": "50", "name": "Parma"},
                "time": {"elapsed": 9},
            }
        ]

        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 100})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            with (
                patch.object(api_provider, "fetch_live", AsyncMock(side_effect=[[base], [goal]])),
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(side_effect=[base, goal])),
                patch.object(live_loop, "is_tracked_for_ft", return_value=True),
                patch.object(live_loop.match_state, "get_fixture_state", return_value={}),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop.match_state, "update_live_message_id") as update_live_id,
                patch.object(live_loop, "prune_live_state", return_value=[]),
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=fake_message)) as upsert_live,
            ):
                await live_loop.run_live_loop(fake_bot)
                await live_loop.run_live_loop(fake_bot)
                return upsert_live, update_live_id

        upsert_live, update_live_id = asyncio.run(run())

        self.assertEqual(upsert_live.await_count, 2)
        self.assertEqual(
            [call.kwargs["content"] for call in upsert_live.await_args_list],
            [
                "⚽ Football LIVE: Parma 0 - 0 Sassuolo",
                "⚽ Football LIVE: Parma 1 - 0 Sassuolo (9' - Scorer (H))",
            ],
        )
        self.assertEqual([call.kwargs["message_id"] for call in upsert_live.await_args_list], [None, 100])
        self.assertEqual([call.args[1] for call in update_live_id.call_args_list], [100, 100])

    def test_live_score_rollback_drops_surplus_voided_goal_event_after_guard_accepts(self):
        from modules import live_loop
        from modules import api_provider

        base = espn_match(fixture_id="voided-live-goal")
        base["teams"]["home"] = {"id": "50", "name": "Belgium"}
        base["teams"]["away"] = {"id": "51", "name": "Iran"}
        base["fixture"]["status"] = {"short": "1H", "elapsed": 24}
        base["goals"] = {"home": 0, "away": 0}
        base["events"] = []

        goal = espn_match(fixture_id="voided-live-goal")
        goal["teams"] = base["teams"]
        goal["fixture"]["status"] = {"short": "1H", "elapsed": 26}
        goal["goals"] = {"home": 0, "away": 1}
        goal["events"] = [
            {
                "type": "Goal",
                "detail": "Normal Goal",
                "player": {"name": "Mehdi Taremi"},
                "team": {"id": "51", "name": "Iran"},
                "time": {"elapsed": 24},
            }
        ]

        rollback = espn_match(fixture_id="voided-live-goal")
        rollback["teams"] = base["teams"]
        rollback["fixture"]["status"] = {"short": "1H", "elapsed": 30}
        rollback["goals"] = {"home": 0, "away": 0}
        rollback["events"] = list(goal["events"])

        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 100})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            with (
                patch.object(api_provider, "fetch_live", AsyncMock(side_effect=[
                    [base],
                    [goal],
                    [rollback],
                    [rollback],
                    [rollback],
                ])),
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(side_effect=[
                    base,
                    goal,
                    rollback,
                    rollback,
                    rollback,
                ])),
                patch.object(live_loop, "is_tracked_for_ft", return_value=True),
                patch.object(live_loop.match_state, "get_fixture_state", return_value={}),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop.match_state, "update_live_message_id"),
                patch.object(live_loop, "prune_live_state", return_value=[]),
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=fake_message)) as upsert_live,
            ):
                for _ in range(5):
                    await live_loop.run_live_loop(fake_bot)
                return upsert_live

        upsert_live = asyncio.run(run())
        sent_contents = [call.kwargs["content"] for call in upsert_live.await_args_list]

        self.assertTrue(
            any("Belgium 0 - 1 Iran" in content and "Mehdi Taremi" in content for content in sent_contents)
        )
        self.assertIn("Belgium 0 - 0 Iran", sent_contents[-1])
        self.assertNotIn("Mehdi Taremi", sent_contents[-1])

    def test_live_pending_missing_events_do_not_render_warning(self):
        from modules import live_loop
        from modules import api_provider

        match = espn_match(fixture_id="pending-live-events")
        match["fixture"]["status"] = {"short": "1H", "elapsed": 20}
        match["goals"] = {"home": 2, "away": 0}
        match["events"] = []
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 100})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            with (
                patch.object(api_provider, "fetch_live", AsyncMock(return_value=[match])),
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(api_provider, "event_completeness_status", return_value={
                    "status": api_provider.EVENTS_PENDING_ENRICHMENT,
                    "missing_goals": 2,
                    "score_key": "pending-live-events:2:0",
                }),
                patch.object(live_loop, "is_tracked_for_ft", return_value=True),
                patch.object(live_loop.match_state, "get_fixture_state", return_value={}),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop.match_state, "update_live_message_id"),
                patch.object(live_loop.match_state, "update_event_completeness"),
                patch.object(live_loop, "prune_live_state", return_value=[]),
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=fake_message)) as upsert_live,
            ):
                await live_loop.run_live_loop(fake_bot)
                return upsert_live

        upsert_live = asyncio.run(run())

        content = upsert_live.await_args.kwargs["content"]
        self.assertIn("Parma 2 - 0 Sassuolo", content)
        self.assertNotIn("missing from event data", content)

    def test_live_exhausted_missing_events_render_warning_even_without_event_count_change(self):
        from modules import live_loop
        from modules import api_provider

        match = espn_match(fixture_id="exhausted-live-events")
        match["fixture"]["status"] = {"short": "1H", "elapsed": 20}
        match["goals"] = {"home": 2, "away": 0}
        match["events"] = []
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 100})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            statuses = [
                {
                    "status": api_provider.EVENTS_PENDING_ENRICHMENT,
                    "missing_goals": 2,
                    "score_key": "exhausted-live-events:2:0",
                },
                {
                    "status": api_provider.EVENTS_EXHAUSTED_MISSING,
                    "missing_goals": 2,
                    "score_key": "exhausted-live-events:2:0",
                },
            ]
            with (
                patch.object(api_provider, "fetch_live", AsyncMock(side_effect=[[match], [match]])),
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(api_provider, "event_completeness_status", side_effect=statuses),
                patch.object(live_loop, "is_tracked_for_ft", return_value=True),
                patch.object(live_loop.match_state, "get_fixture_state", return_value={}),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop.match_state, "update_live_message_id"),
                patch.object(live_loop.match_state, "update_event_completeness"),
                patch.object(live_loop, "prune_live_state", return_value=[]),
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=fake_message)) as upsert_live,
            ):
                await live_loop.run_live_loop(fake_bot)
                await live_loop.run_live_loop(fake_bot)
                return upsert_live

        upsert_live = asyncio.run(run())

        self.assertEqual(upsert_live.await_count, 2)
        self.assertNotIn("missing from event data", upsert_live.await_args_list[0].kwargs["content"])
        self.assertIn("missing from event data", upsert_live.await_args_list[1].kwargs["content"])

    def test_live_loop_uses_persisted_live_message_id_after_restart(self):
        from modules import live_loop
        from modules import api_provider

        match = espn_match(fixture_id="persisted-live-id")
        match["fixture"]["status"] = {"short": "1H", "elapsed": 9}
        match["goals"] = {"home": 1, "away": 0}
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 777})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            with (
                patch.object(api_provider, "fetch_live", AsyncMock(return_value=[match])),
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(live_loop, "is_tracked_for_ft", return_value=True),
                patch.object(live_loop.match_state, "get_fixture_state", return_value={"live_message_id": 777}),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop.match_state, "update_live_message_id") as update_live_id,
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=fake_message)) as upsert_live,
                patch.object(live_loop, "prune_live_state", return_value=[]),
            ):
                await live_loop.run_live_loop(fake_bot)
                return upsert_live, update_live_id

        upsert_live, update_live_id = asyncio.run(run())

        upsert_live.assert_awaited_once()
        self.assertEqual(upsert_live.await_args.kwargs["message_id"], 777)
        update_live_id.assert_called_once_with("persisted-live-id", 777)

    def test_live_loop_uses_canonical_fixture_id_for_mapped_fallback_live_message(self):
        from modules import live_loop
        from modules import api_provider

        match = espn_match(fixture_id="1489379")
        match["canonical_fixture_id"] = "760429"
        match["provider"] = "api_football"
        match["provider_fixture_id"] = "1489379"
        match["provider_ids"] = {"espn": "760429", "api_football": "1489379"}
        match["fixture"]["status"] = {"short": "1H", "elapsed": 9}
        match["goals"] = {"home": 1, "away": 0}
        fake_bot = type("FakeBot", (), {"http_session": None})()
        replacement_message = type("FakeMessage", (), {"id": 888})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()

            def get_state(fixture_id):
                self.assertEqual(fixture_id, "760429")
                return {"live_message_id": 777}

            with (
                patch.object(api_provider, "fetch_live", AsyncMock(return_value=[match])),
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(live_loop, "is_tracked_for_ft", return_value=True),
                patch.object(live_loop.match_state, "get_fixture_state", side_effect=get_state),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop.match_state, "update_live_message_id") as update_live_id,
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=replacement_message)) as upsert_live,
                patch.object(live_loop, "prune_live_state", return_value=[]),
            ):
                await live_loop.run_live_loop(fake_bot)
                return upsert_live, update_live_id, dict(live_loop.live_message_ids)

        upsert_live, update_live_id, live_ids = asyncio.run(run())

        self.assertEqual(upsert_live.await_args.kwargs["message_id"], 777)
        update_live_id.assert_called_once_with("760429", 888)
        self.assertEqual(live_ids["760429"], 888)
        self.assertNotIn("1489379", live_ids)

    def test_live_loop_persists_replacement_message_id_from_stale_upsert(self):
        from modules import live_loop
        from modules import api_provider

        match = espn_match(fixture_id="stale-live-id")
        match["fixture"]["status"] = {"short": "1H", "elapsed": 9}
        match["goals"] = {"home": 1, "away": 0}
        fake_bot = type("FakeBot", (), {"http_session": None})()
        replacement_message = type("FakeMessage", (), {"id": 888})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            with (
                patch.object(api_provider, "fetch_live", AsyncMock(return_value=[match])),
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(live_loop, "is_tracked_for_ft", return_value=True),
                patch.object(live_loop.match_state, "get_fixture_state", return_value={"live_message_id": 777}),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop.match_state, "update_live_message_id") as update_live_id,
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=replacement_message)) as upsert_live,
                patch.object(live_loop, "prune_live_state", return_value=[]),
            ):
                await live_loop.run_live_loop(fake_bot)
                return upsert_live, update_live_id, dict(live_loop.live_message_ids)

        upsert_live, update_live_id, live_ids = asyncio.run(run())

        self.assertEqual(upsert_live.await_args.kwargs["message_id"], 777)
        update_live_id.assert_called_once_with("stale-live-id", 888)
        self.assertEqual(live_ids["stale-live-id"], 888)

    def test_live_loop_cleans_missing_volatile_state_when_no_live_matches_returned(self):
        from modules import live_loop
        from modules import api_provider

        now = datetime(2026, 6, 12, 16, 0, tzinfo=timezone.utc)
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            live_loop.live_state_keys["gone-live"] = "gone-live_2H_1-0_1"
            live_loop.live_message_ids["gone-live"] = 123
            live_loop._missing_since["gone-live"] = now - timedelta(seconds=live_loop._MISSING_GRACE_SEC + 1)
            live_loop._last_observed["gone-live"] = {"home": 1, "away": 0, "elapsed": 80}
            live_loop._regression_hold["gone-live"] = {"state_key": "gone-live_2H_0-0_0", "ticks": 1}
            live_loop._last_sent_content["gone-live"] = ("content", now)

            with (
                patch.object(api_provider, "fetch_live", AsyncMock(return_value=[])),
                patch.object(live_loop, "bot_now", return_value=now),
                patch.object(live_loop, "utc_now", return_value=now),
                patch.object(live_loop, "prune_live_state", return_value=[]) as prune,
            ):
                await live_loop.run_live_loop(fake_bot)
                return prune

        prune = asyncio.run(run())

        self.assertNotIn("gone-live", live_loop.live_state_keys)
        self.assertNotIn("gone-live", live_loop.live_message_ids)
        self.assertNotIn("gone-live", live_loop._missing_since)
        self.assertNotIn("gone-live", live_loop._last_observed)
        self.assertNotIn("gone-live", live_loop._regression_hold)
        self.assertNotIn("gone-live", live_loop._last_sent_content)
        prune.assert_called_once_with(now)

    def test_live_loop_empty_live_result_logs_clear_message_once_per_throttle_window(self):
        from modules import live_loop
        from modules import api_provider

        now = datetime(2026, 6, 12, 16, 0, tzinfo=timezone.utc)
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            if hasattr(live_loop, "_last_empty_live_log_at"):
                live_loop._last_empty_live_log_at = None
            with (
                patch.object(api_provider, "fetch_live", AsyncMock(return_value=[])),
                patch.object(live_loop, "bot_now", return_value=now),
                patch.object(live_loop, "utc_now", return_value=now),
                patch.object(live_loop, "prune_live_state", return_value=[]),
            ):
                with self.assertLogs("modules.live_loop", level="INFO") as first_logs:
                    await live_loop.run_live_loop(fake_bot)
                with self.assertNoLogs("modules.live_loop", level="INFO"):
                    await live_loop.run_live_loop(fake_bot)
                return first_logs.output

        logs = asyncio.run(run())

        self.assertTrue(any("No live football fixtures returned" in line for line in logs))
        self.assertFalse(any("fetch error" in line for line in logs))

    def test_live_loop_empty_live_log_throttle_resets_after_live_match(self):
        from modules import live_loop
        from modules import api_provider

        now = datetime(2026, 6, 12, 16, 0, tzinfo=timezone.utc)
        match = espn_match(fixture_id="live-after-empty")
        match["fixture"]["status"] = {"short": "1H", "elapsed": 12}
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 123})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            if hasattr(live_loop, "_last_empty_live_log_at"):
                live_loop._last_empty_live_log_at = None
            with (
                patch.object(api_provider, "fetch_live", AsyncMock(side_effect=[[], [match], []])),
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(live_loop, "bot_now", return_value=now),
                patch.object(live_loop, "utc_now", return_value=now),
                patch.object(live_loop, "is_tracked_for_ft", return_value=True),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=fake_message)),
                patch.object(live_loop, "prune_live_state", return_value=[]),
            ):
                with self.assertLogs("modules.live_loop", level="INFO") as captured:
                    await live_loop.run_live_loop(fake_bot)
                    await live_loop.run_live_loop(fake_bot)
                    await live_loop.run_live_loop(fake_bot)
                return captured.output

        logs = asyncio.run(run())

        empty_logs = [line for line in logs if "No live football fixtures returned" in line]
        self.assertEqual(len(empty_logs), 2)

    def test_startup_seed_suppresses_duplicate_live_snapshot_post(self):
        from modules import live_loop
        from modules import api_provider

        match = espn_match(fixture_id="seeded-live")
        match["fixture"]["status"] = {"short": "HT", "elapsed": 45}
        match["goals"] = {"home": 1, "away": 0}
        match["events"] = [
            {
                "type": "Goal",
                "detail": "Normal Goal",
                "player": {"name": "Scorer"},
                "team": {"id": "50", "name": "Parma"},
                "time": {"elapsed": 8},
            }
        ]
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            live_loop.seed_already_posted([match])
            with (
                patch.object(api_provider, "fetch_live", AsyncMock(return_value=[match])),
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(live_loop, "is_tracked_for_ft", return_value=True),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop, "upsert_live_message", AsyncMock()) as upsert_live,
                patch.object(live_loop, "prune_live_state", return_value=[]),
            ):
                await live_loop.run_live_loop(fake_bot)
                return upsert_live

        upsert_live = asyncio.run(run())

        upsert_live.assert_not_awaited()

    def test_startup_seed_suppresses_duplicate_when_visible_content_is_unchanged(self):
        from modules import live_loop
        from modules import api_provider

        seeded = espn_match(fixture_id="seeded-status-change")
        seeded["fixture"]["status"] = {"short": "HT", "elapsed": 45}
        seeded["goals"] = {"home": 1, "away": 0}
        seeded["events"] = [
            {
                "type": "Goal",
                "detail": "Normal Goal",
                "player": {"name": "Scorer"},
                "team": {"id": "50", "name": "Parma"},
                "time": {"elapsed": 8},
            }
        ]
        poll = espn_match(fixture_id="seeded-status-change")
        poll["fixture"]["status"] = {"short": "2H", "elapsed": 46}
        poll["goals"] = {"home": 1, "away": 0}
        poll["events"] = list(seeded["events"])
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 400})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            live_loop.seed_already_posted([seeded])
            with (
                patch.object(api_provider, "fetch_live", AsyncMock(return_value=[poll])),
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=poll)),
                patch.object(live_loop, "is_tracked_for_ft", return_value=True),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop.match_state, "update_live_message_id"),
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=fake_message)) as upsert_live,
                patch.object(live_loop, "prune_live_state", return_value=[]),
            ):
                await live_loop.run_live_loop(fake_bot)
                return upsert_live

        upsert_live = asyncio.run(run())

        upsert_live.assert_not_awaited()

    def test_same_rendered_live_content_is_not_reposted_when_state_key_changes(self):
        from modules import live_loop
        from modules import api_provider

        halftime = espn_match(fixture_id="same-content")
        halftime["fixture"]["status"] = {"short": "HT", "elapsed": 45}
        halftime["goals"] = {"home": 1, "away": 0}
        halftime["events"] = [
            {
                "type": "Goal",
                "detail": "Normal Goal",
                "player": {"name": "Scorer"},
                "team": {"id": "50", "name": "Parma"},
                "time": {"elapsed": 8},
            }
        ]
        second_half = espn_match(fixture_id="same-content")
        second_half["fixture"]["status"] = {"short": "2H", "elapsed": 46}
        second_half["goals"] = {"home": 1, "away": 0}
        second_half["events"] = list(halftime["events"])
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 300})()

        async def run():
            live_loop.live_state_keys.clear()
            live_loop.live_message_ids.clear()
            live_loop._missing_since.clear()
            live_loop._last_observed.clear()
            live_loop._regression_hold.clear()
            live_loop._last_sent_content.clear()
            with (
                patch.object(api_provider, "fetch_live", AsyncMock(side_effect=[[halftime], [second_half]])),
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(side_effect=[halftime, second_half])),
                patch.object(live_loop, "is_tracked_for_ft", return_value=True),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop.match_state, "update_live_message_id"),
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=fake_message)) as upsert_live,
                patch.object(live_loop, "prune_live_state", return_value=[]),
            ):
                await live_loop.run_live_loop(fake_bot)
                await live_loop.run_live_loop(fake_bot)
                return upsert_live

        upsert_live = asyncio.run(run())

        self.assertEqual(upsert_live.await_count, 1)
        self.assertEqual(
            upsert_live.await_args.kwargs["content"],
            "⚽ Football LIVE: Parma 1 - 0 Sassuolo (8' - Scorer (H))",
        )


if __name__ == "__main__":
    unittest.main()
