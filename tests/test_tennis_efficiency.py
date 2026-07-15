import asyncio
import copy
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


def tennis_match(*, status="NS", start="2026-07-13T12:00:00+00:00", tour="ATP", winner=None, sets=None):
    return {
        "sport": "tennis",
        "match_id": "tennis:atp:event:competition",
        "start_time": start,
        "status": {"short": status, "detail": status},
        "event_name": "Test Open",
        "round": "Final",
        "tour": tour,
        "player_a": "Jannik Sinner",
        "player_b": "Test Player",
        "winner": winner,
        "sets": sets or [],
    }


class TennisPreferenceTests(unittest.TestCase):
    def test_shared_preference_orders_complete_final_live_incomplete_final_and_ns(self):
        from utils.tennis_lifecycle import tennis_record_preference

        ns = tennis_match(status="NS")
        incomplete = tennis_match(status="FT", winner="Jannik Sinner", sets=[{"a": 6, "b": None}])
        live = tennis_match(status="LIVE", sets=[{"a": 6, "b": 4}, {"a": 2, "b": 1}])
        complete = tennis_match(
            status="FT",
            winner="Jannik Sinner",
            sets=[{"a": 6, "b": 4}, {"a": 6, "b": 3}],
        )

        self.assertGreater(tennis_record_preference(complete), tennis_record_preference(live))
        self.assertGreater(tennis_record_preference(live), tennis_record_preference(incomplete))
        self.assertGreater(tennis_record_preference(incomplete), tennis_record_preference(ns))

    def test_duplicate_merge_keeps_live_over_incomplete_final(self):
        from modules import api_provider

        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        live = tennis_match(status="LIVE", sets=[{"a": 6, "b": 4}])
        incomplete = tennis_match(status="FT", winner="Jannik Sinner", sets=[{"a": 6, "b": None}])
        with patch.dict(api_provider._tennis_source_cache, {}, clear=True):
            api_provider._tennis_source_cache[("atp", None)] = {"matches": [live], "fetched_at": now}
            api_provider._tennis_source_cache[("atp", "20260713")] = {
                "matches": [incomplete],
                "fetched_at": now,
            }
            merged = api_provider._merge_tennis_source_cache(now)
        self.assertEqual(merged[0]["status"]["short"], "LIVE")

    def test_incomplete_final_does_not_clear_existing_live_state(self):
        from modules import tennis_loop

        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        incomplete = tennis_match(
            status="FT",
            start=(now - timedelta(hours=1)).isoformat(),
            winner="Jannik Sinner",
            sets=[{"a": 6, "b": None}],
        )
        track_id = incomplete["match_id"]
        tennis_loop.live_state_keys[track_id] = "live-state"
        try:
            with (
                patch.object(tennis_loop, "is_silent", return_value=False),
                patch.object(tennis_loop, "_load_state_once"),
                patch.object(tennis_loop, "prune_tennis_state"),
            ):
                asyncio.run(
                    tennis_loop.run_tennis_loop(
                        SimpleNamespace(),
                        matches=[incomplete],
                        now_utc=now,
                    )
                )
            self.assertEqual(tennis_loop.live_state_keys[track_id], "live-state")
        finally:
            tennis_loop.live_state_keys.pop(track_id, None)


