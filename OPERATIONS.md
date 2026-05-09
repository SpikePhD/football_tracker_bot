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

## 5. Configuration Files

- `.env` -> secrets only
- `config.json` -> non-secret behavior
- `.env.deploy` -> deploy settings used by scripts

## 6. Common Checks After Update

```bash
python3 -m json.tool config.json >/dev/null && echo "config.json OK"
python3 -m json.tool config.example.json >/dev/null && echo "config.example.json OK"
sudo systemctl restart marco_van_botten
sudo systemctl status marco_van_botten --no-pager -l
sudo journalctl -u marco_van_botten -n 80 --no-pager
```

## 7. Known Failure Pattern: UTF-8 BOM in `config.json`

Symptom:

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

### Bot restarts in loop

1. `journalctl -u marco_van_botten -n 120 --no-pager`
2. Validate `.env`, `config.json`, `.env.deploy`
3. Confirm Python venv and dependencies are present

### API-Football limit reached

Expected mitigation now:

- quota lockout after first explicit daily-limit response
- enrichment dedup per fixture state
- per-tick enrichment cap
- skip enrichment while lockout is active
