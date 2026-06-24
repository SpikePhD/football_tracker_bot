import logging
from datetime import datetime, timezone
from pathlib import Path

import discord

from config import CHANNEL_ID
from modules import match_lifecycle, match_state
from modules.bot_mode import is_silent
from modules.discord_poster import edit_general_message, post_new_general_message
from modules.football_memory import update_match_in_memory
from utils.event_formatter import (
    event_completeness_note,
    format_match_events,
    format_shootout_segments,
    is_shootout_event,
    normalize_api_football_events,
    prune_goal_events_to_score,
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


def _build_ft_message(match_details: dict, *, show_missing_warning: bool = False) -> str:
    match_details, pruned_goal_events = prune_goal_events_to_score(match_details)
    if pruned_goal_events:
        logger.info(
            "Pruned %d surplus goal event(s) before FT message render for fixture %s.",
            pruned_goal_events,
            match_lifecycle.fixture_identity(match_details),
        )
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

    note = event_completeness_note(goals, events, show_warning=show_missing_warning)
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


def _fixture_fully_resolved(fixture_id: str | None, memory_dir: Path | None = None) -> bool:
    if fixture_id is None:
        return False
    current = match_state.get_fixture_state(fixture_id, memory_dir=memory_dir) or {}
    return current.get("ft_announced") is True and current.get("memory_updated") is True


def _fully_resolved_fixture_can_repair_ft_message(
    fixture_id: str | None,
    memory_dir: Path | None = None,
) -> bool:
    if fixture_id is None:
        return False
    current = match_state.get_fixture_state(fixture_id, memory_dir=memory_dir) or {}
    return (
        current.get("ft_announced") is True
        and current.get("memory_updated") is True
        and current.get("ft_message_id") is not None
        and current.get("event_completeness_status") == "exhausted_missing"
    )


def _is_api_football_fixture(match: dict) -> bool:
    return (
        match.get("provider") == "api_football"
        or match.get("source") == "api_football"
        or match.get("fixture", {}).get("provider") == "api_football"
    )


def _api_football_fixture_is_known(match: dict, memory_dir: Path | None = None) -> bool:
    provider_fixture_id = match.get("provider_fixture_id") or match.get("fixture", {}).get("id")
    if provider_fixture_id is None:
        return False
    if match_state.find_canonical_fixture_id("api_football", provider_fixture_id, memory_dir=memory_dir):
        return True
    state = match_state.get_fixture_state(provider_fixture_id, memory_dir=memory_dir)
    return state is not None and state.get("last_status") in match_lifecycle.LIVE_STATUSES


def _has_incomplete_goal_events(match: dict) -> bool:
    goals = match.get("goals", {}) or {}
    try:
        total_goals = int(goals.get("home", 0) or 0) + int(goals.get("away", 0) or 0)
    except (TypeError, ValueError):
        return False
    goal_events = sum(
        1
        for event in match.get("events", [])
        if event.get("type") == "Goal" and not is_shootout_event(event)
    )
    return goal_events < total_goals


def _message_id_or_none(message) -> int | str | None:
    message_id = getattr(message, "id", None)
    if isinstance(message_id, (int, str)):
        return message_id
    return None


async def _post_ft_from_data(
    bot: discord.Client,
    match_details: dict,
    *,
    show_missing_warning: bool = False,
):
    ft_message = _build_ft_message(match_details, show_missing_warning=show_missing_warning)
    safe_message = ft_message.encode("ascii", "backslashreplace").decode("ascii")
    logger.info("Posting FT result: %s", safe_message)
    return await post_new_general_message(bot, CHANNEL_ID, content=ft_message)


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
    fixture_id = match_lifecycle.fixture_identity(match_details)
    if match_lifecycle.is_ft(match_details) and _fixture_fully_resolved(fixture_id, memory_dir=memory_dir):
        if not _fully_resolved_fixture_can_repair_ft_message(fixture_id, memory_dir=memory_dir):
            logger.info("Skipping terminal fixture %s because it is already fully resolved.", fixture_id)
            return
        logger.info(
            "Processing fully resolved terminal fixture %s to check for late FT message enrichment.",
            fixture_id,
        )
    if (
        match_lifecycle.is_ft(match_details)
        and _is_api_football_fixture(match_details)
        and not match_details.get("canonical_fixture_id")
        and not _api_football_fixture_is_known(match_details, memory_dir=memory_dir)
    ):
        logger.info(
            "Skipping unmapped API-Football terminal fixture %s; it was not previously tracked.",
            match_details.get("fixture", {}).get("id"),
        )
        return

    enriched = await api_provider.enrich_fixture_events(bot.http_session, match_details)
    enriched, pruned_goal_events = prune_goal_events_to_score(enriched)
    if pruned_goal_events:
        logger.info(
            "Pruned %d surplus goal event(s) before terminal processing for fixture %s.",
            pruned_goal_events,
            match_lifecycle.fixture_identity(enriched),
        )
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
    source = "api_football" if _is_api_football_fixture(enriched) else "espn"
    fixture = match_state.upsert_fixture_from_match(enriched, now_utc, source=source, memory_dir=memory_dir)
    fixture_id = fixture["fixture_id"]
    current = match_state.get_fixture_state(fixture_id, memory_dir=memory_dir) or {}
    memory_result = None

    event_status = None
    show_missing_warning = False
    if match_lifecycle.is_ft(enriched):
        event_status = api_provider.event_completeness_status(enriched, memory_dir=memory_dir)
        show_missing_warning = event_status["status"] == api_provider.EVENTS_EXHAUSTED_MISSING
        if event_status.get("score_key"):
            match_state.update_event_completeness(
                fixture_id,
                event_status.get("score_key"),
                event_status["status"],
                event_status.get("missing_goals", 0),
                now_utc=now_utc,
                memory_dir=memory_dir,
            )

    if match_lifecycle.is_ft(enriched) and not current.get("memory_updated"):
        if event_status and event_status["status"] == api_provider.EVENTS_PENDING_ENRICHMENT:
            logger.info(
                "Deferring football memory update for FT fixture %s while event enrichment is pending.",
                fixture_id,
            )
            memory_result = {"updated": False, "reason": "event_enrichment_pending"}
        elif _has_required_memory_keys(enriched):
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
        sent = await _post_ft_from_data(
            bot,
            enriched,
            show_missing_warning=show_missing_warning,
        )
        ft_posted = sent is not None
        if sent is not None:
            ft_content = _build_ft_message(enriched, show_missing_warning=show_missing_warning)
            match_state.update_ft_message(
                fixture_id,
                _message_id_or_none(sent),
                ft_content,
                memory_dir=memory_dir,
            )
            match_state.mark_ft_announced(fixture_id, memory_dir=memory_dir)
        else:
            logger.warning("FT announcement skipped or failed for fixture %s.", fixture_id)
    elif match_lifecycle.is_ft(enriched):
        ft_posted = "already_announced"
        ft_content = _build_ft_message(enriched, show_missing_warning=show_missing_warning)
        message_id = current.get("ft_message_id")
        if message_id is not None and current.get("ft_message_content") != ft_content:
            edited = await edit_general_message(
                bot=bot,
                channel_id=CHANNEL_ID,
                message_id=message_id,
                content=ft_content,
            )
            if edited is not None:
                match_state.update_ft_message(
                    fixture_id,
                    _message_id_or_none(edited) or message_id,
                    ft_content,
                    memory_dir=memory_dir,
                )
            else:
                logger.warning(
                    "Could not edit stored FT message %s for fixture %s; not reposting.",
                    message_id,
                    fixture_id,
                )
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
            if (
                match_lifecycle.is_ft(match)
                and _fixture_fully_resolved(fixture_id)
                and not _fully_resolved_fixture_can_repair_ft_message(fixture_id)
            ):
                _past_expected_live_logged.discard(fixture_id)
                continue
            await process_terminal_fixture(bot, match, now_utc=now)
            _past_expected_live_logged.discard(fixture_id)

    due_ids = match_state.expected_ft_due_fixture_ids(now)
    for fixture_id in due_ids:
        if fixture_id in matches_by_id:
            continue
        state = match_state.get_fixture_state(fixture_id) or {}
        provider_ids = state.get("provider_ids", {}) if isinstance(state, dict) else {}
        fetch_fixture_id = provider_ids.get("api_football") if isinstance(provider_ids, dict) else None
        if fetch_fixture_id is None:
            if provider_ids.get("espn") == str(fixture_id) or state.get("provider") == "espn":
                logger.info(
                    "Skipping direct FT fetch for ESPN fixture %s because no API-Football alias is known.",
                    fixture_id,
                )
                continue
            fetch_fixture_id = fixture_id
        payload = await api_provider.fetch_fixture(bot.http_session, fetch_fixture_id)
        response = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(response, list) or not response:
            continue
        normalized = _normalize_api_football_match(response[0])
        normalized["provider"] = "api_football"
        normalized["provider_fixture_id"] = str(fetch_fixture_id)
        normalized["canonical_fixture_id"] = str(fixture_id)
        normalized["provider_ids"] = {
            **(provider_ids if isinstance(provider_ids, dict) else {}),
            "api_football": str(fetch_fixture_id),
        }
        if provider_ids.get("espn"):
            normalized["provider_ids"]["espn"] = str(provider_ids["espn"])
        if match_lifecycle.is_terminal(normalized):
            await process_terminal_fixture(bot, normalized, now_utc=now)


def prune_ft_state(now_utc: datetime | None = None) -> list[str]:
    return match_state.prune_match_tracking_state(now_utc or utc_now())
