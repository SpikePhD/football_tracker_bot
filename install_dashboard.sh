#!/bin/bash
# One-time dashboard/systemd integration for an existing bot installation.
set -euo pipefail

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CURRENT_USER="$(whoami)"

if [ ! -f "$BOT_DIR/.env.deploy" ]; then
    cp "$BOT_DIR/.env.deploy.example" "$BOT_DIR/.env.deploy"
fi
set -a
# shellcheck disable=SC1091
source "$BOT_DIR/.env.deploy"
set +a

: "${SERVICE_NAME:=marco_van_botten}"
: "${DASHBOARD_HOST:=0.0.0.0}"
: "${DASHBOARD_PORT:=8765}"
: "${DASHBOARD_SERVICE_NAME:=marco_van_botten_dashboard}"
: "${UPDATE_SERVICE_NAME:=marco_van_botten_update}"

for service in "$SERVICE_NAME" "$DASHBOARD_SERVICE_NAME" "$UPDATE_SERVICE_NAME"; do
    if [[ ! "$service" =~ ^[A-Za-z0-9_.@-]+$ ]] || [[ "$service" == -* ]]; then
        echo "Invalid systemd service name: $service" >&2
        exit 1
    fi
done
if [[ ! "$DASHBOARD_PORT" =~ ^[0-9]+$ ]] || [ "$DASHBOARD_PORT" -lt 1 ] || [ "$DASHBOARD_PORT" -gt 65535 ]; then
    echo "DASHBOARD_PORT must be between 1 and 65535." >&2
    exit 1
fi
SYSTEMCTL_BIN="$(command -v systemctl)"

mkdir -p "$BOT_DIR/bot_memory/logs"

sudo tee "/etc/systemd/system/$DASHBOARD_SERVICE_NAME.service" >/dev/null <<EOF
[Unit]
Description=Marco Van Botten Configuration Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$BOT_DIR
ExecStart=$BOT_DIR/.venv/bin/python $BOT_DIR/dashboard.py
Restart=on-failure
RestartSec=5
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

sudo tee "/etc/systemd/system/$UPDATE_SERVICE_NAME.service" >/dev/null <<EOF
[Unit]
Description=Managed update for Marco Van Botten
After=network-online.target

[Service]
Type=oneshot
User=$CURRENT_USER
WorkingDirectory=$BOT_DIR
ExecStart=/bin/bash $BOT_DIR/scripts/managed_update.sh
EOF

SUDOERS_FILE="/etc/sudoers.d/football_bot_dashboard_$CURRENT_USER"
sudo tee "$SUDOERS_FILE" >/dev/null <<EOF
$CURRENT_USER ALL=(root) NOPASSWD: $SYSTEMCTL_BIN restart $SERVICE_NAME
$CURRENT_USER ALL=(root) NOPASSWD: $SYSTEMCTL_BIN status $SERVICE_NAME
$CURRENT_USER ALL=(root) NOPASSWD: $SYSTEMCTL_BIN is-active $SERVICE_NAME
$CURRENT_USER ALL=(root) NOPASSWD: $SYSTEMCTL_BIN restart $DASHBOARD_SERVICE_NAME
$CURRENT_USER ALL=(root) NOPASSWD: $SYSTEMCTL_BIN status $DASHBOARD_SERVICE_NAME
$CURRENT_USER ALL=(root) NOPASSWD: $SYSTEMCTL_BIN is-active $DASHBOARD_SERVICE_NAME
$CURRENT_USER ALL=(root) NOPASSWD: $SYSTEMCTL_BIN start --no-block $UPDATE_SERVICE_NAME
EOF
sudo chmod 440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE"

sudo systemctl daemon-reload
sudo systemctl enable --now "$DASHBOARD_SERVICE_NAME"

echo "Dashboard installed: http://$(hostname -I | awk '{print $1}'):$DASHBOARD_PORT"
echo "Initial login: admin / admin"
echo "Use only on a trusted LAN/VPN unless protected by an HTTPS reverse proxy."
