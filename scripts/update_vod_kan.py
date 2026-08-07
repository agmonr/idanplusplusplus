#!/usr/bin/env python3
"""
Builds a searchable catalog of Kan (כאן) VOD shows/episodes, entirely from
Kan's own live JSON APIs - no dependency on any external repo (the
Fishenzon addon's own equivalent search feature instead relies on a
manually-uploaded, months-stale catalog file from a separate GitHub repo;
this script builds and owns its own instead).

Two-stage crawl:
  1. Discover shows: mobapi.kan.org.il/api/mobile/subClass?id={category},
     paginated (from=1,201,401,...) until a page returns < 200 entries.
     Category IDs are Kan's own numeric "subClass" identifiers - see
     KAN_VOD_CATEGORIES below for which ones and why.
  2. Per show: mobapi.kan.org.il/api/mobile/program?id={programId},
     paginated via the response's own play_next_feed_url (&subid=...), to
     get every episode with season/episode/channel/air-date/playable link.

Writes data/vod_kan.json (pretty, for readability/diffs) and
web/vod_kan.json (compact, what the page actually fetches) - a flat array
of episodes, one row per episode, searchable by show name/season/year/
channel. Not embedded into channel_viewer.html like live-channel data -
this dataset is much larger and only needed once the VOD search panel is
actually opened.
"""
import io
import json
import os
import re
import sys
import time

import requests

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 15
REFERER = "https://www.kan.org.il/"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.join(SCRIPT_DIR, "..")
OUT_PATH = os.path.join(REPO_ROOT, "data", "vod_kan.json")
WEB_OUT_PATH = os.path.join(REPO_ROOT, "web", "vod_kan.json")

SUBCLASS_API = "https://mobapi.kan.org.il/api/mobile/subClass"
PROGRAM_API = "https://mobapi.kan.org.il/api/mobile/program"

# Kan's own "subClass" category IDs - source: the Fishenzon Kodi addon's
# kan.py (GetCategoriesList), used ONLY to learn these two numeric IDs.
# Everything else here is fetched live from Kan's own API; nothing depends
# on or is copied from that addon/repo. "כל התוכניות" ("all programs") is
# Kan's own general kan11 catalog; "דיגיטל" is their separate digital-only
# lobby. Kids content (kankids.org.il, a separate domain entirely),
# "תוכניות אקטואליה" (current affairs - no subClass id, HTML-scraped in
# the addon instead of a clean API), and the archive (its own separate
# flow) are out of scope for this first version.
KAN_VOD_CATEGORIES = {
    "כל התוכניות": 4444,
    "דיגיטל": 4464,
}

# Politeness pacing between requests - this crawl is one request per show
# (plus one per extra page of episodes for long-running shows) across
# potentially several hundred shows, so this adds up; a daily cron run has
# no reason to hurry.
SUBCLASS_PAGE_DELAY_S = 1
SHOW_DELAY_S = 0.5
EPISODE_PAGE_DELAY_S = 0.3

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Referer": REFERER})


def log(*a):
    print(*a, file=sys.stderr)


PROGRAM_ID_RE = re.compile(r"/p-(\d+)")


def discover_show_ids():
    """Paginates every category in KAN_VOD_CATEGORIES, returns a dict of
    program_id -> show title (deduped - the same show can appear in more
    than one category, e.g. a digital show also listed under "all
    programs")."""
    shows = {}
    for cat_name, cat_id in KAN_VOD_CATEGORIES.items():
        page_start = 1
        while True:
            try:
                resp = session.get(SUBCLASS_API, params={"from": page_start, "id": cat_id}, timeout=TIMEOUT)
                resp.raise_for_status()
                entries = resp.json().get("entry") or []
            except Exception as ex:  # noqa: BLE001 - one bad page shouldn't lose the rest of the catalog
                log("  subClass[{0}] from={1} failed: {2}".format(cat_name, page_start, ex))
                break
            for entry in entries:
                href = (entry.get("link") or {}).get("href", "")
                m = PROGRAM_ID_RE.search(href)
                if m:
                    shows[m.group(1)] = (entry.get("title") or "").strip()
            log("  subClass[{0}] from={1}: {2} entries".format(cat_name, page_start, len(entries)))
            if len(entries) < 200:
                break
            page_start += 200
            time.sleep(SUBCLASS_PAGE_DELAY_S)
    return shows


