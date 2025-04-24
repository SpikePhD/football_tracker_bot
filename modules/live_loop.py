# modules/live_loop.py

from config import TRACKED_LEAGUE_IDS, CHANNEL_ID
from utils.api_client import fetch_live_fixtures
from utils.time_utils import italy_now
from modules.verbose_logger import log_info
from modules.ft_handler import track_match_for_ft

# keep track of which live scores we've already posted this session
already_posted = set()

async def run_live_loop(bot):
    """
    Poll /fixtures?live=all, post any new goals or red cards,
    and register each match for later FT checking.
    """
    now = italy_now()
    log_info(f"[{now.strftime('%H:%M')}] 🌐 Querying live endpoint…")

    matches = await fetch_live_fixtures()
    channel = bot.get_channel(CHANNEL_ID)

    for match in matches:
        league_id = match['league']['id']
        if league_id not in TRACKED_LEAGUE_IDS:
            continue

        match_id = match['fixture']['id']
        home = match['teams']['home']['name']
        away = match['teams']['away']['name']
        score = match['goals']
        key = f"{match_id}_{score['home']}-{score['away']}"

        # only post once per unique score update
        if key in already_posted:
            continue
        already_posted.add(key)

        # start tracking this match for its eventual FT
        track_match_for_ft(match)

        # assemble any goal/red‑card events
        events = match.get('events', [])
        event_strings = []
        for e in events:
            minute = e['time']['elapsed']
            player = e['player']['name']
            side   = "(H)" if e['team']['name'] == home else "(A)"

            if e['type'] == 'Goal':
                detail = e['detail']
                tag = f" ({detail})" if detail != "Normal Goal" else ""
                event_strings.append(f"{minute}' - {player}{tag} {side}")
            elif e['type'] == 'Card' and e['detail'] == 'Red Card':
                event_strings.append(f"{minute}' - {player} (Red Card) {side}")

        # build and send the live update line
        line = f"{home} {score['home']} - {score['away']} {away}"
        if event_strings:
            line += " (" + "; ".join(event_strings) + ")"

        if channel:
            await channel.send(line)
            log_info(f"📢 Posted live update: {line}")
