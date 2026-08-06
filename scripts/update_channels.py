#!/usr/bin/env python3
"""
Refreshes TV + radio channel stream data in data/channels.json straight
from the broadcasters themselves, and writes a grouped status/EPG report
used by the channel-viewer static page.

data/channels.json started as a one-time copy of the real Kodi addon
distribution's channels.json (Fishenzon/repo, zips/plugin.video.idanplus/
channels.json) but is now this repo's own independent copy - updates here
no longer read from or write back to that repo, so link-refresh fixes made
here only benefit the web viewer, not the actual Kodi addon anymore. That
was a deliberate decoupling: this tool used to require a sibling checkout
of Fishenzon/repo to exist on whatever host it ran on, which broke on any
machine without one.

Usage:
    python3 update_channels.py [--dry-run] [--channels-path PATH]

For channels whose module already knows how to re-derive the current stream
from the station's own site/API (14tv, sport5, hidabroot, glz, 100fm,
1064fm, keshet's ticket service), this actually re-resolves the link and
updates the stale fallback stored in channels.json. For channels with no
known discovery mechanism (kan, reshet, generic "tv"/"radio" links), it only
validates that the stored link is still reachable and reports anything
broken -- it does not guess.

Variant grouping: entries whose name ends in " - גיבוי[ N]" or
" - לקויי שמיעה" are treated as backup/accessibility variants of the base
channel (e.g. "כאן 11 - גיבוי" belongs to "כאן 11"), and folded into a single
group in the report so the viewer page can show one card per logical channel.
"""
import argparse
import io
import json
import os
import re
import sys
import time
import zipfile

import requests

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
TIMEOUT = 15

EPG_URL = "https://raw.githubusercontent.com/fishenzon-epg/EPG/main/epg.json.zip"
MAKO_ENTITLEMENTS = "https://mass.mako.co.il/ClicksStatistics/entitlementsServicesV2.jsp"
MAKO_HOST = "https://mako-streaming.akamaized.net"
GLZ_API = "https://glz.co.il/umbraco/api/"

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})


def log(*a):
    print(*a, file=sys.stderr)


def check_reachable(url, headers=None, method="GET"):
    """Best-effort reachability check for a stream/page URL. Returns (ok, detail)."""
    try:
        h = {"User-Agent": USER_AGENT}
        if headers:
            h.update(headers)
        resp = session.request(method, url, headers=h, timeout=TIMEOUT,
                                allow_redirects=True, stream=True)
        resp.close()
        ok = resp.status_code < 400
        return ok, "HTTP {0}".format(resp.status_code)
    except requests.RequestException as ex:
        return False, "{0}: {1}".format(type(ex).__name__, ex)


# ---------------------------------------------------------------------------
# Per-module resolvers. Each returns a dict:
#   {"status": "ok"|"updated"|"broken", "detail": str, "resolved_link": str|None,
#    "new_fallback": str|None (only set when the stored fallback link is stale)}
# ---------------------------------------------------------------------------

def resolve_generic_static(channel):
    """Pure-static entries (kan, reshet, generic tv/radio links): verify the
    stored link responds. If a 'regex' is present (tv.py/radio.py pattern),
    fetch the page and apply it first, exactly like the addon does at play
    time, falling back to 'direct' if nothing matches."""
    ld = channel["linkDetails"]
    link = ld["link"]
    headers = {}
    if ld.get("referer"):
        headers["Referer"] = ld["referer"]

    regex = ld.get("regex")
    if regex:
        try:
            resp = session.get(link, headers=headers, timeout=TIMEOUT)
            resp.raise_for_status()
            match = re.search(regex, resp.text, re.S)
            stream = match.group(1) if match else ld.get("direct")
            if not stream:
                return {"status": "broken", "detail": "page fetched but regex found nothing and no 'direct' fallback", "resolved_link": None}
        except requests.RequestException as ex:
            return {"status": "broken", "detail": str(ex), "resolved_link": None}
    else:
        stream = link

    if stream.startswith("//"):
        stream = "https:" + stream
    ok, detail = check_reachable(stream, headers=headers)
    return {
        "status": "ok" if ok else "broken",
        "detail": detail if not regex else "live page + regex still resolves ({0})".format(detail),
        "resolved_link": stream if ok else None,
    }


