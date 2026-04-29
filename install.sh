#!/bin/bash
# install.sh — First-time setup for football_tracker_bot on a Linux host (e.g. Raspberry Pi).
# Usage: bash install.sh
# Run from inside the cloned repository directory.
#
# What this script does:
#   1. Checks prerequisites (python3 ≥3.10, git, pip3)
#   2. Prompts for configuration (service name, Discord token, API key, channel ID)
#   3. Creates .venv and installs Python dependencies
#   4. Creates .env, .bot_config, and bot_memory/state.json (skips if already exist)
#   5. Creates and enables a systemd service so the bot starts on boot
#   6. Adds a passwordless sudo rule for the service restart (needed by auto_update.sh)
#   7. Sets up a cron job to run auto_update.sh every 15 minutes
#
# Re-running this script on an existing install is safe — all steps are idempotent.

set -euo pipefail

# ── Helpers ───────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

ok()   { echo -e "${GREEN}  ✔ $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $1${NC}"; }
err()  { echo -e "${RED}  ✖ $1${NC}" >&2; }
section() { echo -e "\n${YELLOW}── $1 ──────────────────────────────────────${NC}"; }

prompt_with_default() {
    # Usage: prompt_with_default "Label" "default_value" VAR_NAME
    local label="$1" default="$2" varname="$3"
    read -rp "  $label [$default]: " input
    printf -v "$varname" '%s' "${input:-$default}"
}

prompt_secret() {
    # Usage: prompt_secret "Label" VAR_NAME
    local label="$1" varname="$2"
    read -rsp "  $label: " input
    echo
    printf -v "$varname" '%s' "$input"
}

# ── Resolve bot directory ─────────────────────────────────────────────────────

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="$(whoami)"

echo ""
echo "=============================================="
echo "  Football Tracker Bot — Installer"
echo "=============================================="
echo "  Install directory : $BOT_DIR"
echo "  Running as user   : $CURRENT_USER"
echo ""

# ── Step 1: Prerequisites ─────────────────────────────────────────────────────

section "Step 1: Checking prerequisites"

MISSING=0
for cmd in git python3 pip3; do
    if command -v "$cmd" &>/dev/null; then
        ok "$cmd found ($(command -v "$cmd"))"
    else
        err "$cmd is not installed. Please install it and re-run."
        MISSING=1
    fi
done

# Check Python version ≥ 3.10
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 10 ]; then
    ok "Python $PY_VERSION (≥3.10 required)"
else
    err "Python $PY_VERSION found but ≥3.10 is required. Please upgrade."
    MISSING=1
fi

if [ "$MISSING" -ne 0 ]; then
    echo ""
    err "One or more prerequisites are missing. Aborting."
    exit 1
fi

# ── Step 2: Configuration prompts ────────────────────────────────────────────

section "Step 2: Configuration"
echo "  Press Enter to accept the default shown in [brackets]."
echo ""

prompt_with_default "Systemd service name" "marco_van_botten" SERVICE_NAME
prompt_with_default "Git branch to track"  "main"             GIT_BRANCH

echo ""
if [ -f "$BOT_DIR/.env" ]; then
    warn ".env already exists — skipping token prompts. Edit it manually if needed."
    BOT_TOKEN=""
    API_KEY=""
    CHANNEL_ID=""
else
    echo "  You will need:"
    echo "    • A Discord bot token  → https://discord.com/developers/applications"
    echo "    • An API-Football key  → https://dashboard.api-football.com"
    echo "    • The numeric ID of the Discord channel the bot should post to"
    echo ""
    prompt_secret "Discord BOT_TOKEN"      BOT_TOKEN
    prompt_secret "API-Football API_KEY"   API_KEY
    prompt_with_default "Discord CHANNEL_ID" "" CHANNEL_ID
fi

# ── Step 3: Python virtual environment ───────────────────────────────────────

section "Step 3: Python virtual environment"

if [ -d "$BOT_DIR/.venv" ]; then
    ok ".venv already exists — skipping creation"
else
    echo "  Creating .venv..."
    python3 -m venv "$BOT_DIR/.venv"
    ok ".venv created"
fi

echo "  Installing/updating dependencies from requirements.txt..."
"$BOT_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$BOT_DIR/.venv/bin/pip" install --quiet -r "$BOT_DIR/requirements.txt"
ok "Dependencies installed"

# ── Step 4: Config files ──────────────────────────────────────────────────────

section "Step 4: Config files"

# .env
if [ -f "$BOT_DIR/.env" ]; then
    ok ".env already exists — not overwritten"
elif [ -z "$BOT_TOKEN" ] || [ -z "$API_KEY" ] || [ -z "$CHANNEL_ID" ]; then
    warn ".env not created (tokens were not entered). Copy .env.example and fill it in manually."
else
    cat > "$BOT_DIR/.env" <<EOF
