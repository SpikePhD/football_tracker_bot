# utils/event_formatter.py
# Shared helpers for formatting match events (goals, red cards) into display strings.


def event_completeness_note(goals: dict, events: list) -> str:
    """
    Return a warning string if the goal events don't account for all goals in
    the score (a known ESPN public API limitation), otherwise return empty string.

    Example return value: ' ⚠️ 2 goal(s) missing from event data'
    """
    try:
        total_goals = int(goals.get("home", 0) or 0) + int(goals.get("away", 0) or 0)
        goal_events = sum(1 for e in events if e.get("type") == "Goal")
        if goal_events < total_goals:
            missing = total_goals - goal_events
            return f" ⚠️ {missing} goal(s) missing from event data"
    except (TypeError, ValueError):
        pass
    return ""


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
