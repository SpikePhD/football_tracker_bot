# utils/api_client.py
import asyncio
import aiohttp
import logging
from datetime import datetime, timezone
from .time_utils import get_current_season_year

from config import API_KEY, TRACKED_LEAGUE_IDS
from utils.time_utils import get_bot_local_date_string, bot_now

logger = logging.getLogger(__name__)

HEADERS = {
    "x-apisports-key": API_KEY,
    "Content-Type": "application/json"
}

# Timeout for API requests in seconds
API_REQUEST_TIMEOUT = 15
_TIMEOUT = aiohttp.ClientTimeout(total=API_REQUEST_TIMEOUT)

_quota_exceeded_day: str | None = None
_plan_unavailable_log_cache: dict[str, str] = {}


def _api_errors_present(api_errors) -> bool:
    if isinstance(api_errors, list):
        return len(api_errors) > 0
    if isinstance(api_errors, dict):
        return bool(api_errors)
    return bool(api_errors)


def _api_error_text(api_errors) -> str:
    if isinstance(api_errors, dict):
        return " ".join(str(value) for value in api_errors.values())
    if isinstance(api_errors, list):
        return " ".join(str(value) for value in api_errors)
    return str(api_errors)


def _is_request_limit_error(api_errors) -> bool:
    error_text = _api_error_text(api_errors).lower()
    return "request limit" in error_text or "reached the request limit" in error_text


def _is_plan_unavailable_error(api_errors) -> bool:
    error_text = _api_error_text(api_errors).lower()
    return "free plans do not have access" in error_text or "do not have access" in error_text


def _log_api_payload_error(url: str, status: int, parameters, api_errors) -> None:
    if _is_plan_unavailable_error(api_errors):
        today = get_bot_local_date_string()
        if _plan_unavailable_log_cache.get(url) != today:
            _plan_unavailable_log_cache[url] = today
            logger.warning(
                f"API-Football plan unavailable for {url}: {api_errors} | "
                f"Status: {status} | Parameters: {parameters}. "
                "Suppressing repeats for this request today."
            )
        else:
            logger.debug(
                f"Suppressed repeated API-Football plan unavailable response for {url}: "
                f"{api_errors} | Status: {status} | Parameters: {parameters}"
            )
        return

    logger.error(f"❌ API Error for {url}: {api_errors} | Status: {status} | Parameters: {parameters}")


def is_quota_exceeded_today() -> bool:
    """Return True when API-Football daily quota is known to be exhausted for today's bot local date."""
    global _quota_exceeded_day
    today = get_bot_local_date_string()
    if _quota_exceeded_day and _quota_exceeded_day != today:
        _quota_exceeded_day = None
    return _quota_exceeded_day == today

async def _make_request(session: aiohttp.ClientSession, url: str) -> dict | None:
    """
    Helper function to make an API request and handle common errors.
    Returns the parsed JSON data (the whole payload) or None on error.
    """
    global _quota_exceeded_day
    logger.info(f"🌐 API Request: {url}")
    try:
        async with session.get(url, headers=HEADERS, timeout=_TIMEOUT) as response:
            if 200 <= response.status < 300:
                data = await response.json()

                api_errors = data.get("errors")
                if _api_errors_present(api_errors):
                    if _is_request_limit_error(api_errors):
                        _quota_exceeded_day = get_bot_local_date_string()
                    _log_api_payload_error(url, response.status, data.get("parameters"), api_errors)
                    return None

                if "response" not in data:
                    logger.warning(f"⚠️ API Warning for {url}: 'response' key missing in successful JSON. Data: {str(data)[:200]}")

                return data

            elif response.status == 429:
                _quota_exceeded_day = get_bot_local_date_string()
                logger.warning(f"Rate limited! Status: {response.status} for {url}. Check API plan limits.")
                return None
            else:
                error_text = await response.text()
                logger.error(f"❌ HTTP Error! Status: {response.status} for {url}. Response: {error_text[:200]}")
                return None

    except aiohttp.ClientError as e:
        logger.error(f"❌ Network/Client Error for {url}: {e}")
        return None
    except asyncio.TimeoutError:
        logger.error(f"❌ Request to {url} timed out after {API_REQUEST_TIMEOUT}s.")
        return None
    except Exception as e:
        logger.error(f"💥 Unexpected error during API request to {url}: {e}", exc_info=True)
        return None

async def fetch_day_fixtures(session: aiohttp.ClientSession) -> list:
    """Compatibility wrapper for the configured display date."""
    return await fetch_fixtures_by_date(session, get_bot_local_date_string())


