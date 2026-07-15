# Operations Runbook - Marco Van Botten

This is the canonical runbook for the Raspberry Pi `systemd` deployment. Keep deployment and recovery workflows here; keep contributor guidance in `DEVELOPER.md`.

## 1. Access

```bash
ssh lucac@raspberry.local
cd ~/football_tracker_bot
```

## 2. Service

Default service name:

```text
marco_van_botten
```

Commands:

```bash
sudo systemctl status marco_van_botten --no-pager -l
sudo systemctl restart marco_van_botten
sudo systemctl stop marco_van_botten
sudo systemctl start marco_van_botten
```

## 3. Logs

Journal:

```bash
journalctl -u marco_van_botten -f
journalctl -u marco_van_botten -n 200 --no-pager
journalctl -u marco_van_botten --since "2 hours ago" --no-pager
journalctl -u marco_van_botten -p err --no-pager
```

App log file used by `!log`:

```text
bot_memory/logs/bot.log
```

Discord log commands:

- `!log` - recent lines
- `!log errors` - warning/error/critical lines
- `!log module modules.api_provider` - lines filtered by module

Large exports are truncated with a header note.

### Daily log rotation

The bot can collect one archive per day so provider behavior and scheduler decisions can be reviewed later:

```bash
cd ~/football_tracker_bot
bash scripts/collect_daily_logs.sh
```

By default the script collects yesterday's app log and systemd journal into:

```text
bot_memory/log_exports/daily/YYYY-MM-DD/
bot_memory/log_exports/daily/logs_YYYY-MM-DD.tar.gz
```

Each daily folder includes `summary_YYYY-MM-DD.txt`. Treat `app_warning_error_count` as the main bot health signal. Counts are based on logger severity labels such as `[WARNING ]`, `[ERROR   ]`, and `[CRITICAL]`, so INFO text containing words like "error" is not counted as an error. Use `journal_warning_error_count` for service, restart, and systemd context; the journal can include duplicated app output because systemd captures service stdout/stderr.

The app export reads numbered rotations from oldest to newest, then the current `bot.log`, and applies a stable timestamp sort. Events logged within the same second therefore retain their original causal order, including across a rotation boundary. Routine successful active ESPN football refreshes are DEBUG diagnostics and do not appear in the normal INFO-level production log; lifecycle events, provider failures, and request counters remain available for operational review.

During football awake windows, `No live football fixtures returned` means the live endpoint returned an empty list for that poll. This is normal before kickoff, during provider visibility delays, or shortly after FT while the scheduler is still checking due work; actual provider failures are logged separately by the provider/client layers. Discord disconnect/resume pairs are usually gateway or network churn when a `Discord session RESUMED` line follows; investigate only if disconnects are frequent, not followed by resumes, or accompanied by ERROR/CRITICAL lines.

To collect a specific date:

```bash
bash scripts/collect_daily_logs.sh 2026-06-12
```

Recommended cron entry on the Raspberry Pi:

```cron
0 6 * * * cd ~/football_tracker_bot && bash scripts/collect_daily_logs.sh >> bot_memory/log_exports/daily/collect_daily_logs.log 2>&1
```

Install it with:

```bash
crontab -e
```

Paste the cron line at the bottom, save, then confirm with:

```bash
crontab -l
```

The script keeps the newest 30 daily archives and removes older dated folders and `logs_YYYY-MM-DD.tar.gz` files. Override retention for a manual run only if needed:

```bash
RETENTION_DAYS=45 bash scripts/collect_daily_logs.sh
```

## 4. Updates

Manual update:

```bash
cd ~/football_tracker_bot
bash update.sh
```

Auto-update:

```bash
tail -n 100 ~/football_tracker_bot/auto_update.log
```

`auto_update.sh` runs through cron and pulls/restarts when remote changes are detected.

Discord-triggered update:

```text
!update
```

`!update` (alias `!pull`) runs `bash update.sh` from inside the bot process. It is owner-only and may restart the service immediately.

When the dashboard integration is installed, `!update` and the dashboard use the separate managed update service. That service runs canonical `update.sh` outside both application services and then restarts the bot and dashboard. Direct in-process updating remains only as a migration fallback when the managed service is unavailable.

