"""Offline lifecycle simulation for UTC-first football tracking.

Run:
    python scripts/simulate_lifecycle.py

This script uses static fixtures only. It does not call Discord or providers.
"""

from __future__ import annotations

import os
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("BOT_TOKEN", "simulation-token")
os.environ.setdefault("API_KEY", "simulation-api-key")
os.environ.setdefault("CHANNEL_ID", "0")

from modules import match_lifecycle  # noqa: E402


NOW_UTC = datetime(2026, 6, 3, 22, 15, tzinfo=timezone.utc)


def fixture(
    fixture_id: str,
    kickoff: str,
    short: str,
    long: str = "",
    home: int | None = None,
    away: int | None = None,
) -> dict:
    return {
        "fixture": {
            "id": fixture_id,
            "date": kickoff,
            "status": {"short": short, "long": long},
        },
        "league": {"id": 1, "name": "Simulation League"},
        "teams": {
            "home": {"id": 10, "name": "Home"},
            "away": {"id": 20, "name": "Away"},
        },
        "goals": {"home": home, "away": away},
    }


SAMPLES = {
    "normal_ft": fixture(
        "normal-ft",
        "2026-06-03T20:00:00Z",
        "FT",
        "Match Finished",
        2,
        1,
    ),
    "cross_midnight_live": fixture(
        "cross-midnight-live",
        "2026-06-03T21:30:00Z",
        "2H",
        "Second Half",
        1,
        1,
    ),
    "api_football_penalty_final": fixture(
        "penalty-final",
        "2026-06-03T19:00:00Z",
        "PEN",
        "Match Finished",
        1,
        1,
    ),
    "cancelled": fixture(
        "cancelled",
        "2026-06-03T18:00:00Z",
        "CANC",
        "Match Cancelled",
    ),
}


def print_match_decisions(name: str, match: dict) -> None:
    expected = match_lifecycle.expected_ft_check_utc(match)
    kickoff = match_lifecycle.fixture_kickoff_utc(match)
    print(f"\n[{name}] fixture_id={match_lifecycle.fixture_identity(match)}")
    print(f"  kickoff_utc: {kickoff.isoformat() if kickoff else 'n/a'}")
    print(f"  raw_status: {match.get('fixture', {}).get('status', {}).get('short')}")
    print(f"  normalized_status: {match_lifecycle.status_short(match)}")
    print(f"  is_live: {match_lifecycle.is_live(match)}")
    print(f"  is_ft: {match_lifecycle.is_ft(match)}")
    print(f"  is_terminal: {match_lifecycle.is_terminal(match)}")
    print(f"  should_track_fixture: {match_lifecycle.should_track_fixture(match, NOW_UTC)}")
    print(f"  expected_ft_check_utc: {expected.isoformat() if expected else 'n/a'}")


def print_state_pruning(name: str, match: dict) -> None:
    status = match_lifecycle.status_short(match)
    state = {
        "fixture_id": match_lifecycle.fixture_identity(match),
        "kickoff_utc": match.get("fixture", {}).get("date"),
        "expected_ft_utc": (
            match_lifecycle.expected_ft_check_utc(match).isoformat()
            if match_lifecycle.expected_ft_check_utc(match)
            else None
        ),
        "last_status": status,
        "last_score": deepcopy(match.get("goals")),
        "last_seen_utc": NOW_UTC.isoformat(),
        "terminal_utc": NOW_UTC.isoformat() if status in match_lifecycle.TERMINAL_STATUSES else None,
        "ft_announced": False,
        "memory_updated": False,
        "live_message_id": 123456789 if name == "cross_midnight_live" else None,
    }
    print(f"  state_is_prunable: {match_lifecycle.state_is_prunable(state, NOW_UTC)}")
    if state.get("live_message_id"):
        print("  live_message_id scenario: persisted ID would be reused; stale edit fallback is handled by discord_poster.")


def main() -> int:
    print(f"UTC lifecycle simulation at now_utc={NOW_UTC.isoformat()}")
    start, end = match_lifecycle.provider_window(NOW_UTC)
    print(f"provider_window: {start.isoformat()} -> {end.isoformat()}")

    for name, match in SAMPLES.items():
        print_match_decisions(name, match)
        print_state_pruning(name, match)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
