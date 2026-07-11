#!/bin/bash
# Managed update entry point. update.sh remains the canonical pull/migration logic.
set -euo pipefail

BOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BOT_DIR"

set -a
# shellcheck disable=SC1091
source .env.deploy
set +a

: "${DASHBOARD_SERVICE_NAME:=marco_van_botten_dashboard}"

SKIP_SERVICE_RESTART=1 bash "$BOT_DIR/update.sh"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl restart "$DASHBOARD_SERVICE_NAME"