async def fetch_fixtures_by_date(session: aiohttp.ClientSession, date_str: str) -> list:
    """Fetch fixtures for one provider date, filtered by TRACKED_LEAGUE_IDS."""
    url = f"https://v3.football.api-sports.io/fixtures?date={date_str}"
    payload = await _make_request(session, url)

    if payload and isinstance(payload.get("response"), list):
        fixtures = payload["response"]
        return [f for f in fixtures if f["league"]["id"] in TRACKED_LEAGUE_IDS]
    return []

async def fetch_live_fixtures(session: aiohttp.ClientSession) -> list:
    """Fetches all live fixtures, filtered by TRACKED_LEAGUE_IDS."""
    url = "https://v3.football.api-sports.io/fixtures?live=all"
    payload = await _make_request(session, url)

    if payload and isinstance(payload.get("response"), list):
        fixtures = payload["response"]
        return [f for f in fixtures if f["league"]["id"] in TRACKED_LEAGUE_IDS]
    return []

async def fetch_live_fixtures_payload(session: aiohttp.ClientSession) -> dict | None:
    """
    Fetches all currently live API-Football fixtures.
    Returns the full JSON payload so callers can inspect the response shape.
    """
    url = "https://v3.football.api-sports.io/fixtures?live=all"
    return await _make_request(session, url)

async def fetch_fixture_by_id(session: aiohttp.ClientSession, fixture_id: int) -> dict | None:
    """
    Fetches a specific fixture by ID.
    Returns the full JSON payload (dict) or None on error, so callers can do payload.get('response').
    """
    url = f"https://v3.football.api-sports.io/fixtures?id={fixture_id}"
    payload = await _make_request(session, url)

    return payload

async def fetch_fixture_events(session: aiohttp.ClientSession, fixture_id: int) -> dict | None:
    """
    Fetches events for a specific API-Football fixture ID.
    Returns the full JSON payload (dict) or None on error.
    """
    url = f"https://v3.football.api-sports.io/fixtures/events?fixture={fixture_id}"
    return await _make_request(session, url)

async def fetch_next_team_fixture(session: aiohttp.ClientSession, team_id: int) -> dict | None:
    """
    Fetches the next 'Not Started' fixture for a specific team in the current season.
    Returns the fixture dictionary or None if not found or on error.
    """
    season_year = get_current_season_year()
    # API endpoint to get all "Not Started" fixtures for the team in the specified season
    url = f"https://v3.football.api-sports.io/fixtures?team={team_id}&season={season_year}&status=NS"
    logger.info(f"🌐 API Request: Fetching next fixture for team {team_id}, season {season_year} using URL: {url}")

    payload = await _make_request(session, url)

    if payload and isinstance(payload.get("response"), list) and payload["response"]:
        fixtures = payload["response"]

        # Sort by fixture date to find the earliest upcoming one
        # The API might already return them sorted, but explicit sort is safer.
        fixtures.sort(key=lambda f: f.get('fixture', {}).get('date', ''))

        now_utc = bot_now().astimezone(timezone.utc)

        for fixture_details in fixtures:
            fixture_obj = fixture_details.get('fixture', {})
            date_str = fixture_obj.get('date')
            if not date_str:
                logger.warning(f"Fixture for team {team_id} missing date: {fixture_details.get('id', 'N/A')}")
                continue

            try:
                # API fixture dates are typically UTC, like "2024-08-10T14:00:00+00:00"
                match_date_utc = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                # Ensure the match date is actually in the future
                if match_date_utc > now_utc:
                    logger.info(f"Found next fixture for team {team_id}: ID {fixture_obj.get('id')} on {date_str}")
                    return fixture_details # Return the entire fixture object
            except ValueError:
                logger.error(f"Could not parse fixture date '{date_str}' for team ID {team_id}, fixture ID {fixture_obj.get('id', 'N/A')}.")
                continue

        logger.info(f"No future 'Not Started' fixtures found for team ID {team_id} in season {season_year} after sorting and date checking from {len(fixtures)} potential fixtures.")
        return None # No future "Not Started" fixture found

    elif payload and isinstance(payload.get("response"), list) and not payload["response"]:
        logger.info(f"API returned no 'Not Started' (NS) fixtures for team ID {team_id} in season {season_year}.")
        return None
    else:
        # _make_request would have logged an error if payload was None due to API/HTTP error
        logger.warning(f"Could not fetch or process fixtures for team ID {team_id}, season {season_year}. Payload received: {str(payload)[:200]}")
        return None
