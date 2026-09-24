#!/usr/bin/env python3
"""Encode a GPX track into the Bryton Aero 60 "Follow Track" format (System has it in Tracks/):
<name>.track + <name>.smy (+ empty <name>.tinfo). Reverse-engineered from real device tracks
(NordOuest/TOUQUET/Avec seb) - the app doesn't write these, the device does, so this lets us
author a followable route over USB without the BLE/routing pipeline. stdlib only.

    bryton_track.py route.gpx --out-dir /media/<user>/BRYTON/Tracks --name "My Route" [--max-points 2500]

Formats (little-endian), validated against the device's own files:
  .track : N records × 16 bytes = { int32 lat*1e6, int32 lon*1e6, int32 ele_m, int32 0 }
  .smy   : [0:2]=1 (ver) [2:4]=N(u16) [4:8]=latmax*1e6 [8:12]=latmin [12:16]=lonmax [16:20]=lonmin
           [20:24]=distance_m(u32) [24:60]=0   (60 bytes; TOUQUET has the elevation fields zeroed
           and still lists in Follow Track, so we zero them too)
  .tinfo : empty (0 bytes) - turn-by-turn info, optional (TOUQUET.tinfo is 0 bytes and works)
"""

import argparse
import math
import os
import re
import struct
import sys


def parse_gpx(path):
    """(lat, lon, ele) for every trkpt (falls back to rtept). ele defaults to 0."""
    text = open(path, encoding="utf-8", errors="replace").read()
    pts = []
    # Grab each point block so a following <ele> can be paired with its point.
    for m in re.finditer(r'<(?:trk|rte)pt[^>]*\blat="([-\d.]+)"[^>]*\blon="([-\d.]+)"(.*?)</(?:trk|rte)pt>',
                         text, re.DOTALL):
        lat, lon, body = float(m.group(1)), float(m.group(2)), m.group(3)
        ele = re.search(r"<ele>([-\d.]+)</ele>", body)
        pts.append((lat, lon, float(ele.group(1)) if ele else 0.0))
    if not pts:  # some files self-close the point tag with lat/lon only
        for m in re.finditer(r'<(?:trk|rte)pt[^>]*\blat="([-\d.]+)"[^>]*\blon="([-\d.]+)"', text):
            pts.append((float(m.group(1)), float(m.group(2)), 0.0))
    return pts


def decimate(pts, max_points):
    """Uniformly thin to <= max_points, always keeping the first and last (breadcrumb fidelity is
    fine for Follow Track; the device's own tracks run ~1700-1800 pts for 110-145 km)."""
    if max_points <= 0 or len(pts) <= max_points:
        return pts
    step = math.ceil(len(pts) / max_points)
    kept = pts[::step]
    if kept[-1] != pts[-1]:
        kept.append(pts[-1])
    return kept


def haversine_m(a, b):
    R = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dl = math.radians(b[1] - a[1])
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def encode(pts):
    """Return (track_bytes, smy_bytes) for the given [(lat,lon,ele)] list."""
    track = bytearray()
    for lat, lon, ele in pts:
        track += struct.pack("<iiii", round(lat * 1e6), round(lon * 1e6), round(ele), 0)
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    dist_m = sum(haversine_m(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    smy = bytearray(60)
    struct.pack_into("<H", smy, 0, 1)                       # version
    struct.pack_into("<H", smy, 2, len(pts) & 0xFFFF)       # N
    struct.pack_into("<i", smy, 4, round(max(lats) * 1e6))  # lat max
    struct.pack_into("<i", smy, 8, round(min(lats) * 1e6))  # lat min
    struct.pack_into("<i", smy, 12, round(max(lons) * 1e6))  # lon max
    struct.pack_into("<i", smy, 16, round(min(lons) * 1e6))  # lon min
    struct.pack_into("<I", smy, 20, round(dist_m))           # distance (m)
    return bytes(track), bytes(smy)


def sanitize(name):
    safe = "".join(c for c in (name or "Route") if c.isalnum() or c in " -_").strip()[:40]
    return safe or "Route"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gpx")
    ap.add_argument("--out-dir", required=True, help="e.g. /media/<user>/BRYTON/Tracks")
    ap.add_argument("--name", help="route name (defaults to the GPX file name)")
    ap.add_argument("--max-points", type=int, default=2500)
    ap.add_argument("--keep-gpx", action="store_true", help="also copy the source GPX next to it")
    args = ap.parse_args()

    pts = parse_gpx(args.gpx)
    if len(pts) < 2:
        print("no track points found", file=sys.stderr)
        return 1
    pts = decimate(pts, args.max_points)
    track, smy = encode(pts)
    name = sanitize(args.name or os.path.splitext(os.path.basename(args.gpx))[0])
    base = os.path.join(args.out_dir, name)
    os.makedirs(args.out_dir, exist_ok=True)
    with open(base + ".track", "wb") as f:
        f.write(track)
    with open(base + ".smy", "wb") as f:
        f.write(smy)
    open(base + ".tinfo", "wb").close()          # empty, like TOUQUET.tinfo
    if args.keep_gpx:
        with open(base + ".gpx", "w", encoding="utf-8") as f:
            f.write(open(args.gpx, encoding="utf-8", errors="replace").read())
    print(f"wrote {name}.track ({len(track)} B, {len(pts)} pts), {name}.smy, {name}.tinfo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
