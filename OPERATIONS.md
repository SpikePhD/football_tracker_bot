# Operations Runbook - Marco Van Botten

Operational guide for the Raspberry Pi deployment.

## 1. Access

```bash
ssh lucac@raspberry.local
cd ~/football_tracker_bot
```

## 2. Service

```bash
sudo systemctl status marco_van_botten --no-pager -l
sudo systemctl restart marco_van_botten
sudo systemctl stop marco_van_botten
sudo systemctl start marco_van_botten
```

## 3. Logs

### Journal

```bash
journalctl -u marco_van_botten -f
journalctl -u marco_van_botten -n 200 --no-pager
journalctl -u marco_van_botten --since "2 hours ago" --no-pager
journalctl -u marco_van_botten -p err --no-pager
```

### App log file (`!log` source)

Default path is configured in `config.json` and typically:

```text
bot_memory/logs/bot.log
```

## 4. Updates

### Manual

```bash
cd ~/football_tracker_bot
bash update.sh
```

### Auto-update

`auto_update.sh` runs via cron and pulls/restarts when remote changes are detected.

Inspect:

```bash
tail -n 100 ~/football_tracker_bot/auto_update.log
```

### Discord-triggered update

`!update` (alias `!pull`) runs `bash update.sh` from inside the bot process.
It is intentionally open to channel users and may restart the service immediately.

## 5. Configuration Files

- `.env` -> secrets only
- `config.json` -> non-secret behavior
- `.env.deploy` -> deploy settings used by scripts

Important API-Football enrichment knobs live in `config.json` under `operations.api_provider`:

- `enrich_daily_call_budget` -> maximum enrichment calls per Italy day
- `enrich_max_calls_per_tick` -> maximum enrichment calls in one scheduler minute
- `enrich_retry_delays_sec` -> retry schedule after ESPN reports a score without enough goal events
- `enrich_negative_mapping_ttl_sec` -> how long to remember that ESPN fixture mapping failed
- `enrich_incomplete_events_cooldown_sec` -> how long to wait before refetching incomplete API-Football events

The current default allows up to 100 enrichment calls per day.

## 6. Common Checks After Update

```bash
python3 -c "import json, pathlib; json.loads(pathlib.Path('config.json').read_text(encoding='utf-8-sig'))" && echo "config.json OK"
python3 -c "import json, pathlib; json.loads(pathlib.Path('config.example.json').read_text(encoding='utf-8-sig'))" && echo "config.example.json OK"
sudo systemctl restart marco_van_botten
sudo systemctl status marco_van_botten --no-pager -l
sudo journalctl -u marco_van_botten -n 80 --no-pager
```

## 7. Known Failure Pattern: UTF-8 BOM in `config.json`

The bot accepts UTF-8 with or without BOM, but some manual JSON tools do not. If a tool fails with:

```text
RuntimeError: config.json is not valid JSON: Unexpected UTF-8 BOM
```

Fix:

```bash
python3 - <<'PY'
from pathlib import Path
p = Path('config.json')
text = p.read_text(encoding='utf-8-sig')
p.write_text(text, encoding='utf-8')
print('config.json BOM removed')
PY
sudo systemctl restart marco_van_botten
```

## 8. `!log` Command Usage

- `!log` -> recent lines
- `!log errors` -> warning/error/critical lines
- `!log module modules.api_provider` -> lines filtered by module

If output is too large, export is truncated with a header note.

## 9. Mode Commands

- `!verbose` -> startup + morning + live + FT
- `!normal` -> live + FT only
- `!silent` -> commands only
- `!mode` -> current mode

## 10. Troubleshooting

### Provider status

Use Discord:

```text
!api
```

Or inspect logs:

```bash
journalctl -u marco_van_botten --since "1 hour ago" --no-pager | grep APIProvider
```

Expected healthy ESPN mode looks like:

```text
[APIProvider] ESPN scoreboard fetched: ...
```

Fallback mode logs mention `API-FOOTBALL fallback` and include the next ESPN retry time.

### Event enrichment status

Use app logs or `!log module modules.api_provider` and search for `[Enrich]`.

Useful markers:

- `Stored best-known events` -> ESPN or API-Football events were saved as the best known event list
- `Reusing best-known enriched events` -> the bot prevented an ESPN event-data downgrade
- `Fixture ... has ... missing goal event(s)` -> enrichment retry state started
- `API-Football enrichment call X/Y` -> one API-Football enrichment-budget call was consumed
- `Requesting API-Football events` -> the bot called the API-Football events endpoint
- `Cached negative API-Football mapping` -> mapping failed and will not be retried until the TTL expires
- `daily call budget exhausted` -> enrichment calls are paused until the next Italy day

If the log only shows `Stored best-known events` and `Reusing best-known enriched events`, enrichment logic ran but API-Football event enrichment was not needed.

### Bot restarts in loop

1. `journalctl -u marco_van_botten -n 120 --no-pager`
2. Validate `.env`, `config.json`, `.env.deploy`
3. Confirm Python venv and dependencies are present

### API-Football limit reached

Expected mitigation now:

- quota lockout after first explicit daily-limit response
- enrichment dedup per fixture state
- per-tick enrichment cap
- daily enrichment call budget
- negative fixture-mapping cache
- incomplete event-response cooldown
- best-known event reuse to avoid downgrades
- skip enrichment while lockout is active

Immediate recovery:

1. Wait for the API-Football daily quota reset.
2. If the limit is being reached too easily, lower `operations.api_provider.enrich_daily_call_budget`.
3. If mapping calls are wasteful, increase `enrich_negative_mapping_ttl_sec`.
4. If event calls repeat too quickly, increase `enrich_incomplete_events_cooldown_sec` or make `enrich_retry_delays_sec` more conservative.

### ESPN is healthy but scorer details are delayed

This can happen when ESPN updates the score before it publishes event details. The bot waits before using API-Football, because ESPN often fills the missing event shortly afterward.

Check for:

```text
[Enrich] Fixture ... has ... missing goal event(s)
[Enrich] Stored best-known events ...
```

If the missing event appears from ESPN before the retry delay expires, API-Football will not be called.
