"""Validated host configuration and secret-management foundation for the admin UI."""

from __future__ import annotations

import json
import hashlib
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values

from modules.storage import save_json_path, save_text_path

DEFAULT_CONFIG_PATH = Path("config.json")
LOCAL_CONFIG_PATH = Path("config.local.json")
ENV_PATH = Path(".env")
SECRET_NAMES = ("BOT_TOKEN", "API_KEY", "LLM_API_KEY")
_DYNAMIC_OBJECT_PATHS = {
    "tracking.league_name_map",
    "tracking.league_slug_map",
    "tracking.domestic_slug_groups",
    "tracking.provider_team_aliases",
}
_FIELD_DESCRIPTIONS = {
    "bot.name": "Display name used in bot messages and exports.",
    "discord.channel_id": "Only Discord channel in which bot commands and posts are accepted.",
    "administration.owner_users": (
        "Discord users authorized for sensitive administration. Add the numeric user ID and a descriptive label."
    ),
    "operations.live_update_edit_window_messages": (
        "Number of recent channel messages searched before a fresh live update is posted."
    ),
}

_SECTION_LABELS = {
    "bot": "Bot and Discord",
    "discord": "Bot and Discord",
    "administration": "Discord Owners",
    "tracking": "Football and Tennis Tracking",
    "operations": "Provider and Polling",
    "log": "Logging and Memory",
    "memory": "Logging and Memory",
    "llm": "Assistant and Search",
    "search": "Assistant and Search",
}

_FIELD_BOUNDS = {
    "discord.channel_id": {"minimum": 1},
    "operations.football_prematch_window_hours": {"minimum": 0},
    "operations.football_display_lookup_window_hours": {"minimum": 1},
    "operations.football_finished_retention_hours": {"minimum": 1},
    "operations.football_state_retention_hours": {"minimum": 1},
    "operations.football_expected_ft_minutes": {"minimum": 1},
    "operations.football_max_live_duration_hours": {"minimum": 1},
    "operations.tennis_cache_ttl_sec": {"minimum": 1},
    "operations.tennis_upcoming_days": {"minimum": 1},
    "operations.tennis_pre_announce_hours": {"minimum": 0},
    "operations.tennis_finished_retention_hours": {"minimum": 1},
    "operations.live_update_edit_window_messages": {"minimum": 1},
}


class ConfigurationError(RuntimeError):
    pass


def _read_json_object(path: Path, *, required: bool) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        if required:
            raise ConfigurationError(f"{path} is missing.")
        return {}
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{path} root must be a JSON object.")
    return raw


def _assert_known_keys(override: dict, base: dict, path: str = "") -> None:
    for key, value in override.items():
        scope = f"{path}.{key}" if path else key
        if key not in base:
            raise ConfigurationError(f"Unknown configuration key: {scope}")
        if isinstance(value, dict):
            if not isinstance(base[key], dict):
                raise ConfigurationError(f"Configuration key {scope} must not be an object.")
            if scope not in _DYNAMIC_OBJECT_PATHS:
                _assert_known_keys(value, base[key], scope)


def _deep_merge(base: dict, override: dict) -> dict:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _minimal_override(base: Any, value: Any) -> Any:
    """Return only values that differ from defaults, or None when identical."""
    if isinstance(base, dict) and isinstance(value, dict):
        result = {}
        for key in base:
            child = _minimal_override(base[key], value[key])
            if child is not None:
                result[key] = child
        return result or None
    return None if base == value else deepcopy(value)


