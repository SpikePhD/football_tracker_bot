# modules/api_provider.py
# Unified API provider with ESPN as primary and API-Football as fallback.
# All other modules import from here instead of directly from api_client or espn_client.

import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

import aiohttp

import asyncio

from config import (
    API_ENRICH_DAILY_CALL_BUDGET,
    API_ENRICH_MAX_CALLS_PER_TICK,
    API_ENRICH_GRACE_SEC,
    API_ENRICH_INCOMPLETE_EVENTS_COOLDOWN_SEC,
    API_ENRICH_NEGATIVE_MAPPING_TTL_SEC,
    API_ENRICH_RETRY_DELAYS_SEC as CONFIG_API_ENRICH_RETRY_DELAYS_SEC,
    API_ESPN_POLL_INTERVAL_SEC,
    API_FAILURE_THRESHOLD,
    API_FALLBACK_POLL_INTERVAL_SEC,
    API_RETRY_INTERVAL_SEC,
    API_SCOREBOARD_CACHE_TTL_SEC,
    FOOTBALL_MATCH_LOOKUP_WINDOW_HOURS,
    LEAGUE_SLUG_MAP,
    TENNIS_CACHE_TTL_SEC,
    TENNIS_UPCOMING_DAYS,
    build_league_slugs,
)
from modules import match_lifecycle
from utils import espn_client
from utils import espn_tennis_client
from utils import api_client
from utils.time_utils import (
    bot_now,
    get_current_season_year,
    get_bot_local_date_string,
    parse_provider_utc,
    to_bot_tz,
    utc_now,
)
from utils.event_formatter import is_shootout_event, normalize_api_football_events

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────

FAILURE_THRESHOLD = API_FAILURE_THRESHOLD
RETRY_INTERVAL_SEC = API_RETRY_INTERVAL_SEC
ESPN_POLL_INTERVAL = API_ESPN_POLL_INTERVAL_SEC
FALLBACK_POLL_INTERVAL = API_FALLBACK_POLL_INTERVAL_SEC

# ── Health state ──────────────────────────────────────────────────────────────

_espn_healthy: bool = True
_consecutive_failures: int = 0
_retry_after: datetime | None = None

# ── Scoreboard cache ──────────────────────────────────────────────────────────

_football_scoreboard_cache: dict[str, dict] = {}
_api_football_date_cache: dict[str, dict] = {}
_cache: list[dict] = []
_cache_date: str | None = None
_cache_ts: datetime | None = None
CACHE_TTL_SEC = API_SCOREBOARD_CACHE_TTL_SEC

# Tennis cache
_tennis_cache: list[dict] = []
_tennis_cache_date: str | None = None
_tennis_cache_ts: datetime | None = None
_TRACKED_SLUGS: set[str] = set(LEAGUE_SLUG_MAP.values())
_enrich_attempted_date: str | None = None
_enrich_tick_key: str | None = None
_enrich_tick_count: int = 0
_enrich_retry_states: dict[str, dict] = {}
_api_fixture_id_cache: dict[str, int] = {}
_api_fixture_id_cache_date: str | None = None
_api_live_fixtures_cache: dict | None = None
_api_live_fixtures_cache_ts: datetime | None = None
_api_fixture_events_cache: dict[int, dict] = {}
_api_fixture_id_negative_cache: dict[str, dict] = {}
_best_known_events_by_espn_fixture: dict[str, dict] = {}
_best_known_reuse_log_keys: set[str] = set()
_enrich_api_call_count_date: str | None = None
_enrich_api_call_count: int = 0
_enrich_budget_exhausted_logged_date: str | None = None
API_ENRICH_RETRY_DELAYS_SEC = CONFIG_API_ENRICH_RETRY_DELAYS_SEC
API_LIVE_FIXTURES_CACHE_TTL_SEC = 60
API_FIXTURE_EVENTS_CACHE_TTL_SEC = 90


# ── Health management ─────────────────────────────────────────────────────────

def is_espn_healthy() -> bool:
    """
    Returns whether ESPN is currently the active provider.
    """
    return _espn_healthy


def _retry_window_elapsed() -> bool:
    return (not _espn_healthy) and (_retry_after is not None) and (bot_now() >= _retry_after)


def _should_try_espn_now() -> bool:
    """Returns True when ESPN should be queried (primary mode or retry probe window)."""
    if _espn_healthy:
        return True
    if _retry_window_elapsed():
        retry_str = _retry_after.strftime("%H:%M") if _retry_after else "N/A"
        logger.info(f"🟢 [APIProvider] ESPN retry window elapsed ({retry_str}) — probing ESPN now.")
        return True
    return False


def get_poll_interval() -> int:
    """Returns the current polling interval in seconds."""
    return ESPN_POLL_INTERVAL if _espn_healthy else FALLBACK_POLL_INTERVAL


def get_status() -> dict:
    """Return a snapshot of the current provider state for !api command."""
    healthy = is_espn_healthy()
    retry_local = _retry_after.astimezone(bot_now().tzinfo) if _retry_after else None
    return {
        "espn_healthy": healthy,
        "consecutive_failures": _consecutive_failures,
        "retry_after": retry_local,
        "poll_interval": get_poll_interval(),
    }


def _mark_espn_success() -> None:
    global _espn_healthy, _consecutive_failures, _retry_after
    was_fallback = not _espn_healthy
    _espn_healthy = True
    _consecutive_failures = 0
    _retry_after = None
    if was_fallback:
        logger.info(
            f"🟢 [APIProvider] ESPN probe succeeded. "
            f"SWITCHING BACK TO ESPN PRIMARY. Poll interval: {ESPN_POLL_INTERVAL}s."
        )


