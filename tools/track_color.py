#!/usr/bin/env python3
"""Colour a track by climb gradient - the OruxMaps-style "route painted by steepness" that
makes a hard pitch jump out on the map and on the elevation profile before you ever ride or
walk it.

Given a track (GPX, or JSON points with lat/lon and optional ele), this:
  1. resamples it to an even step so slope isn't dominated by irregular OSM node spacing,
  2. derives gradient over a distance window (raw point-to-point slope is noise - a 1 m
     elevation quantum across a 3 m node gap reads as a fake 30 %),
  3. classifies each stretch into a gradient bucket with a colour,
  4. emits ready-to-draw coloured polyline segments + a shared legend + an elevation
     profile series coloured the same way, so the red bit on the map and the red bit on the
     graph are obviously the same nasty climb.

The bucket table is data, not code, so a caller can retune it per sport (a gravel climb and
a hiking climb "feel steep" at different numbers). The exact same machinery re-skins to
colour by surface or, for a synced activity, by speed/HR - it's just "classify a value along
a polyline". Here it's gradient because that's the one everyone asked for first.

Stdlib only. Fully offline and hardware-free: `--selftest` proves the maths on a synthetic
hill with no data downloaded.

    ./tools/track_color.py route.gpx                 # -> JSON on stdout
    ./tools/track_color.py track.json --step 25 --window 120
    ./tools/track_color.py route.gpx --hgt ~/srtm    # fill missing ele from .hgt tiles
    ./tools/track_color.py --selftest
"""

import argparse
import json
import sys

import geo_util

# Ordered low->high by upper bound. `upper` is the exclusive top of the band in percent;
# the last band is open-ended (its `upper` is only a label hint). Colours are a cool->hot
# ramp: descents blue/green, climbs yellow->orange->red->purple, chosen to stay legible for
# the common red-green colour blindness (the climb side is a warm luminance ramp, not
# red-vs-green). Retune freely - it's just a list.
DEFAULT_BUCKETS = [
    {"key": "descent",    "label": "descent",       "upper": -3.0, "color": "#4575b4"},
    {"key": "flat",       "label": "flat",          "upper": 3.0,  "color": "#66bd63"},
    {"key": "gentle",     "label": "3-6 % gentle",  "upper": 6.0,  "color": "#fee08b"},
    {"key": "moderate",   "label": "6-9 % moderate", "upper": 9.0, "color": "#fdae61"},
    {"key": "steep",      "label": "9-12 % steep",  "upper": 12.0, "color": "#f46d43"},
    {"key": "very_steep", "label": "12-15 % v.steep", "upper": 15.0, "color": "#d73027"},
    {"key": "brutal",     "label": ">15 % brutal",  "upper": float("inf"), "color": "#7a0177"},
]

UNKNOWN_COLOR = "#9e9e9e"  # no elevation available -> grey, never a fake gradient


def classify(gradient_pct, buckets):
    """First bucket whose exclusive upper bound the gradient falls under."""
    for b in buckets:
        if gradient_pct < b["upper"]:
            return b
    return buckets[-1]


def _resample(points, dists, step_m):
    """Even-step resample of the polyline. Returns parallel lists (lat, lon, ele, dist) at
    0, step, 2*step, ... metres, linearly interpolating each field. Points may carry ele
    None; a resampled ele is None if either side is None (so a gap stays honestly unknown
    rather than inventing a slope across it)."""
    total = dists[-1]
    lats, lons, eles, ds = [], [], [], []
    j = 0
    d = 0.0
    while d <= total + 1e-6:
        while j < len(dists) - 2 and dists[j + 1] < d:
            j += 1
        seg = dists[j + 1] - dists[j]
        t = 0.0 if seg <= 0 else (d - dists[j]) / seg
        t = max(0.0, min(1.0, t))
        a, b = points[j], points[j + 1]
        lats.append(a["lat"] + (b["lat"] - a["lat"]) * t)
        lons.append(a["lon"] + (b["lon"] - a["lon"]) * t)
        if a.get("ele") is None or b.get("ele") is None:
            eles.append(None)
        else:
            eles.append(a["ele"] + (b["ele"] - a["ele"]) * t)
        ds.append(d)
        d += step_m
    # always pin the true final vertex so the drawn line reaches the destination
    last = points[-1]
    if ds and total - ds[-1] > step_m * 0.25:
        lats.append(last["lat"]); lons.append(last["lon"]); eles.append(last.get("ele")); ds.append(total)
    return lats, lons, eles, ds


