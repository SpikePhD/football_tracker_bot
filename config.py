# config.py
import os
from dotenv import load_dotenv

# â”€ load your .env
load_dotenv()

# â”€ Discord / API credentials
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

# Tennis tracking by player name (normalized lowercase).
TRACKED_TENNIS_PLAYERS = [
    "jannik sinner",
    "lorenzo musetti",
]

# Tennis polling/caching settings (v1 uses ESPN only).
TENNIS_CACHE_TTL_SEC = 55
TENNIS_UPCOMING_DAYS = 7
# â”€ Tracked Leagues
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

# â”€ Human-readable league names (shared by matches and competitions cogs)
LEAGUE_NAME_MAP = {
    135:  "Serie A",
    137:  "Coppa Italia",
    547:  "Supercoppa Italiana",
    39:   "Premier League",
    45:   "FA Cup",
    48:   "Carabao Cup",
    528:  "Community Shield",
    140:  "La Liga",
    143:  "Copa del Rey",
    556:  "Supercopa de EspaÃ±a",
    2:    "Champions League",
    3:    "Europa League",
    848:  "Conference League",
    531:  "UEFA Super Cup",
    1168: "Intercontinental Cup",
    15:   "Club World Cup",
    1:    "FIFA World Cup",
    4:    "UEFA EURO",
}

# â”€ Slug groups for !next command: domestic competitions per primary league + shared internationals
INTERNATIONAL_SLUGS = [
    "uefa.champions", "uefa.europa", "uefa.europa.conf",
    "uefa.super_cup", "fifa.cwc", "fifa.intercontinental_cup",
    "fifa.world", "uefa.euro",
]

DOMESTIC_SLUG_GROUPS = {
    "ita.1": ["ita.1", "ita.coppa_italia", "ita.super_cup"],
    "eng.1": ["eng.1", "eng.fa", "eng.league_cup", "eng.charity"],
    "esp.1": ["esp.1", "esp.copa_del_rey", "esp.super_cup"],
}


# â”€â”€ Cloud LLM â€” used by !ask (OpenAI-compatible API) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
LLM_API_KEY   = os.getenv("LLM_API_KEY",   "")
LLM_BASE_URL  = os.getenv("LLM_BASE_URL",  "https://api.mistral.ai/v1")
LLM_MODEL     = os.getenv("LLM_MODEL",     "mistral-small-latest")
LLM_SYSTEM_PROMPT = os.getenv(
    "LLM_SYSTEM_PROMPT",
    "You are Marco Van Botten, a die-hard AC Milan supporter and passionate football expert. "
    "You answer questions about football with deep love for AC Milan and Italian football culture. "
    "Be concise, punchy, and occasionally dramatic. "
    "IMPORTANT: Never answer from memory for anything that could be factually wrong or outdated â€” "
    "this includes ages, birthdays, current roles, recent results, transfer news, standings, or any "
    "specific statistic. Always use web_search for these. Only skip the search for timeless facts "
    "you are completely certain about (e.g. who won a specific historic final). "
    "For AC Milan news and transfers, prefer: acmilan.com, gazzetta.it, corrieredellosport.it, calciomercato.com."
)

# Trusted-source search settings for !ask web_search tool.
_trusted_domains_raw = os.getenv(
    "TRUSTED_SPORT_DOMAINS",
    "acmilan.com,gazzetta.it,corrieredellosport.it,calciomercato.com,espn.com,bbc.com,skysports.com,theathletic.com,uefa.com,fifa.com,legaseriea.it",
)
TRUSTED_SPORT_DOMAINS = [
    d.strip().lower()
    for d in _trusted_domains_raw.split(",")
    if d.strip()
]
WEB_SEARCH_MIN_TRUSTED_RESULTS = int(os.getenv("WEB_SEARCH_MIN_TRUSTED_RESULTS", "2"))


def build_league_slugs(primary_slug: str) -> list:
    """
    Return the full list of ESPN league slugs to search for a team's next fixture.
    Combines the domestic group for the given primary slug with all international slugs.
    """
    domestic = DOMESTIC_SLUG_GROUPS.get(primary_slug, [primary_slug])
    return list(dict.fromkeys(domestic + INTERNATIONAL_SLUGS))

# â”€ ESPN league slugs (maps API-Football league ID â†’ ESPN URL slug)
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
    556:  "esp.super_cup",       # Supercopa de EspaÃ±a
    2:    "uefa.champions",      # Champions League
    3:    "uefa.europa",         # Europa League
    848:  "uefa.europa.conf",    # Conference League
    531:  "uefa.super_cup",      # UEFA Super Cup
    1168: "fifa.intercontinental_cup",  # Intercontinental Cup
    15:   "fifa.cwc",            # Club World Cup
    1:    "fifa.world",          # World Cup
    4:    "uefa.euro",           # EURO
}

