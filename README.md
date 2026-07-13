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
- Football fixture lookup uses rolling UTC-aware windows and dedupes by canonical fixture identity across provider dates.
- ESPN fixture IDs are preferred as the canonical identity when known. API-Football fixture IDs are stored as provider aliases so fallback/live/FT data for the same real match does not create a second lifecycle.
- Public football snapshots show fixtures whose kickoff is on the configured local day, plus any earlier fixture that is still live. Earlier terminal fixtures remain available to lifecycle recovery but are excluded from startup, good-morning, and `!matches` snapshots.
- Public football snapshots reuse the same enrichment/best-known event layer used by live and FT paths, so `!matches` should not regress to a stale ESPN event list after richer event data has already been learned.
- Missing-goal warnings are hidden while enrichment is still pending. Live posts, FT posts, startup snapshots, and `!matches` show the best known score/events first; `⚠️ ... missing from event data` appears only after enrichment is exhausted for that fixture/score state.

## Football Lifecycle

Football match lifecycle is UTC-first and canonical-fixture-ID-first. The bot tracks a fixture by canonical fixture ID, provider aliases, UTC kickoff, provider status, retention windows, and persisted state in `bot_memory/match_state.json`.

The configured timezone is not used to decide whether a football match is active, finished, stale, or eligible for memory updates. It is used only for display, logs, local daily summaries, and scheduled human-facing routines. This prevents cross-midnight tournament fixtures from being dropped when the configured local date changes.

Important lifecycle behavior:

- live and FT state survives local midnight, restarts, provider outages, and Discord reconnects
- FT announcements are exactly-once per canonical fixture ID
- football memory updates are exactly-once per canonical fixture ID and retry independently from FT posts
- live message IDs are canonical-fixture-ID based and can be replaced if a Discord message is stale or deleted
- API-Football fallback fixtures are mapped back to ESPN canonical IDs through persisted `provider_ids` aliases when a conservative league/kickoff/team-name match is available
- old terminal or stale fixtures are pruned by retention windows, not midnight clears

Football and tennis use a sleep/awake scheduler model to reduce idle API calls. When no relevant match is live, near kickoff/start, due for FT handling, or awaiting announcement work, the scheduler sleeps and refreshes the future schedule every 6 hours. It wakes at the configured football pre-match window or tennis start-watch window, then polls at the configured provider interval while work remains.

## Commands

| Command | Aliases | Description |
|---|---|---|
| `!matches` | - | Local-day football and tennis events, plus live football carry-over |
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
| `!log` | - | Owner: export recent runtime logs |
| `!log errors` | - | Owner: export warning/error/critical logs |
| `!log module <name>` | - | Owner: export logs filtered by module |
| `!match_state [fixture_id]` | `!matchstate` | Admin: inspect persisted football lifecycle state |
| `!football_lifecycle` | `!footballlife`, `!lifecycle` | Admin: summarize provider, scheduler, and lifecycle health |
| `!update` | `!pull` | Owner: run `update.sh` and restart the service |
| `!commands` | `!cmds`, `!help` | List commands available to the requester |

Commands are accepted only in the configured Discord channel. Mode/schedule changes and lifecycle diagnostics require a configured owner or Discord `Manage Server`; updates, logs, and memory administration are owner-only. When no owner IDs are configured, Discord's application owner is the migration-safe fallback.

## Configuration

The repository uses a layered configuration split:

- `.env` - secrets only (`BOT_TOKEN`, `API_KEY`, `LLM_API_KEY`)
- `config.json` - committed non-secret defaults
- `config.local.json` - optional Git-ignored host overrides written by the dashboard
- `.env.deploy` - deployment and dashboard process settings

Objects in `config.local.json` are deep-merged over defaults; arrays and scalar values replace their defaults. All settings are validated before startup or an atomic local save, and changes require a service restart. Existing deployments may temporarily keep `CHANNEL_ID` in `.env` until a local `discord.channel_id` override is saved.

Example host override:

```json
{
  "discord": {"channel_id": 123456789012345678},
  "administration": {
    "owner_users": [{"id": 123456789012345678, "label": "Luca"}]
  }
}
```

Important `config.json` sections:

- `bot` - bot name/profile
- `discord` - the single command and announcement channel
- `administration` - stable Discord owner IDs and human-readable labels
- `tracking` - football league IDs, ESPN slugs, provider team-name aliases, tennis players
- `operations` - polling, caching, live edit window, provider/enrichment behavior
- `log` - file logging and Discord log export limits
- `memory` - football memory freshness and ESPN cache settings
- `llm` - non-secret assistant endpoint, model, and persona prompt
- `search` - trusted domains for football web search

## Configuration Dashboard

The dashboard is a separate `aiohttp` process, so a dashboard failure or restart does not stop match tracking. It provides guided configuration, advanced JSON editing, masked secret replacement, Discord-owner and dashboard-account administration, runtime mode and morning-schedule controls, service health, sanitized logs, restart/update actions, and a redacted audit history.