def _mark_espn_failure() -> None:
    global _espn_healthy, _consecutive_failures, _retry_after
    if not _espn_healthy:
        _retry_after = bot_now() + timedelta(seconds=RETRY_INTERVAL_SEC)
        retry_str = _retry_after.strftime("%H:%M")
        logger.warning(
            f"🟡 [APIProvider] ESPN retry probe failed while on fallback. "
            f"Next retry at {retry_str}."
        )
        return

    _consecutive_failures += 1

    if _espn_healthy:
        if _consecutive_failures < FAILURE_THRESHOLD:
            logger.warning(
                f"⚠️ [APIProvider] ESPN request failed "
                f"({_consecutive_failures}/{FAILURE_THRESHOLD} failures). Still using ESPN."
            )
        else:
            _espn_healthy = False
            _retry_after = bot_now() + timedelta(seconds=RETRY_INTERVAL_SEC)
            retry_str = _retry_after.strftime("%H:%M")
            logger.error(
                f"🔴 [APIProvider] ESPN unreachable after {FAILURE_THRESHOLD} consecutive failures. "
                f"SWITCHING TO API-FOOTBALL FALLBACK."
            )
            logger.error(
                f"🔴 [APIProvider] Poll interval changed: "
                f"{ESPN_POLL_INTERVAL}s → {FALLBACK_POLL_INTERVAL}s. "
                f"Will retry ESPN at {retry_str}."
            )


# ── Scoreboard cache ──────────────────────────────────────────────────────────

async def _get_cached_scoreboard_for_date(session: aiohttp.ClientSession, provider_date: str) -> list[dict]:
    """Return one ESPN provider-date scoreboard with TTL caching."""
    global _cache, _cache_date, _cache_ts

    now = bot_now()
    cached = _football_scoreboard_cache.get(provider_date)
    if cached is None and _cache_date == provider_date and _cache_ts is not None:
        cached = {"matches": _cache, "fetched_at": _cache_ts}
    if cached and (now - cached["fetched_at"]).total_seconds() < CACHE_TTL_SEC:
        return cached["matches"]

    logger.info(
        f"[APIProvider] Fetching ESPN scoreboard for {provider_date} "
        f"({len(LEAGUE_SLUG_MAP)} leagues concurrently)..."
    )
    date_str = provider_date.replace("-", "")

    try:
        summary = await espn_client.fetch_all_leagues_with_summary(session, LEAGUE_SLUG_MAP, date_str)
        results = summary["matches"]
        success_count = summary["success_count"]
        failure_count = summary["failure_count"]
        succeeded_league_ids = set(summary.get("succeeded_league_ids", []))
        failed_league_ids = set(summary.get("failed_league_ids", []))
    except Exception as e:
        logger.error(f"[APIProvider] Unexpected error from espn_client: {e}", exc_info=True)
        results = []
        success_count = 0
        failure_count = len(LEAGUE_SLUG_MAP)
        succeeded_league_ids = set()
        failed_league_ids = set(LEAGUE_SLUG_MAP)

    if success_count == 0:
        logger.warning(
            f"[APIProvider] ESPN scoreboard fetch had no successful league responses "
            f"({failure_count} failed); treating as provider failure."
        )
        _mark_espn_failure()
        if not cached:
            return []
        return cached["matches"]

    _mark_espn_success()
    if results:
        if failure_count > 0 and cached:
            stale_matches = [
                m for m in cached["matches"]
                if m.get("league", {}).get("id") in failed_league_ids
            ]
            matches = [*results, *stale_matches]
            logger.warning(
                f"[APIProvider] ESPN partial refresh merged with stale cache for {provider_date}: "
                f"{len(succeeded_league_ids)} league(s) fresh, "
                f"{len(failed_league_ids)} league(s) preserved."
            )
        else:
            matches = results
    elif failure_count > 0 and cached:
        matches = [
            m for m in cached["matches"]
            if m.get("league", {}).get("id") in failed_league_ids
        ]
        logger.warning(
            f"[APIProvider] ESPN partial refresh returned no fresh matches; "
            f"preserved {len(matches)} stale match(es) from failed league(s)."
        )
    else:
        matches = []

    _football_scoreboard_cache[provider_date] = {"matches": matches, "fetched_at": now}
    _cache = matches
    _cache_date = provider_date
    _cache_ts = now
    logger.info(
        f"[APIProvider] ESPN scoreboard for {provider_date}: {len(matches)} matches "
        f"({success_count} league responses ok, {failure_count} failed)."
    )
    return matches


async def _get_cached_scoreboard(session: aiohttp.ClientSession) -> list[dict]:
    """Compatibility wrapper for the configured display date."""
    return await _get_cached_scoreboard_for_date(session, get_bot_local_date_string())


def _provider_dates_for_window(start_utc: datetime, end_utc: datetime) -> list[str]:
    start_day = to_bot_tz(start_utc).date()
    end_day = to_bot_tz(end_utc).date()
    dates = []
    day = start_day
    while day <= end_day:
        dates.append(day.isoformat())
        day += timedelta(days=1)
    return list(dict.fromkeys(dates))


