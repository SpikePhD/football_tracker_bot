import asyncio
import os
import tempfile
import unittest
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
                patch.object(live_loop.match_state, "get_fixture_state", return_value={}),
                patch.object(live_loop.match_state, "upsert_fixture_from_match", return_value={}),
                patch.object(live_loop.match_state, "update_live_message_id"),
                patch.object(live_loop, "prune_live_state", return_value=[]),
                patch.object(live_loop, "upsert_live_message", AsyncMock(return_value=fake_message)) as upsert,
            ):
                await live_loop.run_live_loop(fake_bot)
                return upsert

        upsert = asyncio.run(run())

        self.assertTrue(upsert.await_count)
        self.assertIn("Football LIVE [PEN]: Home 1 - 1 Away", upsert.await_args.kwargs["content"])


if __name__ == "__main__":
    unittest.main()
