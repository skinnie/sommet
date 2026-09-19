#!/usr/bin/env python3
"""Learns how much a rider stops, per distance. The planner asks "for this ~X km ride, how long
will you be off the bike in total (food, rest, sleep, prep)?" — we store that answer keyed by
distance and use it to pre-fill future rides, generalizing across distances (a rider barely stops
on a 200 but rests a lot on a 600).

Store: ~/.sommet/race_stops.json  ->  {"points": [{"km": 210, "hours": 1.0}, ...]}
- suggest(km): interpolate the rider's OWN points (learned); fall back to a sane brevet default
  curve when they have none nearby.
- record(km, hours): remember this answer (one point per ~25 km band, latest wins).

Stdlib only, offline. `--selftest` uses a temp store.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

STORE = os.path.expanduser("~/.sommet/race_stops.json")

# Default brevet off-bike hours vs distance (incl. sleep) — used until the rider has their own data.
# Rolls in the overnight on long events (why 600 jumps): (km, hours).
_DEFAULT = [(120, 0.5), (200, 1.0), (300, 1.75), (400, 3.0), (600, 7.0), (1000, 14.0), (1200, 18.0)]


def _interp(points: List[Tuple[float, float]], km: float) -> float:
    pts = sorted(points)
    if km <= pts[0][0]:
        return pts[0][1]
    if km >= pts[-1][0]:
        # extrapolate along the last segment so very long routes still scale up
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        slope = (y1 - y0) / (x1 - x0) if x1 != x0 else 0.0
        return max(y1, y1 + slope * (km - x1))
    for i in range(1, len(pts)):
        if km <= pts[i][0]:
            (x0, y0), (x1, y1) = pts[i - 1], pts[i]
            f = (km - x0) / (x1 - x0) if x1 != x0 else 0.0
            return y0 + f * (y1 - y0)
    return pts[-1][1]


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {"points": []}


def _save(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


def suggest(km: float, path: str = STORE) -> Dict[str, Any]:
    data = _load(path)
    user = [(float(p["km"]), float(p["hours"])) for p in data.get("points", []) if p.get("hours") is not None]
    if len(user) >= 2:
        return {"ok": True, "hours": round(_interp(user, km), 2), "source": "learned",
                "n_points": len(user)}
    if len(user) == 1:
        # one data point: scale the default curve to pass through it
        ukm, uh = user[0]
        d_at_u = _interp(_DEFAULT, ukm)
        scale = (uh / d_at_u) if d_at_u > 0 else 1.0
        return {"ok": True, "hours": round(_interp(_DEFAULT, km) * scale, 2), "source": "learned",
                "n_points": 1}
    return {"ok": True, "hours": round(_interp(_DEFAULT, km), 2), "source": "default", "n_points": 0}


def record(km: float, hours: float, path: str = STORE) -> Dict[str, Any]:
    data = _load(path)
    pts = data.get("points", [])
    band = round(km / 25.0) * 25.0     # one point per ~25 km band, latest wins
    pts = [p for p in pts if round(float(p["km"]) / 25.0) * 25.0 != band]
    pts.append({"km": round(km, 1), "hours": round(float(hours), 2)})
    data["points"] = sorted(pts, key=lambda p: p["km"])
    _save(path, data)
    return {"ok": True, "n_points": len(data["points"])}


def _selftest():
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "race_stops.json")
    # empty -> default curve
    d200 = suggest(200, p)
    assert d200["source"] == "default" and 0.8 <= d200["hours"] <= 1.3, d200
    # record two answers: barely stops on a 200, lots on a 600
    record(205, 0.8, p)
    record(600, 8.0, p)
    s400 = suggest(400, p)   # should interpolate BETWEEN the rider's own points
    assert s400["source"] == "learned", s400
    assert 3.0 <= s400["hours"] <= 6.0, s400["hours"]
    s600 = suggest(590, p)   # near their 600 point
    assert 7.0 <= s600["hours"] <= 8.5, s600["hours"]
    print("default 200 ->", d200["hours"], "h;  learned 400 ->", s400["hours"],
          "h;  learned ~600 ->", s600["hours"], "h")
    # re-record same band -> latest wins, no duplicate
    record(600, 9.0, p)
    assert suggest(600, p)["hours"] == 9.0 or abs(suggest(600, p)["hours"] - 9.0) < 0.5
    print("✓ race_stopmem selftest passed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Learned per-distance stop-time memory")
    parser.add_argument("input_file", nargs="?", help='JSON {mode:"suggest"|"record", distance_km, hours?}')
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        _selftest()
        return
    try:
        body = json.load(open(args.input_file)) if args.input_file else json.load(sys.stdin)
        km = float(body.get("distance_km") or 0)
        if body.get("mode") == "record":
            print(json.dumps(record(km, float(body.get("hours") or 0))))
        else:
            print(json.dumps(suggest(km)))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
