#!/usr/bin/env python3
"""Race Timeline assembly - the FOUNDATION layer that turns per-leg moving-time estimates into a
brevet/ultra plan a rider can actually use: control-to-control segmentation, a moving-vs-elapsed
split (planned stops per control), arrival times, and cutoff margins.

Architecture (agreed with the PM, 2026-09-18):

    Route GPX -> segment by controls -> estimate_route() [ENGINE speed model] -> moving times
      -> + planned stops -> elapsed timeline -> arrival per control -> cutoff margins

This module owns ONLY the assembly. It never computes speed itself - it calls estimate_route()
from race_event (the engine's curve-first model behind the locked seam), so when the speed model
improves, the timeline improves with no change here.

Deliberately NOT here yet (later features): POIs / water / food / sleep intelligence, what-if
scenarios, the QML/mobile UI. This is the computation core + a --selftest, same pattern as
race_event.py.

Stdlib only (plus race_event / geo_util from this tools/ dir).

NOTE on granularity: segmenting into control legs makes each leg see its OWN climb density, which
is more accurate than the whole-route average on varied routes. But base_speed was calibrated at
WHOLE-RIDE granularity (see race_event's curve NOTE 2) - because the curve is convex, per-segment
prediction with a whole-ride-fit base carries a small known bias. Until race_calibration refits
base at segment granularity, per-control moving times are flagged provisional in the output.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import race_event
from race_event import AthleteInputs, BikeInputs, RaceEvent, estimate_route

_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def _route_points(event: RaceEvent) -> List[Dict[str, Any]]:
    """Route coordinates from explicit points, else parsed from the event's GPX."""
    pts = event.points
    if not pts and event.gpx:
        try:
            import geo_util
            pts = geo_util.parse_gpx_points(event.gpx)
        except Exception:
            pts = []
    return pts or []


def segment_route(points: List[Dict[str, Any]],
                  cut_distances_km: List[float]) -> List[Dict[str, Any]]:
    """Split a route into legs at the given cumulative distances (km).

    Returns one dict per leg: {start_km, end_km, distance_km, ascent_m, descent_m}. Each
    inter-point step is assigned to the leg its midpoint falls in, so distance and ascent/descent
    are attributed per leg. Boundaries snap to the track's own sampling (v1; exact interpolation
    at the cut is a later refinement and rarely matters at brevet control spacing).
    """
    cumul_m, total_m = race_event._cumulative_distances(points)
    if total_m <= 0 or len(points) < 2:
        return []
    total_km = total_m / 1000.0

    cuts = sorted({round(d, 6) for d in cut_distances_km if 0.0 < d < total_km})
    bounds = [0.0] + cuts + [total_km]
    n_seg = len(bounds) - 1
    legs = [{"start_km": bounds[i], "end_km": bounds[i + 1],
             "distance_km": 0.0, "ascent_m": 0.0, "descent_m": 0.0} for i in range(n_seg)]

    def seg_of(km: float) -> int:
        for i in range(n_seg):
            if km <= bounds[i + 1] + 1e-9:
                return i
        return n_seg - 1

    for i in range(1, len(points)):
        step_m = cumul_m[i] - cumul_m[i - 1]
        if step_m <= 0:
            continue
        mid_km = (cumul_m[i] + cumul_m[i - 1]) / 2000.0
        s = seg_of(mid_km)
        legs[s]["distance_km"] += step_m / 1000.0
        e0, e1 = points[i - 1].get("ele"), points[i].get("ele")
        if e0 not in (None, "") and e1 not in (None, ""):
            de = float(e1) - float(e0)
            if de > 0:
                legs[s]["ascent_m"] += de
            else:
                legs[s]["descent_m"] += -de

    for leg in legs:
        leg["distance_km"] = round(leg["distance_km"], 3)
        leg["ascent_m"] = round(leg["ascent_m"], 1)
        leg["descent_m"] = round(leg["descent_m"], 1)
    return legs


