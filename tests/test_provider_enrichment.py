import asyncio
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from tests.regression_helpers import (
    espn_match,
    reset_api_provider_state,
)

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class ProviderEnrichmentTests(unittest.TestCase):

    def setUp(self):
        reset_api_provider_state()

    def test_live_mapping_uses_api_football_live_feed_not_season_lookup(self):
        from modules import api_provider

        match = espn_match(fixture_id="737155")
        other_live_fixture = {
            "fixture": {"id": 111111, "date": "2026-05-24T13:00:00+00:00"},
            "league": {"id": 39},
            "teams": {
                "home": {"name": "Parma"},
                "away": {"name": "Sassuolo"},
            },
        }
        live_fixture = {
            "fixture": {"id": 999999, "date": "2026-05-24T13:00:00+00:00"},
            "league": {"id": 135},
            "teams": {
                "home": {"name": "Parma"},
                "away": {"name": "Sassuolo"},
            },
        }

        async def run():
            api_provider._reset_enrich_state_for_today()
            api_provider._api_fixture_id_cache.clear()
            with (
                patch.object(api_provider.api_client, "fetch_live_fixtures_payload", AsyncMock(return_value={"response": [other_live_fixture, live_fixture]})) as live_fetch,
                patch.object(api_provider.api_client, "_make_request", AsyncMock(return_value={"response": []})) as make_request,
            ):
                resolved = await api_provider.resolve_api_football_fixture_id(None, match)
                return resolved, live_fetch, make_request

        resolved, live_fetch, make_request = asyncio.run(run())
        self.assertEqual(resolved, 999999)
        live_fetch.assert_awaited_once_with(None)
        make_request.assert_not_awaited()

    def test_live_mapping_reuses_cached_live_feed_for_multiple_fixtures(self):
        from modules import api_provider

        match_one = espn_match(fixture_id="737155")
        match_two = espn_match(fixture_id="737156")
        match_two["teams"]["home"]["name"] = "Milan"
        match_two["teams"]["away"]["name"] = "Inter"
        live_payload = {
            "response": [
                {
                    "fixture": {"id": 999999, "date": "2026-05-24T13:00:00+00:00"},
                    "league": {"id": 135},
                    "teams": {
                        "home": {"name": "Parma"},
                        "away": {"name": "Sassuolo"},
                    },
                },
                {
                    "fixture": {"id": 999998, "date": "2026-05-24T13:00:00+00:00"},
                    "league": {"id": 135},
                    "teams": {
                        "home": {"name": "AC Milan"},
                        "away": {"name": "Internazionale"},
                    },
                },
            ]
        }

        async def run():
            with (
                patch.object(api_provider, "italy_now", return_value=datetime(2026, 5, 24, 15, 1, 0)),
                patch.object(api_provider.api_client, "fetch_live_fixtures_payload", AsyncMock(return_value=live_payload)) as live_fetch,
            ):
                first = await api_provider.resolve_api_football_fixture_id(None, match_one)
                second = await api_provider.resolve_api_football_fixture_id(None, match_two)
                return first, second, live_fetch

        first, second, live_fetch = asyncio.run(run())
        self.assertEqual(first, 999999)
        self.assertEqual(second, 999998)
        self.assertEqual(live_fetch.await_count, 1)

    def test_enrichment_reuses_complete_event_cache_without_refetching(self):
        from modules import api_provider

        match = espn_match(fixture_id="737155")
        state = "737155:1:0:0"
        api_goal = {
            "time": {"elapsed": 26},
            "player": {"name": "Scorer"},
            "team": {"id": 50, "name": "Parma"},
            "type": "Goal",
            "detail": "Normal Goal",
        }

        async def run():
            api_provider._api_fixture_id_cache["737155"] = 999999
            api_provider._enrich_retry_states[state] = {
                "first_seen": datetime(2026, 5, 24, 15, 0, 0),
                "attempt_count": 0,
                "last_attempt_at": None,
                "exhausted": False,
            }
            fetch_events = AsyncMock(return_value={"response": [api_goal]})
            with (
                patch.object(api_provider, "italy_now", return_value=datetime(2026, 5, 24, 15, 1, 0)),
                patch.object(api_provider, "API_ENRICH_RETRY_DELAYS_SEC", [0]),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider.api_client, "fetch_fixture_events", fetch_events),
            ):
                first = await api_provider.enrich_fixture_events(None, match)
                second = await api_provider.enrich_fixture_events(None, match)
                return first, second, fetch_events

        first, second, fetch_events = asyncio.run(run())
        self.assertEqual(first["events"][0]["player"]["name"], "Scorer")
        self.assertEqual(second["events"][0]["player"]["name"], "Scorer")
        self.assertEqual(fetch_events.await_count, 1)

    def test_best_known_events_prevent_stale_cache_downgrade(self):
        from modules import api_provider

        match = espn_match(fixture_id="737155")
        api_events = [
            {
                "time": {"elapsed": 26},
                "player": {"name": "Scorer"},
                "team": {"id": 50, "name": "Parma"},
                "type": "Goal",
                "detail": "Normal Goal",
            }
        ]

        async def run():
            api_provider._reset_enrich_state_for_today()
            api_provider._api_fixture_id_cache["737155"] = 999999
            api_provider._api_fixture_events_cache[999999] = {
                "fetched_at": datetime(2026, 5, 24, 15, 0, 0),
                "events": api_events,
            }
            api_provider._best_known_events_by_espn_fixture["737155"] = {
                "events": api_events,
                "goal_count": 1,
                "event_count": 1,
                "score_total_at_capture": 1,
                "source": "API-Football events",
                "api_fixture_id": 999999,
                "updated_at": datetime(2026, 5, 24, 15, 0, 0),
            }
            with (
                patch.object(api_provider, "italy_now", return_value=datetime(2026, 5, 24, 15, 5, 0)),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider.api_client, "fetch_fixture_events", AsyncMock(return_value={"response": []})) as fetch_events,
            ):
                enriched = await api_provider.enrich_fixture_events(None, match)
                return enriched, fetch_events

        enriched, fetch_events = asyncio.run(run())
        self.assertEqual(enriched["events"][0]["player"]["name"], "Scorer")
        self.assertEqual(fetch_events.await_count, 0)

    def test_ft_payload_uses_best_known_events_before_missing_note(self):
        from modules import api_provider

        match = espn_match(fixture_id="737157")
        match["fixture"]["status"]["short"] = "FT"
        match["teams"]["home"]["name"] = "AC Milan"
        match["teams"]["away"]["name"] = "Cagliari"
        match["goals"] = {"home": 1, "away": 2}
        match["events"] = [
            {
                "time": {"elapsed": 1},
                "player": {"name": "Alexis Saelemaekers"},
                "team": {"id": "50", "name": "AC Milan"},
                "type": "Goal",
                "detail": "Normal Goal",
            },
            {
                "time": {"elapsed": 19},
                "player": {"name": "Gennaro Borrelli"},
                "team": {"id": "51", "name": "Cagliari"},
                "type": "Goal",
                "detail": "Normal Goal",
            },
        ]
        best_events = [
            *match["events"],
            {
                "time": {"elapsed": 88},
                "player": {"name": "Late Scorer"},
                "team": {"id": "51", "name": "Cagliari"},
                "type": "Goal",
                "detail": "Normal Goal",
            },
        ]

        async def run():
            api_provider._reset_enrich_state_for_today()
            api_provider._best_known_events_by_espn_fixture["737157"] = {
                "events": best_events,
                "goal_count": 3,
                "event_count": 3,
                "score_total_at_capture": 3,
                "source": "API-Football events",
                "api_fixture_id": 1391198,
                "updated_at": datetime(2026, 5, 24, 22, 20, 0),
            }
            with patch.object(api_provider, "italy_now", return_value=datetime(2026, 5, 24, 22, 51, 0)):
                return await api_provider.enrich_fixture_events(None, match)

        enriched = asyncio.run(run())
        self.assertEqual(len([e for e in enriched["events"] if e.get("type") == "Goal"]), 3)
        self.assertEqual(enriched["events"][2]["player"]["name"], "Late Scorer")

    def test_complete_espn_snapshot_does_not_downgrade_richer_best_known_events(self):
        from modules import api_provider

        match = espn_match(fixture_id="737155")
        match["events"] = [
            {
                "time": {"elapsed": 26},
                "player": {"name": "Scorer"},
                "team": {"id": "50", "name": "Parma"},
                "type": "Goal",
                "detail": "Normal Goal",
            }
        ]
        best_events = [
            *match["events"],
            {
                "time": {"elapsed": 70},
                "player": {"name": "Sent Off"},
                "team": {"id": "51", "name": "Sassuolo"},
                "type": "Card",
                "detail": "Red Card",
            },
        ]

        async def run():
            api_provider._reset_enrich_state_for_today()
            api_provider._best_known_events_by_espn_fixture["737155"] = {
                "events": best_events,
                "goal_count": 1,
                "event_count": 2,
                "score_total_at_capture": 1,
                "source": "API-Football events",
                "api_fixture_id": 999999,
                "updated_at": datetime(2026, 5, 24, 15, 0, 0),
            }
            with patch.object(api_provider, "italy_now", return_value=datetime(2026, 5, 24, 15, 5, 0)):
                return await api_provider.enrich_fixture_events(None, match)

        enriched = asyncio.run(run())
        self.assertEqual(len(enriched["events"]), 2)
        self.assertEqual(enriched["events"][1]["detail"], "Red Card")

    def test_enrichment_does_not_use_espn_id_as_api_football_id(self):
        from modules import api_provider

        match = espn_match(fixture_id="737155")
        state = "737155:1:0:0"

        async def run():
            api_provider._reset_enrich_state_for_today()
            api_provider._enrich_retry_states[state] = {
                "first_seen": datetime(2026, 5, 24, 15, 0, 0),
                "attempt_count": 0,
                "last_attempt_at": None,
                "exhausted": False,
            }
            with (
                patch.object(api_provider, "italy_now", return_value=datetime(2026, 5, 24, 15, 1, 0)),
                patch.object(api_provider, "API_ENRICH_RETRY_DELAYS_SEC", [0]),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider, "resolve_api_football_fixture_id", AsyncMock(return_value=999999)),
                patch.object(api_provider.api_client, "fetch_fixture_events", AsyncMock(return_value={"response": []})) as fetch_events,
            ):
                await api_provider.enrich_fixture_events(None, match)
                return fetch_events

        fetch_events = asyncio.run(run())
        fetch_events.assert_awaited_once_with(None, 999999)

    def test_enrichment_retries_same_incomplete_state_after_delay(self):
        from modules import api_provider

        match = espn_match(fixture_id="737155")
        t0 = datetime(2026, 5, 24, 15, 0, 0)

        async def run():
            api_provider._reset_enrich_state_for_today()
            api_provider._enrich_retry_states.clear()
            fetch_events = AsyncMock(side_effect=[
                {"response": []},
                {"response": []},
            ])
            with (
                patch.object(api_provider, "API_ENRICH_RETRY_DELAYS_SEC", [0, 10]),
                patch.object(api_provider, "API_ENRICH_GRACE_SEC", 0),
                patch.object(api_provider, "API_ENRICH_INCOMPLETE_EVENTS_COOLDOWN_SEC", 0),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider, "resolve_api_football_fixture_id", AsyncMock(return_value=999999)),
                patch.object(api_provider.api_client, "fetch_fixture_events", fetch_events),
            ):
                with patch.object(api_provider, "italy_now", return_value=t0):
                    await api_provider.enrich_fixture_events(None, match)
                    await api_provider.enrich_fixture_events(None, match)
                with patch.object(api_provider, "italy_now", return_value=t0 + timedelta(seconds=11)):
                    await api_provider.enrich_fixture_events(None, match)
            return fetch_events.await_count

        self.assertEqual(asyncio.run(run()), 2)

    def test_enrichment_daily_budget_blocks_api_football_event_fetch(self):
        from modules import api_provider

        match = espn_match(fixture_id="737155")
        state = "737155:1:0:0"

        async def run():
            api_provider._reset_enrich_state_for_today()
            api_provider._enrich_retry_states[state] = {
                "first_seen": datetime(2026, 5, 24, 15, 0, 0),
                "attempt_count": 0,
                "last_attempt_at": None,
                "exhausted": False,
            }
            fetch_events = AsyncMock(return_value={"response": []})
            with (
                patch.object(api_provider, "italy_now", return_value=datetime(2026, 5, 24, 15, 1, 0)),
                patch.object(api_provider, "API_ENRICH_RETRY_DELAYS_SEC", [0]),
                patch.object(api_provider, "API_ENRICH_GRACE_SEC", 0),
                patch.object(api_provider, "API_ENRICH_DAILY_CALL_BUDGET", 0),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider, "resolve_api_football_fixture_id", AsyncMock(return_value=999999)),
                patch.object(api_provider.api_client, "fetch_fixture_events", fetch_events),
            ):
                await api_provider.enrich_fixture_events(None, match)
                return fetch_events

        fetch_events = asyncio.run(run())
        fetch_events.assert_not_awaited()

    def test_negative_mapping_cache_skips_repeated_live_mapping_calls(self):
        from modules import api_provider

        match = espn_match(fixture_id="737155")
        t0 = datetime(2026, 5, 24, 15, 0, 0)

        async def run():
            api_provider._reset_enrich_state_for_today()
            live_fetch = AsyncMock(return_value={"response": []})
            with (
                patch.object(api_provider, "italy_now", return_value=t0),
                patch.object(api_provider.api_client, "fetch_live_fixtures_payload", live_fetch),
            ):
                first = await api_provider.resolve_api_football_fixture_id(None, match)
                second = await api_provider.resolve_api_football_fixture_id(None, match)
                return first, second, live_fetch

        first, second, live_fetch = asyncio.run(run())
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(live_fetch.await_count, 1)

    def test_incomplete_api_football_events_obey_cooldown(self):
        from modules import api_provider

        match = espn_match(fixture_id="737155")
        state = "737155:1:0:0"
        t0 = datetime(2026, 5, 24, 15, 0, 0)

        async def run():
            api_provider._reset_enrich_state_for_today()
            api_provider._api_fixture_id_cache["737155"] = 999999
            api_provider._api_fixture_events_cache[999999] = {
                "fetched_at": t0,
                "events": [],
            }
            api_provider._enrich_retry_states[state] = {
                "first_seen": t0,
                "attempt_count": 0,
                "last_attempt_at": None,
                "exhausted": False,
            }
            fetch_events = AsyncMock(return_value={"response": []})
            with (
                patch.object(api_provider, "italy_now", return_value=t0 + timedelta(seconds=60)),
                patch.object(api_provider, "API_ENRICH_RETRY_DELAYS_SEC", [0]),
                patch.object(api_provider, "API_ENRICH_GRACE_SEC", 0),
                patch.object(api_provider, "API_ENRICH_INCOMPLETE_EVENTS_COOLDOWN_SEC", 180),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider.api_client, "fetch_fixture_events", fetch_events),
            ):
                enriched = await api_provider.enrich_fixture_events(None, match)
                return enriched, fetch_events

        enriched, fetch_events = asyncio.run(run())
        self.assertEqual(enriched["events"], [])
        fetch_events.assert_not_awaited()

    def test_best_known_reuse_logs_once_for_same_state(self):
        from modules import api_provider

        match = espn_match(fixture_id="737155")
        best_events = [
            {
                "time": {"elapsed": 26},
                "player": {"name": "Scorer"},
                "team": {"id": "50", "name": "Parma"},
                "type": "Goal",
                "detail": "Normal Goal",
            }
        ]

        async def run():
            api_provider._reset_enrich_state_for_today()
            api_provider._best_known_events_by_espn_fixture["737155"] = {
                "events": best_events,
                "goal_count": 1,
                "event_count": 1,
                "score_total_at_capture": 1,
                "source": "ESPN",
                "api_fixture_id": None,
                "updated_at": datetime(2026, 5, 24, 15, 0, 0),
            }
            with self.assertLogs("modules.api_provider", level="INFO") as logs:
                first = await api_provider.enrich_fixture_events(None, match)
                second = await api_provider.enrich_fixture_events(None, match)
            return first, second, logs.output

        first, second, output = asyncio.run(run())
        self.assertEqual(first["events"], best_events)
        self.assertEqual(second["events"], best_events)
        reuse_logs = [line for line in output if "Reusing best-known enriched events" in line]
        self.assertEqual(len(reuse_logs), 1)

    def test_partial_espn_cache_preserves_failed_league_matches(self):
        from modules import api_provider

        cached_match = espn_match(fixture_id="737155", league_id=135)
        fresh_match = espn_match(fixture_id="eng-1", league_id=39)

        async def fake_summary(session, slug_map, date_str=None):
            return {
                "matches": [fresh_match],
                "success_count": 1,
                "failure_count": 1,
                "succeeded_league_ids": [39],
                "failed_league_ids": [135],
            }

        async def run():
            today = api_provider.get_italy_date_string()
            api_provider._cache = [cached_match]
            api_provider._cache_date = today
            api_provider._cache_ts = api_provider.italy_now() - timedelta(seconds=api_provider.CACHE_TTL_SEC + 1)
            with patch.object(api_provider.espn_client, "fetch_all_leagues_with_summary", fake_summary):
                return await api_provider._get_cached_scoreboard(None)

        matches = asyncio.run(run())
        self.assertEqual({m["fixture"]["id"] for m in matches}, {"737155", "eng-1"})

    def test_enrichment_replaces_events_when_api_football_has_goal(self):
        from modules import api_provider

        match = espn_match(fixture_id="737155")
        state = "737155:1:0:0"
        api_goal = {
            "time": {"elapsed": 26},
            "player": {"name": "Scorer"},
            "team": {"id": 50, "name": "Parma"},
            "type": "Goal",
            "detail": "Normal Goal",
        }

        async def run():
            api_provider._reset_enrich_state_for_today()
            api_provider._enrich_retry_states[state] = {
                "first_seen": datetime(2026, 5, 24, 15, 0, 0),
                "attempt_count": 0,
                "last_attempt_at": None,
                "exhausted": False,
            }
            with (
                patch.object(api_provider, "italy_now", return_value=datetime(2026, 5, 24, 15, 1, 0)),
                patch.object(api_provider, "API_ENRICH_RETRY_DELAYS_SEC", [0]),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider, "resolve_api_football_fixture_id", AsyncMock(return_value=999999)),
                patch.object(api_provider.api_client, "fetch_fixture_events", AsyncMock(return_value={"response": [api_goal]})),
            ):
                return await api_provider.enrich_fixture_events(None, match)

        enriched = asyncio.run(run())
        self.assertEqual(enriched["events"][0]["type"], "Goal")
        self.assertEqual(enriched["events"][0]["player"]["name"], "Scorer")



if __name__ == "__main__":
    unittest.main()
