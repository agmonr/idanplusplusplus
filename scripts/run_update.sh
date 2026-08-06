#!/usr/bin/env bash
# Pulls the latest idanplusplusplus code, runs update_channels.py, embeds
# the refreshed data into web/channel_viewer.html, and pushes the result -
# meant to be invoked periodically (see install_cron.sh) so the live site
# (channel_viewer.html is what's actually deployed, see publish_channel_
# data.py) stays current with no manual step in between. Scoped to this
# repo only - it does not touch the sibling Fishenzon/repo checkout that
# channels.json itself lives in (by request).
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

echo "Embedding fresh data into channel_viewer.html..."
python3 publish_channel_data.py

cd "$REPO_ROOT"
if git diff --quiet -- data/channels_status.json web/channel_viewer.html; then
  echo "No channel data changes - nothing to commit."
else
  git add data/channels_status.json web/channel_viewer.html
  git commit -m "Auto-update channel data ($(date -Iseconds))"
  git push
  echo "Pushed updated channel data."
fi
