import os
import sys

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


def reset_api_provider_state() -> None:
    api_provider = sys.modules.get("modules.api_provider")
    if api_provider is None:
        return

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


def espn_match(fixture_id="737155", league_id=135):
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


def shootout_match():
    shootout_events = [
        ("100", "Home", "H1"),
        ("200", "Away", "A1"),
        ("100", "Home", "H2"),
        ("200", "Away", "A2"),
        ("100", "Home", "H3"),
        ("200", "Away", "A3"),
        ("100", "Home", "H4"),
    ]
    return {
        "fixture": {
            "id": "shootout-1",
            "date": "2026-05-30T18:00:00+00:00",
            "status": {"short": "FT", "elapsed": 120, "detail": "FT-Pens"},
        },
        "league": {"id": 135},
        "teams": {
            "home": {"id": "100", "name": "Home"},
            "away": {"id": "200", "name": "Away"},
        },
        "goals": {"home": 1, "away": 1},
        "winner": "Home",
        "events": [
            {
                "type": "Goal",
                "detail": "Normal Goal",
                "player": {"name": "Home Goal"},
                "team": {"id": "100", "name": "Home"},
                "time": {"elapsed": 5},
            },
            {
                "type": "Goal",
                "detail": "Penalty",
                "player": {"name": "Away Goal"},
                "team": {"id": "200", "name": "Away"},
                "time": {"elapsed": 64},
            },
            *[
                {
                    "type": "PenaltyShootout",
                    "detail": "Scored",
                    "player": {"name": player},
                    "team": {"id": team_id, "name": team_name},
                    "time": {"elapsed": 120},
                }
                for team_id, team_name, player in shootout_events
            ],
        ],
    }
