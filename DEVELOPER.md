# Developer Guide — Marco Van Botten

This guide is for developers working on the bot's source code. For first-time deployment see `README.md`. For day-to-day operations see `OPERATIONS.md`.

---

## Table of Contents

1. [Local Development Setup](#1-local-development-setup)
2. [Architecture Overview](#2-architecture-overview)
3. [Module Reference](#3-module-reference)
4. [Key Data Flows](#4-key-data-flows)
5. [API Layer](#5-api-layer)
6. [Extending the Bot](#6-extending-the-bot)
7. [Conventions](#7-conventions)

---

## 1. Local Development Setup

### Prerequisites

- Python 3.10 or higher
- Git
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- An API-Football key ([dashboard.api-football.com](https://dashboard.api-football.com))
- A Discord channel ID where the bot can post

### Steps

```bash
# Clone the repository
git clone https://github.com/SpikePhD/football_tracker_bot ~/football_tracker_bot
cd ~/football_tracker_bot

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env and fill in BOT_TOKEN, API_KEY, CHANNEL_ID

# Run the bot
python football_tracker_bot.py
```

> **Note:** The bot posts to a real Discord channel immediately on startup (if in verbose mode). Use a dedicated test channel and set `CHANNEL_ID` to it.

### Silencing the bot during development

Set the bot to silent mode once it's running so it doesn't spam your test channel while you work:

```
!silent
```

Or edit `bot_memory/state.json` before starting:

```json
{"mode": "silent"}
```

---

## 2. Architecture Overview

The bot is structured in four layers:

```
┌─────────────────────────────────────────────────────────────┐
│  football_tracker_bot.py  — entry point, event handlers,    │
│                             timed background tasks           │
├─────────────────────────────────────────────────────────────┤
│  cogs/         — Discord commands (!matches, !next, etc.)    │
├─────────────────────────────────────────────────────────────┤
│  modules/      — core logic: scheduler, live loop, FT,      │
│                  API provider, Discord poster, mode, storage │
├─────────────────────────────────────────────────────────────┤
│  utils/        — stateless helpers: ESPN client,            │
│                  API-Football client, formatters, time       │
└─────────────────────────────────────────────────────────────┘
```

### Startup Sequence

```
on_ready()
  ├── ensure_http_session()          shared aiohttp.ClientSession on bot.http_session
  ├── setup_power_management()       disables OS sleep
  ├── channel.send(startup message)  only in verbose mode
  ├── load all cogs from cogs/
  └── launch_daily_operations_manager()
        └── schedule_day()           the main daily loop (runs as background Task)

Background loops (always running):
  ├── six_thirty_morning_trigger()   06:30 Italy — morning broadcast
  ├── midnight_trigger()             00:01 Italy — restart schedule_day for new day
  └── eleven_am_daily_trigger()      11:00 Italy — secondary daily restart
```

### Daily Polling Loop

```
schedule_day()
  ├── Clear per-day state (already_posted, tracked_matches, _already_announced_ft)
  ├── Fetch today's fixtures
  ├── Seed dedup sets (so restart doesn't re-post existing data)
  ├── Sleep until first kickoff
  └── Poll loop (runs until Italy midnight):
        ├── run_live_loop()          post live score changes
        └── fetch_and_post_ft()     post full-time results
        (wait 60s ESPN / 480s fallback)
```

---

## 3. Module Reference

### Entry Point

| File | Purpose |
|------|---------|
| `football_tracker_bot.py` | Bot instance, event handlers (`on_ready`, `on_resumed`, `on_disconnect`), timed task loops, entry point `main()` |

### config.py

Central configuration. **All league/competition data lives here.** Contains:

- `TRACKED_LEAGUE_IDS` — list of API-Football league IDs the bot monitors
- `LEAGUE_NAME_MAP` — `{league_id: "Human Name"}` used in all output
- `LEAGUE_SLUG_MAP` — `{league_id: "espn-slug"}` for ESPN scoreboard URLs
- `DOMESTIC_SLUG_GROUPS`, `INTERNATIONAL_SLUGS` — used by `!next` to find team fixtures
- `build_league_slugs(primary_slug)` — returns all relevant slugs for a team's competitions

Environment variables (`BOT_TOKEN`, `API_KEY`, `CHANNEL_ID`) are loaded here via `python-dotenv`.

### modules/

| Module | Responsibility |
|--------|---------------|
| `scheduler.py` | Orchestrates the daily cycle: sleeps until KO, drives the poll loop, clears daily state |
| `live_loop.py` | Polls live fixtures, deduplicates by `{id}_{score}_{event_count}`, posts updates |
| `ft_handler.py` | Tracks matches for full-time, detects FT via ESPN cache or API-Football fallback, posts results |
| `api_provider.py` | **Single entry point for all fixture data.** ESPN primary with 55s cache; switches to API-Football after 3 failures; enriches incomplete ESPN events on demand |
| `discord_poster.py` | **All `channel.send()` calls go through here.** Two paths: by channel ID (`post_new_general_message`) and by command context (`post_new_message_to_context`) |
| `bot_mode.py` | Reads/writes broadcast mode (`verbose`/`normal`/`silent`) to `bot_memory/state.json` |
| `storage.py` | Thin JSON read/write wrapper for `bot_memory/` |
| `power_manager.py` | Disables OS sleep on startup (Windows + Linux); restores on exit |

### utils/

| Utility | Responsibility |
|---------|---------------|
| `espn_client.py` | ESPN public API client. Fetches scoreboards concurrently, normalises ESPN's format to the shared match dict format |
| `api_client.py` | API-Football client. Handles auth headers, request timeout, 429 rate-limit logging, response validation |
| `event_formatter.py` | Three pure functions: `normalize_api_football_events()`, `format_match_events()`, `event_completeness_note()` |
| `time_utils.py` | Italy timezone helpers: `italy_now()`, `parse_utc_to_italy()`, `get_italy_date_string()`, `get_current_season_year()` |
| `personality.py` | Bot name (`BOT_NAME`) and greeting strings (`HELLO_MESSAGES`) |

### cogs/

| Cog | Commands |
|-----|---------|
| `matches.py` | `!matches` — today's fixtures grouped by competition |
| `next_command.py` | `!next <team>` — next fixture for any team |
| `mode.py` | `!verbose`, `!normal`, `!silent`, `!mode` |
| `api_status.py` | `!api` / `!apistatus` / `!provider` |
| `version.py` | `!version` / `!ver` / `!commit` |
| `changelog.py` | `!changelog` |
| `competitions.py` | `!competitions` |
| `hello.py` | `!hi` / `!hello` |
| `commands_list.py` | `!commands` / `!cmds` / `!help` |

---

## 4. Key Data Flows

### Normalised match dict

Both ESPN and API-Football data is normalised into this shape before any module touches it:

```python
{
    "fixture": {
        "id": 737089,                          # int
        "date": "2026-04-06T17:00:00+00:00",   # UTC ISO string
        "status": {
            "short": "FT",                     # "NS","1H","HT","2H","ET","PEN","FT","PST","CANC","ABD"
            "elapsed": 90                      # int | None (live matches only)
        }
    },
    "teams": {
        "home": {"name": "Juventus"},
        "away": {"name": "Genoa"}
    },
    "goals": {
        "home": 2,
        "away": 0
    },
    "events": [                                # goal and red card events only
        {
            "time": {"elapsed": 34},
            "player": {"name": "Vlahović"},
            "team": {"name": "Juventus"},
            "type": "Goal",                    # "Goal" | "Card"
            "detail": "Normal Goal"            # "Normal Goal" | "Penalty" | "Own Goal" | "Red Card"
        }
    ],
    "league": {"id": 135}
}
```

### Live update flow

```
run_live_loop()
  ├── api_provider.fetch_live()              ESPN cache or API-Football /fixtures?live=all
  ├── for each match:
  │     key = "{id}_{home}-{away}_{event_count}"
  │     if key in already_posted → skip
  │     enrich_fixture_events()              fetches API-Football if ESPN events incomplete
  │     format_match_events()               → ["34' - Vlahović (H)", ...]
  │     post_live_update()                  → channel.send(...)
  │     track_match_for_ft()               registers match for FT checking
  └── already_posted.add(key)
```

### Full-time detection (ESPN path)

```
fetch_and_post_ft()  [ESPN mode]
  ├── fetch_finished_today()               filtered from 55s cache
  ├── for each tracked match past exp_ft:
  │     if match in finished list:
  │       _post_ft_from_data()
  │         ├── enrich_fixture_events()    fetches API-Football if events incomplete
  │         ├── format_match_events()
  │         └── post_new_general_message()
  │     elif 30+ min past exp_ft and still not FT:
  │       drop from tracking (log warning)
  └── orphan check: FT matches not in tracked_matches → announce anyway
```

### ESPN event enrichment

Called whenever ESPN event data is incomplete (goal count < score total):

```
enrich_fixture_events(session, match)
  ├── count ESPN goal events vs total_goals
  ├── if complete → return match unchanged (no API call)
  ├── else:
  │     fetch_fixture(session, fixture_id)    → GET /fixtures?id={id}
  │     normalize_api_football_events()
  │     if API-Football goal count > ESPN goal count:
  │       return match with enriched events
  │     else:
  │       return original match (both APIs incomplete; ⚠️ warning will show)
```

### API failover

```
ESPN healthy (default):
  fetch → _get_cached_scoreboard() → espn_client.fetch_all_leagues()
  success → _mark_espn_success(), update 55s cache
  failure → _mark_espn_failure()
    3rd consecutive failure → _espn_healthy = False, set _retry_after (+10 min)

ESPN unhealthy (fallback):
  fetch → api_client.fetch_live_fixtures() / fetch_day_fixtures()
  After _retry_after: next is_espn_healthy() call re-arms ESPN → probe on next request
  probe succeeds → log "switching back", reset counters
```

---

## 5. API Layer

### ESPN (primary)

- **Base URL:** `https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard`
- **Auth:** none (public API)
- **Known limitation:** `competition.details` sometimes omits goal events. The bot detects this with `event_completeness_note()` and silently fills in the gaps from API-Football.
- **Cache:** 55 seconds, per Italy date. Invalidates at midnight.
- **Poll interval:** 60 seconds

### API-Football (fallback + enrichment)

- **Base URL:** `https://v3.football.api-sports.io`
- **Auth:** `x-apisports-key` header from `API_KEY` env var
- **Endpoints used:**
  - `GET /fixtures?date=YYYY-MM-DD` — day fixtures (fallback mode)
  - `GET /fixtures?live=all` — live fixtures (fallback mode)
  - `GET /fixtures?id={id}` — single fixture by ID (FT fallback + enrichment)
  - `GET /fixtures?team={id}&season={year}&status=NS` — next fixture for `!next`
- **Poll interval:** 480 seconds (fallback mode)
- **Rate limits:** Respect the daily request cap on your plan. Enrichment calls only fire when ESPN is incomplete, so under normal conditions usage is very low.

---

## 6. Extending the Bot

### Add a new tracked competition

1. Find the league ID on [api-football.com](https://www.api-football.com/documentation-v3#tag/Leagues) and the ESPN slug from the scoreboard URL.
2. In `config.py`:
   - Add to `TRACKED_LEAGUE_IDS`
   - Add to `LEAGUE_NAME_MAP` with a display name
   - Add to `LEAGUE_SLUG_MAP` with the ESPN slug
   - If it's an international competition, add the slug to `INTERNATIONAL_SLUGS`
   - If it belongs to a domestic group (for `!next`), add to the relevant entry in `DOMESTIC_SLUG_GROUPS`

### Add a new Discord command

1. Create `cogs/my_command.py`:

```python
import logging
from discord.ext import commands
from modules.discord_poster import post_new_message_to_context

logger = logging.getLogger(__name__)

class MyCommand(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="mycommand", help="Does something useful.")
    async def my_command(self, ctx: commands.Context):
        await post_new_message_to_context(ctx, content="Hello!")

async def setup(bot: commands.Bot):
    await bot.add_cog(MyCommand(bot))
    logger.info("✔ cogs.my_command loaded")
```

2. Discord loads all cogs in `cogs/` automatically on startup — no registration needed.

> Always use `post_new_message_to_context()` from `modules/discord_poster.py` rather than calling `ctx.send()` directly.

### Add persistent runtime state

1. Decide on a new key in `bot_memory/state.json`, e.g. `"my_setting": true`.
2. Use `modules/storage.py`:

```python
from modules.storage import load, save

# Read
state = load("state.json", default={"my_setting": True})
value = state.get("my_setting", True)

# Write
state["my_setting"] = False
save("state.json", state)
```

3. Add initialisation of the default value in `update.sh` if needed.

---

## 7. Conventions

### Logging

Every module uses `logger = logging.getLogger(__name__)`. Use these levels:

| Level | When to use |
|-------|------------|
| `DEBUG` | Verbose tracing (not currently used in prod) |
| `INFO` | Normal operation: fetches, posts, state changes |
| `WARNING` | Recoverable issues: API timeouts, incomplete data |
| `ERROR` | Failed operations: can't reach channel, unexpected exceptions |

### Discord posting

**Never call `channel.send()` or `ctx.send()` directly.** Use:

- `post_new_general_message(bot, CHANNEL_ID, content=...)` — proactive posts (live updates, FT results, broadcasts)
- `post_new_message_to_context(ctx, content=...)` — command responses

### HTTP session

The bot maintains a single shared `aiohttp.ClientSession` at `bot.http_session`. Pass it explicitly to all async API functions — never create a new session per-request.

### Timezones

All user-facing times and scheduling logic use the Italy timezone (`Europe/Rome`). Import from `utils.time_utils`:

```python
from utils.time_utils import italy_now, parse_utc_to_italy, get_italy_date_string
```

Never use `datetime.now()` (naive local time) in bot logic.

### API provider

All fixture data comes through `modules/api_provider.py`. **Do not import from `utils/espn_client.py` or `utils/api_client.py` directly** in modules or cogs — the provider handles caching, failover, and health state.

The only exception is `cogs/next_command.py`, which uses `espn_client.search_team_espn()` and `fetch_next_team_fixture_espn()` directly because those are ESPN-specific search operations with no fallback equivalent.
