import logging
from datetime import datetime, timezone
from pathlib import Path

import discord

from config import CHANNEL_ID
from modules import match_lifecycle, match_state
from modules.bot_mode import is_silent
from modules.discord_poster import post_new_general_message
from modules.football_memory import update_match_in_memory
from utils.event_formatter import (
    event_completeness_note,
    format_match_events,
    format_shootout_segments,
    is_shootout_event,
    normalize_api_football_events,
)
from utils.time_utils import utc_now

logger = logging.getLogger(__name__)

_past_expected_live_logged: set[str] = set()


def _fixture_status_short(match: dict | None) -> str | None:
    return match_lifecycle.status_short(match)


def track_match_for_ft(match_data: dict, now_utc: datetime | None = None) -> None:
    now_utc = now_utc or utc_now()
    try:
        fixture = match_state.upsert_fixture_from_match(match_data, now_utc, source="espn")
    except Exception as e:
        logger.error("Error tracking match for FT: %s", e, exc_info=True)
        return

    home = match_data.get("teams", {}).get("home", {}).get("name", "Home Team")
    away = match_data.get("teams", {}).get("away", {}).get("name", "Away Team")
    logger.info(
        "Tracking %s vs %s (ID: %s) for FT. Expected check UTC: %s",
        home,
        away,
        fixture.get("fixture_id"),
        fixture.get("expected_ft_utc"),
    )


def is_tracked_for_ft(match_id) -> bool:
    match_state.migrate_ft_state_if_needed()
    return match_state.is_tracked(match_id)


def mark_ft_announced(match_id) -> None:
    if match_id is not None:
        match_state.mark_ft_announced(match_id)


def seed_already_announced_ft(fixtures: list) -> None:
    """Mark already-displayed FT fixtures as announced without touching memory flags."""
    match_state.migrate_ft_state_if_needed()
    count = 0
    now = utc_now()
    for match in fixtures:
        if not match_lifecycle.is_ft(match):
            continue
        mid = match_lifecycle.fixture_identity(match)
        if not mid:
            continue
        match_state.upsert_fixture_from_match(match, now, source="espn")
        existing = match_state.get_fixture_state(mid)
        if existing and existing.get("ft_announced"):
            continue
        match_state.mark_ft_announced(mid)
        count += 1
    if count:
        logger.info("Seeded %d already-FT fixture ID(s).", count)


def _has_required_memory_keys(match_details: dict) -> bool:
    fixture = match_details.get("fixture", {})
    teams = match_details.get("teams", {})
    league = match_details.get("league", {})
    return (
        fixture.get("id") is not None
        and match_lifecycle.is_ft(match_details)
        and teams.get("home", {}).get("id") is not None
        and teams.get("away", {}).get("id") is not None
        and league.get("id") is not None
    )


def _build_ft_message(match_details: dict) -> str:
    home_team = match_details.get("teams", {}).get("home", {}).get("name", "Home Team")
    away_team = match_details.get("teams", {}).get("away", {}).get("name", "Away Team")
    goals = match_details.get("goals", {"home": "?", "away": "?"})
    events = match_details.get("events", [])

    detail_lines = format_match_events(events, home_team, away_team)
    ft_message = f"FT: {home_team} {goals.get('home', '?')} - {goals.get('away', '?')} {away_team}"
    if detail_lines:
        ft_message += f" ({'; '.join(detail_lines)})"

    shootout_segments = format_shootout_segments(match_details, final=True)
    if shootout_segments:
        ft_message += " | " + " | ".join(shootout_segments)

    note = event_completeness_note(goals, events)
    if note:
        ft_message += note
        logger.warning(
            "Warning: FT event mismatch for %s vs %s: score total=%s, goal events=%s.",
            home_team,
            away_team,
            int(goals.get("home", 0) or 0) + int(goals.get("away", 0) or 0),
            sum(1 for e in events if e.get("type") == "Goal" and not is_shootout_event(e)),
        )
    return ft_message


async def _post_ft_from_data(bot: discord.Client, match_details: dict) -> bool:
    ft_message = _build_ft_message(match_details)
    safe_message = ft_message.encode("ascii", "backslashreplace").decode("ascii")
    logger.info("Posting FT result: %s", safe_message)
    sent = await post_new_general_message(bot, CHANNEL_ID, content=ft_message)
    return sent is not None


