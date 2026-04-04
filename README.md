# Football Tracker Bot

A Discord bot that monitors live football matches and posts real-time score updates, goal events, red cards, and full-time results to a Discord channel. Built around the [API-Football](https://www.api-football.com/) (v3) API and [discord.py](https://discordpy.readthedocs.io/).

The bot has a particular focus on AC Milan and the major Italian and European competitions, but any set of leagues can be tracked via configuration.

---

## Features

- Posts live score updates every 8 minutes (goals, red cards)
- Posts full-time results with scorer/event details
- Catches matches already underway or finished when the bot starts
- Daily schedule: fetches fixtures at 11:00 AM (Italy time), sleeps until first kick-off, then polls until midnight
- Disables OS sleep on startup so the bot stays online on a home machine
- Auto-update script for unattended deployment via `systemd`

### Discord Commands

| Command | Aliases | Description |
|---|---|---|
| `!matches` | — | Lists today's tracked fixtures with current status |
| `!competitions` | — | Lists all competitions being tracked |
| `!milan` | `!nextmilan`, `!acmilan` | Shows AC Milan's next scheduled match |
| `!hi` | `!hello` | Alive check / greeting |
| `!changelog` | — | Displays the contents of `CHANGELOG.md` |
| `!version` | — | Shows the current bot version |
| `!api` | `!apistatus`, `!provider` | Shows which data API is active (ESPN or API-Football fallback) |

---

## Requirements

- Python 3.11+
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))
- An API-Football API key ([api-football.com](https://www.api-football.com/))
- A Discord channel ID where the bot will post updates

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/your-username/football_tracker_bot.git
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

Copy the example file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```env
BOT_TOKEN=your_discord_bot_token_here
API_KEY=your_api_football_key_here
CHANNEL_ID=123456789012345678
```

### 5. (Optional) Adjust tracked leagues

Edit `config.py` to change which competitions are tracked. The `TRACKED_LEAGUE_IDS` list uses API-Football league IDs:

```python
TRACKED_LEAGUE_IDS = [
    135,  # Serie A
    2,    # Champions League
    # ... add or remove as needed
]
```

### 6. Run the bot

```bash
python football_tracker_bot.py
```

---

## Deployment (Linux / systemd)

The bot is designed to run as a persistent `systemd` service with optional auto-updates.

### Create a systemd service

Create `/etc/systemd/system/marco_van_botten.service`:

```ini
[Unit]
Description=Football Tracker Discord Bot
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

### Auto-update script

`auto_update.sh` checks for new commits on the `main` branch and restarts the service if updates are found. Schedule it with cron:

```bash
crontab -e
# Add:
0 * * * * /home/lucac/football_tracker_bot/auto_update.sh
```

Configure the paths at the top of `auto_update.sh` to match your deployment.

---

## Project Structure

```
football_tracker_bot/
├── football_tracker_bot.py   # Entry point — bot setup, lifecycle, daily trigger
├── config.py                 # Environment config and league/team IDs
├── requirements.txt
├── auto_update.sh            # Unattended update + service restart script
│
├── cogs/                     # Discord command extensions (loaded dynamically)
│   ├── matches.py            # !matches command
│   ├── competitions.py       # !competitions command
│   ├── milan_command.py      # !milan command
│   ├── hello.py              # !hi / !hello command
│   ├── changelog.py          # !changelog command
│   ├── version.py            # !version command
│   └── api_status.py         # !api command (shows active data provider)
│
├── modules/                  # Core bot logic
│   ├── scheduler.py          # Daily cycle: fetch → wait for KO → poll loop
│   ├── live_loop.py          # Live fixture polling and deduplication
│   ├── ft_handler.py         # Full-time result detection and posting
│   ├── api_provider.py       # ESPN primary / API-Football fallback coordination
│   ├── discord_poster.py     # Centralised Discord message sending
│   └── power_manager.py      # OS sleep prevention
│
└── utils/                    # Stateless utilities
    ├── espn_client.py        # ESPN public API client (no auth, no rate limit)
    ├── api_client.py         # API-Football client (fallback)
    ├── time_utils.py         # Italy timezone helpers
    └── personality.py        # Greeting message variants
```

---

## Configuration Reference

All secrets are loaded from `.env` via `python-dotenv`. The bot will raise a clear `RuntimeError` at startup if any required variable is missing.

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | Yes | Discord bot token |
| `API_KEY` | Yes | API-Football (v3) key |
| `CHANNEL_ID` | Yes | Discord channel ID for live updates |

Non-secret configuration lives in `config.py`:

| Name | Description |
|---|---|
| `TRACKED_LEAGUE_IDS` | List of API-Football league IDs to monitor |
| `AC_MILAN_TEAM_ID` | Team ID used by the `!milan` command (default: 489) |
