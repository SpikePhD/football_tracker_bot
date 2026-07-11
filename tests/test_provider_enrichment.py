import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
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
        from modules import match_state

        self._memory_tmp = tempfile.TemporaryDirectory()
        self._match_state_patch = patch.object(
            match_state,
            "BOT_MEMORY_DIR",
            Path(self._memory_tmp.name),
        )
        self._match_state_patch.start()

    def tearDown(self):
        self._match_state_patch.stop()
        self._memory_tmp.cleanup()

    def test_event_completeness_is_pending_before_enrichment_exhausts(self):
        from modules import api_provider

        match = espn_match(fixture_id="pending-events")
        match["goals"] = {"home": 2, "away": 0}
        match["events"] = []

        status = api_provider.event_completeness_status(match)

        self.assertEqual(status["status"], api_provider.EVENTS_PENDING_ENRICHMENT)
        self.assertEqual(status["missing_goals"], 2)

    def test_event_completeness_becomes_exhausted_after_final_retry(self):
        from modules import api_provider

        match = espn_match(fixture_id="exhausted-events")
        match["goals"] = {"home": 1, "away": 0}
        match["events"] = []
        t0 = datetime(2026, 5, 24, 15, 0, 0)

        async def run():
            with (
                patch.object(api_provider, "API_ENRICH_RETRY_DELAYS_SEC", [0]),
                patch.object(api_provider, "API_ENRICH_GRACE_SEC", 0),
                patch.object(api_provider, "bot_now", return_value=t0),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider, "resolve_api_football_fixture_id", AsyncMock(return_value=None)),
            ):
                await api_provider.enrich_fixture_events(None, match)
                await api_provider.enrich_fixture_events(None, match)
                return api_provider.event_completeness_status(match)

        status = asyncio.run(run())

        self.assertEqual(status["status"], api_provider.EVENTS_EXHAUSTED_MISSING)
        self.assertEqual(status["missing_goals"], 1)

    def test_event_completeness_persisted_exhausted_status_survives_reload(self):
        from modules import api_provider, match_state

        match = espn_match(fixture_id="persisted-exhausted")
        match["goals"] = {"home": 1, "away": 0}
        match["events"] = []
        status = api_provider.event_completeness_status(match)

        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            match_state.upsert_fixture_from_match(
                match,
                datetime(2026, 5, 24, 15, 0, tzinfo=api_provider.timezone.utc),
                memory_dir=memory_dir,
            )
            match_state.update_event_completeness(
                "persisted-exhausted",
                status["score_key"],
                api_provider.EVENTS_EXHAUSTED_MISSING,
                status["missing_goals"],
                memory_dir=memory_dir,
            )

            reset_api_provider_state()
            reloaded = api_provider.event_completeness_status(match, memory_dir=memory_dir)

        self.assertEqual(reloaded["status"], api_provider.EVENTS_EXHAUSTED_MISSING)

    def test_event_completeness_score_change_resets_exhausted_status(self):
        from modules import api_provider, match_state

        match = espn_match(fixture_id="score-reset")
        match["goals"] = {"home": 1, "away": 0}
        match["events"] = []
        old_status = api_provider.event_completeness_status(match)

        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            match_state.upsert_fixture_from_match(
                match,
                datetime(2026, 5, 24, 15, 0, tzinfo=api_provider.timezone.utc),
                memory_dir=memory_dir,
            )
            match_state.update_event_completeness(
                "score-reset",
                old_status["score_key"],
                api_provider.EVENTS_EXHAUSTED_MISSING,
                old_status["missing_goals"],
                memory_dir=memory_dir,
            )
            changed = {**match, "goals": {"home": 2, "away": 0}}

            status = api_provider.event_completeness_status(changed, memory_dir=memory_dir)

        self.assertEqual(status["status"], api_provider.EVENTS_PENDING_ENRICHMENT)
        self.assertEqual(status["missing_goals"], 2)

    def test_fallback_window_maps_api_football_fixture_to_cached_espn_fixture(self):
        from modules import api_provider, match_state

        espn = espn_match(fixture_id="760429", league_id=1)
        espn["fixture"]["date"] = "2026-06-15T22:00:00+00:00"
        espn["teams"]["home"]["name"] = "Saudi Arabia"
        espn["teams"]["away"]["name"] = "Uruguay"
        api_match = espn_match(fixture_id=1489379, league_id=1)
        api_match["fixture"]["date"] = "2026-06-15T22:00:00+00:00"
        api_match["fixture"]["status"] = {"short": "1H", "elapsed": 20}
        api_match["teams"]["home"]["name"] = "Saudi Arabia"
        api_match["teams"]["away"]["name"] = "Uruguay"

        async def run(memory_dir: Path):
            api_provider._football_scoreboard_cache["2026-06-15"] = {
                "matches": [espn],
                "fetched_at": datetime(2026, 6, 15, 22, 10),
            }
            with (
                patch.object(match_state, "BOT_MEMORY_DIR", memory_dir),
                patch.object(api_provider, "_should_try_espn_now", return_value=False),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider, "_fetch_api_football_date", AsyncMock(return_value=[api_match])),
            ):
                return await api_provider.fetch_football_window(
                    None,
                    datetime(2026, 6, 15, 20, 0),
                    datetime(2026, 6, 16, 0, 0),
                    now_utc=datetime(2026, 6, 15, 22, 30),
                )

        with tempfile.TemporaryDirectory() as tmp:
            matches = asyncio.run(run(Path(tmp)))

        self.assertEqual([m["fixture"]["id"] for m in matches], [1489379])
        self.assertEqual(matches[0]["provider"], "api_football")
        self.assertEqual(matches[0]["provider_fixture_id"], "1489379")
        self.assertEqual(matches[0]["canonical_fixture_id"], "760429")
        self.assertEqual(matches[0]["provider_ids"]["espn"], "760429")
        self.assertEqual(matches[0]["provider_ids"]["api_football"], "1489379")

    def test_fallback_window_dedupes_by_canonical_fixture_id(self):
        from modules import api_provider, match_state

        api_one = espn_match(fixture_id=1489379, league_id=1)
        api_one["fixture"]["date"] = "2026-06-15T22:00:00+00:00"
        api_one["canonical_fixture_id"] = "760429"
        api_one["provider"] = "api_football"
        api_one["provider_fixture_id"] = "1489379"
        api_two = espn_match(fixture_id=1489379, league_id=1)
        api_two["fixture"]["date"] = "2026-06-15T22:00:00+00:00"
        api_two["canonical_fixture_id"] = "760429"
        api_two["provider"] = "api_football"
        api_two["provider_fixture_id"] = "1489379"

        async def run(memory_dir: Path):
            with (
                patch.object(match_state, "BOT_MEMORY_DIR", memory_dir),
                patch.object(api_provider, "_should_try_espn_now", return_value=False),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider, "_fetch_api_football_date", AsyncMock(return_value=[api_one, api_two])),
            ):
                return await api_provider.fetch_football_window(
                    None,
                    datetime(2026, 6, 15, 20, 0),
                    datetime(2026, 6, 16, 0, 0),
                    now_utc=datetime(2026, 6, 15, 22, 30),
                )

        with tempfile.TemporaryDirectory() as tmp:
            matches = asyncio.run(run(Path(tmp)))

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["canonical_fixture_id"], "760429")

    def test_unmatched_api_football_fixture_keeps_provider_identity(self):
        from modules import api_provider, match_state

        api_match = espn_match(fixture_id=1489378, league_id=1)
        api_match["fixture"]["date"] = "2026-06-15T22:00:00+00:00"

        async def run(memory_dir: Path):
            with (
                patch.object(match_state, "BOT_MEMORY_DIR", memory_dir),
                patch.object(api_provider, "_should_try_espn_now", return_value=False),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider, "_fetch_api_football_date", AsyncMock(return_value=[api_match])),
            ):
                return await api_provider.fetch_football_window(
                    None,
                    datetime(2026, 6, 15, 20, 0),
                    datetime(2026, 6, 16, 0, 0),
                    now_utc=datetime(2026, 6, 15, 22, 30),
                )

        with tempfile.TemporaryDirectory() as tmp:
            matches = asyncio.run(run(Path(tmp)))

        self.assertEqual(matches[0]["provider"], "api_football")
        self.assertEqual(matches[0]["provider_fixture_id"], "1489378")
        self.assertNotIn("canonical_fixture_id", matches[0])

    def test_live_mapping_uses_api_football_live_feed_not_season_lookup(self):
        from modules import api_provider
        from modules import match_state

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

        async def run(memory_dir: Path):
            api_provider._reset_enrich_state_for_today()
            api_provider._api_fixture_id_cache.clear()
            with (
                patch.object(match_state, "BOT_MEMORY_DIR", memory_dir),
                patch.object(api_provider.api_client, "fetch_live_fixtures_payload", AsyncMock(return_value={"response": [other_live_fixture, live_fixture]})) as live_fetch,
                patch.object(api_provider.api_client, "_make_request", AsyncMock(return_value={"response": []})) as make_request,
            ):
                resolved = await api_provider.resolve_api_football_fixture_id(None, match)
                state = match_state.get_fixture_state("737155", memory_dir=memory_dir)
                canonical = match_state.find_canonical_fixture_id(
                    "api_football",
                    "999999",
                    memory_dir=memory_dir,
                )
                return resolved, live_fetch, make_request, state, canonical

        with tempfile.TemporaryDirectory() as tmp:
            resolved, live_fetch, make_request, state, canonical = asyncio.run(run(Path(tmp)))
        self.assertEqual(resolved, 999999)
        self.assertEqual(canonical, "737155")
        self.assertEqual(state["provider_ids"]["espn"], "737155")
        self.assertEqual(state["provider_ids"]["api_football"], "999999")
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
                patch.object(api_provider, "bot_now", return_value=datetime(2026, 5, 24, 15, 1, 0)),
                patch.object(api_provider.api_client, "fetch_live_fixtures_payload", AsyncMock(return_value=live_payload)) as live_fetch,
            ):
                first = await api_provider.resolve_api_football_fixture_id(None, match_one)
                second = await api_provider.resolve_api_football_fixture_id(None, match_two)
                return first, second, live_fetch

        first, second, live_fetch = asyncio.run(run())
        self.assertEqual(first, 999999)
        self.assertEqual(second, 999998)
        self.assertEqual(live_fetch.await_count, 1)

    def test_mapping_reuses_persisted_provider_alias_after_restart(self):
        from modules import api_provider, match_state

        match = espn_match(fixture_id="737155")
        t0 = datetime(2026, 5, 24, 15, 0, 0)

        async def run(memory_dir: Path):
            api_provider._reset_enrich_state_for_today()
            api_provider._api_fixture_id_cache.clear()
            api_provider._api_fixture_id_negative_cache["737155"] = {
                "expires_at": t0 + timedelta(seconds=900),
                "reason": "date/league lookup returned no candidates",
            }
            match_state.link_provider_fixture_id("737155", "espn", "737155", memory_dir=memory_dir)
            match_state.link_provider_fixture_id("737155", "api_football", "999999", memory_dir=memory_dir)
            with (
                patch.object(match_state, "BOT_MEMORY_DIR", memory_dir),
                patch.object(api_provider, "bot_now", return_value=t0),
                patch.object(api_provider.api_client, "fetch_live_fixtures_payload", AsyncMock(return_value={"response": []})) as live_fetch,
                patch.object(api_provider.api_client, "_make_request", AsyncMock(return_value={"response": []})) as make_request,
            ):
                resolved = await api_provider.resolve_api_football_fixture_id(None, match)
                return resolved, live_fetch, make_request, dict(api_provider._api_fixture_id_cache)

        with tempfile.TemporaryDirectory() as tmp:
            resolved, live_fetch, make_request, cache = asyncio.run(run(Path(tmp)))

        self.assertEqual(str(resolved), "999999")
        self.assertEqual(str(cache["737155"]), "999999")
        live_fetch.assert_not_awaited()
        make_request.assert_not_awaited()

    def test_prelink_live_fixture_stores_alias_without_fetching_events(self):
        from modules import api_provider, match_state

        match = espn_match(fixture_id="737155")
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
            ]
        }

        async def run():
            with (
                patch.object(api_provider.api_client, "fetch_live_fixtures_payload", AsyncMock(return_value=live_payload)) as live_fetch,
                patch.object(api_provider.api_client, "fetch_fixture_events", AsyncMock()) as event_fetch,
                patch.object(api_provider.api_client, "_make_request", AsyncMock(return_value={"response": []})) as make_request,
            ):
                resolved = await api_provider.prelink_live_api_football_fixture(None, match)
                state = match_state.get_fixture_state("737155")
                return resolved, state, live_fetch, event_fetch, make_request

        resolved, state, live_fetch, event_fetch, make_request = asyncio.run(run())

        self.assertEqual(resolved, 999999)
        self.assertEqual(state["provider_ids"]["espn"], "737155")
        self.assertEqual(state["provider_ids"]["api_football"], "999999")
        live_fetch.assert_awaited_once_with(None)
        event_fetch.assert_not_awaited()
        make_request.assert_not_awaited()

    def test_prelink_live_fixture_reuses_cached_live_payload_for_multiple_fixtures(self):
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
            with patch.object(
                api_provider.api_client,
                "fetch_live_fixtures_payload",
                AsyncMock(return_value=live_payload),
            ) as live_fetch:
                first = await api_provider.prelink_live_api_football_fixture(None, match_one)
                second = await api_provider.prelink_live_api_football_fixture(None, match_two)
                return first, second, live_fetch

        first, second, live_fetch = asyncio.run(run())

        self.assertEqual(first, 999999)
        self.assertEqual(second, 999998)
        self.assertEqual(live_fetch.await_count, 1)

    def test_prelink_live_fixture_skips_when_persisted_alias_exists(self):
        from modules import api_provider, match_state

        match = espn_match(fixture_id="737155")
        match_state.link_provider_fixture_id("737155", "espn", "737155")
        match_state.link_provider_fixture_id("737155", "api_football", "999999")

        async def run():
            with patch.object(
                api_provider.api_client,
                "fetch_live_fixtures_payload",
                AsyncMock(return_value={"response": []}),
            ) as live_fetch:
                resolved = await api_provider.prelink_live_api_football_fixture(None, match)
                return resolved, live_fetch

        resolved, live_fetch = asyncio.run(run())

        self.assertEqual(resolved, 999999)
        live_fetch.assert_not_awaited()

    def test_prelink_live_fixture_cooldowns_failed_mapping(self):
        from modules import api_provider

        match = espn_match(fixture_id="737155")
        t0 = datetime(2026, 5, 24, 15, 0, 0)

        async def run():
            with (
                patch.object(api_provider, "bot_now", return_value=t0),
                patch.object(
                    api_provider.api_client,
                    "fetch_live_fixtures_payload",
                    AsyncMock(return_value={"response": []}),
                ) as live_fetch,
            ):
                first = await api_provider.prelink_live_api_football_fixture(None, match)
                second = await api_provider.prelink_live_api_football_fixture(None, match)
                return first, second, live_fetch

        first, second, live_fetch = asyncio.run(run())

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(live_fetch.await_count, 1)

    def test_enrichment_uses_persisted_prelinked_alias_without_date_lookup(self):
        from modules import api_provider, match_state

        match = espn_match(fixture_id="737155")
        match["goals"] = {"home": 1, "away": 0}
        match["events"] = []
        match_state.link_provider_fixture_id("737155", "espn", "737155")
        match_state.link_provider_fixture_id("737155", "api_football", "999999")
        api_events = [
            {
                "type": "Goal",
                "detail": "Normal Goal",
                "player": {"name": "Scorer"},
                "team": {"id": "50", "name": "Parma"},
                "time": {"elapsed": 8},
            }
        ]

        async def run():
            api_provider._enrich_retry_states[api_provider._event_retry_state_key(match, match["events"])] = {
                "first_seen": datetime(2026, 5, 24, 15, 0, 0),
                "attempt_count": 0,
                "last_attempt_at": None,
                "exhausted": False,
            }
            with (
                patch.object(api_provider, "bot_now", return_value=datetime(2026, 5, 24, 15, 2, 0)),
                patch.object(api_provider, "API_ENRICH_GRACE_SEC", 0),
                patch.object(api_provider, "API_ENRICH_RETRY_DELAYS_SEC", [0]),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider.api_client, "_make_request", AsyncMock(return_value={"response": []})) as make_request,
                patch.object(api_provider.api_client, "fetch_fixture_events", AsyncMock(return_value={"response": api_events})) as event_fetch,
            ):
                enriched = await api_provider.enrich_fixture_events(None, match)
                return enriched, make_request, event_fetch

        enriched, make_request, event_fetch = asyncio.run(run())

        self.assertEqual(enriched["events"][0]["player"]["name"], "Scorer")
        make_request.assert_not_awaited()
        event_fetch.assert_awaited_once_with(None, 999999)

    def test_live_mapping_accepts_configured_national_team_aliases(self):
        from modules import api_provider

        match = espn_match(fixture_id="760414", league_id=1)
        match["fixture"]["date"] = "2026-06-12T02:00:00+00:00"
        match["teams"]["home"]["name"] = "South Korea"
        match["teams"]["away"]["name"] = "Czechia"
        live_payload = {
            "response": [
                {
                    "fixture": {"id": 1400414, "date": "2026-06-12T02:00:00+00:00"},
                    "league": {"id": 1},
                    "teams": {
                        "home": {"name": "Korea Republic"},
                        "away": {"name": "Czech Republic"},
                    },
                },
            ]
        }

        async def run():
            with patch.object(
                api_provider.api_client,
                "fetch_live_fixtures_payload",
                AsyncMock(return_value=live_payload),
            ):
                return await api_provider.resolve_api_football_fixture_id(None, match)

        self.assertEqual(asyncio.run(run()), 1400414)

    def test_live_mapping_rejects_alias_match_in_wrong_league(self):
        from modules import api_provider

        match = espn_match(fixture_id="760414", league_id=1)
        match["fixture"]["date"] = "2026-06-12T02:00:00+00:00"
        match["teams"]["home"]["name"] = "South Korea"
        match["teams"]["away"]["name"] = "Czechia"
        live_payload = {
            "response": [
                {
                    "fixture": {"id": 1400414, "date": "2026-06-12T02:00:00+00:00"},
                    "league": {"id": 999},
                    "teams": {
                        "home": {"name": "Korea Republic"},
                        "away": {"name": "Czech Republic"},
                    },
                },
            ]
        }

        async def run():
            with patch.object(
                api_provider.api_client,
                "fetch_live_fixtures_payload",
                AsyncMock(return_value=live_payload),
            ):
                return await api_provider.resolve_api_football_fixture_id(None, match)

        self.assertIsNone(asyncio.run(run()))

    def test_live_mapping_rejects_alias_match_with_large_kickoff_delta(self):
        from modules import api_provider

        match = espn_match(fixture_id="760414", league_id=1)
        match["fixture"]["date"] = "2026-06-12T02:00:00+00:00"
        match["teams"]["home"]["name"] = "South Korea"
        match["teams"]["away"]["name"] = "Czechia"
        live_payload = {
            "response": [
                {
                    "fixture": {"id": 1400414, "date": "2026-06-12T05:00:00+00:00"},
                    "league": {"id": 1},
                    "teams": {
                        "home": {"name": "Korea Republic"},
                        "away": {"name": "Czech Republic"},
                    },
                },
            ]
        }

        async def run():
            with patch.object(
                api_provider.api_client,
                "fetch_live_fixtures_payload",
                AsyncMock(return_value=live_payload),
            ):
                return await api_provider.resolve_api_football_fixture_id(None, match)

        self.assertIsNone(asyncio.run(run()))

    def test_live_mapping_rejects_low_confidence_team_names(self):
        from modules import api_provider

        match = espn_match(fixture_id="760414", league_id=1)
        match["fixture"]["date"] = "2026-06-12T02:00:00+00:00"
        match["teams"]["home"]["name"] = "South Korea"
        match["teams"]["away"]["name"] = "Czechia"
        live_payload = {
            "response": [
                {
                    "fixture": {"id": 1400414, "date": "2026-06-12T02:00:00+00:00"},
                    "league": {"id": 1},
                    "teams": {
                        "home": {"name": "Canada"},
                        "away": {"name": "Bosnia-Herzegovina"},
                    },
                },
            ]
        }

        async def run():
            with patch.object(
                api_provider.api_client,
                "fetch_live_fixtures_payload",
                AsyncMock(return_value=live_payload),
            ):
                return await api_provider.resolve_api_football_fixture_id(None, match)

        self.assertIsNone(asyncio.run(run()))

    def test_failed_live_mapping_logs_debug_candidate_diagnostics(self):
        from modules import api_provider

        match = espn_match(fixture_id="760414", league_id=1)
        match["fixture"]["date"] = "2026-06-12T02:00:00+00:00"
        match["teams"]["home"]["name"] = "South Korea"
        match["teams"]["away"]["name"] = "Czechia"
        live_payload = {
            "response": [
                {
                    "fixture": {"id": 1400414, "date": "2026-06-12T02:00:00+00:00"},
                    "league": {"id": 1},
                    "teams": {
                        "home": {"name": "Canada"},
                        "away": {"name": "Bosnia-Herzegovina"},
                    },
                },
            ]
        }

        async def run():
            with (
                patch.object(
                    api_provider.api_client,
                    "fetch_live_fixtures_payload",
                    AsyncMock(return_value=live_payload),
                ),
                self.assertLogs("modules.api_provider", level="DEBUG") as logs,
            ):
                resolved = await api_provider.resolve_api_football_fixture_id(None, match)
                return resolved, logs.output

        resolved, logs = asyncio.run(run())
        self.assertIsNone(resolved)
        diagnostic_lines = [line for line in logs if "API-Football mapping candidate" in line]
        self.assertTrue(diagnostic_lines)
        self.assertIn("home_score=", diagnostic_lines[0])
        self.assertIn("reject_reason=", diagnostic_lines[0])

    def test_enrichment_uses_alias_mapping_to_fill_missing_goal_event(self):
        from modules import api_provider

        match = espn_match(fixture_id="760414", league_id=1)
        match["fixture"]["date"] = "2026-06-12T02:00:00+00:00"
        match["teams"]["home"]["name"] = "South Korea"
        match["teams"]["away"]["name"] = "Czechia"
        match["goals"] = {"home": 0, "away": 1}
        match["events"] = []
        live_payload = {
            "response": [
                {
                    "fixture": {"id": 1400414, "date": "2026-06-12T02:00:00+00:00"},
                    "league": {"id": 1},
                    "teams": {
                        "home": {"name": "Korea Republic"},
                        "away": {"name": "Czech Republic"},
                    },
                },
            ]
        }
        api_goal = {
            "time": {"elapsed": 22},
            "player": {"name": "Czech Scorer"},
            "team": {"id": 200, "name": "Czech Republic"},
            "type": "Goal",
            "detail": "Normal Goal",
        }

        async def run():
            with (
                patch.object(api_provider, "bot_now", return_value=datetime(2026, 6, 12, 4, 1, 0)),
                patch.object(api_provider, "API_ENRICH_RETRY_DELAYS_SEC", [0]),
                patch.object(api_provider, "API_ENRICH_GRACE_SEC", 0),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider.api_client, "fetch_live_fixtures_payload", AsyncMock(return_value=live_payload)),
                patch.object(api_provider.api_client, "fetch_fixture_events", AsyncMock(return_value={"response": [api_goal]})),
            ):
                await api_provider.enrich_fixture_events(None, match)
                return await api_provider.enrich_fixture_events(None, match)

        enriched = asyncio.run(run())
        self.assertEqual(enriched["events"][0]["player"]["name"], "Czech Scorer")

    def test_enrichment_canonicalizes_api_football_event_team_to_espn_side(self):
        from modules import api_provider

        match = espn_match(fixture_id="760414", league_id=1)
        match["fixture"]["date"] = "2026-06-12T02:00:00+00:00"
        match["teams"]["home"] = {"id": "espn-home", "name": "South Korea"}
        match["teams"]["away"] = {"id": "espn-away", "name": "Czechia"}
        match["goals"] = {"home": 0, "away": 1}
        match["events"] = []
        api_provider._api_fixture_id_cache["760414"] = 1400414
        live_payload = {
            "response": [
                {
                    "fixture": {"id": 1400414, "date": "2026-06-12T02:00:00+00:00"},
                    "league": {"id": 1},
                    "teams": {
                        "home": {"id": 10, "name": "Korea Republic"},
                        "away": {"id": 20, "name": "Czech Republic"},
                    },
                },
            ]
        }
        api_goal = {
            "time": {"elapsed": 22},
            "player": {"name": "Czech Scorer"},
            "team": {"id": 20, "name": "Czech Republic"},
            "type": "Goal",
            "detail": "Normal Goal",
        }

        async def run():
            with (
                patch.object(api_provider, "bot_now", return_value=datetime(2026, 6, 12, 4, 1, 0)),
                patch.object(api_provider, "API_ENRICH_RETRY_DELAYS_SEC", [0]),
                patch.object(api_provider, "API_ENRICH_GRACE_SEC", 0),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider.api_client, "fetch_live_fixtures_payload", AsyncMock(return_value=live_payload)),
                patch.object(api_provider.api_client, "fetch_fixture_events", AsyncMock(return_value={"response": [api_goal]})),
            ):
                await api_provider.enrich_fixture_events(None, match)
                return await api_provider.enrich_fixture_events(None, match)

        enriched = asyncio.run(run())

        self.assertEqual(enriched["events"][0]["team"], {"id": "espn-away", "name": "Czechia"})

    def test_cached_api_football_events_are_canonicalized_before_reuse(self):
        from modules import api_provider

        match = espn_match(fixture_id="760414", league_id=1)
        match["fixture"]["date"] = "2026-06-12T02:00:00+00:00"
        match["teams"]["home"] = {"id": "espn-home", "name": "South Korea"}
        match["teams"]["away"] = {"id": "espn-away", "name": "Czechia"}
        match["goals"] = {"home": 0, "away": 1}
        match["events"] = []
        api_provider._api_fixture_id_cache["760414"] = 1400414
        api_provider._api_fixture_events_cache[1400414] = {
            "fetched_at": datetime(2026, 6, 12, 4, 0, 0),
            "events": [
                {
                    "time": {"elapsed": 22},
                    "player": {"name": "Czech Scorer"},
                    "team": {"id": 20, "name": "Czech Republic"},
                    "type": "Goal",
                    "detail": "Normal Goal",
                }
            ],
        }

        async def run():
            with (
                patch.object(api_provider, "bot_now", return_value=datetime(2026, 6, 12, 4, 1, 0)),
                patch.object(api_provider, "API_ENRICH_RETRY_DELAYS_SEC", [0]),
                patch.object(api_provider, "API_ENRICH_GRACE_SEC", 0),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider, "resolve_api_football_fixture_id", AsyncMock(return_value=None)),
                patch.object(api_provider.api_client, "fetch_fixture_events", AsyncMock()) as fetch_events,
            ):
                await api_provider.enrich_fixture_events(None, match)
                enriched = await api_provider.enrich_fixture_events(None, match)
                return enriched, fetch_events

        enriched, fetch_events = asyncio.run(run())

        fetch_events.assert_not_awaited()
        self.assertEqual(enriched["events"][0]["team"], {"id": "espn-away", "name": "Czechia"})

    def test_enrichment_merges_complementary_partial_goal_events(self):
        from modules import api_provider

        match = espn_match(fixture_id="760509", league_id=1)
        match["teams"]["home"] = {"id": "100", "name": "Argentina"}
        match["teams"]["away"] = {"id": "200", "name": "Egypt"}
        match["goals"] = {"home": 0, "away": 2}
        match["events"] = [
            {
                "time": {"elapsed": 57},
                "player": {"name": "Mostafa Zico"},
                "team": {"id": "200", "name": "Egypt"},
                "type": "Goal",
                "detail": "Normal Goal",
            }
        ]
        best_known = {
            "events": [
                {
                    "time": {"elapsed": 15},
                    "player": {"name": "Y. Ibrahim"},
                    "team": {"id": "200", "name": "Egypt"},
                    "type": "Goal",
                    "detail": "Normal Goal",
                }
            ],
            "goal_count": 1,
            "event_count": 1,
            "score_total_at_capture": 1,
            "source": "API-Football events",
            "api_fixture_id": 1576804,
            "updated_at": datetime(2026, 7, 7, 18, 18, 26),
        }
        cached_api_events = {
            "fetched_at": datetime(2026, 7, 7, 19, 24, 37),
            "events": [
                {
                    "time": {"elapsed": 15},
                    "player": {"name": "Y. Ibrahim"},
                    "team": {"id": "200", "name": "Egypt"},
                    "type": "Goal",
                    "detail": "Normal Goal",
                }
            ],
        }

        async def run():
            with (
                patch.object(api_provider, "bot_now", return_value=datetime(2026, 7, 7, 19, 25, 0)),
                patch.object(api_provider, "API_ENRICH_RETRY_DELAYS_SEC", [0]),
                patch.object(api_provider, "API_ENRICH_GRACE_SEC", 0),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider, "resolve_api_football_fixture_id", AsyncMock(return_value=None)),
                patch.object(api_provider.api_client, "fetch_fixture_events", AsyncMock()) as fetch_events,
            ):
                api_provider._reset_enrich_state_for_today()
                api_provider._api_fixture_id_cache["760509"] = 1576804
                api_provider._best_known_events_by_espn_fixture["760509"] = best_known
                api_provider._api_fixture_events_cache[1576804] = cached_api_events
                await api_provider.enrich_fixture_events(None, match)
                enriched = await api_provider.enrich_fixture_events(None, match)
                return enriched, fetch_events

        enriched, fetch_events = asyncio.run(run())

        fetch_events.assert_not_awaited()
        self.assertEqual(enriched["_event_completeness"]["status"], api_provider.EVENTS_COMPLETE)
        self.assertEqual(
            [event["player"]["name"] for event in enriched["events"] if event.get("type") == "Goal"],
            ["Y. Ibrahim", "Mostafa Zico"],
        )

    def test_enrichment_merges_complementary_api_refresh_and_espn_events(self):
        from modules import api_provider

        match = espn_match(fixture_id="760509", league_id=1)
        match["teams"]["home"] = {"id": "100", "name": "Argentina"}
        match["teams"]["away"] = {"id": "200", "name": "Egypt"}
        match["goals"] = {"home": 0, "away": 2}
        match["events"] = [
            {
                "time": {"elapsed": 57},
                "player": {"name": "Mostafa Zico"},
                "team": {"id": "200", "name": "Egypt"},
                "type": "Goal",
                "detail": "Normal Goal",
            }
        ]
        api_goal = {
            "time": {"elapsed": 15},
            "player": {"name": "Y. Ibrahim"},
            "team": {"id": 20, "name": "Egypt"},
            "type": "Goal",
            "detail": "Normal Goal",
        }

        async def run():
            with (
                patch.object(api_provider, "bot_now", return_value=datetime(2026, 7, 7, 19, 25, 0)),
                patch.object(api_provider, "API_ENRICH_RETRY_DELAYS_SEC", [0]),
                patch.object(api_provider, "API_ENRICH_GRACE_SEC", 0),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider, "resolve_api_football_fixture_id", AsyncMock(return_value=1576804)),
                patch.object(api_provider.api_client, "fetch_fixture_events", AsyncMock(return_value={"response": [api_goal]})),
            ):
                api_provider._reset_enrich_state_for_today()
                api_provider._api_fixture_id_cache["760509"] = 1576804
                await api_provider.enrich_fixture_events(None, match)
                return await api_provider.enrich_fixture_events(None, match)

        enriched = asyncio.run(run())

        self.assertEqual(enriched["_event_completeness"]["status"], api_provider.EVENTS_COMPLETE)
        self.assertEqual(
            [event["player"]["name"] for event in enriched["events"] if event.get("type") == "Goal"],
            ["Y. Ibrahim", "Mostafa Zico"],
        )

    def test_complementary_merge_dedupes_duplicate_goal_events(self):
        from modules import api_provider

        match = espn_match(fixture_id="dedupe-complementary", league_id=1)
        match["teams"]["home"] = {"id": "100", "name": "Argentina"}
        match["teams"]["away"] = {"id": "200", "name": "Egypt"}
        match["goals"] = {"home": 0, "away": 1}
        match["events"] = [
            {
                "time": {"elapsed": 15},
                "player": {"name": "Y. Ibrahim"},
                "team": {"id": "200", "name": "Egypt"},
                "type": "Goal",
                "detail": "Normal Goal",
            }
        ]
        api_provider._best_known_events_by_espn_fixture["dedupe-complementary"] = {
            "events": list(match["events"]),
            "goal_count": 1,
            "event_count": 1,
            "score_total_at_capture": 1,
            "source": "API-Football events",
            "api_fixture_id": 1576804,
            "updated_at": datetime(2026, 7, 7, 18, 18, 26),
        }

        enriched = asyncio.run(api_provider.enrich_fixture_events(None, match))

        counted_goals = [event for event in enriched["events"] if event.get("type") == "Goal"]
        self.assertEqual(len(counted_goals), 1)
        self.assertEqual(counted_goals[0]["player"]["name"], "Y. Ibrahim")

    def test_complementary_merge_keeps_pending_status_when_still_incomplete(self):
        from modules import api_provider

        match = espn_match(fixture_id="partial-still-incomplete", league_id=1)
        match["teams"]["home"] = {"id": "100", "name": "Argentina"}
        match["teams"]["away"] = {"id": "200", "name": "Egypt"}
        match["goals"] = {"home": 0, "away": 3}
        match["events"] = [
            {
                "time": {"elapsed": 57},
                "player": {"name": "Mostafa Zico"},
                "team": {"id": "200", "name": "Egypt"},
                "type": "Goal",
                "detail": "Normal Goal",
            }
        ]
        api_provider._best_known_events_by_espn_fixture["partial-still-incomplete"] = {
            "events": [
                {
                    "time": {"elapsed": 15},
                    "player": {"name": "Y. Ibrahim"},
                    "team": {"id": "200", "name": "Egypt"},
                    "type": "Goal",
                    "detail": "Normal Goal",
                }
            ],
            "goal_count": 1,
            "event_count": 1,
            "score_total_at_capture": 1,
            "source": "API-Football events",
            "api_fixture_id": 1576804,
            "updated_at": datetime(2026, 7, 7, 18, 18, 26),
        }

        async def run():
            with patch.object(api_provider, "resolve_api_football_fixture_id", AsyncMock(return_value=None)):
                return await api_provider.enrich_fixture_events(None, match)

        enriched = asyncio.run(run())

        self.assertEqual(enriched["_event_completeness"]["status"], api_provider.EVENTS_PENDING_ENRICHMENT)
        self.assertEqual(enriched["_event_completeness"]["missing_goals"], 1)
        self.assertEqual(
            [event["player"]["name"] for event in enriched["events"] if event.get("type") == "Goal"],
            ["Y. Ibrahim", "Mostafa Zico"],
        )

    def test_complementary_merge_prunes_voided_goal_after_score_rollback(self):
        from modules import api_provider

        match = espn_match(fixture_id="760509", league_id=1)
        match["teams"]["home"] = {"id": "100", "name": "Argentina"}
        match["teams"]["away"] = {"id": "200", "name": "Egypt"}
        match["goals"] = {"home": 0, "away": 1}
        match["events"] = [
            {
                "time": {"elapsed": 15},
                "player": {"name": "Y. Ibrahim"},
                "team": {"id": "200", "name": "Egypt"},
                "type": "Goal",
                "detail": "Normal Goal",
            }
        ]
        api_provider._best_known_events_by_espn_fixture["760509"] = {
            "events": [
                {
                    "time": {"elapsed": 15},
                    "player": {"name": "Y. Ibrahim"},
                    "team": {"id": "200", "name": "Egypt"},
                    "type": "Goal",
                    "detail": "Normal Goal",
                },
                {
                    "time": {"elapsed": 57},
                    "player": {"name": "Mostafa Zico"},
                    "team": {"id": "200", "name": "Egypt"},
                    "type": "Goal",
                    "detail": "Normal Goal",
                },
            ],
            "goal_count": 2,
            "event_count": 2,
            "score_total_at_capture": 2,
            "source": "merged complementary events",
            "api_fixture_id": 1576804,
            "updated_at": datetime(2026, 7, 7, 19, 24, 37),
        }

        enriched = asyncio.run(api_provider.enrich_fixture_events(None, match))

        self.assertEqual(enriched["goals"], {"home": 0, "away": 1})
        self.assertEqual(
            [event["player"]["name"] for event in enriched["events"] if event.get("type") == "Goal"],
            ["Y. Ibrahim"],
        )

    def test_non_scoring_goal_details_do_not_satisfy_complementary_merge(self):
        from modules import api_provider

        match = espn_match(fixture_id="non-scoring-complementary", league_id=1)
        match["teams"]["home"] = {"id": "100", "name": "Brazil"}
        match["teams"]["away"] = {"id": "200", "name": "Norway"}
        match["goals"] = {"home": 1, "away": 2}
        match["events"] = [
            {
                "time": {"elapsed": 14},
                "player": {"name": "Bruno Guimaraes"},
                "team": {"id": "100", "name": "Brazil"},
                "type": "Goal",
                "detail": "Missed Penalty",
            },
            {
                "time": {"elapsed": 79},
                "player": {"name": "E. Haaland"},
                "team": {"id": "200", "name": "Norway"},
                "type": "Goal",
                "detail": "Normal Goal",
            },
            {
                "time": {"elapsed": 90},
                "player": {"name": "E. Haaland"},
                "team": {"id": "200", "name": "Norway"},
                "type": "Goal",
                "detail": "Normal Goal",
            },
        ]

        async def run():
            with patch.object(api_provider, "resolve_api_football_fixture_id", AsyncMock(return_value=None)):
                return await api_provider.enrich_fixture_events(None, match)

        enriched = asyncio.run(run())

        self.assertEqual(enriched["_event_completeness"]["status"], api_provider.EVENTS_PENDING_ENRICHMENT)
        self.assertEqual(enriched["_event_completeness"]["missing_goals"], 1)
        self.assertEqual(
            [event["player"]["name"] for event in enriched["events"] if api_provider.is_counted_goal_event(event)],
            ["E. Haaland", "E. Haaland"],
        )

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
                patch.object(api_provider, "bot_now", return_value=datetime(2026, 5, 24, 15, 1, 0)),
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
                patch.object(api_provider, "bot_now", return_value=datetime(2026, 5, 24, 15, 5, 0)),
                patch.object(api_provider.api_client, "is_quota_exceeded_today", return_value=False),
                patch.object(api_provider.api_client, "fetch_fixture_events", AsyncMock(return_value={"response": []})) as fetch_events,
            ):
                enriched = await api_provider.enrich_fixture_events(None, match)
                return enriched, fetch_events

        enriched, fetch_events = asyncio.run(run())
        self.assertEqual(enriched["events"][0]["player"]["name"], "Scorer")
        self.assertEqual(fetch_events.await_count, 0)

    def test_best_known_goal_events_are_not_reused_after_score_rollback_to_nil(self):
        from modules import api_provider

        match = espn_match(fixture_id="voided-goal")
        match["teams"]["home"] = {"id": "50", "name": "Belgium"}
        match["teams"]["away"] = {"id": "51", "name": "Iran"}
        match["goals"] = {"home": 0, "away": 0}
        match["events"] = []
        stale_goal = {
            "time": {"elapsed": 24},
            "player": {"name": "Mehdi Taremi"},
            "team": {"id": "51", "name": "Iran"},
            "type": "Goal",
            "detail": "Normal Goal",
        }

        async def run():
            api_provider._reset_enrich_state_for_today()
            api_provider._best_known_events_by_espn_fixture["voided-goal"] = {
                "events": [stale_goal],
                "goal_count": 1,
                "event_count": 1,
                "score_total_at_capture": 1,
                "source": "ESPN",
                "api_fixture_id": None,
                "updated_at": datetime(2026, 6, 21, 21, 26, 0),
            }
            with patch.object(api_provider, "bot_now", return_value=datetime(2026, 6, 21, 21, 30, 0)):
                return await api_provider.enrich_fixture_events(None, match)

        enriched = asyncio.run(run())
        self.assertEqual(enriched["goals"], {"home": 0, "away": 0})
        self.assertEqual(
            [event for event in enriched["events"] if event.get("type") == "Goal"],
            [],
        )

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
            with patch.object(api_provider, "bot_now", return_value=datetime(2026, 5, 24, 22, 51, 0)):
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
            with patch.object(api_provider, "bot_now", return_value=datetime(2026, 5, 24, 15, 5, 0)):
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
                patch.object(api_provider, "bot_now", return_value=datetime(2026, 5, 24, 15, 1, 0)),
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
                with patch.object(api_provider, "bot_now", return_value=t0):
                    await api_provider.enrich_fixture_events(None, match)
                    await api_provider.enrich_fixture_events(None, match)
                with patch.object(api_provider, "bot_now", return_value=t0 + timedelta(seconds=11)):
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
                patch.object(api_provider, "bot_now", return_value=datetime(2026, 5, 24, 15, 1, 0)),
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
                patch.object(api_provider, "bot_now", return_value=t0),
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
                patch.object(api_provider, "bot_now", return_value=t0 + timedelta(seconds=60)),
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
            today = api_provider.get_bot_local_date_string()
            api_provider._cache = [cached_match]
            api_provider._cache_date = today
            api_provider._cache_ts = api_provider.bot_now() - timedelta(seconds=api_provider.CACHE_TTL_SEC + 1)
            with patch.object(api_provider.espn_client, "fetch_all_leagues_with_summary", fake_summary):
                return await api_provider._get_cached_scoreboard(None)

        matches = asyncio.run(run())
        self.assertEqual({m["fixture"]["id"] for m in matches}, {"737155", "eng-1"})

    def test_partial_espn_cache_warning_logs_once_per_date_and_failed_leagues(self):
        from modules import api_provider

        provider_date = "2026-06-14"
        cached_match = espn_match(fixture_id="cached", league_id=135)
        fresh_match = espn_match(fixture_id="fresh", league_id=39)

        async def fake_summary(session, slug_map, date_str=None):
            return {
                "matches": [fresh_match],
                "success_count": 1,
                "failure_count": 1,
                "succeeded_league_ids": [39],
                "failed_league_ids": [135],
            }

        async def run():
            api_provider._football_scoreboard_cache.clear()
            api_provider._espn_partial_refresh_warning_log_keys.clear()
            stale_ts = api_provider.bot_now() - timedelta(seconds=api_provider.CACHE_TTL_SEC + 1)
            api_provider._football_scoreboard_cache[provider_date] = {
                "matches": [cached_match],
                "fetched_at": stale_ts,
            }
            with patch.object(api_provider.espn_client, "fetch_all_leagues_with_summary", fake_summary):
                with self.assertLogs("modules.api_provider", level="WARNING") as first_logs:
                    first = await api_provider._get_cached_scoreboard_for_date(None, provider_date)
                api_provider._football_scoreboard_cache[provider_date]["fetched_at"] = stale_ts
                with self.assertLogs("modules.api_provider", level="DEBUG") as second_logs:
                    second = await api_provider._get_cached_scoreboard_for_date(None, provider_date)
            return first, second, first_logs.output, second_logs.output

        first, second, first_output, second_output = asyncio.run(run())

        self.assertEqual({m["fixture"]["id"] for m in first}, {"cached", "fresh"})
        self.assertEqual({m["fixture"]["id"] for m in second}, {"cached", "fresh"})
        self.assertEqual(sum("ESPN partial refresh merged with stale cache" in line for line in first_output), 1)
        self.assertEqual(sum("ESPN partial refresh merged with stale cache" in line for line in second_output), 1)
        self.assertTrue(all("WARNING" not in line for line in second_output))

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
                patch.object(api_provider, "bot_now", return_value=datetime(2026, 5, 24, 15, 1, 0)),
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
