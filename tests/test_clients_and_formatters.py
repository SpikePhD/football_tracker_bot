import asyncio
import os
import unittest
from unittest.mock import patch

from tests.regression_helpers import shootout_match

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class ClientsAndFormattersTests(unittest.TestCase):

    class _FakeResponse:
        def __init__(self, status, payload=None, text=""):
            self.status = status
            self._payload = payload or {}
            self._text = text

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            return self._payload

        async def text(self):
            return self._text

    class _FakeSession:
        def __init__(self, payload, status=200):
            self.payload = payload
            self.status = status
            self.calls = []

        def get(self, url, headers=None, timeout=None):
            self.calls.append(url)
            return ClientsAndFormattersTests._FakeResponse(self.status, self.payload)

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

    def test_api_football_event_normalization_preserves_stoppage_time(self):
        from utils.event_formatter import format_match_events, normalize_api_football_events

        events = normalize_api_football_events([{
            "time": {"elapsed": 90, "extra": 6},
            "player": {"name": "Late Scorer"},
            "team": {"id": 10, "name": "Home"},
            "type": "Goal",
            "detail": "Normal Goal",
        }])

        self.assertEqual(events[0]["time"], {"elapsed": 90, "extra": 6})
        self.assertEqual(format_match_events(events, "Home", "Away"), ["90+6' - Late Scorer (H)"])

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

    def test_espn_fixture_760516_normalizes_all_scorers_and_display_clocks(self):
        from modules.ft_handler import _build_ft_message
        from utils import espn_client

        def goal(type_id, type_text, seconds, display, team_id, player, **flags):
            return {
                "type": {"id": str(type_id), "text": type_text},
                "clock": {"value": seconds, "displayValue": display},
                "team": {"id": team_id},
                "scoreValue": 1,
                "scoringPlay": True,
                "shootout": False,
                "athletesInvolved": [{"fullName": player}],
                **flags,
            }

        raw = {
            "id": "760516",
            "date": "2026-07-18T21:00Z",
            "status": {
                "period": 2,
                "displayClock": "90:00",
                "type": {"state": "post", "description": "Full Time", "name": "STATUS_FULL_TIME"},
            },
            "competitions": [{
                "competitors": [
                    {"homeAway": "home", "score": "4", "team": {"id": "478", "displayName": "France"}},
                    {"homeAway": "away", "score": "6", "team": {"id": "448", "displayName": "England"}},
                ],
                "details": [
                    goal(70, "Goal", 134, "3'", "448", "Declan Rice"),
                    goal(137, "Goal - Header", 1072, "18'", "448", "Ezri Konsa"),
                    goal(70, "Goal", 2170, "37'", "448", "Bukayo Saka"),
                    goal(70, "Goal", 2700, "45'+1'", "448", "Bukayo Saka"),
                    goal(70, "Goal", 2850, "48'", "478", "Kylian Mbappé"),
                    goal(70, "Goal", 3219, "54'", "478", "Bradley Barcola"),
                    goal(70, "Goal", 3950, "66'", "478", "Kylian Mbappé"),
                    goal(98, "Penalty - Scored", 5201, "87'", "448", "Bukayo Saka", penaltyKick=True),
                    goal(70, "Goal", 5400, "90'+6'", "478", "Ousmane Dembélé"),
                    goal(70, "Goal", 5400, "90'+8'", "448", "Jude Bellingham"),
                ],
            }],
        }

        match = espn_client._normalize_event(raw, 1)

        self.assertEqual(len(match["events"]), 10)
        self.assertEqual(
            [(event["time"], event["player"]["name"]) for event in match["events"]],
            [
                ({"elapsed": 3}, "Declan Rice"),
                ({"elapsed": 18}, "Ezri Konsa"),
                ({"elapsed": 37}, "Bukayo Saka"),
                ({"elapsed": 45, "extra": 1}, "Bukayo Saka"),
                ({"elapsed": 48}, "Kylian Mbappé"),
                ({"elapsed": 54}, "Bradley Barcola"),
                ({"elapsed": 66}, "Kylian Mbappé"),
                ({"elapsed": 87}, "Bukayo Saka"),
                ({"elapsed": 90, "extra": 6}, "Ousmane Dembélé"),
                ({"elapsed": 90, "extra": 8}, "Jude Bellingham"),
            ],
        )
        self.assertEqual(
            _build_ft_message(match),
            "FT: France 4 - 6 England (3' - Declan Rice (A); 18' - Ezri Konsa (A); "
            "37' - Bukayo Saka (A); 45+1' - Bukayo Saka (A); 48' - Kylian Mbappé (H); "
            "54' - Bradley Barcola (H); 66' - Kylian Mbappé (H); "
            "87' - Bukayo Saka (Penalty) (A); 90+6' - Ousmane Dembélé (H); "
            "90+8' - Jude Bellingham (A))",
        )

    def test_espn_normalization_separates_shootout_penalties_and_preserves_winner(self):
        from utils import espn_client

        match = espn_client._normalize_event(
            {
                "id": "shootout-1",
                "date": "2026-05-30T18:00Z",
                "status": {
                    "period": 5,
                    "displayClock": "120:00",
                    "type": {
                        "state": "post",
                        "name": "STATUS_FINAL_PEN",
                        "description": "Final Score - After Penalties",
                        "detail": "FT-Pens",
                    },
                },
                "competitions": [
                    {
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "1",
                                "winner": True,
                                "team": {"id": "100", "displayName": "Home"},
                            },
                            {
                                "homeAway": "away",
                                "score": "1",
                                "winner": False,
                                "team": {"id": "200", "displayName": "Away"},
                            },
                        ],
                        "details": [
                            {
                                "type": {"id": "98", "text": "Penalty - Scored"},
                                "clock": {"value": 3840},
                                "team": {"id": "100"},
                                "athletesInvolved": [{"fullName": "Normal Penalty"}],
                            },
                            {
                                "type": {"id": "104", "text": "Penalty - Scored"},
                                "clock": {"value": 7200},
                                "team": {"id": "100"},
                                "athletesInvolved": [{"fullName": "Shootout Scorer"}],
                            },
                        ],
                    }
                ],
            },
            135,
        )

        self.assertEqual(match["fixture"]["status"]["short"], "FT")
        self.assertEqual(match["fixture"]["status"]["detail"], "FT-Pens")
        self.assertEqual(match["winner"], "Home")
        self.assertEqual(match["events"][0]["type"], "Goal")
        self.assertEqual(match["events"][0]["detail"], "Penalty")
        self.assertEqual(match["events"][1]["type"], "PenaltyShootout")
        self.assertEqual(match["events"][1]["detail"], "Scored")

    def test_espn_tennis_normalization_preserves_retirement_status(self):
        from utils import espn_tennis_client
        from utils.tennis_lifecycle import tennis_final_data_ready, tennis_final_result_reason

        match = espn_tennis_client._competition_to_match(
            {"id": "event-1", "name": "Test Open"},
            {
                "id": "competition-1",
                "date": "2026-06-12T08:00:00Z",
                "status": {
                    "type": {
                        "state": "post",
                        "name": "STATUS_RET",
                        "detail": "Retired",
                        "shortDetail": "Ret.",
                        "completed": True,
                    }
                },
                "competitors": [
                    {
                        "winner": True,
                        "athlete": {"displayName": "Jannik Sinner"},
                        "linescores": [{"value": 6}, {"value": 2}],
                    },
                    {
                        "winner": False,
                        "athlete": {"displayName": "Opponent"},
                        "linescores": [{"value": 4}, {"value": 1}],
                    },
                ],
            },
            "atp",
        )

        self.assertEqual(match["status"]["short"], "FT")
        self.assertEqual(match["status"]["name"], "STATUS_RET")
        self.assertEqual(match["status"]["short_detail"], "Ret.")
        self.assertTrue(match["status"]["completed"])
        self.assertEqual(tennis_final_result_reason(match), "Retirement")
        self.assertTrue(tennis_final_data_ready(match))

    def test_shootout_formatter_separates_match_goals_penalty_score_and_takers(self):
        from utils.event_formatter import (
            event_completeness_note,
            format_match_events,
            format_shootout_segments,
        )

        match = shootout_match()

        normal_events = format_match_events(match["events"], "Home", "Away")
        shootout_segments = format_shootout_segments(match, final=True)
        note = event_completeness_note(match["goals"], match["events"], show_warning=True)

        self.assertEqual(normal_events, ["5' - Home Goal (H)", "64' - Away Goal (Penalty) (A)"])
        self.assertEqual(shootout_segments[0], "After 90': 1 - 1")
        self.assertEqual(shootout_segments[1], "Home win 4 - 3 on penalties")
        self.assertIn("Pens scored: Home: H1, H2, H3, H4; Away: A1, A2, A3", shootout_segments)
        self.assertEqual(note, "")

    def test_missed_penalty_does_not_count_or_render_as_goal(self):
        from utils.event_formatter import (
            event_completeness_note,
            format_match_events,
            is_counted_goal_event,
        )

        events = [
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

        self.assertFalse(is_counted_goal_event(events[0]))
        self.assertEqual(
            format_match_events(events, "Brazil", "Norway"),
            ["79' - E. Haaland (A)", "90' - E. Haaland (A)"],
        )
        self.assertIn(
            "1 goal(s) missing",
            event_completeness_note({"home": 1, "away": 2}, events, show_warning=True),
        )

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

    def test_espn_scoreboard_timeout_logs_warning_once_per_slug_and_date(self):
        from utils import espn_client

        class FakeSession:
            def get(self, *args, **kwargs):
                raise asyncio.TimeoutError()

        async def run():
            espn_client._scoreboard_warning_log_keys.clear()
            with self.assertLogs("utils.espn_client", level="WARNING") as first_logs:
                first = await espn_client.fetch_scoreboard_result(FakeSession(), "fifa.world", "20260614")
            with self.assertLogs("utils.espn_client", level="DEBUG") as second_logs:
                second = await espn_client.fetch_scoreboard_result(FakeSession(), "fifa.world", "20260614")
            return first, second, first_logs.output, second_logs.output

        first, second, first_output, second_output = asyncio.run(run())

        self.assertFalse(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(sum("Timeout fetching fifa.world scoreboard" in line for line in first_output), 1)
        self.assertEqual(sum("Timeout fetching fifa.world scoreboard" in line for line in second_output), 1)
        self.assertTrue(all("WARNING" not in line for line in second_output))

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

    def test_api_football_plan_unavailable_logs_warning_once_per_url_day(self):
        from utils import api_client

        cache = getattr(api_client, "_plan_unavailable_log_cache", None)
        if cache is not None:
            cache.clear()

        url = "https://v3.football.api-sports.io/fixtures?date=2026-06-12&league=1&season=2025"
        payload = {
            "errors": {"plan": "Free plans do not have access to this endpoint."},
            "parameters": {"date": "2026-06-12", "league": "1", "season": "2025"},
            "response": [],
        }

        async def run():
            session = self._FakeSession(payload)
            with (
                patch.object(api_client, "get_bot_local_date_string", return_value="2026-06-13"),
                patch.object(api_client.logger, "warning") as warning_log,
                patch.object(api_client.logger, "error") as error_log,
            ):
                first = await api_client._make_request(session, url)
                second = await api_client._make_request(session, url)
                return first, second, warning_log, error_log

        first, second, warning_log, error_log = asyncio.run(run())
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(warning_log.call_count, 1)
        self.assertIn("plan unavailable", warning_log.call_args.args[0].lower())
        error_log.assert_not_called()

    def test_api_football_plan_unavailable_logs_each_distinct_url_once(self):
        from utils import api_client

        cache = getattr(api_client, "_plan_unavailable_log_cache", None)
        if cache is not None:
            cache.clear()

        first_url = "https://v3.football.api-sports.io/fixtures?date=2026-06-12&league=1&season=2025"
        second_url = "https://v3.football.api-sports.io/fixtures?date=2026-06-13&league=1&season=2025"
        payload = {
            "errors": {"endpoint": "You do not have access to this endpoint with your current plan."},
            "parameters": {"league": "1", "season": "2025"},
            "response": [],
        }

        async def run():
            session = self._FakeSession(payload)
            with (
                patch.object(api_client, "get_bot_local_date_string", return_value="2026-06-13"),
                patch.object(api_client.logger, "warning") as warning_log,
                patch.object(api_client.logger, "error") as error_log,
            ):
                await api_client._make_request(session, first_url)
                await api_client._make_request(session, second_url)
                await api_client._make_request(session, first_url)
                return warning_log, error_log

        warning_log, error_log = asyncio.run(run())
        self.assertEqual(warning_log.call_count, 2)
        self.assertIn(first_url, warning_log.call_args_list[0].args[0])
        self.assertIn(second_url, warning_log.call_args_list[1].args[0])
        error_log.assert_not_called()

    def test_api_football_request_limit_still_marks_quota_exceeded(self):
        from utils import api_client

        api_client._quota_exceeded_day = None
        payload = {
            "errors": {"requests": "You have reached the request limit for the day."},
            "parameters": {},
            "response": [],
        }

        async def run():
            session = self._FakeSession(payload)
            with (
                patch.object(api_client, "get_bot_local_date_string", return_value="2026-06-13"),
                patch.object(api_client.logger, "warning") as warning_log,
                patch.object(api_client.logger, "error") as error_log,
            ):
                result = await api_client._make_request(session, "https://v3.football.api-sports.io/fixtures?date=2026-06-13")
                quota = api_client.is_quota_exceeded_today()
                return result, quota, warning_log, error_log

        result, quota, warning_log, error_log = asyncio.run(run())
        self.assertIsNone(result)
        self.assertTrue(quota)
        warning_log.assert_not_called()
        error_log.assert_called_once()

    def test_api_football_generic_payload_error_still_logs_error(self):
        from utils import api_client

        payload = {
            "errors": {"league": "The League field is required."},
            "parameters": {},
            "response": [],
        }

        async def run():
            session = self._FakeSession(payload)
            with patch.object(api_client.logger, "error") as error_log:
                result = await api_client._make_request(session, "https://v3.football.api-sports.io/fixtures")
                return result, error_log

        result, error_log = asyncio.run(run())
        self.assertIsNone(result)
        error_log.assert_called_once()



if __name__ == "__main__":
    unittest.main()
