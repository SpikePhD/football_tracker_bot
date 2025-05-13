# utils/api_client.py
import asyncio 
import aiohttp
import logging # MODIFIED: Import standard logging

from config import API_KEY, TRACKED_LEAGUE_IDS
from utils.time_utils import get_italy_date_string
# MODIFIED: Remove verbose_logger import
# from modules.verbose_logger import log_error, log_warning, log_info 

# MODIFIED: Get a logger instance for this module
logger = logging.getLogger(__name__)

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
    logger.info(f"🌐 API Request: {url}") # MODIFIED: Use logger.info
    try:
        async with session.get(url, headers=HEADERS, timeout=API_REQUEST_TIMEOUT) as response:
            if 200 <= response.status < 300:
                data = await response.json()
                
                api_errors = data.get("errors")
                if api_errors and ( (isinstance(api_errors, list) and len(api_errors) > 0) or isinstance(api_errors, dict) ):
                    logger.error(f"❌ API Error for {url}: {api_errors} | Status: {response.status} | Parameters: {data.get('parameters')}") # MODIFIED
                    return None 

                if "response" not in data:
                    logger.warning(f"⚠️ API Warning for {url}: 'response' key missing in successful JSON. Data: {str(data)[:200]}") # MODIFIED
                
                return data 
            
            elif response.status == 429: 
                logger.warning(f"Rate limited! Status: {response.status} for {url}. Check API plan limits.") # MODIFIED
                return None
            else:
                error_text = await response.text()
                logger.error(f"❌ HTTP Error! Status: {response.status} for {url}. Response: {error_text[:200]}") # MODIFIED
                return None
                
    except aiohttp.ClientError as e: 
        logger.error(f"❌ Network/Client Error for {url}: {e}") # MODIFIED
        return None
    except asyncio.TimeoutError:
        logger.error(f"❌ Request to {url} timed out after {API_REQUEST_TIMEOUT}s.") # MODIFIED
        return None
    except Exception as e: 
        logger.error(f"💥 Unexpected error during API request to {url}: {e}", exc_info=True) # MODIFIED, added exc_info=True for full traceback on unexpected errors
        return None

async def fetch_day_fixtures(session: aiohttp.ClientSession) -> list:
    """Fetches fixtures for today, filtered by TRACKED_LEAGUE_IDS."""
    url = f"https://v3.football.api-sports.io/fixtures?date={get_italy_date_string()}"
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

async def fetch_fixture_by_id(session: aiohttp.ClientSession, fixture_id: int) -> dict | None:
    """
    Fetches a specific fixture by ID.
    Returns the full JSON payload (dict) or None on error, so callers can do payload.get('response').
    """
    url = f"https://v3.football.api-sports.io/fixtures?id={fixture_id}"
    payload = await _make_request(session, url)
    
    return payload