def _windowed_gradient(eles, ds, window_m):
    """Central-difference gradient (%) at each sample over a ~window_m span - the smoothing
    that turns quantised elevation into a usable slope. None where elevation is missing."""
    n = len(eles)
    grads = [None] * n
    half = window_m / 2.0
    for i in range(n):
        lo = i
        while lo > 0 and ds[i] - ds[lo] < half:
            lo -= 1
        hi = i
        while hi < n - 1 and ds[hi] - ds[i] < half:
            hi += 1
        if eles[lo] is None or eles[hi] is None:
            continue
        run = ds[hi] - ds[lo]
        if run <= 0:
            grads[i] = 0.0
        else:
            grads[i] = (eles[hi] - eles[lo]) / run * 100.0
    return grads


def colorize(points, step_m=30.0, window_m=100.0, buckets=None, hgt_dir=None):
    """Track points [{'lat','lon','ele'?}] -> a dict of coloured segments, legend, elevation
    profile and summary. `step_m` is the resample spacing, `window_m` the gradient smoothing
    span. If elevation is missing and `hgt_dir` is given, fill it from SRTM .hgt tiles."""
    buckets = buckets or DEFAULT_BUCKETS
    pts = [{"lat": float(p["lat"]), "lon": float(p["lon"]),
            "ele": (None if p.get("ele") in (None, "") else float(p["ele"]))} for p in points]
    if len(pts) < 2:
        return {"ok": False, "error": "a track needs at least 2 points", "segments": [],
                "legend": [], "profile": [], "summary": {}}

    if hgt_dir and any(p["ele"] is None for p in pts):
        for p in pts:
            if p["ele"] is None:
                p["ele"] = geo_util.sample_hgt(p["lat"], p["lon"], hgt_dir)

    dists = geo_util.cumulative_distances([(p["lat"], p["lon"]) for p in pts])
    if dists[-1] <= 0:
        return {"ok": False, "error": "track has zero length", "segments": [],
                "legend": [], "profile": [], "summary": {}}

    lats, lons, eles, ds = _resample(pts, dists, step_m)
    grads = _windowed_gradient(eles, ds, window_m)

    # elevation profile series (one entry per resampled sample), coloured the same way
    profile = []
    for i in range(len(ds)):
        g = grads[i]
        color = UNKNOWN_COLOR if g is None else classify(g, buckets)["color"]
        profile.append({"dist_m": round(ds[i], 1),
                        "ele_m": None if eles[i] is None else round(eles[i], 1),
                        "grad_pct": None if g is None else round(g, 1),
                        "color": color})

    # merge consecutive same-bucket samples into drawable polyline segments
    segments = []
    per_bucket = {b["key"]: {"label": b["label"], "color": b["color"],
                             "distance_m": 0.0, "ascent_m": 0.0} for b in buckets}
    per_bucket["unknown"] = {"label": "no elevation", "color": UNKNOWN_COLOR,
                             "distance_m": 0.0, "ascent_m": 0.0}
    cur = None
    for i in range(len(ds)):
        g = grads[i]
        b = {"key": "unknown", "color": UNKNOWN_COLOR} if g is None else classify(g, buckets)
        if cur is None or cur["key"] != b["key"]:
            if cur is not None:
                segments.append(_finish_segment(cur))
            cur = {"key": b["key"], "color": b["color"], "coords": [], "grads": [],
                   "start_m": ds[i]}
        cur["coords"].append([round(lats[i], 6), round(lons[i], 6)])
        if g is not None:
            cur["grads"].append(g)
        cur["end_m"] = ds[i]
        # accumulate per-bucket distance / ascent
        if i > 0:
            seg_d = ds[i] - ds[i - 1]
            per_bucket[b["key"]]["distance_m"] += seg_d
            if eles[i] is not None and eles[i - 1] is not None and eles[i] > eles[i - 1]:
                per_bucket[b["key"]]["ascent_m"] += eles[i] - eles[i - 1]
    if cur is not None:
        segments.append(_finish_segment(cur))

    # totals
    asc = desc = 0.0
    clean = [e for e in eles if e is not None]
    for a, b in zip(clean, clean[1:]):
        if b > a:
            asc += b - a
        else:
            desc += a - b
    real_grads = [g for g in grads if g is not None]
    summary = {
        "distance_m": round(dists[-1], 1),
        "ascent_m": round(asc, 1),
        "descent_m": round(desc, 1),
        "max_gradient_pct": round(max(real_grads), 1) if real_grads else None,
        "min_gradient_pct": round(min(real_grads), 1) if real_grads else None,
        "has_elevation": bool(clean),
    }
    legend = [{"key": k, **v, "distance_m": round(v["distance_m"], 1),
               "ascent_m": round(v["ascent_m"], 1)}
              for k, v in per_bucket.items() if v["distance_m"] > 0]

    return {"ok": True, "segments": segments, "legend": legend,
            "profile": profile, "summary": summary}