class TennisSchedulerPhaseTests(unittest.TestCase):
    def _decision(self, now, match):
        from modules import scheduler

        fake_bot = SimpleNamespace(http_session=object())
        with (
            patch.object(scheduler.tennis_loop, "ensure_tennis_state_loaded"),
            patch.object(scheduler.tennis_loop, "prune_tennis_state"),
            patch.object(scheduler.api_provider, "fetch_tennis_day", AsyncMock(return_value=[match])),
        ):
            return asyncio.run(scheduler._tennis_poll_decision(fake_bot, now))

    def test_polling_phase_boundaries_and_delayed_start(self):
        from modules import scheduler

        start = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        match = tennis_match(start=start.isoformat())
        early = self._decision(start - timedelta(hours=8), match)
        imminent = self._decision(start - timedelta(minutes=30), match)
        delayed = self._decision(start + timedelta(hours=4), match)
        expired = self._decision(start + timedelta(hours=4, seconds=1), match)

        self.assertEqual((early.phase, early.interval_sec), ("early", scheduler.TENNIS_EARLY_WATCH_POLL_INTERVAL_SEC))
        self.assertEqual((imminent.phase, imminent.interval_sec), ("imminent", scheduler.TENNIS_IMMINENT_POLL_INTERVAL_SEC))
        self.assertEqual((delayed.phase, delayed.interval_sec), ("imminent", scheduler.TENNIS_IMMINENT_POLL_INTERVAL_SEC))
        self.assertEqual((expired.phase, expired.interval_sec), ("idle", scheduler.TENNIS_IDLE_DISCOVERY_INTERVAL_SEC))

    def test_live_and_unannounced_final_use_live_cadence(self):
        from modules import scheduler

        now = datetime(2026, 7, 13, 12, tzinfo=timezone.utc)
        for status in ("LIVE", "FT"):
            decision = self._decision(now, tennis_match(status=status, start=(now - timedelta(hours=1)).isoformat()))
            self.assertEqual(decision.phase, "live")
            self.assertEqual(decision.interval_sec, scheduler.TENNIS_LIVE_POLL_INTERVAL_SEC)

    def test_processing_supplied_snapshot_does_not_refetch(self):
        from modules import api_provider, tennis_loop

        fetch = AsyncMock()
        with (
            patch.object(tennis_loop, "is_silent", return_value=False),
            patch.object(tennis_loop, "_load_state_once"),
            patch.object(tennis_loop, "prune_tennis_state"),
            patch.object(api_provider, "fetch_tennis_day", fetch),
        ):
            asyncio.run(tennis_loop.run_tennis_loop(SimpleNamespace(), matches=(), now_utc=datetime.now(timezone.utc)))
        fetch.assert_not_awaited()


