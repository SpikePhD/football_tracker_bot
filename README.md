# Marco Van Botten - Football Tracker Bot

Marco Van Botten is a Discord bot for tracking selected football competitions and tennis players in one channel. It posts daily fixture summaries, live score updates, final results, tracked tennis updates, provider status, operational logs, and football Q&A answers.

The bot is designed for a Raspberry Pi deployment with `systemd`, but it also runs locally for development.

## What It Tracks

- Football competitions configured in `config.json`
- Tracked tennis players configured in `config.json`
- Live football scores, goals, red cards, and final results
- Live, upcoming, and recently finished tennis matches
- Football memory used by the `!ask` assistant

Default football coverage includes Serie A, Coppa Italia, Supercoppa Italiana, Premier League, FA Cup, Carabao Cup, Community Shield, La Liga, Copa del Rey, Supercopa de Espana, UEFA club competitions, Club World Cup, Intercontinental Cup, FIFA World Cup, and UEFA EURO.

## Data Providers

Football uses a provider hierarchy:

- ESPN is the primary provider for fixtures, live polling, and full-time detection.
- API-Football is the secondary provider for fallback mode and sparse event enrichment.

ESPN is queried first because it does not require an API key and has been the most reliable source for this bot. API-Football is protected by quota controls because the free plan has a daily call limit.

The enrichment system only calls API-Football when ESPN has an incomplete goal-event list, for example when the score is `1-0` but ESPN has not supplied the scorer event yet. It uses retry delays, per-tick caps, a daily budget, mapping caches, incomplete-response cooldowns, and best-known event reuse to avoid wasting calls.

## Core Features

- Grouped daily fixture snapshots via `!matches`
- Live football message upserts with scoreline, minute, goals, and cards
- Final result posts with scorer details where available
- Tennis tracking for live, upcoming, and finished matches
- Morning broadcast controls with `!goodmorning`
- Broadcast modes: verbose, normal, and silent
- Provider status command showing ESPN or API-Football fallback state
- `!ask` football assistant with web search, trusted-source preference, live fixture tools, next-match lookup, and football memory
- Runtime football memory export and refresh commands for admins
- Runtime log export through Discord
- Discord-triggered update command for Raspberry Pi deployment
- Automatic update script support

## Commands

| Command | Aliases | Description |
|---|---|---|
| `!matches` | - | Today's tracked football and tennis events |
| `!tennis` | - | Tracked tennis: live now, upcoming, and today's finished matches |
| `!competitions` | - | List tracked football competitions |
| `!next <team>` | - | Show a team's next scheduled match |
| `!hi` | `!hello` | Health/greeting check |
| `!changelog` | - | Show changelog |
| `!version` | `!ver`, `!commit` | Show running version and last update |
| `!api` | `!apistatus`, `!provider` | Current football data provider status |
| `!goodmorning` | `!gm` | Morning broadcast settings |
| `!mode` | - | Show current broadcast mode |
| `!verbose` | `!Verbose`, `!VERBOSE` | Startup, morning, live, and FT posts |
| `!normal` | `!Normal`, `!NORMAL` | Live and FT posts only |
| `!silent` | `!Silent`, `!SILENT` | Commands only, no automatic posts |
| `!ask <question>` | - | Ask the football assistant |
| `!refresh_memory` | - | Owner-only memory refresh |
| `!dump_memory` | - | Owner-only memory export |
| `!log` | - | Export recent runtime logs |
| `!log errors` | - | Export warning/error/critical logs |
| `!log module <name>` | - | Export logs filtered by module |
| `!update` | `!pull` | Run `update.sh` and restart the service |
| `!commands` | `!cmds`, `!help` | List available commands |

Mode commands require Discord `manage_guild` permission. Memory commands require bot owner permission. `!update` is intentionally available to channel users and can restart the bot.

## Configuration Model

The repository uses a 3-file split:

- `.env` contains secrets only.
- `config.json` contains committed, non-secret behavior knobs.
- `.env.deploy` contains deployment script variables.

Do not put secrets in `config.json`.

### `.env`

```env
BOT_TOKEN=...
API_KEY=...
SECONDARY_API_KEY=...
CHANNEL_ID=...
LLM_API_KEY=...
```

`API_KEY` and `SECONDARY_API_KEY` are for API-Football compatibility and fallback/enrichment paths. `LLM_API_KEY` is used by the external LLM endpoint configured in `config.json`.

### `config.json`

Use `config.example.json` as the starting point. Important sections:

- `bot`: bot name
- `tracking`: football league IDs, ESPN slugs, tennis players
- `operations`: polling, caching, live edit window, provider behavior
- `log`: file logging and Discord log export limits
- `memory`: stale-memory and ESPN cache settings
- `llm`: non-secret assistant endpoint, model, and persona prompt
- `search`: trusted domains for football web search

API-Football enrichment controls live under `operations.api_provider`:

```json
{
  "enrich_max_calls_per_tick": 2,
  "enrich_grace_sec": 10,
  "enrich_daily_call_budget": 100,
  "enrich_negative_mapping_ttl_sec": 900,
  "enrich_incomplete_events_cooldown_sec": 180,
  "enrich_retry_delays_sec": [60, 180, 600, 1200]
}
```

These settings keep enrichment bounded while still allowing the full free daily API-Football quota to be used if needed.

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

On Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python football_tracker_bot.py
```

## Deployment

The target deployment is Raspberry Pi plus `systemd`.

Default service name:

```text
marco_van_botten
```

Useful commands:

```bash
sudo systemctl restart marco_van_botten
sudo systemctl status marco_van_botten --no-pager -l
sudo journalctl -u marco_van_botten -f
```

Manual update:

```bash
bash update.sh
```

Discord update:

```text
!update
```

## Runtime State

Runtime state is stored in `bot_memory/`, which is gitignored and survives deployments. It includes mode state, logs, football memory, and other generated state.

`inject_memory/` is repo-controlled reference material and should be treated as read-only by runtime logic.

## Project Structure

```text
football_tracker_bot.py
config.py
config.json
cogs/
modules/
utils/
bot_memory/      runtime state, gitignored
inject_memory/   repo-controlled reference data
update.sh
auto_update.sh
```

## Development Checks

```bash
python -m unittest tests.test_regressions
python -m compileall config.py modules tests
python -c "import json, pathlib; json.loads(pathlib.Path('config.json').read_text(encoding='utf-8-sig'))"
python -c "import json, pathlib; json.loads(pathlib.Path('config.example.json').read_text(encoding='utf-8-sig'))"
```

## Notes For Contributors

- Route command replies through `post_new_message_to_context(...)`.
- Route proactive posts through `modules/discord_poster.py`.
- Use the shared bot HTTP session; do not create ad-hoc `aiohttp` sessions.
- Prefer `modules/api_provider.py` for fixture data access.
- Keep runtime persistence in `bot_memory/`.
- Keep `inject_memory/` read-only from runtime code.