def _field_sources(base: Any, local: Any, path: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    if isinstance(base, dict) and path not in _DYNAMIC_OBJECT_PATHS:
        local_dict = local if isinstance(local, dict) else {}
        for key, child in base.items():
            scope = f"{path}.{key}" if path else key
            result.update(_field_sources(child, local_dict.get(key), scope))
        return result
    result[path] = "local" if local is not None else "default"
    return result


def configuration_revision(config: dict | None = None) -> str:
    payload = json.dumps(config or load_effective_config(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _required(cfg: dict, key: str, expected_type, path: str):
    scope = f"{path}.{key}" if path else key
    if key not in cfg:
        raise ConfigurationError(f"Missing required configuration key: {scope}")
    value = cfg[key]
    if not isinstance(value, expected_type) or isinstance(value, bool) and expected_type is int:
        name = expected_type.__name__
        raise ConfigurationError(f"Configuration key {scope} must be {name}.")
    return value


def _positive_int(cfg: dict, key: str, path: str, minimum: int = 1) -> int:
    value = _required(cfg, key, int, path)
    if value < minimum:
        raise ConfigurationError(f"Configuration key {path}.{key} must be >= {minimum}.")
    return value


def _exact_keys(cfg: dict, allowed: set[str], path: str) -> None:
    unknown = set(cfg) - allowed
    missing = allowed - set(cfg)
    if unknown:
        raise ConfigurationError(f"Unknown configuration key(s) in {path}: {', '.join(sorted(unknown))}")
    if missing:
        raise ConfigurationError(f"Missing configuration key(s) in {path}: {', '.join(sorted(missing))}")


def _validate_string_list(value: Any, path: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ConfigurationError(f"Configuration key {path} must be a non-empty list.")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ConfigurationError(f"Configuration key {path} must contain non-empty strings.")


def validate_config(cfg: dict) -> dict:
    """Validate a complete effective configuration and return a defensive copy."""
    if not isinstance(cfg, dict):
        raise ConfigurationError("Effective configuration root must be an object.")
    required_sections = {
        "bot", "discord", "administration", "tracking", "operations",
        "log", "memory", "llm", "search",
    }
    unknown = set(cfg) - required_sections
    missing = required_sections - set(cfg)
    if unknown:
        raise ConfigurationError(f"Unknown configuration section(s): {', '.join(sorted(unknown))}")
    if missing:
        raise ConfigurationError(f"Missing configuration section(s): {', '.join(sorted(missing))}")

    bot = _required(cfg, "bot", dict, "")
    _exact_keys(bot, {"name"}, "bot")
    if not _required(bot, "name", str, "bot").strip():
        raise ConfigurationError("Configuration key bot.name cannot be empty.")

    discord_cfg = _required(cfg, "discord", dict, "")
    _exact_keys(discord_cfg, {"channel_id"}, "discord")
    _positive_int(discord_cfg, "channel_id", "discord")

    admin = _required(cfg, "administration", dict, "")
    _exact_keys(admin, {"owner_users"}, "administration")
    owners = _required(admin, "owner_users", list, "administration")
    seen_owner_ids: set[int] = set()
    for index, owner in enumerate(owners):
        path = f"administration.owner_users[{index}]"
        if not isinstance(owner, dict) or set(owner) != {"id", "label"}:
            raise ConfigurationError(f"{path} must contain exactly id and label.")
        owner_id = _positive_int(owner, "id", path)
        label = _required(owner, "label", str, path)
        if not label.strip():
            raise ConfigurationError(f"{path}.label cannot be empty.")
        if owner_id in seen_owner_ids:
            raise ConfigurationError(f"Duplicate configured owner ID: {owner_id}")
        seen_owner_ids.add(owner_id)

    tracking = _required(cfg, "tracking", dict, "")
    _exact_keys(tracking, {
        "tennis_players", "tracked_league_ids", "league_name_map",
        "league_slug_map", "international_slugs", "domestic_slug_groups",
        "provider_team_aliases",
    }, "tracking")
    _validate_string_list(_required(tracking, "tennis_players", list, "tracking"), "tracking.tennis_players")
    league_ids = _required(tracking, "tracked_league_ids", list, "tracking")
    if not league_ids or any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in league_ids):
        raise ConfigurationError("tracking.tracked_league_ids must contain positive integers.")
    if len(set(league_ids)) != len(league_ids):
        raise ConfigurationError("tracking.tracked_league_ids cannot contain duplicates.")
    league_names = _required(tracking, "league_name_map", dict, "tracking")
    league_slugs = _required(tracking, "league_slug_map", dict, "tracking")
    if any(not str(key).isdigit() or not isinstance(value, str) or not value.strip() for key, value in league_names.items()):
        raise ConfigurationError("tracking.league_name_map must map numeric IDs to non-empty names.")
    if any(not str(key).isdigit() or not isinstance(value, str) or not value.strip() for key, value in league_slugs.items()):
        raise ConfigurationError("tracking.league_slug_map must map numeric IDs to non-empty slugs.")
    domestic_groups = _required(tracking, "domestic_slug_groups", dict, "tracking")
    for key, values in domestic_groups.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigurationError("tracking.domestic_slug_groups keys must be non-empty strings.")
        _validate_string_list(values, f"tracking.domestic_slug_groups.{key}")
    aliases = _required(tracking, "provider_team_aliases", dict, "tracking")
    if any(not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip() for key, value in aliases.items()):
        raise ConfigurationError("tracking.provider_team_aliases must map non-empty strings.")
    _validate_string_list(_required(tracking, "international_slugs", list, "tracking"), "tracking.international_slugs", allow_empty=True)

    operations = _required(cfg, "operations", dict, "")
    _exact_keys(operations, {
        "timezone", "football_prematch_window_hours",
        "football_display_lookup_window_hours", "football_finished_retention_hours",
        "football_state_retention_hours", "football_expected_ft_minutes",
        "football_max_live_duration_hours", "tennis_cache_ttl_sec",
        "tennis_upcoming_days", "tennis_pre_announce_hours",
        "tennis_finished_retention_hours", "live_update_edit_window_messages",
        "api_provider",
    }, "operations")
    timezone_name = _required(operations, "timezone", str, "operations")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigurationError(f"Invalid operations.timezone: {timezone_name!r}") from exc
    for key, minimum in {
        "football_prematch_window_hours": 0,
        "football_display_lookup_window_hours": 1,
        "football_finished_retention_hours": 1,
        "football_state_retention_hours": 1,
        "football_expected_ft_minutes": 1,
        "football_max_live_duration_hours": 1,
        "tennis_cache_ttl_sec": 1,
        "tennis_upcoming_days": 1,
        "tennis_pre_announce_hours": 0,
        "tennis_finished_retention_hours": 1,
        "live_update_edit_window_messages": 1,
    }.items():
        _positive_int(operations, key, "operations", minimum)
    provider = _required(operations, "api_provider", dict, "operations")
    _exact_keys(provider, {
        "failure_threshold", "retry_interval_sec", "espn_poll_interval_sec",
        "fallback_poll_interval_sec", "scoreboard_cache_ttl_sec",
        "enrich_max_calls_per_tick", "enrich_grace_sec", "enrich_daily_call_budget",
        "enrich_negative_mapping_ttl_sec", "enrich_incomplete_events_cooldown_sec",
        "enrich_retry_delays_sec",
    }, "operations.api_provider")
    for key in (
        "failure_threshold", "retry_interval_sec", "espn_poll_interval_sec",
        "fallback_poll_interval_sec", "scoreboard_cache_ttl_sec",
        "enrich_max_calls_per_tick", "enrich_grace_sec", "enrich_daily_call_budget",
        "enrich_negative_mapping_ttl_sec", "enrich_incomplete_events_cooldown_sec",
    ):
        _positive_int(provider, key, "operations.api_provider", 0 if key == "enrich_grace_sec" else 1)
    delays = _required(provider, "enrich_retry_delays_sec", list, "operations.api_provider")
    if not delays or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in delays):
        raise ConfigurationError("operations.api_provider.enrich_retry_delays_sec must contain non-negative integers.")

    log_cfg = _required(cfg, "log", dict, "")
    _exact_keys(log_cfg, {
        "file_path", "file_max_bytes", "file_backup_count", "export_default_lines",
        "export_max_lines", "export_max_bytes",
    }, "log")
    if not _required(log_cfg, "file_path", str, "log").strip():
        raise ConfigurationError("log.file_path cannot be empty.")
    for key in ("file_max_bytes", "file_backup_count", "export_default_lines", "export_max_lines", "export_max_bytes"):
        _positive_int(log_cfg, key, "log", 0 if key == "file_backup_count" else 1)
    if log_cfg["export_default_lines"] > log_cfg["export_max_lines"]:
        raise ConfigurationError("log.export_default_lines cannot exceed log.export_max_lines.")

    memory = _required(cfg, "memory", dict, "")
    _exact_keys(memory, {"stale_threshold_days", "espn_cache_ttl_sec"}, "memory")
    _positive_int(memory, "stale_threshold_days", "memory")
    _positive_int(memory, "espn_cache_ttl_sec", "memory")

    llm = _required(cfg, "llm", dict, "")
    _exact_keys(llm, {"base_url", "model", "system_prompt"}, "llm")
    base_url = _required(llm, "base_url", str, "llm")
    parsed_url = urlparse(base_url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        raise ConfigurationError("llm.base_url must be an absolute HTTP(S) URL.")
    for key in ("model", "system_prompt"):
        if not _required(llm, key, str, "llm").strip():
            raise ConfigurationError(f"llm.{key} cannot be empty.")

    search = _required(cfg, "search", dict, "")
    _exact_keys(search, {"trusted_sport_domains", "min_trusted_results"}, "search")
    _validate_string_list(_required(search, "trusted_sport_domains", list, "search"), "search.trusted_sport_domains")
    _positive_int(search, "min_trusted_results", "search")
    return deepcopy(cfg)


def load_effective_config(
    default_path: Path = DEFAULT_CONFIG_PATH,
    local_path: Path = LOCAL_CONFIG_PATH,
) -> dict:
    base = _read_json_object(default_path, required=True)
    local = _read_json_object(local_path, required=False)
    _assert_known_keys(local, base)
    effective = _deep_merge(base, local)

    # One-release migration bridge: local Discord config wins, otherwise use
    # the existing CHANNEL_ID environment/.env value before the committed placeholder.
    local_channel = (local.get("discord") or {}).get("channel_id")
    legacy_channel = os.getenv("CHANNEL_ID", "").strip()
    if local_channel is None and legacy_channel:
        try:
            effective.setdefault("discord", {})["channel_id"] = int(legacy_channel)
        except ValueError as exc:
            raise ConfigurationError("Legacy CHANNEL_ID must be a numeric Discord channel ID.") from exc
    return validate_config(effective)


def write_local_overrides(
    overrides: dict,
    default_path: Path = DEFAULT_CONFIG_PATH,
    local_path: Path = LOCAL_CONFIG_PATH,
) -> None:
    base = _read_json_object(default_path, required=True)
    if not isinstance(overrides, dict):
        raise ConfigurationError("Local configuration overrides must be an object.")
    _assert_known_keys(overrides, base)
    validate_config(_deep_merge(base, overrides))
    save_json_path(local_path, overrides, ensure_ascii=False)


def save_complete_config(
    config: dict,
    *,
    expected_revision: str | None = None,
    default_path: Path = DEFAULT_CONFIG_PATH,
    local_path: Path = LOCAL_CONFIG_PATH,
) -> dict:
    """Validate and save a complete draft as the smallest local override."""
    current = load_effective_config(default_path, local_path)
    current_revision = configuration_revision(current)
    if expected_revision is not None and expected_revision != current_revision:
        raise ConfigurationError("Configuration changed since it was loaded.")
    validated = validate_config(config)
    base = _read_json_object(default_path, required=True)
    overrides = _minimal_override(base, validated) or {}
    write_local_overrides(overrides, default_path, local_path)
    return {
        "config": validated,
        "overrides": overrides,
        "revision": configuration_revision(validated),
    }


def configuration_catalog(base_config: dict | None = None) -> list[dict]:
    """Return non-secret field metadata for the future configuration interface."""
    cfg = base_config or _read_json_object(DEFAULT_CONFIG_PATH, required=True)
    fields: list[dict] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict) and path not in _DYNAMIC_OBJECT_PATHS:
            for key, child in value.items():
                walk(child, f"{path}.{key}" if path else key)
            return
        value_type = "array" if isinstance(value, list) else "object" if isinstance(value, dict) else type(value).__name__
        category = path.split(".", 1)[0]
        editor = "json"
        if isinstance(value, bool):
            editor = "toggle"
        elif isinstance(value, int):
            editor = "number"
        elif isinstance(value, str):
            editor = "textarea" if len(value) > 120 else "text"
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            editor = "tags"
        if path == "discord.channel_id":
            editor = "text"
        elif path == "administration.owner_users":
            editor = "owners"
        fields.append({
            "path": path,
            "category": category,
            "section": _SECTION_LABELS.get(category, category.title()),
            "label": path.rsplit(".", 1)[-1].replace("_", " ").title(),
            "description": _FIELD_DESCRIPTIONS.get(
                path,
                f"Restart-required configuration value for {path}.",
            ),
            "value_type": value_type,
            "editor": editor,
            **_FIELD_BOUNDS.get(path, {}),
            "secret": False,
            "restart_required": True,
        })

    walk(cfg, "")
    for name in SECRET_NAMES:
        fields.append({
            "path": f"secrets.{name}",
            "category": "secrets",
            "section": "Secrets",
            "label": name.replace("_", " ").title(),
            "description": "Secret environment value; the stored value is never returned.",
            "value_type": "secret",
            "editor": "secret",
            "secret": True,
            "restart_required": True,
        })
    return fields


def secret_status(env_path: Path = ENV_PATH) -> dict[str, dict]:
    values = dotenv_values(env_path) if env_path.exists() else {}
    status: dict[str, dict] = {}
    for name in SECRET_NAMES:
        value = str(values.get(name) or os.getenv(name) or "")
        status[name] = {
            "configured": bool(value),
            "masked": (f"***{value[-4:]}" if len(value) > 4 else "***") if value else None,
        }
    return status


def replace_secret(name: str, value: str, env_path: Path = ENV_PATH) -> None:
    if name not in SECRET_NAMES:
        raise ConfigurationError(f"Unsupported secret name: {name}")
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise ConfigurationError(f"Secret {name} must be a non-empty single-line value.")
    lines = env_path.read_text(encoding="utf-8-sig").splitlines() if env_path.exists() else []
    replacement = f"{name}={json.dumps(value)}"
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=")
    updated: list[str] = []
    replaced = False
    for line in lines:
        if pattern.match(line) and not replaced:
            updated.append(replacement)
            replaced = True
        elif not pattern.match(line):
            updated.append(line)
    if not replaced:
        updated.append(replacement)
    save_text_path(env_path, "\n".join(updated) + "\n", mode=0o600)


def configuration_snapshot() -> dict:
    """Return the effective non-secret config plus masked secret status."""
    defaults = _read_json_object(DEFAULT_CONFIG_PATH, required=True)
    local = _read_json_object(LOCAL_CONFIG_PATH, required=False)
    effective = load_effective_config()
    sources = _field_sources(defaults, local)
    if (local.get("discord") or {}).get("channel_id") is None and os.getenv("CHANNEL_ID", "").strip():
        sources["discord.channel_id"] = "legacy_environment"
    return {
        "config": effective,
        "defaults": defaults,
        "overrides": local,
        "sources": sources,
        "revision": configuration_revision(effective),
        "fields": configuration_catalog(defaults),
        "secrets": secret_status(),
        "restart_required": True,
    }
