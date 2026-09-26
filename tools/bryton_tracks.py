#!/usr/bin/env python3
"""The Bryton Aero 60's Follow Track list, for the Routes page's library menu: list the routes on
the device, read one back as GPX (to open it in the planner), rename, delete. Format: see
bryton_track.py (Tracks/<name>.track + .smy + .tinfo, plus an optional <name>/ map-tile folder).

    ./tools/bryton_tracks.py list   <mount>
    ./tools/bryton_tracks.py gpx    <mount> <name>
    ./tools/bryton_tracks.py rename <mount> <name> <new name>
    ./tools/bryton_tracks.py delete <mount> <name>
"""

import json
import os
import shutil
import struct
import sys

from bryton_track import sanitize

EXTS = (".track", ".smy", ".tinfo")


def _dir(mount):
    return os.path.join(mount, "Tracks")


def _safe(name):
    if not name or "/" in name or "\\" in name or name.startswith(".") or ".." in name:
        raise ValueError("bad route name")
    return name


def list_tracks(mount):
    d = _dir(mount)
    out = []
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith(".track"):
            continue
        name = f[:-6]
        dist = pts = None
        try:
            with open(os.path.join(d, name + ".smy"), "rb") as s:
                smy = s.read(60)
            if len(smy) >= 24:
                pts = struct.unpack_from("<H", smy, 2)[0]
                dist = struct.unpack_from("<I", smy, 20)[0]
        except OSError:
            pass
        out.append({"name": name, "distanceMeters": dist, "pointCount": pts,
                    "modified": int(os.path.getmtime(os.path.join(d, f)))})
    return out


def to_gpx(mount, name):
    path = os.path.join(_dir(mount), _safe(name) + ".track")
    with open(path, "rb") as f:
        data = f.read()
    pts = [struct.unpack_from("<iiii", data, i)[:3] for i in range(0, len(data) - 15, 16)]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="Sommet (Bryton Tracks)" xmlns="http://www.topografix.com/GPX/1/1">',
             f"<trk><name>{name}</name><trkseg>"]
    lines += [f'<trkpt lat="{la / 1e6:.6f}" lon="{lo / 1e6:.6f}"><ele>{el}</ele></trkpt>' for la, lo, el in pts]
    lines += ["</trkseg></trk>", "</gpx>"]
    return "\n".join(lines)


def rename(mount, name, new):
    d = _dir(mount)
    old, new = _safe(name), sanitize(new)
    if os.path.exists(os.path.join(d, new + ".track")):
        raise ValueError(f"a route called {new!r} is already on the Bryton")
    for ext in EXTS:
        src = os.path.join(d, old + ext)
        if os.path.exists(src):
            os.replace(src, os.path.join(d, new + ext))
    if os.path.isdir(os.path.join(d, old)):
        os.replace(os.path.join(d, old), os.path.join(d, new))
    return {"ok": True, "name": new}


def delete(mount, name):
    d = _dir(mount)
    n = _safe(name)
    for ext in EXTS:
        try:
            os.remove(os.path.join(d, n + ext))
        except FileNotFoundError:
            pass
    if os.path.isdir(os.path.join(d, n)):
        shutil.rmtree(os.path.join(d, n))
    return {"ok": True}


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    try:
        cmd, mount = a[0], a[1]
        if cmd == "list":
            out = {"ok": True, "routes": list_tracks(mount)}
        elif cmd == "gpx":
            out = {"ok": True, "name": a[2], "gpx": to_gpx(mount, a[2])}
        elif cmd == "rename":
            out = rename(mount, a[2], a[3])
        elif cmd == "delete":
            out = delete(mount, a[2])
        else:
            raise ValueError(f"unknown command {cmd}")
    except (IndexError, ValueError, OSError) as exc:
        out = {"ok": False, "error": str(exc)}
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