class TennisProviderRequestTests(unittest.TestCase):
    def setUp(self):
        from modules import api_provider

        api_provider._tennis_cache = []
        api_provider._tennis_cache_date = None
        api_provider._tennis_cache_ts = None
        api_provider._tennis_source_cache.clear()
        api_provider._tennis_last_discovery_ts = None
        api_provider._tennis_stats_date = None
        api_provider._tennis_last_success_ts = None
        for key in api_provider._tennis_stats:
            api_provider._tennis_stats[key] = 0

    def test_cold_discovery_targeted_refresh_and_periodic_rediscovery_counts(self):
        from modules import api_provider
        from utils.espn_tennis_client import TennisSourceResult

        current = [datetime(2026, 7, 13, 10, tzinfo=timezone.utc)]
        match = tennis_match(status="LIVE", start="2026-07-13T10:00:00+00:00")

        async def fake_fetch(_session, sources):
            return [
                TennisSourceResult(tour, date_str, (match,) if (tour, date_str) == ("atp", "20260713") else (), True)
                for tour, date_str in sources
            ]

        with (
            patch.object(api_provider, "bot_now", side_effect=lambda: current[0]),
            patch.object(api_provider, "get_bot_local_date_string", return_value="2026-07-13"),
            patch.object(api_provider.espn_tennis_client, "fetch_tennis_sources", AsyncMock(side_effect=fake_fetch)) as fetch,
        ):
            asyncio.run(api_provider.fetch_tennis_day(object()))
            self.assertEqual(len(fetch.await_args.args[1]), 8)

            current[0] += timedelta(seconds=60)
            asyncio.run(api_provider.fetch_tennis_day(object()))
            self.assertEqual(fetch.await_count, 2)
            self.assertEqual(fetch.await_args.args[1], [("atp", "20260713")])

            current[0] += timedelta(minutes=30)
            asyncio.run(api_provider.fetch_tennis_day(object()))
            self.assertEqual(len(fetch.await_args.args[1]), 8)

            status = api_provider.get_tennis_status()

        self.assertEqual(status["requests"]["discovery"], 16)
        self.assertEqual(status["requests"]["targeted"], 1)
        self.assertEqual(status["requests"]["total"], 17)
        self.assertTrue(status["last_discovery_utc"].endswith("+00:00"))

    def test_failed_source_preserves_last_success_until_stale_expiry(self):
        from modules import api_provider
        from utils.espn_tennis_client import TennisSourceResult

        now = datetime(2026, 7, 13, 10, tzinfo=timezone.utc)
        match = tennis_match(status="LIVE", start=now.isoformat())
        api_provider._tennis_cache = [match]
        api_provider._tennis_cache_date = "2026-07-13"
        api_provider._tennis_cache_ts = now - timedelta(minutes=2)
        api_provider._tennis_last_discovery_ts = now
        api_provider._tennis_source_cache[("atp", "20260713")] = {
            "matches": [match],
            "fetched_at": now - timedelta(minutes=59),
        }
        failed = [TennisSourceResult("atp", "20260713", (), False, "timeout")]
        with (
            patch.object(api_provider, "bot_now", return_value=now),
            patch.object(api_provider, "get_bot_local_date_string", return_value="2026-07-13"),
            patch.object(api_provider.espn_tennis_client, "fetch_tennis_sources", AsyncMock(return_value=failed)),
        ):
            retained = asyncio.run(api_provider.fetch_tennis_day(object()))
        self.assertEqual(len(retained), 1)

        expired_at = now + timedelta(minutes=2)
        api_provider._tennis_cache_ts = now
        with (
            patch.object(api_provider, "bot_now", return_value=expired_at),
            patch.object(api_provider, "get_bot_local_date_string", return_value="2026-07-13"),
            patch.object(api_provider.espn_tennis_client, "fetch_tennis_sources", AsyncMock(return_value=failed)),
        ):
            expired = asyncio.run(api_provider.fetch_tennis_day(object()))
        self.assertEqual(expired, [])

    def test_successful_empty_target_replaces_that_source(self):
        from modules import api_provider
        from utils.espn_tennis_client import TennisSourceResult

        now = datetime(2026, 7, 13, 10, tzinfo=timezone.utc)
        match = tennis_match(status="LIVE", start=now.isoformat())
        api_provider._tennis_cache = [match]
        api_provider._tennis_cache_date = "2026-07-13"
        api_provider._tennis_cache_ts = now - timedelta(minutes=2)
        api_provider._tennis_last_discovery_ts = now
        api_provider._tennis_source_cache[("atp", "20260713")] = {
            "matches": [match],
            "fetched_at": now,
        }
        empty = [TennisSourceResult("atp", "20260713", (), True)]
        with (
            patch.object(api_provider, "bot_now", return_value=now),
            patch.object(api_provider, "get_bot_local_date_string", return_value="2026-07-13"),
            patch.object(api_provider.espn_tennis_client, "fetch_tennis_sources", AsyncMock(return_value=empty)),
        ):
            result = asyncio.run(api_provider.fetch_tennis_day(object()))
        self.assertEqual(result, [])

    def test_target_sources_are_distinct_per_tour_and_date(self):
        from modules import api_provider

        matches = [
            tennis_match(tour="ATP"),
            tennis_match(tour="ATP"),
            tennis_match(tour="WTA"),
        ]
        self.assertEqual(
            api_provider._tennis_target_sources(matches),
            [("atp", "20260713"), ("wta", "20260713")],
        )


class TennisClientWarningTests(unittest.TestCase):
    def test_identical_source_warning_is_limited_to_once_per_thirty_minutes(self):
        from utils import espn_tennis_client

        espn_tennis_client._last_warning_at.clear()
        with (
            patch.object(espn_tennis_client.time, "monotonic", side_effect=[0, 10, 1801]),
            self.assertLogs(espn_tennis_client.logger, level="WARNING") as captured,
        ):
            espn_tennis_client._warn_source_failure("atp", "20260713", "timeout", "timeout")
            espn_tennis_client._warn_source_failure("atp", "20260713", "timeout", "timeout")
            espn_tennis_client._warn_source_failure("atp", "20260713", "timeout", "timeout")
        self.assertEqual(len(captured.records), 2)


class TennisConfigurationTests(unittest.TestCase):
    def test_polling_cadence_order_and_watch_window_are_validated(self):
        from modules.configuration import ConfigurationError, load_effective_config, validate_config

        config = load_effective_config()
        invalid_cadence = copy.deepcopy(config)
        invalid_cadence["operations"]["tennis_early_watch_poll_interval_sec"] = 30
        with self.assertRaises(ConfigurationError):
            validate_config(invalid_cadence)

        invalid_window = copy.deepcopy(config)
        invalid_window["operations"]["tennis_pre_announce_hours"] = 0
        with self.assertRaises(ConfigurationError):
            validate_config(invalid_window)

    def test_catalog_relabels_early_watch_and_exposes_new_controls(self):
        from modules.configuration import configuration_catalog

        catalog = {field["path"]: field for field in configuration_catalog()}
        self.assertEqual(
            catalog["operations.tennis_pre_announce_hours"]["label"],
            "Early Start-Watch Lead Time (Hours)",
        )
        self.assertTrue(catalog["operations.tennis_full_discovery_interval_sec"]["restart_required"])


