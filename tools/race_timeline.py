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
from typing import Any, Callable, Dict, List, Optional, Tuple

import race_event
from race_event import AthleteInputs, BikeInputs, RaceEvent, estimate_route

_CONF_RANK = {"low": 0, "medium": 1, "high": 2}

# Default per-control base stop (s) before the proportional remainder is added (PM: ~10-15 min).
_DEFAULT_CONTROL_BASE_S = 600.0

# --- Fatigue model (multi-day ultras) -------------------------------------------------------
# ONE driver: hours awake since the last real sleep (= sleep debt). Riders hold pace for the first
# night, then slow as awake-time climbs; a sleep block resets it, a micro-nap trims it. This is the
# evidence-based shape (Race Across France / TCR studies: more sleep -> faster; cognition/pace decline
# with awake-time and DON'T truly recover across days - only the *feeling* does, the "second wind").
# So there is NO day-3 speed rebound here on purpose. Effect is negligible on a one-night 600 (you
# reset before ~16 h awake) and grows on PBP-length rides. Conservative + calibratable.
_FATIGUE_ONSET_H = 16.0          # fresh until ~16 h continuously awake
_FATIGUE_RATE_PER_H = 0.010      # then ~1% slower per extra hour awake
_FATIGUE_FLOOR = 0.80            # never worse than 20% slower (riders nap rather than crawl)
# Multi-day WEAR: a night does not fully undo the days before it, but how much it undoes depends on how
# much you slept. Each real night (a sleep block >= 2 h) costs up to 3 % speed from then on, scaled by the
# shortfall against 8 h: 10.5 h -> no wear, 4 h -> 1.5 %, nothing -> 3 %. (First cut, 2021 Bikingman Corsica,
# charged a flat 3 % per night; André pointed out that ride had full 10.5 h nights and that he could have
# ridden it faster, so the days-3-5 slowdown was not wear - the curfew capped him, and one ride cannot
# separate wear from terrain/weather. Sleep-scaled wear is the conservative middle for short-sleep PBP-style
# rides and is not fitted to that ride.) The overall floor above still applies.
_WEAR_PER_NIGHT = 0.03
_WEAR_FULL_NIGHT_S = 8 * 3600
_WEAR_MIN_SLEEP_S = 2 * 3600
# Planning margin: predicted moving time is padded 4 % so the plan errs slow (André, 2026-09-21: "better
# a good surprise than a bad one"). With the smoothed ascent the bare model ran ~2 % FAST on the real
# BRM600 (24.9 h vs ~25.6 h); +4 % puts it ~1.5 % slow there.
_PLANNING_MARGIN = 1.04
_SLEEP_RESET_K = 8.0             # 1 s of sleep pays down ~8 s of awake-time (3 h block -> full reset;
                                 # a 20 min nap -> ~2.7 h off the clock)


def _fatigue_factor(awake_s: float, enabled: bool,
                    onset_h: float = _FATIGUE_ONSET_H, rate: float = _FATIGUE_RATE_PER_H,
                    floor: float = _FATIGUE_FLOOR) -> float:
    """Speed multiplier (<=1) for how long you've been awake. 1.0 until `onset_h`, then linear
    decay at `rate`/h, floored. Returns 1.0 when fatigue is off."""
    if not enabled:
        return 1.0
    over = max(0.0, awake_s / 3600.0 - onset_h)
    return max(floor, 1.0 - rate * over)


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

    steps = race_event.ascent_steps(points, cumul_m)      # smoothed + hysteresis (raw GPX elevation is noisy)
    for i in range(1, len(points)):
        step_m = cumul_m[i] - cumul_m[i - 1]
        if step_m <= 0:
            continue
        mid_km = (cumul_m[i] + cumul_m[i - 1]) / 2000.0
        s = seg_of(mid_km)
        legs[s]["distance_km"] += step_m / 1000.0
        g, l_ = steps[i]
        legs[s]["ascent_m"] += g
        legs[s]["descent_m"] += l_

    for leg in legs:
        leg["distance_km"] = round(leg["distance_km"], 3)
        leg["ascent_m"] = round(leg["ascent_m"], 1)
        leg["descent_m"] = round(leg["descent_m"], 1)
    return legs


