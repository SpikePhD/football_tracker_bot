#!/bin/bash
# update.sh — Pull latest bot code and initialise any new bot_memory files.
# Usage: bash update.sh
# Run from the football_tracker_bot directory on the Raspberry Pi.

set -e
cd "$(dirname "$0")"

# Load deployment config (SERVICE_NAME) — created by install.sh
# Auto-bootstrap from .bot_config.example on first run after an update.
if [ ! -f .bot_config ]; then
    echo "⚠️  .bot_config not found — creating from .bot_config.example"
    cp .bot_config.example .bot_config
    echo "  ✔ Created .bot_config (edit it if your service name differs from marco_van_botten)"
fi
# shellcheck source=.bot_config.example
source .bot_config

echo "⬇️  Pulling latest code..."
git pull

echo ""
echo "🧠 Checking bot_memory/..."
mkdir -p bot_memory

# Add new default files here as the bot grows.
# Existing files are never overwritten.

if [ ! -f bot_memory/state.json ]; then
    echo '{"mode": "verbose"}' > bot_memory/state.json
    echo "  ✔ Created bot_memory/state.json"
else
    echo "  ✔ bot_memory/state.json already exists — keeping state"
fi

echo ""
echo "🔄 Restarting bot service ($SERVICE_NAME)..."
sudo systemctl restart "$SERVICE_NAME"

echo ""
echo "✅ Update complete."