## 4a. Dashboard Service

Install once after pulling a dashboard-capable release:

```bash
cd ~/football_tracker_bot
bash install_dashboard.sh
```

Default services and access:

```text
marco_van_botten_dashboard.service
marco_van_botten_update.service
http://<pi-address>:8765
admin / admin
```

Useful checks:

```bash
sudo systemctl status marco_van_botten_dashboard --no-pager -l
journalctl -u marco_van_botten_dashboard -n 100 --no-pager
sudo systemctl restart marco_van_botten_dashboard
```

The default credentials intentionally remain active until manually changed, with a persistent warning. Dashboard administrators are stored as salted scrypt hashes in `bot_memory/dashboard_users.json`. Sessions are memory-only, so a dashboard restart signs users out. Administrative actions are written without secrets to the rotating `bot_memory/logs/dashboard_audit.jsonl`.

Use dashboard HTTP only on a trusted LAN/VPN. Public internet access requires an HTTPS reverse proxy; do not forward port 8765 directly from the router.

## 5. Configuration And State

- `.env` - secrets only
- `config.json` - committed non-secret defaults
- `config.local.json` - Git-ignored host overrides, never overwritten by updates
- `.env.deploy` - host-specific deployment variables
- `bot_memory/` - runtime state, gitignored, never overwritten by updates
- `inject_memory/` - repo-controlled reference data, read-only at runtime

Local objects are deep-merged over committed defaults; arrays and scalar values replace defaults. Configuration is fully validated at startup and changes require a restart. During migration, a local `discord.channel_id` wins over legacy `CHANNEL_ID` in `.env`. Owner entries use `{ "id": <Discord user ID>, "label": <human label> }`; authorization uses only the numeric ID. If the list is empty, the Discord application owner is the temporary fallback.

Keep host identity settings in `config.local.json`, for example:

```json
{
  "discord": {"channel_id": 123456789012345678},
  "administration": {
    "owner_users": [{"id": 123456789012345678, "label": "Luca"}]
  }
}
```

Commands are accepted only in the configured channel. Updates, logs, memory refreshes/exports, and future configuration controls are owner-only. Owners or members with `Manage Server` may change broadcast/morning settings and inspect lifecycle diagnostics.

Important API-Football enrichment knobs live under `operations.api_provider` in `config.json`:

- `enrich_daily_call_budget`
- `enrich_max_calls_per_tick`
- `enrich_retry_delays_sec`
- `enrich_negative_mapping_ttl_sec`
- `enrich_incomplete_events_cooldown_sec`

Football display and lifecycle knobs live directly under `operations`:

- `timezone` - display and scheduled-routine timezone only
- `football_display_lookup_window_hours` - startup, `!matches`, and `!upcoming` display lookup breadth only
- `football_prematch_window_hours`
- `football_finished_retention_hours`
- `football_state_retention_hours`
- `football_expected_ft_minutes`
- `football_max_live_duration_hours`

Lifecycle polling does not use `football_display_lookup_window_hours`; it uses the UTC lifecycle window derived from prematch, live-duration, finished-retention, and state-retention settings. `football_match_lookup_window_hours` is no longer supported and should be renamed if it exists in a deployed config.

Provider team-name aliases live under `tracking.provider_team_aliases`. Use them when ESPN and API-Football name the same team differently, especially national teams. Keep aliases conservative; they are used only to help map the same real fixture across providers, not to broaden tracked competitions.

Football lifecycle state is persisted in `bot_memory/match_state.json`. Tennis lifecycle state is persisted in versioned per-match records in `bot_memory/tennis_state.json`. Runtime JSON writes are lock-protected and atomic. Do not edit these files while the service is running unless you are recovering from a specific incident.

State records are keyed by canonical fixture ID, preferring ESPN IDs when known. API-Football fallback IDs are stored under `provider_ids`, for example:

```json
{
  "fixture_id": "760429",
  "provider_ids": {
    "espn": "760429",
    "api_football": "1489379"
  }
}
```

This aliasing is what prevents fallback live/FT data from creating a second lifecycle for the same real match.

Football and tennis both use a sleep/awake scheduler model:

