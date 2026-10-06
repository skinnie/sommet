#!/usr/bin/env python3
"""Make a GPX usable on a Garmin eTrex 30/30x/32x.

The eTrex draws a track as a bare line: no directions, and where the route meets itself you can
take a wrong branch and still be "on the line". Two outputs from one source GPX:

  track mode  the original track, point for point, PLUS direction pins as waypoints - only where
              the route meets itself (crossing, out-and-back, loop base). The rules and their
              hardware history live in route_pins.py; this file is the eTrex-shaped wrapper.
  route mode  <= `max_via` (default 50) via points: start/end, every decision spot, then the rest
              spread by Douglas-Peucker importance. Only useful with the unit's own road routing,
              which is slow on this hardware - kept for short routes.

    ./tools/etrex_export.py route.gpx --mode track > etrex_track.gpx
    ./tools/etrex_export.py route.gpx --mode route --max-via 50 > etrex_route.gpx

Stdlib only (via geo_util / route_pins). `--selftest` is offline.
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import sys
from typing import List, Optional, Sequence, Tuple

import geo_util
import route_pins

MAX_WAYPOINTS = 2000     # eTrex 30 stores 2000 waypoints


def _dp_rank(xy: Sequence[Tuple[float, float]], forced: Sequence[int], budget: int) -> List[int]:
    """Indices to keep (sorted), at most `budget` beyond the forced ones: always the point that deviates
    most from its current chord next (Douglas-Peucker order, so any prefix is a good simplification).
    `forced` (and the two ends) are always kept and split the line into independent segments."""
    n = len(xy)
    keep = {0, n - 1, *[i for i in forced if 0 <= i < n]}
    bounds = sorted(keep)

    def worst(a: int, b: int) -> Tuple[float, int]:
        ax, ay = xy[a]
        bx, by = xy[b]
        dx, dy = bx - ax, by - ay
        sq = dx * dx + dy * dy
        best, at = -1.0, -1
        for i in range(a + 1, b):
            px, py = xy[i]
            if sq == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / sq
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > best:
                best, at = d, i
        return best, at

    heap: List[Tuple[float, int, int, int]] = []
    for a, b in zip(bounds, bounds[1:]):
        if b > a + 1:
            d, at = worst(a, b)
            heapq.heappush(heap, (-d, a, b, at))
    extra = 0
    while heap and extra < budget:
        _, a, b, at = heapq.heappop(heap)
        keep.add(at)
        extra += 1
        for lo, hi in ((a, at), (at, b)):
            if hi > lo + 1:
                d, i = worst(lo, hi)
                heapq.heappush(heap, (-d, lo, hi, i))
    return sorted(keep)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def build(gpx_text: str, mode: str = "track", name: Optional[str] = None,
          max_track: int = 10000, max_via: int = 50, reverse: bool = False) -> dict:
    """{ok, gpx, stats:{...}} or {ok: False, error}. mode: "track" | "route"."""
    if mode not in ("track", "route"):
        return {"ok": False, "error": "mode must be 'track' or 'route'"}
    try:
        pts = geo_util.parse_gpx_points(gpx_text)
    except Exception as e:  # malformed XML
        return {"ok": False, "error": "could not read GPX: %s" % e}
    if len(pts) < 2:
        return {"ok": False, "error": "no track points found in this GPX"}
    if reverse:
        pts = list(reversed(pts))
    label = name or "eTrex route"
    an = route_pins.analyze(pts)
    pins = route_pins.make_pins(an, "etrex")
    dropped = max(0, len(pins) - MAX_WAYPOINTS)
    if dropped:  # the OK backups are the first to go
        pins = [p for p in pins if p["kind"] != "ok"][:MAX_WAYPOINTS]
    stats = {"mode": mode, "km": round(an["al"][-1] / 1000.0, 2), "points_in": len(pts),
             "junctions": len({e["jid"] for e in an["events"] if e["kind"] == "decision"}),
             "decisions": sum(1 for e in an["events"] if e["kind"] == "decision"),
             "turnarounds": sum(1 for e in an["events"] if e["kind"] == "turn"),
             "ignored_spikes": len(an["spikes"]), "waypoints_dropped": dropped}

    if mode == "track":
        if len(pts) > max_track:  # only then is the line thinned; never resampled
            an = dict(an, pts=[pts[i] for i in _dp_rank(an["xy"], [], max_track - 2)])
        gpx = route_pins.etrex_gpx(an, pins, label, [] if reverse else route_pins.source_waypoints(gpx_text))
        stats.update(points_out=len(an["pts"]), pins=len(pins))
        return {"ok": True, "gpx": gpx, "stats": stats}

    res, al = an["res"], an["al"]
    named = {}
    for p in pins:
        if p["kind"] != "ok":
            named[min(range(len(al)), key=lambda q: abs(al[q] - p["along"]))] = p["name"]
    forced = sorted(named)[:max(0, max_via - 2)]
    idx = _dp_rank(res, forced, max(0, max_via - 2 - len(forced)))
    rows = []
    for i in idx:
        lat, lon = an["ll"](res[i])
        nm = "<name>%s</name>" % _esc(named[i]) if i in named and i in forced else ""
        rows.append('    <rtept lat="%.6f" lon="%.6f">%s</rtept>' % (lat, lon, nm))
    gpx = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">\n'
           "  <rte><name>%s</name>\n%s\n  </rte>\n</gpx>\n" % (_esc(label), "\n".join(rows)))
    stats.update(points_out=len(idx), pins=0)
    return {"ok": True, "gpx": gpx, "stats": stats}


def _selftest() -> int:
    walk = route_pins._synthetic(route_pins.TEST_WALK)
    r = build(walk, "track")
    assert r["ok"] and r["stats"]["decisions"] == 8 and r["stats"]["turnarounds"] == 1, r["stats"]
    assert "<name>Right 0.3</name>" in r["gpx"] and "<name>Turn back 3.7</name>" in r["gpx"], r["gpx"][:900]
    assert r["gpx"].count("<trkpt") == r["stats"]["points_in"]            # the track is untouched
    r = build(walk, "track", max_track=60)
    assert r["gpx"].count("<trkpt") <= 60, r["stats"]
    r = build(walk, "route", max_via=20)
    n = r["gpx"].count("<rtept")
    assert r["ok"] and 12 <= n <= 20 and "<name>Left 7.5</name>" in r["gpx"], (n, r["stats"])
    assert build(route_pins._synthetic([(0, 0), (0, 2000)]), "track")["stats"]["pins"] == 0
    assert build("<gpx></gpx>")["ok"] is False
    print("etrex_export selftest OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Direction pins (or a <=50-point route) for Garmin eTrex.")
    ap.add_argument("track", nargs="?", help="GPX file")
    ap.add_argument("--mode", choices=("track", "route"), default="track")
    ap.add_argument("--name")
    ap.add_argument("--max-track", type=int, default=10000)
    ap.add_argument("--max-via", type=int, default=50)
    ap.add_argument("--reverse", action="store_true", help="ride the route the other way")
    ap.add_argument("--json", action="store_true", help="print {ok,gpx,stats} instead of bare GPX")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.track:
        ap.error("a GPX file is required")
    raw = open(args.track, "r", encoding="utf-8", errors="replace").read()
    res = build(raw, args.mode, args.name, args.max_track, args.max_via, args.reverse)
    if args.json:
        print(json.dumps(res))
        return 0 if res["ok"] else 2
    if not res["ok"]:
        sys.stderr.write(res["error"] + "\n")
        return 2
    sys.stderr.write(json.dumps(res["stats"]) + "\n")
    sys.stdout.write(res["gpx"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
