# modules/ft_handler.py

from datetime import datetime, timedelta
from config import CHANNEL_ID
from utils.api_client import fetch_fixture_by_id
from utils.time_utils import italy_now
from modules.verbose_logger import log_info

tracked_matches = {}

def track_match_for_ft(match):
    match_id    = match['fixture']['id']
    kickoff_utc = match['fixture']['date']
    kickoff     = datetime.fromisoformat(kickoff_utc.replace('Z', '+00:00'))
    kickoff     = kickoff.astimezone(italy_now().tzinfo)

    expected_ft = kickoff + timedelta(minutes=112)
    tracked_matches[match_id] = {
        "exp_ft": expected_ft,
        "score":  match['goals']
    }

    home = match['teams']['home']['name']
    away = match['teams']['away']['name']
    log_info(f"🆕 Tracking {home} vs {away} (exp FT {expected_ft.strftime('%H:%M')})")


async def fetch_and_post_ft(bot):
    now = italy_now()
    for match_id, info in list(tracked_matches.items()):
        if now < info["exp_ft"]:
            continue

        log_info(f"🔍 FT check for match {match_id}")
        payload = await fetch_fixture_by_id(match_id)
        # now payload is a dict, so .get works
        resp = payload.get('response')
        if not resp:
            continue

        data = resp[0]
        if data['fixture']['status']['short'] != "FT":
            continue

        home   = data['teams']['home']['name']
        away   = data['teams']['away']['name']
        goals  = data['goals']
        events = data.get('events', [])

        detail_lines = []
        for e in events:
            minute = e['time']['elapsed']
            player = e['player']['name']
            tag    = "(H)" if e['team']['name']==home else "(A)"
            if e['type']=="Goal":
                extra = f" ({e['detail']})" if e['detail']!="Normal Goal" else ""
                detail_lines.append(f"{minute}' – {player}{extra} {tag}")
            elif e['type']=="Card" and e['detail']=="Red Card":
                detail_lines.append(f"{minute}' – {player} {tag} (Red Card)")

        ft_line = f"FT: {home} {goals['home']} – {goals['away']} {away}"
        if detail_lines:
            ft_line += f" ({'; '.join(detail_lines)})"

        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            await channel.send(ft_line)
            log_info(f"📢 Posted FT: {ft_line}")

        del tracked_matches[match_id]


async def post_initial_fts(fixtures, bot):
    """
    On startup, post any games already at FT.
    """
    for m in fixtures:
        if m['fixture']['status']['short'] != "FT":
            continue

        payload = await fetch_fixture_by_id(m['fixture']['id'])
        resp    = payload.get('response')
        if not resp:
            continue

        data   = resp[0]
        home   = data['teams']['home']['name']
        away   = data['teams']['away']['name']
        goals  = data['goals']
        events = data.get('events', [])

        detail_lines = []
        for e in events:
            minute = e['time']['elapsed']
            player = e['player']['name']
            tag    = "(H)" if e['team']['name']==home else "(A)"
            if e['type']=="Goal":
                extra = f" ({e['detail']})" if e['detail']!="Normal Goal" else ""
                detail_lines.append(f"{minute}' – {player}{extra} {tag}")
            elif e['type']=="Card" and e['detail']=="Red Card":
                detail_lines.append(f"{minute}' – {player} {tag} (Red Card)")

        line = f"FT: {home} {goals['home']} – {goals['away']} {away}"
        if detail_lines:
            line += f" ({'; '.join(detail_lines)})"

        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            await channel.send(line)
            log_info(f"📢 Posted initial FT: {line}")