def resolve_hidabroot(channel):
    ld = channel["linkDetails"]
    page_url = ld["link"]
    try:
        resp = session.get(page_url, timeout=TIMEOUT)
        resp.raise_for_status()
        match = re.search(r'<source\s*?src="(.*?)"', resp.text, re.S)
        if not match:
            return {"status": "broken", "detail": "page fetched but no <source> tag found", "resolved_link": None}
        stream = match.group(1)
        ok, detail = check_reachable(stream)
        return {
            "status": "ok" if ok else "broken",
            "detail": "live page + regex still resolves ({0})".format(detail),
            "resolved_link": stream if ok else None,
        }
    except requests.RequestException as ex:
        return {"status": "broken", "detail": str(ex), "resolved_link": None}


def _resolve_via_fallback_field(channel, resolve_stream_fn, source_name):
    """Shared shape for modules that resolve a live URL from some API/page
    and fall back to the static linkDetails['link'] on failure, updating that
    fallback in-place when it turns out to be stale."""
    ld = channel["linkDetails"]
    fallback = ld.get("link")
    try:
        stream = resolve_stream_fn(ld)
        if not stream:
            raise ValueError("resolver returned no stream")
        ok, detail = check_reachable(stream)
        if not ok:
            return {"status": "broken", "detail": "resolved {0} but unreachable: {1}".format(stream, detail), "resolved_link": None}
        updated = stream != fallback
        return {
            "status": "updated" if updated else "ok",
            "detail": "resolved live via {0}".format(source_name) + (" (fallback link was stale)" if updated else ""),
            "resolved_link": stream,
            "new_fallback": stream if updated else None,
        }
    except (requests.RequestException, ValueError, KeyError, IndexError) as ex:
        log("  {0} failed ({1}), falling back to static link check".format(source_name, ex))
        return resolve_generic_static(channel)


