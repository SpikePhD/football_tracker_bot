# utils/api_client.py
import asyncio # For asyncio.TimeoutError
import aiohttp
from config import API_KEY, TRACKED_LEAGUE_IDS # TRACKED_LEAGUE_IDS still used for filtering
from utils.time_utils import get_italy_date_string
# Assuming verbose_logger is still the primary logger for this module
# If migrating to standard logging, these would change to:
# import logging
# logger = logging.getLogger(__name__)
from modules.verbose_logger import log_error, log_warning, log_info

HEADERS = {
    "x-apisports-key": API_KEY,
    "Content-Type": "application/json"
}

# Timeout for API requests in seconds
API_REQUEST_TIMEOUT = 15

async def _make_request(session: aiohttp.ClientSession, url: str) -> dict | None:
    """
    Helper function to make an API request and handle common errors.
    Returns the parsed JSON data (the whole payload) or None on error.
    """
    log_info(f"🌐 API Request: {url}") # Log the URL being called
    try:
        async with session.get(url, headers=HEADERS, timeout=API_REQUEST_TIMEOUT) as response:
            if 200 <= response.status < 300:
                data = await response.json()
                
                # Check for API-specific errors (common in api-sports.io)
                # Errors can be an empty list, a list of strings, or a dict.
                api_errors = data.get("errors")
                if api_errors and ( (isinstance(api_errors, list) and len(api_errors) > 0) or isinstance(api_errors, dict) ):
                    log_error(f"❌ API Error for {url}: {api_errors} | Status: {response.status} | Parameters: {data.get('parameters')}")
                    return None # Indicate failure due to API error message

                # Check if 'response' key exists, as it's expected by callers
                if "response" not in data:
                    log_warning(f"⚠️ API Warning for {url}: 'response' key missing in successful JSON. Data: {str(data)[:200]}")
                    # Depending on API behavior, this might be an error or just an empty valid response.
                    # For now, return the data, callers must be robust.
                    # Or, decide if this should be treated as an error: return None
                
                return data # Return the full parsed JSON payload
            
            elif response.status == 429: # Too Many Requests
                log_warning(f"Rate limited! Status: {response.status} for {url}. Check API plan limits.")
                # Consider adding a retry mechanism with backoff here for critical calls.
                return None
            else:
                error_text = await response.text()
                log_error(f"❌ HTTP Error! Status: {response.status} for {url}. Response: {error_text[:200]}")
                return None
                
    except aiohttp.ClientError as e: # Covers various connection errors
        log_error(f"❌ Network/Client Error for {url}: {e}")
        return None
    except asyncio.TimeoutError:
        log_error(f"❌ Request to {url} timed out after {API_REQUEST_TIMEOUT}s.")
        return None
    except Exception as e: # Catch any other unexpected errors during request/parsing
        log_error(f"💥 Unexpected error during API request to {url}: {e}")
        return None

async def fetch_day_fixtures(session: aiohttp.ClientSession) -> list:
    """Fetches fixtures for today, filtered by TRACKED_LEAGUE_IDS."""
    url = f"https://v3.football.api-sports.io/fixtures?date={get_italy_date_string()}"
    payload = await _make_request(session, url)
    
    if payload and isinstance(payload.get("response"), list):
        fixtures = payload["response"]
        # Filter here, so downstream modules don't need to.
        return [f for f in fixtures if f["league"]["id"] in TRACKED_LEAGUE_IDS]
    return [] # Return empty list on error or if response format is unexpected

async def fetch_live_fixtures(session: aiohttp.ClientSession) -> list:
    """Fetches all live fixtures, filtered by TRACKED_LEAGUE_IDS."""
    url = "https://v3.football.api-sports.io/fixtures?live=all"
    payload = await _make_request(session, url)

    if payload and isinstance(payload.get("response"), list):
        fixtures = payload["response"]
        # Filter here, so downstream modules don't need to.
        return [f for f in fixtures if f["league"]["id"] in TRACKED_LEAGUE_IDS]
    return []

async def fetch_fixture_by_id(session: aiohttp.ClientSession, fixture_id: int) -> dict | None:
    """
    Fetches a specific fixture by ID.
    Returns the full JSON payload (dict) or None on error, so callers can do payload.get('response').
    """
    url = f"https://v3.football.api-sports.io/fixtures?id={fixture_id}"
    payload = await _make_request(session, url)
    
    # Callers expect the full payload to check 'response' themselves
    return payload