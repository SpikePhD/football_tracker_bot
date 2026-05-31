import asyncio
import os
import unittest
from unittest.mock import patch

from tests.regression_helpers import shootout_match

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class ClientsAndFormattersTests(unittest.TestCase):

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

    def test_shootout_formatter_separates_match_goals_penalty_score_and_takers(self):
        from utils.event_formatter import (
            event_completeness_note,
            format_match_events,
            format_shootout_segments,
        )

        match = shootout_match()

        normal_events = format_match_events(match["events"], "Home", "Away")
        shootout_segments = format_shootout_segments(match, final=True)
        note = event_completeness_note(match["goals"], match["events"])

        self.assertEqual(normal_events, ["5' - Home Goal (H)", "64' - Away Goal (Penalty) (A)"])
        self.assertEqual(shootout_segments[0], "After 90': 1 - 1")
        self.assertEqual(shootout_segments[1], "Home win 4 - 3 on penalties")
        self.assertIn("Pens scored: Home: H1, H2, H3, H4; Away: A1, A2, A3", shootout_segments)
        self.assertEqual(note, "")

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



if __name__ == "__main__":
    unittest.main()
