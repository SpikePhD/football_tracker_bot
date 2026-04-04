#!/bin/bash
# update.sh — Pull latest bot code and initialise any new bot_memory files.
# Usage: bash update.sh
# Run from the football_tracker_bot directory on the Raspberry Pi.

set -e
cd "$(dirname "$0")"

echo "⬇️  Pulling latest code..."
git pull

echo ""
echo "🧠 Checking bot_memory/..."
mkdir -p bot_memory

# Add new default files here as the bot grows.
# Existing files are never overwritten.

if [ ! -f bot_memory/state.json ]; then
    echo '{"silent": false}' > bot_memory/state.json
    echo "  ✔ Created bot_memory/state.json"
else
    echo "  ✔ bot_memory/state.json already exists — keeping Pi state"
fi

echo ""
echo "🔄 Restarting bot service..."
sudo systemctl restart marco_van_botten

echo ""
echo "✅ Update complete."