def _fmt(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if isinstance(dt, datetime) else dt


def build_timeline(event: RaceEvent, athlete: Optional[AthleteInputs] = None,
                   bike: Optional[BikeInputs] = None,
                   stops_s: Optional[List[float]] = None,
                   route_estimator: Optional[Callable[..., Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Assemble the full race timeline.

    - Segments the route at each control's distance (event.cutoffs) plus the finish.
    - Gets per-leg moving times from the engine seam (estimate_route).
    - Applies a per-leg planned STOP schedule (stops_s, seconds spent at the control ENDING each
      leg; default all 0) to produce the moving-vs-elapsed split.
    - Walks the clock from event.start_dt to give each control an arrival + departure time.
    - Computes cutoff margins (control.cutoff_dt - predicted arrival); negative = you miss it.

    `route_estimator` is injectable only for tests; production uses estimate_route.
    """
    athlete = athlete or AthleteInputs(weight_kg=75.0)
    bike = bike or BikeInputs(bike_weight_kg=10.0, load_weight_kg=5.0)
    route_estimator = route_estimator or estimate_route

    points = _route_points(event)
    _, total_m = race_event._cumulative_distances(points) if points else ([], 0.0)
    if total_m <= 0:
        return {"ok": False, "error": "route has no distance (no GPX or points)"}
    total_km = total_m / 1000.0

    controls_sorted = sorted(event.cutoffs, key=lambda c: c.distance_km)
    # Interior controls become leg boundaries; a control at (or ~at) the route end is the finish.
    tol_km = max(0.2, 0.01 * total_km)
    interior = [c for c in controls_sorted if 0.0 < c.distance_km < total_km - tol_km]
    finish_control = next((c for c in reversed(controls_sorted)
                           if abs(c.distance_km - total_km) <= tol_km), None)

    legs_geo = segment_route(points, [c.distance_km for c in interior])
    if not legs_geo:
        return {"ok": False, "error": "could not segment route"}

    # The control that ENDS each leg: the interior controls in order, then the finish.
    end_controls = list(interior) + [finish_control]  # finish_control may be None (no finish cutoff)

    route_est = route_estimator(
        [(l["distance_km"], l["ascent_m"], l["descent_m"]) for l in legs_geo], athlete, bike)
    leg_estimates = route_est.get("legs", [])

    n = len(legs_geo)
    if stops_s is None:
        stops_s = [0.0] * n
    elif len(stops_s) < n:
        stops_s = list(stops_s) + [0.0] * (n - len(stops_s))

    rows: List[Dict[str, Any]] = []
    clock = event.start_dt
    total_moving_s = 0.0
    total_stop_s = 0.0
    worst_margin_s: Optional[float] = None
    worst_margin_label: Optional[str] = None
    per_leg_provisional = len(legs_geo) > 1  # per-control granularity bias (see module NOTE)

    for i in range(n):
        geo = legs_geo[i]
        lt = leg_estimates[i] if i < len(leg_estimates) else {"moving_time_s": 0.0, "avg_speed_kmh": 0.0,
                                                              "confidence": "low", "model_source": "placeholder"}
        ctrl = end_controls[i] if i < len(end_controls) else None
        move_s = float(lt.get("moving_time_s", 0.0))
        total_moving_s += move_s
        arrival = clock + timedelta(seconds=move_s)

        stop_s = float(stops_s[i]) if i < len(stops_s) else 0.0
        # No stop at the very finish.
        is_finish = (i == n - 1)
        if is_finish:
            stop_s = 0.0
        depart = arrival + timedelta(seconds=stop_s)
        total_stop_s += stop_s

        cutoff_dt = ctrl.cutoff_dt if ctrl else None
        margin_s = None
        if isinstance(cutoff_dt, datetime):
            margin_s = (cutoff_dt - arrival).total_seconds()
            if worst_margin_s is None or margin_s < worst_margin_s:
                worst_margin_s = margin_s
                worst_margin_label = (ctrl.label if ctrl else None)

        rows.append({
            "index": i,
            "label": (ctrl.label if ctrl else ("Finish" if is_finish else f"Control {i + 1}")),
            "distance_km": round(geo["end_km"], 2),
            "leg_distance_km": geo["distance_km"],
            "leg_ascent_m": geo["ascent_m"],
            "moving_time_s": round(move_s, 1),
            "avg_speed_kmh": lt.get("avg_speed_kmh"),
            "arrival_dt": _fmt(arrival),
            "stop_s": round(stop_s, 1),
            "depart_dt": _fmt(depart),
            "cutoff_dt": _fmt(cutoff_dt),
            "margin_s": None if margin_s is None else round(margin_s, 1),
            "confidence": lt.get("confidence"),
        })
        clock = depart

    finish_eta = clock  # last depart == arrival at finish (no finish stop)
    elapsed_s = (finish_eta - event.start_dt).total_seconds()

    total_ascent_m = round(sum(l["ascent_m"] for l in legs_geo), 1)

    return {
        "ok": True,
        "distance_km": round(total_km, 2),
        "total_ascent_m": total_ascent_m,
        "start_dt": _fmt(event.start_dt),
        "finish_eta_dt": _fmt(finish_eta),
        "moving_time_s": round(total_moving_s, 1),
        "stop_time_s": round(total_stop_s, 1),
        "elapsed_time_s": round(elapsed_s, 1),
        "confidence": route_est.get("confidence", "low"),
        "model_source": route_est.get("model_source", "placeholder"),
        "worst_margin_s": None if worst_margin_s is None else round(worst_margin_s, 1),
        "worst_margin_control": worst_margin_label,
        "per_control_provisional": per_leg_provisional,
        "controls": rows,
    }


# --- self test ------------------------------------------------------------------------------

def _synthetic_event_with_controls() -> RaceEvent:
    """~200 km route, 3 controls + finish, start 06:00, generous cutoffs."""
    from race_event import Cutoff
    start = datetime(2026, 9, 25, 6, 0)
    # ~200 km straight line, gentle rolling elevation so ascent is non-zero.
    pts = []
    for i in range(400):
        ele = 200 + (i % 40) * 2  # sawtooth -> real ascent/descent
        pts.append({"lat": 45.0 + i * 0.005, "lon": 3.0, "ele": ele})
    total_km = race_event._cumulative_distances(pts)[1] / 1000.0
    cutoffs = [
        Cutoff("Control 1", round(total_km * 0.25, 1), start + timedelta(hours=5)),
        Cutoff("Control 2", round(total_km * 0.50, 1), start + timedelta(hours=10)),
        Cutoff("Control 3", round(total_km * 0.75, 1), start + timedelta(hours=15)),
        Cutoff("Finish", round(total_km, 1), start + timedelta(hours=20)),
    ]
    return RaceEvent(name="Test 200k BRM", event_type="BRM", start_dt=start, points=pts, cutoffs=cutoffs)


def _selftest():
    event = _synthetic_event_with_controls()
    # Calibrated rider so we exercise the real curve, not the cold-start placeholder.
    athlete = AthleteInputs(weight_kg=82.0, speed_profile={
        "base_speed_kmh": 25.0, "confidence": "high", "model_source": "personal", "n_recent_rides": 30})

    # 30 min at each of the 3 intermediate controls, none at the finish.
    tl = build_timeline(event, athlete=athlete, stops_s=[1800, 1800, 1800, 0])
    assert tl["ok"], tl

    print("=== Race Timeline: synthetic 200k BRM (base 25.0, personal) ===")
    print(f"Distance {tl['distance_km']} km | ascent {tl['total_ascent_m']} m | "
          f"source {tl['model_source']} ({tl['confidence']})")
    h = lambda s: f"{int(s // 3600)}h{int((s % 3600) // 60):02d}"
    print(f"Moving {h(tl['moving_time_s'])} | stops {h(tl['stop_time_s'])} | "
          f"elapsed {h(tl['elapsed_time_s'])} | finish {tl['finish_eta_dt'][11:16]}")
    for r in tl["controls"]:
        m = r["margin_s"]
        mtxt = "-" if m is None else (f"+{h(m)}" if m >= 0 else f"-{h(-m)}")
        print(f"  {r['label']:<10} km {r['distance_km']:<6} arr {r['arrival_dt'][11:16]} "
              f"leg {h(r['moving_time_s'])} @ {r['avg_speed_kmh']} km/h  margin {mtxt}")
    print(f"Worst margin: {tl['worst_margin_control']} "
          f"({'n/a' if tl['worst_margin_s'] is None else h(tl['worst_margin_s'])})")

    # --- assertions ---
    # 4 rows (3 controls + finish), distances monotincreasing, ending at total.
    assert len(tl["controls"]) == 4, tl["controls"]
    ds = [r["distance_km"] for r in tl["controls"]]
    assert ds == sorted(ds) and abs(ds[-1] - tl["distance_km"]) < 0.1
    # leg distances sum to the whole route.
    assert abs(sum(r["leg_distance_km"] for r in tl["controls"]) - tl["distance_km"]) < 0.5
    # elapsed == moving + stops.
    assert abs(tl["elapsed_time_s"] - (tl["moving_time_s"] + tl["stop_time_s"])) < 1.0
    # stops applied: 3 x 30 min.
    assert abs(tl["stop_time_s"] - 3 * 1800) < 1.0
    # margins present at every control that has a cutoff.
    assert all(r["margin_s"] is not None for r in tl["controls"])
    # personal profile propagated through the seam into the timeline.
    assert tl["model_source"] == "personal"

    # Contract: build_timeline consumes the seam via an injected route estimator (mock).
    def mock_route(legs, athlete, bike, conditions=None):
        # 20 km/h flat everywhere, high confidence.
        est = [{"moving_time_s": d / 20.0 * 3600.0, "avg_speed_kmh": 20.0,
                "confidence": "high", "model_source": "personal"} for (d, a, de) in legs]
        return {"legs": est, "confidence": "high", "model_source": "personal"}
    tl2 = build_timeline(event, athlete=athlete, stops_s=[0, 0, 0, 0], route_estimator=mock_route)
    # At 20 km/h with no stops, elapsed hours ~= distance/20.
    assert abs(tl2["elapsed_time_s"] - tl2["distance_km"] / 20.0 * 3600.0) < 5.0
    print(f"\nContract (mock 20km/h, no stops): elapsed {h(tl2['elapsed_time_s'])} "
          f"for {tl2['distance_km']} km -> seam wired correctly")

    print("\n✓ All race_timeline selftest checks passed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Race timeline assembly")
    parser.add_argument("input_file", nargs="?", help="JSON {event, athlete?, bike?, stops_s?}")
    parser.add_argument("--selftest", action="store_true", help="Run self-tests")
    args = parser.parse_args(argv)

    if args.selftest:
        _selftest()
        return

    try:
        if args.input_file:
            with open(args.input_file) as f:
                body = json.load(f)
        else:
            body = json.load(sys.stdin)
        event = RaceEvent.from_dict(body.get("event", {}))
        athlete = AthleteInputs.from_dict(body["athlete"]) if body.get("athlete") else None
        bike = BikeInputs.from_dict(body["bike"]) if body.get("bike") else None
        stops_s = body.get("stops_s")
        tl = build_timeline(event, athlete, bike, stops_s)
        print(json.dumps({"ok": tl.get("ok", False), "timeline": tl}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