def resolve_14tv(channel):
    ld = channel["linkDetails"]
    if not ld.get("ch"):
        return resolve_generic_static(channel)

    def _resolve(ld):
        resp = session.get(ld["ch"], headers={"x-tenant-id": "channel14"}, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("vod", {}).get("hlsStream")

    return _resolve_via_fallback_field(channel, _resolve, "channel14 API")


def resolve_sport5(channel):
    ld = channel["linkDetails"]
    if not ld.get("ch"):
        return resolve_generic_static(channel)

    def _resolve(ld):
        resp = session.get(
            "https://radio.sport5.co.il/data/data.json?v={0}".format(int(time.time() * 1000)),
            headers={"Referer": "https://radio.sport5.co.il"}, timeout=TIMEOUT)
        resp.raise_for_status()
        stream = resp.json().get(ld["ch"])
        return stream.replace("https://nekot.sport5.co.il:10000?", "") if stream else None

    return _resolve_via_fallback_field(channel, _resolve, "sport5 data.json")


def resolve_100fm(channel):
    ld = channel["linkDetails"]
    if not ld.get("ch"):
        return resolve_generic_static(channel)

    def _resolve(ld):
        resp = session.get(ld["ch"], timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()["stations"][0]["audio"]

    return _resolve_via_fallback_field(channel, _resolve, "100fm app API")


def resolve_1064fm(channel):
    ld = channel["linkDetails"]
    if not ld.get("ch"):
        return resolve_generic_static(channel)

    def _resolve(ld):
        resp = session.get(ld["ch"], timeout=TIMEOUT)
        resp.raise_for_status()
        match = re.search(r'"webapp\.broadcast_link":"(.*?)"', resp.text)
        return match.group(1).replace("\\u002F", "/") if match else None

    return _resolve_via_fallback_field(channel, _resolve, "1064fm broadcast link")


def resolve_glz(channel):
    ld = channel["linkDetails"]
    if not ld.get("rootId"):
        return resolve_generic_static(channel)

    def _resolve(ld):
        resp = session.get("{0}player/getplayerdata?rootId={1}".format(GLZ_API, ld["rootId"]), timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("liveBroadcast", {}).get("fileUrl")

    ld_compat = dict(ld)
    ld_compat.setdefault("link", ld.get("live"))
    channel = dict(channel, linkDetails=ld_compat)
    return _resolve_via_fallback_field(channel, _resolve, "glz player API")


def resolve_keshet(channel):
    """Verify end-to-end via mako's real entitlements ticket service (no
    session/cookies required). We can't discover new channel IDs, but a real
    ticket + CDN fetch proves the stored relative path is still valid."""
    ld = channel["linkDetails"]
    path = ld["link"]
    host = ld.get("host", MAKO_HOST)
    cdn = ld.get("cdn", "AKAMAI")
    try:
        resp = session.get(MAKO_ENTITLEMENTS, params={"et": "ngt", "lp": path, "rv": cdn}, timeout=TIMEOUT)
        resp.raise_for_status()
        result = resp.json()
        if result.get("caseId") != "1" or not result.get("tickets"):
            return {"status": "broken", "detail": "entitlements service refused: {0}".format(result), "resolved_link": None}
        ticket = result["tickets"][0]["ticket"]
        sep = "&" if "?" in path else "?"
        full_url = "{0}{1}{2}{3}".format(host, path, sep, ticket)
        ok, detail = check_reachable(full_url)
        return {
            "status": "ok" if ok else "broken",
            "detail": "entitlements ticket issued, manifest check: {0}".format(detail),
            "resolved_link": full_url if ok else None,
            # The ticket above expires in ~15 min (mako's own entitlements
            # service says so via st/exp fields in the token) -- baking it
            # into a report that might be viewed hours later would go stale.
            # The raw un-ticketed path + host/cdn let the viewer page fetch
            # its OWN fresh ticket client-side right before playing (the
            # entitlements endpoint sends Access-Control-Allow-Origin: *, so
            # a plain browser fetch works with no server needed).
            "refresh": {"kind": "keshet", "path": path, "host": host, "cdn": cdn},
        }
    except (requests.RequestException, ValueError) as ex:
        return {"status": "broken", "detail": str(ex), "resolved_link": None}


RESOLVERS = {
    "hidabroot": resolve_hidabroot,
    "14tv": resolve_14tv,
    "sport5": resolve_sport5,
    "keshet": resolve_keshet,
    "100fm": resolve_100fm,
    "1064fm": resolve_1064fm,
    "glz": resolve_glz,
}


def resolve_channel(channel_id, channel):
    module = channel.get("module")
    resolver = RESOLVERS.get(module, resolve_generic_static)
    try:
        result = resolver(channel)
    except Exception as ex:  # noqa: BLE001 - last-resort safety net per channel
        result = {"status": "broken", "detail": "resolver crashed: {0}".format(ex), "resolved_link": None}
    result["channel_id"] = channel_id
    result["module"] = module
    return result


# ---------------------------------------------------------------------------
# EPG
# ---------------------------------------------------------------------------
# Own per-broadcaster EPG resolvers (EPG_RESOLVERS below), same dispatch
# shape as the stream RESOLVERS above but keyed by an independent source id
# rather than module - a module's stream endpoint doesn't necessarily cover
# all of that module's channels for EPG (e.g. mako only has keshet 12's
# schedule, not keshet's other channels). fishenzon-epg (EPG_URL) is kept as
# an explicit, temporary fallback layer underneath our own sources - see
# ENABLE_FISHENZON_FALLBACK - until enough of our own sources exist to
# match its coverage; our own sources always win when they succeed.

MAKO_EPG_URL = "https://www.mako.co.il/AjaxPage?jspName=EPGResponse.jsp"
ENABLE_FISHENZON_FALLBACK = True


def _now_playing_from_schedule(schedule):
    """schedule: {tvgID: [{"name","start","end"}, ...]} (epoch seconds,
    not necessarily sorted/filtered) -> {tvgID: {"name","start","end"}}
    for whichever program's start/end window contains right now. Shared by
    every EPG source (including fetch_fishenzon_epg) instead of each
    reimplementing "find the current program" independently."""
    now = int(time.time())
    now_playing = {}
    for tvg_id, programs in schedule.items():
        for program in programs:
            if program["start"] <= now < program["end"]:
                now_playing[tvg_id] = program
                break
    return now_playing


def epg_mako():
    """Keshet 12's own schedule feed - the same one iptv-org/epg's
    mako.co.il grabber uses. No query params, no special headers. Covers
    only tvgID "12" (keshet's other channels - 24/erets/savri - have no
    known EPG source). Response already spans today+tomorrow.

    NOTE: seen returning HTTP 403 from some networks (bot-blocking is
    commonly IP-reputation-based) - if this consistently fails in
    production, check the log for "EPG[mako] failed" and investigate
    headers/Referer, or accept this source may not be viable from that
    host."""
    resp = session.get(MAKO_EPG_URL, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    programs = data.get("programs") or []
    if not programs:
        raise ValueError("mako EPG response had no programs")
    schedule = []
    for item in programs:
        start = item["StartTimeUTC"] / 1000.0
        schedule.append({
            "name": item["ProgramName"],
            "start": start,
            "end": start + item["DurationMs"] / 1000.0,
        })
    return {"12": schedule}


EPG_RESOLVERS = {
    "mako": epg_mako,
}


def fetch_now_playing():
    """Runs every EPG_RESOLVERS source, merging each into a combined
    {tvgID: {"name","start","end"}} now-playing dict. Each source's
    failure is caught individually (mirroring resolve_channel's per-
    channel isolation on the stream side) so one broken source never
    affects the others."""
    combined = {}
    for source_name, fetch_fn in EPG_RESOLVERS.items():
        try:
            schedule = fetch_fn()
            picked = _now_playing_from_schedule(schedule)
            combined.update(picked)
            log("  EPG[{0}]: {1} tvgID(s) currently airing".format(source_name, len(picked)))
        except Exception as ex:  # noqa: BLE001 - one broken source must not affect the others
            log("  EPG[{0}] failed: {1}".format(source_name, ex))
    return combined


def fetch_fishenzon_epg():
    """Third-party fallback (see ENABLE_FISHENZON_FALLBACK) - mirrors
    resources/lib/epg.py's GetNowEPG() data source. Raises on failure,
    same convention as the epg_<source>() resolvers above, so callers
    handle it identically."""
    resp = session.get(EPG_URL, timeout=30)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".json"))
        return json.loads(zf.read(name).decode("utf-8"))


def load_previous_now_playing(report_path):
    """Best-effort fallback for fetch_now_playing() returning None: pulls
    whatever now_playing values were embedded in the LAST successful
    report, keyed back by tvgID, so a transient EPG outage freezes each
    channel's displayed program at its last known value instead of
    blanking all of them - see fetch_now_playing's own comment."""
    try:
        with io.open(report_path, "r", encoding="utf-8") as f:
            previous = json.load(f)
    except Exception:  # noqa: BLE001 - no previous report yet, or unreadable; nothing to fall back to
        return {}
    now_playing = {}
    for group in previous.get("tv", []) + previous.get("radio", []):
        variants = [group.get("primary"), group.get("accessibility")] + group.get("backups", [])
        for variant in variants:
            if not variant:
                continue
            tvg_id, name = variant.get("tvgID"), variant.get("now_playing")
            if tvg_id and name:
                now_playing[tvg_id] = {"name": name}
    return now_playing


# ---------------------------------------------------------------------------
# Variant grouping (main / גיבוי N / לקויי שמיעה -> one logical channel)
# ---------------------------------------------------------------------------

ACCESSIBILITY_RE = re.compile(r"^(.*?)\s*-\s*לקויי שמיעה$")
BACKUP_RE = re.compile(r"^(.*?)\s*-\s*גיבוי(?:\s*(\d+))?$")


def classify_variant(name):
    m = ACCESSIBILITY_RE.match(name)
    if m:
        return m.group(1).strip(), "accessibility", None
    m = BACKUP_RE.match(name)
    if m:
        return m.group(1).strip(), "backup", int(m.group(2)) if m.group(2) else 1
    return name.strip(), "primary", None


def group_channels(entries):
    """entries: list of channel dicts (channel_id, name, image, module,
    tvgID, index, status, resolved_link, now_playing). Returns grouped list."""
    groups = {}
    order = []
    for entry in entries:
        base, kind, backup_n = classify_variant(entry["name"])
        variant = dict(entry, kind=kind, backupN=backup_n)
        if base not in groups:
            groups[base] = {"name": base, "primary": None, "backups": [], "accessibility": None}
            order.append(base)
        g = groups[base]
        if kind == "primary":
            g["primary"] = variant
        elif kind == "backup":
            g["backups"].append(variant)
        else:
            g["accessibility"] = variant

    result = []
    for base in order:
        g = groups[base]
        if g["primary"] is None:
            # No entry without a suffix (e.g. only "X - גיבוי" exists) -- promote one.
            if g["backups"]:
                g["backups"].sort(key=lambda b: b["backupN"] or 1)
                g["primary"] = g["backups"].pop(0)
            else:
                g["primary"] = g["accessibility"]
                g["accessibility"] = None
        g["backups"].sort(key=lambda b: b["backupN"] or 1)
        p = g["primary"]
        variants_status = [p["status"]] + [b["status"] for b in g["backups"]]
        group_status = "ok" if any(s in ("ok", "updated") for s in variants_status) else "broken"
        result.append({
            "key": base,
            "name": base,
            "index": p["index"] if isinstance(p.get("index"), int) else 999,
            "module": p["module"],
            "status": group_status,
            "primary": p,
            "backups": g["backups"],
            "accessibility": g["accessibility"],
        })
    result.sort(key=lambda g: (g["index"], g["name"]))
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_channels_path = os.path.join(script_dir, "..", "data", "channels.json")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channels-path", default=default_channels_path,
                         help="Path to channels.json (default: data/channels.json in this repo)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write channels.json, just report")
    parser.add_argument("--report-out", default=None, help="Where to write the combined grouped status/EPG JSON for the viewer page")
    args = parser.parse_args()

    channels_path = os.path.abspath(args.channels_path)
    report_path = args.report_out or os.path.join(script_dir, "..", "data", "channels_status.json")

    if not os.path.isfile(channels_path):
        raise SystemExit(
            "channels.json not found at {0} (from --channels-path {1!r}).".format(
                channels_path, args.channels_path)
        )

    # newline="" preserves the file's original CRLF line endings so in-place
    # text substitutions below don't spuriously touch every line.
    with io.open(channels_path, "r", encoding="utf-8", newline="") as f:
        raw_text = f.read()
    channels = json.loads(raw_text)

    watchable_ids = [cid for cid, c in channels.items() if c.get("type") in ("tv", "radio")]
    log("Found {0} TV+radio channels to check.".format(len(watchable_ids)))

    # Floor-then-layer: start from whatever the previous report had (so any
    # tvgID a source doesn't cover this run - because it failed, or was
    # never covered - keeps its last known value instead of going blank),
    # then let progressively more-trusted sources override it: fishenzon
    # (transitional third-party fallback, see ENABLE_FISHENZON_FALLBACK)
    # first, then our own EPG_RESOLVERS sources, which always win.
    now_playing = load_previous_now_playing(report_path)
    log("EPG: {0} tvgID(s) carried over from the previous report as a floor.".format(len(now_playing)))
    if ENABLE_FISHENZON_FALLBACK:
        try:
            fishenzon_playing = _now_playing_from_schedule(fetch_fishenzon_epg())
            now_playing.update(fishenzon_playing)
            log("EPG[fishenzon]: {0} tvgID(s) currently airing".format(len(fishenzon_playing)))
        except Exception as ex:  # noqa: BLE001 - transitional fallback, never fatal
            log("EPG[fishenzon] failed: {0}".format(ex))
    now_playing.update(fetch_now_playing())

    results = {}
    text_substitutions = []  # (block_start, block_end, new_block)
    for cid in watchable_ids:
        channel = channels[cid]
        old_link = channel.get("linkDetails", {}).get("link")
        result = resolve_channel(cid, channel)
        results[cid] = result
        status = result["status"]
        marker = {"ok": "OK", "updated": "UPDATED", "broken": "BROKEN!"}.get(status, status.upper())
        log("  [{0:8}] {1:12} {2:30} {3}".format(marker, cid, channel.get("name", ""), result["detail"]))
        if result.get("new_fallback") and result["new_fallback"] != old_link:
            block_start = raw_text.find('"{0}":'.format(cid))
            block_end = raw_text.find("\n\t},", block_start) if block_start != -1 else -1
            if block_start == -1 or block_end == -1:
                log("  WARNING: could not locate object block for {0}; skipping in-place update".format(cid))
                continue
            block = raw_text[block_start:block_end]
            old_literal = json.dumps(old_link)
            new_literal = json.dumps(result["new_fallback"])
            if block.count(old_literal) != 1:
                log("  WARNING: could not safely locate a unique '{0}' within {1}'s block; skipping in-place update".format(old_link, cid))
                continue
            new_block = block.replace(old_literal, new_literal, 1)
            text_substitutions.append((block_start, block_end, new_block))
            channel["linkDetails"]["link"] = result["new_fallback"]

    if text_substitutions and not args.dry_run:
        for block_start, block_end, new_block in sorted(text_substitutions, reverse=True):
            raw_text = raw_text[:block_start] + new_block + raw_text[block_end:]
        with io.open(channels_path, "w", encoding="utf-8", newline="") as f:
            f.write(raw_text)
        log("channels.json updated with {0} refreshed link(s).".format(len(text_substitutions)))
    elif text_substitutions:
        log("[dry-run] channels.json would have been updated with {0} refreshed link(s).".format(len(text_substitutions)))
    else:
        log("No stale links found; channels.json unchanged.")

    broken = [cid for cid, r in results.items() if r["status"] == "broken"]
    if broken:
        log("\n{0} channel(s) appear to be BROKEN and need manual investigation:".format(len(broken)))
        for cid in broken:
            log("  - {0} ({1}): {2}".format(cid, channels[cid].get("name", ""), results[cid]["detail"]))

    entries_by_type = {"tv": [], "radio": []}
    for cid in watchable_ids:
        channel = channels[cid]
        ctype = channel.get("type")
        tvg_id = channel.get("tvgID", "")
        program = now_playing.get(tvg_id)
        entries_by_type[ctype].append({
            "channel_id": cid,
            "name": channel.get("name"),
            "image": channel.get("image"),
            "module": channel.get("module"),
            "tvgID": tvg_id,
            "index": channel.get("index"),
            "status": results[cid]["status"],
            "detail": results[cid]["detail"],
            "resolved_link": results[cid]["resolved_link"],
            "now_playing": program["name"] if program else None,
            "refresh": results[cid].get("refresh"),
        })

    report = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "tv": group_channels(entries_by_type["tv"]),
        "radio": group_channels(entries_by_type["radio"]),
    }

    report_path = os.path.abspath(report_path)
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with io.open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
    log("\nWrote grouped status report ({0} tv groups, {1} radio groups) to {2}".format(
        len(report["tv"]), len(report["radio"]), report_path))


if __name__ == "__main__":
    main()
