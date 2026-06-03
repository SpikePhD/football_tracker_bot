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
  -> long-running orchestration plus local daily routines

modules/live_loop.py
  -> live football polling, enrichment, dedup, and upserts

modules/ft_handler.py
  -> full-time tracking and final result posts

modules/match_lifecycle.py
  -> UTC-first football lifecycle decisions

modules/match_state.py
  -> atomic persisted football fixture state in bot_memory/match_state.json

modules/tennis_loop.py
  -> tracked tennis polling and announcements

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
- Keep football lifecycle decisions UTC-first and fixture-ID-first.
- Use the configured timezone only for display, logs, grouping, and scheduled human-facing routines.
- Keep runtime state in `bot_memory/` via `modules/storage.py`.
- Keep `inject_memory/` read-only from runtime code.
- Keep secrets out of `config.json`.
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

Enrichment protections:

- configured retry delays and grace period
- per-tick call cap
- per configured-local-day enrichment call budget
- live fixture payload cache
- successful ESPN-to-API-Football fixture mapping cache
- temporary failed-mapping cache
- incomplete API-Football event cooldown
- best-known event snapshots to prevent ESPN event-data downgrades

When changing this area, add or update focused regression tests under `tests/`.

## Extension Notes

Add a command:

1. Create `cogs/<name>.py`.
2. Add a `commands.Cog` subclass.
3. Use `post_new_message_to_context(...)` for responses.
4. Add `async def setup(bot): await bot.add_cog(...)`.

Add a competition:

1. Update tracked IDs/slugs in `config.json` and `config.example.json`.
2. Update loader validation in `config.py` only if the schema changes.
3. Keep naming centralized; avoid per-cog constants.

Add runtime state:

1. Use `modules/storage.py`.
2. Store it under `bot_memory/`.
3. Ensure `install.sh` and `update.sh` create safe defaults without overwriting existing state.

Football fixture lifecycle state is centralized in `modules/match_state.py`. Do not add new daily football state files or local-midnight clears. Use fixture IDs, UTC kickoff times, provider status, explicit retention windows, and `match_state.json` flags such as `ft_announced` and `memory_updated`.

## Validation Before Push

```bash
python -m unittest discover -s tests -p "test_*.py"
python -m compileall config.py modules utils cogs tests football_tracker_bot.py
python -c "import json, pathlib; json.loads(pathlib.Path('config.json').read_text(encoding='utf-8-sig'))"
python -c "import json, pathlib; json.loads(pathlib.Path('config.example.json').read_text(encoding='utf-8-sig'))"
```

If tests fail because local dependencies are missing, run them through the project virtualenv.

## Deferred Agent-Light Refactors

Future medium-risk cleanup can split `cogs/ask.py` and `modules/api_provider.py` into smaller focused files. Do that only as a deliberate refactor with full regression coverage; this repo currently keeps those behavior-heavy modules intact.
