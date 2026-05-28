# Developer Guide - Marco Van Botten

This guide is for contributors working on the bot codebase.

- User setup/runbook: `README.md` and `OPERATIONS.md`
- Runtime target: Raspberry Pi + systemd
- Local dev runtime: Python 3.12

## Bot Summary

Marco Van Botten is a single-channel Discord assistant for tracked football and tennis. The football side is ESPN-first, with API-Football reserved for fallback mode and event enrichment. The tennis side polls ESPN tennis scoreboards for configured players. The assistant side uses an external LLM endpoint plus tool-assisted search/live-data lookups and generated football memory.

The main engineering goal is reliability under a small Raspberry Pi deployment: one shared HTTP session, bounded polling, bounded enrichment calls, persistent runtime state, and clear logs.

## 1. Local Development

```bash
git clone https://github.com/SpikePhD/football_tracker_bot.git
cd football_tracker_bot
py -3.12 -m venv .venv
# Windows: .\.venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp .env.deploy.example .env.deploy
cp config.example.json config.json
python football_tracker_bot.py
```

## 2. Configuration Model

The project uses three configuration layers:

- `.env` (secrets only)
- `config.json` (non-secret behavior/customization)
- `.env.deploy` (deployment wiring for scripts)

### `.env` (secrets)

Expected sensitive values include:

- `BOT_TOKEN`
- `API_KEY`
- `SECONDARY_API_KEY`
- `CHANNEL_ID`
- `LLM_API_KEY` (if using external LLM)

### `config.json` (non-secrets)

Holds behavior knobs and bot profile such as:

- bot name/identity text
- tracked leagues/slugs
- tracked tennis players
- polling/cache intervals
- post/edit behavior settings
- log export limits/defaults
- LLM non-secret defaults (`model`, `base_url`, `system_prompt`, etc.)

Startup is fail-fast on missing/invalid `config.json`.

### `.env.deploy`

Used by deployment/update scripts for host-specific deploy values:

- `SERVICE_NAME`
- `GIT_BRANCH`

## 3. Architecture (high level)

```text
football_tracker_bot.py
  -> loads cogs
  -> starts scheduler and task loops
  -> manages shared aiohttp session

modules/scheduler.py
  -> daily orchestration
  -> periodic calls into live/FT/tennis loops

modules/live_loop.py
  -> live football polling
  -> event enrichment before dedup/upsert

modules/ft_handler.py
  -> full-time tracking and final result posts
  -> reuses enrichment before FT message formatting

modules/tennis_loop.py
  -> tracked tennis polling and announcements

modules/api_provider.py
  -> single fixture provider entrypoint
  -> ESPN primary + API-Football fallback/enrichment

modules/discord_poster.py
  -> proactive Discord sends and message upserts

modules/storage.py
  -> runtime JSON state in bot_memory/

modules/football_memory.py
  -> generated football memory used by !ask

cogs/
  -> command entrypoints
```

All Discord sends flow through `modules/discord_poster.py`.

## 4. Provider and Enrichment Model

`modules/api_provider.py` is the only place that should orchestrate football provider behavior.

The provider model is:

1. ESPN is primary.
2. After repeated ESPN failures, the bot switches to API-Football fallback.
3. ESPN is retried after the configured retry interval.
4. API-Football event enrichment is only attempted when ESPN score totals exceed ESPN goal-event count.

Enrichment protections:

- Retry delays are configured with `operations.api_provider.enrich_retry_delays_sec`.
- First retry is also bounded by `enrich_grace_sec`.
- Calls are limited by `enrich_max_calls_per_tick`.
- Calls are limited per Italy day by `enrich_daily_call_budget`.
- Live fixture mapping payloads are cached.
- Successful ESPN-to-API-Football fixture mappings are cached for the day.
- Failed mappings are cached temporarily with `enrich_negative_mapping_ttl_sec`.
- Incomplete API-Football event responses cool down via `enrich_incomplete_events_cooldown_sec`.
- Best-known event snapshots prevent ESPN event-data downgrades.

When changing this area, add or update regression tests in `tests/test_regressions.py`. Avoid direct calls to `utils/api_client.py` from cogs or feature modules unless the provider orchestration truly does not apply.

## 5. Key Engineering Rules

- Do not call `channel.send()` or `ctx.send()` directly in cogs/modules.
- Do not create ad-hoc `aiohttp.ClientSession` instances.
- Do not bypass `modules/api_provider.py` for fixture retrieval paths.
- Keep user-facing time handling in Europe/Rome utilities.
- Keep runtime state in `bot_memory/` via `modules/storage.py`.
- Keep `inject_memory/` read-only from runtime logic.
- Keep secrets out of `config.json`.

## 6. Add a Command

1. Create `cogs/<name>.py`
2. Add a `commands.Cog` subclass
3. Use `post_new_message_to_context(...)` for responses
4. Add `async def setup(bot): await bot.add_cog(...)`

Cogs are auto-loaded at startup.

Note: `cogs/update.py` executes `update.sh` via subprocess and uses an in-process lock to avoid concurrent update runs.

## 7. Logging

- Root logger configured in startup
- Module loggers via `logging.getLogger(__name__)`
- App file logging available (for `!log`) via rotating file handler
- Provider logs use `[APIProvider]`
- Enrichment logs use `[Enrich]`
- Keep logs grep-friendly and avoid `print()` in production code

## 8. Validation Before Push

```bash
python -m unittest tests.test_regressions
python -m compileall config.py modules tests
python -c "import json, pathlib; json.loads(pathlib.Path('config.json').read_text(encoding='utf-8-sig'))"
python -c "import json, pathlib; json.loads(pathlib.Path('config.example.json').read_text(encoding='utf-8-sig'))"
```

## 9. Common Pitfalls

- BOM-encoded `config.json` can break startup on strict UTF-8 parsing in some environments.
- API-Football enrichment must remain bounded and sparse to protect daily quota.
- Keep secrets out of `config.json` and repository commits.
- If tests fail locally with missing modules, run them through the project virtualenv.
- Do not add backward-compat shims unless explicitly requested.
- Do not duplicate update logic in Python; `update.sh` is the canonical updater path.
