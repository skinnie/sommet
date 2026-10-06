#!/usr/bin/env python3
"""Direction pins for devices that draw a route as a bare line (Garmin eTrex 30, Suunto Ambit).

A line on the screen is enough everywhere it simply bends. It stops being enough where the route
meets itself: a crossing, the two ends of an out-and-back, the base of a lollipop loop, the centre
of a figure-8. There you can take a wrong branch and still be "on the line", so the device cannot
warn you. Those spots, and only those, get a pin. Rules agreed with André on real hardware
(2026-09-30 .. 2026-10-05, eTrex 30 + Ambit3 Peak):

  where      only where the route's own line has 3+ branches, plus "Turn back" at a turnaround.
             Bends and hairpins get nothing. A turnaround shorter than MIN_SPUR_M is a drawing slip
             (or a hairpin) and is ignored.
  how many   one pin per visit, PIN_BEFORE_M before the junction on the path that visit arrives on,
             so the two visits' pins sit on different parts of the route.
  the word   line up the route's branches as you ARRIVE and say which one to take: Left / Straight /
             Right. Not the compass angle of your own turn (a curvy trail reads as "right" when the
             only other branches are sharper on both sides - André's km 8.1 case).
  the name   "<word> <km>", e.g. "Left 1.2". Km is the route distance at the junction.
  backup     an "OK <km>" pin OK_AFTER_M after a decision, unless the next pin is closer than
             OK_MIN_GAP_M.
  too close  pins closer than MERGE_M along the route (about GPS precision) become one pin naming
             the steps in order: "Back, left 1.2". An OK that close to another pin is dropped.

Targets:
  etrex   the original track untouched + the pins as waypoints (+ the source file's own waypoints)
  ambit   a route (<rte>) whose pins are route waypoints: names <= 15 bytes, each on a route point,
          plus Start (and End when the route is not a loop). The Ambit announces waypoints strictly
          in order ("Approaching <name>"), so the route must be walked from its start.

    ./tools/route_pins.py hike.gpx --target etrex > hike_etrex.gpx
    ./tools/route_pins.py hike.gpx --target ambit --name "Cap Roux" > hike_ambit.gpx
    ./tools/route_pins.py hike.gpx --json          # {ok, gpx, stats, pins, events}

Stdlib only (via geo_util). `--selftest` is offline.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple

import geo_util

STEP_M = 5.0             # resampling step for the analysis (the output track is never resampled)
SAME_PATH_M = 12.0       # two passes closer than this are on the same path...
MIN_SEP_M = 60.0         # ...when at least this far apart along the route
TIP_OVERLAP_M = 8.0      # a turnaround: the route before and after the tip stays this close
MIN_SPUR_M = 15.0        # a turnaround shorter than this is a drawing slip or a hairpin: no pin
JUNCTION_MERGE_M = 35.0  # junction candidates this close are one junction (a 6 m dogleg is still
                         # one crossroads on the ground)
PASS_RADIUS_M = 25.0
ARM_M = 25.0             # a branch's direction is measured this far from the junction, not at it
BRANCH_MERGE_DEG = 35.0
STRAIGHT_DEG = 30.0
PIN_BEFORE_M = 20.0
PIN_CLEAR_M = 8.0
OK_AFTER_M = 100.0
OK_MIN_GAP_M = 150.0
MERGE_M = 30.0
AMBIT_NAME_BYTES = 15
AMBIT_MAX_PINS = 80      # the watch holds 100 route waypoints in total, shared by every route on it
ABBREV = {"Left": "L", "Right": "R", "Straight": "S", "Back": "B", "Slight left": "SL",
          "Slight right": "SR", "Start": "Start"}
SYM = {"decision": "Flag, Blue", "turn": "Flag, Red", "merged": "Flag, Blue", "ok": "Pin, Green"}

XY = Tuple[float, float]


def _bearing(a: XY, b: XY) -> float:
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) % 360.0


def _turn(heading: float, brg: float) -> float:
    d = (brg - heading + 180.0) % 360.0 - 180.0
    return 180.0 if d == -180.0 else d


def _dist(a: XY, b: XY) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def analyze(pts: Sequence[dict]) -> dict:
    """pts: [{'lat','lon','ele'?}] in riding order. Returns the junction visits and turnarounds."""
    lat0, lon0 = pts[0]["lat"], pts[0]["lon"]
    kx = math.cos(math.radians(lat0)) * 111320.0
    ky = 110540.0
    xy = [((p["lon"] - lon0) * kx, (p["lat"] - lat0) * ky) for p in pts]
    cum = [0.0]
    for i in range(1, len(xy)):
        cum.append(cum[-1] + _dist(xy[i], xy[i - 1]))

    res, al = [xy[0]], [0.0]
    trav, nxt = 0.0, STEP_M
    for i in range(1, len(xy)):
        (x0, y0), (x1, y1) = xy[i - 1], xy[i]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg == 0:
            continue
        while nxt <= trav + seg:
            f = (nxt - trav) / seg
            res.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
            al.append(nxt)
            nxt += STEP_M
        trav += seg
    if al[-1] < trav - 0.5:
        res.append(xy[-1])
        al.append(trav)
    n = len(res)

    # turnarounds: the route before and after a point stays together
    tips: List[Tuple[int, int]] = []
    for k in range(1, n - 1):
        m = 0
        while k - m - 1 >= 0 and k + m + 1 < n and _dist(res[k - m - 1], res[k + m + 1]) <= TIP_OVERLAP_M:
            m += 1
        if m >= 2:
            tips.append((k, m))
    tip_list: List[Tuple[int, int]] = []
    for k, m in sorted(tips, key=lambda t: -t[1]):
        if all(abs(k - k2) > m2 + 2 for k2, m2 in tip_list):
            tip_list.append((k, m))
    spikes = []
    for k, m in list(tip_list):
        leg = max(_dist(res[k], res[k - q]) for q in range(1, m + 1))
        if leg < MIN_SPUR_M:
            tip_list.remove((k, m))
            spikes.append({"idx": k, "km": al[k] / 1000.0, "leg": leg})

    # junction candidates: the base of each turnaround + both ends of every shared stretch
    cand: List[XY] = [((res[k - m][0] + res[k + m][0]) / 2, (res[k - m][1] + res[k + m][1]) / 2)
                      for k, m in tip_list]
    cell = SAME_PATH_M
    grid: Dict[Tuple[int, int], List[int]] = {}
    for i, p in enumerate(res):
        grid.setdefault((int(p[0] // cell), int(p[1] // cell)), []).append(i)
    shared = [False] * n
    for i, p in enumerate(res):
        cx, cy = int(p[0] // cell), int(p[1] // cell)
        shared[i] = any(abs(al[j] - al[i]) >= MIN_SEP_M and _dist(p, res[j]) <= SAME_PATH_M
                        for gx in (cx - 1, cx, cx + 1) for gy in (cy - 1, cy, cy + 1)
                        for j in grid.get((gx, gy), ()))
    for i in range(n):
        if shared[i] and (i == 0 or not shared[i - 1]):
            cand.append(res[i])
        if shared[i] and (i == n - 1 or not shared[i + 1]):
            cand.append(res[i])
    clusters: List[List[XY]] = []
    for c in cand:
        hit = [cl for cl in clusters if any(_dist(c, o) <= JUNCTION_MERGE_M for o in cl)]
        merged = [c]
        for cl in hit:
            merged += cl
            clusters.remove(cl)
        clusters.append(merged)
    centers = [(sum(p[0] for p in cl) / len(cl), sum(p[1] for p in cl) / len(cl)) for cl in clusters]

    events: List[dict] = []
    arm_n = int(ARM_M / STEP_M)
    for jid, jc in enumerate(centers):
        d = [_dist(p, jc) for p in res]
        minima = [i for i in range(n) if d[i] <= PASS_RADIUS_M
                  and (i == 0 or d[i] <= d[i - 1]) and (i == n - 1 or d[i] < d[i + 1])]
        # two closest approaches are separate visits only if the route goes away in between
        passes: List[int] = []
        for i in minima:
            if passes:
                prev = passes[-1]
                gap_max = max(d[prev:i + 1])
                if gap_max <= PASS_RADIUS_M and gap_max - max(d[prev], d[i]) <= TIP_OVERLAP_M:
                    if d[i] < d[prev]:
                        passes[-1] = i
                    continue
            passes.append(i)

        def arm(c: int, direction: int) -> Optional[float]:
            rng = range(c - 1, max(-1, c - arm_n - 1), -1) if direction < 0 else range(c + 1, min(n, c + arm_n + 1))
            far = max(rng, key=lambda r: d[r], default=None)
            if far is None or d[far] < 6.0:
                return None
            return _bearing(jc, res[far])

        arms = [(c, arm(c, -1), arm(c, +1)) for c in passes]
        branches: List[float] = []
        for _, a, b in arms:
            for brg in (a, b):
                if brg is not None and not any(abs(_turn(o, brg)) <= BRANCH_MERGE_DEG for o in branches):
                    branches.append(brg)
        if len(branches) < 3:
            continue
        br_of = lambda brg: min(range(len(branches)), key=lambda q: abs(_turn(branches[q], brg)))
        for c, a, b in arms:
            if a is None or b is None:
                continue  # the route starts or ends here: nothing to decide
            arr, dep = br_of(a), br_of(b)
            heading = (a + 180.0) % 360.0
            if arr == dep:
                word = "Turn back"
            else:
                opts = sorted((_turn(heading, branches[q]), q) for q in range(len(branches)) if q != arr)
                chosen = next(t for t, q in opts if q == dep)
                rank = [q for _, q in opts].index(dep)
                if len(opts) == 2:
                    other = next(t for t, q in opts if q != dep)
                    word = "Straight" if abs(chosen) <= STRAIGHT_DEG < abs(other) else ("Left" if rank == 0 else "Right")
                elif len(opts) == 3:
                    word = ("Left", "Straight", "Right")[rank]
                elif rank == 0:
                    word = "Left"
                elif rank == len(opts) - 1:
                    word = "Right"
                else:
                    word = "Straight" if abs(chosen) <= STRAIGHT_DEG else ("Slight left" if chosen < 0 else "Slight right")
            options = []
            for q in range(len(branches)):
                if q == arr:
                    continue
                used = sorted({round(al[c2] / 1000.0, 1) for c2, a2, b2 in arms if c2 != c and
                               ((a2 is not None and br_of(a2) == q) or (b2 is not None and br_of(b2) == q))})
                options.append({"brg": branches[q], "turn": round(_turn(heading, branches[q]), 1),
                                "chosen": q == dep, "usedKm": used})
            events.append({"kind": "decision", "idx": c, "km": al[c] / 1000.0, "word": word, "jid": jid,
                           "arrive": a, "leave": b, "branches": list(branches), "options": options})
    for k, m in tip_list:
        back = _bearing(res[k], res[max(0, k - m)])
        events.append({"kind": "turn", "idx": k, "km": al[k] / 1000.0, "word": "Turn back", "jid": -1,
                       "arrive": back, "leave": back, "branches": [back], "options": []})
    events.sort(key=lambda e: e["km"])
    return {"pts": list(pts), "xy": xy, "cum": cum, "res": res, "al": al, "events": events, "spikes": spikes,
            "ll": lambda p: (lat0 + p[1] / ky, lon0 + p[0] / kx)}


def _short_name(words: List[str], km: Optional[str], limit: Optional[int]) -> str:
    w = [words[0].replace("Turn back", "Back") if len(words) > 1 else words[0]] + \
        [x.replace("Turn back", "back").lower() for x in words[1:]]
    full = ", ".join(w) + (" %s" % km if km else "")
    if limit is None or len(full.encode()) <= limit:
        return full
    if len(", ".join(w).encode()) <= limit:
        return ", ".join(w)
    short = ",".join(ABBREV.get(x.replace("Turn back", "Back"), x[:2]) for x in words) + (" %s" % km if km else "")
    return short.encode()[:limit].decode(errors="ignore")


def make_pins(an: dict, target: str = "etrex", ok_pins: bool = True) -> List[dict]:
    """[{kind, name, along, xy}] in riding order. kind: start | end | decision | turn | merged | ok."""
    res, al, events = an["res"], an["al"], an["events"]
    raw: List[dict] = []
    for e in events:
        if e["kind"] == "turn":
            raw.append({"kind": "turn", "words": ["Turn back"], "km": "%.1f" % e["km"], "i": e["idx"]})
    for e in events:
        if e["kind"] != "decision":
            continue
        c, spot = e["idx"], e["idx"]
        for back in range(int(PIN_BEFORE_M / STEP_M), 0, -1):
            q = max(0, c - back)
            if all(_dist(res[q], res[o["i"]]) >= PIN_CLEAR_M for o in raw):
                spot = q
                break
        raw.append({"kind": "decision", "words": [e["word"]], "km": "%.1f" % e["km"], "i": spot})
    if ok_pins:
        for n_e, e in enumerate(events):
            next_km = events[n_e + 1]["km"] if n_e + 1 < len(events) else al[-1] / 1000.0
            if (next_km - e["km"]) * 1000.0 < OK_MIN_GAP_M:
                continue
            q = min(range(len(al)), key=lambda r: abs(al[r] - (al[e["idx"]] + OK_AFTER_M)))
            raw.append({"kind": "ok", "words": ["OK"], "km": "%.1f" % (al[q] / 1000.0), "i": q})
    is_loop = _dist(res[0], res[-1]) < 1.0
    if target == "ambit":
        raw.append({"kind": "start", "words": ["Start"], "km": None, "i": 0})
        if not is_loop:
            raw.append({"kind": "end", "words": ["End"], "km": None, "i": len(res) - 1})
    order = {"start": 0, "turn": 1, "decision": 1, "ok": 1, "end": 2}
    raw.sort(key=lambda p: (al[p["i"]], order[p["kind"]]))

    kept: List[dict] = []
    for p in raw:
        prev = kept[-1] if kept else None
        if prev is not None and al[p["i"]] - al[prev["i"]] < MERGE_M and p["kind"] != "end" and prev["kind"] != "end":
            if p["kind"] == "ok":
                continue
            if prev["kind"] == "ok":
                kept[-1] = p
                continue
            prev["words"] = prev["words"] + p["words"]
            prev["km"] = prev["km"] or None
            if prev["kind"] != "start":
                prev["kind"] = "merged"
            continue
        kept.append(dict(p))
    limit = AMBIT_NAME_BYTES if target == "ambit" else None
    return [{"kind": p["kind"], "name": _short_name(p["words"], p["km"], limit),
             "along": al[p["i"]], "xy": res[p["i"]]} for p in kept]


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_WPT = re.compile(r'<wpt\b[^>]*?lat="([-\d.]+)"[^>]*?lon="([-\d.]+)"[^>]*>(.*?)</wpt>', re.S)
_NAME = re.compile(r"<name>([^<]*)</name>")


def source_waypoints(gpx_text: str) -> List[Tuple[float, float, str]]:
    out = []
    for lat, lon, body in _WPT.findall(gpx_text):
        m = _NAME.search(body)
        if m and m.group(1).strip():
            out.append((float(lat), float(lon), m.group(1).strip()))
    return out


def etrex_gpx(an: dict, pins: Sequence[dict], name: str, extra_wpts: Sequence[Tuple[float, float, str]] = ()) -> str:
    ll = an["ll"]
    rows = []
    for p in pins:
        lat, lon = ll(p["xy"])
        rows.append('  <wpt lat="%.7f" lon="%.7f"><name>%s</name><sym>%s</sym></wpt>'
                    % (lat, lon, _esc(p["name"]), SYM.get(p["kind"], "Flag, Blue")))
    for lat, lon, nm in extra_wpts:
        rows.append('  <wpt lat="%.7f" lon="%.7f"><name>%s</name><sym>Scenic Area</sym></wpt>' % (lat, lon, _esc(nm[:30])))
    trk = []
    for p in an["pts"]:
        ele = "" if p.get("ele") is None else "<ele>%.1f</ele>" % p["ele"]
        trk.append('    <trkpt lat="%.7f" lon="%.7f">%s</trkpt>' % (p["lat"], p["lon"], ele))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">\n'
            + "".join(r + "\n" for r in rows)
            + "  <trk><name>%s</name><trkseg>\n%s\n  </trkseg></trk>\n</gpx>\n" % (_esc(name), "\n".join(trk)))


def ambit_gpx(an: dict, pins: Sequence[dict], name: str) -> str:
    """A route whose pins are waypoints sitting exactly on route points (build_route.py matches a
    waypoint to a point within 1e-7 deg), in riding order. A pin's point is inserted into the line."""
    ll, pts, cum = an["ll"], an["pts"], an["cum"]
    seq = [(c, round(p["lat"], 7), round(p["lon"], 7), p.get("ele"), None) for c, p in zip(cum, pts)]
    wpts = []
    for p in pins:
        if p["kind"] == "start":
            wpts.append((seq[0][1], seq[0][2], p["name"]))
        elif p["kind"] == "end":
            continue
        else:
            lat, lon = ll(p["xy"])
            lat, lon = round(lat, 7), round(lon, 7)
            # nudge off any existing point so the match is unique (first match wins in build_route)
            while any(abs(s[1] - lat) < 1e-7 and abs(s[2] - lon) < 1e-7 for s in seq):
                lat = round(lat + 2e-7, 7)
            seq.append((p["along"], lat, lon, None, p["name"]))
            wpts.append((lat, lon, p["name"]))
    seq.sort(key=lambda s: (s[0], s[4] is not None))
    line, last_ele = [], next((s[3] for s in seq if s[3] is not None), 0.0)
    for _, lat, lon, ele, _nm in seq:
        if ele is None:
            ele = last_ele
        last_ele = ele
        line.append((lat, lon, ele))
    end = next((p for p in pins if p["kind"] == "end"), None)
    if end is not None:
        lat, lon = line[-1][0], line[-1][1]
        if any(abs(w[0] - lat) < 1e-7 and abs(w[1] - lon) < 1e-7 for w in wpts) or \
                sum(1 for q in line if abs(q[0] - lat) < 1e-7 and abs(q[1] - lon) < 1e-7) > 1:
            lat = round(lat + 2e-7, 7)
            line[-1] = (lat, lon, line[-1][2])
        wpts.append((lat, lon, end["name"]))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">\n'
            "  <metadata><name>%s</name></metadata>\n" % _esc(name)
            + "".join('  <wpt lat="%.7f" lon="%.7f"><name>%s</name><type>Waypoint</type></wpt>\n' % (la, lo, _esc(nm))
                      for la, lo, nm in wpts)
            + "  <rte><name>%s</name>\n" % _esc(name)
            + "".join('    <rtept lat="%.7f" lon="%.7f"><ele>%.1f</ele></rtept>\n' % q for q in line)
            + "  </rte>\n</gpx>\n")