def _stream_from_entry(entry):
    """A youtube-id becomes a real watch URL, an http(s) src is used
    directly - same rule as the addon's _mobStreamFromEntry. "content"
    lives at the entry's own top level, NOT under "extensions" - easy to
    get wrong, confirmed by a live response that had extensions.content
    == {}.

    Unlike the addon: confirmed live that content.src is EMPTY for most
    real episodes (type video/hls, src "") - the addon resolves those
    through a much heavier page-scrape-and-resolve path (GetPlayerKanUrl,
    Kaltura widget resolution, HLS/DASH extraction from the episode's own
    page - the same class of work update_channels.py already does for
    live channels, just per-episode instead of per-channel, which isn't
    viable across a whole catalog on a daily crawl). For anything without
    a directly-usable src, this falls back to the episode's own kan.org.il
    page (entry.link.href, always present) - opened in a new tab rather
    than played in-app. Every episode ends up with SOME usable link this
    way; skipping the ones without a direct stream would have dropped
    nearly the entire catalog."""
    content = entry.get("content") or {}
    ctype = content.get("type", "")
    src = (content.get("src") or "").strip()
    if ctype == "youtube-id" and src:
        return "youtube", "https://www.youtube.com/watch?v={0}".format(src)
    if src.startswith("http"):
        return "stream", src
    page_url = ((entry.get("link") or {}).get("href") or "").split("?")[0]
    if page_url.startswith("http"):
        return "page", page_url
    return None, None


def fetch_show_episodes(program_id, show_title):
    episodes = []
    url = "{0}?id={1}".format(PROGRAM_API, program_id)
    seen_ids = set()
    while url:
        try:
            resp = session.get(url, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as ex:  # noqa: BLE001 - one bad show/page shouldn't lose the rest
            log("  program[{0}] failed: {1}".format(program_id, ex))
            break
        entries = data.get("entry") or []
        next_url = None
        for entry in entries:
            eid = entry.get("id")
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            ext = entry.get("extensions") or {}
            props = ext.get("analyticsCustomProperties") or {}
            play_type, play_link = _stream_from_entry(entry)
            # play_next_feed_url is read regardless of whether THIS entry
            # is playable - a page with only DRM/dead entries can still be
            # followed by a page that has real ones.
            next_url = ext.get("play_next_feed_url") or next_url
            if not play_link:
                continue
            published = entry.get("published") or ""
            year = int(published[:4]) if published[:4].isdigit() else None
            episodes.append({
                "show": props.get("show_name") or show_title,
                "season": props.get("season"),
                "episode": props.get("episode_number"),
                "title": (entry.get("title") or "").strip(),
                "description": (entry.get("description") or entry.get("summary") or "").strip(),
                "channel": props.get("channel_name") or "",
                "year": year,
                "type": play_type,
                "link": play_link,
                "image": (ext.get("audio_player_background_image") or "").split("?")[0],
            })
        url = next_url
        if url:
            time.sleep(EPISODE_PAGE_DELAY_S)
    return episodes


def main():
    log("Discovering Kan VOD shows...")
    shows = discover_show_ids()
    log("Found {0} shows.".format(len(shows)))

    all_episodes = []
    for i, (program_id, title) in enumerate(shows.items()):
        episodes = fetch_show_episodes(program_id, title)
        log("  [{0}/{1}] {2}: {3} episode(s)".format(i + 1, len(shows), title, len(episodes)))
        all_episodes.extend(episodes)
        time.sleep(SHOW_DELAY_S)

    log("Total episodes: {0}".format(len(all_episodes)))
    with io.open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_episodes, f, ensure_ascii=False, indent=2)
    # Compact - this is the copy the web page actually fetches over the wire.
    with io.open(WEB_OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(all_episodes, f, ensure_ascii=False, separators=(",", ":"))
    log("Wrote {0} episodes to {1} and {2}".format(len(all_episodes), OUT_PATH, WEB_OUT_PATH))


if __name__ == "__main__":
    main()
