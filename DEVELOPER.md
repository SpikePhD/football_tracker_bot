# Developer Guide - Marco Van Botten

This guide is for contributors working on the bot codebase.

- User setup/runbook: `README.md` and `OPERATIONS.md`
- Runtime target: Raspberry Pi + systemd
- Local dev runtime: Python 3.12

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

modules/api_provider.py
  -> single fixture provider entrypoint
  -> ESPN primary + API-Football fallback/enrichment
```

All Discord sends flow through `modules/discord_poster.py`.

## 4. Key Engineering Rules

- Do not call `channel.send()` or `ctx.send()` directly in cogs/modules.
- Do not create ad-hoc `aiohttp.ClientSession` instances.
- Do not bypass `modules/api_provider.py` for fixture retrieval paths.
- Keep user-facing time handling in Europe/Rome utilities.
- Keep runtime state in `bot_memory/` via `modules/storage.py`.

## 5. Add a Command

1. Create `cogs/<name>.py`
2. Add a `commands.Cog` subclass
3. Use `post_new_message_to_context(...)` for responses
4. Add `async def setup(bot): await bot.add_cog(...)`

Cogs are auto-loaded at startup.

Note: `cogs/update.py` executes `update.sh` via subprocess and uses an in-process lock to avoid concurrent update runs.

## 6. Logging

- Root logger configured in startup
- Module loggers via `logging.getLogger(__name__)`
- App file logging available (for `!log`) via rotating file handler

## 7. Validation Before Push

```bash
python -m py_compile football_tracker_bot.py config.py cogs\*.py modules\*.py utils\*.py
python -m json.tool config.json >nul
python -m json.tool config.example.json >nul
```

## 8. Common Pitfalls

- BOM-encoded `config.json` can break startup on strict UTF-8 parsing in some environments.
- API-Football enrichment must remain bounded and sparse to protect daily quota.
- Keep secrets out of `config.json` and repository commits.
