# Agent Guide — Football Tracker Bot (Marco Van Botten)

This file is for AI coding assistants (Claude, Copilot, Cursor, etc.) working in this repository.
Read it before making changes. It describes the architecture, conventions, and what to avoid.

---

## What This Project Is

A Discord bot that tracks live football matches and posts score updates, goal events, red cards,
and full-time results to a single Discord channel. It runs as a `systemd` service on a Raspberry Pi.

Primary focus: AC Milan + major Italian and European competitions.

**Data sources:**
- **Primary:** ESPN public API (`site.api.espn.com`) — no auth, no rate limit, polled every 60s
- **Fallback:** API-Football v3 — used automatically if ESPN fails (polled every 480s)

---

## Architecture

```
football_tracker_bot.py
    └── on_ready()
            ├── loads all cogs/ dynamically
            ├── posts startup message (greeting + grouped fixture list)  [verbose mode only]
            ├── starts eleven_am_daily_trigger (tasks.loop @ 11:00)
            └── calls launch_daily_operations_manager()
                    └── schedule_day()                       ← modules/scheduler.py
                            ├── api_provider.fetch_day()     ← modules/api_provider.py
                            │       ├── espn_client (primary)
                            │       └── api_client  (fallback)
                            └── loop every 60s (ESPN) / 480s (fallback):
                                    ├── run_live_loop()      ← modules/live_loop.py
                                    ├── fetch_and_post_ft()  ← modules/ft_handler.py
                                    └── run_tennis_loop()    ← modules/tennis_loop.py

All Discord sends → modules/discord_poster.py
Bot state reads/writes → modules/storage.py → bot_memory/state.json
```

---

## Module Responsibilities

| File | Responsibility |
|---|---|
| `football_tracker_bot.py` | Bot lifecycle, task loops (morning + 11am), HTTP session, cog loading |
| `config.py` | All config: secrets, `TRACKED_LEAGUE_IDS`, `LEAGUE_NAME_MAP`, `LEAGUE_SLUG_MAP`, `build_league_slugs()` |
| `modules/scheduler.py` | Daily orchestration: fetch fixtures → sleep until KO → poll loop until midnight |
| `modules/live_loop.py` | Live polling: builds score updates, deduplicates by `{match_id}_{score}_{event_count}` |
| `modules/ft_handler.py` | Tracks live matches for FT check; posts final results (dual ESPN/fallback path) |
| `modules/api_provider.py` | Provider layer: ESPN primary + API-Football fallback, health state, 55s cache |
| `modules/discord_poster.py` | All Discord sends — the only place `channel.send()` is called |
| `modules/bot_mode.py` | Silent/verbose flag — reads and writes `bot_memory/state.json` via `storage.py` |
| `modules/storage.py` | JSON read/write wrapper for `bot_memory/` files |
| `modules/power_manager.py` | OS sleep prevention |
| `utils/espn_client.py` | ESPN API client; normalises response to common match dict format |
| `utils/api_client.py` | API-Football client; used as fallback by `api_provider` |
| `utils/time_utils.py` | Italy timezone helpers (`italy_now`, `parse_utc_to_italy`) |
| `utils/personality.py` | Greeting message variants |
| `cogs/matches.py` | `!matches` command; also exports `build_matches_message()` used by startup and morning broadcasts |
| `cogs/goodmorning.py` | `!goodmorning` / `!gm` command and configurable Europe/Rome morning broadcast |
| `cogs/competitions.py` | `!competitions` command |
| `cogs/next_command.py` | `!next <team>` command — any team's next fixture via ESPN search |
| `cogs/hello.py` | `!hi` / `!hello` command |
| `cogs/changelog.py` | `!changelog` command |
| `cogs/version.py` | `!version` command |
| `cogs/api_status.py` | `!api` command — shows active provider, interval, failure count |
| `cogs/mode.py` | `!verbose` / `!normal` / `!silent` / `!mode` commands |
| `cogs/commands_list.py` | `!commands` command — dynamically lists all registered commands |
| `utils/event_formatter.py` | `format_match_events()` — shared event formatting (goals, red cards) |

---

## Folder Structure — Memory

```
bot_memory/       # Pi-owned runtime state. Gitignored. Never overwritten by git pull.
  state.json      # {"silent": false} — persists across restarts

inject_memory/    # GitHub-controlled reference data. Updated on every git pull.
  (milan_calendar.json, etc. added as needed)
```

`update.sh` creates missing `bot_memory/` files with safe defaults on each deploy, but never
overwrites existing files. Add new default files to the initialisation block in `update.sh`.

---

## Key Conventions

### Logging
- Every module uses `logger = logging.getLogger(__name__)`.
- Root logger is configured once in `football_tracker_bot.py` with `logging.basicConfig(...)`.
- Never use `print()` in production code.

### Discord posting
`discord_poster.py` is the single point for all Discord sends:
- `post_live_update(bot, channel_id, content=...)` — live score updates from `live_loop`
- `post_new_general_message(bot, channel_id, content=...)` — FT results, announcements
- `post_new_message_to_context(ctx, content=...)` — responses to user commands (cogs)

