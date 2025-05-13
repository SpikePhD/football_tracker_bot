# modules/live_loop.py

from config import TRACKED_LEAGUE_IDS, CHANNEL_ID
from utils.api_client import fetch_live_fixtures
from utils.time_utils import italy_now
from modules.verbose_logger import log_info, log_error
from modules.ft_handler import track_match_for_ft
from modules.message_edit_tracker import safe_upsert # MODIFIED: Import safe_upsert

# keep track of which live scores we've already posted this session
already_posted = set()

async def run_live_loop(bot):
    """
    Poll /fixtures?live=all, post/edit any new goals or red cards,
    and register each match for later FT checking.
    """
    now = italy_now()
    log_info(f"[{now.strftime('%H:%M')}] 🌐 Querying live endpoint…")

    # It's assumed fetch_live_fixtures will be updated to use a shared session
    # and have better error handling from api_client.py rewrite.
    # For now, structure remains the same.
    matches = await fetch_live_fixtures(bot.http_session) 
    if not matches: # Basic check if fetch_live_fixtures could return None or empty on error
        log_info(f"[{now.strftime('%H:%M')}] 😕 No live fixtures returned or error in fetch.")
        return

    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        log_error(f"[{now.strftime('%H:%M')}] ❌ Cannot find channel with ID {CHANNEL_ID}")
        return

    for match in matches:
        league_id = match['league']['id']
        # Note: api_client.fetch_live_fixtures also filters by TRACKED_LEAGUE_IDS.
        # This check is redundant if api_client guarantees it.
        if league_id not in TRACKED_LEAGUE_IDS:
            continue

        match_id = match['fixture']['id']
        home = match['teams']['home']['name']
        away = match['teams']['away']['name']
        score = match['goals']
        key = f"{match_id}_{score['home']}-{score['away']}"

        # only post once per unique score update
        # TODO: Consider if non-score-changing events (e.g., a red card after a score is known)
        # should trigger an update. If so, this 'key' and 'already_posted' logic needs refinement.
        if key in already_posted:
            continue
        
        # --- Start of new logic for preparing message content ---
        # We need to determine if there are actual *new events* to report for this scoreline,
        # or if it's just the first time we're seeing this score.
        # The original logic would post if the 'key' (score) was new.
        # For a more refined "update" system, we'd ideally check if the *events* associated with this score are new.
        # However, the current `already_posted` only prevents re-posting the *same score*.
        # Let's proceed with the assumption that if the score is new to `already_posted`, we attempt to post/edit.
        
        events = match.get('events', [])
        event_strings = []
        new_significant_event_for_this_score = False # Flag to check if there's content beyond just score

        for e in events:
            minute = e['time']['elapsed']
            player = e['player']['name']
            side = "(H)" if e['team']['name'] == home else "(A)"

            if e['type'] == 'Goal':
                detail = e['detail']
                tag = f" ({detail})" if detail != "Normal Goal" else ""
                event_strings.append(f"{minute}' - {player}{tag} {side}")
                new_significant_event_for_this_score = True
            elif e['type'] == 'Card' and e['detail'] == 'Red Card':
                event_strings.append(f"{minute}' - {player} (Red Card) {side}")
                new_significant_event_for_this_score = True
        
        # Only proceed if:
        # 1. It's a genuinely new score (key not in already_posted).
        #    (This check is done above with `if key in already_posted: continue`)
        # 2. There are significant events (goals/red cards) to announce with this score,
        #    OR if you want to announce every score change regardless of specific new events.
        #    The current logic effectively announces any new score. If events[] is empty
        #    but score changed, it still forms a line and upserts. This is usually desired.

        # Add to already_posted *before* attempting to send, to handle concurrent runs or quick retries.
        already_posted.add(key)
        
        # Start tracking this match for its eventual FT (even if we don't post an update now,
        # for instance, if a match starts and there are no immediate goals/cards)
        # This should be fine, as ft_handler checks actual FT status.
        track_match_for_ft(match)

        # build the live update line
        line = f"{home} {score['home']} - {score['away']} {away}"
        if event_strings:
            line += " (" + "; ".join(event_strings) + ")"
        else:
            # If there are no specific goal/red card events *right now* for this score,
            # but the score itself is new (e.g. match just started 0-0, or score changed from 0-0 to 1-0 with no events listed *yet*),
            # we still want to post the score.
            # If you ONLY want to post when there's a goal/red card event string,
            # you could add: if not event_strings: continue
            pass


        # MODIFIED: Use safe_upsert instead of channel.send
        await safe_upsert(channel, content=line)
        # Update log message
        log_info(f"📢 Upserted live update: {line}")