For an existing Raspberry Pi installation, first update the bot normally, then run once over SSH:

```bash
cd ~/football_tracker_bot
bash install_dashboard.sh
```

Open `http://192.168.8.150:8765` (using the Pi's actual address if different) and sign in with lowercase `admin` / `admin`. This requested bootstrap password remains usable until changed and therefore produces a persistent critical warning. New passwords must contain at least 10 characters.

The default listener is `0.0.0.0:8765`. Plain HTTP is for a trusted LAN or VPN only. Do not expose the port directly to the public internet; place it behind an HTTPS reverse proxy first. Dashboard accounts are deliberately separate from Discord owner IDs.

The relevant `.env.deploy` settings are:

```text
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8765
DASHBOARD_SERVICE_NAME=marco_van_botten_dashboard
UPDATE_SERVICE_NAME=marco_van_botten_update
```

On a non-systemd host, run `.venv/bin/python dashboard.py` (or the equivalent virtual-environment Python). Configuration, authentication, logs, and runtime-file controls work normally; restart and managed-update controls report that they are unavailable.

Key `operations` timezone/display/lifecycle settings:

- `timezone` - display and scheduled-routine timezone, default `Europe/Rome`
- `football_prematch_window_hours` - when near-kickoff fixtures become active
- `football_display_lookup_window_hours` - public startup, `!matches`, and `!upcoming` display lookup horizon; not used for lifecycle polling
- `football_finished_retention_hours` - how long terminal fixtures remain relevant
- `football_state_retention_hours` - stale state retention for non-terminal records
- `football_expected_ft_minutes` - expected FT check offset from UTC kickoff
- `football_max_live_duration_hours` - maximum live tracking duration before stale pruning
- `tennis_pre_announce_hours` - early start-watch lead time; it does not send standalone upcoming posts
- `tennis_early_watch_poll_interval_sec` - polling cadence from the early watch boundary until the imminent window
- `tennis_imminent_window_minutes` / `tennis_imminent_poll_interval_sec` - faster scheduled/delayed-start watch near kickoff
- `tennis_live_poll_interval_sec` - cadence for live matches and unannounced finals
- `tennis_full_discovery_interval_sec` - cadence for the full ATP/WTA default/yesterday/today/tomorrow discovery sweep while awake
- `tennis_idle_discovery_interval_sec` - maximum discovery interval when no tennis work is active
- `tennis_post_start_watch_hours` - delayed-start watch duration after scheduled start
- `tennis_finished_retention_hours` - rolling window in which an unannounced tennis final remains eligible for retry, including matches that finish after local midnight
- `live_update_edit_window_messages` - number of recent channel messages searched before a buried live post is replaced with a fresh update
- `memory.roster_unsupported_retry_days` - retry delay for ESPN team roster endpoints confirmed unsupported by 400/404 responses
- `operations.api_provider.espn_poll_interval_sec` - active ESPN polling interval while football is awake
- `operations.api_provider.fallback_poll_interval_sec` - active fallback polling interval while football is awake

`football_match_lookup_window_hours` is no longer supported. Rename existing deployed configs to `football_display_lookup_window_hours` before restarting the bot.

Key `tracking` provider mapping setting:

- `provider_team_aliases` - operator-editable team-name aliases used when mapping ESPN fixtures to API-Football fixtures, especially for national-team naming differences such as `South Korea` / `Korea Republic`.

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
# Set discord.channel_id in config.local.json, or temporarily export CHANNEL_ID.
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
# Set discord.channel_id in config.local.json, or temporarily set CHANNEL_ID.
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

Runtime state lives in `bot_memory/`, which is gitignored and survives deployments. JSON state writes are atomic, and write failures are surfaced instead of being treated as successful. The directory includes mode state, logs, football memory, generated exports, and persisted football and tennis lifecycle state. `match_state.json` stores provider aliases under `provider_ids`, for example an ESPN canonical fixture plus its API-Football fallback fixture ID. It also stores FT/live message IDs and event-completeness state so missing-event warnings and later edits survive restart. `tennis_state.json` uses versioned per-match records and persists start-watch, final-announcement, and live-message IDs; legacy list-based state migrates automatically without losing deduplication.

Daily operational log archives can be collected with `scripts/collect_daily_logs.sh`. On the Pi, the recommended cron job runs at 06:00 and keeps the newest 30 daily archives under `bot_memory/log_exports/daily/`. Each summary splits app-log warning/error counts from systemd journal counts so the app log remains the primary bot-health signal.

`inject_memory/` is repo-controlled reference material and should be treated as read-only by runtime logic.

## Project Structure

```text
football_tracker_bot.py
config.py
config.json
config.local.json  optional host overrides (gitignored)
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