def build(gpx_text: str, target: str = "etrex", name: Optional[str] = None, reverse: bool = False,
          ok_pins: bool = True, plain: bool = False) -> dict:
    """{ok, gpx, stats, pins, events, spikes} or {ok: False, error}."""
    if target not in ("etrex", "ambit"):
        return {"ok": False, "error": "target must be 'etrex' or 'ambit'"}
    try:
        pts = geo_util.parse_gpx_points(gpx_text)
    except Exception as e:  # malformed XML
        return {"ok": False, "error": "could not read GPX: %s" % e}
    if len(pts) < 2:
        return {"ok": False, "error": "no track points found in this GPX"}
    if reverse:
        pts = list(reversed(pts))
    an = analyze(pts)
    if plain:  # no pins at all: just the line (and, for the Ambit, its Start/End)
        an = dict(an, events=[])
    pins = make_pins(an, target, ok_pins)
    if target == "ambit" and len(pins) > AMBIT_MAX_PINS:
        pins = make_pins(an, target, False)  # the OK backups go first
        if len(pins) > AMBIT_MAX_PINS:
            return {"ok": False, "error": "this route needs %d pins and the watch only has room for about %d "
                                          "- split it into shorter routes (for example one per day)"
                                          % (len(pins), AMBIT_MAX_PINS)}
    label = name or ("Route" if target == "ambit" else "eTrex route")
    gpx = ambit_gpx(an, pins, label) if target == "ambit" else etrex_gpx(an, pins, label, source_waypoints(gpx_text))
    ll = an["ll"]
    return {"ok": True, "gpx": gpx,
            "stats": {"target": target, "km": round(an["al"][-1] / 1000.0, 2), "points_in": len(pts),
                      "junctions": len({e["jid"] for e in an["events"] if e["kind"] == "decision"}),
                      "decisions": sum(1 for e in an["events"] if e["kind"] == "decision"),
                      "turnarounds": sum(1 for e in an["events"] if e["kind"] == "turn"),
                      "pins": sum(1 for p in pins if p["kind"] not in ("start", "end")),
                      "ignored_spikes": len(an["spikes"])},
            "pins": [{"kind": p["kind"], "name": p["name"], "km": round(p["along"] / 1000.0, 3),
                      "ll": list(ll(p["xy"]))} for p in pins],
            "events": [{"kind": e["kind"], "km": round(e["km"], 3), "word": e["word"], "jid": e["jid"],
                        "ll": list(ll(an["res"][e["idx"]])), "arrive": e["arrive"], "leave": e["leave"],
                        "branches": e["branches"], "options": e["options"]} for e in an["events"]],
            "spikes": [{"km": round(s["km"], 3), "leg": round(s["leg"], 1), "ll": list(ll(an["res"][s["idx"]]))}
                       for s in an["spikes"]]}


