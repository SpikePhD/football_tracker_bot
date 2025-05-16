# utils/api_client.py
import asyncio 
import aiohttp
import logging # MODIFIED: Import standard logging
import pytz
from datetime import datetime
from .time_utils import get_current_season_year, parse_utc_to_italy

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
        
        now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)

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