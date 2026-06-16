import json
import logging
import os
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from modules import match_lifecycle
from modules.storage import BOT_MEMORY_DIR
from utils.time_utils import parse_provider_utc

logger = logging.getLogger(__name__)

MATCH_STATE_FILE = "match_state.json"
LEGACY_FT_STATE_FILE = "ft_state.json"
_state_lock = threading.RLock()
_DEFAULT_STATE = {
    "version": 1,
    "migrated_from_ft_state": False,
    "fixtures": {},
}


def _memory_dir(memory_dir: Path | None = None) -> Path:
    return memory_dir or BOT_MEMORY_DIR


def _state_path(memory_dir: Path | None = None) -> Path:
    return _memory_dir(memory_dir) / MATCH_STATE_FILE


def _legacy_ft_path(memory_dir: Path | None = None) -> Path:
    return _memory_dir(memory_dir) / LEGACY_FT_STATE_FILE


def _default_state() -> dict:
    return deepcopy(_DEFAULT_STATE)


def _normalize_state(state: dict | None) -> dict:
    normalized = _default_state()
    if isinstance(state, dict):
        normalized.update({k: v for k, v in state.items() if k != "fixtures"})
        fixtures = state.get("fixtures", {})
        normalized["fixtures"] = fixtures if isinstance(fixtures, dict) else {}
        for fixture_id, fixture in list(normalized["fixtures"].items()):
            if not isinstance(fixture, dict):
                del normalized["fixtures"][fixture_id]
                continue
            provider_ids = fixture.get("provider_ids")
            if not isinstance(provider_ids, dict):
                provider_ids = {}
            fixture["provider_ids"] = {
                str(provider): str(provider_fixture_id)
                for provider, provider_fixture_id in provider_ids.items()
                if provider_fixture_id is not None
            }
    return normalized