def _dedupe_by_fixture_id(matches: list[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    for match in matches:
        fixture_id = match_lifecycle.fixture_identity(match)
        if fixture_id is not None:
            deduped[str(fixture_id)] = match
    return list(deduped.values())


async def _fetch_api_football_date(session: aiohttp.ClientSession, provider_date: str) -> list[dict]:
    now = bot_now()
    cached = _api_football_date_cache.get(provider_date)
    if cached and (now - cached["fetched_at"]).total_seconds() < CACHE_TTL_SEC:
        return cached["matches"]

    matches = await api_client.fetch_fixtures_by_date(session, provider_date)
    _api_football_date_cache[provider_date] = {"matches": matches, "fetched_at": now}
    return matches


async def _fetch_api_football_live_matches(session: aiohttp.ClientSession) -> list[dict]:
    global _api_live_fixtures_cache, _api_live_fixtures_cache_ts

    now = bot_now()
    if (
        _api_live_fixtures_cache is not None
        and _api_live_fixtures_cache_ts is not None
        and (now - _api_live_fixtures_cache_ts).total_seconds() < API_LIVE_FIXTURES_CACHE_TTL_SEC
    ):
        response = _api_live_fixtures_cache.get("response")
        return response if isinstance(response, list) else []

    matches = await api_client.fetch_live_fixtures(session)
    _api_live_fixtures_cache = {"response": matches}
    _api_live_fixtures_cache_ts = now
    return matches


async def fetch_football_window(
    session: aiohttp.ClientSession,
    start_utc: datetime,
    end_utc: datetime,
) -> list[dict]:
    provider_dates = _provider_dates_for_window(start_utc, end_utc)
    if _should_try_espn_now():
        date_results = await asyncio.gather(
            *(_get_cached_scoreboard_for_date(session, provider_date) for provider_date in provider_dates)
        )
        matches = _dedupe_by_fixture_id([m for result in date_results for m in result])
        if not _espn_healthy:
            logger.warning("[APIProvider] ESPN became unhealthy during window fetch.")
            return []
    else:
        if api_client.is_quota_exceeded_today():
            logger.warning("[APIProvider] API-Football quota exhausted. Skipping fallback window fetch.")
            return []
        date_results = await asyncio.gather(
            *(_fetch_api_football_date(session, provider_date) for provider_date in provider_dates)
        )
        matches = _dedupe_by_fixture_id([m for result in date_results for m in result])

    start_utc = start_utc.astimezone(timezone.utc)
    end_utc = end_utc.astimezone(timezone.utc)
    filtered = []
    for match in matches:
        kickoff = match_lifecycle.fixture_kickoff_utc(match)
        if match_lifecycle.is_live(match):
            filtered.append(match)
        elif kickoff and start_utc <= kickoff <= end_utc:
            filtered.append(match)
        elif match_lifecycle.is_recently_finished(match, utc_now()):
            filtered.append(match)
    return _dedupe_by_fixture_id(filtered)


async def fetch_relevant_football(session: aiohttp.ClientSession, now_utc: datetime | None = None) -> list[dict]:
    now_utc = now_utc or utc_now()
    start_utc, end_utc = match_lifecycle.provider_window(now_utc)
    return await fetch_football_window(session, start_utc, end_utc)


async def fetch_display_football(session: aiohttp.ClientSession, now_utc: datetime | None = None) -> list[dict]:
    now_utc = now_utc or utc_now()
    start_utc = now_utc - timedelta(hours=FOOTBALL_MATCH_LOOKUP_WINDOW_HOURS)
    end_utc = now_utc + timedelta(hours=FOOTBALL_MATCH_LOOKUP_WINDOW_HOURS)
    return await fetch_football_window(session, start_utc, end_utc)


async def fetch_day(session: aiohttp.ClientSession) -> list[dict]:
    """Display snapshot wrapper over the wider configured football lookup."""
    return await fetch_display_football(session, utc_now())


async def fetch_live(session: aiohttp.ClientSession) -> list[dict]:
    """Currently in-progress matches from the rolling football lookup."""
    matches = await fetch_relevant_football(session, utc_now())
    if not _espn_healthy:
        if api_client.is_quota_exceeded_today():
            logger.warning("[APIProvider] API-Football quota exhausted. Skipping fallback live endpoint fetch.")
        else:
            matches = _dedupe_by_fixture_id([*matches, *await _fetch_api_football_live_matches(session)])
    return [m for m in matches if match_lifecycle.is_live(m)]


async def fetch_finished_recent(session: aiohttp.ClientSession) -> list[dict]:
    """Recently terminal football fixtures visible inside the rolling lookup."""
    matches = await fetch_relevant_football(session, utc_now())
    return [m for m in matches if match_lifecycle.is_ft(m)]


async def fetch_fixture(session: aiohttp.ClientSession, fixture_id) -> dict | None:
    """
    Fetch a single fixture by ID. Used by FT recovery and fallback checks.
    Delegates to API-Football.
    """
    if api_client.is_quota_exceeded_today():
        return None
    return await api_client.fetch_fixture_by_id(session, fixture_id)

async def fetch_next_match_for_team(session: aiohttp.ClientSession, team_name: str) -> dict | None:
    """
    Find a team's next fixture using ESPN search and competition-aware slugs.
    Returns a normalized match dict or None when team/match is not found.
    """
    result = await espn_client.search_team_espn(session, team_name, _TRACKED_SLUGS)
    if not result:
        return None
    espn_team_id, primary_slug = result
    slugs = build_league_slugs(primary_slug)
    return await espn_client.fetch_next_team_fixture_espn(session, espn_team_id, slugs)


def _reset_enrich_state_for_today() -> None:
    global _enrich_attempted_date, _api_fixture_id_cache_date
    global _api_live_fixtures_cache, _api_live_fixtures_cache_ts
    global _enrich_api_call_count_date, _enrich_api_call_count
    global _enrich_budget_exhausted_logged_date
    today = get_bot_local_date_string()
    if _enrich_attempted_date != today:
        _enrich_retry_states.clear()
        _api_fixture_events_cache.clear()
        _api_fixture_id_negative_cache.clear()
        _best_known_events_by_espn_fixture.clear()
        _best_known_reuse_log_keys.clear()
        _api_live_fixtures_cache = None
        _api_live_fixtures_cache_ts = None
        _enrich_attempted_date = today
        _enrich_budget_exhausted_logged_date = None
    if _api_fixture_id_cache_date != today:
        _api_fixture_id_cache.clear()
        _api_fixture_id_cache_date = today
    if _enrich_api_call_count_date != today:
        _enrich_api_call_count = 0
        _enrich_api_call_count_date = today


def _consume_enrichment_api_call(call_label: str) -> bool:
    global _enrich_api_call_count, _enrich_budget_exhausted_logged_date

    _reset_enrich_state_for_today()
    if API_ENRICH_DAILY_CALL_BUDGET <= 0:
        if _enrich_budget_exhausted_logged_date != get_bot_local_date_string():
            logger.info("[Enrich] API-Football enrichment disabled: daily call budget is 0.")
            _enrich_budget_exhausted_logged_date = get_bot_local_date_string()
        return False

    if _enrich_api_call_count >= API_ENRICH_DAILY_CALL_BUDGET:
        if _enrich_budget_exhausted_logged_date != get_bot_local_date_string():
            logger.info(
                f"[Enrich] API-Football enrichment daily call budget exhausted "
                f"({_enrich_api_call_count}/{API_ENRICH_DAILY_CALL_BUDGET}); "
                f"skipping {call_label}."
            )
            _enrich_budget_exhausted_logged_date = get_bot_local_date_string()
        return False

    _enrich_api_call_count += 1
    logger.info(
        f"[Enrich] API-Football enrichment call "
        f"{_enrich_api_call_count}/{API_ENRICH_DAILY_CALL_BUDGET}: {call_label}."
    )
    return True


def _get_negative_api_fixture_mapping(espn_fixture_id: str, now_local: datetime) -> dict | None:
    negative = _api_fixture_id_negative_cache.get(espn_fixture_id)
    if not negative:
        return None

    expires_at = negative.get("expires_at")
    if expires_at is None or now_local >= expires_at:
        _api_fixture_id_negative_cache.pop(espn_fixture_id, None)
        return None

    return negative


def _remember_negative_api_fixture_mapping(espn_fixture_id: str, reason: str) -> None:
    if API_ENRICH_NEGATIVE_MAPPING_TTL_SEC <= 0:
        return

    expires_at = bot_now() + timedelta(seconds=API_ENRICH_NEGATIVE_MAPPING_TTL_SEC)
    _api_fixture_id_negative_cache[espn_fixture_id] = {
        "expires_at": expires_at,
        "reason": reason,
    }
    logger.info(
        f"[Enrich] Cached negative API-Football mapping for ESPN fixture "
        f"{espn_fixture_id} for {API_ENRICH_NEGATIVE_MAPPING_TTL_SEC}s: {reason}."
    )


def _normalize_fixture_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9\s]", " ", text).lower()
    text = re.sub(r"\s+", " ", text).strip()

    aliases = {
        "internazionale": "inter",
        "internazionale milano": "inter",
        "inter milan": "inter",
        "fc internazionale milano": "inter",
        "ac milan": "milan",
        "a c milan": "milan",
    }
    if text in aliases:
        return aliases[text]

    removable_tokens = {
        "fc", "cf", "afc", "sc", "ac", "as", "ss", "us",
        "calcio", "club", "football", "soccer",
    }
    tokens = [token for token in text.split() if token not in removable_tokens]
    text = " ".join(tokens) or text
    return aliases.get(text, text)


def _name_similarity(left: str | None, right: str | None) -> float:
    norm_left = _normalize_fixture_name(left)
    norm_right = _normalize_fixture_name(right)
    if not norm_left or not norm_right:
        return 0.0
    if norm_left == norm_right:
        return 1.0
    if norm_left in norm_right or norm_right in norm_left:
        return 0.92
    return SequenceMatcher(None, norm_left, norm_right).ratio()


def _espn_fixture_datetime(match: dict) -> datetime | None:
    raw_date = match.get("fixture", {}).get("date")
    if not raw_date:
        return None
    try:
        return datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
    except ValueError:
        return None


def _espn_fixture_date(match: dict) -> str:
    fixture_dt = _espn_fixture_datetime(match)
    if fixture_dt:
        if fixture_dt.tzinfo is None:
            return fixture_dt.date().isoformat()
        return fixture_dt.astimezone(bot_now().tzinfo).date().isoformat()
    raw_date = match.get("fixture", {}).get("date")
    if raw_date:
        return str(raw_date)[:10]
    return get_bot_local_date_string()


def _season_for_match(match: dict) -> int:
    fixture_dt = _espn_fixture_datetime(match)
    if fixture_dt is None:
        return get_current_season_year()
    return fixture_dt.year if fixture_dt.month >= 8 else fixture_dt.year - 1


def _candidate_time_delta_minutes(espn_dt: datetime | None, candidate: dict) -> float:
    if espn_dt is None:
        return 0.0
    raw_date = candidate.get("fixture", {}).get("date")
    if not raw_date:
        return 999999.0
    try:
        candidate_dt = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
    except ValueError:
        return 999999.0
    if candidate_dt.tzinfo is None and espn_dt.tzinfo is not None:
        candidate_dt = candidate_dt.replace(tzinfo=espn_dt.tzinfo)
    elif candidate_dt.tzinfo is not None and espn_dt.tzinfo is None:
        espn_dt = espn_dt.replace(tzinfo=candidate_dt.tzinfo)
    return abs((candidate_dt - espn_dt).total_seconds()) / 60


def _is_live_espn_match(match: dict) -> bool:
    return match_lifecycle.is_live(match)


def _goal_event_count(events: list) -> int:
    return sum(1 for e in events if e.get("type") == "Goal" and not is_shootout_event(e))


def _event_quality(events: list) -> tuple[int, int, int]:
    goal_count = _goal_event_count(events)
    red_cards = sum(1 for e in events if e.get("type") == "Card" and e.get("detail") == "Red Card")
    return goal_count, red_cards, len(events)


def _events_are_strictly_better(candidate_events: list, current_events: list) -> bool:
    return _event_quality(candidate_events) > _event_quality(current_events)


def _fresh_age_seconds(fetched_at: datetime | None, now_local: datetime) -> float | None:
    if fetched_at is None:
        return None
    return (now_local - fetched_at).total_seconds()


async def _get_api_football_live_payload(session: aiohttp.ClientSession) -> dict | None:
    global _api_live_fixtures_cache, _api_live_fixtures_cache_ts
    now_local = bot_now()
    age = _fresh_age_seconds(_api_live_fixtures_cache_ts, now_local)
    if (
        _api_live_fixtures_cache is not None
        and age is not None
        and age < API_LIVE_FIXTURES_CACHE_TTL_SEC
    ):
        logger.info(
            f"[Enrich] Using cached API-Football /fixtures?live=all payload "
            f"({age:.0f}s old)."
        )
        return _api_live_fixtures_cache

    if not _consume_enrichment_api_call("live fixture mapping payload"):
        return None

    logger.info("[Enrich] Requesting API-Football /fixtures?live=all for live fixture mapping.")
    payload = await api_client.fetch_live_fixtures_payload(session)
    if payload and isinstance(payload.get("response"), list):
        _api_live_fixtures_cache = payload
        _api_live_fixtures_cache_ts = now_local
        logger.info(
            f"[Enrich] Cached API-Football /fixtures?live=all payload with "
            f"{len(payload.get('response', []))} live fixture(s)."
        )
    return payload


def _get_cached_complete_fixture_events(
    api_fixture_id: int,
    total_goals: int,
    now_local: datetime,
) -> list | None:
    cached = _api_fixture_events_cache.get(api_fixture_id)
    if not cached:
        return None

    age = _fresh_age_seconds(cached.get("fetched_at"), now_local)
    if age is None or age >= API_FIXTURE_EVENTS_CACHE_TTL_SEC:
        logger.info(
            f"[Enrich] Cached events for API-Football fixture {api_fixture_id} "
            f"are stale; will refresh on the next allowed attempt."
        )
        return None

    events = cached.get("events", [])
    cached_goals = _goal_event_count(events)
    if cached_goals >= total_goals:
        logger.info(
            f"[Enrich] Using cached API-Football events for fixture {api_fixture_id} "
            f"({age:.0f}s old, {cached_goals}/{total_goals} goals)."
        )
        return events

    logger.info(
        f"[Enrich] Cached API-Football events for fixture {api_fixture_id} "
        f"are still incomplete ({cached_goals}/{total_goals} goals); "
        f"will refresh on the next allowed attempt."
    )
    return None


async def _fetch_fixture_events_for_enrichment(
    session: aiohttp.ClientSession,
    api_fixture_id: int,
    total_goals: int,
) -> tuple[list | None, bool]:
    now_local = bot_now()
    cached_events = _get_cached_complete_fixture_events(api_fixture_id, total_goals, now_local)
    if cached_events is not None:
        return cached_events, True

    cached = _api_fixture_events_cache.get(api_fixture_id)
    if cached:
        age = _fresh_age_seconds(cached.get("fetched_at"), now_local)
        events = cached.get("events", [])
        if (
            age is not None
            and age < API_ENRICH_INCOMPLETE_EVENTS_COOLDOWN_SEC
        ):
            logger.info(
                f"[Enrich] Cached API-Football events for fixture {api_fixture_id} "
                f"are incomplete ({_goal_event_count(events)}/{total_goals} goals) "
                f"but only {age:.0f}s old; waiting before another events request."
            )
            return None, True

    if not _consume_enrichment_api_call(f"fixture events for API-Football fixture {api_fixture_id}"):
        return None, False

    logger.info(f"[Enrich] Requesting API-Football events for fixture {api_fixture_id}.")
    payload = await api_client.fetch_fixture_events(session, api_fixture_id)
    if not payload:
        return None, False

    response = payload.get("response", [])
    if not isinstance(response, list):
        return None, False

    events = normalize_api_football_events(response)
    _api_fixture_events_cache[api_fixture_id] = {
        "fetched_at": now_local,
        "events": events,
    }
    logger.info(
        f"[Enrich] Cached API-Football events for fixture {api_fixture_id} "
        f"({len(events)} event(s), {_goal_event_count(events)}/{total_goals} goals)."
    )
    return events, False


def _remember_best_known_events(
    match: dict,
    events: list,
    total_goals: int,
    source_label: str,
    api_fixture_id: int | None = None,
) -> None:
    fixture_id = str(match.get("fixture", {}).get("id") or "")
    if not fixture_id:
        return

    existing = _best_known_events_by_espn_fixture.get(fixture_id)
    existing_events = existing.get("events", []) if existing else []
    if existing and not _events_are_strictly_better(events, existing_events):
        return

    _best_known_events_by_espn_fixture[fixture_id] = {
        "events": list(events),
        "goal_count": _goal_event_count(events),
        "event_count": len(events),
        "score_total_at_capture": total_goals,
        "source": source_label,
        "api_fixture_id": api_fixture_id,
        "updated_at": bot_now(),
    }
    api_part = f", API-Football fixture {api_fixture_id}" if api_fixture_id is not None else ""
    logger.info(
        f"[Enrich] Stored best-known events for ESPN fixture {fixture_id} "
        f"from {source_label}{api_part}: "
        f"{_goal_event_count(events)}/{total_goals} goals, {len(events)} event(s)."
    )


def _apply_best_known_events_if_better(match: dict, total_goals: int, goal_events: int) -> dict | None:
    fixture_id = str(match.get("fixture", {}).get("id") or "")
    if not fixture_id:
        return None

    best = _best_known_events_by_espn_fixture.get(fixture_id)
    if not best:
        return None

    current_events = match.get("events", [])
    best_events = best.get("events", [])
    if not _events_are_strictly_better(best_events, current_events):
        return None

    best_goal_count = _goal_event_count(best_events)
    source = best.get("source", "best-known events")
    reuse_log_key = (
        f"{fixture_id}:{source}:{best_goal_count}:{total_goals}:"
        f"{goal_events}:{len(current_events)}:{len(best_events)}"
    )
    if reuse_log_key not in _best_known_reuse_log_keys:
        logger.info(
            f"[Enrich] Reusing best-known enriched events for ESPN fixture {fixture_id} "
            f"from {source}: {best_goal_count}/{total_goals} goals vs ESPN's "
            f"{goal_events}/{total_goals}; preventing event-data downgrade."
        )
        _best_known_reuse_log_keys.add(reuse_log_key)
    return {**match, "events": list(best_events)}


def _merge_enriched_events_if_better(
    match: dict,
    enriched_events: list,
    total_goals: int,
    goal_events: int,
    api_fixture_id: int,
    source_label: str,
) -> dict | None:
    events = match.get("events", [])
    af_goals = _goal_event_count(enriched_events)
    fixture_id = match.get("fixture", {}).get("id")

    if af_goals <= goal_events:
        logger.info(
            f"[Enrich] {source_label} for API-Football fixture {api_fixture_id} "
            f"has {af_goals} goal event(s) for ESPN fixture {fixture_id}; "
            f"ESPN has {goal_events}. Keeping ESPN events."
        )
        return None

    if abs(total_goals - af_goals) > abs(total_goals - goal_events):
        logger.info(
            f"[Enrich] {source_label} for API-Football fixture {api_fixture_id} "
            f"has {af_goals}/{total_goals} goal events, farther from the score "
            f"than ESPN's {goal_events}/{total_goals}. Keeping ESPN events."
        )
        return None

    espn_non_goal_events = [e for e in events if e.get("type") != "Goal"]
    api_non_goal_events = [e for e in enriched_events if e.get("type") != "Goal"]
    if espn_non_goal_events and not api_non_goal_events:
        enriched_events = [*enriched_events, *espn_non_goal_events]

    logger.info(
        f"[Enrich] ESPN fixture {fixture_id}: replaced/merged {len(events)} ESPN events "
        f"with {len(enriched_events)} event(s) from {source_label} "
        f"for API-Football fixture {api_fixture_id} ({af_goals}/{total_goals} goals)."
    )
    merged = {**match, "events": enriched_events}
    _remember_best_known_events(merged, enriched_events, total_goals, source_label, api_fixture_id)
    return merged


def _match_api_fixture_candidate(
    espn_match: dict,
    candidates: list,
    league_id: int,
    max_delta_minutes: int = 120,
) -> tuple[int | None, float]:
    espn_home = espn_match.get("teams", {}).get("home", {}).get("name")
    espn_away = espn_match.get("teams", {}).get("away", {}).get("name")
    espn_dt = _espn_fixture_datetime(espn_match)

    best: tuple[float, dict] | None = None
    for candidate in candidates:
        try:
            candidate_league_id = int(candidate.get("league", {}).get("id"))
        except (TypeError, ValueError):
            continue
        if candidate_league_id != league_id:
            continue

        delta_minutes = _candidate_time_delta_minutes(espn_dt, candidate)
        if delta_minutes > max_delta_minutes:
            continue

        candidate_home = candidate.get("teams", {}).get("home", {}).get("name")
        candidate_away = candidate.get("teams", {}).get("away", {}).get("name")
        home_score = _name_similarity(espn_home, candidate_home)
        away_score = _name_similarity(espn_away, candidate_away)
        if home_score < 0.70 or away_score < 0.70:
            continue

        average_name_score = (home_score + away_score) / 2
        time_score = max(0.0, 1.0 - (delta_minutes / max_delta_minutes))
        confidence = (average_name_score * 0.8) + (time_score * 0.2)
        if best is None or confidence > best[0]:
            best = (confidence, candidate)

    if best is None or best[0] < 0.78:
        return None, 0.0

    api_fixture_id = best[1].get("fixture", {}).get("id")
    try:
        return int(api_fixture_id), best[0]
    except (TypeError, ValueError):
        return None, best[0]


async def _resolve_live_api_football_fixture_id(
    session: aiohttp.ClientSession,
    espn_match: dict,
    espn_fixture_id: str,
    league_id: int,
) -> int | None:
    payload = await _get_api_football_live_payload(session)
    if payload is None:
        logger.info(
            f"[Enrich] Cannot resolve live API-Football fixture for ESPN fixture "
            f"{espn_fixture_id}: live mapping payload unavailable."
        )
        return None

    candidates = payload.get("response", []) if payload else []
    if not isinstance(candidates, list) or not candidates:
        _remember_negative_api_fixture_mapping(espn_fixture_id, "live feed returned no candidates")
        logger.info(
            f"[Enrich] No API-Football live fixture candidates for ESPN fixture "
            f"{espn_fixture_id}; /fixtures?live=all returned no live fixtures."
        )
        return None

    api_fixture_id, confidence = _match_api_fixture_candidate(espn_match, candidates, league_id)
    if api_fixture_id is None:
        home = espn_match.get("teams", {}).get("home", {}).get("name")
        away = espn_match.get("teams", {}).get("away", {}).get("name")
        _remember_negative_api_fixture_mapping(espn_fixture_id, "no confident live mapping")
        logger.info(
            f"[Enrich] No confident API-Football live mapping for ESPN fixture "
            f"{espn_fixture_id} ({home} vs {away}, league {league_id})."
        )
        return None

    _api_fixture_id_cache[espn_fixture_id] = api_fixture_id
    logger.info(
        f"[Enrich] Mapped ESPN fixture {espn_fixture_id} -> API-Football live fixture "
        f"{api_fixture_id} (confidence {confidence:.2f})"
    )
    return api_fixture_id


async def resolve_api_football_fixture_id(session: aiohttp.ClientSession, espn_match: dict) -> int | None:
    """
    Map an ESPN fixture ID to API-Football's fixture ID using date, league,
    team names, and kickoff proximity. The ESPN ID is only a cache key.
    """
    _reset_enrich_state_for_today()

    espn_fixture_id = str(espn_match.get("fixture", {}).get("id") or "")
    if not espn_fixture_id:
        logger.info("[Enrich] Cannot resolve API-Football fixture ID: ESPN fixture ID missing.")
        return None
    if espn_fixture_id in _api_fixture_id_cache:
        logger.info(
            f"[Enrich] Using cached ESPN -> API-Football fixture mapping: "
            f"{espn_fixture_id} -> {_api_fixture_id_cache[espn_fixture_id]}"
        )
        return _api_fixture_id_cache[espn_fixture_id]

    now_local = bot_now()
    negative_mapping = _get_negative_api_fixture_mapping(espn_fixture_id, now_local)
    if negative_mapping is not None:
        expires_at = negative_mapping.get("expires_at")
        age_left = _fresh_age_seconds(now_local, expires_at) if expires_at else None
        suffix = f" ({age_left:.0f}s left)" if age_left is not None else ""
        logger.info(
            f"[Enrich] Skipping API-Football mapping for ESPN fixture "
            f"{espn_fixture_id}: cached negative mapping{suffix}."
        )
        return None

    try:
        league_id = int(espn_match.get("league", {}).get("id"))
    except (TypeError, ValueError):
        logger.info(f"[Enrich] Cannot resolve API-Football fixture for ESPN fixture {espn_fixture_id}: league ID missing.")
        return None

    if _is_live_espn_match(espn_match):
        return await _resolve_live_api_football_fixture_id(session, espn_match, espn_fixture_id, league_id)

    match_date = _espn_fixture_date(espn_match)
    season = _season_for_match(espn_match)
    url = (
        "https://v3.football.api-sports.io/fixtures"
        f"?date={match_date}&league={league_id}&season={season}"
    )
    if not _consume_enrichment_api_call(
        f"fixture mapping lookup for ESPN fixture {espn_fixture_id}"
    ):
        return None

    payload = await api_client._make_request(session, url)
    candidates = payload.get("response", []) if payload else []
    if not isinstance(candidates, list) or not candidates:
        _remember_negative_api_fixture_mapping(espn_fixture_id, "date/league lookup returned no candidates")
        logger.info(
            f"[Enrich] No API-Football fixture candidates for ESPN fixture "
            f"{espn_fixture_id} on {match_date} league {league_id}."
        )
        return None

    api_fixture_id, confidence = _match_api_fixture_candidate(espn_match, candidates, league_id)
    if api_fixture_id is None:
        espn_home = espn_match.get("teams", {}).get("home", {}).get("name")
        espn_away = espn_match.get("teams", {}).get("away", {}).get("name")
        _remember_negative_api_fixture_mapping(espn_fixture_id, "no confident date/league mapping")
        logger.info(
            f"[Enrich] No confident API-Football mapping for ESPN fixture "
            f"{espn_fixture_id} ({espn_home} vs {espn_away}, league {league_id})."
        )
        return None

    _api_fixture_id_cache[espn_fixture_id] = api_fixture_id
    logger.info(
        f"[Enrich] Mapped ESPN fixture {espn_fixture_id} -> API-Football fixture "
        f"{api_fixture_id} (confidence {confidence:.2f})"
    )
    return api_fixture_id


async def enrich_fixture_events(session: aiohttp.ClientSession, match: dict) -> dict:
    """
    If ESPN event data is incomplete (goal count < score total), fetch events
    from API-Football and return a copy of the match with more complete events.
    Returns the original match unchanged if data is complete or on any error.
    """
    goals = match.get("goals", {})
    events = match.get("events", [])

    try:
        total_goals = int(goals.get("home", 0) or 0) + int(goals.get("away", 0) or 0)
        goal_events = _goal_event_count(events)
        missing = max(0, total_goals - goal_events)
    except (TypeError, ValueError):
        return match

    fixture_id = match.get("fixture", {}).get("id")
    if not fixture_id:
        return match

    global _enrich_attempted_date, _enrich_tick_key, _enrich_tick_count
    _reset_enrich_state_for_today()
    now_local = bot_now()

    if goal_events >= total_goals:
        best_known = _apply_best_known_events_if_better(match, total_goals, goal_events)
        if best_known is not None:
            return best_known
        _remember_best_known_events(match, events, total_goals, "ESPN")
        return match

    best_known = _apply_best_known_events_if_better(match, total_goals, goal_events)
    if best_known is not None:
        match = best_known
        events = match.get("events", [])
        goal_events = _goal_event_count(events)
        if goal_events >= total_goals:
            return match

    cached_api_fixture_id = _api_fixture_id_cache.get(str(fixture_id))
    if cached_api_fixture_id is not None:
        cached_events = _get_cached_complete_fixture_events(
            cached_api_fixture_id,
            total_goals,
            now_local,
        )
        if cached_events is not None:
            merged = _merge_enriched_events_if_better(
                match,
                cached_events,
                total_goals,
                goal_events,
                cached_api_fixture_id,
                "cached API-Football events",
            )
            if merged is not None:
                return merged

    enrich_state = f"{fixture_id}:{goals.get('home')}:{goals.get('away')}:{len(events)}"
    retry_state = _enrich_retry_states.get(enrich_state)
    if retry_state is None:
        first_retry_delay = max(API_ENRICH_GRACE_SEC, API_ENRICH_RETRY_DELAYS_SEC[0])
        retry_state = {
            "first_seen": now_local,
            "attempt_count": 0,
            "last_attempt_at": None,
            "exhausted": False,
        }
        _enrich_retry_states[enrich_state] = retry_state
        logger.info(
            f"[Enrich] Fixture {fixture_id} has {missing} missing goal event(s); "
            f"first retry in {first_retry_delay}s."
        )
        return match

    if retry_state.get("exhausted"):
        return match

    attempt_count = int(retry_state.get("attempt_count", 0))
    if attempt_count >= len(API_ENRICH_RETRY_DELAYS_SEC):
        retry_state["exhausted"] = True
        return match

    first_seen = retry_state.get("first_seen") or now_local
    required_delay = max(API_ENRICH_GRACE_SEC, API_ENRICH_RETRY_DELAYS_SEC[attempt_count])
    if (now_local - first_seen).total_seconds() < required_delay:
        return match

    tick_key = now_local.strftime("%Y%m%d%H%M")
    if _enrich_tick_key != tick_key:
        _enrich_tick_key = tick_key
        _enrich_tick_count = 0
    if _enrich_tick_count >= API_ENRICH_MAX_CALLS_PER_TICK:
        logger.info(
            f"[Enrich] Skipping fixture {fixture_id}: per-tick cap "
            f"{API_ENRICH_MAX_CALLS_PER_TICK} reached."
        )
        return match

    if api_client.is_quota_exceeded_today():
        return match

    _enrich_tick_count += 1
    retry_state["attempt_count"] = attempt_count + 1
    retry_state["last_attempt_at"] = now_local

    try:
        api_fixture_id = await resolve_api_football_fixture_id(session, match)
        if api_fixture_id is None:
            logger.info(
                f"[Enrich] Skipping enrichment for ESPN fixture {fixture_id}: "
                f"no API-Football fixture mapping exists."
            )
            return match

        logger.info(
            f"[Enrich] Loading API-Football events for mapped fixture "
            f"{api_fixture_id} (ESPN fixture {fixture_id}; {missing} missing goal(s))"
        )
        enriched_events, from_cache = await _fetch_fixture_events_for_enrichment(
            session,
            api_fixture_id,
            total_goals,
        )
        if enriched_events is None:
            return match

        source_label = "cached API-Football events" if from_cache else "API-Football events"
        merged = _merge_enriched_events_if_better(
            match,
            enriched_events,
            total_goals,
            goal_events,
            api_fixture_id,
            source_label,
        )
        if merged is not None:
            retry_state["exhausted"] = True
            return merged

        if attempt_count + 1 >= len(API_ENRICH_RETRY_DELAYS_SEC):
            retry_state["exhausted"] = True
        return match
    except Exception as e:
        logger.warning(f"[Enrich] Failed to enrich ESPN fixture {fixture_id}: {e}")
        return match


async def enrich_fixtures(session: aiohttp.ClientSession, fixtures: list) -> list:
    """
    Batch-enrich a list of fixtures concurrently.
    Only makes API-Football calls for fixtures where ESPN event data is incomplete.
    """
    return list(await asyncio.gather(
        *(enrich_fixture_events(session, m) for m in fixtures)
    ))


async def _get_cached_tennis_scoreboard(session: aiohttp.ClientSession) -> list[dict]:
    """
    Returns tracked tennis matches from a rolling window around today.
    Cached for TENNIS_CACHE_TTL_SEC seconds; invalidated at configured local midnight.
    """
    global _tennis_cache, _tennis_cache_date, _tennis_cache_ts

    today = get_bot_local_date_string()
    now = bot_now()

    if (
        _tennis_cache_date == today
        and _tennis_cache_ts is not None
        and (now - _tennis_cache_ts).total_seconds() < TENNIS_CACHE_TTL_SEC
    ):
        return _tennis_cache

    base_day = bot_now().date()
    date_params: list[str | None] = [
        None,
        (base_day - timedelta(days=1)).strftime("%Y%m%d"),
        base_day.strftime("%Y%m%d"),
        (base_day + timedelta(days=1)).strftime("%Y%m%d"),
    ]

    try:
        day_batches = await asyncio.gather(
            *(espn_tennis_client.fetch_tracked_tennis_matches(session, ds) for ds in date_params),
            return_exceptions=True,
        )
    except Exception as e:
        logger.warning(f"[APIProvider] Tennis fetch error: {e}", exc_info=True)
        day_batches = []

    deduped: dict[str, dict] = {}
    for batch in day_batches:
        if isinstance(batch, Exception) or not isinstance(batch, list):
            continue
        for match in batch:
            key = _tennis_identity_key(match)
            existing = deduped.get(key)
            if existing is None or _tennis_match_rank(match) > _tennis_match_rank(existing):
                deduped[key] = match

    matches = []
    for key, match in deduped.items():
        merged = dict(match)
        merged["canonical_id"] = key
        matches.append(merged)

    matches.sort(key=lambda m: m.get("start_time") or "")

    if not matches and _tennis_cache_date == today and _tennis_cache:
        logger.warning(
            "[APIProvider] Tennis refresh returned 0 tracked matches; keeping previous same-day cache."
        )
        _tennis_cache_ts = now
        return _tennis_cache

    _tennis_cache = matches
    _tennis_cache_date = today
    _tennis_cache_ts = now
    logger.info(
        f"[APIProvider] Tennis scoreboard fetched: {len(matches)} tracked match(es) "
        f"across default scoreboard plus {len(date_params) - 1} dated day(s)."
    )
    return _tennis_cache


async def fetch_tennis_day(session: aiohttp.ClientSession) -> list[dict]:
    """Tracked tennis matches from the rolling ESPN ATP/WTA scoreboard window."""
    return await _get_cached_tennis_scoreboard(session)


async def fetch_tennis_live(session: aiohttp.ClientSession) -> list[dict]:
    """Tracked tennis matches currently live."""
    matches = await _get_cached_tennis_scoreboard(session)
    return [m for m in matches if m.get("status", {}).get("short") == "LIVE"]


async def fetch_tennis_finished_today(session: aiohttp.ClientSession) -> list[dict]:
    """Tracked tennis matches that reached final status today."""
    matches = await _get_cached_tennis_scoreboard(session)
    return [m for m in matches if m.get("status", {}).get("short") == "FT" and _is_today(m.get("start_time"))]


def _match_dt_bot_tz(start_time: str | None):
    if not start_time:
        return None
    try:
        return to_bot_tz(start_time)
    except Exception:
        return None


def _normalize_name(value: str | None) -> str:
    return " ".join((value or "").strip().lower().split())


def _tennis_identity_key(match: dict) -> str:
    players = sorted([
        _normalize_name(match.get("player_a")),
        _normalize_name(match.get("player_b")),
    ])
    event_name = _normalize_name(match.get("event_name"))
    start_time = match.get("start_time") or ""
    date_part = ""
    if start_time:
        try:
            date_part = to_bot_tz(start_time).date().isoformat()
        except Exception:
            date_part = str(start_time)[:10]
    return f"{players[0]}|{players[1]}|{date_part}|{event_name}"


def _tennis_status_rank(match: dict) -> int:
    status = (match.get("status") or {}).get("short")
    if status == "FT":
        return 3
    if status == "LIVE":
        return 2
    if status == "NS":
        return 1
    return 0


def _tennis_match_rank(match: dict) -> tuple:
    status = match.get("status") or {}
    sets = match.get("sets") or []
    return (
        _tennis_status_rank(match),
        1 if match.get("winner") else 0,
        len(sets),
        1 if status.get("detail") else 0,
        1 if match.get("round") else 0,
    )


def _is_today(start_time: str | None) -> bool:
    dt = _match_dt_bot_tz(start_time)
    return bool(dt and dt.date() == bot_now().date())


def _is_future(start_time: str | None, horizon_days: int = TENNIS_UPCOMING_DAYS) -> bool:
    dt = _match_dt_bot_tz(start_time)
    if not dt:
        return False
    now = bot_now()
    return now < dt <= now + timedelta(days=horizon_days)


def _is_past(start_time: str | None) -> bool:
    dt = _match_dt_bot_tz(start_time)
    if not dt:
        return False
    return dt < bot_now()


async def fetch_tennis_upcoming(session: aiohttp.ClientSession, horizon_days: int = TENNIS_UPCOMING_DAYS) -> list[dict]:
    """Tracked tennis matches upcoming within horizon_days."""
    matches = await _get_cached_tennis_scoreboard(session)
    return [m for m in matches if _is_future(m.get("start_time"), horizon_days)]
