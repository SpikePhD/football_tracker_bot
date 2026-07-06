import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.regression_helpers import shootout_match

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class FootballMemoryTests(unittest.TestCase):

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
                first = asyncio.run(football_memory.update_match_in_memory(None, match))
                second = asyncio.run(football_memory.update_match_in_memory(None, match))
                memory = json.loads(memory_path.read_text(encoding="utf-8"))

        self.assertEqual(first, {"updated": True, "reason": "updated"})
        self.assertEqual(second, {"updated": True, "reason": "updated_existing"})
        self.assertEqual(memory["teams"]["100"]["stats"]["wins"], 1)
        self.assertEqual(memory["teams"]["100"]["stats"]["goals_for"], 2)
        self.assertEqual(memory["teams"]["100"]["players"]["Scorer"]["goals"], 1)
        self.assertEqual(len(memory["matches"]), 1)

    def test_match_memory_uses_canonical_fixture_id_for_mapped_provider_match(self):
        from modules import football_memory

        match = {
            "canonical_fixture_id": "760429",
            "provider": "api_football",
            "provider_fixture_id": "1489379",
            "fixture": {
                "id": "1489379",
                "date": "2026-06-15T22:00:00+00:00",
                "status": {"short": "FT", "long": "Match Finished"},
            },
            "league": {"id": 1},
            "teams": {
                "home": {"id": "100", "name": "Saudi Arabia"},
                "away": {"id": "200", "name": "Uruguay"},
            },
            "goals": {"home": 1, "away": 1},
            "events": [],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "football_memory.json"
            with patch.object(football_memory, "MEMORY_PATH", memory_path):
                result = asyncio.run(football_memory.update_match_in_memory(None, match))
                memory = json.loads(memory_path.read_text(encoding="utf-8"))

        self.assertEqual(result, {"updated": True, "reason": "updated"})
        self.assertIn("760429", memory["matches"])
        self.assertNotIn("1489379", memory["matches"])

    def test_match_memory_updates_terminal_penalty_result(self):
        from modules import football_memory

        match = shootout_match()
        match["fixture"]["status"] = {
            "short": "PEN",
            "long": "Match Finished",
            "elapsed": 120,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "football_memory.json"
            with patch.object(football_memory, "MEMORY_PATH", memory_path):
                result = asyncio.run(football_memory.update_match_in_memory(None, match))
                memory = json.loads(memory_path.read_text(encoding="utf-8"))

        self.assertEqual(result, {"updated": True, "reason": "updated"})
        self.assertEqual(memory["matches"]["shootout-1"]["status"], "PEN_DONE")
        self.assertEqual(memory["teams"]["100"]["stats"]["draws"], 1)
        self.assertEqual(memory["teams"]["200"]["stats"]["draws"], 1)

    def test_match_memory_does_not_count_surplus_voided_goal_event(self):
        from modules import football_memory

        match = {
            "fixture": {
                "id": "voided-ft",
                "date": "2026-06-21T19:00:00+00:00",
                "status": {"short": "FT"},
            },
            "league": {"id": 1},
            "teams": {
                "home": {"id": "100", "name": "Belgium"},
                "away": {"id": "200", "name": "Iran"},
            },
            "goals": {"home": 0, "away": 0},
            "events": [
                {
                    "type": "Goal",
                    "detail": "Normal Goal",
                    "player": {"name": "Mehdi Taremi"},
                    "team": {"id": "200", "name": "Iran"},
                    "time": {"elapsed": 24},
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "football_memory.json"
            with patch.object(football_memory, "MEMORY_PATH", memory_path):
                result = asyncio.run(football_memory.update_match_in_memory(None, match))
                memory = json.loads(memory_path.read_text(encoding="utf-8"))

        self.assertEqual(result, {"updated": True, "reason": "updated"})
        self.assertEqual(memory["matches"]["voided-ft"]["events"], [])
        self.assertEqual(memory["teams"]["100"]["stats"]["draws"], 1)
        self.assertEqual(memory["teams"]["200"]["stats"]["draws"], 1)
        self.assertNotIn("Mehdi Taremi", memory["teams"]["200"].get("players", {}))

    def test_match_memory_does_not_count_missed_penalty_as_goal(self):
        from modules import football_memory

        match = {
            "fixture": {
                "id": "missed-penalty-ft",
                "date": "2026-07-05T22:00:00+00:00",
                "status": {"short": "FT"},
            },
            "league": {"id": 1},
            "teams": {
                "home": {"id": "100", "name": "Brazil"},
                "away": {"id": "200", "name": "Norway"},
            },
            "goals": {"home": 1, "away": 2},
            "events": [
                {
                    "type": "Goal",
                    "detail": "Missed Penalty",
                    "player": {"name": "Bruno Guimaraes"},
                    "team": {"id": "100", "name": "Brazil"},
                    "time": {"elapsed": 14},
                },
                {
                    "type": "Goal",
                    "detail": "Normal Goal",
                    "player": {"name": "E. Haaland"},
                    "team": {"id": "200", "name": "Norway"},
                    "time": {"elapsed": 79},
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "football_memory.json"
            with patch.object(football_memory, "MEMORY_PATH", memory_path):
                result = asyncio.run(football_memory.update_match_in_memory(None, match))
                memory = json.loads(memory_path.read_text(encoding="utf-8"))

        self.assertEqual(result, {"updated": True, "reason": "updated"})
        self.assertNotIn("Bruno Guimaraes", memory["teams"]["100"].get("players", {}))
        self.assertEqual(memory["teams"]["200"]["players"]["E. Haaland"]["goals"], 1)

    def test_match_memory_skips_non_ft_without_updating(self):
        from modules import football_memory

        match = shootout_match()
        match["fixture"]["status"] = {"short": "2H", "elapsed": 80}

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "football_memory.json"
            with patch.object(football_memory, "MEMORY_PATH", memory_path):
                result = asyncio.run(football_memory.update_match_in_memory(None, match))
                memory = json.loads(memory_path.read_text(encoding="utf-8")) if memory_path.exists() else None

        self.assertEqual(result, {"updated": False, "reason": "not_ft"})
        self.assertIsNone(memory)

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

    def test_match_memory_ignores_shootout_penalties_for_team_and_player_stats(self):
        from modules import football_memory

        match = shootout_match()
        match["fixture"]["status"]["short"] = "FT"

        with tempfile.TemporaryDirectory() as tmpdir:
            memory_path = Path(tmpdir) / "football_memory.json"
            with patch.object(football_memory, "MEMORY_PATH", memory_path):
                asyncio.run(football_memory.update_match_in_memory(None, match))
                memory = json.loads(memory_path.read_text(encoding="utf-8"))

        self.assertEqual(memory["teams"]["100"]["stats"]["draws"], 1)
        self.assertEqual(memory["teams"]["100"]["stats"]["goals_for"], 1)
        self.assertEqual(memory["teams"]["100"]["players"]["Home Goal"]["goals"], 1)
        self.assertNotIn("H1", memory["teams"]["100"]["players"])
        self.assertEqual(memory["teams"]["200"]["stats"]["draws"], 1)
        self.assertEqual(memory["teams"]["200"]["stats"]["goals_for"], 1)
        self.assertNotIn("A1", memory["teams"]["200"]["players"])

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



if __name__ == "__main__":
    unittest.main()
