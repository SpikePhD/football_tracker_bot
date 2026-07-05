import asyncio
import os
import unittest
from datetime import datetime, timezone, timedelta
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
    sets=None,
    winner=None,
):
    if sets is None:
        sets = [{"a": 6, "b": 4}] if status in ("LIVE", "FT") else []
    if winner is None and status == "FT":
        winner = "Player A"
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
        "sets": sets,
        "winner": winner,
    }


class TennisLoopTests(unittest.TestCase):

    def setUp(self):
        from modules import tennis_loop
        from utils.time_utils import to_bot_tz

        tennis_loop.start_watch_prepared_ids.clear()
        tennis_loop.final_announced_ids.clear()
        tennis_loop.live_message_ids.clear()
        tennis_loop.live_state_keys.clear()
        tennis_loop._state_loaded = False
        tennis_loop._last_reset_date = None
        self.now = to_bot_tz("2026-06-12T10:00:00+00:00")

    def test_ns_match_inside_start_watch_window_prepares_without_posting(self):
        from modules import api_provider, tennis_loop

        match = tennis_match(canonical_id="canonical-tennis-1")
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
                await tennis_loop.run_tennis_loop(fake_bot)
                return post_msg, save_state

        post_msg, save_state = asyncio.run(run())

        post_msg.assert_not_awaited()
        self.assertIn("canonical-tennis-1", tennis_loop.start_watch_prepared_ids)
        self.assertTrue(save_state.called)
        saved_state = save_state.call_args.args[1]
        self.assertIn("start_watch_prepared_ids", saved_state)
        self.assertNotIn("pre_announced_ids", saved_state)

    def test_ns_match_outside_start_watch_window_does_not_post(self):
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
        self.assertFalse(tennis_loop.start_watch_prepared_ids)
        self.assertFalse(save_state.called)

    def test_start_watch_config_larger_than_old_48_hour_window_is_honored(self):
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
                patch.object(tennis_loop, "post_new_general_message", AsyncMock()) as post_msg,
            ):
                await tennis_loop.run_tennis_loop(fake_bot)
                return post_msg

        post_msg = asyncio.run(run())

        post_msg.assert_not_awaited()
        self.assertIn("tennis-1", tennis_loop.start_watch_prepared_ids)

    def test_start_watch_window_is_rolling_across_local_midnight(self):
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
                patch.object(tennis_loop, "post_new_general_message", AsyncMock()) as post_msg,
            ):
                await tennis_loop.run_tennis_loop(fake_bot)
                return post_msg

        post_msg = asyncio.run(run())

        post_msg.assert_not_awaited()
        self.assertIn("tennis-1", tennis_loop.start_watch_prepared_ids)

    def test_silent_start_watch_prepare_persists_without_discord_message(self):
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
                patch.object(tennis_loop, "post_new_general_message", AsyncMock()) as post_msg,
            ):
                await tennis_loop.run_tennis_loop(fake_bot)
                return post_msg, save_state

        post_msg, save_state = asyncio.run(run())

        post_msg.assert_not_awaited()
        self.assertIn("tennis-1", tennis_loop.start_watch_prepared_ids)
        self.assertTrue(save_state.called)

    def test_legacy_pre_announced_state_seeds_start_watch_prepared_ids(self):
        from modules import api_provider, tennis_loop

        match = tennis_match(match_id="legacy-1")
        fake_bot = SimpleNamespace(http_session=None)

        async def run():
            with (
                patch.object(tennis_loop, "TENNIS_PRE_ANNOUNCE_HOURS", 4),
                patch.object(tennis_loop, "bot_now", return_value=self.now),
                patch.object(
                    tennis_loop,
                    "load",
                    Mock(return_value={
                        "pre_announced_ids": ["legacy-1"],
                        "final_announced_ids": [],
                        "last_reset_date": None,
                    }),
                ),
                patch.object(tennis_loop, "save", Mock()) as save_state,
                patch.object(api_provider, "fetch_tennis_day", AsyncMock(return_value=[match])),
                patch.object(tennis_loop, "post_new_general_message", AsyncMock()) as post_msg,
            ):
                await tennis_loop.run_tennis_loop(fake_bot)
                return post_msg, save_state

        post_msg, save_state = asyncio.run(run())

        post_msg.assert_not_awaited()
        self.assertIn("legacy-1", tennis_loop.start_watch_prepared_ids)
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

    def test_incomplete_ft_without_winner_does_not_post_or_mark_final(self):
        from modules import api_provider, tennis_loop

        ft = tennis_match(
            match_id="ft-incomplete",
            status="FT",
            sets=[{"a": 3, "b": 6}, {"a": 4, "b": 4}],
            winner="",
        )
        fake_bot = SimpleNamespace(http_session=None)

        async def run():
            with (
                patch.object(tennis_loop, "bot_now", return_value=self.now),
                patch.object(tennis_loop, "load", Mock(return_value=tennis_loop._TENNIS_STATE_DEFAULT.copy())),
                patch.object(tennis_loop, "save", Mock()) as save_state,
                patch.object(api_provider, "fetch_tennis_day", AsyncMock(return_value=[ft])),
                patch.object(tennis_loop, "post_new_general_message", AsyncMock()) as post_msg,
            ):
                await tennis_loop.run_tennis_loop(fake_bot)
                return post_msg, save_state

        post_msg, save_state = asyncio.run(run())

        post_msg.assert_not_awaited()
        self.assertNotIn("ft-incomplete", tennis_loop.final_announced_ids)
        self.assertFalse(save_state.called)

    def test_complete_ft_with_winner_and_final_sets_still_posts(self):
        from modules import api_provider, tennis_loop

        ft = tennis_match(
            match_id="ft-complete",
            status="FT",
            sets=[{"a": 3, "b": 6}, {"a": 6, "b": 4}, {"a": 6, "b": 3}],
            winner="Player A",
        )
        fake_bot = SimpleNamespace(http_session=None)

        async def run():
            with (
                patch.object(tennis_loop, "bot_now", return_value=self.now),
                patch.object(tennis_loop, "load", Mock(return_value=tennis_loop._TENNIS_STATE_DEFAULT.copy())),
                patch.object(tennis_loop, "save", Mock()),
                patch.object(api_provider, "fetch_tennis_day", AsyncMock(return_value=[ft])),
                patch.object(tennis_loop, "post_new_general_message", AsyncMock(return_value=SimpleNamespace(id=502))) as post_msg,
            ):
                await tennis_loop.run_tennis_loop(fake_bot)
                return post_msg

        post_msg = asyncio.run(run())

        post_msg.assert_awaited_once()
        self.assertIn("Winner: Player A", post_msg.await_args.kwargs["content"])
        self.assertIn("Final sets: 3-6 | 6-4 | 6-3", post_msg.await_args.kwargs["content"])
        self.assertIn("ft-complete", tennis_loop.final_announced_ids)

    def test_should_prepare_tennis_start_watch_accepts_injected_now(self):
        from modules import tennis_loop
        from utils.time_utils import to_bot_tz

        now = to_bot_tz("2026-06-12T10:00:00+00:00")
        match = tennis_match(start_time="2026-06-12T12:00:00+00:00")

        with patch.object(tennis_loop, "TENNIS_PRE_ANNOUNCE_HOURS", 4):
            self.assertTrue(tennis_loop.should_prepare_tennis_start_watch(match, now=now))

    def test_fetch_upcoming_tennis_schedule_returns_future_matches_only(self):
        from modules import api_provider

        future = tennis_match(match_id="future", start_time="2026-06-12T13:00:00+00:00")
        past = tennis_match(match_id="past", start_time="2026-06-12T09:00:00+00:00")

        async def run():
            with patch.object(api_provider, "fetch_tennis_day", AsyncMock(return_value=[past, future])):
                return await api_provider.fetch_upcoming_tennis_schedule(
                    None,
                    datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc),
                )

        matches = asyncio.run(run())
        self.assertEqual([m["match_id"] for m in matches], ["future"])

    def test_fetch_tennis_finished_today_excludes_incomplete_final_payloads(self):
        from modules import api_provider

        complete = tennis_match(
            match_id="complete",
            status="FT",
            sets=[{"a": 6, "b": 4}, {"a": 7, "b": 5}],
            winner="Player A",
        )
        incomplete = tennis_match(
            match_id="incomplete",
            status="FT",
            sets=[{"a": 3, "b": 6}, {"a": 4, "b": 4}],
            winner="",
        )

        async def run():
            with (
                patch.object(api_provider, "bot_now", return_value=self.now),
                patch.object(api_provider, "_get_cached_tennis_scoreboard", AsyncMock(return_value=[incomplete, complete])),
            ):
                return await api_provider.fetch_tennis_finished_today(None)

        matches = asyncio.run(run())
        self.assertEqual([m["match_id"] for m in matches], ["complete"])

    def test_scheduler_tennis_sleep_plan_refreshes_before_distant_start(self):
        from modules import scheduler

        future = tennis_match(match_id="future-11h", start_time="2026-06-12T21:00:00+00:00")
        now_utc = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
        fake_bot = SimpleNamespace(http_session=None)

        async def run():
            with (
                patch.object(scheduler, "TENNIS_PRE_ANNOUNCE_HOURS", 4),
                patch.object(scheduler.api_provider, "fetch_upcoming_tennis_schedule", AsyncMock(return_value=[future])),
            ):
                return await scheduler._plan_tennis_sleep_until_next_match(fake_bot, now_utc)

        next_check = asyncio.run(run())

        self.assertEqual(next_check, now_utc + timedelta(hours=6))
        status = scheduler.get_tennis_scheduler_status()
        self.assertEqual(status["mode"], "sleeping")
        self.assertEqual(status["next_planned_start_utc"], datetime(2026, 6, 12, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(status["next_planned_wake_utc"], datetime(2026, 6, 12, 17, 0, tzinfo=timezone.utc))

    def test_scheduler_tennis_sleep_plan_wakes_at_start_watch_window(self):
        from modules import scheduler

        future = tennis_match(match_id="future-5h", start_time="2026-06-12T15:00:00+00:00")
        now_utc = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
        fake_bot = SimpleNamespace(http_session=None)

        async def run():
            with (
                patch.object(scheduler, "TENNIS_PRE_ANNOUNCE_HOURS", 4),
                patch.object(scheduler.api_provider, "fetch_upcoming_tennis_schedule", AsyncMock(return_value=[future])),
            ):
                return await scheduler._plan_tennis_sleep_until_next_match(fake_bot, now_utc)

        next_check = asyncio.run(run())

        self.assertEqual(next_check, datetime(2026, 6, 12, 11, 0, tzinfo=timezone.utc))

    def test_scheduler_tennis_poll_needed_for_live_match(self):
        from modules import scheduler

        live = tennis_match(match_id="live-1", status="LIVE")
        fake_bot = SimpleNamespace(http_session=None)

        async def run():
            with patch.object(scheduler.api_provider, "fetch_tennis_day", AsyncMock(return_value=[live])):
                return await scheduler._tennis_poll_needed(fake_bot, datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc))

        self.assertTrue(asyncio.run(run()))

    def test_scheduler_tennis_poll_needed_for_ns_match_in_start_watch_window(self):
        from modules import scheduler

        match = tennis_match(match_id="pre-1", start_time="2026-06-12T12:00:00+00:00")
        fake_bot = SimpleNamespace(http_session=None)
        now_utc = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)

        async def run():
            with (
                patch.object(scheduler, "TENNIS_PRE_ANNOUNCE_HOURS", 4),
                patch.object(scheduler.api_provider, "fetch_tennis_day", AsyncMock(return_value=[match])),
            ):
                return await scheduler._tennis_poll_decision(fake_bot, now_utc)

        needed, reason, detail = asyncio.run(run())
        self.assertTrue(needed)
        self.assertEqual(reason, "tennis_start_watch")
        self.assertIn("fixture=pre-1", detail)

    def test_scheduler_tennis_poll_needed_for_prepared_match_in_start_watch_window(self):
        from modules import scheduler

        match = tennis_match(match_id="pre-1", start_time="2026-06-12T12:00:00+00:00")
        fake_bot = SimpleNamespace(http_session=None)
        now_utc = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)

        async def run():
            scheduler.tennis_loop.start_watch_prepared_ids.add("pre-1")
            try:
                with (
                    patch.object(scheduler, "TENNIS_PRE_ANNOUNCE_HOURS", 4),
                    patch.object(scheduler.api_provider, "fetch_tennis_day", AsyncMock(return_value=[match])),
                ):
                    return await scheduler._tennis_poll_decision(fake_bot, now_utc)
            finally:
                scheduler.tennis_loop.start_watch_prepared_ids.discard("pre-1")

        needed, reason, detail = asyncio.run(run())
        self.assertTrue(needed)
        self.assertEqual(reason, "tennis_start_watch")
        self.assertIn("fixture=pre-1", detail)

    def test_scheduler_tennis_poll_needed_for_prepared_match_after_scheduled_start(self):
        from modules import scheduler

        match = tennis_match(match_id="pre-1", start_time="2026-06-12T12:00:00+00:00")
        fake_bot = SimpleNamespace(http_session=None)
        now_utc = datetime(2026, 6, 12, 13, 0, tzinfo=timezone.utc)

        async def run():
            scheduler.tennis_loop.start_watch_prepared_ids.add("pre-1")
            try:
                with (
                    patch.object(scheduler, "TENNIS_PRE_ANNOUNCE_HOURS", 4),
                    patch.object(scheduler.api_provider, "fetch_tennis_day", AsyncMock(return_value=[match])),
                ):
                    return await scheduler._tennis_poll_decision(fake_bot, now_utc)
            finally:
                scheduler.tennis_loop.start_watch_prepared_ids.discard("pre-1")

        needed, reason, detail = asyncio.run(run())
        self.assertTrue(needed)
        self.assertEqual(reason, "tennis_start_watch")
        self.assertIn("fixture=pre-1", detail)

    def test_scheduler_tennis_poll_not_needed_for_stale_announced_ns_match(self):
        from modules import scheduler

        match = tennis_match(match_id="pre-1", start_time="2026-06-12T12:00:00+00:00")
        fake_bot = SimpleNamespace(http_session=None)
        now_utc = datetime(2026, 6, 12, 17, 1, tzinfo=timezone.utc)

        async def run():
            scheduler.tennis_loop.start_watch_prepared_ids.add("pre-1")
            try:
                with (
                    patch.object(scheduler, "TENNIS_PRE_ANNOUNCE_HOURS", 4),
                    patch.object(scheduler.api_provider, "fetch_tennis_day", AsyncMock(return_value=[match])),
                ):
                    return await scheduler._tennis_poll_decision(fake_bot, now_utc)
            finally:
                scheduler.tennis_loop.start_watch_prepared_ids.discard("pre-1")

        needed, reason, detail = asyncio.run(run())
        self.assertFalse(needed)
        self.assertEqual(reason, "no_relevant_tennis")
        self.assertIn("matches=1", detail)

    def test_scheduler_tennis_sleep_plan_does_not_return_now_when_wake_is_due(self):
        from modules import scheduler

        future = tennis_match(match_id="future-2h", start_time="2026-06-12T12:00:00+00:00")
        now_utc = datetime(2026, 6, 12, 10, 0, tzinfo=timezone.utc)
        fake_bot = SimpleNamespace(http_session=None)

        async def run():
            with (
                patch.object(scheduler, "TENNIS_PRE_ANNOUNCE_HOURS", 4),
                patch.object(scheduler.api_provider, "fetch_upcoming_tennis_schedule", AsyncMock(return_value=[future])),
            ):
                return await scheduler._plan_tennis_sleep_until_next_match(fake_bot, now_utc)

        next_check = asyncio.run(run())

        self.assertEqual(next_check, now_utc + timedelta(seconds=scheduler._TENNIS_INTERVAL_SEC))
        status = scheduler.get_tennis_scheduler_status()
        self.assertEqual(status["sleep_reason"], "next_tennis_wake")

    def test_scheduler_tennis_poll_needed_for_unannounced_ft_uses_injected_day(self):
        from modules import scheduler

        match = tennis_match(match_id="ft-1", status="FT", start_time="2026-06-12T12:00:00+00:00")
        fake_bot = SimpleNamespace(http_session=None)

        async def run():
            with patch.object(scheduler.api_provider, "fetch_tennis_day", AsyncMock(return_value=[match])):
                return await scheduler._tennis_poll_needed(
                    fake_bot,
                    datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc),
                )

        self.assertTrue(asyncio.run(run()))

    def test_tennis_scheduler_logs_only_meaningful_state_changes(self):
        from modules import scheduler

        scheduler._last_logged_tennis_state = None

        with self.assertLogs("modules.scheduler", level="INFO") as logs:
            scheduler._set_tennis_scheduler_state(
                mode="awake",
                next_tennis_check_utc=datetime(2026, 6, 12, 10, 1, tzinfo=timezone.utc),
                wake_reason="tennis_live",
                wake_reason_detail="fixture=live-1 status=LIVE start=n/a",
            )

        self.assertEqual(len(logs.output), 1)
        self.assertIn("Tennis scheduler awake", logs.output[0])
        self.assertEqual(
            scheduler.get_tennis_scheduler_status()["next_tennis_check_utc"],
            datetime(2026, 6, 12, 10, 1, tzinfo=timezone.utc),
        )

        with self.assertNoLogs("modules.scheduler", level="INFO"):
            scheduler._set_tennis_scheduler_state(
                mode="awake",
                next_tennis_check_utc=datetime(2026, 6, 12, 10, 2, tzinfo=timezone.utc),
                wake_reason="tennis_live",
                wake_reason_detail="fixture=live-1 status=LIVE start=n/a",
            )

        self.assertEqual(
            scheduler.get_tennis_scheduler_status()["next_tennis_check_utc"],
            datetime(2026, 6, 12, 10, 2, tzinfo=timezone.utc),
        )

        with self.assertLogs("modules.scheduler", level="INFO") as logs:
            scheduler._set_tennis_scheduler_state(
                mode="sleeping",
                next_tennis_check_utc=datetime(2026, 6, 12, 16, 0, tzinfo=timezone.utc),
                next_schedule_refresh_utc=datetime(2026, 6, 12, 16, 0, tzinfo=timezone.utc),
                next_planned_start_utc=datetime(2026, 6, 12, 21, 0, tzinfo=timezone.utc),
                next_planned_wake_utc=datetime(2026, 6, 12, 17, 0, tzinfo=timezone.utc),
                sleep_reason="next_tennis_wake",
                sleep_reason_detail="start=2026-06-12T21:00:00+00:00 wake=2026-06-12T17:00:00+00:00",
            )

        self.assertEqual(len(logs.output), 1)
        self.assertIn("Tennis scheduler sleeping", logs.output[0])

        with self.assertNoLogs("modules.scheduler", level="INFO"):
            scheduler._set_tennis_scheduler_state(
                mode="sleeping",
                next_tennis_check_utc=datetime(2026, 6, 12, 16, 1, tzinfo=timezone.utc),
                next_schedule_refresh_utc=datetime(2026, 6, 12, 16, 1, tzinfo=timezone.utc),
                next_planned_start_utc=datetime(2026, 6, 12, 21, 0, tzinfo=timezone.utc),
                next_planned_wake_utc=datetime(2026, 6, 12, 17, 0, tzinfo=timezone.utc),
                sleep_reason="next_tennis_wake",
                sleep_reason_detail="start=2026-06-12T21:00:00+00:00 wake=2026-06-12T17:00:00+00:00",
            )

        with self.assertLogs("modules.scheduler", level="INFO") as logs:
            scheduler._set_tennis_scheduler_state(
                mode="sleeping",
                next_tennis_check_utc=datetime(2026, 6, 12, 16, 2, tzinfo=timezone.utc),
                next_schedule_refresh_utc=datetime(2026, 6, 12, 16, 2, tzinfo=timezone.utc),
                next_planned_start_utc=datetime(2026, 6, 12, 22, 0, tzinfo=timezone.utc),
                next_planned_wake_utc=datetime(2026, 6, 12, 18, 0, tzinfo=timezone.utc),
                sleep_reason="next_tennis_wake",
                sleep_reason_detail="start=2026-06-12T22:00:00+00:00 wake=2026-06-12T18:00:00+00:00",
            )

        self.assertEqual(len(logs.output), 1)


if __name__ == "__main__":
    unittest.main()
