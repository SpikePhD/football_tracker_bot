#!/bin/bash
set -Eeuo pipefail

# --- Configuration ---
# Absolute path to the directory where your bot's code is cloned
BOT_DIR="/home/lucac/football_tracker_bot"

# Path to the python executable WITHIN your virtual environment
VENV_PYTHON_PATH="$BOT_DIR/.venv/bin/python"

# Path to the pip executable WITHIN your virtual environment
VENV_PIP_PATH="$BOT_DIR/.venv/bin/pip"

# The Git branch you want to pull updates from (usually "main" or "master")
GIT_BRANCH="main"

# The name of your systemd service for the bot
SYSTEMD_SERVICE_NAME="marco_van_botten"

# Log file for this update script
LOG_FILE="$BOT_DIR/auto_update.log"

# --- Functions ---
log_echo() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# --- Main Script ---
log_echo "--- Starting Bot Auto-Update Check ---"

cd "$BOT_DIR" || { log_echo "ERROR: Failed to cd to bot directory '$BOT_DIR'. Exiting."; exit 1; }

log_echo "Fetching remote changes for branch '$GIT_BRANCH'..."
if ! git fetch origin "$GIT_BRANCH"; then
    log_echo "ERROR: 'git fetch origin $GIT_BRANCH' failed. Exiting."
    exit 1
fi
log_echo "Git fetch completed."

LOCAL_SHA=$(git rev-parse HEAD)
REMOTE_SHA=$(git rev-parse "origin/$GIT_BRANCH")

if [ "$LOCAL_SHA" == "$REMOTE_SHA" ]; then
    log_echo "Bot is already up-to-date with 'origin/$GIT_BRANCH' (Commit: $LOCAL_SHA)."
else
    log_echo "New updates detected on 'origin/$GIT_BRANCH'."
    log_echo "Current local commit: $LOCAL_SHA"
    log_echo "Latest remote commit: $REMOTE_SHA"

    # Fail-safe: stash local changes before pulling
    if ! git diff --quiet || ! git diff --cached --quiet; then
        log_echo "Local changes detected. Stashing before update."
        git stash push -u -m "auto-update-$(date +%s)" || true
    fi

    log_echo "Attempting to pull changes from 'origin/$GIT_BRANCH'..."
    if ! git pull --ff-only origin "$GIT_BRANCH"; then
        log_echo "WARNING: 'git pull --ff-only' failed. Forcing hard reset to remote."
        git reset --hard "origin/$GIT_BRANCH"
    fi
    log_echo "Repository now matches origin/$GIT_BRANCH."

    # Optional: Check if requirements.txt changed and re-install dependencies
    if git diff --name-only HEAD@{1} HEAD | grep -q "^requirements\.txt$"; then
        log_echo "'requirements.txt' changed. Re-installing dependencies..."
        if ! "$VENV_PIP_PATH" install -r requirements.txt; then
            log_echo "ERROR: Failed to install dependencies from requirements.txt. Bot might not start correctly."
        else
            log_echo "Dependencies re-installed successfully."
        fi
    else
        log_echo "'requirements.txt' unchanged, skipping dependency re-install."
    fi

    log_echo "Restarting '$SYSTEMD_SERVICE_NAME' service..."
    if ! sudo systemctl restart "$SYSTEMD_SERVICE_NAME"; then
        log_echo "ERROR: Failed to restart '$SYSTEMD_SERVICE_NAME'. Check service status and logs manually."
        exit 1
    fi

    sleep 5
    log_echo "Status of '$SYSTEMD_SERVICE_NAME' after restart attempt:"
    sudo systemctl status "$SYSTEMD_SERVICE_NAME" --no-pager | tee -a "$LOG_FILE"

    log_echo "Bot update process finished successfully."
fi

log_echo "--- Bot Auto-Update Check Finished ---"
echo "" >> "$LOG_FILE"
