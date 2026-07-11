import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")

from tests.regression_helpers import espn_match


class EspnRequestReductionTests(unittest.TestCase):
    def setUp(self):
        from modules import api_provider

        api_provider._football_scoreboard_cache.clear()
        api_provider._cache = []
        api_provider._cache_date = None
        api_provider._cache_ts = None
        api_provider._espn_partial_refresh_warning_log_keys.clear()
        api_provider._espn_request_stats_date = None
        api_provider._espn_full_league_requests = 0
        api_provider._espn_active_league_requests = 0
        api_provider._espn_healthy = True
        api_provider._consecutive_failures = 0
        api_provider._retry_after = None

    @staticmethod
    def _summary(matches, succeeded):
        return {
            "matches": matches,
            "success_count": len(succeeded),
            "failure_count": 0,
            "succeeded_league_ids": list(succeeded),
            "failed_league_ids": [],
        }

    def test_warm_active_refresh_requests_only_the_live_league(self):
        from modules import api_provider

        now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
        live = espn_match(fixture_id="live-135", league_id=135)
        live["fixture"]["date"] = "2026-07-11T17:30:00Z"
        live["fixture"]["status"] = {"short": "1H", "elapsed": 30}
        future = espn_match(fixture_id="future-39", league_id=39)
        future["fixture"]["date"] = "2026-07-11T23:00:00Z"
        future["fixture"]["status"] = {"short": "NS", "elapsed": None}
        fresh_live = {**live, "goals": {"home": 1, "away": 0}}
        cached = {
            "matches": [live, future],
            "fetched_at": now - timedelta(minutes=2),
            "full_fetched_at": now - timedelta(minutes=5),
            "league_fetched_at": {
                "135": now - timedelta(seconds=60),
                "39": now - timedelta(seconds=60),
            },
        }
        api_provider._football_scoreboard_cache["2026-07-11"] = cached

        async def run():
            fetch = AsyncMock(return_value=self._summary([fresh_live], {135}))
            with (
                patch.object(api_provider, "bot_now", return_value=now),
                patch.object(api_provider.espn_client, "fetch_all_leagues_with_summary", fetch),
            ):
                matches = await api_provider._get_active_scoreboard_for_date(
                    None, "2026-07-11", now
                )
                return matches, fetch

        matches, fetch = asyncio.run(run())

        requested_map = fetch.await_args.args[1]
        self.assertEqual(requested_map, {135: api_provider.LEAGUE_SLUG_MAP[135]})
        self.assertEqual(
            {match["fixture"]["id"] for match in matches},
            {"live-135", "future-39"},
        )
        updated_live = next(match for match in matches if match["fixture"]["id"] == "live-135")
        self.assertEqual(updated_live["goals"], {"home": 1, "away": 0})
        self.assertEqual(
            api_provider.get_status()["espn_league_requests_today"],
            {"full_discovery": 0, "active_refresh": 1, "total": 1},
        )

    def test_recent_discovery_with_no_active_fixture_makes_no_request(self):
        from modules import api_provider

        now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
        future = espn_match(fixture_id="future-39", league_id=39)
        future["fixture"]["date"] = "2026-07-11T23:00:00Z"
        future["fixture"]["status"] = {"short": "NS", "elapsed": None}
        api_provider._football_scoreboard_cache["2026-07-11"] = {
            "matches": [future],
            "fetched_at": now - timedelta(minutes=5),
            "full_fetched_at": now - timedelta(minutes=5),
            "league_fetched_at": {"39": now - timedelta(minutes=5)},
        }

        async def run():
            fetch = AsyncMock()
            with (
                patch.object(api_provider, "bot_now", return_value=now),
                patch.object(api_provider.espn_client, "fetch_all_leagues_with_summary", fetch),
            ):
                matches = await api_provider._get_active_scoreboard_for_date(
                    None, "2026-07-11", now
                )
                return matches, fetch

        matches, fetch = asyncio.run(run())
        self.assertEqual(matches, [future])
        fetch.assert_not_awaited()

    def test_full_discovery_still_runs_every_thirty_minutes(self):
        from modules import api_provider

        now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
        api_provider._football_scoreboard_cache["2026-07-11"] = {
            "matches": [],
            "fetched_at": now - timedelta(minutes=31),
            "full_fetched_at": now - timedelta(minutes=31),
            "league_fetched_at": {},
        }

        async def run():
            fetch = AsyncMock(return_value=self._summary([], set(api_provider.LEAGUE_SLUG_MAP)))
            with (
                patch.object(api_provider, "bot_now", return_value=now),
                patch.object(api_provider.espn_client, "fetch_all_leagues_with_summary", fetch),
            ):
                await api_provider._get_active_scoreboard_for_date(None, "2026-07-11", now)
                return fetch

        fetch = asyncio.run(run())
        self.assertEqual(fetch.await_args.args[1], api_provider.LEAGUE_SLUG_MAP)
        self.assertEqual(
            api_provider.get_status()["espn_league_requests_today"]["full_discovery"],
            len(api_provider.LEAGUE_SLUG_MAP),
        )

    def test_cross_midnight_live_match_refreshes_only_its_past_date_league(self):
        from modules import api_provider

        now = datetime(2026, 7, 11, 0, 30, tzinfo=timezone.utc)
        live = espn_match(fixture_id="cross-midnight", league_id=135)
        live["fixture"]["date"] = "2026-07-10T23:00:00Z"
        live["fixture"]["status"] = {"short": "2H", "elapsed": 70}
        api_provider._football_scoreboard_cache["2026-07-10"] = {
            "matches": [live],
            "fetched_at": now - timedelta(minutes=2),
            "full_fetched_at": now - timedelta(hours=1),
            "league_fetched_at": {"135": now - timedelta(seconds=60)},
        }

        async def run():
            fetch = AsyncMock(return_value=self._summary([live], {135}))
            with (
                patch.object(api_provider, "bot_now", return_value=now),
                patch.object(api_provider.espn_client, "fetch_all_leagues_with_summary", fetch),
            ):
                await api_provider._get_active_scoreboard_for_date(None, "2026-07-10", now)
                return fetch

        fetch = asyncio.run(run())
        self.assertEqual(fetch.await_args.args[1], {135: api_provider.LEAGUE_SLUG_MAP[135]})

    def test_resolved_ft_does_not_keep_its_league_in_active_refresh(self):
        from modules import api_provider, match_state

        now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
        finished = espn_match(fixture_id="resolved-ft", league_id=135)
        finished["fixture"]["date"] = "2026-07-11T16:00:00Z"
        finished["fixture"]["status"] = {"short": "FT", "long": "Full Time"}
        cached = {"matches": [finished]}
        with patch.object(
            match_state,
            "get_fixture_state",
            return_value={
                "ft_announced": True,
                "memory_updated": True,
                "event_completeness_status": api_provider.EVENTS_COMPLETE,
            },
        ):
            league_ids = api_provider._active_espn_league_ids(cached, now)
        self.assertEqual(league_ids, set())

    def test_unresolved_ft_remains_in_active_refresh(self):
        from modules import api_provider, match_state

        now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
        finished = espn_match(fixture_id="unresolved-ft", league_id=135)
        finished["fixture"]["date"] = "2026-07-11T16:00:00Z"
        finished["fixture"]["status"] = {"short": "FT", "long": "Full Time"}
        with patch.object(
            match_state,
            "get_fixture_state",
            return_value={"ft_announced": True, "memory_updated": False},
        ):
            league_ids = api_provider._active_espn_league_ids({"matches": [finished]}, now)
        self.assertEqual(league_ids, {135})

    def test_retry_window_forces_one_league_probe_despite_fresh_cache(self):
        from modules import api_provider

        now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
        api_provider._espn_healthy = False
        api_provider._retry_after = now - timedelta(seconds=1)
        api_provider._football_scoreboard_cache["2026-07-11"] = {
            "matches": [],
            "fetched_at": now - timedelta(minutes=5),
            "full_fetched_at": now - timedelta(minutes=5),
            "league_fetched_at": {},
        }
        probe_id = next(iter(api_provider.LEAGUE_SLUG_MAP))

        async def run():
            fetch = AsyncMock(return_value=self._summary([], {probe_id}))
            with (
                patch.object(api_provider, "bot_now", return_value=now),
                patch.object(api_provider.espn_client, "fetch_all_leagues_with_summary", fetch),
            ):
                matches = await api_provider.fetch_football_window(
                    None,
                    now - timedelta(hours=1),
                    now + timedelta(hours=1),
                    now_utc=now,
                    refresh_mode="active",
                )
                return matches, fetch

        matches, fetch = asyncio.run(run())
        self.assertEqual(matches, [])
        self.assertTrue(api_provider._espn_healthy)
        self.assertEqual(fetch.await_count, 1)
        self.assertEqual(
            fetch.await_args.args[1],
            {probe_id: api_provider.LEAGUE_SLUG_MAP[probe_id]},
        )

    def test_failed_one_league_probe_keeps_fallback_active(self):
        from modules import api_provider

        now = datetime(2026, 7, 11, 18, 0, tzinfo=timezone.utc)
        api_provider._espn_healthy = False
        api_provider._retry_after = now - timedelta(seconds=1)
        probe_id = next(iter(api_provider.LEAGUE_SLUG_MAP))
        failed = {
            "matches": [],
            "success_count": 0,
            "failure_count": 1,
            "succeeded_league_ids": [],
            "failed_league_ids": [probe_id],
        }

        async def run():
            fetch = AsyncMock(return_value=failed)
            with (
                patch.object(api_provider, "bot_now", return_value=now),
                patch.object(api_provider.espn_client, "fetch_all_leagues_with_summary", fetch),
            ):
                matches = await api_provider.fetch_football_window(
                    None,
                    now - timedelta(hours=1),
                    now + timedelta(hours=1),
                    now_utc=now,
                    refresh_mode="active",
                )
                return matches, fetch

        matches, fetch = asyncio.run(run())
        self.assertEqual(matches, [])
        self.assertFalse(api_provider._espn_healthy)
        self.assertGreater(api_provider._retry_after, now)
        self.assertEqual(fetch.await_count, 1)


if __name__ == "__main__":
    unittest.main()
