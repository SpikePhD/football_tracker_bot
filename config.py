import os
import re
import unicodedata
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from modules.configuration import load_effective_config

load_dotenv()


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} is not set in environment/.env")
    return value.strip()


BOT_TOKEN = _require_env("BOT_TOKEN")
API_KEY = _require_env("API_KEY")
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()


def _load_public_config() -> dict:
    return load_effective_config()


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


def _expect_int_range(cfg: dict, key: str, minimum: int, parent: str = "") -> int:
    value = int(_expect(cfg, key, int, parent))
    scope = f"{parent}.{key}" if parent else key
    if value < minimum:
        raise RuntimeError(f"config.json key '{scope}' must be >= {minimum}.")
    return value


def _load_display_lookup_window_hours(ops_cfg: dict) -> int:
    return _expect_int_range(
        ops_cfg,
        "football_display_lookup_window_hours",
        1,
        "operations",
    )


def _validate_timezone_name(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as e:
        raise RuntimeError(
            f"config.json key 'operations.timezone' is invalid: {value!r}. "
            "Use an IANA timezone name such as 'Europe/Rome'."
        ) from e
    return value


def _normalize_provider_alias_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9\s]", " ", text).lower()
    return re.sub(r"\s+", " ", text).strip()


def _load_provider_team_aliases(raw: dict) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise RuntimeError(
                "config.json key 'tracking.provider_team_aliases' must be a string-to-string object."
            )
        normalized_key = _normalize_provider_alias_text(key)
        normalized_value = _normalize_provider_alias_text(value)
        if not normalized_key or not normalized_value:
            raise RuntimeError(
                "config.json key 'tracking.provider_team_aliases' cannot contain empty aliases."
            )
        aliases[normalized_key] = normalized_value
    return aliases


_PUBLIC = _load_public_config()

bot_cfg = _expect(_PUBLIC, "bot", dict)
discord_cfg = _expect(_PUBLIC, "discord", dict)
administration_cfg = _expect(_PUBLIC, "administration", dict)
tracking_cfg = _expect(_PUBLIC, "tracking", dict)
ops_cfg = _expect(_PUBLIC, "operations", dict)
log_cfg = _expect(_PUBLIC, "log", dict)
memory_cfg = _expect(_PUBLIC, "memory", dict)
llm_cfg = _expect(_PUBLIC, "llm", dict)
search_cfg = _expect(_PUBLIC, "search", dict)

BOT_NAME = _expect(bot_cfg, "name", str, "bot")
CHANNEL_ID = int(_expect(discord_cfg, "channel_id", int, "discord"))
BOT_OWNER_USERS = _expect(administration_cfg, "owner_users", list, "administration")
BOT_OWNER_IDS = frozenset(int(owner["id"]) for owner in BOT_OWNER_USERS)

TRACKED_TENNIS_PLAYERS = _expect(tracking_cfg, "tennis_players", list, "tracking")
TRACKED_LEAGUE_IDS = _expect(tracking_cfg, "tracked_league_ids", list, "tracking")

league_name_map_raw = _expect(tracking_cfg, "league_name_map", dict, "tracking")
LEAGUE_NAME_MAP = {int(k): str(v) for k, v in league_name_map_raw.items()}

league_slug_map_raw = _expect(tracking_cfg, "league_slug_map", dict, "tracking")
LEAGUE_SLUG_MAP = {int(k): str(v) for k, v in league_slug_map_raw.items()}

PROVIDER_TEAM_ALIASES = _load_provider_team_aliases(
    _expect(tracking_cfg, "provider_team_aliases", dict, "tracking")
)

INTERNATIONAL_SLUGS = _expect(tracking_cfg, "international_slugs", list, "tracking")
DOMESTIC_SLUG_GROUPS = _expect(tracking_cfg, "domestic_slug_groups", dict, "tracking")

TENNIS_CACHE_TTL_SEC = int(_expect(ops_cfg, "tennis_cache_ttl_sec", int, "operations"))
TENNIS_UPCOMING_DAYS = int(_expect(ops_cfg, "tennis_upcoming_days", int, "operations"))
TENNIS_PRE_ANNOUNCE_HOURS = int(_expect(ops_cfg, "tennis_pre_announce_hours", int, "operations"))
TENNIS_EARLY_WATCH_POLL_INTERVAL_SEC = int(_expect(ops_cfg, "tennis_early_watch_poll_interval_sec", int, "operations"))
TENNIS_IMMINENT_WINDOW_MINUTES = int(_expect(ops_cfg, "tennis_imminent_window_minutes", int, "operations"))
TENNIS_IMMINENT_POLL_INTERVAL_SEC = int(_expect(ops_cfg, "tennis_imminent_poll_interval_sec", int, "operations"))
TENNIS_LIVE_POLL_INTERVAL_SEC = int(_expect(ops_cfg, "tennis_live_poll_interval_sec", int, "operations"))
TENNIS_FULL_DISCOVERY_INTERVAL_SEC = int(_expect(ops_cfg, "tennis_full_discovery_interval_sec", int, "operations"))
TENNIS_IDLE_DISCOVERY_INTERVAL_SEC = int(_expect(ops_cfg, "tennis_idle_discovery_interval_sec", int, "operations"))
TENNIS_POST_START_WATCH_HOURS = int(_expect(ops_cfg, "tennis_post_start_watch_hours", int, "operations"))
TENNIS_FINISHED_RETENTION_HOURS = _expect_int_range(
    ops_cfg,
    "tennis_finished_retention_hours",
    1,
    "operations",
)
LIVE_UPDATE_EDIT_WINDOW_MESSAGES = int(
    _expect(ops_cfg, "live_update_edit_window_messages", int, "operations")
)
OPERATIONS_TIMEZONE = _validate_timezone_name(_expect(ops_cfg, "timezone", str, "operations"))
FOOTBALL_PREMATCH_WINDOW_HOURS = _expect_int_range(ops_cfg, "football_prematch_window_hours", 0, "operations")
FOOTBALL_DISPLAY_LOOKUP_WINDOW_HOURS = _load_display_lookup_window_hours(ops_cfg)
FOOTBALL_FINISHED_RETENTION_HOURS = _expect_int_range(ops_cfg, "football_finished_retention_hours", 1, "operations")
FOOTBALL_STATE_RETENTION_HOURS = _expect_int_range(ops_cfg, "football_state_retention_hours", 1, "operations")
FOOTBALL_EXPECTED_FT_MINUTES = _expect_int_range(ops_cfg, "football_expected_ft_minutes", 1, "operations")
FOOTBALL_MAX_LIVE_DURATION_HOURS = _expect_int_range(ops_cfg, "football_max_live_duration_hours", 1, "operations")

