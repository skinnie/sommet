#!/usr/bin/env python3
"""Make a GPX usable on a Garmin eTrex 30/30x/32x (no turn guidance on tracks, 50-point route cap).

The eTrex draws a track as a bare line - no directions, and at a crossing of the track with itself it
cannot tell which way to go. A route has directions but only ~50 via points, which the device turns
into a road route on its own map (straight lines / detours if the points are badly placed). So:

  track mode  the full geometry (thinned only past `max_track` points, default 10 000) PLUS named
              waypoints: one at every real turn ("Right 12.4", "Bear left 30.1", "U-turn 41.0")
              and at every place the track meets itself ("Cross Straight 3.2", "Cross Left 3.2").
              Names are spelled out, not coded, since the eTrex doesn't reliably show <desc> while
              navigating (confirmed on hardware) - the visible name has to stand on its own.
  route mode  <= `max_via` (default 50) via points, chosen where they matter: start/end, both sides of
              every crossing, the sharpest turns first, then the remaining budget spread by
              Douglas-Peucker importance. The device's routable map then calculates the guidance.

Input: GPX text (<trkpt>, else <rtept>). Output: GPX text + stats. Stdlib only (via geo_util), like the
rest of tools/. `--selftest` is offline.

    ./tools/etrex_export.py route.gpx --mode track > etrex_track.gpx
    ./tools/etrex_export.py route.gpx --mode route --max-via 50 > etrex_route.gpx
    ./tools/etrex_export.py route.gpx --parts track,route --waypoint-kinds crossing > combined.gpx
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import geo_util

STEP_M = 10.0            # resampling step along the line for turn / crossing analysis
LOOK = 3                 # samples either side used to measure a heading change (30 m)
MIN_TURN_DEG = 40.0      # below this a bend is not worth a waypoint
TURN_GAP_M = 40.0        # two turn waypoints closer than this collapse into the sharper one
CROSS_M = 15.0           # two passes closer than this are "the track meeting itself"
CROSS_MIN_ALONG_M = 150.0  # ...provided they are at least this far apart ALONG the track
MAX_WAYPOINTS = 2000     # eTrex 30 stores 2000 waypoints


# --- geometry -----------------------------------------------------------------------------

def _project(points: Sequence[dict]) -> List[Tuple[float, float]]:
    """Local equirectangular metres (x east, y north) - accurate to well under a metre at route scale
    for the short distances the turn / crossing maths compares."""
    lat0 = sum(p["lat"] for p in points) / len(points)
    kx = math.cos(math.radians(lat0)) * 111320.0
    ky = 110540.0
    return [(p["lon"] * kx, p["lat"] * ky) for p in points]


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


def _resample(xy: Sequence[Tuple[float, float]], pts: Sequence[dict], step: float
              ) -> Tuple[List[Tuple[float, float]], List[dict], List[float]]:
    """Points every `step` metres along the line: (xy, {lat,lon,ele}, along-track metres)."""
    out_xy = [xy[0]]
    out_pt = [dict(pts[0])]
    out_d = [0.0]
    travelled = 0.0
    next_at = step
    for i in range(1, len(xy)):
        x0, y0 = xy[i - 1]
        x1, y1 = xy[i]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg == 0:
            continue
        while next_at <= travelled + seg:
            f = (next_at - travelled) / seg
            out_xy.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
            a, b = pts[i - 1], pts[i]
            ele = None if a.get("ele") is None or b.get("ele") is None else a["ele"] + (b["ele"] - a["ele"]) * f
            out_pt.append({"lat": a["lat"] + (b["lat"] - a["lat"]) * f,
                           "lon": a["lon"] + (b["lon"] - a["lon"]) * f, "ele": ele})
            out_d.append(next_at)
            next_at += step
        travelled += seg
    if out_d[-1] < travelled - 1.0:
        out_xy.append(xy[-1])
        out_pt.append(dict(pts[-1]))
        out_d.append(travelled)
    return out_xy, out_pt, out_d


def _bearing(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))


def _delta(bearing_in: float, bearing_out: float) -> float:
    """Signed heading change in (-180, 180]; positive = right turn."""
    d = (bearing_out - bearing_in + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def _heading_change(xy: Sequence[Tuple[float, float]], a: int, b: int) -> float:
    """Heading change from approaching sample `a` to leaving sample `b` (a <= b)."""
    n = len(xy)
    i0, i1 = max(0, a - LOOK), min(n - 1, b + LOOK)
    if i0 == a or i1 == b:
        return 0.0
    return _delta(_bearing(xy[i0], xy[a]), _bearing(xy[b], xy[i1]))


def _label(delta: float) -> str:
    d = abs(delta)
    side = "R" if delta > 0 else "L"
    if d >= 165:
        return "U"
    if d >= 120:
        return "SH" + side          # sharp
    if d >= 60:
        return side
    if d >= 20:
        return "S" + side           # slight
    return "STR"


def _wording(label: str) -> str:
    return {"L": "Turn left", "R": "Turn right", "SL": "Bear left", "SR": "Bear right",
            "SHL": "Sharp left", "SHR": "Sharp right", "U": "U-turn", "STR": "Go straight"}[label]


# Spelled out, not the raw L/R/SL/SR code - a rider glancing at the map has no legend and the
# fuller <desc> text isn't reliably shown by the eTrex UI (confirmed on hardware, 2026-09-25:
# only the <name> is visible while navigating), so the visible name has to be self-explanatory
# on its own. Still short enough for a Garmin name field (well under the ~30-char limit).
def _name_word(label: str) -> str:
    return {"L": "Left", "R": "Right", "SL": "Bear left", "SR": "Bear right",
            "SHL": "Sharp left", "SHR": "Sharp right", "U": "U-turn", "STR": "Straight"}[label]


# --- detection ----------------------------------------------------------------------------

def find_turns(xy: Sequence[Tuple[float, float]], along: Sequence[float],
               min_deg: float = MIN_TURN_DEG) -> List[dict]:
    """[{i, km, delta, label}] - local maxima of the heading change, at least TURN_GAP_M apart."""
    n = len(xy)
    if n < 2 * LOOK + 1:
        return []
    delta = [0.0] * n
    for i in range(LOOK, n - LOOK):
        delta[i] = _delta(_bearing(xy[i - LOOK], xy[i]), _bearing(xy[i], xy[i + LOOK]))
    cands = [i for i in range(LOOK, n - LOOK)
             if abs(delta[i]) >= min_deg and abs(delta[i]) >= abs(delta[i - 1])
             and abs(delta[i]) > abs(delta[i + 1]) - 1e-9]
    cands.sort(key=lambda i: -abs(delta[i]))
    taken: List[int] = []
    for i in cands:
        if all(abs(along[i] - along[j]) >= TURN_GAP_M for j in taken):
            taken.append(i)
    taken.sort()
    return [{"i": i, "km": along[i] / 1000.0, "delta": delta[i], "label": _label(delta[i])}
            for i in taken]


def find_crossings(xy: Sequence[Tuple[float, float]], along: Sequence[float],
                   cross_m: float = CROSS_M) -> List[dict]:
    """Places where the track comes within `cross_m` of an earlier/later part of itself (>=
    CROSS_MIN_ALONG_M away along the track). Each pass through such a place is one entry:
    [{i, km, delta, label, event, other_km}] - `event` groups every pass through the same junction."""
    n = len(xy)
    cell = max(cross_m, 1.0)
    grid: Dict[Tuple[int, int], List[int]] = {}
    for i, (x, y) in enumerate(xy):
        grid.setdefault((int(x // cell), int(y // cell)), []).append(i)
    partners: Dict[int, List[int]] = {}
    for i, (x, y) in enumerate(xy):
        cx, cy = int(x // cell), int(y // cell)
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for j in grid.get((gx, gy), ()):
                    if j <= i or along[j] - along[i] < CROSS_MIN_ALONG_M:
                        continue
                    if math.hypot(xy[j][0] - x, xy[j][1] - y) <= cross_m:
                        partners.setdefault(i, []).append(j)
                        partners.setdefault(j, []).append(i)
    if not partners:
        return []

    # contiguous runs of near-self samples = one pass through a junction
    near = sorted(partners)
    runs: List[List[int]] = []
    for i in near:
        if runs and i - runs[-1][-1] <= 2:
            runs[-1].append(i)
        else:
            runs.append([i])
    run_of = {i: r for r, run in enumerate(runs) for i in run}
    # runs that touch each other belong to the same event
    parent = list(range(len(runs)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, js in partners.items():
        for j in js:
            parent[find(run_of[i])] = find(run_of[j])

    events: Dict[int, List[int]] = {}
    for r in range(len(runs)):
        events.setdefault(find(r), []).append(r)
    out: List[dict] = []
    for ev_no, (_, rs) in enumerate(sorted(events.items(), key=lambda kv: runs[kv[1][0]][0])):
        kms = [along[runs[r][0]] / 1000.0 for r in rs]
        for r in rs:
            a, b = runs[r][0], runs[r][-1]
            length = along[b] - along[a]
            spots = [(a + b) // 2] if length <= 60.0 else [a, b]
            for i in spots:
                d = _heading_change(xy, a, b) if len(spots) == 1 else (
                    _heading_change(xy, a, a) if i == a else _heading_change(xy, b, b))
                out.append({"i": i, "km": along[i] / 1000.0, "delta": d, "label": _label(d),
                            "event": ev_no + 1,
                            "other_km": [round(k, 1) for k in kms if abs(k - along[a] / 1000.0) > 0.05]})
    out.sort(key=lambda c: c["i"])
    return out


# --- GPX out ------------------------------------------------------------------------------

def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _pt(tag: str, p: dict, inner: str = "") -> str:
    ele = "" if p.get("ele") is None else "<ele>%.1f</ele>" % p["ele"]
    return '<%s lat="%.6f" lon="%.6f">%s%s</%s>' % (tag, p["lat"], p["lon"], ele, inner, tag)


def _wpt(p: dict, name: str, desc: str, sym: str) -> str:
    return _pt("wpt", p, "<name>%s</name><desc>%s</desc><sym>%s</sym>" % (_esc(name), _esc(desc), _esc(sym)))


def build(gpx_text: str, mode: str = "track", name: Optional[str] = None,
          max_track: int = 10000, max_via: int = 50, min_turn_deg: float = MIN_TURN_DEG,
          cross_m: float = CROSS_M, reverse: bool = False,
          parts: Optional[Sequence[str]] = None, waypoint_kinds: Optional[Sequence[str]] = None) -> dict:
    """{ok, gpx, stats:{...}} or {ok: False, error}.

    `mode` ("track" | "route") is the normal shorthand: "track" = full geometry + all
    waypoints, "route" = <=max_via via points, no waypoint markers. For a hardware-test
    package where the two need to be isolated (e.g. "does the original track alone confuse
    the rider" vs "do ONLY the crossing markers help"), pass `parts` (any of "track",
    "route", "waypoints") and/or `waypoint_kinds` (any of "turn", "crossing") to override
    what mode implies - e.g. parts=("track","route"), waypoint_kinds=("crossing",) for a
    track+route file with only the crossing waypoints, no turn clutter. The route's via
    points are always placed at every detected turn/crossing regardless of waypoint_kinds -
    that only filters which marks get their own <wpt>, not routing quality.
    """
    if mode not in ("track", "route"):
        return {"ok": False, "error": "mode must be 'track' or 'route'"}
    parts = set(parts) if parts is not None else ({"track", "waypoints"} if mode == "track" else {"route"})
    waypoint_kinds = set(waypoint_kinds) if waypoint_kinds is not None else {"turn", "crossing"}
    try:
        pts = geo_util.parse_gpx_points(gpx_text)
    except Exception as e:  # malformed XML
        return {"ok": False, "error": "could not read GPX: %s" % e}
    if len(pts) < 2:
        return {"ok": False, "error": "no track points found in this GPX"}
    if reverse:
        pts = list(reversed(pts))
    label = name or "eTrex route"

    xy_raw = _project(pts)
    # Light noise removal before analysis so GPS jitter does not read as a chain of tiny turns.
    quiet = _tolerance_filter(xy_raw, 2.0)
    sxy, spts, salong = _resample([xy_raw[i] for i in quiet], [pts[i] for i in quiet], STEP_M)

    turns = find_turns(sxy, salong, min_turn_deg)
    crossings = find_crossings(sxy, salong, cross_m)
    # a crossing marker already says what to do there - drop turn markers on top of it
    turns = [t for t in turns if all(abs(salong[t["i"]] - salong[c["i"]]) > TURN_GAP_M for c in crossings)]

    total_km = salong[-1] / 1000.0
    marks: List[dict] = []
    for t in turns:
        nxt = next((u["km"] for u in turns if u["km"] > t["km"]), None)
        marks.append({**t, "name": "%s %.1f" % (_name_word(t["label"]), t["km"]), "sym": "Flag, Blue",
                      "desc": "%s at %.1f km%s" % (_wording(t["label"]), t["km"],
                                                   "; next turn in %.1f km" % (nxt - t["km"]) if nxt else "; then to the end (%.1f km)" % total_km),
                      "kind": "turn"})
    for c in crossings:
        others = ", ".join("%.1f" % k for k in c["other_km"])
        marks.append({**c, "name": "Cross %s %.1f" % (_name_word(c["label"]), c["km"]), "sym": "Flag, Red", "kind": "crossing",
                      "desc": "Track crosses itself here (also at km %s): %s at %.1f km" % (
                          others or "-", _wording(c["label"]).lower(), c["km"])})
    marks.sort(key=lambda m: m["i"])
    wp_dropped = max(0, len(marks) - MAX_WAYPOINTS)
    if wp_dropped:
        marks.sort(key=lambda m: (m["kind"] != "crossing", -abs(m["delta"])))
        marks = sorted(marks[:MAX_WAYPOINTS], key=lambda m: m["i"])

    head = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">\n')
    body: List[str] = []
    n_out = 0
    shown_marks = [m for m in marks if m["kind"] in waypoint_kinds]

    if "waypoints" in parts and shown_marks:
        body.append("  " + _wpt_block(spts, shown_marks))

    if "track" in parts:
        # The untouched original points (geo_util.parse_gpx_points output, reversed if asked) -
        # never the resample used for turn/crossing analysis. Only thinned if over max_track.
        if len(pts) > max_track:
            idx = _dp_rank(xy_raw, [], max_track - 2)
            outpts = [pts[i] for i in idx]
        else:
            outpts = pts
        body.append("  <trk><name>%s</name><trkseg>\n%s\n  </trkseg></trk>"
                    % (_esc(label), "\n".join("    " + _pt("trkpt", p) for p in outpts)))
        n_out = len(outpts)

    if "route" in parts:
        # Via points always land on every detected turn/crossing (not just the shown ones) -
        # waypoint_kinds only controls which marks get their own <wpt>, not routing quality.
        forced_marks = sorted(marks, key=lambda m: (m["kind"] != "crossing", -abs(m["delta"])))
        room = max(0, max_via - 2)
        chosen = forced_marks[:room]
        forced_idx = sorted({m["i"] for m in chosen})
        idx = _dp_rank(sxy, forced_idx, max(0, max_via - 2 - len(forced_idx)))
        by_i = {m["i"]: m for m in marks}
        rows = []
        for i in idx:
            m = by_i.get(i)
            rows.append("    " + _pt("rtept", spts[i], "<name>%s</name>" % _esc(m["name"]) if m else ""))
        body.append("  <rte><name>%s</name>\n%s\n  </rte>" % (_esc(label), "\n".join(rows)))
        if "track" not in parts:
            n_out = len(idx)

    gpx = head + "\n".join(b for b in body if b.strip()) + "\n</gpx>\n"
    return {"ok": True, "gpx": gpx, "stats": {
        "mode": mode, "parts": sorted(parts), "waypoint_kinds": sorted(waypoint_kinds),
        "km": round(total_km, 2), "points_in": len(pts), "points_out": n_out,
        "turns": sum(1 for m in marks if m["kind"] == "turn"),
        "crossings": len({m["event"] for m in marks if m["kind"] == "crossing"}),
        "waypoints_shown": len(shown_marks), "waypoints_dropped": wp_dropped,
    }}


def _wpt_block(spts: Sequence[dict], marks: Sequence[dict]) -> str:
    return "\n  ".join(_wpt(spts[m["i"]], m["name"], m["desc"], m["sym"]) for m in marks)


def _tolerance_filter(xy: Sequence[Tuple[float, float]], tol: float) -> List[int]:
    """Douglas-Peucker to a metre tolerance (iterative); kept indices."""
    n = len(xy)
    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
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
        if best > tol:
            keep[at] = True
            stack.append((a, at))
            stack.append((at, b))
    return [i for i, k in enumerate(keep) if k]


# --- self test ----------------------------------------------------------------------------

def _synthetic(points_xy: Sequence[Tuple[float, float]], lat0: float = 46.0, lon0: float = 6.0) -> str:
    kx = math.cos(math.radians(lat0)) * 111320.0
    pts = [{"lat": lat0 + y / 110540.0, "lon": lon0 + x / kx, "ele": 100.0} for x, y in points_xy]
    return geo_util.points_to_gpx(pts, "test")


def _densify(corners: Sequence[Tuple[float, float]], step: float = 5.0) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = [corners[0]]
    for (x0, y0), (x1, y1) in zip(corners, corners[1:]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) // step))
        out += [(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n) for k in range(1, n + 1)]
    return out


def _selftest() -> int:
    import re
    # 1) an L: 500 m north then 500 m east -> exactly one right turn near 0.5 km
    r = build(_synthetic(_densify([(0, 0), (0, 500), (500, 500)])), "track")
    assert r["ok"] and r["stats"]["turns"] == 1, r
    m = re.search(r"<name>Right (\d+\.\d)</name>", r["gpx"])
    assert m and abs(float(m.group(1)) - 0.5) < 0.05, r["gpx"][:600]
    # 2) mirror -> left turn
    r = build(_synthetic(_densify([(0, 0), (0, 500), (-500, 500)])), "track")
    assert re.search(r"<name>Left \d", r["gpx"]), r["gpx"][:400]
    # 3) a figure-of-eight crossing itself straight at ~(0,0)
    loop = [(-300, -300), (300, 300), (300, 600), (-300, 600), (-300, 300), (300, -300)]
    r = build(_synthetic(_densify(loop)), "track")
    assert r["stats"]["crossings"] == 1, r["stats"]
    assert "Cross Straight" in r["gpx"] or "Cross Bear" in r["gpx"], r["gpx"][:800]
    # 4) route mode obeys the via cap and keeps both ends
    big = _densify([(0, 0), (0, 400), (400, 400), (400, 0), (800, 0), (800, 400), (1200, 400), (1200, 0)], 5.0)
    r = build(_synthetic(big), "route", max_via=12)
    assert r["ok"] and r["gpx"].count("<rtept") <= 12 and r["gpx"].count("<rtept") >= 8, r["stats"]
    # 5) track mode caps the number of track points
    r = build(_synthetic(big), "track", max_track=60)
    assert r["gpx"].count("<trkpt") <= 60, r["stats"]
    # 6) a straight line has no turns; junk input is a clean error
    assert build(_synthetic(_densify([(0, 0), (0, 2000)])), "track")["stats"]["turns"] == 0
    assert build("<gpx></gpx>")["ok"] is False
    print("etrex_export selftest OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Add turn/crossing waypoints (or a smart <=50-point route) for Garmin eTrex.")
    ap.add_argument("track", nargs="?", help="GPX file")
    ap.add_argument("--mode", choices=("track", "route"), default="track")
    ap.add_argument("--name")
    ap.add_argument("--max-track", type=int, default=10000)
    ap.add_argument("--max-via", type=int, default=50)
    ap.add_argument("--min-turn", type=float, default=MIN_TURN_DEG)
    ap.add_argument("--cross-m", type=float, default=CROSS_M)
    ap.add_argument("--reverse", action="store_true", help="ride the route the other way")
    ap.add_argument("--parts", help="comma list overriding --mode: any of track,route,waypoints "
                                     "(e.g. track,route for a combined file with no waypoint markers)")
    ap.add_argument("--waypoint-kinds", help="comma list of which marks get a <wpt>: turn,crossing "
                                              "(default both; e.g. 'crossing' to isolate crossings for a hardware test)")
    ap.add_argument("--json", action="store_true", help="print {ok,gpx,stats} instead of bare GPX")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.track:
        ap.error("a GPX file is required")
    raw = open(args.track, "r", encoding="utf-8", errors="replace").read()
    parts = args.parts.split(",") if args.parts else None
    waypoint_kinds = args.waypoint_kinds.split(",") if args.waypoint_kinds else None
    res = build(raw, args.mode, args.name, args.max_track, args.max_via, args.min_turn, args.cross_m, args.reverse,
                parts, waypoint_kinds)
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
