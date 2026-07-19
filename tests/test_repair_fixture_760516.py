import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")

from scripts import repair_fixture_760516 as repair


def _bad_event(minute, player, team_id="448", team_name="England", detail="Normal Goal"):
    return {
        "time": {"elapsed": minute},
        "player": {"name": player},
        "team": {"id": team_id, "name": team_name},
        "type": "Goal",
        "detail": detail,
    }


def _production_state():
    events = [
        _bad_event(2, "Declan Rice"),
        _bad_event(3, "D. Rice"),
        _bad_event(18, "E. Konsa"),
        _bad_event(36, "Bukayo Saka"),
        _bad_event(45, "Bukayo Saka"),
        _bad_event(47, "Kylian Mbappé", "478", "France"),
        _bad_event(53, "Bradley Barcola", "478", "France"),
        _bad_event(65, "Kylian Mbappé", "478", "France"),
        _bad_event(86, "Bukayo Saka", detail="Penalty"),
        _bad_event(90, "Ousmane Dembélé", "478", "France"),
    ]
    blank = {"goals": 0, "assists": 0, "yellow_cards": 0, "red_cards": 0}
    memory = {
        "metadata": {},
        "leagues": {},
        "teams": {
            "448": {
                "name": "England",
                "stats": {"wins": 6, "draws": 1, "losses": 1, "goals_for": 20, "goals_against": 12},
                "players": {
                    "Declan Rice": {**blank, "goals": 1},
                    "D. Rice": {**blank, "goals": 1},
                    "E. Konsa": {**blank, "goals": 1},
                    "Bukayo Saka": {**blank, "goals": 3},
                    "Jude Bellingham": {**blank, "goals": 3},
                },
            }
        },
        "matches": {
            repair.FIXTURE_ID: {
                "home": {"id": "478", "name": "France"},
                "away": {"id": "448", "name": "England"},
                "score": {"home": 4, "away": 6},
                "events": events,
            }
        },
    }
    state = {
        "version": 1,
        "fixtures": {
            repair.FIXTURE_ID: {
                "fixture_id": repair.FIXTURE_ID,
                "ft_announced": True,
                "memory_updated": True,
                "ft_message_id": repair.EXPECTED_MESSAGE_ID,
                "ft_message_content": "bad content",
                "event_completeness_status": "complete",
                "event_missing_goal_count": 0,
            }
        },
    }
    return memory, state


class RepairFixture760516Tests(unittest.TestCase):
    def test_build_repair_applies_exact_player_delta_and_is_idempotent(self):
        memory, state = _production_state()

        first = repair.build_repair(memory, state)
        players = first["memory"]["teams"]["448"]["players"]

        self.assertTrue(first["memory_changed"])
        self.assertNotIn("D. Rice", players)
        self.assertNotIn("E. Konsa", players)
        self.assertEqual(players["Ezri Konsa"]["goals"], 1)
        self.assertEqual(players["Jude Bellingham"]["goals"], 4)
        self.assertEqual(first["memory"]["teams"]["448"]["stats"], memory["teams"]["448"]["stats"])
        self.assertEqual(
            first["memory"]["matches"][repair.FIXTURE_ID]["events"],
            repair.EXPECTED_EVENTS,
        )

        second = repair.build_repair(first["memory"], first["match_state"])
        self.assertTrue(second["already_repaired"])
        self.assertFalse(second["memory_changed"])
        self.assertFalse(second["state_changed"])

    def test_build_repair_aborts_on_unknown_event_fingerprint(self):
        memory, state = _production_state()
        memory["matches"][repair.FIXTURE_ID]["events"][0]["player"]["name"] = "Unexpected"

        with self.assertRaisesRegex(RuntimeError, "fingerprint"):
            repair.build_repair(memory, state)

    def test_run_dry_run_does_not_write_or_contact_discord(self):
        memory, state = _production_state()
        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            memory_path = memory_dir / "football_memory.json"
            state_path = memory_dir / "match_state.json"
            memory_path.write_text(json.dumps(memory), encoding="utf-8")
            state_path.write_text(json.dumps(state), encoding="utf-8")
            before_memory = memory_path.read_text(encoding="utf-8")
            before_state = state_path.read_text(encoding="utf-8")

            result = repair.run(memory_dir, apply=False)

            self.assertFalse(result["applied"])
            self.assertEqual(memory_path.read_text(encoding="utf-8"), before_memory)
            self.assertEqual(state_path.read_text(encoding="utf-8"), before_state)
            self.assertFalse((memory_dir / "repair_backups").exists())

    def test_apply_discord_failure_leaves_json_unchanged(self):
        memory, state = _production_state()

        async def fail_edit(*args):
            return False

        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            memory_path = memory_dir / "football_memory.json"
            state_path = memory_dir / "match_state.json"
            memory_path.write_text(json.dumps(memory), encoding="utf-8")
            state_path.write_text(json.dumps(state), encoding="utf-8")

            with patch.object(repair, "_assert_service_stopped"):
                with self.assertRaisesRegex(RuntimeError, "Discord message edit failed"):
                    repair.run(memory_dir, apply=True, discord_editor=fail_edit)

            self.assertEqual(json.loads(memory_path.read_text(encoding="utf-8")), memory)
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8")), state)
            self.assertEqual(len(list((memory_dir / "repair_backups").iterdir())), 1)

    def test_apply_writes_atomic_repair_and_second_run_is_noop(self):
        memory, state = _production_state()
        edit_calls = []

        async def successful_edit(*args):
            edit_calls.append(args)
            return True

        with tempfile.TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            (memory_dir / "football_memory.json").write_text(json.dumps(memory), encoding="utf-8")
            (memory_dir / "match_state.json").write_text(json.dumps(state), encoding="utf-8")

            with patch.object(repair, "_assert_service_stopped"):
                first = repair.run(memory_dir, apply=True, discord_editor=successful_edit)
                second = repair.run(memory_dir, apply=True, discord_editor=successful_edit)

            self.assertTrue(first["applied"])
            self.assertTrue(second["already_repaired"])
            self.assertFalse(second["applied"])
            self.assertEqual(len(edit_calls), 1)
            saved_memory = json.loads((memory_dir / "football_memory.json").read_text(encoding="utf-8"))
            saved_state = json.loads((memory_dir / "match_state.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_memory["matches"][repair.FIXTURE_ID]["events"], repair.EXPECTED_EVENTS)
            self.assertEqual(
                saved_state["fixtures"][repair.FIXTURE_ID]["ft_message_content"],
                repair.EXPECTED_FT_CONTENT,
            )


if __name__ == "__main__":
    unittest.main()
