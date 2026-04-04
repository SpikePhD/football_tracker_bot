# Agent Guide — Football Tracker Bot

This file is for AI coding assistants (Claude, Copilot, Cursor, etc.) working in this repository.
Read it before making changes.

---

## What This Project Is

A Discord bot that polls the [API-Football v3](https://www.api-football.com/) API for live match data and posts score updates, goal events, red cards, and full-time results to a single Discord channel. It runs as a `systemd` service on a Linux home machine.

Primary focus: AC Milan + major Italian and European competitions.

---

## Architecture at a Glance

```
football_tracker_bot.py
    └── on_ready()
            ├── loads all cogs/ dynamically
            ├── starts eleven_am_daily_trigger (tasks.loop)
            └── calls launch_daily_operations_manager()
                    └── schedule_day()                    ← modules/scheduler.py
                            ├── fetch_day_fixtures()      ← utils/api_client.py
                            ├── post_initial_fts()        ← modules/ft_handler.py
                            ├── [sleep until first KO]
                            └── loop every 8 min:
                                    ├── run_live_loop()   ← modules/live_loop.py
                                    └── fetch_and_post_ft() ← modules/ft_handler.py
```

All Discord messages go through `modules/discord_poster.py`. Never call `channel.send()` directly from other modules.

---

## Key Conventions

### Logging
- Every module uses `logger = logging.getLogger(__name__)`.
- The root logger is configured once in `football_tracker_bot.py` with `logging.basicConfig(...)`.
- Do not use `print()` anywhere in production code.

### Discord posting
- `discord_poster.py` is the single point for all Discord sends. Use the appropriate function:
  - `post_live_update(bot, channel_id, content=...)` — live score updates
  - `post_new_general_message(bot, channel_id, content=...)` — FT results, announcements
  - `post_new_message_to_context(ctx, content=...)` — responses to user commands (cogs)
- Never import `discord` and call `channel.send()` directly from `modules/` or `cogs/`.

### HTTP session
- The bot holds a single `aiohttp.ClientSession` at `bot.http_session`.
- It is created in `ensure_http_session()` and cleaned up in `cleanup_sessions()`.
- All API calls receive `bot.http_session` as a parameter — do not create new sessions.

### API client
- All API-Football requests go through `utils/api_client.py`.
- `_make_request()` handles all error cases (HTTP errors, timeouts, API-level errors) and returns `None` on failure — callers must handle `None`.
- League filtering (`TRACKED_LEAGUE_IDS`) is applied inside the API client functions, not in callers.

### Timezone
- All user-facing times are in `Europe/Rome` (Italy timezone).
- Use `italy_now()` and `parse_utc_to_italy()` from `utils/time_utils.py`. Do not use `datetime.now()` or `datetime.utcnow()` directly.

### Config
- Secrets (`BOT_TOKEN`, `API_KEY`, `CHANNEL_ID`) come from `.env` via `python-dotenv`.
- `config.py` raises `RuntimeError` at import time if any required variable is missing — this is intentional.
- Non-secret config (`TRACKED_LEAGUE_IDS`, `AC_MILAN_TEAM_ID`) lives in `config.py` directly.

---

## Module Responsibilities

| File | Responsibility |
|---|---|
| `football_tracker_bot.py` | Bot lifecycle, daily trigger, HTTP session, cog loading |
| `config.py` | All configuration — secrets, static IDs, and `LEAGUE_SLUG_MAP` |
| `modules/scheduler.py` | Daily orchestration: fetch → sleep → poll loop |
| `modules/live_loop.py` | Live polling: deduplicates by `{match_id}_{home}-{away}` score key |
| `modules/ft_handler.py` | Tracks matches for FT check; posts final results (dual ESPN/fallback path) |
| `modules/api_provider.py` | Provider layer: ESPN primary, API-Football fallback, health state, cache |
| `modules/discord_poster.py` | All Discord sends — the only place `channel.send()` is called |
| `modules/power_manager.py` | OS sleep prevention via `powercfg` (Windows) / `systemctl mask` (Linux) |
| `utils/espn_client.py` | ESPN public API client; normalization to common match dict format |
| `utils/api_client.py` | API-Football client; used as fallback by `api_provider` |
| `utils/time_utils.py` | Italy timezone helpers |
| `utils/personality.py` | Greeting message variants |
| `cogs/` | Discord command extensions, loaded dynamically from the directory |

---

## What to Avoid

- **Do not add new modules** without a clear, single responsibility. Prefer extending an existing module.
- **Do not create new `aiohttp.ClientSession` instances.** Always use `bot.http_session`.
- **Do not call `espn_client` or `api_client` directly from `modules/` or `cogs/`.** Go through `api_provider` so fallback logic is always respected.
- **Do not add league filtering in callers.** It belongs inside `api_provider` / `espn_client`.
- **Do not use `datetime.now()` or `datetime.utcnow()` for match timing.** Use `italy_now()`.
- **Do not import `config.py` values outside of `config.py`, `modules/`, and `cogs/`.** Utils should remain stateless.
- **Do not send Discord messages directly from `modules/` or `cogs/`.** Use `discord_poster.py`.
- **Do not add compatibility shims or backwards-compat layers** — this is a single-deployment personal bot.

---

## Cogs

Cogs are loaded dynamically from the `cogs/` directory at startup. Any `.py` file in `cogs/` that is not `__init__.py` will be loaded as a cog extension. Each cog must define an `async def setup(bot)` function.

New commands go in new cog files. Keep one logical group per file.

---

## Adding a New Competition

1. Find the league ID on [api-football.com](https://www.api-football.com/documentation-v3).
2. Add it to `TRACKED_LEAGUE_IDS` in `config.py`.
3. Add a human-readable name to `LEAGUE_NAME_MAP` in `cogs/competitions.py`.

No other changes needed — filtering is centralised in `api_client.py`.

---

## Adding a New Discord Command

1. Create `cogs/your_command.py`.
2. Define a `commands.Cog` subclass with your command(s).
3. Use `post_new_message_to_context(ctx, content=...)` for all replies.
4. Define `async def setup(bot): await bot.add_cog(YourCog(bot))`.
5. The cog is loaded automatically on next startup.

---

## Deployment Context

- Runs on Linux as a `systemd` service named `marco_van_botten`.
- Virtual environment at `.venv/` (path in `auto_update.sh`).
- `auto_update.sh` fetches from `origin/main` and restarts the service on changes.
- The bot file path in `auto_update.sh` is `/home/lucac/football_tracker_bot` — update this if deploying elsewhere.
