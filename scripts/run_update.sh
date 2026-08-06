#!/usr/bin/env bash
# Pulls the latest idanplusplusplus code, then runs update_channels.py -
# meant to be invoked periodically (see install_cron.sh), so this repo's
# own copy of the script/data stays current before each run rather than
# drifting from whatever was checked out when the cron job was installed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== $(date -Iseconds) ==="

cd "$REPO_ROOT"
echo "Pulling latest idanplusplusplus..."
git pull --ff-only

cd "$SCRIPT_DIR"
echo "Running update_channels.py..."
python3 update_channels.py
