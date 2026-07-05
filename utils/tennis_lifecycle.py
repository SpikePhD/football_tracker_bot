def _is_completed_tennis_set(a, b) -> bool:
    try:
        a_score = int(a)
        b_score = int(b)
    except (TypeError, ValueError):
        return False

    high = max(a_score, b_score)
    low = min(a_score, b_score)

    if high == 6 and low <= 4:
        return True
    if high == 7 and low in (5, 6):
        return True
    if high >= 10 and high - low >= 2:
        return True
    return False


def tennis_final_data_ready(match: dict) -> bool:
    """Return true when an FT tennis payload is complete enough to announce."""
    if match.get("status", {}).get("short") != "FT":
        return False
    if not match.get("winner"):
        return False

    sets = match.get("sets") or []
    if not sets:
        return False

    return all(_is_completed_tennis_set(s.get("a"), s.get("b")) for s in sets)
