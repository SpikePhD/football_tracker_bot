#!/bin/bash
# update.sh — Pull latest bot code and initialise any new bot_memory files.
# Usage: bash update.sh
# Run from the football_tracker_bot directory on the Raspberry Pi.

set -e
cd "$(dirname "$0")"

# Load deployment config (SERVICE_NAME/GIT_BRANCH) — created by install.sh
# Auto-bootstrap from .env.deploy.example on first run after an update.
if [ ! -f .env.deploy ]; then
    echo "⚠️  .env.deploy not found — creating from .env.deploy.example"
    cp .env.deploy.example .env.deploy
    echo "  ✔ Created .env.deploy (edit SERVICE_NAME/GIT_BRANCH for your host if needed)"
fi
load_env_file_safely() {
    local env_file="$1"
    local tmp_file
    tmp_file="$(mktemp)"
    # Strip UTF-8 BOM from first line if present, then source.
    sed '1s/^\xEF\xBB\xBF//' "$env_file" > "$tmp_file"
    # shellcheck disable=SC1090
    source "$tmp_file"
    rm -f "$tmp_file"
}
load_env_file_safely .env.deploy

if [ ! -f config.json ]; then
    echo "⚠️  config.json not found — creating from config.example.json"
    cp config.example.json config.json
    echo "  ✔ Created config.json"
fi

echo "⬇️  Pulling latest code from $GIT_BRANCH..."
git pull origin "$GIT_BRANCH"

# Re-check committed defaults after pulling. Host-owned config.local.json is
# gitignored and intentionally never overwritten by updates.
if [ ! -f config.json ]; then
    echo "⚠️  config.json missing after pull — creating from config.example.json"
    cp config.example.json config.json
    echo "  ✔ Created config.json"
fi

echo ""
echo "🧠 Checking bot_memory/..."
mkdir -p bot_memory
mkdir -p bot_memory/logs
mkdir -p bot_memory/log_exports

# Add new default files here as the bot grows.
# Existing files are never overwritten.

if [ ! -f bot_memory/state.json ]; then
    echo '{"mode": "verbose"}' > bot_memory/state.json
    echo "  ✔ Created bot_memory/state.json"
else
    echo "  ✔ bot_memory/state.json already exists — keeping state"
fi

if [ ! -f bot_memory/goodmorning.json ]; then
    echo '{"enabled": true, "hour": 6, "minute": 30, "timezone": "Europe/Rome"}' > bot_memory/goodmorning.json
    echo "  Created bot_memory/goodmorning.json"
else
    echo "  bot_memory/goodmorning.json already exists - keeping state"
fi

if [ ! -f bot_memory/tennis_state.json ]; then
    echo '{"version": 2, "matches": {}}' > bot_memory/tennis_state.json
    echo "  Created bot_memory/tennis_state.json"
else
    echo "  bot_memory/tennis_state.json already exists - keeping state"
fi

echo ""
echo "🔄 Restarting bot service ($SERVICE_NAME)..."
if [ "${SKIP_SERVICE_RESTART:-0}" = "1" ]; then
    echo "Managed updater will restart application services."
else
    sudo systemctl restart "$SERVICE_NAME"
fi

echo ""
echo "✅ Update complete."
