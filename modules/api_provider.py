# modules/api_provider.py
# Unified API provider with ESPN as primary and API-Football as fallback.
# All other modules import from here instead of directly from api_client or espn_client.

import logging
from datetime import datetime, timedelta

import aiohttp

import asyncio

from config import (
    API_ENRICH_MAX_CALLS_PER_TICK,
    API_ENRICH_GRACE_SEC,
    API_ESPN_POLL_INTERVAL_SEC,
    API_FAILURE_THRESHOLD,
    API_FALLBACK_POLL_INTERVAL_SEC,
    API_RETRY_INTERVAL_SEC,
    API_SCOREBOARD_CACHE_TTL_SEC,
    LEAGUE_SLUG_MAP,
    TENNIS_CACHE_TTL_SEC,
    TENNIS_UPCOMING_DAYS,
    build_league_slugs,
)
from utils import espn_client
from utils import espn_tennis_client
from utils import api_client
from utils.time_utils import italy_now, get_italy_date_string, parse_utc_to_italy
from utils.event_formatter import normalize_api_football_events

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────

FAILURE_THRESHOLD = API_FAILURE_THRESHOLD
RETRY_INTERVAL_SEC = API_RETRY_INTERVAL_SEC
ESPN_POLL_INTERVAL = API_ESPN_POLL_INTERVAL_SEC
FALLBACK_POLL_INTERVAL = API_FALLBACK_POLL_INTERVAL_SEC

# ── Health state ──────────────────────────────────────────────────────────────

_espn_healthy: bool = True
_consecutive_failures: int = 0
_retry_after: datetime | None = None   # wall-clock datetime (Italy tz)

# ── Scoreboard cache ──────────────────────────────────────────────────────────

_cache: list[dict] = []
_cache_date: str | None = None  # Italy date string (YYYY-MM-DD) the cache covers
_cache_ts: datetime | None = None
CACHE_TTL_SEC = API_SCOREBOARD_CACHE_TTL_SEC

# Tennis cache
_tennis_cache: list[dict] = []
_tennis_cache_date: str | None = None
_tennis_cache_ts: datetime | None = None
_TRACKED_SLUGS: set[str] = set(LEAGUE_SLUG_MAP.values())
_enrich_attempted_states: set[str] = set()
_enrich_attempted_date: str | None = None
_enrich_tick_key: str | None = None
_enrich_tick_count: int = 0
_enrich_first_seen_states: dict[str, datetime] = {}


# ── Health management ─────────────────────────────────────────────────────────

def is_espn_healthy() -> bool:
    """
    Returns whether ESPN is currently the active provider.
    """
    return _espn_healthy


def _retry_window_elapsed() -> bool:
    return (not _espn_healthy) and (_retry_after is not None) and (italy_now() >= _retry_after)


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
    retry_local = _retry_after.astimezone(italy_now().tzinfo) if _retry_after else None
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
        _retry_after = italy_now() + timedelta(seconds=RETRY_INTERVAL_SEC)
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
            _retry_after = italy_now() + timedelta(seconds=RETRY_INTERVAL_SEC)
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

