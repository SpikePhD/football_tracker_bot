# config.py
import os
from dotenv import load_dotenv

# ─ load your .env
load_dotenv()

# ─ Discord / API credentials
API_KEY   = os.getenv("API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
_channel  = os.getenv("CHANNEL_ID")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment/.env")
if not API_KEY:
    raise RuntimeError("API_KEY is not set in environment/.env")
if not _channel:
    raise RuntimeError("CHANNEL_ID is not set in environment/.env")

CHANNEL_ID = int(_channel)

# ─ Tracked Leagues
TRACKED_LEAGUE_IDS = [
    135,  # Serie A
    137,  # Coppa Italia
    547,  # Supercoppa Italiana
    39,   # Premier League
    45,   # FA Cup
    48,   # Carabao Cup
    528,  # Community Shield
    140,  # La Liga
    143,  # Copa del Rey
    556,  # Supercopa
    2,    # Champions League
    3,    # Europa League
    848,  # Conference League
    531,  # UEFA Super Cup
    1168, # Intercontinental Cup
    15,   # Club World Cup
    1,    # World Cup
    4     # EURO
]

# - Tracked individual teams
AC_MILAN_TEAM_ID = 489

# ─ ESPN league slugs (maps API-Football league ID → ESPN URL slug)
LEAGUE_SLUG_MAP = {
    135:  "ita.1",               # Serie A
    137:  "ita.coppa_italia",    # Coppa Italia
    547:  "ita.super_cup",       # Supercoppa Italiana
    39:   "eng.1",               # Premier League
    45:   "eng.fa",              # FA Cup
    48:   "eng.league_cup",      # Carabao Cup
    528:  "eng.charity",         # Community Shield
    140:  "esp.1",               # La Liga
    143:  "esp.copa_del_rey",    # Copa del Rey
    556:  "esp.super_cup",       # Supercopa de España
    2:    "uefa.champions",      # Champions League
    3:    "uefa.europa",         # Europa League
    848:  "uefa.europa.conf",    # Conference League
    531:  "uefa.super_cup",      # UEFA Super Cup
    1168: "fifa.intercontinental_cup",  # Intercontinental Cup
    15:   "fifa.cwc",            # Club World Cup
    1:    "fifa.world",          # World Cup
    4:    "uefa.euro",           # EURO
}