# --- self test ----------------------------------------------------------------------------

def _synthetic(corners: Sequence[XY], step: float = 10.0, lat0: float = 46.0, lon0: float = 6.0) -> str:
    kx = math.cos(math.radians(lat0)) * 111320.0
    xy = [corners[0]]
    for (x0, y0), (x1, y1) in zip(corners, corners[1:]):
        k = max(1, int(math.hypot(x1 - x0, y1 - y0) // step))
        xy += [(x0 + (x1 - x0) * q / k, y0 + (y1 - y0) * q / k) for q in range(1, k + 1)]
    return geo_util.points_to_gpx([{"lat": lat0 + y / 110540.0, "lon": lon0 + x / kx, "ele": 100.0} for x, y in xy], "test")


TEST_WALK = [(0, 0), (0, 300), (300, 300), (300, 700), (-300, 700), (-300, 300), (0, 300), (0, 0), (600, 0),
             (600, 500), (600, 0), (1500, 0), (1800, 0), (1800, 300), (1500, 300), (1500, 0), (1500, -300),
             (1200, -300), (1200, -900), (1500, -900), (1500, -1200), (1200, -1200), (1200, -900), (600, -900)]


def _selftest() -> int:
    names = lambda r, kinds=("decision", "turn", "merged"): [p["name"] for p in r["pins"] if p["kind"] in kinds]
    # 1) the walk André approved on the map: lollipop, out-and-back, crossroads, figure-8 centre
    r = build(_synthetic(TEST_WALK), "etrex")
    assert r["ok"], r
    want = ["Right 0.3", "Right 2.3", "Left 3.2", "Turn back 3.7", "Left 4.2", "Straight 5.1", "Straight 6.3",
            "Left 7.5", "Left 8.7"]
    assert names(r) == want, names(r)
    assert "OK 0.4" in names(r, ("ok",)) and r["gpx"].count("<trkpt") == r["stats"]["points_in"], r["stats"]
    # 2) nothing to decide: a straight line, an L, a plain closed loop, a hairpin
    for shape in ([(0, 0), (0, 2000)], [(0, 0), (0, 500), (500, 500)],
                  [(0, 0), (0, 500), (500, 500), (500, 0), (0, 0)], [(0, 0), (0, 300), (6, 310), (12, 300), (12, 0)]):
        assert names(build(_synthetic(shape), "etrex")) == [], (shape, names(build(_synthetic(shape), "etrex")))
    # 3) a 20 m viewpoint spur: the turn-back and the way out are 20 m apart -> one merged pin
    spur = [(0, 0), (0, 300), (20, 300), (0, 300), (0, 600)]
    assert names(build(_synthetic(spur, 5.0), "etrex")) == ["Right 0.3", "Back, right 0.3"], names(build(_synthetic(spur, 5.0), "etrex"))
    # 4) a 7 m poke off the path is a drawing slip: no pin, reported as ignored
    r = build(_synthetic([(0, 0), (0, 300), (7, 300), (0, 300), (0, 600)], 1.0), "etrex")
    assert names(r) == [] and r["stats"]["ignored_spikes"] == 1, (names(r), r["stats"])
    # 5) Ambit: every name fits 15 bytes, waypoints sit on route points in riding order, Start first
    r = build(_synthetic(TEST_WALK), "ambit", name="Test")
    wp = re.findall(r'<wpt lat="([-\d.]+)" lon="([-\d.]+)"><name>([^<]*)</name>', r["gpx"])
    rte = re.findall(r'<rtept lat="([-\d.]+)" lon="([-\d.]+)"', r["gpx"])
    assert wp[0][2] == "Start" and wp[-1][2] == "End" and all(len(n.encode()) <= 15 for _, _, n in wp), wp
    idx = [rte.index((la, lo)) for la, lo, _ in wp[:-1]] + [len(rte) - 1]
    assert idx == sorted(idx) and (wp[-1][0], wp[-1][1]) == rte[-1], idx
    # 6) a loop that starts into a junction: the first pin merges into "Start, ..." and there is no End
    lolli = [(0, 0), (0, 20), (200, 20), (200, 220), (-200, 220), (-200, 20), (0, 20), (0, 0)]
    r = build(_synthetic(lolli, 5.0), "ambit")
    wn = _NAME.findall(r["gpx"].split("<rte>")[0])[1:]
    assert wn[0].startswith("Start, ") and "End" not in wn, wn
    assert build("<gpx></gpx>")["ok"] is False
    r = build(_synthetic(TEST_WALK), "ambit", plain=True)
    assert r["ok"] and [p["name"] for p in r["pins"]] == ["Start", "End"], r["pins"]
    print("route_pins selftest OK")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Direction pins where a route meets itself (eTrex / Ambit).")
    ap.add_argument("track", nargs="?", help="GPX file")
    ap.add_argument("--target", choices=("etrex", "ambit"), default="etrex")
    ap.add_argument("--name")
    ap.add_argument("--reverse", action="store_true", help="walk the route the other way")
    ap.add_argument("--no-ok", action="store_true", help="leave out the OK backup pins")
    ap.add_argument("--plain", action="store_true", help="no pins: only the line (Ambit: + Start/End)")
    ap.add_argument("--json", action="store_true", help="print {ok,gpx,stats,pins,events} instead of bare GPX")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.track:
        ap.error("a GPX file is required")
    raw = open(args.track, "r", encoding="utf-8", errors="replace").read()
    res = build(raw, args.target, args.name, args.reverse, not args.no_ok, args.plain)
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
