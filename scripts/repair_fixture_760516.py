"""Guarded one-time repair for France 4-6 England (ESPN fixture 760516).

Dry-run (default):
    python scripts/repair_fixture_760516.py

Apply while the bot service is stopped:
    sudo systemctl stop marco_van_botten
    python scripts/repair_fixture_760516.py --apply
    sudo systemctl start marco_van_botten
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modules.storage import save_json_path  # noqa: E402

FIXTURE_ID = "760516"
SERVICE_NAME = "marco_van_botten"
EXPECTED_MESSAGE_ID = 1528175327103156407
PLAYER_STAT_KEYS = ("goals", "assists", "yellow_cards", "red_cards")


def _goal(minute: int, player: str, team_id: str, team_name: str, detail: str = "Normal Goal", extra: int | None = None) -> dict:
    event_time = {"elapsed": minute}
    if extra is not None:
        event_time["extra"] = extra
    return {
        "time": event_time,
        "player": {"name": player},
        "team": {"id": team_id, "name": team_name},
        "type": "Goal",
        "detail": detail,
    }


EXPECTED_EVENTS = [
    _goal(3, "Declan Rice", "448", "England"),
    _goal(18, "Ezri Konsa", "448", "England"),
    _goal(37, "Bukayo Saka", "448", "England"),
    _goal(45, "Bukayo Saka", "448", "England", extra=1),
    _goal(48, "Kylian Mbappé", "478", "France"),
    _goal(54, "Bradley Barcola", "478", "France"),
    _goal(66, "Kylian Mbappé", "478", "France"),
    _goal(87, "Bukayo Saka", "448", "England", detail="Penalty"),
    _goal(90, "Ousmane Dembélé", "478", "France", extra=6),
    _goal(90, "Jude Bellingham", "448", "England", extra=8),
]

EXPECTED_FT_CONTENT = (
    "FT: France 4 - 6 England ("
    "3' - Declan Rice (A); 18' - Ezri Konsa (A); 37' - Bukayo Saka (A); "
    "45+1' - Bukayo Saka (A); 48' - Kylian Mbappé (H); "
    "54' - Bradley Barcola (H); 66' - Kylian Mbappé (H); "
    "87' - Bukayo Saka (Penalty) (A); 90+6' - Ousmane Dembélé (H); "
    "90+8' - Jude Bellingham (A))"
)

BAD_EVENT_FINGERPRINT = [
    (2, 0, "Declan Rice", "448", "Normal Goal"),
    (3, 0, "D. Rice", "448", "Normal Goal"),
    (18, 0, "E. Konsa", "448", "Normal Goal"),
    (36, 0, "Bukayo Saka", "448", "Normal Goal"),
    (45, 0, "Bukayo Saka", "448", "Normal Goal"),
    (47, 0, "Kylian Mbappé", "478", "Normal Goal"),
    (53, 0, "Bradley Barcola", "478", "Normal Goal"),
    (65, 0, "Kylian Mbappé", "478", "Normal Goal"),
    (86, 0, "Bukayo Saka", "448", "Penalty"),
    (90, 0, "Ousmane Dembélé", "478", "Normal Goal"),
]


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _event_fingerprint(events: list) -> list[tuple[int, int, str, str, str]]:
    result = []
    for event in events or []:
        event_time = event.get("time", {}) or {}
        result.append(
            (
                int(event_time.get("elapsed") or 0),
                int(event_time.get("extra") or 0),
                str(event.get("player", {}).get("name") or ""),
                str(event.get("team", {}).get("id") or ""),
                str(event.get("detail") or ""),
            )
        )
    return result


def _player(players: dict, name: str) -> dict:
    record = players.setdefault(name, {})
    for key in PLAYER_STAT_KEYS:
        record[key] = int(record.get(key) or 0)
    return record


def _remove_empty_stat_only_player(players: dict, name: str) -> None:
    record = players.get(name)
    if not isinstance(record, dict):
        return
    if all(int(record.get(key) or 0) == 0 for key in PLAYER_STAT_KEYS) and set(record) <= set(PLAYER_STAT_KEYS):
        players.pop(name, None)


def build_repair(memory: dict, match_state: dict, *, now_utc: datetime | None = None) -> dict:
    """Validate production fingerprints and return repaired copies without writing."""
    repaired_memory = deepcopy(memory)
    repaired_state = deepcopy(match_state)
    stored_match = repaired_memory.get("matches", {}).get(FIXTURE_ID)
    fixture_state = repaired_state.get("fixtures", {}).get(FIXTURE_ID)
    if not isinstance(stored_match, dict) or not isinstance(fixture_state, dict):
        raise RuntimeError(f"Fixture {FIXTURE_ID} is missing from football memory or match state.")

    if stored_match.get("home", {}).get("name") != "France" or stored_match.get("away", {}).get("name") != "England":
        raise RuntimeError("Fixture identity mismatch; expected France vs England.")
    score = stored_match.get("score", {}) or {}
    if (score.get("home"), score.get("away")) != (4, 6):
        raise RuntimeError("Fixture score mismatch; expected France 4-6 England.")
    if int(fixture_state.get("ft_message_id") or 0) != EXPECTED_MESSAGE_ID:
        raise RuntimeError("Stored FT message ID does not match the known production message.")

    fingerprint = _event_fingerprint(stored_match.get("events", []))
    expected_fingerprint = _event_fingerprint(EXPECTED_EVENTS)
    if fingerprint == expected_fingerprint:
        memory_changed = False
    elif fingerprint == BAD_EVENT_FINGERPRINT:
        england = repaired_memory.get("teams", {}).get("448")
        if not isinstance(england, dict):
            raise RuntimeError("England team 448 is missing from football memory.")
        players = england.setdefault("players", {})
        for abbreviated in ("D. Rice", "E. Konsa"):
            record = _player(players, abbreviated)
            if record["goals"] < 1:
                raise RuntimeError(f"Cannot remove the bad goal contribution from {abbreviated}.")
        if _player(players, "Declan Rice")["goals"] < 1 or _player(players, "Bukayo Saka")["goals"] < 3:
            raise RuntimeError("Known good England scorer totals do not match the repair preconditions.")

        players["D. Rice"]["goals"] -= 1
        players["E. Konsa"]["goals"] -= 1
        _player(players, "Ezri Konsa")["goals"] += 1
        _player(players, "Jude Bellingham")["goals"] += 1
        _remove_empty_stat_only_player(players, "D. Rice")
        _remove_empty_stat_only_player(players, "E. Konsa")
        stored_match["events"] = deepcopy(EXPECTED_EVENTS)
        memory_changed = True
    else:
        raise RuntimeError("Stored event fingerprint is neither the known bad state nor the repaired state.")

    state_changed = fixture_state.get("ft_message_content") != EXPECTED_FT_CONTENT
    state_changed = state_changed or fixture_state.get("event_completeness_status") != "complete"
    state_changed = state_changed or int(fixture_state.get("event_missing_goal_count") or 0) != 0
    if state_changed:
        timestamp = (now_utc or datetime.now(timezone.utc)).isoformat()
        fixture_state["ft_message_content"] = EXPECTED_FT_CONTENT
        fixture_state["event_completeness_status"] = "complete"
        fixture_state["event_missing_goal_count"] = 0
        fixture_state["event_completeness_key"] = f"{FIXTURE_ID}:4:6"
        fixture_state["event_completeness_updated_utc"] = timestamp

    return {
        "memory": repaired_memory,
        "match_state": repaired_state,
        "message_id": EXPECTED_MESSAGE_ID,
        "content": EXPECTED_FT_CONTENT,
        "memory_changed": memory_changed,
        "state_changed": state_changed,
        "already_repaired": not memory_changed and not state_changed,
    }


def _assert_service_stopped() -> None:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", SERVICE_NAME],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("systemctl is required for --apply.") from exc
    if result.returncode == 0:
        raise RuntimeError(f"Stop {SERVICE_NAME} before applying the repair.")


def _create_backups(memory_dir: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = memory_dir / "repair_backups" / f"fixture_{FIXTURE_ID}_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(memory_dir / "football_memory.json", backup_dir / "football_memory.json")
    shutil.copy2(memory_dir / "match_state.json", backup_dir / "match_state.json")
    return backup_dir


async def _edit_discord_message(token: str, channel_id: int, message_id: int, content: str) -> bool:
    import discord

    from modules.discord_poster import edit_general_message

    class RepairClient(discord.Client):
        def __init__(self) -> None:
            super().__init__(intents=discord.Intents.default())
            self.edit_succeeded = False

        async def on_ready(self) -> None:
            edited = await edit_general_message(self, channel_id, message_id, content)
            self.edit_succeeded = edited is not None
            await self.close()

    client = RepairClient()
    try:
        await asyncio.wait_for(client.start(token), timeout=60)
    finally:
        if not client.is_closed():
            await client.close()
    return client.edit_succeeded


def run(memory_dir: Path, *, apply: bool, discord_editor=_edit_discord_message) -> dict:
    memory_path = memory_dir / "football_memory.json"
    state_path = memory_dir / "match_state.json"
    repair = build_repair(_load_json(memory_path), _load_json(state_path))
    if not apply or repair["already_repaired"]:
        return {**repair, "applied": False, "backup_dir": None}

    _assert_service_stopped()
    backup_dir = _create_backups(memory_dir)

    from config import BOT_TOKEN, CHANNEL_ID

    edited = asyncio.run(
        discord_editor(BOT_TOKEN, CHANNEL_ID, repair["message_id"], repair["content"])
    )
    if not edited:
        raise RuntimeError("Discord message edit failed; JSON state was not changed.")

    if repair["memory_changed"]:
        save_json_path(memory_path, repair["memory"], ensure_ascii=False)
    if repair["state_changed"]:
        save_json_path(state_path, repair["match_state"], ensure_ascii=False)
    return {**repair, "applied": True, "backup_dir": backup_dir}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Edit Discord and atomically repair production JSON files.")
    parser.add_argument(
        "--memory-dir",
        type=Path,
        default=REPO_ROOT / "bot_memory",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    try:
        result = run(args.memory_dir.resolve(), apply=args.apply)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    mode = "APPLIED" if result["applied"] else ("ALREADY REPAIRED" if result["already_repaired"] else "DRY RUN OK")
    print(f"{mode}: fixture {FIXTURE_ID}")
    print(result["content"])
    if result.get("backup_dir"):
        print(f"Backups: {result['backup_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
