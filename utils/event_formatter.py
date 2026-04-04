# utils/event_formatter.py
# Shared helper for formatting match events (goals, red cards) into display strings.


def format_match_events(events: list, home: str, away: str) -> list[str]:
    """
    Convert a list of match event dicts into human-readable strings.

    Returns strings like:
        "45' - Player Name (H)"
        "67' - Player Name (Penalty) (A)"
        "80' - Player Name (Red Card) (H)"
    """
    result = []
    for e in events:
        minute = e.get("time", {}).get("elapsed", "?")
        player = e.get("player", {}).get("name", "N/A")
        team = e.get("team", {}).get("name")
        side = "(H)" if team == home else "(A)" if team == away else ""

        event_type = e.get("type")
        detail = e.get("detail")

        if event_type == "Goal":
            tag = f" ({detail})" if detail and detail != "Normal Goal" else ""
            result.append(f"{minute}' - {player}{tag} {side}")
        elif event_type == "Card" and detail == "Red Card":
            result.append(f"{minute}' - {player} (Red Card) {side}")

    return result