- sleeping: no live, near-start, FT-due, or pending announcement work; future schedule refresh runs every 6 hours
- awake: active work exists; football polls at the configured provider interval; tennis uses 15-minute early watch, 2-minute imminent/delayed-start, and 60-second live/final-pending defaults

One awake football check uses one rolling provider snapshot for scheduler decisions, live updates, and FT processing. Direct single-fixture FT recovery remains separate only for persisted due fixtures missing from that shared window.

ESPN refreshes are active-targeted. Full discovery across all configured leagues remains every 30 minutes for current/future provider dates and every 6 hours for past dates. Between discoveries, the existing live freshness interval refreshes only leagues with live, near-kickoff, unresolved FT, or late-event-repair work. This retains competition discovery and cross-midnight coverage while avoiding all-league fan-out every minute. Provider health snapshots expose daily `full_discovery`, `active_refresh`, and `total` league-request counts.

Tennis follows the same discovery/targeting principle. A cold or periodic discovery makes eight ESPN requests (ATP/WTA across default, yesterday, today, and tomorrow). Between discoveries, one request is made for each distinct known tour/date pair. Failed sources retain recent successful data for up to twice the discovery interval. `!api` and dashboard health report tennis discovery, targeted, success, timeout, HTTP-error, and other-error counters for the local day/process.

Unannounced tennis finals remain eligible for retry for `operations.tennis_finished_retention_hours`, including matches that cross local midnight. A failed Discord send is not recorded as announced. Tennis live-message IDs and final deduplication survive service restarts; old list-based tennis state is migrated automatically on first load.

The scheduler loads tennis deduplication before its first decision. Expired terminal tennis records are pruned after the same finished-retention window; live and future records are retained.

Weekly roster refreshes derive ESPN league slugs from stored standings and matches, then fall back to the generic team endpoint. Confirmed 400/404 unsupported lookups are retained in `bot_memory/roster_lookup_state.json` for the configured retry period; transient failures are never negative-cached and existing roster data is preserved.

Inspect scheduler mode with:

```text
!football_lifecycle
```

The command reports football and tennis scheduler modes, next check times, planned wake times, wake/sleep reasons, provider mode, lifecycle windows, and display lookup settings. Tennis may stay awake for an `NS` match inside the configured start-watch window while waiting for ESPN to change it to `LIVE`; this does not send a standalone upcoming post.

## 6. Checks After Update

```bash
python3 -c "import json, pathlib; json.loads(pathlib.Path('config.json').read_text(encoding='utf-8-sig'))" && echo "config.json OK"
python3 -c "import json, pathlib; json.loads(pathlib.Path('config.example.json').read_text(encoding='utf-8-sig'))" && echo "config.example.json OK"
python3 -c "from modules.configuration import load_effective_config; load_effective_config()" && echo "effective config OK"
sudo systemctl restart marco_van_botten
sudo systemctl status marco_van_botten --no-pager -l
sudo journalctl -u marco_van_botten -n 80 --no-pager
```

## 7. Broadcast Modes

- `!verbose` - startup + morning + live + FT
- `!normal` - live + FT only
- `!silent` - commands only
- `!mode` - current mode

Mode changes require a configured owner or Discord `Manage Server`. Read-only mode status remains public in the configured channel.

## 8. Troubleshooting

### Provider status

Use Discord:

```text
!api
```

Or inspect logs:

```bash
journalctl -u marco_van_botten --since "1 hour ago" --no-pager | grep APIProvider
```

Expected healthy ESPN mode logs mention ESPN scoreboard fetches. Fallback mode logs mention API-Football fallback and include the next ESPN retry time.

### Event enrichment status

Use app logs or `!log module modules.api_provider` and search for `[Enrich]`.

Useful markers:

- `Stored best-known events` - events were saved as the best known event list
- `Reusing best-known enriched events` - the bot prevented an ESPN event-data downgrade
- `Mapped ESPN fixture ... -> API-Football fixture ...` - provider IDs were linked for enrichment and future direct FT recovery
- `missing goal event(s)` - enrichment retry state started
- `API-Football enrichment call X/Y` - one enrichment-budget call was consumed
- `Requesting API-Football events` - the bot called the API-Football events endpoint
- `Cached negative API-Football mapping` - mapping failed and is temporarily cached
- `daily call budget exhausted` - enrichment calls are paused until the next configured local day

