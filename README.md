# Football Tracker Bot — Marco Van Botten

A Discord bot that monitors live football matches and posts real-time score updates, goal events, red cards, and full-time results to a Discord channel.

Primary data source: [ESPN public API](https://github.com/pseudo-r/Public-ESPN-API) (no auth, no rate limits, polled every 60 seconds). [API-Football v3](https://www.api-football.com/) is kept as an automatic fallback.

The bot has a particular focus on AC Milan and the major Italian and European competitions, but any set of leagues can be tracked via configuration.

---

## Features

- **`!ask` command** — ask the bot any football question, answered by a local LLM (ollama) running on the Pi. The LLM can search the web via DuckDuckGo and query live fixture data in real time. Fully configurable persona via `.env`.
- Live score updates for football and tracked tennis players
- Full-time results with complete scorer and event details
- **Grouped by sport and competition** — `!matches` shows football and tennis sections
- **Configurable morning broadcast** at Europe/Rome time — greeting + today's tracked sports
- **Startup snapshot** — on restart, immediately posts the day's tracked football and tennis status
- Daily schedule: scheduler starts immediately, polls football/tennis until midnight, and restarts daily
- Automatic fallback to API-Football if ESPN is unavailable (3-strike threshold, 10-minute retry)
- Silent/verbose mode to suppress automatic broadcasts without stopping live updates
- Persistent bot memory — state survives restarts and code updates
- Disables OS sleep on startup so the bot stays online on a home machine

### Discord Commands

| Command | Aliases | Description |
|---|---|---|
| `!matches` | — | Today's tracked football and tennis events, grouped by sport |
| `!competitions` | — | Lists all tracked competitions |
| `!next <team name>` | — | Any team's next scheduled match (e.g. `!next AC Milan`, `!next Arsenal`) |
| `!hi` | `!hello` | Alive check / random greeting |
| `!changelog` | — | Displays the version changelog |
| `!version` | `!ver`, `!commit` | Shows the current bot version and last commit |
| `!api` | `!apistatus`, `!provider` | Shows active data provider (ESPN or API-Football fallback) |
| `!goodmorning` | `!gm` | Show or configure the morning broadcast time in Europe/Rome |
| `!mode` | — | Show the current broadcast mode |
| `!verbose` | — | Enable verbose mode: startup message, morning broadcast, live updates, FT results |
| `!normal` | — | Enable normal mode: live updates and FT results only, no broadcasts |
| `!silent` | — | Enable silent mode: commands only, no automatic posts |
| `!ask <question>` | — | Ask the local LLM a question. Can search the web and query live fixtures |
| `!commands` | `!cmds`, `!help` | List all available commands |

---

## Requirements

- Python 3.11+
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- An API-Football API key ([api-football.com](https://www.api-football.com/)) — used as fallback only
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

Edit `.env`:

```env
BOT_TOKEN=your_discord_bot_token_here
API_KEY=your_api_football_key_here
CHANNEL_ID=123456789012345678

# Optional — LLM persona for !ask (defaults shown)
BOT_NAME=Marco Van Botten
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_SYSTEM_PROMPT=You are Marco Van Botten, a die-hard AC Milan supporter...
```

### 5. Run the bot

```bash
python football_tracker_bot.py
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

**From Windows** — double-click `update_bot.bat`. This SSH-es into the Pi and runs `update.sh` automatically.

**From the Pi directly:**

```bash
cd ~/football_tracker_bot && bash update.sh
```

`update.sh` does the following safely:
1. `git pull` — fetches the latest code and `inject_memory/` contents
2. Creates any new `bot_memory/` files with safe defaults — **never overwrites existing Pi state**
3. Restarts the `marco_van_botten` systemd service

---

## Project Structure

```
football_tracker_bot/
│
├── football_tracker_bot.py   # Entry point — bot lifecycle, task loops, cog loading
├── config.py                 # All config: secrets, league IDs, league names, ESPN slugs
├── requirements.txt
├── update.sh                 # Safe deployment script for the Pi
├── update_bot.bat            # One-click Windows updater (SSH → update.sh)
├── auto_update.sh            # Unattended auto-update via cron
│
├── bot_memory/               # Pi-owned runtime state (gitignored, never overwritten)
│   └── state.json            # {"silent": false} — persists across restarts
│
├── inject_memory/            # GitHub-controlled reference data (updated on git pull)
│   └── (milan_calendar.json, etc. — added as needed)
│
├── cogs/                     # Discord command extensions (loaded dynamically at startup)
│   ├── matches.py            # !matches — grouped fixture list with scorers
│   ├── competitions.py       # !competitions
│   ├── next_command.py       # !next <team> — any team's next fixture
│   ├── hello.py              # !hi / !hello
│   ├── changelog.py          # !changelog
│   ├── version.py            # !version
│   ├── api_status.py         # !api — live provider status
│   ├── mode.py               # !verbose / !normal / !silent — broadcast mode
│   ├── commands_list.py      # !commands — list all available commands
│   └── ask.py                # !ask — local LLM via ollama with tool calling
│
├── modules/                  # Core bot logic
│   ├── scheduler.py          # Daily cycle: fetch → sleep until KO → poll loop
│   ├── live_loop.py          # Live fixture polling and score deduplication
│   ├── ft_handler.py         # Full-time detection and result posting
│   ├── api_provider.py       # ESPN primary / API-Football fallback coordination
│   ├── discord_poster.py     # Centralised Discord message sending
│   ├── bot_mode.py           # Silent/verbose flag (reads/writes bot_memory/state.json)
│   ├── storage.py            # JSON read/write wrapper for bot_memory/
│   └── power_manager.py      # OS sleep prevention
│
└── utils/                    # Stateless utilities
    ├── espn_client.py        # ESPN public API client — fetches and normalises match data
    ├── api_client.py         # API-Football client (fallback path)
    ├── time_utils.py         # Italy timezone helpers
    └── personality.py        # Greeting and startup message variants
```

---

## Architecture

```
football_tracker_bot.py
    └── on_ready()
            ├── loads all cogs/ dynamically
            ├── posts startup message (greeting + grouped football/tennis snapshot)  [verbose mode only]
            ├── starts eleven_am_daily_trigger (tasks.loop @ 11:00)
            └── calls launch_daily_operations_manager()
                    └── schedule_day()                       ← modules/scheduler.py
                            ├── api_provider.fetch_day()     ← modules/api_provider.py
                            │       ├── espn_client (primary, 60s poll)
                            │       └── api_client  (fallback, 480s poll)
                            └── football and tennis polling loop until midnight:
                                    ├── run_live_loop()      ← modules/live_loop.py
                                    ├── fetch_and_post_ft()  ← modules/ft_handler.py
                                    └── run_tennis_loop()    ← modules/tennis_loop.py (every 60s)

All Discord sends → modules/discord_poster.py
Bot memory reads/writes → modules/storage.py → bot_memory/state.json
```

### Data flow — ESPN primary path

1. `api_provider.fetch_day()` fetches all 18 leagues concurrently via `espn_client.fetch_all_leagues()`
2. Results are cached for 55 seconds — subsequent calls within the window hit the cache
3. `espn_client` normalises ESPN's response format into the same dict shape used by API-Football, so all downstream code is provider-agnostic
4. If ESPN fails 3 times consecutively, `api_provider` switches to API-Football and logs the transition loudly
5. After 10 minutes, ESPN is probed again; on success the bot switches back automatically

### Deduplication — live updates

`live_loop.py` tracks which score states have already been posted using a key:

```
{match_id}_{home_goals}-{away_goals}_{event_count}
```

The event count is included because ESPN sometimes reports a score change before populating the scorer details. Including it ensures a follow-up post when scorer data arrives.

---

## Configuration Reference

Secrets are loaded from `.env` via `python-dotenv`. The bot raises a clear `RuntimeError` at startup if any are missing.

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | Yes | Discord bot token |
| `API_KEY` | Yes | API-Football (v3) key (fallback only) |
| `CHANNEL_ID` | Yes | Discord channel ID for all updates |
| `OLLAMA_URL` | No | ollama server URL (default: `http://localhost:11434`) |
| `OLLAMA_MODEL` | No | ollama model to use (default: `qwen2.5:3b`) |
| `BOT_NAME` | No | Bot display name used in the default LLM persona (default: `Marco`) |
| `OLLAMA_SYSTEM_PROMPT` | No | Full system prompt for the LLM. Overrides the default persona entirely |

Non-secret config lives in `config.py`:

| Name | Description |
|---|---|
| `TRACKED_LEAGUE_IDS` | List of API-Football league IDs to monitor |
| `LEAGUE_NAME_MAP` | Maps league ID → human-readable name (shared by all cogs) |
| `LEAGUE_SLUG_MAP` | Maps league ID → ESPN URL slug |
| `DOMESTIC_SLUG_GROUPS` | Maps a primary league slug → all domestic cup slugs for that country |
| `INTERNATIONAL_SLUGS` | ESPN slugs for European and international competitions |
| `build_league_slugs(slug)` | Returns the full slug list for a team's country + all international competitions |