async def _get_cached_scoreboard(session: aiohttp.ClientSession) -> list[dict]:
    """
    Returns today's full scoreboard (all leagues, all statuses).
    Cached for CACHE_TTL_SEC seconds; invalidated at Italy midnight.
    Calls espn_client.fetch_all_leagues on miss, manages health state.
    """
    global _cache, _cache_date, _cache_ts

    today = get_italy_date_string()
    now = italy_now()

    # Cache hit: same day and not expired
    if (
        _cache_date == today
        and _cache_ts is not None
        and (now - _cache_ts).total_seconds() < CACHE_TTL_SEC
    ):
        return _cache

    # Cache miss: fetch fresh data
    logger.info(f"[APIProvider] Fetching ESPN scoreboard for {today} ({len(LEAGUE_SLUG_MAP)} leagues concurrently)…")
    date_str = today.replace("-", "")  # YYYYMMDD

    try:
        summary = await espn_client.fetch_all_leagues_with_summary(session, LEAGUE_SLUG_MAP, date_str)
        results = summary["matches"]
        success_count = summary["success_count"]
        failure_count = summary["failure_count"]
    except Exception as e:
        logger.error(f"[APIProvider] Unexpected error from espn_client: {e}", exc_info=True)
        results = []
        success_count = 0
        failure_count = len(LEAGUE_SLUG_MAP)

    if success_count == 0:
        logger.warning(
            f"[APIProvider] ESPN scoreboard fetch had no successful league responses "
            f"({failure_count} failed); treating as provider failure."
        )
        _mark_espn_failure()
        if _cache_date != today:
            return []
    elif results:
        _mark_espn_success()
        _cache = results
        _cache_date = today
        _cache_ts = now
        logger.info(
            f"[APIProvider] ESPN scoreboard fetched: {len(results)} matches "
            f"({success_count} league responses ok, {failure_count} failed)."
        )
    else:
        _mark_espn_success()
        _cache = []
        _cache_date = today
        _cache_ts = now
        logger.info(
            f"[APIProvider] ESPN returned 0 matches for today "
            f"({success_count} league responses ok, {failure_count} failed)."
        )

    return _cache


# ── Public API surface ────────────────────────────────────────────────────────

async def fetch_day(session: aiohttp.ClientSession) -> list[dict]:
    """
    All of today's tracked matches (any status).
    ESPN primary, API-Football fallback.
    """
    if _should_try_espn_now():
        return await _get_cached_scoreboard(session)
    if api_client.is_quota_exceeded_today():
        logger.warning("🟡 [APIProvider] API-Football quota exhausted for today. Skipping fallback day fetch.")
        return []
    logger.info("🟡 [APIProvider] Running on API-FOOTBALL fallback. Fetching day fixtures...")
    return await api_client.fetch_day_fixtures(session)


async def fetch_live(session: aiohttp.ClientSession) -> list[dict]:
    """
    Currently in-progress matches only.
    ESPN primary, API-Football fallback.
    """
    if _should_try_espn_now():
        all_matches = await _get_cached_scoreboard(session)
        live_statuses = {"1H", "HT", "2H", "ET", "PEN"}
        live = [m for m in all_matches if m["fixture"]["status"]["short"] in live_statuses]
        if not _espn_healthy:
            # Health switched to fallback during the scoreboard fetch
            logger.warning("🟡 [APIProvider] ESPN became unhealthy during fetch_live. Will use fallback next cycle.")
            return []
        return live

    retry_str = _retry_after.strftime("%H:%M") if _retry_after else "N/A"
    if api_client.is_quota_exceeded_today():
        logger.warning("🟡 [APIProvider] API-Football quota exhausted for today. Skipping fallback live fetch.")
        return []
    logger.info(f"🟡 [APIProvider] Running on API-FOOTBALL fallback. ESPN retry at {retry_str}.")
    return await api_client.fetch_live_fixtures(session)


async def fetch_finished_today(session: aiohttp.ClientSession) -> list[dict]:
    """
    ESPN mode only: all matches that have reached FT status today.
    Returns empty list when in fallback mode (ft_handler uses its own path).
    """
    if not is_espn_healthy():
        return []
    all_matches = await _get_cached_scoreboard(session)
    return [m for m in all_matches if m["fixture"]["status"]["short"] == "FT"]


