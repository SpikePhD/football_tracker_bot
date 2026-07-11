# Developer Guide - Marco Van Botten

This guide is for contributors changing bot code. Use `README.md` for setup and `OPERATIONS.md` for Raspberry Pi runbooks.

## Bot Summary

Marco Van Botten is a single-channel Discord assistant for tracked football and tennis. Football is ESPN-first, with API-Football used only for fallback mode and sparse event enrichment. Tennis uses ESPN tennis scoreboards for configured players. The assistant side uses an external LLM endpoint plus tool-assisted search/live-data lookups and generated football memory.

The engineering priority is reliability on a small Raspberry Pi deployment: one shared HTTP session, bounded polling, bounded enrichment calls, persistent runtime state, and grep-friendly logs.

## Architecture

```text
football_tracker_bot.py
  -> loads cogs
  -> starts scheduler/task loops
  -> owns the shared aiohttp session

modules/scheduler.py
  -> sleep/awake orchestration plus local daily routines

modules/football_cycle.py
  -> one rolling provider snapshot shared by scheduler, live, and FT consumers

modules/live_loop.py
  -> live football polling, enrichment, dedup, and upserts

modules/ft_handler.py
  -> full-time tracking and final result posts

modules/match_lifecycle.py
  -> UTC-first football lifecycle decisions

modules/match_state.py
  -> atomic persisted football fixture state in bot_memory/match_state.json

modules/tennis_loop.py
  -> tracked tennis live/start-watch and FT processing with versioned per-match state

modules/api_provider.py
  -> ESPN primary provider plus API-Football fallback/enrichment policy

modules/discord_poster.py
  -> command replies, proactive posts, and live-message upserts

modules/storage.py
  -> runtime JSON state in bot_memory/

modules/football_memory.py
  -> generated football memory used by !ask

cogs/
  -> small command entrypoints
```

## Engineering Rules

- Route command replies through `post_new_message_to_context(...)`.
- Route proactive posts through `modules/discord_poster.py` helpers.
- Do not create ad-hoc `aiohttp.ClientSession` instances.
- Do not bypass `modules/api_provider.py` for fixture data access paths.
- Keep football lifecycle decisions UTC-first and canonical-fixture-ID-first.
- Use the configured timezone only for display, logs, grouping, and scheduled human-facing routines.
- Keep football lifecycle polling on `match_lifecycle.provider_window(...)`; `football_display_lookup_window_hours` is for public snapshots and upcoming displays only.
- Keep football and tennis scheduler wake decisions in `modules/scheduler.py`; loops should process work, not decide long idle sleeps.
- Build one `FootballCycleSnapshot` per football scheduler check and pass it through decision, live, and FT paths; do not refetch the rolling window inside an awake cycle.
- Keep public football display snapshots on the enrichment path before formatting, so `!matches` cannot downgrade learned event details.
- Keep daily public football snapshots scoped to configured-local-day kickoffs plus earlier fixtures that are still live; do not display earlier terminal fixtures merely because lifecycle retention still includes them.
- Key football live-state, FT-state, and memory-state by `match_lifecycle.fixture_identity(...)`, not raw provider `fixture.id`.
- Keep runtime state in `bot_memory/` via `modules/storage.py`; its writes are atomic and failures propagate to callers.
- Keep `inject_memory/` read-only from runtime code.
- Keep secrets in `.env` only. Committed `config.json` is defaults; host/UI overrides belong in Git-ignored `config.local.json`.
- Apply command authorization through `modules/admin.py`; do not add one-off owner or permission checks.
- Never log raw Discord command content or full secret values.
- Use module loggers with `logging.getLogger(__name__)`.
- Avoid `print()` in production code.
- Do not duplicate update logic in Python; `update.sh` is the canonical updater.
- Do not add backward-compat shims unless explicitly requested.

## Provider And Enrichment Model

`modules/api_provider.py` is the only place that should orchestrate football provider behavior.