If the log only shows stored/reused best-known events, enrichment logic ran but API-Football event enrichment was not needed.

`!matches`, startup snapshots, live updates, and FT posts all pass football fixtures through the same enrichment/best-known event layer before formatting. Missing-goal warnings are intentionally hidden while event completeness is `pending_enrichment`; the bot shows the best known score/events and keeps retrying within the configured enrichment budget. The warning appears only after the fixture/score state reaches `exhausted_missing`.

For FT results, the first FT post is sent promptly with the best current data. If enrichment later improves the event list or changes the warning state, the bot edits the stored FT message instead of reposting. Memory updates remain deferred while event completeness is pending, then run once when data is complete or enrichment is exhausted.

If live updates and `!matches` disagree after enrichment is exhausted, inspect `[Enrich]` logs for best-known event reuse and verify `match_state.json` has the expected `event_completeness_key`, `event_completeness_status`, and `ft_message_id` for the fixture.

### Bot restarts in a loop

1. Inspect `journalctl -u marco_van_botten -n 120 --no-pager`.
2. Validate `.env`, `config.json`, and `.env.deploy`.
3. Confirm `.venv` and dependencies exist.
4. Run the update checks in section 6.

### Config parse or BOM issues

The bot reads JSON with `utf-8-sig`, but some manual tools fail on BOM-encoded files. Normalize `config.json` if a JSON tool reports an unexpected UTF-8 BOM:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path("config.json")
p.write_text(p.read_text(encoding="utf-8-sig"), encoding="utf-8")
PY
sudo systemctl restart marco_van_botten
```

### API-Football limit reached

Expected mitigations:

- quota lockout after the first explicit daily-limit response
- enrichment dedup per fixture state
- per-tick enrichment cap
- daily enrichment call budget
- negative fixture-mapping cache
- incomplete event-response cooldown
- best-known event reuse
- skip enrichment while lockout is active

Recovery:

1. Wait for the API-Football daily quota reset.
2. Lower `operations.api_provider.enrich_daily_call_budget` if the limit is reached too easily.
3. Increase `enrich_negative_mapping_ttl_sec` if mapping calls are wasteful.
4. Increase `enrich_incomplete_events_cooldown_sec` or make `enrich_retry_delays_sec` more conservative if event calls repeat too quickly.

### Cross-midnight football fixtures

Football polling is not bounded by the configured local calendar day. A fixture that kicks off before local midnight and finishes after local midnight should keep:

- live message upserts
- FT tracking
- FT announcement dedupe
- football memory update dedupe
- retained fixture state

If a cross-midnight fixture is missing, inspect:

```bash
journalctl -u marco_van_botten --since "4 hours ago" --no-pager | grep -E "match_state|APIProvider|ft_handler|live_loop"
python3 -m json.tool bot_memory/match_state.json | less
```

Expected state records include `kickoff_utc`, `expected_ft_utc`, `last_status`, `live_message_id`, `ft_announced`, and `memory_updated`.

### Duplicate FT or fallback-provider posts

If a fixture appears twice, once with an ESPN ID and once with an API-Football ID, inspect the canonical alias state:

```bash
python3 -m json.tool bot_memory/match_state.json | grep -A12 -B4 '"provider_ids"'
```

Expected behavior is one canonical record with both IDs under `provider_ids`. A mapped fallback terminal fixture whose canonical ESPN record already has `ft_announced=true` and `memory_updated=true` should not post another FT message or keep football awake.

If aliases are missing, check `!log module modules.api_provider` for mapping failures or low-confidence candidate rejections. For national-team naming differences, add a conservative alias under `tracking.provider_team_aliases` and restart the service.

### ESPN is healthy but scorer details are delayed

ESPN can update the score before publishing event details. The bot waits before using API-Football because ESPN often fills the missing event shortly afterward.

Check for:

```text
[Enrich] Fixture ... has ... missing goal event(s)
[Enrich] Stored best-known events ...
```

If the missing event appears from ESPN before the retry delay expires, API-Football will not be called.