def _fmt(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if isinstance(dt, datetime) else dt


# --- No-ride hours (a personal rule: "I never ride 00:00-03:00", or a curfew 21:00-06:00) ------------
# Riding is never scheduled inside the window: reaching it, the rider rests until it ends. That rest is
# real sleep (it resets the awake-clock and, if >= 2 h, counts as a night for wear). Clock times are the
# event's own local wall-clock (start_dt is naive local).

def _hour_of(dt: datetime) -> float:
    return dt.hour + dt.minute / 60.0 + dt.second / 3600.0


def _in_no_ride(dt: datetime, nr: Tuple[float, float]) -> bool:
    s_, e_ = nr
    h = _hour_of(dt)
    return (s_ <= h < e_) if s_ < e_ else (h >= s_ or h < e_)


def _at_hour(dt: datetime, hour: float, forward: bool = True) -> datetime:
    """Next wall-clock `hour` (float hours) strictly after dt."""
    hh = int(hour) % 24
    mm = int(round((hour - int(hour)) * 60))
    cand = dt.replace(hour=hh, minute=mm % 60, second=0, microsecond=0)
    if mm >= 60:
        cand += timedelta(hours=1)
    if cand <= dt:
        cand += timedelta(days=1)
    return cand


def _walk_riding(clock: datetime, move_s: float, nr: Optional[Tuple[float, float]],
                 rest_at_start: bool = True, stops: Optional[List[Tuple[float, float]]] = None,
                 alert: Any = None):
    """Ride `move_s` seconds of BASE moving time, optionally never riding inside the no-ride window `nr`,
    optionally pausing for `stops` = [(base-seconds offset, stop seconds)] on the way. With `alert` (a
    race_alertness.Alertness) the rider slows when sleepy: base progress per real second is the speed
    factor at that moment, and the model's sleep pressure is advanced through riding, stops and rests.
    Returns (arrival, rests, riding_s) - rests = [(start_dt, end_dt, base_seconds_done_before)]."""
    todo = sorted(stops or [])
    si = 0
    rests: List[Tuple[datetime, datetime, float]] = []
    done, first, riding_s = 0.0, True, 0.0
    while True:
        if nr and (rest_at_start or not first) and _in_no_ride(clock, nr):
            end = _at_hour(clock, nr[1])
            rests.append((clock, end, done))
            if alert:
                alert.sleep((end - clock).total_seconds())
            clock = end
        first = False
        if si < len(todo) and todo[si][0] <= done + 1e-6:
            if alert:
                alert.awake(todo[si][1])
            clock += timedelta(seconds=todo[si][1])
            si += 1
            continue
        remaining = move_s - done
        if remaining <= 1e-6 and si >= len(todo):
            break
        f = alert.speed_factor(clock) if alert else 1.0
        until_stop = ((todo[si][0] - done) / f) if si < len(todo) else float("inf")
        until_win = (_at_hour(clock, nr[0]) - clock).total_seconds() if nr else float("inf")
        step = min(max(remaining, 0.0) / f, until_stop, until_win, 600.0 if alert else float("inf"))
        if alert:
            alert.record(clock)
            alert.awake(step)
        clock += timedelta(seconds=step)
        done += step * f
        riding_s += step
        if done >= move_s - 1e-6 and si >= len(todo):
            break
    return clock, rests, riding_s


def build_timeline(event: RaceEvent, athlete: Optional[AthleteInputs] = None,
                   bike: Optional[BikeInputs] = None,
                   stops_s: Optional[List[float]] = None,
                   stop_total_s: Optional[float] = None,
                   stop_profile: Optional[Dict[str, Any]] = None,
                   sleep: Optional[Dict[str, Any]] = None,
                   sleep_windows: Optional[List[Dict[str, Any]]] = None,
                   stop_events: Optional[List[Dict[str, Any]]] = None,
                   wind_speed_delta_kmh: float = 0.0,
                   control_overrides: Optional[Dict[int, float]] = None,
                   fatigue: Optional[Dict[str, Any]] = None,
                   no_ride: Optional[Dict[str, Any]] = None,
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

    # Optional prevailing-wind fold: a single uniform speed shift on every leg (negative = the net
    # headwind slows you, positive = tailwind), floored at the curve's 4 km/h and capped at 1.5x so
    # a gale can't invent a fantasy speed. This is the route-mean effect not already in base_speed
    # (loops net ~0); per-leg wind stays display-only to avoid the convexity double-count.
    if wind_speed_delta_kmh and abs(wind_speed_delta_kmh) > 0.05:
        for i in range(min(len(leg_estimates), len(legs_geo))):
            e = leg_estimates[i]
            sp = float(e.get("avg_speed_kmh") or 0.0)
            dkm = float(legs_geo[i]["distance_km"])
            if sp > 0 and dkm > 0:
                nsp = max(4.0, min(sp + wind_speed_delta_kmh, sp * 1.5))
                e["avg_speed_kmh"] = round(nsp, 1)
                e["moving_time_s"] = round(dkm / nsp * 3600.0, 1)

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
    elif stop_total_s and stop_total_s > 0:
        # The rider's own TOTAL off-bike estimate for this distance (incl. food/rest/sleep) -
        # spread it across the genuine controls the same way a ratio is, so it's the exact budget.
        if genuine_idxs and total_moving_s > 0:
            ratio = float(stop_total_s) / total_moving_s
            dist = stop_distributor(genuine_idxs, leg_moving_s, ratio,
                                    _DEFAULT_CONTROL_BASE_S, control_overrides or {})
            for idx, secs in dist.items():
                if 0 <= idx < n:
                    leg_stops[idx] = float(secs)
        else:
            aggregate_stop_s = float(stop_total_s)
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
    def _assign_to_leg(km: float) -> int:
        idx = next((i for i in range(n) if legs_geo[i]["end_km"] >= km - 0.01), n - 1)
        return max(0, n - 2) if idx >= n - 1 else idx   # never the finish leg

    leg_sleep = [0.0] * n
    # A sleep window can be a full block or a micro-nap; is_nap trims awake-time less than a block.
    leg_nap = [False] * n
    for w in (sleep_windows or []):
        dur = float(w.get("duration_s", 0.0))
        if dur <= 0:
            continue
        idx = _assign_to_leg(float(w.get("km", 0.0)))
        leg_sleep[idx] += dur
        if w.get("nap"):
            leg_nap[idx] = True

    # Named off-bike stops placed at an arbitrary km (lunch, dinner, a cafe/resupply) - folded into
    # whichever leg contains that km, counted as STOP time (not sleep). Lets the plan put real stops
    # BETWEEN controls, where riders actually stop, instead of only at checkpoints.
    leg_event_stop = [0.0] * n
    for e in (stop_events or []):
        dur = float(e.get("duration_s", 0.0))
        if dur <= 0:
            continue
        leg_event_stop[_assign_to_leg(float(e.get("km", 0.0)))] += dur

    fatigue_on = bool(fatigue and fatigue.get("enabled"))
    margin = _PLANNING_MARGIN if route_estimator is None else 1.0     # only the real engine is padded
    awake_s = float((fatigue or {}).get("awake_at_start_s", 0.0))   # already-awake at the start line
    wear = 0.0                        # accumulated multi-day wear (fraction of speed lost)
    nr: Optional[Tuple[float, float]] = None
    if no_ride and no_ride.get("start_h") is not None and no_ride.get("end_h") is not None:
        a_, b_ = float(no_ride["start_h"]) % 24.0, float(no_ride["end_h"]) % 24.0
        if a_ != b_:
            nr = (a_, b_)
    forced_rest_total_s = 0.0
    alert = None
    if fatigue_on and (fatigue or {}).get("model") == "twoprocess":
        import race_alertness
        f_ = fatigue or {}
        alert = race_alertness.Alertness(float(f_.get("bed_h", 22.0)), float(f_.get("wake_h", 6.0)),
                                         f_.get("awake_h_at_start"), start=event.start_dt)

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
        base_move_s = float(lt.get("moving_time_s", 0.0)) * margin
        # Fatigue: how tired at the MIDDLE of this leg (awake-time so far + half the leg), turned into
        # a speed multiplier; slower when awake > onset. A sleep block later in this leg resets it.
        fac = _fatigue_factor(awake_s + 0.5 * base_move_s, fatigue_on)
        if alert:
            fac = 1.0                        # the two-process model slows the rider inside the walk
        elif fatigue_on:
            fac = max(_FATIGUE_FLOOR, fac * (1.0 - wear))
        move_s = base_move_s / fac if fac > 0 else base_move_s
        inline_stop_s = 0.0
        leg_rests: List[Dict[str, Any]] = []
        # No controls: the whole stop budget used to be ONE lump at the finish, which puts every
        # rider miles too far along when the night comes (a 600 with no roadbook "reached" km 433 by
        # midnight). Spread it over the ride instead, as evenly spaced stops.
        inline: List[Tuple[float, float]] = []
        if n == 1 and aggregate_stop_s > 0:
            k_ = max(1, int(round(aggregate_stop_s / 1500.0)))
            inline = [((j + 1) * move_s / (k_ + 1), aggregate_stop_s / k_) for j in range(k_)]
        riding_s = move_s
        if nr or inline or alert:
            arrival, raw_rests, riding_s = _walk_riding(clock, move_s, nr, rest_at_start=(i > 0),
                                                        stops=inline, alert=alert)
            for (r0, r1, done) in raw_rests:
                km_r = geo["start_km"] + geo["distance_km"] * (done / move_s if move_s > 0 else 0.0)
                leg_rests.append({"start": r0, "end": r1, "km": km_r})
            if inline:
                total_stop_s += aggregate_stop_s
                inline_stop_s = aggregate_stop_s
        else:
            arrival = clock + timedelta(seconds=move_s)
        running_moving_s += riding_s if alert else move_s

        stop_s = leg_stops[i] + leg_event_stop[i]   # spread pool + any named stop on this leg
        # No stop at the very finish.
        is_finish = (i == n - 1)
        if is_finish:
            stop_s = 0.0
        slp = leg_sleep[i]
        depart = arrival + timedelta(seconds=stop_s + slp)
        if alert:                                   # control stop = awake, planned sleep block = asleep
            alert.awake(stop_s)
            alert.sleep(slp)
        if nr and not is_finish and _in_no_ride(depart, nr):       # stop ran into the window: rest through it
            r_end = _at_hour(depart, nr[1])
            leg_rests.append({"start": depart, "end": r_end, "km": geo["end_km"]})
            if alert:
                alert.sleep((r_end - depart).total_seconds())
            depart = r_end
        leg_rest_s = sum((r["end"] - r["start"]).total_seconds() for r in leg_rests)
        forced_rest_total_s += leg_rest_s
        total_stop_s += stop_s
        planned_sleep_s += slp

        # Advance the awake clock through this leg + its stop, then pay it down with any sleep here.
        awake_s += move_s + stop_s + inline_stop_s
        if slp > 0:
            awake_s = max(0.0, awake_s - slp * _SLEEP_RESET_K)
            if slp >= _WEAR_MIN_SLEEP_S:
                wear += _WEAR_PER_NIGHT * max(0.0, 1.0 - slp / _WEAR_FULL_NIGHT_S)
        for r in leg_rests:                       # a forced rest is sleep: same reset + wear rules
            d_s = (r["end"] - r["start"]).total_seconds()
            awake_s = max(0.0, awake_s - d_s * _SLEEP_RESET_K)
            if d_s >= _WEAR_MIN_SLEEP_S:
                wear += _WEAR_PER_NIGHT * max(0.0, 1.0 - d_s / _WEAR_FULL_NIGHT_S)

        cutoff_dt = ctrl.cutoff_dt if ctrl else None
        margin_s = None
        if isinstance(cutoff_dt, datetime):
            margin_s = (cutoff_dt - arrival).total_seconds()
            if worst_margin_s is None or margin_s < worst_margin_s:
                worst_margin_s = margin_s
                worst_margin_label = (ctrl.label if ctrl else None)

        # Opening time (ouverture): a manned control won't stamp your card before it opens, so
        # arriving before open_dt means you'd have to WAIT. early_s = how long before opening you
        # arrive (>0 = too early / wait time), None when the control has no opening time.
        open_dt = ctrl.open_dt if ctrl else None
        early_s = None
        if isinstance(open_dt, datetime):
            early_s = (open_dt - arrival).total_seconds()

        rows.append({
            "index": i,
            "label": (ctrl.label if ctrl else ("Finish" if is_finish else f"Control {i + 1}")),
            "distance_km": round(geo["end_km"], 2),
            "leg_distance_km": geo["distance_km"],
            "leg_ascent_m": geo["ascent_m"],
            "moving_time_s": round(move_s, 1),
            "avg_speed_kmh": round(float(lt.get("avg_speed_kmh") or 0.0) * fac, 1),
            "fatigue_factor": round(fac, 3),
            "arrival_dt": _fmt(arrival),
            "stop_s": round(stop_s, 1),
            "sleep_s": round(slp, 1),
            "rests": [{"start": _fmt(r["start"]), "end": _fmt(r["end"]), "km": round(r["km"], 1)} for r in leg_rests],
            "stop_overridden": bool(control_overrides and i in control_overrides and not is_finish),
            "depart_dt": _fmt(depart),
            "cutoff_dt": _fmt(cutoff_dt),
            "margin_s": None if margin_s is None else round(margin_s, 1),
            "opens_dt": _fmt(open_dt),
            "early_s": None if early_s is None else round(early_s, 1),
            "confidence": lt.get("confidence"),
        })
        clock = depart

    # No-controls case: the calibrated stop budget applies as a single aggregate on elapsed.
    if aggregate_stop_s > 0 and n != 1:
        total_stop_s += aggregate_stop_s
        clock = clock + timedelta(seconds=aggregate_stop_s)

    # Sleep sits ON TOP of stops (never in the ratio). The tier suggestion is from the SLEEPLESS
    # elapsed. Planned windows (sleep_windows) were already folded into the walk above; an explicit
    # `sleep` lump (e.g. the what-if "extra sleep" knob) is added on top here.
    elapsed_no_sleep_s = (clock - event.start_dt).total_seconds() - planned_sleep_s - forced_rest_total_s
    sleep_suggested_s = _suggest_sleep_s(elapsed_no_sleep_s)
    lump_sleep_s = 0.0
    if sleep and sleep.get("enabled") and float(sleep.get("duration_s", 0.0)) > 0:
        lump_sleep_s = float(sleep["duration_s"])
        clock = clock + timedelta(seconds=lump_sleep_s)

    finish_eta = clock
    elapsed_s = (finish_eta - event.start_dt).total_seconds()
    sleep_time_s = planned_sleep_s + lump_sleep_s + forced_rest_total_s

    total_ascent_m = round(sum(l["ascent_m"] for l in legs_geo), 1)

    return {
        "ok": True,
        "distance_km": round(total_km, 2),
        "total_ascent_m": total_ascent_m,
        "start_dt": _fmt(event.start_dt),
        "finish_eta_dt": _fmt(finish_eta),
        "moving_time_s": round(running_moving_s, 1),
        "stop_time_s": round(total_stop_s, 1),
        "sleep_time_s": round(sleep_time_s, 1),
        "alertness": None if not alert else {
            "model": "twoprocess", "min": round(alert.min_alertness, 1),
            "min_at": _fmt(alert.min_at) if alert.min_at else None},
        "no_ride": None if not nr else {"start_h": nr[0], "end_h": nr[1], "rest_s": round(forced_rest_total_s, 1)},
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

    # No-ride hours: start 20:00 with 00:00-03:00 off-limits -> a 3 h rest, and NO arrival/stop inside it
    ev = _synthetic_event_with_controls()
    ev.start_dt = datetime(2026, 9, 25, 20, 0)
    for c in ev.cutoffs:
        c.cutoff_dt = ev.start_dt + timedelta(hours=60)
    base = build_timeline(ev, stop_total_s=0.0)
    nrt = build_timeline(ev, stop_total_s=0.0, no_ride={"start_h": 0.0, "end_h": 3.0})
    assert nrt["ok"] and nrt["no_ride"]["rest_s"] >= 3 * 3600 - 1, nrt["no_ride"]
    assert abs(nrt["elapsed_time_s"] - base["elapsed_time_s"] - nrt["no_ride"]["rest_s"]) < 5.0, (nrt["elapsed_time_s"], base["elapsed_time_s"])
    for r in nrt["controls"]:
        h = int(r["arrival_dt"][11:13])
        assert not (0 <= h < 3), "arrived inside the no-ride window: " + r["arrival_dt"]
    rests = [x for r in nrt["controls"] for x in r["rests"]]
    assert rests and rests[0]["start"][11:16] == "00:00" and rests[0]["end"][11:16] == "03:00", rests
    # overnight window (curfew 21:00-06:00) wraps midnight
    cf = build_timeline(ev, stop_total_s=0.0, no_ride={"start_h": 21.0, "end_h": 6.0})
    assert cf["no_ride"]["rest_s"] >= 9 * 3600 - 1, cf["no_ride"]
    print("No-ride hours: 00:00-03:00 -> rest %.1f h, curfew 21-06 -> rest %.1f h. OK"
          % (nrt["no_ride"]["rest_s"] / 3600, cf["no_ride"]["rest_s"] / 3600))
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
                            stop_total_s=body.get("stop_total_s"),
                            stop_profile=body.get("stop_profile"),
                            sleep=body.get("sleep"),
                            sleep_windows=body.get("sleep_windows"),
                            stop_events=body.get("stop_events"),
                            wind_speed_delta_kmh=float(body.get("wind_speed_delta_kmh") or 0.0),
                            control_overrides=overrides or None,
                            fatigue=body.get("fatigue"),
                            no_ride=body.get("no_ride"))
        print(json.dumps({"ok": tl.get("ok", False), "timeline": tl}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