provider_cfg = _expect(ops_cfg, "api_provider", dict, "operations")
API_FAILURE_THRESHOLD = int(_expect(provider_cfg, "failure_threshold", int, "operations.api_provider"))
API_RETRY_INTERVAL_SEC = int(_expect(provider_cfg, "retry_interval_sec", int, "operations.api_provider"))
API_ESPN_POLL_INTERVAL_SEC = int(_expect(provider_cfg, "espn_poll_interval_sec", int, "operations.api_provider"))
API_FALLBACK_POLL_INTERVAL_SEC = int(_expect(provider_cfg, "fallback_poll_interval_sec", int, "operations.api_provider"))
API_SCOREBOARD_CACHE_TTL_SEC = int(_expect(provider_cfg, "scoreboard_cache_ttl_sec", int, "operations.api_provider"))
API_ENRICH_MAX_CALLS_PER_TICK = int(_expect(provider_cfg, "enrich_max_calls_per_tick", int, "operations.api_provider"))
API_ENRICH_GRACE_SEC = int(_expect(provider_cfg, "enrich_grace_sec", int, "operations.api_provider"))
API_ENRICH_DAILY_CALL_BUDGET = int(_expect(provider_cfg, "enrich_daily_call_budget", int, "operations.api_provider"))
API_ENRICH_NEGATIVE_MAPPING_TTL_SEC = int(_expect(provider_cfg, "enrich_negative_mapping_ttl_sec", int, "operations.api_provider"))
API_ENRICH_INCOMPLETE_EVENTS_COOLDOWN_SEC = int(_expect(provider_cfg, "enrich_incomplete_events_cooldown_sec", int, "operations.api_provider"))
_enrich_retry_delays_raw = _expect(provider_cfg, "enrich_retry_delays_sec", list, "operations.api_provider")
if (
    not _enrich_retry_delays_raw
    or any(not isinstance(v, int) or v < 0 for v in _enrich_retry_delays_raw)
):
    raise RuntimeError(
        "config.json key 'operations.api_provider.enrich_retry_delays_sec' "
        "must be a non-empty list of non-negative integers."
    )
API_ENRICH_RETRY_DELAYS_SEC = [int(v) for v in _enrich_retry_delays_raw]

LOG_FILE_PATH = _expect(log_cfg, "file_path", str, "log")
LOG_FILE_MAX_BYTES = int(_expect(log_cfg, "file_max_bytes", int, "log"))
LOG_FILE_BACKUP_COUNT = int(_expect(log_cfg, "file_backup_count", int, "log"))
LOG_EXPORT_DEFAULT_LINES = int(_expect(log_cfg, "export_default_lines", int, "log"))
LOG_EXPORT_MAX_LINES = int(_expect(log_cfg, "export_max_lines", int, "log"))
LOG_EXPORT_MAX_BYTES = int(_expect(log_cfg, "export_max_bytes", int, "log"))

MEMORY_STALE_THRESHOLD_DAYS = int(_expect(memory_cfg, "stale_threshold_days", int, "memory"))
ESPN_CACHE_TTL_SEC = int(_expect(memory_cfg, "espn_cache_ttl_sec", int, "memory"))
ROSTER_UNSUPPORTED_RETRY_DAYS = int(_expect(memory_cfg, "roster_unsupported_retry_days", int, "memory"))

LLM_BASE_URL = _expect(llm_cfg, "base_url", str, "llm")
LLM_MODEL = _expect(llm_cfg, "model", str, "llm")
LLM_SYSTEM_PROMPT = _expect(llm_cfg, "system_prompt", str, "llm")

TRUSTED_SPORT_DOMAINS = _expect(search_cfg, "trusted_sport_domains", list, "search")
WEB_SEARCH_MIN_TRUSTED_RESULTS = int(_expect(search_cfg, "min_trusted_results", int, "search"))


def build_league_slugs(primary_slug: str) -> list:
    domestic = DOMESTIC_SLUG_GROUPS.get(primary_slug, [primary_slug])
    return list(dict.fromkeys(domestic + INTERNATIONAL_SLUGS))
