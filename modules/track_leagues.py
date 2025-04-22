# modules/track_leagues.py
from config import TRACKED_LEAGUE_IDS

def is_tracked(league_id: int) -> bool:
    return league_id in TRACKED_LEAGUE_IDS

async def setup(bot):
    print("✔ track_leagues module loaded")
