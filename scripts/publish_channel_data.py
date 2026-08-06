#!/usr/bin/env python3
"""
Embeds data/channels_status.json into web/channel_viewer.html's
<script id="channel-data"> block, replacing whatever was there before.

This is the actual "build" step that makes a fresh update_channels.py run
visible on the live site: channel_viewer.html is committed pre-built, with
the channel data baked directly into the page rather than fetched at
runtime. web/template.html is left untouched - it always keeps the literal
__CHANNEL_DATA_JSON__ placeholder instead of real data, by convention.
"""
import io
import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.join(SCRIPT_DIR, "..")
DATA_PATH = os.path.join(REPO_ROOT, "data", "channels_status.json")
VIEWER_PATH = os.path.join(REPO_ROOT, "web", "channel_viewer.html")

TAG_RE = re.compile(
    r'(<script type="application/json" id="channel-data">)(.*?)(</script>)',
    re.S,
)


def main():
    with io.open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Matches update_channels.py's embedded format exactly (single line,
    # spaces after separators) - NOT the pretty indent=2 the report file
    # itself is written with.
    compact = json.dumps(data, ensure_ascii=False)

    with io.open(VIEWER_PATH, "r", encoding="utf-8") as f:
        html = f.read()

    # Replacement as a function, not a plain string - a plain string would
    # have backslash-escapes in the JSON (e.g. \") misinterpreted as regex
    # backreferences by re.sub.
    new_html, count = TAG_RE.subn(lambda m: m.group(1) + compact + m.group(3), html, count=1)
    if count != 1:
        raise SystemExit("channel-data script tag not found in " + VIEWER_PATH)

    with io.open(VIEWER_PATH, "w", encoding="utf-8") as f:
        f.write(new_html)
    print("Embedded fresh channel data ({0} bytes) into {1}".format(len(compact), VIEWER_PATH))


if __name__ == "__main__":
    main()
