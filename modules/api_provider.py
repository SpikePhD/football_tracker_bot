# modules/api_provider.py
# Unified API provider with ESPN as primary and API-Football as fallback.
# All other modules import from here instead of directly from api_client or espn_client.

import logging
from datetime import datetime, timedelta

import aiohttp

import asyncio

from config import LEAGUE_SLUG_MAP
from utils import espn_client
from utils import api_client
from utils.time_utils import italy_now, get_italy_date_string
from utils.event_formatter import normalize_api_football_events

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────

FAILURE_THRESHOLD = 3       # consecutive ESPN failures before switching to fallback
RETRY_INTERVAL_SEC = 600    # seconds before probing ESPN again after a switch (10 min)
ESPN_POLL_INTERVAL = 60     # seconds between polls when ESPN is primary
FALLBACK_POLL_INTERVAL = 480  # seconds between polls when using API-Football

# ── Health state ──────────────────────────────────────────────────────────────

_espn_healthy: bool = True
_consecutive_failures: int = 0
_retry_after: datetime | None = None   # wall-clock datetime (Italy tz)

# ── Scoreboard cache ──────────────────────────────────────────────────────────

_cache: list[dict] = []
_cache_date: str | None = None  # Italy date string (YYYY-MM-DD) the cache covers
_cache_ts: datetime | None = None
CACHE_TTL_SEC = 55   # expire just before the next 60s poll


# ── Health management ─────────────────────────────────────────────────────────

def is_espn_healthy() -> bool:
    """
    Returns True if ESPN should be tried.
    Automatically re-arms ESPN once the retry window has elapsed.
    """
    global _espn_healthy, _consecutive_failures, _retry_after

    if not _espn_healthy and _retry_after is not None:
        if italy_now() >= _retry_after:
            _espn_healthy = True
            _consecutive_failures = 0
            _retry_after = None
            logger.info("🟢 [APIProvider] ESPN retry window elapsed — probing ESPN on next request.")

    return _espn_healthy


def get_poll_interval() -> int:
    """Returns the current polling interval in seconds."""
    return ESPN_POLL_INTERVAL if is_espn_healthy() else FALLBACK_POLL_INTERVAL


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
        results = await espn_client.fetch_all_leagues(session, LEAGUE_SLUG_MAP, date_str)
    except Exception as e:
        logger.error(f"[APIProvider] Unexpected error from espn_client: {e}", exc_info=True)
        results = []

    if results or _cache_date != today:
        # Accept empty list as valid (no matches today) only on a new day;
        # on the same day, an empty result after a non-empty cache suggests a transient failure.
        if results:
            _mark_espn_success()
            _cache = results
            _cache_date = today
            _cache_ts = now
            logger.info(f"[APIProvider] ESPN scoreboard fetched: {len(results)} matches across all leagues.")
        elif _cache_date != today:
            # New day, ESPN returned empty — could be genuinely no matches
            _mark_espn_success()
            _cache = []
            _cache_date = today
            _cache_ts = now
            logger.info("[APIProvider] ESPN returned 0 matches for today (may be correct).")
        else:
            # Same day, empty result — treat as failure
            logger.warning("[APIProvider] ESPN returned 0 matches (same day, previous cache had data) — treating as failure.")
            _mark_espn_failure()
    else:
        # Empty on a new day where we have no prior cache — ambiguous
        _mark_espn_failure()

    return _cache


# ── Public API surface ────────────────────────────────────────────────────────

async def fetch_day(session: aiohttp.ClientSession) -> list[dict]:
    """
    All of today's tracked matches (any status).
    ESPN primary, API-Football fallback.
    """
    if is_espn_healthy():
        return await _get_cached_scoreboard(session)
    logger.info("🟡 [APIProvider] Running on API-FOOTBALL fallback. Fetching day fixtures...")
    return await api_client.fetch_day_fixtures(session)


async def fetch_live(session: aiohttp.ClientSession) -> list[dict]:
    """
    Currently in-progress matches only.
    ESPN primary, API-Football fallback.
    """
    if is_espn_healthy():
        all_matches = await _get_cached_scoreboard(session)
        live_statuses = {"1H", "HT", "2H", "ET", "PEN"}
        live = [m for m in all_matches if m["fixture"]["status"]["short"] in live_statuses]
        if not is_espn_healthy():
            # Health switched to fallback during the scoreboard fetch
            logger.warning("🟡 [APIProvider] ESPN became unhealthy during fetch_live. Will use fallback next cycle.")
            return []
        return live

    retry_str = _retry_after.strftime("%H:%M") if _retry_after else "N/A"
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
    return await api_client.fetch_fixture_by_id(session, fixture_id)


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
        logger.info(
            f"✅ [Enrich] Fixture {fixture_id}: replaced {len(events)} ESPN events "
            f"with {len(enriched_events)} API-Football events."
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

