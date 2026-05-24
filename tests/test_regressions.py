import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
