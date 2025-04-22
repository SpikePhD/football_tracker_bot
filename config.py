# config.py
import os
from dotenv import load_dotenv

# ─ load your .env
load_dotenv()

# ─ Discord / API credentials
API_KEY    = os.getenv("API_KEY")      # your Football‑API key
BOT_TOKEN  = os.getenv("BOT_TOKEN")    # your Discord bot token
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

# ─ Which leagues you track today
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