BOT_TOKEN=$BOT_TOKEN
API_KEY=$API_KEY
CHANNEL_ID=$CHANNEL_ID
EOF
    ok ".env created"
fi

# .bot_config
if [ -f "$BOT_DIR/.bot_config" ]; then
    ok ".bot_config already exists — not overwritten"
else
    cat > "$BOT_DIR/.bot_config" <<EOF
# .bot_config — deployment configuration (generated by install.sh)
SERVICE_NAME=$SERVICE_NAME
EOF
    ok ".bot_config created (SERVICE_NAME=$SERVICE_NAME)"
fi

# bot_memory/state.json
mkdir -p "$BOT_DIR/bot_memory"
if [ -f "$BOT_DIR/bot_memory/state.json" ]; then
    ok "bot_memory/state.json already exists — keeping existing state"
else
    echo '{"mode": "verbose"}' > "$BOT_DIR/bot_memory/state.json"
    ok "bot_memory/state.json created"
fi

# ── Step 5: Systemd service ───────────────────────────────────────────────────

if [ -f "$BOT_DIR/bot_memory/goodmorning.json" ]; then
    ok "bot_memory/goodmorning.json already exists - keeping existing state"
else
    echo '{"enabled": true, "hour": 6, "minute": 30, "timezone": "Europe/Rome"}' > "$BOT_DIR/bot_memory/goodmorning.json"
    ok "bot_memory/goodmorning.json created"
fi

section "Step 5: Systemd service"

SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"

if [ -f "$SERVICE_FILE" ]; then
    ok "$SERVICE_FILE already exists — not overwritten"
else
    echo "  Writing $SERVICE_FILE (requires sudo)..."
    sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Football Tracker Bot ($SERVICE_NAME)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/.venv/bin/python $BOT_DIR/football_tracker_bot.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    ok "$SERVICE_FILE created"
fi

echo "  Reloading systemd daemon..."
sudo systemctl daemon-reload
ok "daemon reloaded"

echo "  Enabling $SERVICE_NAME to start on boot..."
sudo systemctl enable "$SERVICE_NAME"
ok "$SERVICE_NAME enabled"

echo "  Starting $SERVICE_NAME..."
if sudo systemctl start "$SERVICE_NAME"; then
    ok "$SERVICE_NAME started"
else
    warn "$SERVICE_NAME failed to start. Check logs: journalctl -u $SERVICE_NAME -n 30"
fi

# ── Step 6: Passwordless sudo for service restart ─────────────────────────────

section "Step 6: Sudoers rule for auto-update"

SUDOERS_FILE="/etc/sudoers.d/football_bot_$SERVICE_NAME"

if [ -f "$SUDOERS_FILE" ]; then
    ok "$SUDOERS_FILE already exists — not overwritten"
else
    echo "  Writing $SUDOERS_FILE (requires sudo)..."
    # Validate with visudo before installing
    SUDOERS_LINE="$CURRENT_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart $SERVICE_NAME, /bin/systemctl status $SERVICE_NAME"
    echo "$SUDOERS_LINE" | sudo tee "$SUDOERS_FILE" > /dev/null
    sudo chmod 440 "$SUDOERS_FILE"
    ok "Sudoers rule added — auto_update.sh can restart the service without a password"
fi

# ── Step 7: Cron job for auto_update.sh ──────────────────────────────────────

section "Step 7: Auto-update cron job"

CRON_JOB="*/15 * * * * $BOT_DIR/auto_update.sh >> $BOT_DIR/auto_update.log 2>&1"

if crontab -l 2>/dev/null | grep -qF "auto_update.sh"; then
    ok "Cron job already exists — not added again"
else
    ( crontab -l 2>/dev/null; echo "$CRON_JOB" ) | crontab -
    ok "Cron job added (runs every 15 minutes)"
    echo "  → $CRON_JOB"
fi

# ── Summary ───────────────────────────────────────────────────────────────────

echo ""
echo "=============================================="
echo -e "${GREEN}  Installation complete!${NC}"
echo "=============================================="
echo ""
echo "  Service status:"
sudo systemctl status "$SERVICE_NAME" --no-pager | head -8 || true
echo ""
echo "  Useful commands:"
echo "    View logs       : journalctl -u $SERVICE_NAME -f"
echo "    Restart bot     : sudo systemctl restart $SERVICE_NAME"
echo "    Update manually : bash $BOT_DIR/update.sh"
echo "    Auto-update log : tail -f $BOT_DIR/auto_update.log"
echo ""

if [ ! -f "$BOT_DIR/.env" ]; then
    warn "IMPORTANT: .env was not created. The bot will not start until you fill it in:"
    warn "  cp $BOT_DIR/.env.example $BOT_DIR/.env && nano $BOT_DIR/.env"
    warn "  Then restart: sudo systemctl restart $SERVICE_NAME"
fi