Provider flow:

1. ESPN is primary.
2. Repeated ESPN failures switch the bot to API-Football fallback.
3. ESPN is retried after the configured retry interval.
4. API-Football event enrichment is attempted only when ESPN score totals exceed ESPN goal-event count.

Provider identity rules:

- ESPN IDs are the preferred canonical fixture IDs.
- API-Football IDs must be stored as aliases in `match_state.provider_ids` when they map to an ESPN fixture.
- API-Football fallback/date/live payloads should be annotated with `provider`, `provider_fixture_id`, and `canonical_fixture_id` when mapping is known.
- Dedupe provider results by `match_lifecycle.fixture_identity(...)`, not raw provider ID.
- Do not post first-time FT/memory work from an unmapped API-Football terminal fixture unless it was already tracked live under that API ID.

Enrichment protections:

- configured retry delays and grace period
- per-tick call cap
- per configured-local-day enrichment call budget
- live fixture payload cache
- successful ESPN-to-API-Football fixture mapping cache and persisted provider aliases
- temporary failed-mapping cache
- incomplete API-Football event cooldown
- best-known event snapshots to prevent ESPN event-data downgrades

ESPN request-volume protections:

- cold cache and schedule/display discovery refresh every tracked league
- current and future provider dates receive full discovery at most every 30 minutes
- past provider dates receive full discovery at most every 6 hours
- between discovery refreshes, only leagues containing live, near-kickoff, unresolved FT, or repairable exhausted-event fixtures are refreshed at the normal scoreboard TTL
- provider health exposes daily full-discovery and active-refresh league-request counters

The live loop, FT handler, and public `!matches` snapshot should all reuse `api_provider.enrich_fixture_events(...)` or `api_provider.enrich_fixtures(...)` before formatting football events. This keeps scorer details consistent across proactive live posts, final posts, startup snapshots, and command output.

Event completeness is an explicit persisted lifecycle separate from fixture lifecycle. `complete` means goal events cover the score; `pending_enrichment` means a missing-event gap exists but retry/fallback work may still improve it; `exhausted_missing` means the warning is allowed to be shown for that fixture/score key. Formatters should show `⚠️ ... missing from event data` only when callers explicitly pass the exhausted state through.

FT posts are exactly-once by fixture ID, but their stored Discord message can be edited later if enrichment improves the event list or changes an exhausted warning. Do not repost deleted/uneditable FT messages, and do not recount football memory when better events arrive after `memory_updated=true`. Memory updates should stay deferred while event completeness is pending, then run once when data is complete or exhausted.

## Scheduler Model

`modules/scheduler.py` owns long idle sleeps for both sports.

Football:

- `_football_poll_needed(...)` wakes for FT-due IDs, lifecycle-window fixtures, or fallback live endpoint visibility.
- Each scheduler check builds one `modules.football_cycle.FootballCycleSnapshot`. Its relevant fixtures and derived live fixtures are reused for the wake decision and, when awake, by live updates and FT handling.
- When awake, `run_football_cycle(...)` consumes that snapshot, runs live updates, FT handling, and live-state pruning without repeating the rolling-window fetch.
- When asleep, `_plan_sleep_until_next_fixture(...)` refreshes future schedule at most every 6 hours or wakes at `football_prematch_window_hours` before the next kickoff.

Tennis:

- `_tennis_poll_needed(...)` wakes for live matches, unannounced FT matches inside `tennis_finished_retention_hours`, or `NS` matches inside the configured start-watch window.
- When awake, `tennis_loop.run_tennis_loop(...)` handles live/FT posts/upserts and dedupe. It does not send standalone upcoming tennis posts; upcoming matches are shown by snapshots and tennis commands.
- Tennis lifecycle state is rolling across local midnight. `tennis_state.json` version 2 stores one record per canonical match, including the live Discord message ID, so a restart edits the existing live post and retains FT dedupe. Do not reintroduce daily ID clearing or loose top-level ID lists.
- Tennis FT dedupe must only be persisted after Discord confirms the message send. Retirement and walkover finals may be complete without a conventionally complete set list when ESPN supplies a winner and terminal reason.
- Load tennis state before scheduler decisions and prune only expired terminal records; never prune live or future records.
- When asleep, `_plan_tennis_sleep_until_next_match(...)` refreshes future schedule at most every 6 hours or wakes at `tennis_pre_announce_hours` before the next start. If that wake is already due but no work is needed, it schedules the next normal tennis poll instead of returning an immediate one-second loop.

