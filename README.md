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
- Football fixture lookup uses rolling UTC-aware windows and dedupes by fixture ID across provider dates.
- Public football snapshots reuse the same enrichment/best-known event layer used by live and FT paths, so `!matches` should not regress to a stale ESPN event list after richer event data has already been learned.

## Football Lifecycle

Football match lifecycle is UTC-first and fixture-ID-first. The bot tracks a fixture by provider fixture ID, UTC kickoff, provider status, retention windows, and persisted state in `bot_memory/match_state.json`.

The configured timezone is not used to decide whether a football match is active, finished, stale, or eligible for memory updates. It is used only for display, logs, local daily summaries, and scheduled human-facing routines. This prevents cross-midnight tournament fixtures from being dropped when the configured local date changes.

Important lifecycle behavior:

- live and FT state survives local midnight, restarts, provider outages, and Discord reconnects
- FT announcements are exactly-once per fixture ID
- football memory updates are exactly-once per fixture ID and retry independently from FT posts
- live message IDs are fixture-ID based and can be replaced if a Discord message is stale or deleted
- old terminal or stale fixtures are pruned by retention windows, not midnight clears

Football and tennis use a sleep/awake scheduler model to reduce idle API calls. When no relevant match is live, near kickoff/start, due for FT handling, or awaiting announcement work, the scheduler sleeps and refreshes the future schedule every 6 hours. It wakes at the configured football pre-match window or tennis pre-announcement window, then polls at the configured provider interval while work remains.

## Commands

| Command | Aliases | Description |
|---|---|---|
| `!matches` | - | Current tracked football and local-day tennis events |
| `!upcoming` | - | Upcoming tracked football fixtures grouped by local date |
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
| `!match_state [fixture_id]` | `!matchstate` | Admin: inspect persisted football lifecycle state |
| `!football_lifecycle` | `!footballlife`, `!lifecycle` | Admin: summarize provider, scheduler, and lifecycle health |
| `!update` | `!pull` | Run `update.sh` and restart the service |
| `!commands` | `!cmds`, `!help` | List available commands |

Mode and lifecycle diagnostic commands require Discord `manage_guild` permission. Memory commands require bot owner permission. `!update` is intentionally available to channel users and may restart the bot.

## Configuration

The repository uses a 3-file split:

- `.env` - secrets only (`BOT_TOKEN`, `API_KEY`, `CHANNEL_ID`, `LLM_API_KEY`)
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

Key `operations` timezone/display/lifecycle settings:

- `timezone` - display and scheduled-routine timezone, default `Europe/Rome`
- `football_prematch_window_hours` - when near-kickoff fixtures become active
- `football_display_lookup_window_hours` - public startup, `!matches`, and `!upcoming` display lookup horizon; not used for lifecycle polling
- `football_finished_retention_hours` - how long terminal fixtures remain relevant
- `football_state_retention_hours` - stale state retention for non-terminal records
- `football_expected_ft_minutes` - expected FT check offset from UTC kickoff
- `football_max_live_duration_hours` - maximum live tracking duration before stale pruning
- `tennis_pre_announce_hours` - rolling tennis pre-announcement and scheduler wake window
- `operations.api_provider.espn_poll_interval_sec` - active ESPN polling interval while football is awake
- `operations.api_provider.fallback_poll_interval_sec` - active fallback polling interval while football is awake

`football_match_lookup_window_hours` is no longer supported. Rename existing deployed configs to `football_display_lookup_window_hours` before restarting the bot.

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

Runtime state lives in `bot_memory/`, which is gitignored and survives deployments. It includes mode state, logs, football memory, tennis announcement state, generated exports, and `match_state.json` for persisted football fixture lifecycle state.

Daily operational log archives can be collected with `scripts/collect_daily_logs.sh`. On the Pi, the recommended cron job runs at 06:00 and keeps the newest 30 daily archives under `bot_memory/log_exports/daily/`. Each summary splits app-log warning/error counts from systemd journal counts so the app log remains the primary bot-health signal.

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
scripts/
bot_memory/      runtime state, gitignored
inject_memory/   repo-controlled reference data
docs/archive/    archived documentation/history
install.sh
update.sh
auto_update.sh
```

## Development Checks

```bash
python -m unittest discover -s tests -p "test_*.py"
python -m compileall config.py modules utils cogs tests scripts football_tracker_bot.py
python -c "import json, pathlib; json.loads(pathlib.Path('config.json').read_text(encoding='utf-8-sig'))"
python -c "import json, pathlib; json.loads(pathlib.Path('config.example.json').read_text(encoding='utf-8-sig'))"
```