async def fetch_fixture(session: aiohttp.ClientSession, fixture_id) -> dict | None:
    """
    Fetch a single fixture by ID. Used by ft_handler in fallback mode only.
    Delegates to API-Football (ESPN doesn't have a per-event endpoint we use here).
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


async def enrich_fixture_events(session: aiohttp.ClientSession, match: dict) -> dict:
    """
    If ESPN event data is incomplete (goal count < score total), fetch the fixture
    from API-Football and return a copy of the match with complete events.
    Returns the original match unchanged if data is complete or on any error.
    """
    goals = match.get("goals", {})
    events = match.get("events", [])

    try:
        total_goals = int(goals.get("home", 0) or 0) + int(goals.get("away", 0) or 0)
        goal_events = sum(1 for e in events if e.get("type") == "Goal")
        if goal_events >= total_goals:
            return match
        missing = total_goals - goal_events
    except (TypeError, ValueError):
        return match

    fixture_id = match.get("fixture", {}).get("id")
    if not fixture_id:
        return match

    # Do not spam enrichment attempts for identical match states.
    global _enrich_attempted_date, _enrich_tick_key, _enrich_tick_count
    today = get_italy_date_string()
    if _enrich_attempted_date != today:
        _enrich_attempted_states.clear()
        _enrich_first_seen_states.clear()
        _enrich_attempted_date = today

    enrich_state = f"{fixture_id}:{goals.get('home')}:{goals.get('away')}:{len(events)}"
    if enrich_state in _enrich_attempted_states:
        return match

    first_seen = _enrich_first_seen_states.get(enrich_state)
    now_local = italy_now()
    if first_seen is None:
        _enrich_first_seen_states[enrich_state] = now_local
        logger.info(
            f"[Enrich] Deferring fixture {fixture_id} enrichment for "
            f"{API_ENRICH_GRACE_SEC}s grace window."
        )
        return match
    if (now_local - first_seen).total_seconds() < API_ENRICH_GRACE_SEC:
        return match

    # Hard cap enrichment calls per scheduler tick/minute.
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

    _enrich_attempted_states.add(enrich_state)
    _enrich_tick_count += 1

    logger.info(
        f"🔍 [Enrich] Fetching API-Football events for fixture {fixture_id} "
        f"({missing} missing goal(s) in ESPN data)"
    )
    try:
        payload = await fetch_fixture(session, fixture_id)
        if not payload:
            return match
        response = payload.get("response", [])
        if not response:
            return match
        enriched_events = normalize_api_football_events(response[0].get("events", []))
        af_goals = sum(1 for e in enriched_events if e.get("type") == "Goal")
        if af_goals <= goal_events:
            logger.info(
                f"ℹ️ [Enrich] Fixture {fixture_id}: API-Football also has incomplete data "
                f"({af_goals} goal events vs ESPN's {goal_events}). Keeping ESPN events."
            )
            return match
        logger.info(
            f"✅ [Enrich] Fixture {fixture_id}: replaced {len(events)} ESPN events "
            f"with {len(enriched_events)} API-Football events ({af_goals}/{total_goals} goals)."
        )
        return {**match, "events": enriched_events}
    except Exception as e:
        logger.warning(f"⚠️ [Enrich] Failed to enrich fixture {fixture_id}: {e}")
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
    Cached for TENNIS_CACHE_TTL_SEC seconds; invalidated at Italy midnight.
    """
    global _tennis_cache, _tennis_cache_date, _tennis_cache_ts

    today = get_italy_date_string()
    now = italy_now()

    if (
        _tennis_cache_date == today
        and _tennis_cache_ts is not None
        and (now - _tennis_cache_ts).total_seconds() < TENNIS_CACHE_TTL_SEC
    ):
        return _tennis_cache

    base_day = italy_now().date()
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


def _match_dt_italy(start_time: str | None):
    if not start_time:
        return None
    try:
        return parse_utc_to_italy(start_time)
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
            date_part = parse_utc_to_italy(start_time).date().isoformat()
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
    dt = _match_dt_italy(start_time)
    return bool(dt and dt.date() == italy_now().date())


def _is_future(start_time: str | None, horizon_days: int = TENNIS_UPCOMING_DAYS) -> bool:
    dt = _match_dt_italy(start_time)
    if not dt:
        return False
    now = italy_now()
    return now < dt <= now + timedelta(days=horizon_days)


def _is_past(start_time: str | None) -> bool:
    dt = _match_dt_italy(start_time)
    if not dt:
        return False
    return dt < italy_now()


async def fetch_tennis_upcoming(session: aiohttp.ClientSession, horizon_days: int = TENNIS_UPCOMING_DAYS) -> list[dict]:
    """Tracked tennis matches upcoming within horizon_days."""
    matches = await _get_cached_tennis_scoreboard(session)
    return [m for m in matches if _is_future(m.get("start_time"), horizon_days)]
