# Marco Van Botten - Football Tracker Bot

Marco Van Botten is a single-channel Discord bot for tracked football and tennis updates. It posts daily fixture summaries, live score updates, final results, provider status, runtime logs, and football Q&A answers.

The deployment target is a Raspberry Pi running a `systemd` service, with ESPN as the primary football provider and API-Football reserved for fallback/enrichment.

## Canonical Docs

- `README.md` - project overview, setup, commands, and file map
- `OPERATIONS.md` - Raspberry Pi service, update, logging, and troubleshooting workflows
- `DEVELOPER.md` - architecture, coding rules, and validation checks
- `AGENTS.md` - instructions for coding agents working in this repo
- `CHANGELOG.md` - current release notes shown by `!changelog`
- `docs/archive/CHANGELOG-legacy.md` - older release history

## What It Tracks

- Football competitions configured in `config.json`
- Tracked tennis players configured in `config.json`
- Live football scores, goals, red cards, and final results
- Live, upcoming, and recently finished tennis matches
- Football memory used by the `!ask` assistant

Default football coverage includes Serie A, Coppa Italia, Supercoppa Italiana, Premier League, FA Cup, Carabao Cup, Community Shield, La Liga, Copa del Rey, Supercopa de Espana, UEFA club competitions, Club World Cup, Intercontinental Cup, FIFA World Cup, and UEFA EURO.

## Data Providers

Football data flows through `modules/api_provider.py`.

- ESPN is primary for fixtures, live polling, and full-time detection.
- API-Football is secondary for provider fallback and sparse event enrichment.
- Enrichment is bounded by retry delays, per-tick caps, daily call budgets, mapping caches, incomplete-response cooldowns, and best-known event reuse.

## Commands

| Command | Aliases | Description |
|---|---|---|
| `!matches` | - | Today's tracked football and tennis events |
| `!tennis` | - | Tracked tennis: live now, upcoming, and today's finished matches |
| `!competitions` | - | List tracked football competitions |
| `!next <team>` | - | Show a team's next scheduled match |
| `!hi` | `!hello` | Health/greeting check |
| `!changelog` | - | Show current changelog |
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

Mode commands require Discord `manage_guild` permission. Memory commands require bot owner permission. `!update` is intentionally available to channel users and may restart the bot.

## Configuration

The repository uses a 3-file split:

- `.env` - secrets only (`BOT_TOKEN`, `API_KEY`, `SECONDARY_API_KEY`, `CHANNEL_ID`, `LLM_API_KEY`)
- `config.json` - committed non-secret behavior knobs
- `.env.deploy` - deployment script variables (`SERVICE_NAME`, `GIT_BRANCH`)

Do not put secrets in `config.json`. Start from `.env.example`, `.env.deploy.example`, and `config.example.json`.

Important `config.json` sections:

- `bot` - bot name/profile
- `tracking` - football league IDs, ESPN slugs, tennis players
- `operations` - polling, caching, live edit window, provider/enrichment behavior
- `log` - file logging and Discord log export limits
- `memory` - football memory freshness and ESPN cache settings
- `llm` - non-secret assistant endpoint, model, and persona prompt
- `search` - trusted domains for football web search

## Local Setup

Linux/macOS:

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

Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
copy .env.deploy.example .env.deploy
copy config.example.json config.json
python football_tracker_bot.py
```

## Deployment

For a first Raspberry Pi install, run:

```bash
bash install.sh
```

For an existing deployment:

```bash
bash update.sh
```

Useful service commands:

```bash
sudo systemctl restart marco_van_botten
sudo systemctl status marco_van_botten --no-pager -l
sudo journalctl -u marco_van_botten -f
```

See `OPERATIONS.md` for the canonical runbook.

## Runtime State

Runtime state lives in `bot_memory/`, which is gitignored and survives deployments. It includes mode state, logs, football memory, tennis announcement state, and generated exports.

`inject_memory/` is repo-controlled reference material and should be treated as read-only by runtime logic.

## Project Structure

```text
football_tracker_bot.py
config.py
config.json
cogs/
modules/
utils/
tests/
bot_memory/      runtime state, gitignored
inject_memory/   repo-controlled reference data
docs/archive/    archived documentation/history
install.sh
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