def _finish_segment(cur):
    avg = round(sum(cur["grads"]) / len(cur["grads"]), 1) if cur["grads"] else None
    return {"bucket": cur["key"], "color": cur["color"], "coords": cur["coords"],
            "from_m": round(cur["start_m"], 1), "to_m": round(cur["end_m"], 1),
            "avg_gradient_pct": avg}


# --- synthetic hill for --selftest -----------------------------------------------------

def _synthetic_hill():
    """A 2 km line heading east that climbs a symmetric bump: flat, then a steady ~10 %
    ramp, then back down. Elevation is deliberately quantised to whole metres so the test
    exercises the smoothing (raw per-point slope here would be spiky garbage)."""
    import math
    lat0, lon0 = 46.0, 6.0
    _, m_per_deg_lon = geo_util.meters_per_degree(lat0)
    pts = []
    for i in range(201):  # ~10 m spacing over 2 km
        east_m = i * 10.0
        # elevation: 0 for first 500 m, +10%/climb to 1000 m, -10% back to 1500 m, flat
        if east_m < 500:
            ele = 0.0
        elif east_m < 1000:
            ele = (east_m - 500) * 0.10
        elif east_m < 1500:
            ele = 50.0 - (east_m - 1000) * 0.10
        else:
            ele = 0.0
        pts.append({"lat": lat0, "lon": lon0 + east_m / m_per_deg_lon,
                    "ele": round(ele)})  # quantise to whole metres on purpose
    return pts


def _selftest():
    r = colorize(_synthetic_hill(), step_m=25.0, window_m=100.0)
    assert r["ok"], r
    # total distance ~2 km
    assert 1950 <= r["summary"]["distance_m"] <= 2050, r["summary"]
    # climbed ~50 m up and ~50 m down
    assert 45 <= r["summary"]["ascent_m"] <= 55, r["summary"]
    assert 45 <= r["summary"]["descent_m"] <= 55, r["summary"]
    # the steady 10 % ramp must be detected as a "steep" (9-12 %) stretch, coloured
    keys = {s["bucket"] for s in r["segments"]}
    assert "steep" in keys, ("expected a 9-12%% band, got %s" % sorted(keys))
    # and a descent band on the way down
    assert "descent" in keys, ("expected a descent band, got %s" % sorted(keys))
    # smoothing worked: no crazy spikes from the 1 m elevation quantum
    assert r["summary"]["max_gradient_pct"] <= 13.0, r["summary"]
    # legend distances add up to ~ total
    legtot = sum(l["distance_m"] for l in r["legend"])
    assert abs(legtot - r["summary"]["distance_m"]) < 60, (legtot, r["summary"])
    # profile is coloured and same length family as segments cover
    assert all("color" in p for p in r["profile"])
    print("track_color selftest OK:", json.dumps(r["summary"]))
    print(json.dumps({"ok": True, "selftest": "passed"}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Colour a track by climb gradient (offline).")
    ap.add_argument("track", nargs="?", help="GPX file, or JSON [{lat,lon,ele?}] / {points:[...]}")
    ap.add_argument("--step", type=float, default=30.0, help="resample spacing, m (default 30)")
    ap.add_argument("--window", type=float, default=100.0,
                    help="gradient smoothing window, m (default 100)")
    ap.add_argument("--hgt", help="dir of SRTM .hgt tiles, to fill missing elevation")
    ap.add_argument("--selftest", action="store_true", help="run the offline self-test and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.track:
        ap.error("a track file is required (or use --selftest)")

    raw = open(args.track, "r", encoding="utf-8", errors="replace").read()
    if args.track.lower().endswith(".gpx") or raw.lstrip().startswith("<"):
        points = geo_util.parse_gpx_points(raw)
    else:
        doc = json.loads(raw)
        points = doc["points"] if isinstance(doc, dict) else doc
    if not points:
        print(json.dumps({"ok": False, "error": "no points found in track"}))
        return 2

    result = colorize(points, step_m=args.step, window_m=args.window, hgt_dir=args.hgt)
    print(json.dumps(result))  # single machine-readable JSON line (backend reads the last one)
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
