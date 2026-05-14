import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is not set in environment/.env")
    return value.strip()


BOT_TOKEN = _require_env("BOT_TOKEN")
API_KEY = _require_env("API_KEY")
try:
    CHANNEL_ID = int(_require_env("CHANNEL_ID"))
except ValueError as e:
    raise RuntimeError("CHANNEL_ID must be a numeric Discord channel ID.") from e

# Optional additional secret for secondary football provider usage.
SECONDARY_API_KEY = os.getenv("SECONDARY_API_KEY", "").strip()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()


def _load_public_config() -> dict:
    path = Path("config.json")
    if not path.exists():
        raise RuntimeError(
            "config.json is missing. Create it from config.example.json and restart the bot."
        )

    try:
        # Accept UTF-8 with or without BOM to avoid Windows editor compatibility issues.
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise RuntimeError(f"config.json is not valid JSON: {e}") from e

    if not isinstance(raw, dict):
        raise RuntimeError("config.json root must be a JSON object.")

    return raw


def _expect(cfg: dict, key: str, expected_type, parent: str = ""):
    scope = f"{parent}.{key}" if parent else key
    if key not in cfg:
        raise RuntimeError(f"config.json is missing required key: {scope}")
    value = cfg[key]
    if not isinstance(value, expected_type):
        expected_name = (
            ", ".join(t.__name__ for t in expected_type)
            if isinstance(expected_type, tuple)
            else expected_type.__name__
        )
        raise RuntimeError(
            f"config.json key '{scope}' has invalid type: "
            f"expected {expected_name}, got {type(value).__name__}"
        )
    return value


_PUBLIC = _load_public_config()

bot_cfg = _expect(_PUBLIC, "bot", dict)
tracking_cfg = _expect(_PUBLIC, "tracking", dict)
ops_cfg = _expect(_PUBLIC, "operations", dict)
log_cfg = _expect(_PUBLIC, "log", dict)
memory_cfg = _expect(_PUBLIC, "memory", dict)
llm_cfg = _expect(_PUBLIC, "llm", dict)
search_cfg = _expect(_PUBLIC, "search", dict)

BOT_NAME = _expect(bot_cfg, "name", str, "bot")

TRACKED_TENNIS_PLAYERS = _expect(tracking_cfg, "tennis_players", list, "tracking")
TRACKED_LEAGUE_IDS = _expect(tracking_cfg, "tracked_league_ids", list, "tracking")

league_name_map_raw = _expect(tracking_cfg, "league_name_map", dict, "tracking")
LEAGUE_NAME_MAP = {int(k): str(v) for k, v in league_name_map_raw.items()}

league_slug_map_raw = _expect(tracking_cfg, "league_slug_map", dict, "tracking")
LEAGUE_SLUG_MAP = {int(k): str(v) for k, v in league_slug_map_raw.items()}

INTERNATIONAL_SLUGS = _expect(tracking_cfg, "international_slugs", list, "tracking")
DOMESTIC_SLUG_GROUPS = _expect(tracking_cfg, "domestic_slug_groups", dict, "tracking")

TENNIS_CACHE_TTL_SEC = int(_expect(ops_cfg, "tennis_cache_ttl_sec", int, "operations"))
TENNIS_UPCOMING_DAYS = int(_expect(ops_cfg, "tennis_upcoming_days", int, "operations"))
TENNIS_PRE_ANNOUNCE_HOURS = int(_expect(ops_cfg, "tennis_pre_announce_hours", int, "operations"))
LIVE_UPDATE_EDIT_WINDOW_MESSAGES = int(
    _expect(ops_cfg, "live_update_edit_window_messages", int, "operations")
)

provider_cfg = _expect(ops_cfg, "api_provider", dict, "operations")
API_FAILURE_THRESHOLD = int(_expect(provider_cfg, "failure_threshold", int, "operations.api_provider"))
API_RETRY_INTERVAL_SEC = int(_expect(provider_cfg, "retry_interval_sec", int, "operations.api_provider"))
API_ESPN_POLL_INTERVAL_SEC = int(_expect(provider_cfg, "espn_poll_interval_sec", int, "operations.api_provider"))
API_FALLBACK_POLL_INTERVAL_SEC = int(_expect(provider_cfg, "fallback_poll_interval_sec", int, "operations.api_provider"))
API_SCOREBOARD_CACHE_TTL_SEC = int(_expect(provider_cfg, "scoreboard_cache_ttl_sec", int, "operations.api_provider"))
API_ENRICH_MAX_CALLS_PER_TICK = int(_expect(provider_cfg, "enrich_max_calls_per_tick", int, "operations.api_provider"))
API_ENRICH_GRACE_SEC = int(_expect(provider_cfg, "enrich_grace_sec", int, "operations.api_provider"))

LOG_FILE_PATH = _expect(log_cfg, "file_path", str, "log")
LOG_FILE_MAX_BYTES = int(_expect(log_cfg, "file_max_bytes", int, "log"))
LOG_FILE_BACKUP_COUNT = int(_expect(log_cfg, "file_backup_count", int, "log"))
LOG_EXPORT_DEFAULT_LINES = int(_expect(log_cfg, "export_default_lines", int, "log"))
LOG_EXPORT_MAX_LINES = int(_expect(log_cfg, "export_max_lines", int, "log"))
LOG_EXPORT_MAX_BYTES = int(_expect(log_cfg, "export_max_bytes", int, "log"))

MEMORY_STALE_THRESHOLD_DAYS = int(_expect(memory_cfg, "stale_threshold_days", int, "memory"))
ESPN_CACHE_TTL_SEC = int(_expect(memory_cfg, "espn_cache_ttl_sec", int, "memory"))

LLM_BASE_URL = _expect(llm_cfg, "base_url", str, "llm")
LLM_MODEL = _expect(llm_cfg, "model", str, "llm")
LLM_SYSTEM_PROMPT = _expect(llm_cfg, "system_prompt", str, "llm")

TRUSTED_SPORT_DOMAINS = _expect(search_cfg, "trusted_sport_domains", list, "search")
WEB_SEARCH_MIN_TRUSTED_RESULTS = int(_expect(search_cfg, "min_trusted_results", int, "search"))


def build_league_slugs(primary_slug: str) -> list:
    domestic = DOMESTIC_SLUG_GROUPS.get(primary_slug, [primary_slug])
    return list(dict.fromkeys(domestic + INTERNATIONAL_SLUGS))
