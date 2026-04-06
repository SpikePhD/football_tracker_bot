# Operations Runbook — Marco Van Botten

Day-to-day maintenance guide for the bot running on a Raspberry Pi (or any Linux host). For first-time setup see `README.md`. For code changes see `DEVELOPER.md`.

---

## Table of Contents

1. [Accessing the Bot](#1-accessing-the-bot)
2. [Logs](#2-logs)
3. [Service Management](#3-service-management)
4. [Updating the Bot](#4-updating-the-bot)
5. [Broadcast Mode](#5-broadcast-mode)
6. [API Status](#6-api-status)
7. [File Locations on the Pi](#7-file-locations-on-the-pi)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Accessing the Bot

### SSH from Windows (PowerShell or Terminal)

```powershell
ssh lucac@raspberry.local
cd ~/football_tracker_bot
```

### Useful one-liners (without staying connected)

```powershell
# Tail the last 50 log lines
ssh lucac@raspberry.local "journalctl -u marco_van_botten -n 50 --no-pager"

# Check service status
ssh lucac@raspberry.local "systemctl status marco_van_botten --no-pager"
```

---

## 2. Logs

The bot logs to the systemd journal. All log lines include a timestamp, level, and the originating module.

### View logs (live — follows new entries)

```bash
journalctl -u marco_van_botten -f
```

### View the last N lines

```bash
journalctl -u marco_van_botten -n 100 --no-pager
```

### Filter by time window

```bash
# Since a specific time today
journalctl -u marco_van_botten --since "19:00" --no-pager

# Between two times
journalctl -u marco_van_botten --since "18:00" --until "19:30" --no-pager

# Since yesterday
journalctl -u marco_van_botten --since "yesterday" --no-pager
```

### Filter for errors only

```bash
journalctl -u marco_van_botten -p err --no-pager
```

### Auto-update log (separate file)

The cron-based auto-updater writes to `~/football_tracker_bot/auto_update.log`:

```bash
tail -f ~/football_tracker_bot/auto_update.log

# Or last 30 lines
tail -30 ~/football_tracker_bot/auto_update.log
```

### Log format

```
[2026-04-06 19:10:52] [INFO    ] [modules.api_provider] ESPN scoreboard fetched: 5 matches
[2026-04-06 19:10:52] [INFO    ] [modules.api_provider] 🔍 [Enrich] Fetching API-Football events for fixture 737089
[2026-04-06 19:10:53] [WARNING ] [utils.espn_client   ] espn_client: Timeout fetching uefa.champions scoreboard.
```

---

## 3. Service Management

The bot runs as a systemd service called `marco_van_botten`.

### Status

```bash
systemctl status marco_van_botten
```

### Start / Stop / Restart

```bash
sudo systemctl start marco_van_botten
sudo systemctl stop marco_van_botten
sudo systemctl restart marco_van_botten
```

### Enable / Disable auto-start on boot

```bash
sudo systemctl enable marco_van_botten    # start on boot (already set by install.sh)
sudo systemctl disable marco_van_botten   # stop starting on boot
```

### View the service definition

```bash
cat /etc/systemd/system/marco_van_botten.service
```

If you ever edit the service file, reload the daemon before restarting:

```bash
sudo systemctl daemon-reload
sudo systemctl restart marco_van_botten
```

---

## 4. Updating the Bot

### From Windows (recommended — one click)

Double-click **`update_bot.bat`** in the project folder. It SSHs into the Pi, pulls the latest code, and restarts the service automatically.

### From the Pi directly

```bash
cd ~/football_tracker_bot
bash update.sh
```

This will:
1. Pull the latest code from GitHub (`main` branch)
2. Create any new `bot_memory/` files (existing files are **never overwritten**)
3. Restart the service

### Automatic updates (cron)

`auto_update.sh` runs every 15 minutes via cron. It checks if the local commit matches the remote; if not, it pulls and restarts automatically. No action needed — this is set up by `install.sh`.

To check when the last auto-update ran:

```bash
tail -20 ~/football_tracker_bot/auto_update.log
```

To temporarily disable auto-updates:

```bash
crontab -e   # comment out or delete the auto_update.sh line
```

### After a requirements.txt change

`auto_update.sh` detects changes to `requirements.txt` and re-runs `pip install -r requirements.txt` automatically. Manual updates via `update.sh` do **not** re-install dependencies — run this manually if needed:

```bash
cd ~/football_tracker_bot
.venv/bin/pip install -r requirements.txt
sudo systemctl restart marco_van_botten
```

---

## 5. Broadcast Mode

The bot supports three broadcast modes that control what it posts automatically. Mode is persisted across restarts in `bot_memory/state.json`.

| Mode | What the bot posts automatically |
|------|----------------------------------|
| `verbose` | Startup message, 06:30 morning broadcast, live score updates, FT results |
| `normal` | Live score updates and FT results only (no startup or morning broadcast) |
| `silent` | Nothing — only responds to commands |

### Change mode via Discord

```
!verbose
!normal
!silent
```

### Check current mode

```
!mode
```

### Change mode directly on the Pi (if Discord is unavailable)

```bash
nano ~/football_tracker_bot/bot_memory/state.json
# Change the "mode" value to "verbose", "normal", or "silent"
# Then restart:
sudo systemctl restart marco_van_botten
```

---

## 6. API Status

The bot uses ESPN as its primary data source. It automatically switches to API-Football as a fallback if ESPN fails 3 times in a row.

### Check which API is active

In Discord:

```
!api
```

Output examples:

```
# Healthy (normal)
🟢 ESPN (primary) — Poll: 60s

# On fallback
🟡 API-Football (fallback) — Poll: 480s
ESPN failed 4 times. Retrying at 20:30.
```

### Poll intervals

| Provider | Interval |
|----------|---------|
| ESPN (primary) | 60 seconds |
| API-Football (fallback) | 480 seconds (8 minutes) |

The slower fallback interval is intentional — API-Football has a daily request cap.

### ESPN event enrichment

Even when ESPN is the primary provider, API-Football is occasionally called to fill in missing goal events (ESPN's public API sometimes omits them from completed matches). These calls only happen when a discrepancy is detected and are logged as:

```
🔍 [Enrich] Fetching API-Football events for fixture 737089 (2 missing goal(s) in ESPN data)
✅ [Enrich] Fixture 737089: replaced 1 ESPN events with 3 API-Football events.
```

If API-Football is also missing the data:

```
ℹ️ [Enrich] Fixture 737089: API-Football also has incomplete data. Keeping ESPN events.
```

In this case a `⚠️ N goal(s) missing from event data` warning appears in the Discord output. This is a data quality limitation of both providers and not a bot bug.

---

## 7. File Locations on the Pi

```
~/football_tracker_bot/
├── football_tracker_bot.py     Main bot entry point
├── config.py                   League IDs, names, ESPN slugs
├── .env                        Secrets: BOT_TOKEN, API_KEY, CHANNEL_ID  ← gitignored
├── .bot_config                 Deployment config: SERVICE_NAME           ← gitignored
├── requirements.txt            Python dependencies
│
├── install.sh                  First-time installer
├── update.sh                   Manual update script
├── auto_update.sh              Cron auto-updater
├── auto_update.log             Auto-update history log                   ← gitignored
│
├── bot_memory/
│   └── state.json              Broadcast mode state                      ← gitignored
│
├── cogs/                       Discord command extensions
├── modules/                    Core bot logic
└── utils/                      Stateless helpers
```

**Service file (outside the repo):**

```
/etc/systemd/system/marco_van_botten.service
```

**Sudoers rule (allows auto_update.sh to restart the service without a password):**

```
/etc/sudoers.d/football_bot_marco_van_botten
```

---

## 8. Troubleshooting

### Bot is not posting anything

1. Check the service is running: `systemctl status marco_van_botten`
2. Check broadcast mode: `!mode` — if silent, switch with `!verbose` or `!normal`
3. Check logs for errors: `journalctl -u marco_van_botten -n 50 --no-pager`
4. Check the bot has permission to post in the configured channel

### Bot crashed / service stopped

```bash
# Check what happened
journalctl -u marco_van_botten -n 100 --no-pager

# Restart
sudo systemctl restart marco_van_botten
```

The service is configured with `Restart=on-failure` so it will restart automatically after most crashes. If it keeps restarting, check for a Python error in the logs.

### "Command not found" when running update.sh

The script requires `.bot_config` to exist. If it's missing:

```bash
cd ~/football_tracker_bot
cp .bot_config.example .bot_config
# Edit .bot_config if your service name is different from marco_van_botten
```

### Bot posts duplicate updates after a restart

This is handled automatically by startup seeding — on restart, the bot reads the current fixture list and pre-populates its deduplication sets so it won't re-post updates already visible. If duplicates appear, check the logs around startup for seeding messages:

```
🌱 Seeded 2 already-FT match IDs (will not re-announce).
🌱 Seeded 1 in-progress match snapshot(s) into already_posted.
```

### ESPN timeouts in logs

```
WARNING: espn_client: Timeout fetching uefa.champions scoreboard.
```

Occasional timeouts from individual ESPN league endpoints are normal — the bot continues with the other leagues and the timed-out one will be retried on the next poll cycle. Only worry if *all* leagues are failing (the bot will switch to API-Football fallback automatically in that case).

### API-Football rate limit warning

```
WARNING: API-Football returned HTTP 429 (rate limited).
```

You've hit your daily request cap. The bot will keep trying but results will be degraded until midnight UTC when the cap resets. Consider upgrading your API-Football plan if this happens regularly, or reduce the enrichment frequency.

### Bot mode was reset to verbose after a restart

The mode is stored in `bot_memory/state.json`. If this file was deleted or overwritten, the mode resets to `verbose`. Check:

```bash
cat ~/football_tracker_bot/bot_memory/state.json
# Should contain: {"mode": "verbose"} or {"mode": "normal"} or {"mode": "silent"}
```

`update.sh` and `install.sh` only create this file if it doesn't exist — they never overwrite it. If it keeps resetting, check that your git repo doesn't have a `bot_memory/state.json` tracked (it should be gitignored).

### Checking the Discord bot token / channel ID

These are in `~/football_tracker_bot/.env`. If the token has been regenerated in the Discord Developer Portal, update it here and restart:

```bash
nano ~/football_tracker_bot/.env
sudo systemctl restart marco_van_botten
```
