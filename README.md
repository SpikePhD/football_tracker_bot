# Football Tracker Bot â€” Marco Van Botten

A Discord bot that monitors live football matches and posts real-time score updates, goal events, red cards, and full-time results to a Discord channel.

Primary data source: [ESPN public API](https://github.com/pseudo-r/Public-ESPN-API) (no auth, no rate limits, polled every 60 seconds). [API-Football v3](https://www.api-football.com/) is kept as an automatic fallback.

The bot has a particular focus on AC Milan and the major Italian and European competitions, but any set of leagues can be tracked via configuration.

---

## Features

- **`!ask` command** â€” ask the bot any football question, answered by a local LLM (OpenAI-compatible API). The LLM can search the web via DuckDuckGo, query live fixture data, **and access a persistent football memory** (standings, team stats, player stats) sourced from ESPN. Persona/model/base URL are configured in `config.json` (API key stays in `.env`).
- Live score updates for football and tracked tennis players
- Tennis upcoming announcements are gated to a configurable pre-match window (default: 8 hours)
- Full-time results with complete scorer and event details
- **Grouped by sport and competition** â€” `!matches` shows football and tennis sections
- **Configurable morning broadcast** at Europe/Rome time â€” greeting + today's tracked sports
- **Startup snapshot** â€” on restart, immediately posts the day's tracked football and tennis status
- Daily schedule: scheduler starts immediately, polls football/tennis until midnight, and restarts daily
- Automatic fallback to API-Football if ESPN is unavailable (3-strike threshold, 10-minute retry)
- Silent/verbose mode to suppress automatic broadcasts without stopping live updates
- Persistent bot memory â€” state survives restarts and code updates
- Disables OS sleep on startup so the bot stays online on a home machine

### Discord Commands

| Command | Aliases | Description |
|---|---|---|
| `!matches` | â€” | Today's tracked football and tennis events, grouped by sport |
| `!competitions` | â€” | Lists all tracked competitions |
| `!next <team name>` | â€” | Any team's next scheduled match (e.g. `!next AC Milan`, `!next Arsenal`) |
| `!hi` | `!hello` | Alive check / random greeting |
| `!changelog` | â€” | Displays the version changelog |
| `!version` | `!ver`, `!commit` | Shows the current bot version and last commit |
| `!api` | `!apistatus`, `!provider` | Shows active data provider (ESPN or API-Football fallback) |
| `!goodmorning` | `!gm` | Show or configure the morning broadcast time in Europe/Rome |
| `!mode` | â€” | Show the current broadcast mode |
| `!verbose` | â€” | Enable verbose mode: startup message, morning broadcast, live updates, FT results |
| `!normal` | â€” | Enable normal mode: live updates and FT results only, no broadcasts |
| `!silent` | â€” | Enable silent mode: commands only, no automatic posts |
| `!ask <question>` | â€” | Ask the local LLM a question. Can search the web, query live fixtures, and access football memory (standings, team stats, player stats) |
| `!refresh_memory` | â€” | Admin: Force update all football memory (standings, teams, players) from ESPN |
| `!dump_memory` | â€” | Admin: Export football memory to a file for debugging |
| `!commands` | `!cmds`, `!help` | List all available commands |

---

## Requirements

- Python 3.12
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- An API-Football API key ([api-football.com](https://www.api-football.com/)) â€” used as fallback only
- A Discord channel ID where the bot will post updates
- **For `!ask`:** [ollama](https://ollama.com/) installed on the host, with a model pulled (e.g. `ollama pull qwen2.5:3b`)

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/SpikePhD/football_tracker_bot.git
cd football_tracker_bot
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate       # Linux/macOS
# .venv\Scripts\activate        # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` (secrets only):

```env
BOT_TOKEN=your_discord_bot_token_here
API_KEY=your_primary_football_api_key_here
SECONDARY_API_KEY=your_secondary_api_key_here
CHANNEL_ID=123456789012345678
LLM_API_KEY=your_llm_api_key_here
```

Then customize non-secret behavior in `config.json` (create from `config.example.json` if needed).

### 5. Run the bot

```bash
python football_tracker_bot.py
```

---

## Football Memory Feature

The `!ask` command now has access to a **persistent football memory** that stores:
- **League standings** (updated daily at midnight Italy time)
- **Team information** (coach, roster/players â€” updated weekly on Sundays)
- **Team stats** (W/D/L, goals for/against â€” updated after every Full-Time match)
- **Player stats** (goals, assists, yellow/red cards â€” updated after every Full-Time match)
- **Match history** (last 30 days of Full-Time matches)

### How It Works
1. **Data Source:** All memory data is sourced from **ESPN API** (no web scraping).
2. **Update Triggers:**
   - **Full-Time matches:** Automatically updates team stats, player stats, and match history.
   - **Daily (midnight):** Updates league standings for all tracked leagues.
   - **Weekly (Sunday midnight):** Updates team rosters and coach information.
3. **LLM Integration:** The LLM is instructed to **use memory first** for factual questions (standings, team stats, player stats). If memory is missing or stale, it falls back to web search.
4. **Staleness Warnings:** If memory hasn't been updated in over `MEMORY_STALE_THRESHOLD_DAYS` (default: 30), the LLM will warn users that the data may be outdated.

### Configuration
| Variable | Default | Description |
|---|---|---|
| `MEMORY_STALE_THRESHOLD_DAYS` | 30 | Warn if memory is older than this (days) |
| `ESPN_CACHE_TTL_SEC` | 43200 (12h) | Cache TTL for ESPN API responses (standings, roster) |

### Admin Commands
| Command | Description |
|---|---|
| `!refresh_memory` | Force update all football memory (standings, teams, players) from ESPN |
| `!dump_memory` | Export the current football memory to a JSON file |

### Example Usage
```
User: !ask What is AC Milan's position in Serie A?
Bot: AC Milan is currently 1st in Serie A with 60 points (P20 W15 D5 L0).
     Sources: Bot Memory

User: !ask Who is AC Milan's top scorer?
Bot: Olivier Giroud is AC Milan's top scorer with 12 goals.
     Sources: Bot Memory

User: !ask When is the next Inter vs Milan match?
Bot: Inter vs AC Milan - Saturday, May 10, 2025 at 20:45 (Serie A)
     Sources: ESPN API
```

---

## Deployment (Raspberry Pi / Linux / systemd)

### Create a systemd service

Create `/etc/systemd/system/marco_van_botten.service`:

```ini
[Unit]
Description=Marco Van Botten Football Tracker Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=lucac
WorkingDirectory=/home/lucac/football_tracker_bot
ExecStart=/home/lucac/football_tracker_bot/.venv/bin/python football_tracker_bot.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable marco_van_botten
sudo systemctl start marco_van_botten
sudo journalctl -u marco_van_botten -f   # follow logs
```

### Updating the bot

**From Windows** â€” double-click `update_bot.bat`. This SSH-es into the Pi and runs `update.sh` automatically.

**From the Pi directly:**

```bash
cd ~/football_tracker_bot && bash update.sh
```

`update.sh` does the following safely:
1. `git pull` â€” fetches the latest code and `inject_memory/` contents
2. Creates any new `bot_memory/` files with safe defaults â€” **never overwrites existing Pi state**
3. Restarts the `marco_van_botten` systemd service

---

## Project Structure

```
football_tracker_bot/
â”‚
â”œâ”€â”€ football_tracker_bot.py   # Entry point â€” bot lifecycle, task loops, cog loading
â”œâ”€â”€ config.py                 # Config loader: .env secrets + config.json public settings
â”œâ”€â”€ requirements.txt
â”œâ”€â”€ update.sh                 # Safe deployment script for the Pi
â”œâ”€â”€ update_bot.bat            # One-click Windows updater (SSH â†’ update.sh)
â”œâ”€â”€ auto_update.sh            # Unattended auto-update via cron
â”‚
â”œâ”€â”€ bot_memory/               # Pi-owned runtime state (gitignored, never overwritten)
â”‚   â”œâ”€â”€ state.json            # {"silent": false} â€” persists across restarts
â”‚   â”œâ”€â”€ tennis_state.json     # Tennis dedup state (upcoming/final announcements)
â”‚   â””â”€â”€ football_memory.json  # Football memory (standings, teams, players, matches)
â”‚
â”œâ”€â”€ inject_memory/            # GitHub-controlled reference data (updated on git pull)
â”‚   â””â”€â”€ (milan_calendar.json, etc. â€” added as needed)
â”‚
â”œâ”€â”€ cogs/                     # Discord command extensions (loaded dynamically at startup)
â”‚   â”œâ”€â”€ matches.py            # !matches â€” grouped fixture list with scorers
â”‚   â”œâ”€â”€ competitions.py       # !competitions
â”‚   â”œâ”€â”€ next_command.py       # !next <team> â€” any team's next fixture
â”‚   â”œâ”€â”€ hello.py              # !hi / !hello
â”‚   â”œâ”€â”€ changelog.py          # !changelog
â”‚   â”œâ”€â”€ version.py            # !version
â”‚   â”œâ”€â”€ api_status.py         # !api â€” live provider status
â”‚   â”œâ”€â”€ mode.py               # !verbose / !normal / !silent â€” broadcast mode
â”‚   â”œâ”€â”€ commands_list.py      # !commands â€” list all available commands
â”‚   â””â”€â”€ ask.py                # !ask â€” local LLM via ollama with tool calling
â”‚
â”œâ”€â”€ modules/                  # Core bot logic
â”‚   â”œâ”€â”€ scheduler.py          # Daily cycle: fetch â†’ sleep until KO â†’ poll loop
â”‚   â”œâ”€â”€ live_loop.py          # Live fixture polling and score deduplication
â”‚   â”œâ”€â”€ ft_handler.py         # Full-time detection and result posting
â”‚   â”œâ”€â”€ api_provider.py       # ESPN primary / API-Football fallback coordination
â”‚   â”œâ”€â”€ discord_poster.py     # Centralised Discord message sending
â”‚   â”œâ”€â”€ bot_mode.py           # Silent/verbose flag (reads/writes bot_memory/state.json)
â”‚   â”œâ”€â”€ storage.py            # JSON read/write wrapper for bot_memory/
â”‚   â”œâ”€â”€ power_manager.py      # OS sleep prevention
â”‚   â””â”€â”€ football_memory.py    # Football memory management (standings, teams, players)
â”‚
â””â”€â”€ utils/                    # Stateless utilities
    â”œâ”€â”€ espn_client.py        # ESPN public API client â€” fetches and normalises match data, standings, and rosters
    â”œâ”€â”€ api_client.py         # API-Football client (fallback path)
    â”œâ”€â”€ time_utils.py         # Italy timezone helpers
    â””â”€â”€ personality.py        # Greeting and startup message variants
```

---

## Architecture

```
football_tracker_bot.py
    â””â”€â”€ on_ready()
            â”œâ”€â”€ loads all cogs/ dynamically
            â”œâ”€â”€ posts startup message (greeting + grouped football/tennis snapshot)  [verbose mode only]
            â”œâ”€â”€ starts eleven_am_daily_trigger (tasks.loop @ 11:00)
            â””â”€â”€ calls launch_daily_operations_manager()
                    â””â”€â”€ schedule_day()                       â† modules/scheduler.py
                            â”œâ”€â”€ api_provider.fetch_day()     â† modules/api_provider.py
                            â”‚       â”œâ”€â”€ espn_client (primary, 60s poll)
                            â”‚       â””â”€â”€ api_client  (fallback, 480s poll)
                            â””â”€â”€ football and tennis polling loop until midnight:
                                    â”œâ”€â”€ run_live_loop()      â† modules/live_loop.py
                                    â”œâ”€â”€ fetch_and_post_ft()  â† modules/ft_handler.py
                                    â””â”€â”€ run_tennis_loop()    â† modules/tennis_loop.py (every 60s)

All Discord sends â†’ modules/discord_poster.py
Bot memory reads/writes â†’ modules/storage.py â†’ bot_memory/state.json
```

### Data flow â€” ESPN primary path

1. `api_provider.fetch_day()` fetches all 18 leagues concurrently via `espn_client.fetch_all_leagues()`
2. Results are cached for 55 seconds â€” subsequent calls within the window hit the cache
3. `espn_client` normalises ESPN's response format into the same dict shape used by API-Football, so all downstream code is provider-agnostic
4. If ESPN fails 3 times consecutively, `api_provider` switches to API-Football and logs the transition loudly
5. After 10 minutes, ESPN is probed again; on success the bot switches back automatically

### Deduplication â€” live updates

`live_loop.py` tracks which score states have already been posted using a key:

```
{match_id}_{home_goals}-{away_goals}_{event_count}
```

The event count is included because ESPN sometimes reports a score change before populating the scorer details. Including it ensures a follow-up post when scorer data arrives.

---

## Configuration Reference

Secrets are loaded from `.env` via `python-dotenv`; public behavior is loaded from `config.json`. The bot fails fast with a clear `RuntimeError` if required values are missing or invalid.

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | Yes | Discord bot token |
| `API_KEY` | Yes | API-Football (v3) key (fallback only) |
| `CHANNEL_ID` | Yes | Discord channel ID for all updates |
| `LLM_API_KEY` | No | LLM API key (Mistral/Ollama-compatible) for `!ask` command |

Non-secret behavior config lives in `config.json` (committed, safe to publish), including leagues, tennis players, polling intervals, log export limits, and LLM non-secret defaults (`base_url`, `model`, `system_prompt`, trusted domains).

