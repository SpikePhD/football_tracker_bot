# utils/event_formatter.py
# Shared helpers for formatting match events (goals, red cards, shootouts).


def is_shootout_event(event: dict) -> bool:
    """Return True for penalty shootout events that must not count as match goals."""
    event_type = event.get("type")
    detail = str(event.get("detail") or "").lower()
    return (
        event.get("shootout") is True
        or event_type == "PenaltyShootout"
        or "shootout" in detail
    )


def normal_match_events(events: list) -> list:
    return [event for event in events if not is_shootout_event(event)]


def shootout_events(events: list) -> list:
    return [event for event in events if is_shootout_event(event)]


def event_completeness_note(goals: dict, events: list) -> str:
    """
    Return a warning string if the goal events don't account for all goals in
    the score (a known ESPN public API limitation), otherwise return empty string.

    Example return value: ' ⚠️ 2 goal(s) missing from event data'
    """
    try:
        total_goals = int(goals.get("home", 0) or 0) + int(goals.get("away", 0) or 0)
        goal_events = sum(1 for e in normal_match_events(events) if e.get("type") == "Goal")
        if goal_events < total_goals:
            missing = total_goals - goal_events
            return f" ⚠️ {missing} goal(s) missing from event data"
    except (TypeError, ValueError):
        pass
    return ""


def normalize_api_football_events(raw_events: list) -> list:
    """
    Convert raw API-Football event dicts to the normalized format used across the bot.
    """
    normalized = []
    for e in raw_events:
        event_type = e.get("type")
        detail = e.get("detail")
        is_shootout = (
            event_type == "Penalty Shootout"
            or "shootout" in str(detail or "").lower()
        )
        normalized.append({
            "time": {"elapsed": e.get("time", {}).get("elapsed", "?")},
            "player": {"name": e.get("player", {}).get("name", "N/A")},
            "team": {
                "id": e.get("team", {}).get("id"),
                "name": e.get("team", {}).get("name"),
            },
            "type": "PenaltyShootout" if is_shootout else event_type,
            "detail": "Scored" if is_shootout and not detail else detail,
            "shootout": True if is_shootout else False,
        })
    return normalized


def format_match_events(events: list, home: str, away: str) -> list[str]:
    """
    Convert a list of match event dicts into human-readable strings.

    Returns strings like:
        "45' - Player Name (H)"
        "67' - Player Name (Penalty) (A)"
        "80' - Player Name (Red Card) (H)"
    """
    result = []
    for e in normal_match_events(events):
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


def _event_team_matches(event: dict, team: dict) -> bool:
    event_team = event.get("team", {})
    team_id = team.get("id")
    team_name = team.get("name")
    return (
        team_id is not None
        and event_team.get("id") is not None
        and str(event_team.get("id")) == str(team_id)
    ) or (
        team_name is not None
        and event_team.get("name") == team_name
    )


def _shootout_score(match: dict) -> tuple[int, int]:
    teams = match.get("teams", {})
    home = teams.get("home", {})
    away = teams.get("away", {})
    home_score = 0
    away_score = 0
    for event in shootout_events(match.get("events", [])):
        if event.get("detail") not in (None, "", "Scored"):
            continue
        if _event_team_matches(event, home):
            home_score += 1
        elif _event_team_matches(event, away):
            away_score += 1
    return home_score, away_score


def _regular_time_score(match: dict) -> tuple[int, int]:
    teams = match.get("teams", {})
    home = teams.get("home", {})
    away = teams.get("away", {})
    goals = match.get("goals", {})
    home_score = 0
    away_score = 0
    goal_events = [
        e for e in normal_match_events(match.get("events", []))
        if e.get("type") == "Goal"
    ]

    try:
        expected_total = int(goals.get("home", 0) or 0) + int(goals.get("away", 0) or 0)
    except (TypeError, ValueError):
        expected_total = None

    if expected_total is not None and len(goal_events) != expected_total:
        return goals.get("home", "?"), goals.get("away", "?")

    for event in goal_events:
        elapsed = event.get("time", {}).get("elapsed")
        if isinstance(elapsed, int) and elapsed > 90:
            continue
        if _event_team_matches(event, home):
            home_score += 1
        elif _event_team_matches(event, away):
            away_score += 1
    return home_score, away_score


def _format_shootout_takers(match: dict) -> str:
    teams = match.get("teams", {})
    home = teams.get("home", {})
    away = teams.get("away", {})
    grouped = {home.get("name", "Home"): [], away.get("name", "Away"): []}

    for event in shootout_events(match.get("events", [])):
        if event.get("detail") not in (None, "", "Scored"):
            continue
        player = event.get("player", {}).get("name")
        if not player:
            continue
        if _event_team_matches(event, home):
            grouped[home.get("name", "Home")].append(player)
        elif _event_team_matches(event, away):
            grouped[away.get("name", "Away")].append(player)

    parts = [f"{team}: {', '.join(players)}" for team, players in grouped.items() if players]
    return f"Pens scored: {'; '.join(parts)}" if parts else ""


def format_shootout_segments(match: dict, final: bool = False) -> list[str]:
    events = shootout_events(match.get("events", []))
    status = match.get("fixture", {}).get("status", {})
    status_text = " ".join(
        str(status.get(key) or "") for key in ("short", "detail", "description", "name")
    ).lower()
    has_shootout_status = "pen" in status_text
    if not events and not has_shootout_status:
        return []

    teams = match.get("teams", {})
    home = teams.get("home", {}).get("name", "Home")
    away = teams.get("away", {}).get("name", "Away")
    home_pens, away_pens = _shootout_score(match)
    segments = []

    if final:
        regular_home, regular_away = _regular_time_score(match)
        segments.append(f"After 90': {regular_home} - {regular_away}")
        winner = match.get("winner")
        if winner and (home_pens or away_pens):
            segments.append(f"{winner} win {home_pens} - {away_pens} on penalties")
        elif winner:
            segments.append(f"{winner} win on penalties")
        else:
            segments.append(f"Penalties: {home} {home_pens} - {away_pens} {away}")
    else:
        segments.append(f"Penalties: {home} {home_pens} - {away_pens} {away}")

    takers = _format_shootout_takers(match)
    if takers:
        segments.append(takers)
    return segments
