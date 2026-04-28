# utils/tennis_formatter.py
from utils.time_utils import parse_utc_to_italy


def _format_sets(sets: list[dict]) -> str:
    if not sets:
        return ""

    parts = []
    for s in sets:
        a = s.get("a")
        b = s.get("b")
        if a is None or b is None:
            continue
        a_tb = s.get("a_tb")
        b_tb = s.get("b_tb")
        score = f"{a}-{b}"
        if a_tb is not None or b_tb is not None:
            score += f" ({a_tb or 0}-{b_tb or 0})"
        parts.append(score)

    return " | ".join(parts)


def format_tennis_pre_message(match: dict) -> str:
    start = match.get("start_time")
    dt_text = "TBD"
    if start:
        try:
            dt = parse_utc_to_italy(start)
            dt_text = dt.strftime('%A, %B %d, %Y at %H:%M')
        except Exception:
            dt_text = start
    return (
        f"🎾 Upcoming: {match.get('player_a')} vs {match.get('player_b')} "
        f"({match.get('event_name')} - {match.get('tour')})\n"
        f"Round/Status: {match.get('round') or 'Scheduled'}\n"
        f"Time: {dt_text} (Italy Time)"
    )


def format_tennis_live_message(match: dict) -> str:
    sets_str = _format_sets(match.get("sets") or [])
    status = match.get("status", {})
    detail = status.get("detail") or status.get("description") or "LIVE"
    base = (
        f"🎾 LIVE: {match.get('player_a')} vs {match.get('player_b')} "
        f"({match.get('event_name')} - {match.get('tour')})"
    )
    if sets_str:
        base += f"\nSets: {sets_str}"
    base += f"\nStatus: {detail}"
    return base


def format_tennis_final_message(match: dict) -> str:
    sets_str = _format_sets(match.get("sets") or [])
    winner = match.get("winner") or "Winner not available"
    msg = (
        f"🎾 FT: {match.get('player_a')} vs {match.get('player_b')} "
        f"({match.get('event_name')} - {match.get('tour')})\n"
        f"Winner: {winner}"
    )
    if sets_str:
        msg += f"\nFinal sets: {sets_str}"
    return msg


def tennis_live_state_key(match: dict) -> str:
    sets_str = _format_sets(match.get("sets") or [])
    status = match.get("status", {})
    return f"{status.get('short')}|{status.get('detail')}|{sets_str}|{match.get('winner')}"
