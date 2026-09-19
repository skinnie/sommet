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

# Default per-control base stop (s) before the proportional remainder is added (PM: ~10-15 min).
_DEFAULT_CONTROL_BASE_S = 600.0


def _suggest_sleep_s(elapsed_s_no_sleep: float) -> int:
    """Suggested sleep (seconds) for a predicted sleepless elapsed time. Delegates to the engine's
    race_calibration.suggest_sleep_s (tiers <20h:0, 20-30h:1h, 30-40h:3h, >40h:5h) so there's one
    implementation; returns 0 if that module isn't importable. Suggestion only, never applied."""
    try:
        import race_calibration
        return int(race_calibration.suggest_sleep_s(elapsed_s_no_sleep))
    except (ImportError, AttributeError):
        return 0


def _default_distribute_stops(controls, leg_moving_s, stop_ratio, per_control_base_s, overrides):
    """Production stop distributor: the engine's race_calibration.distribute_stops. Imported
    lazily so this module loads even before that helper ships; raises a clear error if a caller
    asks for ratio-based stops before the engine side has landed (tests inject a mock instead)."""
    try:
        import race_calibration
        return race_calibration.distribute_stops(
            controls, leg_moving_s, stop_ratio, per_control_base_s, overrides)
    except (ImportError, AttributeError) as e:
        raise RuntimeError(
            "ratio-based stop distribution needs race_calibration.distribute_stops "
            "(engine workstream); pass stops_s to override, or a stop_distributor for tests") from e


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
                   stop_profile: Optional[Dict[str, Any]] = None,
                   sleep: Optional[Dict[str, Any]] = None,
                   sleep_windows: Optional[List[Dict[str, Any]]] = None,
                   control_overrides: Optional[Dict[int, float]] = None,
                   route_estimator: Optional[Callable[..., Dict[str, Any]]] = None,
                   stop_distributor: Optional[Callable[..., Dict[int, float]]] = None) -> Dict[str, Any]:
    """Assemble the full race timeline.

    - Segments the route at each control's distance (event.cutoffs) plus the finish.
    - Gets per-leg moving times from the engine seam (estimate_route).
    - Applies a per-leg planned STOP schedule (stops_s, seconds spent at the control ENDING each
      leg; default all 0) to produce the moving-vs-elapsed split.
    - Walks the clock from event.start_dt to give each control an arrival + departure time.
    - Computes cutoff margins (control.cutoff_dt - predicted arrival); negative = you miss it.

    Stops (2026-09-18 increment):
      - stops_s: raw per-leg seconds - the ESCAPE HATCH; if given, used verbatim (finish forced 0).
      - stop_profile: {"ratio", "source", "confidence"} - a calibrated stop/moving ratio (from
        race_calibration.stop_ratio_from_rides). The rolling budget (ratio x total moving) is
        distributed ONLY across genuine controls via race_calibration.distribute_stops; a manual
        control override wins while the total stays fixed. With NO controls, the budget is applied
        as a single aggregate to elapsed (no manufactured per-leg stops), per the PM.
      - control_overrides: {control_leg_index: seconds} manual per-control stops.
      - sleep: {"enabled": bool, "duration_s": float} - added ON TOP of stops (never in the ratio),
        as a lump on elapsed/finish for now. A suggestion is always computed by tier and returned.

    `route_estimator` / `stop_distributor` are injectable only for tests; production uses
    estimate_route / race_calibration.distribute_stops.
    """
    athlete = athlete or AthleteInputs(weight_kg=75.0)
    bike = bike or BikeInputs(bike_weight_kg=10.0, load_weight_kg=5.0)
    route_estimator = route_estimator or estimate_route
    stop_distributor = stop_distributor or _default_distribute_stops

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
    leg_moving_s = [float(leg_estimates[i].get("moving_time_s", 0.0)) if i < len(leg_estimates)
                    else 0.0 for i in range(n)]
    total_moving_s = sum(leg_moving_s)

    # Genuine stop-controls = legs that END at an interior control (the finish leg never gets a
    # stop). end_controls == interior + [finish], so interior controls are leg indices 0..len-1.
    genuine_idxs = list(range(len(interior)))

    # Resolve the per-leg stop schedule from (priority) the raw escape hatch, else a calibrated
    # stop_profile, else nothing. aggregate_stop_s is only used when there are no genuine controls.
    leg_stops = [0.0] * n
    aggregate_stop_s = 0.0
    if stops_s is not None:                       # escape hatch: use verbatim
        for i in range(min(n, len(stops_s))):
            leg_stops[i] = float(stops_s[i])
    elif stop_profile and float(stop_profile.get("ratio", 0.0)) > 0.0:
        ratio = float(stop_profile["ratio"])
        base_s = float(stop_profile.get("per_control_base_s", _DEFAULT_CONTROL_BASE_S))
        if genuine_idxs:
            dist = stop_distributor(genuine_idxs, leg_moving_s, ratio, base_s,
                                    control_overrides or {})
            for idx, secs in dist.items():
                if 0 <= idx < n:
                    leg_stops[idx] = float(secs)
        else:
            aggregate_stop_s = ratio * total_moving_s  # no controls -> one lump, per PM rule 1

    # Planned sleep windows (from the circadian sleep plan) folded into the walk so BOTH per-control
    # arrivals after a sleep AND the finish ETA include it. Each window is assigned to the control
    # at/after its km (you sleep there); never at the very finish.
    leg_sleep = [0.0] * n
    for w in (sleep_windows or []):
        dur = float(w.get("duration_s", 0.0))
        if dur <= 0:
            continue
        wkm = float(w.get("km", 0.0))
        idx = next((i for i in range(n) if legs_geo[i]["end_km"] >= wkm - 0.01), n - 1)
        if idx >= n - 1:
            idx = max(0, n - 2)   # not at the finish leg
        leg_sleep[idx] += dur

    rows: List[Dict[str, Any]] = []
    clock = event.start_dt
    running_moving_s = 0.0
    total_stop_s = 0.0
    planned_sleep_s = 0.0
    worst_margin_s: Optional[float] = None
    worst_margin_label: Optional[str] = None
    per_leg_provisional = len(legs_geo) > 1  # per-control granularity bias (see module NOTE)

    for i in range(n):
        geo = legs_geo[i]
        lt = leg_estimates[i] if i < len(leg_estimates) else {"moving_time_s": 0.0, "avg_speed_kmh": 0.0,
                                                              "confidence": "low", "model_source": "placeholder"}
        ctrl = end_controls[i] if i < len(end_controls) else None
        move_s = float(lt.get("moving_time_s", 0.0))
        running_moving_s += move_s
        arrival = clock + timedelta(seconds=move_s)

        stop_s = leg_stops[i]
        # No stop at the very finish.
        is_finish = (i == n - 1)
        if is_finish:
            stop_s = 0.0
        slp = leg_sleep[i]
        depart = arrival + timedelta(seconds=stop_s + slp)
        total_stop_s += stop_s
        planned_sleep_s += slp

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
            "sleep_s": round(slp, 1),
            "stop_overridden": bool(control_overrides and i in control_overrides and not is_finish),
            "depart_dt": _fmt(depart),
            "cutoff_dt": _fmt(cutoff_dt),
            "margin_s": None if margin_s is None else round(margin_s, 1),
            "confidence": lt.get("confidence"),
        })
        clock = depart

    # No-controls case: the calibrated stop budget applies as a single aggregate on elapsed.
    if aggregate_stop_s > 0:
        total_stop_s += aggregate_stop_s
        clock = clock + timedelta(seconds=aggregate_stop_s)

    # Sleep sits ON TOP of stops (never in the ratio). The tier suggestion is from the SLEEPLESS
    # elapsed. Planned windows (sleep_windows) were already folded into the walk above; an explicit
    # `sleep` lump (e.g. the what-if "extra sleep" knob) is added on top here.
    elapsed_no_sleep_s = (clock - event.start_dt).total_seconds() - planned_sleep_s
    sleep_suggested_s = _suggest_sleep_s(elapsed_no_sleep_s)
    lump_sleep_s = 0.0
    if sleep and sleep.get("enabled") and float(sleep.get("duration_s", 0.0)) > 0:
        lump_sleep_s = float(sleep["duration_s"])
        clock = clock + timedelta(seconds=lump_sleep_s)

    finish_eta = clock
    elapsed_s = (finish_eta - event.start_dt).total_seconds()
    sleep_time_s = planned_sleep_s + lump_sleep_s

    total_ascent_m = round(sum(l["ascent_m"] for l in legs_geo), 1)

    return {
        "ok": True,
        "distance_km": round(total_km, 2),
        "total_ascent_m": total_ascent_m,
        "start_dt": _fmt(event.start_dt),
        "finish_eta_dt": _fmt(finish_eta),
        "moving_time_s": round(total_moving_s, 1),
        "stop_time_s": round(total_stop_s, 1),
        "sleep_time_s": round(sleep_time_s, 1),
        "elapsed_time_s": round(elapsed_s, 1),
        "confidence": route_est.get("confidence", "low"),
        "model_source": route_est.get("model_source", "placeholder"),
        "stop_source": (stop_profile or {}).get("source") if stop_profile and stops_s is None else ("manual" if stops_s is not None else None),
        "sleep_suggested_s": sleep_suggested_s,
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

    # --- stops increment (2026-09-18): calibrated ratio distributed across controls ---
    tlr = build_timeline(event, athlete=athlete,
                         stop_profile={"ratio": 0.2, "source": "personal", "confidence": "high"})
    budget = 0.2 * tlr["moving_time_s"]
    assert abs(tlr["stop_time_s"] - budget) < 1.0, (tlr["stop_time_s"], budget)  # total == ratio x moving
    # stops land on the 3 genuine controls, not the finish.
    assert tlr["controls"][3]["stop_s"] == 0.0
    assert all(tlr["controls"][i]["stop_s"] > 0 for i in range(3))
    assert tlr["stop_source"] == "personal"
    print(f"Stop ratio 0.2: budget {h(budget)} across 3 controls "
          f"({[int(tlr['controls'][i]['stop_s']) for i in range(3)]} s), finish 0")

    # Override wins AND total budget stays fixed (redistributed among the others).
    tlo = build_timeline(event, athlete=athlete,
                         stop_profile={"ratio": 0.2}, control_overrides={1: 3600})
    assert abs(tlo["stop_time_s"] - budget) < 1.0, "override must not change the total budget"
    assert abs(tlo["controls"][1]["stop_s"] - 3600) < 1.0 and tlo["controls"][1]["stop_overridden"]
    print(f"Override C2=1h: total still {h(tlo['stop_time_s'])} (redistributed), C2 fixed at 1h00")

    # No controls -> budget applied as a single aggregate to elapsed, no manufactured per-leg stops.
    from race_event import RaceEvent as _RE
    ev_nc = _RE(name="No controls", event_type="ultra", start_dt=event.start_dt,
                points=event.points, cutoffs=[])
    tlnc = build_timeline(ev_nc, athlete=athlete, stop_profile={"ratio": 0.2})
    assert len(tlnc["controls"]) == 1  # just the whole-route/finish leg
    assert tlnc["controls"][0]["stop_s"] == 0.0
    assert abs(tlnc["stop_time_s"] - 0.2 * tlnc["moving_time_s"]) < 1.0
    print(f"No controls: aggregate stop {h(tlnc['stop_time_s'])} on elapsed (no per-leg stops)")

    # Sleep: suggestion is computed from sleepless elapsed; an enabled plan adds a lump on top.
    tls = build_timeline(event, athlete=athlete, stops_s=[0, 0, 0, 0],
                         sleep={"enabled": True, "duration_s": 3600})
    assert tls["sleep_time_s"] == 3600
    assert abs(tls["elapsed_time_s"] - (tls["moving_time_s"] + tls["stop_time_s"] + 3600)) < 1.0
    assert "sleep_suggested_s" in tls and isinstance(tls["sleep_suggested_s"], int)
    print(f"Sleep +1h applied; suggestion for this ride: {h(tls['sleep_suggested_s'])}")

    print("\n✓ All race_timeline selftest checks passed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Race timeline assembly")
    parser.add_argument("input_file", nargs="?",
                        help="JSON {event, athlete?, bike?, stops_s?, stop_profile?, sleep?, control_overrides?}")
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
        # JSON object keys are strings; control_overrides is keyed by control leg-index.
        raw_ov = body.get("control_overrides") or {}
        overrides = {int(k): float(v) for k, v in raw_ov.items()}
        tl = build_timeline(event, athlete, bike,
                            stops_s=body.get("stops_s"),
                            stop_profile=body.get("stop_profile"),
                            sleep=body.get("sleep"),
                            sleep_windows=body.get("sleep_windows"),
                            control_overrides=overrides or None)
        print(json.dumps({"ok": tl.get("ok", False), "timeline": tl}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
