#!/usr/bin/env bash
# Installs (or re-installs) a crontab entry that runs run_update.sh every
# 30 minutes, so channels.json/data/channels_status.json stay fresh without
# anyone needing to run the updater by hand. Safe to re-run: replaces its
# own prior entry (matched by MARKER) rather than duplicating it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="$REPO_ROOT/data/update_channels.log"
MARKER="# idanplusplusplus-update-channels"

CRON_LINE="*/30 * * * * \"$SCRIPT_DIR/run_update.sh\" >> \"$LOG_FILE\" 2>&1 $MARKER"

( crontab -l 2>/dev/null | grep -vF "$MARKER" || true; echo "$CRON_LINE" ) | crontab -

echo "Installed crontab entry:"
echo "  $CRON_LINE"
echo "Logs will be appended to: $LOG_FILE"
