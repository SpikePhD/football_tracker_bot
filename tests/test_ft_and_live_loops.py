import asyncio
import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.regression_helpers import espn_match, shootout_match

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class FtAndLiveLoopTests(unittest.TestCase):

    def test_ft_state_resets_only_on_date_change_and_persists_marks(self):
        from modules import ft_handler

        saved = []

        def fake_load(filename, default):
            return {"announced_ids": ["old"], "last_reset_date": "2026-05-23"}

        def fake_save(filename, data):
            saved.append(data)

        class FixedNow(datetime):
            @classmethod
            def now(cls):
                return cls(2026, 5, 24, 12, 0, 0)

        def fake_italy_now():
            return FixedNow.now()

        ft_handler._already_announced_ft.clear()
        ft_handler._ft_state_loaded = False
        ft_handler._last_reset_date = None

        with patch.object(ft_handler, "load", fake_load), patch.object(ft_handler, "save", fake_save), patch.object(ft_handler, "italy_now", fake_italy_now):
            ft_handler._ensure_ft_state_current_date()
            self.assertEqual(ft_handler._already_announced_ft, set())
            self.assertEqual(ft_handler._last_reset_date, "2026-05-24")
            ft_handler.mark_ft_announced("new")
            ft_handler.mark_ft_announced("new")

        self.assertEqual(ft_handler._already_announced_ft, {"new"})
        self.assertEqual(saved[-1], {"announced_ids": ["new"], "last_reset_date": "2026-05-24"})

    def test_ft_handler_keeps_penalty_match_tracked_past_expected_ft(self):
        from modules import ft_handler
        from modules import api_provider

        match = espn_match(fixture_id="pen-1")
        match["fixture"]["status"]["short"] = "PEN"
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            ft_handler.tracked_matches.clear()
            ft_handler._already_announced_ft.clear()
            ft_handler._ft_state_loaded = True
            ft_handler._last_reset_date = "2026-05-24"
            ft_handler.tracked_matches["pen-1"] = {
                "exp_ft": datetime(2026, 5, 24, 20, 0, 0),
                "initial_score_at_tracking": {"home": 1, "away": 1},
            }
            with (
                patch.object(ft_handler, "italy_now", return_value=datetime(2026, 5, 24, 20, 45, 0)),
                patch.object(api_provider, "is_espn_healthy", return_value=True),
                patch.object(api_provider, "fetch_day", AsyncMock(return_value=[match])),
                patch.object(api_provider, "fetch_finished_today", AsyncMock(return_value=[])),
                patch.object(ft_handler, "_post_ft_from_data", AsyncMock(return_value=True)) as post_ft,
            ):
                await ft_handler.fetch_and_post_ft(fake_bot)
                return post_ft

        post_ft = asyncio.run(run())
        self.assertIn("pen-1", ft_handler.tracked_matches)
        post_ft.assert_not_awaited()

    def test_ft_handler_keeps_extra_time_match_tracked_past_expected_ft(self):
        from modules import ft_handler
        from modules import api_provider

        match = espn_match(fixture_id="et-1")
        match["fixture"]["status"]["short"] = "ET"
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            ft_handler.tracked_matches.clear()
            ft_handler._already_announced_ft.clear()
            ft_handler._ft_state_loaded = True
            ft_handler._last_reset_date = "2026-05-24"
            ft_handler.tracked_matches["et-1"] = {
                "exp_ft": datetime(2026, 5, 24, 20, 0, 0),
                "initial_score_at_tracking": {"home": 1, "away": 1},
            }
            with (
                patch.object(ft_handler, "italy_now", return_value=datetime(2026, 5, 24, 20, 45, 0)),
                patch.object(api_provider, "is_espn_healthy", return_value=True),
                patch.object(api_provider, "fetch_day", AsyncMock(return_value=[match])),
                patch.object(api_provider, "fetch_finished_today", AsyncMock(return_value=[])),
                patch.object(ft_handler, "_post_ft_from_data", AsyncMock(return_value=True)) as post_ft,
            ):
                await ft_handler.fetch_and_post_ft(fake_bot)
                return post_ft

        post_ft = asyncio.run(run())
        self.assertIn("et-1", ft_handler.tracked_matches)
        post_ft.assert_not_awaited()

    def test_ft_handler_posts_drawn_ft_match(self):
        from modules import ft_handler
        from modules import api_provider

        match = espn_match(fixture_id="draw-ft")
        match["fixture"]["status"]["short"] = "FT"
        match["goals"] = {"home": 1, "away": 1}
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            saved_states = []
            ft_handler.tracked_matches.clear()
            ft_handler._already_announced_ft.clear()
            ft_handler._ft_state_loaded = True
            ft_handler._last_reset_date = "2026-05-24"
            ft_handler.tracked_matches["draw-ft"] = {
                "exp_ft": datetime(2026, 5, 24, 20, 0, 0),
                "initial_score_at_tracking": {"home": 1, "away": 1},
            }
            with (
                patch.object(ft_handler, "italy_now", return_value=datetime(2026, 5, 24, 20, 5, 0)),
                patch.object(ft_handler, "save", lambda _filename, state: saved_states.append(state)),
                patch.object(api_provider, "is_espn_healthy", return_value=True),
                patch.object(api_provider, "fetch_day", AsyncMock(return_value=[match])),
                patch.object(api_provider, "fetch_finished_today", AsyncMock(return_value=[])),
                patch.object(ft_handler, "_post_ft_from_data", AsyncMock(return_value=True)) as post_ft,
            ):
                await ft_handler.fetch_and_post_ft(fake_bot)
                return post_ft, saved_states

        post_ft, saved_states = asyncio.run(run())
        self.assertNotIn("draw-ft", ft_handler.tracked_matches)
        post_ft.assert_awaited_once_with(fake_bot, match)
        self.assertIn("draw-ft", ft_handler._already_announced_ft)
        self.assertEqual(saved_states[-1]["announced_ids"], ["draw-ft"])

    def test_ft_handler_drops_missing_match_after_stale_grace(self):
        from modules import ft_handler
        from modules import api_provider

        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            ft_handler.tracked_matches.clear()
            ft_handler._already_announced_ft.clear()
            ft_handler._ft_state_loaded = True
            ft_handler._last_reset_date = "2026-05-24"
            ft_handler.tracked_matches["missing-1"] = {
                "exp_ft": datetime(2026, 5, 24, 20, 0, 0),
                "initial_score_at_tracking": {"home": 1, "away": 0},
            }
            with (
                patch.object(ft_handler, "italy_now", return_value=datetime(2026, 5, 24, 20, 45, 0)),
                patch.object(api_provider, "is_espn_healthy", return_value=True),
                patch.object(api_provider, "fetch_day", AsyncMock(return_value=[])),
                patch.object(api_provider, "fetch_finished_today", AsyncMock(return_value=[])),
                patch.object(ft_handler, "_post_ft_from_data", AsyncMock(return_value=True)) as post_ft,
            ):
                with self.assertLogs("modules.ft_handler", level="WARNING") as captured:
                    await ft_handler.fetch_and_post_ft(fake_bot)
                return post_ft, captured.output

        post_ft, log_lines = asyncio.run(run())
        self.assertNotIn("missing-1", ft_handler.tracked_matches)
        post_ft.assert_not_awaited()
        self.assertTrue(any("Warning: Match ID missing-1" in line for line in log_lines))
        self.assertFalse(any("â" in line or "�" in line for line in log_lines))

    def test_ft_handler_drops_terminal_non_ft_status_without_posting(self):
        from modules import ft_handler
        from modules import api_provider

        match = espn_match(fixture_id="abd-1")
        match["fixture"]["status"]["short"] = "ABD"
        fake_bot = type("FakeBot", (), {"http_session": None})()

        async def run():
            ft_handler.tracked_matches.clear()
            ft_handler._already_announced_ft.clear()
            ft_handler._ft_state_loaded = True
            ft_handler._last_reset_date = "2026-05-24"
            ft_handler.tracked_matches["abd-1"] = {
                "exp_ft": datetime(2026, 5, 24, 20, 0, 0),
                "initial_score_at_tracking": {"home": 1, "away": 1},
            }
            with (
                patch.object(ft_handler, "italy_now", return_value=datetime(2026, 5, 24, 20, 45, 0)),
                patch.object(api_provider, "is_espn_healthy", return_value=True),
                patch.object(api_provider, "fetch_day", AsyncMock(return_value=[match])),
                patch.object(api_provider, "fetch_finished_today", AsyncMock(return_value=[])),
                patch.object(ft_handler, "_post_ft_from_data", AsyncMock(return_value=True)) as post_ft,
            ):
                await ft_handler.fetch_and_post_ft(fake_bot)
                return post_ft

        post_ft = asyncio.run(run())
        self.assertNotIn("abd-1", ft_handler.tracked_matches)
        post_ft.assert_not_awaited()

    def test_ft_post_after_penalties_includes_winner_score_and_not_shootout_as_goals(self):
        from modules import ft_handler
        from modules import api_provider

        match = shootout_match()
        match["fixture"]["status"]["short"] = "FT"
        fake_bot = type("FakeBot", (), {"http_session": None})()
        fake_message = type("FakeMessage", (), {"id": 123})()

        async def run():
            with (
                patch.object(api_provider, "enrich_fixture_events", AsyncMock(return_value=match)),
                patch.object(ft_handler, "update_match_in_memory", AsyncMock()),
                patch.object(ft_handler, "post_new_general_message", AsyncMock(return_value=fake_message)) as post_msg,
            ):
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
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=fake_message)) as upsert,
            ):
                await live_loop.run_live_loop(fake_bot)
                return upsert

        upsert = asyncio.run(run())
        content = upsert.await_args.kwargs["content"]

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
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=fake_message)) as upsert,
            ):
                await live_loop.run_live_loop(fake_bot)
                return upsert

        upsert = asyncio.run(run())

        self.assertTrue(upsert.await_count)
        self.assertIn("Football LIVE [PEN]: Home 1 - 1 Away", upsert.await_args.kwargs["content"])



if __name__ == "__main__":
    unittest.main()
