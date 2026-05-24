import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class RegressionTests(unittest.TestCase):
    def test_api_football_event_normalization_preserves_team_id(self):
        from utils.event_formatter import normalize_api_football_events

        events = normalize_api_football_events([
            {
                "time": {"elapsed": 12},
                "player": {"name": "Scorer"},
                "team": {"id": 10, "name": "Home"},
                "type": "Goal",
                "detail": "Normal Goal",
            }
        ])

        self.assertEqual(events[0]["team"], {"id": 10, "name": "Home"})

    def test_espn_normalization_preserves_team_ids_on_match_and_events(self):
        from utils import espn_client

        match = espn_client._normalize_event(
            {
                "id": "fixture-1",
                "date": "2026-05-24T18:00Z",
                "status": {
                    "period": 2,
                    "displayClock": "77:00",
                    "type": {"state": "in", "description": "77'", "name": "STATUS_IN_PROGRESS"},
                },
                "competitions": [
                    {
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "1",
                                "team": {"id": "100", "displayName": "Home"},
                            },
                            {
                                "homeAway": "away",
                                "score": "0",
                                "team": {"id": "200", "displayName": "Away"},
                            },
                        ],
                        "details": [
                            {
                                "type": {"text": "Goal"},
                                "clock": {"value": 720},
                                "team": {"id": "100"},
                                "athletesInvolved": [{"fullName": "Scorer"}],
                            }
                        ],
                    }
                ],
            },
            135,
        )

        self.assertEqual(match["teams"]["home"]["id"], "100")
        self.assertEqual(match["teams"]["away"]["id"], "200")
        self.assertEqual(match["events"][0]["team"], {"id": "100", "name": "Home"})

    def test_espn_all_leagues_summary_distinguishes_success_and_failure(self):
        from utils import espn_client

        async def fake_fetch_scoreboard_result(session, slug, date_str=None):
            if slug == "bad":
                return {"ok": False, "events": []}
            return {"ok": True, "events": []}

        async def run():
            with patch.object(espn_client, "fetch_scoreboard_result", fake_fetch_scoreboard_result):
                return await espn_client.fetch_all_leagues_with_summary(
                    session=None,
                    slug_map={1: "ok", 2: "bad"},
                    date_str="20260524",
                )

        summary = asyncio.run(run())
        self.assertEqual(summary["matches"], [])
        self.assertEqual(summary["success_count"], 1)
        self.assertEqual(summary["failure_count"], 1)
        self.assertEqual(summary["succeeded_league_ids"], [1])
        self.assertEqual(summary["failed_league_ids"], [2])

    def test_fetch_fixture_events_uses_events_endpoint(self):
        from utils import api_client

        captured = {}

        async def fake_make_request(session, url):
            captured["url"] = url
            return {"response": []}

        async def run():
            with patch.object(api_client, "_make_request", fake_make_request):
                return await api_client.fetch_fixture_events(None, 123)

        payload = asyncio.run(run())
        self.assertEqual(payload, {"response": []})
        self.assertIn("/fixtures/events?fixture=123", captured["url"])

    def test_fetch_live_fixtures_payload_uses_live_all_endpoint(self):
        from utils import api_client

        captured = {}

        async def fake_make_request(session, url):
            captured["url"] = url
            return {"response": []}

        async def run():
            with patch.object(api_client, "_make_request", fake_make_request):
                return await api_client.fetch_live_fixtures_payload(None)

        payload = asyncio.run(run())
        self.assertEqual(payload, {"response": []})
        self.assertIn("/fixtures?live=all", captured["url"])

    def test_live_mapping_uses_api_football_live_feed_not_season_lookup(self):
        from modules import api_provider

        match = self._espn_match(fixture_id="737155")
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

    def test_enrichment_does_not_use_espn_id_as_api_football_id(self):
        from modules import api_provider

        match = self._espn_match(fixture_id="737155")
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

        match = self._espn_match(fixture_id="737155")
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

    def test_partial_espn_cache_preserves_failed_league_matches(self):
        from modules import api_provider

        cached_match = self._espn_match(fixture_id="737155", league_id=135)
        fresh_match = self._espn_match(fixture_id="eng-1", league_id=39)

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

        match = self._espn_match(fixture_id="737155")
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

    def test_match_memory_update_is_idempotent(self):
        from modules import football_memory

        match = {
            "fixture": {
                "id": "m1",
                "date": "2026-05-24T18:00:00+00:00",
                "status": {"short": "FT"},
            },
            "league": {"id": 135},
            "teams": {
                "home": {"id": "100", "name": "Home"},
                "away": {"id": "200", "name": "Away"},
            },
            "goals": {"home": 2, "away": 1},
            "events": [
                {
                    "type": "Goal",
                    "detail": "Normal Goal",
                    "player": {"name": "Scorer"},
                    "team": {"id": "100", "name": "Home"},
                    "time": {"elapsed": 10},
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "football_memory.json"
            with patch.object(football_memory, "MEMORY_PATH", memory_path):
                asyncio.run(football_memory.update_match_in_memory(None, match))
                asyncio.run(football_memory.update_match_in_memory(None, match))
                memory = json.loads(memory_path.read_text(encoding="utf-8"))

        self.assertEqual(memory["teams"]["100"]["stats"]["wins"], 1)
        self.assertEqual(memory["teams"]["100"]["stats"]["goals_for"], 2)
        self.assertEqual(memory["teams"]["100"]["players"]["Scorer"]["goals"], 1)
        self.assertEqual(len(memory["matches"]), 1)

    def _espn_match(self, fixture_id="737155", league_id=135):
        return {
            "fixture": {
                "id": fixture_id,
                "date": "2026-05-24T13:00:00+00:00",
                "status": {"short": "1H", "elapsed": 30},
            },
            "league": {"id": league_id},
            "teams": {
                "home": {"id": "50", "name": "Parma"},
                "away": {"id": "51", "name": "Sassuolo"},
            },
            "goals": {"home": 1, "away": 0},
            "events": [],
        }


if __name__ == "__main__":
    unittest.main()
