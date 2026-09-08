#!/usr/bin/env python3
"""Detect a bike computer connected over MTP (Garmin Edge, Hammerhead Karoo) and pull its
recorded activities (.fit files) off it - rough direct-USB import, no settings, read-only.

    ./tools/mtp_import.py --list                 # JSON: which MTP bike computers are connected
    ./tools/mtp_import.py --pull <dest-dir>      # copy every activity .fit into dest-dir
    ./tools/mtp_import.py --pull <dest> --since 2026-09-01-00-00-00.fit   # only newer names

Linux desktop path only for now: uses gvfs via `gio`, which auto-mounts MTP devices with no
root and no extra install (confirmed against a real Edge 1040 Solar, 2026-09-04). libmtp
(mtp-detect/mtp-files) would be the cross-platform route for Windows/macOS later - kept out
here so this stays dependency-free on the box it runs on.

Activity locations, confirmed/observed:
  * Garmin Edge  : "<mount>/<store>/Garmin/Activities/*.fit"   (store e.g. "Internal Storage")
  * Hammerhead   : "<mount>/<store>/FitFiles/*.fit"            (Karoo, MTP enabled in dev options)
Both are timestamp-named (YYYY-MM-DD-HH-MM-SS.fit), so a plain name sort is chronological and
--since is a simple string compare.
"""

import argparse
import json
import os
import re
import subprocess
import sys

GVFS_ROOT = f"/run/user/{os.getuid()}/gvfs" if hasattr(os, "getuid") else ""

# host-name substring -> (kind, source-tag, relative activity dir under the storage root).
# The gvfs mount is named from the device's own USB product string.
DEVICE_KINDS = [
    ("garmin",     "edge",  "Garmin/Activities"),
    ("edge",       "edge",  "Garmin/Activities"),
    ("hammerhead", "karoo", "FitFiles"),
    ("karoo",      "karoo", "FitFiles"),
]


def _gio_list(path):
    """Names directly under an MTP path, or [] if it can't be listed."""
    try:
        out = subprocess.run(["gio", "list", path], capture_output=True, text=True,
                             timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [ln for ln in out.stdout.splitlines() if ln]


def _classify(host):
    low = host.lower()
    for needle, kind, activity_dir in DEVICE_KINDS:
        if needle in low:
            return kind, activity_dir
    return None, None


def _mount_roots():
    """Every MTP mount gvfs currently exposes, as (host, mount_path)."""
    roots = []
    if GVFS_ROOT and os.path.isdir(GVFS_ROOT):
        for name in os.listdir(GVFS_ROOT):
            if name.startswith("mtp:host="):
                host = name[len("mtp:host="):]
                roots.append((host, os.path.join(GVFS_ROOT, name)))
    return roots


def _activities_dir(mount_path, rel):
    """Find "<store>/<rel>" under a mount, trying each storage root the device exposes
    (name varies: "Internal Storage", "Primary", …). Returns the gio URI or None."""
    # gvfs paths are real filesystem paths here; list stores directly.
    for store in _gio_list(mount_path) or os.listdir(mount_path):
        cand = os.path.join(mount_path, store, *rel.split("/"))
        if os.path.isdir(cand):
            return cand
    return None


def discover():
    devices = []
    for host, mount in _mount_roots():
        kind, rel = _classify(host)
        if not kind:
            continue
        adir = _activities_dir(mount, rel)
        fits = sorted(f for f in (os.listdir(adir) if adir and os.path.isdir(adir) else [])
                      if f.lower().endswith(".fit"))
        devices.append({
            "kind": kind,
            "host": host,
            "name": host.replace("_", " ").strip(),
            "mount": mount,
            "activitiesDir": adir or "",
            "activityCount": len(fits),
            "files": fits,          # ride filenames on the device (cheap - listed, not pulled)
        })
    return devices


def pull(dest, since=None, only=None):
    """Copy ride .fit files off the connected device(s) into `dest`.

    `only`, when given, is a set of (kind, name) pairs - only those exact files are pulled. This
    is the incremental path (2026-09-04): the client already knows the device's full file list
    from discover() and which it has processed before, so it asks for just the new ones instead
    of re-pulling everything each sync. `since` is the older name-prefix filter (Edge only)."""
    os.makedirs(dest, exist_ok=True)
    copied = []
    for dev in discover():
        adir = dev["activitiesDir"]
        if not adir:
            continue
        for name in sorted(os.listdir(adir)):
            if not name.lower().endswith(".fit"):
                continue
            if only is not None and (dev["kind"], name) not in only:
                continue
            if since and name <= since:
                continue
            src = os.path.join(adir, name)
            # Namespaced by kind so an Edge and a Karoo file of the same timestamp can't collide.
            out = os.path.join(dest, f"{dev['kind']}__{name}")
            try:
                # gio copy handles the gvfs backend cleanly; a plain shutil.copy also works on
                # the fuse path but gio is the documented, retrying route.
                subprocess.run(["gio", "copy", src, out], check=True,
                               capture_output=True, timeout=120)
                copied.append({"kind": dev["kind"], "name": name, "path": out})
            except (OSError, subprocess.SubprocessError):
                continue
    return copied


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="JSON of connected MTP bike computers")
    ap.add_argument("--pull", metavar="DEST", help="copy activity .fit files into DEST")
    ap.add_argument("--since", metavar="NAME", help="only files whose name sorts after NAME")
    ap.add_argument("--only-stdin", action="store_true",
                    help="with --pull, read JSON {\"files\":[{\"kind\",\"name\"}]} from stdin and "
                         "pull ONLY those (incremental sync)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if args.pull:
        only = None
        if args.only_stdin:
            try:
                spec = json.loads(sys.stdin.read() or "{}")
                only = {(f.get("kind"), f.get("name")) for f in spec.get("files", [])}
            except (json.JSONDecodeError, AttributeError):
                only = set()          # unparseable -> pull nothing rather than everything
        copied = pull(args.pull, since=args.since, only=only)
        print(json.dumps({"ok": True, "copied": copied, "count": len(copied)}))
        return 0
    # default / --list
    print(json.dumps({"ok": True, "devices": discover()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
