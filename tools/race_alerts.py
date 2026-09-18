#!/usr/bin/env python3
"""Critical points - the one ranked list a brevet rider actually reads: major climbs, cutoff
risk, water/food gaps, and darkness, distilled from everything else and sorted by km.

This is the "route data -> insight" layer (à la Bike Trip Planner): it consumes the already-
computed timeline, weather and POIs and adds climb detection from the route itself, then emits
a single severity-ranked list so the plan has ONE place that says what to worry about and where.

Input (JSON, file or stdin):
  {"gpx"|"points", "timeline": {...}, "weather": {...}, "pois": {...}}
  (timeline/weather/pois are the outputs of the other race_* tools; all optional except a route.)
Output: {ok, alerts:[{km, kind, severity, text}], counts:{critical,warn,info}}

Stdlib only, offline (all inputs are precomputed). `--selftest` included.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

import geo_util

SEV_RANK = {"critical": 0, "warn": 1, "info": 2}


def _resample_ele(points, cumul_m, step_m=100.0):
    """Even-distance (dist_m, ele_m) samples, linearly interpolating elevation; skips if no ele."""
    eles = [p.get("ele") for p in points]
    if any(e in (None, "") for e in eles):
        # fill gaps by carrying the last known elevation (routes sometimes miss a few)
        last = next((float(e) for e in eles if e not in (None, "")), None)
        if last is None:
            return []
        eles = [(float(e) if e not in (None, "") else last) for e in eles]
        for i, e in enumerate(eles):
            if e is not None:
                last = e
    else:
        eles = [float(e) for e in eles]
    total = cumul_m[-1]
    out = []
    d = 0.0
    j = 0
    while d <= total:
        while j < len(cumul_m) - 1 and cumul_m[j + 1] < d:
            j += 1
        if j >= len(cumul_m) - 1:
            out.append((d, eles[-1])); break
        span = cumul_m[j + 1] - cumul_m[j]
        f = 0.0 if span <= 0 else (d - cumul_m[j]) / span
        out.append((d, eles[j] + f * (eles[j + 1] - eles[j])))
        d += step_m
    return out


def find_climbs(points, cumul_m):
    """Sustained climbs, starting where the ascent actually begins (not the preceding flat).
    A climb is a low->high run; it closes when the route drops `drop_reset` from the running high.
    Returns notable climbs (gain >= 120 m)."""
    samp = _resample_ele(points, cumul_m)
    if len(samp) < 3:
        return []
    drop_reset = 20.0
    climb_start = 10.0   # metres above the low before we call it "climbing"
    climbs = []

    def close(lo, hi):
        gain = samp[hi][1] - samp[lo][1]
        length_m = samp[hi][0] - samp[lo][0]
        if gain >= 120 and length_m > 0:
            climbs.append({"start_km": samp[lo][0] / 1000.0, "top_km": samp[hi][0] / 1000.0,
                           "gain_m": round(gain), "avg_grade": round(100.0 * gain / length_m, 1)})

    low_i = hi_i = 0
    climbing = False
    for i in range(1, len(samp)):
        if not climbing:
            if samp[i][1] <= samp[low_i][1]:
                low_i = hi_i = i                       # still descending/flat: track the low
            elif samp[i][1] - samp[low_i][1] >= climb_start:
                climbing = True; hi_i = i              # ascent has begun
            else:
                hi_i = i
        else:
            if samp[i][1] >= samp[hi_i][1]:
                hi_i = i
            elif samp[hi_i][1] - samp[i][1] >= drop_reset:
                close(low_i, hi_i)
                low_i = hi_i = i; climbing = False
    if climbing:
        close(low_i, hi_i)
    return climbs


def build_alerts(points, timeline=None, weather=None, pois=None) -> Dict[str, Any]:
    if len(points) < 2:
        return {"ok": False, "error": "route needs >= 2 points"}
    cumul_m = geo_util.cumulative_distances([(p["lat"], p["lon"]) for p in points])
    alerts: List[Dict[str, Any]] = []

    # climbs
    for c in find_climbs(points, cumul_m):
        major = c["gain_m"] >= 350 or c["avg_grade"] >= 7.0
        alerts.append({
            "km": round(c["start_km"], 1), "kind": "climb",
            "severity": "warn" if major else "info",
            "text": "%s climb from km %.0f: +%d m at %.1f%%"
                    % ("Major" if major else "Notable", c["start_km"], c["gain_m"], c["avg_grade"]),
        })

    # cutoff risk (from the timeline's per-control margins)
    for r in (timeline or {}).get("controls", []):
        m = r.get("margin_s")
        if m is None:
            continue
        if m < 0:
            alerts.append({"km": r["distance_km"], "kind": "cutoff", "severity": "critical",
                           "text": "%s: OVER cutoff by %s" % (r.get("label"), _hm(-m))})
        elif m < 3600:
            alerts.append({"km": r["distance_km"], "kind": "cutoff", "severity": "warn",
                           "text": "%s: tight cutoff — only %s margin" % (r.get("label"), _hm(m))})

    # darkness (from weather per-control)
    for c in (weather or {}).get("controls", []):
        if c.get("is_dark"):
            alerts.append({"km": c.get("km", 0), "kind": "dark", "severity": "info",
                           "text": "%s: reached after dark" % (c.get("label") or ("km %.0f" % c.get("km", 0)))})

    # water / food gaps (from pois)
    cats = (pois or {}).get("categories", {})
    w = cats.get("water")
    if w and w.get("count", 0) >= 0:
        if w.get("count", 0) == 0:
            alerts.append({"km": 0, "kind": "water", "severity": "critical",
                           "text": "No drinking water found on route — carry/plan resupply"})
        elif w.get("longest_gap_km", 0) >= 40:
            sev = "critical" if w.get("longest_gap_over_carry") else "warn"
            extra = (" — needs ~%.1f L (> %.1f carried)" % (w.get("longest_gap_litres", 0), w.get("carry_l", 0))) \
                if w.get("longest_gap_over_carry") else ""
            alerts.append({"km": w.get("longest_gap_after_km", 0), "kind": "water", "severity": sev,
                           "text": "%.0f km without water after km %.0f%s"
                                   % (w["longest_gap_km"], w.get("longest_gap_after_km", 0), extra)})
    f = cats.get("food")
    if f and f.get("longest_gap_km", 0) >= 80:
        alerts.append({"km": f.get("longest_gap_after_km", 0), "kind": "food", "severity": "warn",
                       "text": "No food for %.0f km after km %.0f"
                               % (f["longest_gap_km"], f.get("longest_gap_after_km", 0))})

    alerts.sort(key=lambda a: (SEV_RANK.get(a["severity"], 3), a["km"]))
    counts = {s: sum(1 for a in alerts if a["severity"] == s) for s in ("critical", "warn", "info")}
    return {"ok": True, "alerts": alerts, "counts": counts}


def _hm(seconds):
    seconds = int(abs(seconds))
    return "%dh%02d" % (seconds // 3600, (seconds % 3600) // 60)


# --- self test -------------------------------------------------------------------------------

def _selftest():
    # a route with a big climb around km 30-40 (+500 m), flat elsewhere
    pts = []
    for i in range(1000):
        km = i * 0.1
        ele = 100.0
        if 30 <= km <= 40:
            ele = 100.0 + (km - 30) * 50.0     # +500 m over 10 km ~ 5%
        elif km > 40:
            ele = 600.0 - min(500.0, (km - 40) * 50.0)
        pts.append({"lat": 45.0 + i * 0.0009, "lon": 3.0, "ele": ele})
    cumul = geo_util.cumulative_distances([(p["lat"], p["lon"]) for p in pts])
    total_km = cumul[-1] / 1000.0

    timeline = {"controls": [
        {"label": "C1", "distance_km": 50.0, "margin_s": 1200},        # tight
        {"label": "Finish", "distance_km": round(total_km, 1), "margin_s": -600},  # missed
    ]}
    weather = {"controls": [
        {"label": "C1", "km": 50.0, "is_dark": False},
        {"label": "Finish", "km": round(total_km, 1), "is_dark": True},
    ]}
    pois = {"categories": {
        "water": {"count": 2, "longest_gap_km": 82.0, "longest_gap_after_km": 10.0,
                  "longest_gap_litres": 1.6, "carry_l": 1.5, "longest_gap_over_carry": True},
        "food": {"count": 1, "longest_gap_km": 117.0, "longest_gap_after_km": 20.0},
    }}
    r = build_alerts(pts, timeline, weather, pois)
    assert r["ok"], r
    print("=== Critical points ===")
    for a in r["alerts"]:
        print("  [%s] km %.0f — %s" % (a["severity"].upper(), a["km"], a["text"]))
    print("counts:", r["counts"])

    kinds = {a["kind"] for a in r["alerts"]}
    assert "climb" in kinds and "cutoff" in kinds and "water" in kinds and "food" in kinds and "dark" in kinds
    # critical first
    assert r["alerts"][0]["severity"] == "critical"
    # the big climb detected around km 30
    climb = next(a for a in r["alerts"] if a["kind"] == "climb")
    assert 28 <= climb["km"] <= 32, climb
    assert r["counts"]["critical"] >= 2  # missed cutoff + over-carry water gap
    print("\n✓ All race_alerts selftest checks passed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Critical-points assembler")
    parser.add_argument("input_file", nargs="?", help="JSON {gpx|points, timeline?, weather?, pois?}")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        _selftest()
        return
    try:
        body = json.load(open(args.input_file)) if args.input_file else json.load(sys.stdin)
        points = body.get("points")
        if not points and body.get("gpx"):
            points = geo_util.parse_gpx_points(body["gpx"])
        r = build_alerts(points or [], body.get("timeline"), body.get("weather"), body.get("pois"))
        print(json.dumps(r))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