class RosterResilienceTests(unittest.TestCase):
    class _Response:
        def __init__(self, status, payload=None):
            self.status = status
            self.payload = payload or {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def json(self, **_kwargs):
            return self.payload

    class _Session:
        def __init__(self, responses):
            self.responses = list(responses)
            self.urls = []

        def get(self, url, **_kwargs):
            self.urls.append(url)
            return self.responses.pop(0)

    def test_scoped_roster_404_falls_back_to_generic(self):
        from utils.espn_client import fetch_team_roster_espn

        payload = {"team": {"displayName": "AC Milan"}, "athletes": [], "staff": []}
        session = self._Session([self._Response(404), self._Response(200, payload)])
        result = asyncio.run(fetch_team_roster_espn(session, "103", ["ita.1"]))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["scope"], "generic")
        self.assertIn("/ita.1/teams/103", session.urls[0])
        self.assertTrue(session.urls[1].endswith("/teams/103"))

    def test_roster_5xx_is_transient_and_does_not_call_generic(self):
        from utils.espn_client import fetch_team_roster_espn

        session = self._Session([self._Response(503)])
        result = asyncio.run(fetch_team_roster_espn(session, "23", ["fifa.world"]))
        self.assertEqual(result["status"], "transient_error")
        self.assertEqual(len(session.urls), 1)

    def test_slug_candidates_come_from_standings_and_matches(self):
        from modules import football_memory

        memory = {
            "leagues": {"135": {"standings": [{"team_id": "103"}]}},
            "matches": {
                "m1": {
                    "league_id": 1,
                    "home": {"id": "23"},
                    "away": {"id": "103"},
                }
            },
        }
        with patch.object(football_memory, "LEAGUE_SLUG_MAP", {135: "ita.1", 1: "fifa.world"}):
            candidates = football_memory._team_slug_candidates(memory)
        self.assertEqual(candidates["23"], ["fifa.world"])
        self.assertEqual(candidates["103"], ["ita.1", "fifa.world"])

    def test_unsupported_result_is_persisted_and_suppresses_retry(self):
        from modules import football_memory

        state = copy.deepcopy(football_memory._ROSTER_LOOKUP_STATE_DEFAULT)

        def fake_load(_filename, _default):
            return copy.deepcopy(state)

        def fake_save(_filename, value):
            state.clear()
            state.update(copy.deepcopy(value))

        fetch = AsyncMock(return_value={
            "status": "unsupported",
            "roster": None,
            "attempts": [{"scope": "fifa.world", "http_status": 400}, {"scope": "generic", "http_status": 404}],
        })
        now = datetime(2026, 7, 13, 10, tzinfo=timezone.utc)
        with (
            patch.object(football_memory, "load", side_effect=fake_load),
            patch.object(football_memory, "save", side_effect=fake_save),
            patch.object(football_memory, "bot_now", return_value=now),
            patch("utils.espn_client.fetch_team_roster_espn", fetch),
        ):
            self.assertIsNone(asyncio.run(football_memory.update_team_info(object(), "23", ["fifa.world"])))
            self.assertIsNone(asyncio.run(football_memory.update_team_info(object(), "23", ["fifa.world"])))
        self.assertEqual(fetch.await_count, 1)
        self.assertIn("23", state["unsupported"])

    def test_transient_roster_failure_is_not_negative_cached(self):
        from modules import football_memory

        saved = Mock()
        with (
            patch.object(football_memory, "load", return_value=copy.deepcopy(football_memory._ROSTER_LOOKUP_STATE_DEFAULT)),
            patch.object(football_memory, "save", saved),
            patch(
                "utils.espn_client.fetch_team_roster_espn",
                AsyncMock(return_value={"status": "transient_error", "roster": None, "attempts": [{"error": "timeout"}]}),
            ),
        ):
            result = asyncio.run(football_memory.update_team_info(object(), "23", ["fifa.world"]))
        self.assertIsNone(result)
        saved.assert_not_called()


if __name__ == "__main__":
    unittest.main()
