#!/usr/bin/env bash
# Installs (or re-installs) two crontab entries:
#   - run_update.sh every 6 hours, so channels.json/data/channels_status.json
#     stay fresh without anyone needing to run the updater by hand.
#   - run_vod_update.sh once a day (04:00, off the 6-hourly job's own :00
#     slots), for the much heavier Kan VOD catalog crawl - see
#     run_vod_update.sh's own comment on why this needs nowhere near
#     6-hourly freshness.
# Safe to re-run: each replaces its own prior entry (matched by its own
# MARKER) rather than duplicating it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_FILE="$REPO_ROOT/data/update_channels.log"
MARKER="# idanplusplusplus-update-channels"
VOD_LOG_FILE="$REPO_ROOT/data/update_vod_kan.log"
VOD_MARKER="# idanplusplusplus-update-vod-kan"

CRON_LINE="0 */6 * * * \"$SCRIPT_DIR/run_update.sh\" >> \"$LOG_FILE\" 2>&1 $MARKER"
VOD_CRON_LINE="0 4 * * * \"$SCRIPT_DIR/run_vod_update.sh\" >> \"$VOD_LOG_FILE\" 2>&1 $VOD_MARKER"

( crontab -l 2>/dev/null | grep -vF "$MARKER" | grep -vF "$VOD_MARKER" || true; echo "$CRON_LINE"; echo "$VOD_CRON_LINE" ) | crontab -

echo "Installed crontab entries:"
echo "  $CRON_LINE"
echo "  $VOD_CRON_LINE"
echo "Logs will be appended to: $LOG_FILE and $VOD_LOG_FILE"
