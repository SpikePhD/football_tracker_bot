import asyncio
import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


def tennis_match(
    *,
    match_id="tennis-1",
    canonical_id=None,
    status="NS",
    start_time="2026-06-12T12:00:00+00:00",
):
    return {
        "match_id": match_id,
        "canonical_id": canonical_id,
        "status": {"short": status, "detail": status},
        "start_time": start_time,
        "player_a": "Player A",
        "player_b": "Player B",
        "event_name": "Tracked Open",
        "tour": "ATP",
        "round": "Round 1",
        "sets": [{"a": 6, "b": 4}] if status in ("LIVE", "FT") else [],
        "winner": "Player A" if status == "FT" else None,
    }


class TennisLoopTests(unittest.TestCase):

    def setUp(self):
        from modules import tennis_loop
        from utils.time_utils import to_bot_tz

        tennis_loop.pre_announced_ids.clear()
        tennis_loop.final_announced_ids.clear()
        tennis_loop.live_message_ids.clear()
        tennis_loop.live_state_keys.clear()
        tennis_loop._state_loaded = False
        tennis_loop._last_reset_date = None
        self.now = to_bot_tz("2026-06-12T10:00:00+00:00")

    def test_ns_match_inside_pre_announce_window_posts_once_and_persists(self):
        from modules import api_provider, tennis_loop

        match = tennis_match(canonical_id="canonical-tennis-1")
        fake_bot = SimpleNamespace(http_session=None)
        fake_msg = SimpleNamespace(id=42)

        async def run():
            with (
                patch.object(tennis_loop, "TENNIS_PRE_ANNOUNCE_HOURS", 4),
                patch.object(tennis_loop, "bot_now", return_value=self.now),
                patch.object(tennis_loop, "load", Mock(return_value=tennis_loop._TENNIS_STATE_DEFAULT.copy())),
                patch.object(tennis_loop, "save", Mock()) as save_state,
                patch.object(api_provider, "fetch_tennis_day", AsyncMock(return_value=[match])),
                patch.object(tennis_loop, "post_new_general_message", AsyncMock(return_value=fake_msg)) as post_msg,
            ):
                await tennis_loop.run_tennis_loop(fake_bot)
                await tennis_loop.run_tennis_loop(fake_bot)
                return post_msg, save_state

        post_msg, save_state = asyncio.run(run())

        post_msg.assert_awaited_once()
        self.assertIn("Tennis Upcoming", post_msg.await_args.kwargs["content"])
        self.assertIn("canonical-tennis-1", tennis_loop.pre_announced_ids)
        self.assertTrue(save_state.called)

    def test_ns_match_outside_pre_announce_window_does_not_post(self):
        from modules import api_provider, tennis_loop

        match = tennis_match(start_time="2026-06-12T17:00:00+00:00")
        fake_bot = SimpleNamespace(http_session=None)

        async def run():
            with (
                patch.object(tennis_loop, "TENNIS_PRE_ANNOUNCE_HOURS", 4),
                patch.object(tennis_loop, "bot_now", return_value=self.now),
                patch.object(tennis_loop, "load", Mock(return_value=tennis_loop._TENNIS_STATE_DEFAULT.copy())),
                patch.object(tennis_loop, "save", Mock()) as save_state,
                patch.object(api_provider, "fetch_tennis_day", AsyncMock(return_value=[match])),
                patch.object(tennis_loop, "post_new_general_message", AsyncMock()) as post_msg,
            ):
                await tennis_loop.run_tennis_loop(fake_bot)
                return post_msg, save_state

        post_msg, save_state = asyncio.run(run())

        post_msg.assert_not_awaited()
        self.assertFalse(tennis_loop.pre_announced_ids)
        self.assertFalse(save_state.called)

    def test_pre_announce_config_larger_than_old_48_hour_window_is_honored(self):
        from modules import api_provider, tennis_loop

        match = tennis_match(start_time="2026-06-14T22:00:00+00:00")
        fake_bot = SimpleNamespace(http_session=None)

        async def run():
            with (
                patch.object(tennis_loop, "TENNIS_PRE_ANNOUNCE_HOURS", 72),
                patch.object(tennis_loop, "bot_now", return_value=self.now),
                patch.object(tennis_loop, "load", Mock(return_value=tennis_loop._TENNIS_STATE_DEFAULT.copy())),
                patch.object(tennis_loop, "save", Mock()),
                patch.object(api_provider, "fetch_tennis_day", AsyncMock(return_value=[match])),
                patch.object(tennis_loop, "post_new_general_message", AsyncMock(return_value=SimpleNamespace(id=77))) as post_msg,
            ):
                await tennis_loop.run_tennis_loop(fake_bot)
                return post_msg

        post_msg = asyncio.run(run())

        post_msg.assert_awaited_once()

    def test_pre_announce_window_is_rolling_across_local_midnight(self):
        from modules import api_provider, tennis_loop
        from utils.time_utils import to_bot_tz

        now = to_bot_tz("2026-06-12T21:30:00+00:00")
        match = tennis_match(start_time="2026-06-12T23:30:00+00:00")
        fake_bot = SimpleNamespace(http_session=None)

        async def run():
            with (
                patch.object(tennis_loop, "TENNIS_PRE_ANNOUNCE_HOURS", 4),
                patch.object(tennis_loop, "bot_now", return_value=now),
                patch.object(tennis_loop, "load", Mock(return_value=tennis_loop._TENNIS_STATE_DEFAULT.copy())),
                patch.object(tennis_loop, "save", Mock()),
                patch.object(api_provider, "fetch_tennis_day", AsyncMock(return_value=[match])),
                patch.object(tennis_loop, "post_new_general_message", AsyncMock(return_value=SimpleNamespace(id=99))) as post_msg,
            ):
                await tennis_loop.run_tennis_loop(fake_bot)
                return post_msg

        post_msg = asyncio.run(run())

        post_msg.assert_awaited_once()

    def test_failed_pre_announce_post_does_not_mark_announced(self):
        from modules import api_provider, tennis_loop

        match = tennis_match()
        fake_bot = SimpleNamespace(http_session=None)

        async def run():
            with (
                patch.object(tennis_loop, "TENNIS_PRE_ANNOUNCE_HOURS", 4),
                patch.object(tennis_loop, "bot_now", return_value=self.now),
                patch.object(tennis_loop, "load", Mock(return_value=tennis_loop._TENNIS_STATE_DEFAULT.copy())),
                patch.object(tennis_loop, "save", Mock()) as save_state,
                patch.object(api_provider, "fetch_tennis_day", AsyncMock(return_value=[match])),
                patch.object(tennis_loop, "post_new_general_message", AsyncMock(return_value=None)) as post_msg,
            ):
                await tennis_loop.run_tennis_loop(fake_bot)
                return post_msg, save_state

        post_msg, save_state = asyncio.run(run())

        post_msg.assert_awaited_once()
        self.assertFalse(tennis_loop.pre_announced_ids)
        self.assertFalse(save_state.called)

    def test_live_and_ft_behavior_stays_unchanged(self):
        from modules import api_provider, tennis_loop

        live = tennis_match(match_id="live-1", status="LIVE")
        ft = tennis_match(match_id="ft-1", status="FT")
        fake_bot = SimpleNamespace(http_session=None)
        fake_live_msg = SimpleNamespace(id=500)
        fake_ft_msg = SimpleNamespace(id=501)

        async def run():
            with (
                patch.object(tennis_loop, "TENNIS_PRE_ANNOUNCE_HOURS", 4),
                patch.object(tennis_loop, "bot_now", return_value=self.now),
                patch.object(tennis_loop, "load", Mock(return_value=tennis_loop._TENNIS_STATE_DEFAULT.copy())),
                patch.object(tennis_loop, "save", Mock()) as save_state,
                patch.object(api_provider, "fetch_tennis_day", AsyncMock(return_value=[live, ft])),
                patch.object(tennis_loop, "upsert_live_message", AsyncMock(return_value=fake_live_msg)) as upsert_live,
                patch.object(tennis_loop, "post_new_general_message", AsyncMock(return_value=fake_ft_msg)) as post_msg,
            ):
                await tennis_loop.run_tennis_loop(fake_bot)
                return upsert_live, post_msg, save_state

        upsert_live, post_msg, save_state = asyncio.run(run())

        upsert_live.assert_awaited_once()
        post_msg.assert_awaited_once()
        self.assertIn("Tennis FT", post_msg.await_args.kwargs["content"])
        self.assertIn("ft-1", tennis_loop.final_announced_ids)
        self.assertTrue(save_state.called)

    def test_should_pre_announce_accepts_injected_now(self):
        from modules import tennis_loop
        from utils.time_utils import to_bot_tz

        now = to_bot_tz("2026-06-12T10:00:00+00:00")
        match = tennis_match(start_time="2026-06-12T12:00:00+00:00")

        with patch.object(tennis_loop, "TENNIS_PRE_ANNOUNCE_HOURS", 4):
            self.assertTrue(tennis_loop.should_pre_announce_tennis(match, now=now))


if __name__ == "__main__":
    unittest.main()
