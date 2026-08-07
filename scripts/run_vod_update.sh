#!/usr/bin/env bash
# Pulls the latest idanplusplusplus code, runs update_vod_kan.py, and
# pushes the result - meant to be invoked daily (see install_cron.sh),
# separately from the 6-hourly live-channel update (own log/cron marker,
# own commit) since this crawl is far heavier (one request per show,
# several hundred shows) and the data it produces doesn't need anywhere
# near that freshness. Fully self-contained, same as update_channels.py:
# everything comes from Kan's own live API, nothing depends on the
# separate Fishenzon/repo distribution repo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== $(date -Iseconds) ==="

cd "$REPO_ROOT"
echo "Pulling latest idanplusplusplus..."
git pull --ff-only

cd "$SCRIPT_DIR"
echo "Running update_vod_kan.py..."
python3 update_vod_kan.py

cd "$REPO_ROOT"
if git diff --quiet -- data/vod_kan.json web/vod_kan.json; then
  echo "No VOD catalog changes - nothing to commit."
else
  git add data/vod_kan.json web/vod_kan.json
  git commit -m "Auto-update Kan VOD catalog ($(date -Iseconds))"
  git push
  echo "Pushed updated VOD catalog."
fi