def load_match_state(memory_dir: Path | None = None) -> dict:
    with _state_lock:
        path = _state_path(memory_dir)
        try:
            return _normalize_state(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return _default_state()
        except json.JSONDecodeError as e:
            logger.error("match_state: %s is corrupt (%s), using defaults.", path.name, e)
            return _default_state()


def save_match_state(state: dict, memory_dir: Path | None = None) -> None:
    with _state_lock:
        target_dir = _memory_dir(memory_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = _state_path(memory_dir)
        tmp = target_dir / f"{MATCH_STATE_FILE}.{uuid.uuid4().hex}.tmp"
        try:
            tmp.write_text(
                json.dumps(_normalize_state(state), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(tmp, target)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            finally:
                raise


def update_match_state(mutator: Callable[[dict], object], memory_dir: Path | None = None) -> object:
    with _state_lock:
        state = load_match_state(memory_dir=memory_dir)
        result = mutator(state)
        save_match_state(state, memory_dir=memory_dir)
        return result


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _score(match: dict) -> dict:
    goals = match.get("goals", {}) or {}
    return {"home": goals.get("home"), "away": goals.get("away")}


def _provider_name(provider: str | None) -> str:
    return str(provider or "espn")


def _raw_fixture_id(match: dict) -> str | None:
    fixture_id = match.get("fixture", {}).get("id")
    return str(fixture_id) if fixture_id is not None else None


def _provider_fixture_id(match: dict, provider: str) -> str | None:
    provider_fixture_id = match.get("provider_fixture_id")
    if provider_fixture_id is not None:
        return str(provider_fixture_id)
    provider_ids = match.get("provider_ids", {})
    if isinstance(provider_ids, dict) and provider_ids.get(provider) is not None:
        return str(provider_ids[provider])
    return _raw_fixture_id(match)


def _merge_fixture_records(canonical_id: str, canonical: dict, duplicate: dict) -> dict:
    canonical["fixture_id"] = canonical_id
    canonical["provider_ids"] = {
        **(duplicate.get("provider_ids") if isinstance(duplicate.get("provider_ids"), dict) else {}),
        **(canonical.get("provider_ids") if isinstance(canonical.get("provider_ids"), dict) else {}),
    }

    for flag in ("ft_announced", "memory_updated"):
        canonical[flag] = bool(canonical.get(flag)) or bool(duplicate.get(flag))

    if canonical.get("live_message_id") is None and duplicate.get("live_message_id") is not None:
        canonical["live_message_id"] = duplicate.get("live_message_id")

    for key in (
        "provider",
        "kickoff_utc",
        "expected_ft_utc",
        "last_status",
        "last_score",
        "last_seen_utc",
        "terminal_utc",
    ):
        if canonical.get(key) is None and duplicate.get(key) is not None:
            canonical[key] = duplicate.get(key)

    return canonical


def _find_canonical_fixture_id_in_state(state: dict, provider: str, provider_fixture_id: str) -> str | None:
    provider = _provider_name(provider)
    provider_fixture_id = str(provider_fixture_id)
    for fixture_id, fixture in state.get("fixtures", {}).items():
        provider_ids = fixture.get("provider_ids", {})
        if isinstance(provider_ids, dict) and str(provider_ids.get(provider)) == provider_fixture_id:
            return str(fixture_id)
    return None


def _link_provider_fixture_id_in_state(
    state: dict,
    canonical_fixture_id,
    provider: str,
    provider_fixture_id,
) -> dict:
    canonical_id = str(canonical_fixture_id)
    provider = _provider_name(provider)
    provider_id = str(provider_fixture_id)

    fixtures = state.setdefault("fixtures", {})
    fixture = fixtures.setdefault(
        canonical_id,
        {
            "fixture_id": canonical_id,
            "ft_announced": False,
            "memory_updated": False,
            "provider_ids": {},
        },
    )

    existing_key = _find_canonical_fixture_id_in_state(state, provider, provider_id)
    if existing_key and existing_key != canonical_id:
        duplicate = fixtures.pop(existing_key)
        fixture = _merge_fixture_records(canonical_id, fixture, duplicate)
        fixtures[canonical_id] = fixture

    provider_ids = fixture.setdefault("provider_ids", {})
    provider_ids[provider] = provider_id
    return deepcopy(fixture)


def link_provider_fixture_id(
    canonical_fixture_id,
    provider: str,
    provider_fixture_id,
    memory_dir: Path | None = None,
) -> dict:
    def mutator(state: dict) -> dict:
        return _link_provider_fixture_id_in_state(
            state,
            canonical_fixture_id,
            provider,
            provider_fixture_id,
        )

    return update_match_state(mutator, memory_dir=memory_dir)


def find_canonical_fixture_id(provider: str, provider_fixture_id, memory_dir: Path | None = None) -> str | None:
    state = load_match_state(memory_dir=memory_dir)
    return _find_canonical_fixture_id_in_state(state, provider, str(provider_fixture_id))


def get_provider_fixture_id(fixture_id, provider: str, memory_dir: Path | None = None) -> str | None:
    fixture = get_fixture_state(fixture_id, memory_dir=memory_dir)
    if not fixture:
        return None
    provider_ids = fixture.get("provider_ids", {})
    if not isinstance(provider_ids, dict):
        return None
    value = provider_ids.get(provider)
    return str(value) if value is not None else None


def upsert_fixture_from_match(
    match: dict,
    now_utc: datetime,
    source: str = "espn",
    memory_dir: Path | None = None,
) -> dict:
    fixture_id = match_lifecycle.fixture_identity(match)
    if not fixture_id:
        raise ValueError("Cannot persist fixture state without fixture.id")

    kickoff = match_lifecycle.fixture_kickoff_utc(match)
    expected_ft = match_lifecycle.expected_ft_check_utc(match)
    status = match_lifecycle.status_short(match)
    terminal_utc = now_utc if match_lifecycle.is_terminal(match) else None
    provider = _provider_name(match.get("provider") or source)
    provider_id = _provider_fixture_id(match, provider)

    def mutator(state: dict) -> dict:
        if provider_id is not None:
            linked_id = _find_canonical_fixture_id_in_state(state, provider, provider_id)
            if linked_id and linked_id != fixture_id:
                _link_provider_fixture_id_in_state(state, fixture_id, provider, provider_id)
        fixture = state["fixtures"].setdefault(
            fixture_id,
            {
                "fixture_id": fixture_id,
                "ft_announced": False,
                "memory_updated": False,
                "provider_ids": {},
            },
        )
        fixture.update(
            {
                "fixture_id": fixture_id,
                "provider": provider,
                "kickoff_utc": _iso(kickoff),
                "expected_ft_utc": _iso(expected_ft),
                "last_status": status,
                "last_score": _score(match),
                "last_seen_utc": _iso(now_utc),
            }
        )
        provider_ids = fixture.setdefault("provider_ids", {})
        if provider_id is not None:
            provider_ids[provider] = provider_id
        for extra_provider, extra_provider_id in (match.get("provider_ids") or {}).items():
            if extra_provider_id is not None:
                provider_ids[str(extra_provider)] = str(extra_provider_id)
        if terminal_utc and not fixture.get("terminal_utc"):
            fixture["terminal_utc"] = _iso(terminal_utc)
        return deepcopy(fixture)

    return update_match_state(mutator, memory_dir=memory_dir)


def get_fixture_state(fixture_id, memory_dir: Path | None = None) -> dict | None:
    state = load_match_state(memory_dir=memory_dir)
    fixture = state.get("fixtures", {}).get(str(fixture_id))
    return deepcopy(fixture) if fixture else None


def is_tracked(fixture_id, memory_dir: Path | None = None) -> bool:
    return get_fixture_state(fixture_id, memory_dir=memory_dir) is not None


def mark_ft_announced(fixture_id, memory_dir: Path | None = None) -> None:
    mid = str(fixture_id)

    def mutator(state: dict) -> None:
        fixture = state["fixtures"].setdefault("{}".format(mid), {"fixture_id": mid})
        fixture["ft_announced"] = True

    update_match_state(mutator, memory_dir=memory_dir)


def mark_memory_updated(fixture_id, memory_dir: Path | None = None) -> None:
    mid = str(fixture_id)

    def mutator(state: dict) -> None:
        fixture = state["fixtures"].setdefault(mid, {"fixture_id": mid})
        fixture["memory_updated"] = True

    update_match_state(mutator, memory_dir=memory_dir)


def update_live_message_id(fixture_id, message_id: int | None, memory_dir: Path | None = None) -> None:
    mid = str(fixture_id)

    def mutator(state: dict) -> None:
        fixture = state["fixtures"].setdefault(mid, {"fixture_id": mid})
        fixture["live_message_id"] = message_id

    update_match_state(mutator, memory_dir=memory_dir)


def migrate_ft_state_if_needed(memory_dir: Path | None = None) -> bool:
    with _state_lock:
        state = load_match_state(memory_dir=memory_dir)
        if state.get("migrated_from_ft_state"):
            return False

        legacy_path = _legacy_ft_path(memory_dir)
        if not legacy_path.exists():
            state["migrated_from_ft_state"] = True
            save_match_state(state, memory_dir=memory_dir)
            return False

        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("match_state: could not migrate %s: %s", legacy_path.name, e)
            state["migrated_from_ft_state"] = True
            save_match_state(state, memory_dir=memory_dir)
            return False

        count = 0
        for fixture_id in legacy.get("announced_ids", []):
            mid = str(fixture_id)
            fixture = state["fixtures"].setdefault(mid, {"fixture_id": mid})
            fixture["provider"] = fixture.get("provider") or "legacy-ft-state"
            fixture["ft_announced"] = True
            fixture.setdefault("memory_updated", False)
            fixture["legacy_ft_state_date"] = legacy.get("last_reset_date")
            count += 1
        state["migrated_from_ft_state"] = True
        save_match_state(state, memory_dir=memory_dir)
        logger.info("match_state: migrated %d FT announcement id(s) from %s.", count, legacy_path.name)
        return count > 0


def prune_match_tracking_state(now_utc: datetime, memory_dir: Path | None = None) -> list[str]:
    def mutator(state: dict) -> list[str]:
        removed = []
        for fixture_id, fixture in list(state.get("fixtures", {}).items()):
            if match_lifecycle.state_is_prunable(fixture, now_utc):
                del state["fixtures"][fixture_id]
                removed.append(fixture_id)
        return removed

    return update_match_state(mutator, memory_dir=memory_dir)


def expected_ft_due_fixture_ids(now_utc: datetime, memory_dir: Path | None = None) -> list[str]:
    now_utc = now_utc.astimezone(timezone.utc)
    state = load_match_state(memory_dir=memory_dir)
    due = []
    for fixture_id, fixture in state.get("fixtures", {}).items():
        if fixture.get("last_status") in match_lifecycle.TERMINAL_NON_FT_STATUSES:
            continue
        if fixture.get("ft_announced") and fixture.get("memory_updated"):
            continue
        expected = fixture.get("expected_ft_utc")
        if not expected:
            continue
        if parse_provider_utc(expected) <= now_utc:
            due.append(fixture_id)
    return due


def next_unresolved_expected_ft_utc(now_utc: datetime, memory_dir: Path | None = None) -> datetime | None:
    now_utc = now_utc.astimezone(timezone.utc)
    state = load_match_state(memory_dir=memory_dir)
    candidates = []
    for fixture in state.get("fixtures", {}).values():
        if fixture.get("last_status") in match_lifecycle.TERMINAL_NON_FT_STATUSES:
            continue
        if fixture.get("ft_announced") and fixture.get("memory_updated"):
            continue
        if match_lifecycle.state_is_prunable(fixture, now_utc):
            continue
        expected = fixture.get("expected_ft_utc")
        if not expected:
            continue
        try:
            expected_utc = parse_provider_utc(expected)
        except Exception:
            continue
        if expected_utc > now_utc:
            candidates.append(expected_utc)
    return min(candidates) if candidates else None
