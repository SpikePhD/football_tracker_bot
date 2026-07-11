import asyncio
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")

from tests.regression_helpers import espn_match


class FootballCycleSnapshotTests(unittest.TestCase):
    def test_snapshot_fetches_relevant_window_once_and_derives_live_from_it(self):
        from modules import api_provider
        from modules.football_cycle import build_football_cycle_snapshot

        now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
        match = espn_match(fixture_id="cycle-live")
        match["fixture"]["status"] = {"short": "1H", "elapsed": 12}
        relevant_matches = [match]
        session = object()

        async def run():
            with (
                patch.object(
                    api_provider,
                    "fetch_relevant_football",
                    AsyncMock(return_value=relevant_matches),
                ) as relevant,
                patch.object(
                    api_provider,
                    "fetch_live",
                    AsyncMock(return_value=[match]),
                ) as live,
            ):
                snapshot = await build_football_cycle_snapshot(session, now)
                return snapshot, relevant, live

        snapshot, relevant, live = asyncio.run(run())

        relevant.assert_awaited_once_with(session, now)
        live.assert_awaited_once_with(
            session,
            now_utc=now,
            relevant_matches=relevant_matches,
        )
        self.assertEqual(snapshot.relevant_matches, (match,))
        self.assertEqual(snapshot.live_matches, (match,))
        self.assertEqual(snapshot.relevant_by_id(), {"cycle-live": match})

    def test_scheduler_cycle_passes_one_snapshot_to_live_and_ft_consumers(self):
        from modules import scheduler
        from modules.football_cycle import FootballCycleSnapshot

        now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
        live_match = espn_match(fixture_id="cycle-live")
        live_match["fixture"]["status"] = {"short": "1H", "elapsed": 12}
        terminal = espn_match(fixture_id="cycle-ft")
        terminal["fixture"]["status"] = {"short": "FT", "long": "Full Time"}
        snapshot = FootballCycleSnapshot(now, (live_match, terminal), (live_match,))
        bot = SimpleNamespace(http_session=object())

        async def run():
            with (
                patch.object(
                    scheduler,
                    "build_football_cycle_snapshot",
                    AsyncMock(side_effect=AssertionError("snapshot must not be rebuilt")),
                ),
                patch.object(scheduler, "run_live_loop", AsyncMock()) as live,
                patch.object(scheduler, "fetch_and_post_ft", AsyncMock()) as ft,
                patch.object(scheduler, "prune_live_state") as prune,
            ):
                await scheduler.run_football_cycle(bot, now, snapshot=snapshot)
                return live, ft, prune

        live, ft, prune = asyncio.run(run())

        live.assert_awaited_once_with(bot, matches=snapshot.live_matches, now_utc=now)
        ft.assert_awaited_once_with(bot, matches=snapshot.relevant_matches, now_utc=now)
        prune.assert_called_once_with(now)

    def test_snapshot_decision_does_not_refetch_provider_data(self):
        from modules import scheduler
        from modules.football_cycle import FootballCycleSnapshot

        now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
        match = espn_match(fixture_id="cycle-live")
        match["fixture"]["date"] = "2026-07-11T18:00:00Z"
        match["fixture"]["status"] = {"short": "1H", "elapsed": 12}
        snapshot = FootballCycleSnapshot(now, (match,), (match,))
        bot = SimpleNamespace(http_session=object())

        async def run():
            with (
                patch.object(scheduler, "expected_ft_due_fixture_ids", return_value=[]),
                patch.object(
                    scheduler.api_provider,
                    "fetch_relevant_football",
                    AsyncMock(side_effect=AssertionError("unexpected relevant refetch")),
                ),
                patch.object(
                    scheduler.api_provider,
                    "has_live_football",
                    AsyncMock(side_effect=AssertionError("unexpected live refetch")),
                ),
            ):
                return await scheduler._football_poll_decision(bot, now, snapshot=snapshot)

        decision = asyncio.run(run())
        self.assertEqual(decision[0:2], (True, "lifecycle_fixture"))

    def test_consumers_skip_their_provider_fetch_when_matches_are_supplied(self):
        from modules import api_provider, ft_handler, live_loop, match_state

        bot = SimpleNamespace(http_session=object())
        now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)

        async def run():
            with (
                patch.object(live_loop, "is_silent", return_value=False),
                patch.object(api_provider, "fetch_live", AsyncMock()) as fetch_live,
                patch.object(live_loop, "_cleanup_missing_live_state"),
                patch.object(live_loop, "prune_live_state"),
            ):
                await live_loop.run_live_loop(bot, matches=(), now_utc=now)
            with (
                patch.object(ft_handler, "is_silent", return_value=False),
                patch.object(match_state, "migrate_ft_state_if_needed"),
                patch.object(match_state, "expected_ft_due_fixture_ids", return_value=[]),
                patch.object(api_provider, "fetch_relevant_football", AsyncMock()) as fetch_relevant,
            ):
                await ft_handler.fetch_and_post_ft(bot, matches=(), now_utc=now)
            return fetch_live, fetch_relevant

        fetch_live, fetch_relevant = asyncio.run(run())
        fetch_live.assert_not_awaited()
        fetch_relevant.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
