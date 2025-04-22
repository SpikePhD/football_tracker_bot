import aiohttp
from config import API_KEY, TRACKED_LEAGUE_IDS
from utils.time_utils import get_italy_date_string

HEADERS = {
    "x-apisports-key": API_KEY,
    "Content-Type": "application/json"
}

async def fetch_day_fixtures():
    url = f"https://v3.football.api-sports.io/fixtures?date={get_italy_date_string()}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=HEADERS) as response:
            data = await response.json()
            fixtures = data.get("response", [])
            return [f for f in fixtures if f["league"]["id"] in TRACKED_LEAGUE_IDS]

async def fetch_live_fixtures():
    url = "https://v3.football.api-sports.io/fixtures?live=all"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=HEADERS) as response:
            data = await response.json()
            fixtures = data.get("response", [])
            return [f for f in fixtures if f["league"]["id"] in TRACKED_LEAGUE_IDS]

async def fetch_fixture_by_id(fixture_id: int):
    """
    Return the full JSON payload so callers can do payload['response'][0].
    """
    url = f"https://v3.football.api-sports.io/fixtures?id={fixture_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=HEADERS) as response:
            return await response.json()