Never import `discord` and call `channel.send()` directly from `modules/`, `cogs/`,
or the main bot file. Use `modules/discord_poster.py` for lifecycle, live, FT,
announcement, and command messages.

### HTTP session
- The bot holds a single `aiohttp.ClientSession` at `bot.http_session`.
- Created in `ensure_http_session()`, cleaned up in `cleanup_sessions()`.
- All API calls receive `bot.http_session` as a parameter — never create new sessions.

### API provider
- Always go through `modules/api_provider.py` for fixture data — never call `espn_client` or
  `api_client` directly from `modules/` or `cogs/`. This ensures fallback logic is always active.
- `api_provider` maintains a 55-second cache. Multiple callers in the same cycle hit the cache,
  not the network.

### Timezone
- All user-facing times are in `Europe/Rome` (Italy timezone).
- Use `italy_now()` and `parse_utc_to_italy()` from `utils/time_utils.py`.
- Never use `datetime.now()` or `datetime.utcnow()` directly.

### Config and league names
- `LEAGUE_NAME_MAP` lives in `config.py` — it is the single source of truth for league names.
  Import it from there in any cog or module that needs it.
- `TRACKED_LEAGUE_IDS` and `LEAGUE_SLUG_MAP` also live in `config.py`.

### Deduplication — live updates
`live_loop.py` uses this key to decide whether a score state has already been posted:
```
{match_id}_{home_goals}-{away_goals}_{event_count}
```
The event count is included because ESPN sometimes reports score changes before populating scorer
details. A follow-up post is triggered when new events appear for the same scoreline.

### Bot memory
- `modules/storage.py` provides `load(filename, default)` and `save(filename, data)` for JSON files
  in `bot_memory/`.
- Use `storage.load` / `storage.save` for any new runtime state that should survive restarts.
- `inject_memory/` files are read-only from the bot's perspective — the bot reads them, we write them.

### Broadcast mode
- `modules/bot_mode.py` exposes `get_mode()`, `set_mode(mode)`, `is_verbose()`, `is_silent()`.
- Valid modes: `"verbose"` (all broadcasts), `"normal"` (live + FT only), `"silent"` (commands only).
- Check `is_verbose()` before startup/morning broadcasts; check `is_silent()` before live/FT posts.
- Command responses are **always** sent regardless of mode.

---

## What to Avoid

- **Do not call `espn_client` or `api_client` directly** from `modules/` or `cogs/`. Go through `api_provider`.
- **Do not create new `aiohttp.ClientSession` instances.** Always use `bot.http_session`.
- **Do not add league filtering in callers.** It belongs inside `api_provider`.
- **Do not use `datetime.now()` or `datetime.utcnow()` for match timing.** Use `italy_now()`.
- **Do not define `LEAGUE_NAME_MAP` in cogs.** Import it from `config.py`.
- **Do not add new modules** without a clear, single responsibility.
- **Do not add backwards-compat shims** — this is a single-deployment personal bot.
- **Do not write to `inject_memory/` from bot code** — it is GitHub-controlled, read-only for the bot.

---

## Adding a New Competition

1. Find the API-Football league ID and ESPN slug for the competition.
2. Add the ID to `TRACKED_LEAGUE_IDS` in `config.py`.
3. Add `id: "Human Name"` to `LEAGUE_NAME_MAP` in `config.py`.
4. Add `id: "espn.slug"` to `LEAGUE_SLUG_MAP` in `config.py`.

No other changes needed — filtering and name resolution are centralised.

---

## Adding a New Discord Command

1. Create `cogs/your_command.py`.
2. Define a `commands.Cog` subclass with your command(s).
3. Use `post_new_message_to_context(ctx, content=...)` for all replies.
4. Define `async def setup(bot): await bot.add_cog(YourCog(bot))`.
5. The cog is loaded automatically on next startup — no registration needed.

---

## Adding New Persistent State

1. Add a default entry to the `bot_memory/state.json` initialisation block in `update.sh`.
2. Use `modules/storage.load("state.json", default)` and `storage.save("state.json", data)`
   in whichever module manages the new state.

---

## Adding Inject Memory Data

1. Create the file in `inject_memory/` (e.g. `inject_memory/milan_calendar.json`).
2. Commit and push — it will be deployed to the Pi on the next `bash update.sh`.
3. Read it in bot code via `pathlib` or a direct `json.load()` — no write access needed.

---

## Deployment Context

- Runs on a Raspberry Pi as a `systemd` service named `marco_van_botten`.
- Virtual environment at `.venv/` inside the project directory.
- **Update from Windows:** double-click `update_bot.bat`.
- **Update from Pi:** `cd ~/football_tracker_bot && bash update.sh`.
- `update.sh` pulls code, initialises missing `bot_memory/` files, restarts the service.
- `auto_update.sh` is a separate cron-based unattended updater.
