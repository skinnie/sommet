#!/usr/bin/env python3
"""The saved-route library behind the Routes page's library menu (André, 2026-09-26: "2 yes" -
routes you import or plan are kept, so they're still there next time).

    ./tools/route_library.py list   <dir>
    ./tools/route_library.py save   <dir> <name> <gpx-file>      -> {ok, id, existed}
    ./tools/route_library.py get    <dir> <id>                   -> {ok, name, gpx}
    ./tools/route_library.py rename <dir> <id> <new name>
    ./tools/route_library.py delete <dir> <id>

Storage: one <id>.gpx per route plus index.json [{id, name, created, distanceMeters,
ascentMeters, sha1}] in <dir> (the backend passes ~/AmbitAppBackups/Routes). Saving the same GPX
again (same content hash) doesn't add a second copy - it returns the existing entry (renamed if a
new name is given), so importing a file twice never duplicates it. stdlib only.
"""

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time

INDEX = "index.json"


def _index(d):
    try:
        with open(os.path.join(d, INDEX), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _write_index(d, items):
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(d, INDEX))


def _stats(gpx):
    """(distance m, ascent m) from the GPX's track/route points (haversine; no smoothing)."""
    pts = []
    for m in re.finditer(r'<(?:trk|rte)pt[^>]*\blat="([-\d.]+)"[^>]*\blon="([-\d.]+)"(.*?)(?:</(?:trk|rte)pt>|/>)',
                         gpx, re.DOTALL):
        ele = re.search(r"<ele>([-\d.]+)</ele>", m.group(3) or "")
        pts.append((float(m.group(1)), float(m.group(2)), float(ele.group(1)) if ele else None))
    dist = asc = 0.0
    for a, b in zip(pts, pts[1:]):
        p1, p2 = math.radians(a[0]), math.radians(b[0])
        x = (math.sin((p2 - p1) / 2) ** 2
             + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2)
        dist += 2 * 6371000 * math.asin(min(1.0, math.sqrt(x)))
        if a[2] is not None and b[2] is not None and b[2] > a[2]:
            asc += b[2] - a[2]
    return round(dist), round(asc)


def list_routes(d):
    return sorted(_index(d), key=lambda r: r.get("created", 0), reverse=True)


def save(d, name, gpx):
    items = _index(d)
    sha = hashlib.sha1(gpx.encode("utf-8")).hexdigest()
    name = (name or "Route").strip()[:80] or "Route"
    for r in items:
        if r.get("sha1") == sha:                       # same file again: no duplicate
            return {"ok": True, "id": r["id"], "existed": True}
    rid = f"{int(time.time())}-{sha[:8]}"
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, rid + ".gpx"), "w", encoding="utf-8") as f:
        f.write(gpx)
    dist, asc = _stats(gpx)
    items.append({"id": rid, "name": name, "created": int(time.time()),
                  "distanceMeters": dist, "ascentMeters": asc, "sha1": sha})
    _write_index(d, items)
    return {"ok": True, "id": rid, "existed": False}


def _find(d, rid):
    if not re.fullmatch(r"[0-9a-f-]+", rid or ""):
        raise ValueError("bad id")
    items = _index(d)
    for r in items:
        if r["id"] == rid:
            return items, r
    raise ValueError("no such route")


def get(d, rid):
    _items, r = _find(d, rid)
    with open(os.path.join(d, rid + ".gpx"), encoding="utf-8") as f:
        return {"ok": True, "name": r["name"], "gpx": f.read()}


def rename(d, rid, name):
    items, r = _find(d, rid)
    r["name"] = (name or r["name"]).strip()[:80] or r["name"]
    _write_index(d, items)
    return {"ok": True}


def delete(d, rid):
    items, r = _find(d, rid)
    items = [x for x in items if x["id"] != rid]
    try:
        os.remove(os.path.join(d, rid + ".gpx"))
    except OSError:
        pass
    _write_index(d, items)
    return {"ok": True}


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    try:
        cmd, d = a[0], a[1]
        if cmd == "list":
            out = {"ok": True, "routes": list_routes(d)}
        elif cmd == "save":
            with open(a[3], encoding="utf-8", errors="replace") as f:
                out = save(d, a[2], f.read())
        elif cmd == "get":
            out = get(d, a[2])
        elif cmd == "rename":
            out = rename(d, a[2], a[3])
        elif cmd == "delete":
            out = delete(d, a[2])
        else:
            raise ValueError(f"unknown command {cmd}")
    except (IndexError, ValueError, OSError) as exc:
        out = {"ok": False, "error": str(exc)}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
