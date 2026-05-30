import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class RegressionTests(unittest.TestCase):
    def setUp(self):
        api_provider = sys.modules.get("modules.api_provider")
        if api_provider is not None:
            api_provider._enrich_retry_states.clear()
            api_provider._api_fixture_id_cache.clear()
            api_provider._api_live_fixtures_cache = None
            api_provider._api_live_fixtures_cache_ts = None
            api_provider._api_fixture_events_cache.clear()
            api_provider._api_fixture_id_negative_cache.clear()
            api_provider._best_known_events_by_espn_fixture.clear()
            api_provider._best_known_reuse_log_keys.clear()
            api_provider._enrich_tick_key = None
            api_provider._enrich_tick_count = 0
            api_provider._enrich_api_call_count = 0
            api_provider._enrich_api_call_count_date = None
            api_provider._enrich_budget_exhausted_logged_date = None

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

    def test_command_error_context_includes_full_content_and_discord_ids(self):
        import football_tracker_bot

        ctx = SimpleNamespace(
            command=SimpleNamespace(
                name="ask",
                qualified_name="ask",
                cog=SimpleNamespace(qualified_name="AskCog"),
            ),
            author=SimpleNamespace(id=111, name="Luca", display_name="Luca Display"),
            channel=SimpleNamespace(id=222, name="bot-test"),
            guild=SimpleNamespace(id=333, name="Guild Name"),
            message=SimpleNamespace(
                id=444,
                content="!ask full command text with token=abc123",
                attachments=[object(), object()],
            ),
        )

        context = football_tracker_bot._format_command_error_context(ctx)

        self.assertIn("command=ask", context)
        self.assertIn("qualified=ask", context)
        self.assertIn("cog=AskCog", context)
        self.assertIn("author_id=111", context)
        self.assertIn("author_name=Luca", context)
        self.assertIn("channel_id=222", context)
        self.assertIn("channel_name=bot-test", context)
        self.assertIn("guild_id=333", context)
        self.assertIn("guild_name=Guild Name", context)
        self.assertIn("message_id=444", context)
        self.assertIn("attachments=2", context)
        self.assertIn("content='!ask full command text with token=abc123'", context)

    def test_command_error_unwraps_command_invoke_error(self):
        from discord.ext import commands
        import football_tracker_bot

        original = ValueError("boom")
        wrapped = commands.CommandInvokeError(original)

        self.assertIs(football_tracker_bot._unwrap_command_error(wrapped), original)

    def test_command_error_action_classifies_expected_errors_as_warnings(self):
        from discord.ext import commands
        import football_tracker_bot

        action = football_tracker_bot._command_error_action(commands.BadArgument("bad input"))

        self.assertFalse(action["ignore"])
        self.assertEqual(action["log_level"], "warning")
        self.assertFalse(action["log_traceback"])
        self.assertIn("Invalid command argument", action["user_message"])

    def test_command_error_action_ignores_unknown_commands(self):
        from discord.ext import commands
        import football_tracker_bot

        action = football_tracker_bot._command_error_action(commands.CommandNotFound("missing"))

        self.assertTrue(action["ignore"])
        self.assertIsNone(action["user_message"])
        self.assertFalse(action["log_traceback"])

    def test_command_error_action_classifies_unexpected_errors_for_traceback_logging(self):
        import football_tracker_bot

        action = football_tracker_bot._command_error_action(RuntimeError("boom"))

        self.assertFalse(action["ignore"])
        self.assertEqual(action["log_level"], "error")
        self.assertTrue(action["log_traceback"])
        self.assertIn("Command failed unexpectedly", action["user_message"])

    def test_regression_guard_context_includes_previous_and_current_state(self):
        from modules import live_loop

        detail = live_loop._regression_guard_context(
            match_id="m1",
            previous_observed={"home": 2, "away": 1, "elapsed": 75, "events_count": 3},
            current_score={"home": 1, "away": 1},
            current_elapsed=60,
            current_events_count=2,
            state_key="m1_1-1_2",
            reason="score,elapsed",
        )

        self.assertIn("match=m1", detail)
        self.assertIn("reason=score,elapsed", detail)
        self.assertIn("prev_score=2-1", detail)
        self.assertIn("curr_score=1-1", detail)
        self.assertIn("prev_elapsed=75", detail)
        self.assertIn("curr_elapsed=60", detail)
        self.assertIn("prev_events=3", detail)
        self.assertIn("curr_events=2", detail)
        self.assertIn("state_key=m1_1-1_2", detail)

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

    def test_live_mapping_reuses_cached_live_feed_for_multiple_fixtures(self):
        from modules import api_provider

        match_one = self._espn_match(fixture_id="737155")
        match_two = self._espn_match(fixture_id="737156")
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

        match = self._espn_match(fixture_id="737155")
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

        match = self._espn_match(fixture_id="737157")
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

        match = self._espn_match(fixture_id="737155")
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

        match = self._espn_match(fixture_id="737155")
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

        match = self._espn_match(fixture_id="737155")
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

        match = self._espn_match(fixture_id="737155")
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

    def test_match_memory_update_repairs_existing_teams_missing_stats(self):
        from modules import football_memory

        match = {
            "fixture": {
                "id": "m2",
                "date": "2026-05-24T18:00:00+00:00",
                "status": {"short": "FT"},
            },
            "league": {"id": 135},
            "teams": {
                "home": {"id": "100", "name": "Home"},
                "away": {"id": "200", "name": "Away"},
            },
            "goals": {"home": 1, "away": 1},
            "events": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "football_memory.json"
            memory_path.write_text(
                json.dumps(
                    {
                        "metadata": {},
                        "leagues": {},
                        "teams": {
                            "100": {"name": "Home", "coach": "Coach", "players": {}},
                            "200": {"name": "Away", "coach": "Coach", "players": {}},
                        },
                        "matches": {},
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(football_memory, "MEMORY_PATH", memory_path):
                asyncio.run(football_memory.update_match_in_memory(None, match))
                memory = json.loads(memory_path.read_text(encoding="utf-8"))

        self.assertEqual(memory["teams"]["100"]["stats"]["draws"], 1)
        self.assertEqual(memory["teams"]["100"]["stats"]["goals_for"], 1)
        self.assertEqual(memory["teams"]["200"]["stats"]["draws"], 1)
        self.assertEqual(memory["teams"]["200"]["stats"]["goals_against"], 1)

    def test_match_memory_update_repairs_existing_teams_missing_players(self):
        from modules import football_memory

        match = {
            "fixture": {
                "id": "m3",
                "date": "2026-05-24T18:00:00+00:00",
                "status": {"short": "FT"},
            },
            "league": {"id": 135},
            "teams": {
                "home": {"id": "100", "name": "Home"},
                "away": {"id": "200", "name": "Away"},
            },
            "goals": {"home": 1, "away": 0},
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
            memory_path.write_text(
                json.dumps(
                    {
                        "metadata": {},
                        "leagues": {},
                        "teams": {
                            "100": {
                                "name": "Home",
                                "coach": "Coach",
                                "stats": {"wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0},
                            },
                            "200": {
                                "name": "Away",
                                "coach": "Coach",
                                "stats": {"wins": 0, "draws": 0, "losses": 0, "goals_for": 0, "goals_against": 0},
                            },
                        },
                        "matches": {},
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(football_memory, "MEMORY_PATH", memory_path):
                asyncio.run(football_memory.update_match_in_memory(None, match))
                memory = json.loads(memory_path.read_text(encoding="utf-8"))

        self.assertEqual(memory["teams"]["100"]["stats"]["wins"], 1)
        self.assertEqual(memory["teams"]["100"]["players"]["Scorer"]["goals"], 1)
        self.assertEqual(memory["teams"]["200"]["players"], {})

    def test_team_info_refresh_preserves_existing_team_stats(self):
        from modules import football_memory

        async def fake_update_team_info(session, team_id, slug):
            return {
                "name": "Home FC",
                "coach": "New Coach",
                "players": {
                    "Scorer": {"position": "Forward"},
                    "New Player": {"position": "Midfielder"},
                },
                "last_updated": "2026-05-24T20:00:00+00:00",
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "football_memory.json"
            memory_path.write_text(
                json.dumps(
                    {
                        "metadata": {},
                        "leagues": {
                            "135": {
                                "standings": [{"team_id": "100"}],
                            }
                        },
                        "teams": {
                            "100": {
                                "name": "Home",
                                "coach": "Old Coach",
                                "players": {
                                    "Scorer": {"goals": 3, "assists": 1, "yellow_cards": 0, "red_cards": 0}
                                },
                                "stats": {"wins": 2, "draws": 1, "losses": 0, "goals_for": 7, "goals_against": 3},
                                "last_updated": "old",
                            }
                        },
                        "matches": {},
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(football_memory, "MEMORY_PATH", memory_path),
                patch.object(football_memory, "update_team_info", fake_update_team_info),
            ):
                asyncio.run(football_memory.update_team_info_only(None))
                memory = json.loads(memory_path.read_text(encoding="utf-8"))

        team = memory["teams"]["100"]
        self.assertEqual(team["name"], "Home FC")
        self.assertEqual(team["coach"], "New Coach")
        self.assertEqual(team["stats"], {"wins": 2, "draws": 1, "losses": 0, "goals_for": 7, "goals_against": 3})
        self.assertEqual(team["players"]["Scorer"]["goals"], 3)
        self.assertEqual(team["players"]["Scorer"]["position"], "Forward")
        self.assertIn("New Player", team["players"])

    def test_all_memory_refresh_preserves_existing_team_stats(self):
        from modules import football_memory

        async def fake_update_league_standings(session, league_id, slug):
            if league_id != 135:
                return None
            return {
                "name": "Serie A",
                "standings": [{"team_id": "100"}],
                "last_updated": "2026-05-24T20:00:00+00:00",
            }

        async def fake_update_team_info(session, team_id, slug):
            return {
                "name": "Home FC",
                "coach": "New Coach",
                "players": {
                    "Scorer": {"position": "Forward"},
                },
                "last_updated": "2026-05-24T20:00:00+00:00",
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "football_memory.json"
            memory_path.write_text(
                json.dumps(
                    {
                        "metadata": {},
                        "leagues": {},
                        "teams": {
                            "100": {
                                "name": "Home",
                                "coach": "Old Coach",
                                "players": {
                                    "Scorer": {"goals": 4, "assists": 2, "yellow_cards": 1, "red_cards": 0}
                                },
                                "stats": {"wins": 3, "draws": 2, "losses": 1, "goals_for": 11, "goals_against": 6},
                                "last_updated": "old",
                            }
                        },
                        "matches": {},
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(football_memory, "MEMORY_PATH", memory_path),
                patch.object(football_memory, "TRACKED_LEAGUE_IDS", [135]),
                patch.object(football_memory, "LEAGUE_SLUG_MAP", {135: "ita.1"}),
                patch.object(football_memory, "update_league_standings", fake_update_league_standings),
                patch.object(football_memory, "update_team_info", fake_update_team_info),
            ):
                asyncio.run(football_memory.update_all_memory(None))
                memory = json.loads(memory_path.read_text(encoding="utf-8"))

        team = memory["teams"]["100"]
        self.assertEqual(team["stats"], {"wins": 3, "draws": 2, "losses": 1, "goals_for": 11, "goals_against": 6})
        self.assertEqual(team["players"]["Scorer"]["goals"], 4)
        self.assertEqual(team["players"]["Scorer"]["position"], "Forward")

    def test_ft_handler_keeps_penalty_match_tracked_past_expected_ft(self):
        from modules import ft_handler
        from modules import api_provider

        match = self._espn_match(fixture_id="pen-1")
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

        match = self._espn_match(fixture_id="et-1")
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

        match = self._espn_match(fixture_id="draw-ft")
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

        match = self._espn_match(fixture_id="abd-1")
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
