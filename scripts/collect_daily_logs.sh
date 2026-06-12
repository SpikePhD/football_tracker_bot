#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE_NAME="${SERVICE_NAME:-marco_van_botten}"
TARGET_DATE="${1:-$(date -d yesterday +%F)}"

APP_LOG="$ROOT_DIR/bot_memory/logs/bot.log"
EXPORT_DIR="$ROOT_DIR/bot_memory/log_exports/daily/$TARGET_DATE"
mkdir -p "$EXPORT_DIR"

APP_EXPORT="$EXPORT_DIR/bot_app_${TARGET_DATE}.log"
JOURNAL_EXPORT="$EXPORT_DIR/journal_${SERVICE_NAME}_${TARGET_DATE}.log"
SUMMARY="$EXPORT_DIR/summary_${TARGET_DATE}.txt"

shopt -s nullglob
APP_LOG_FILES=("$ROOT_DIR"/bot_memory/logs/bot.log*)
if (( ${#APP_LOG_FILES[@]} > 0 )); then
  : > "$APP_EXPORT"
  for log_file in "${APP_LOG_FILES[@]}"; do
    grep "^\[$TARGET_DATE " "$log_file" >> "$APP_EXPORT" || true
  done
else
  printf 'App log not found: %s\n' "$APP_LOG" > "$APP_EXPORT"
fi

if command -v journalctl >/dev/null 2>&1; then
  journalctl -u "$SERVICE_NAME" \
    --since "$TARGET_DATE 00:00:00" \
    --until "$TARGET_DATE 23:59:59" \
    --no-pager > "$JOURNAL_EXPORT" || true
else
  printf 'journalctl not available on this host.\n' > "$JOURNAL_EXPORT"
fi

{
  printf 'Marco Van Botten daily log collection\n'
  printf 'date=%s\n' "$TARGET_DATE"
  printf 'generated_at=%s\n' "$(date -Iseconds)"
  printf 'service=%s\n' "$SERVICE_NAME"
  printf 'root=%s\n' "$ROOT_DIR"
  printf 'app_log=%s\n' "$APP_EXPORT"
  printf 'journal=%s\n' "$JOURNAL_EXPORT"
  printf 'app_log_lines=%s\n' "$(wc -l < "$APP_EXPORT" | tr -d ' ')"
  printf 'journal_lines=%s\n' "$(wc -l < "$JOURNAL_EXPORT" | tr -d ' ')"
  printf 'warning_error_count=%s\n' "$(grep -Eih 'WARNING|ERROR|CRITICAL|Traceback|Exception' "$APP_EXPORT" "$JOURNAL_EXPORT" 2>/dev/null | wc -l | tr -d ' ')"
} > "$SUMMARY"

tar -czf "$ROOT_DIR/bot_memory/log_exports/daily/logs_${TARGET_DATE}.tar.gz" -C "$EXPORT_DIR" .

printf 'Daily logs collected in %s\n' "$EXPORT_DIR"
printf 'Archive: %s\n' "$ROOT_DIR/bot_memory/log_exports/daily/logs_${TARGET_DATE}.tar.gz"