async def process_terminal_fixture(
    bot: discord.Client,
    match_details: dict,
    now_utc: datetime | None = None,
    memory_dir: Path | None = None,
) -> None:
    if not match_lifecycle.is_terminal(match_details):
        return

    from modules import api_provider

    now_utc = now_utc or utc_now()
    match_state.migrate_ft_state_if_needed(memory_dir=memory_dir)
    enriched = await api_provider.enrich_fixture_events(bot.http_session, match_details)
    status_payload = enriched.get("fixture", {}).get("status", {}) or {}
    raw_status = status_payload.get("short")
    normalized_status = match_lifecycle.status_short(enriched, log_normalization=True)
    raw_status_text = " | ".join(
        str(status_payload.get(key))
        for key in ("long", "detail", "description", "name")
        if status_payload.get(key)
    )
    provider = (
        enriched.get("provider")
        or enriched.get("source")
        or enriched.get("fixture", {}).get("provider")
        or "unknown"
    )
    logger.info(
        "Terminal fixture processing snapshot: fixture_id=%s provider=%s raw_status=%s "
        "normalized_status=%s raw_status_text=%r.",
        match_lifecycle.fixture_identity(enriched),
        provider,
        raw_status,
        normalized_status,
        raw_status_text,
    )
    fixture = match_state.upsert_fixture_from_match(enriched, now_utc, source="espn", memory_dir=memory_dir)
    fixture_id = fixture["fixture_id"]
    current = match_state.get_fixture_state(fixture_id, memory_dir=memory_dir) or {}
    memory_result = None

    if match_lifecycle.is_ft(enriched) and not current.get("memory_updated"):
        if _has_required_memory_keys(enriched):
            try:
                memory_result = await update_match_in_memory(bot.http_session, enriched)
            except Exception as e:
                logger.error("Failed to update football memory for match %s: %s", fixture_id, e)
                memory_result = {"updated": False, "reason": "failed_exception"}
            else:
                if memory_result.get("updated"):
                    match_state.mark_memory_updated(fixture_id, memory_dir=memory_dir)
                    logger.info("Updated football memory with FT match: %s", fixture_id)
                else:
                    logger.warning(
                        "Football memory update skipped for FT fixture %s: %s.",
                        fixture_id,
                        memory_result.get("reason", "unknown"),
                    )
        else:
            logger.warning(
                "Skipping football memory update for FT fixture %s because required IDs are missing.",
                fixture_id,
            )
            memory_result = {"updated": False, "reason": "missing_required_data"}
    elif match_lifecycle.is_ft(enriched):
        memory_result = {"updated": True, "reason": "already_updated"}

    current = match_state.get_fixture_state(fixture_id, memory_dir=memory_dir) or {}
    ft_posted = None
    if match_lifecycle.is_ft(enriched) and not current.get("ft_announced"):
        ft_posted = await _post_ft_from_data(bot, enriched)
        if ft_posted:
            match_state.mark_ft_announced(fixture_id, memory_dir=memory_dir)
        else:
            logger.warning("FT announcement skipped or failed for fixture %s.", fixture_id)
    elif match_lifecycle.is_ft(enriched):
        ft_posted = "already_announced"
    logger.info(
        "Terminal fixture processing result: fixture_id=%s memory_update=%s ft_announcement=%s.",
        fixture_id,
        memory_result if memory_result is not None else "not_attempted",
        "posted" if ft_posted is True else (
            "already_announced" if ft_posted == "already_announced"
            else ("skipped_or_failed" if ft_posted is False else "not_attempted")
        ),
    )


def _normalize_api_football_match(match_details_raw: dict) -> dict:
    fixture_status = match_details_raw.get("fixture", {}).get("status", {})
    home_team = match_details_raw.get("teams", {}).get("home", {}).get("name", "Home Team")
    away_team = match_details_raw.get("teams", {}).get("away", {}).get("name", "Away Team")
    raw_events = match_details_raw.get("events", [])

    return {
        "fixture": {
            "id": match_details_raw.get("fixture", {}).get("id"),
            "date": match_details_raw.get("fixture", {}).get("date"),
            "status": {
                "short": fixture_status.get("short"),
                "long": fixture_status.get("long"),
                "elapsed": fixture_status.get("elapsed"),
            },
        },
        "league": {"id": match_details_raw.get("league", {}).get("id")},
        "teams": {
            "home": {
                "id": match_details_raw.get("teams", {}).get("home", {}).get("id"),
                "name": home_team,
            },
            "away": {
                "id": match_details_raw.get("teams", {}).get("away", {}).get("id"),
                "name": away_team,
            },
        },
        "goals": match_details_raw.get("goals", {"home": "?", "away": "?"}),
        "events": normalize_api_football_events(raw_events),
    }


async def fetch_and_post_ft(bot: discord.Client) -> None:
    if is_silent():
        return

    from modules import api_provider

    match_state.migrate_ft_state_if_needed()
    now = utc_now()
    matches = await api_provider.fetch_relevant_football(bot.http_session, now)
    matches_by_id = {
        match_lifecycle.fixture_identity(match): match
        for match in matches
        if match_lifecycle.fixture_identity(match)
    }

    for match in matches:
        fixture_id = match_lifecycle.fixture_identity(match)
        if fixture_id is None:
            continue
        match_state.upsert_fixture_from_match(match, now, source="espn")
        expected = match_lifecycle.expected_ft_check_utc(match)
        if match_lifecycle.is_live(match) and expected and now >= expected:
            status = _fixture_status_short(match)
            if status in {"ET", "PEN"} and fixture_id not in _past_expected_live_logged:
                logger.info(
                    "Match ID %s is past expected FT but still live with status '%s'. Keeping FT tracking active.",
                    fixture_id,
                    status,
                )
                _past_expected_live_logged.add(fixture_id)
            continue
        if match_lifecycle.is_terminal(match):
            await process_terminal_fixture(bot, match, now_utc=now)
            _past_expected_live_logged.discard(fixture_id)

    due_ids = match_state.expected_ft_due_fixture_ids(now)
    for fixture_id in due_ids:
        if fixture_id in matches_by_id:
            continue
        payload = await api_provider.fetch_fixture(bot.http_session, fixture_id)
        response = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(response, list) or not response:
            continue
        normalized = _normalize_api_football_match(response[0])
        if match_lifecycle.is_terminal(normalized):
            await process_terminal_fixture(bot, normalized, now_utc=now)


def prune_ft_state(now_utc: datetime | None = None) -> list[str]:
    return match_state.prune_match_tracking_state(now_utc or utc_now())
