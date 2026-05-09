# Football Tracker Bot - Marco Van Botten

A Discord bot that monitors live football matches and tracked tennis players, then posts live updates and final results in a Discord channel.

Primary source: ESPN public API (no auth). Secondary source: API-Football (fallback/enrichment only).

## Features

- Live football updates with scoreline + events
- Full-time result posting
- Tennis upcoming/final tracking for configured players
- `!matches` grouped daily snapshot (football + tennis)
- `!ask` command with tool-assisted football Q&A
- Startup + scheduled broadcast controls via bot mode
- `!log` command to export runtime log snippets as `.txt`

## Commands

| Command | Aliases | Description |
|---|---|---|
| `!matches` | - | Today's tracked football/tennis events |
| `!competitions` | - | List tracked competitions |
| `!next <team>` | - | Next fixture for a team |
| `!hi` | `!hello` | Health/greeting check |
| `!changelog` | - | Show changelog |
| `!version` | `!ver`, `!commit` | Show running version |
| `!api` | `!apistatus`, `!provider` | Current provider status |
| `!goodmorning` | `!gm` | Morning broadcast settings |
| `!mode` | - | Show mode |
| `!verbose` | - | Full automatic posting |
| `!normal` | - | Live + FT only |
| `!silent` | - | Commands only |
| `!ask <question>` | - | Ask the football assistant |
| `!refresh_memory` | - | Admin memory refresh |
| `!dump_memory` | - | Admin memory export |
| `!log [errors|module <name>]` | - | Export bot logs as text |
| `!commands` | `!cmds`, `!help` | List available commands |

## Python Version

Pinned to Python 3.12.

## Configuration Model

The project now uses 3 files:

- `.env` -> secrets only (token/API keys/channel IDs)
- `config.json` -> non-secret behavior (bot name, leagues, tennis players, intervals, toggles)
- `.env.deploy` -> deployment wiring (`SERVICE_NAME`, `GIT_BRANCH`)

### `.env` (secrets)

```env
BOT_TOKEN=...
API_KEY=...
SECONDARY_API_KEY=...
CHANNEL_ID=...
LLM_API_KEY=...
```

### `config.json` (non-secrets)

Use `config.example.json` as base and customize:

- bot identity/display
- tracked leagues/slugs
- tracked tennis players
- polling/caching intervals
- message behavior (edit vs post)
- log export defaults/caps

## Quick Setup

```bash
git clone https://github.com/SpikePhD/football_tracker_bot.git
cd football_tracker_bot
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp .env.deploy.example .env.deploy
cp config.example.json config.json
python football_tracker_bot.py
```

## Deployment (systemd)

Service name default: `marco_van_botten`.

Useful commands:

```bash
sudo systemctl restart marco_van_botten
sudo systemctl status marco_van_botten --no-pager -l
sudo journalctl -u marco_van_botten -f
```

## Project Structure

```text
football_tracker_bot/
|- football_tracker_bot.py
|- config.py
|- config.json
|- cogs/
|- modules/
|- utils/
|- bot_memory/      (runtime state, gitignored)
|- inject_memory/   (repo-controlled reference data)
|- update.sh
|- auto_update.sh
```

## Notes

- All Discord sends must go through `modules/discord_poster.py`.
- Provider calls should go through `modules/api_provider.py`.
- Do not put non-secret behavior knobs in `.env`.