Do not reintroduce minute-by-minute provider polling while a sport is idle. The main loop may still wake for lightweight local daily routines.

When changing this area, add or update focused regression tests under `tests/`.

## Extension Notes

Add a command:

1. Create `cogs/<name>.py`.
2. Add a `commands.Cog` subclass.
3. Use `post_new_message_to_context(...)` for responses.
4. Add `async def setup(bot): await bot.add_cog(...)`.

Add a competition:

1. Update tracked IDs/slugs in `config.json` and `config.example.json`.
2. Update validation and field metadata in `modules/configuration.py` if the schema changes.
3. Keep naming centralized; avoid per-cog constants.

Configuration service:

1. `modules.configuration.load_effective_config()` is the validated source of truth.
2. Local dictionaries deep-merge; arrays/scalars replace defaults; unknown fields are rejected.
3. Use `write_local_overrides(...)` and secret helpers for future UI writes. They validate before atomic replacement and never return full secrets.
4. `config.py` remains the import-compatible constants facade. Runtime hot reload is intentionally unsupported.

Dashboard architecture:

1. `dashboard.py` runs independently from the Discord bot and serves local assets from `dashboard_static/`.
2. `modules/dashboard_service.py` owns authenticated HTTP routes; every mutation requires CSRF and is audited.
3. Dashboard passwords and sessions live in `modules/dashboard_auth.py`; never merge dashboard accounts with Discord owners.
4. Configuration writes must continue through `modules.configuration.save_complete_config(...)` with revision checking.
5. Secrets use the existing masked status and individual atomic replacement helpers only.
6. Process actions go through the exact systemd adapter in `modules/dashboard_process.py`; do not accept service or shell commands from requests.

Add runtime state:

1. Use `modules/storage.py`.
2. Store it under `bot_memory/`.
3. Ensure `install.sh` and `update.sh` create safe defaults without overwriting existing state.

Use `modules.storage.save(...)` or `save_json_path(...)` for JSON persistence. Both write a same-directory temporary file, flush it, and atomically replace the target; persistence errors must remain visible to the caller.

Football fixture lifecycle state is centralized in `modules/match_state.py`. Do not add new daily football state files or local-midnight clears. Use canonical fixture IDs, provider aliases, UTC kickoff times, provider status, explicit retention windows, and `match_state.json` flags such as `ft_announced` and `memory_updated`.

When adding a provider path, ensure it either produces the canonical ESPN fixture ID or records a provider alias through `match_state.link_provider_fixture_id(...)`. Merging duplicate provider records must preserve true dedupe flags, `live_message_id`, score/status timestamps, and provider IDs.

## Validation Before Push

```bash
python -m unittest discover -s tests -p "test_*.py"
python -m compileall config.py modules utils cogs tests scripts football_tracker_bot.py
python -c "import json, pathlib; json.loads(pathlib.Path('config.json').read_text(encoding='utf-8-sig'))"
python -c "import json, pathlib; json.loads(pathlib.Path('config.example.json').read_text(encoding='utf-8-sig'))"
```

If tests fail because local dependencies are missing, run them through the project virtualenv.

## Deferred Agent-Light Refactors

Future medium-risk cleanup can split `cogs/ask.py` and `modules/api_provider.py` into smaller focused files. Do that only as a deliberate refactor with full regression coverage; this repo currently keeps those behavior-heavy modules intact.
