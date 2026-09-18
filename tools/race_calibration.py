#!/usr/bin/env python3
"""Personal calibration for the curve-first race speed model (engine, 2026-09-18).

Turns a rider's ride HISTORY into the `speed_profile` that AthleteInputs carries and
race_event.estimate_leg() reads. The speed model is

    moving_speed = max(base_speed_kmh - (climb_density / 300) ** 1.2, 4)

and this module fits the ONE rider-specific term, base_speed_kmh, plus how confident that fit
is. It NEVER runs inside estimate_leg (which stays pure) - it is the upstream step that produces
speed_profile, called when history changes.

Why it looks the way it does (all validated against André's real rides + his ultra friend's
hardware-proven Transiberica planner):
  * Fit base from a RECENT trailing window of FILTERED rides. Fitness drifts (André's fitted
    base moved 28.3 -> 25.4 across eras), so recent rides set the LEVEL while the curve SHAPE
    (a fixed constant in race_event) is what all history agrees on.
  * Fit against MOVING speed, never elapsed - stops are the planner's job, not the speed model's.
  * Do NOT calibrate away drafting / wind / terrain. A robust MEDIAN over filtered rides resists
    the fast (draft, tailwind) and slow (headwind) outliers instead of averaging them in. The
    "dirty" rides are still fine for validation elsewhere - just not for the solo baseline.
  * Granularity must match prediction. v1 predicts whole-route, so base is fit from whole-RIDE
    climb density vs whole-ride moving speed (see the convexity note in race_event).

Cold-start cascade for a rider without usable history:
    history fit  ->  one long-ride FIT (back-solve)  ->  self-rating bucket  ->  weight prior

Stdlib only. `--selftest` fits André's real ride summaries (a hardcoded fixture) fully offline.
`--validate <dir>` (optional) fits from real FIT files if you point it at a folder of them.
"""
from __future__ import annotations

import argparse
import math
import os
import statistics
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# Run standalone from anywhere: make sibling tools importable (mirrors how the backend runs tools).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from race_event import CURVE_DIVISOR, CURVE_EXPONENT, climb_density_m_per_100km, curve_speed_kmh

# --- calibration-set inclusion rules (simple, rule-based; deliberately NO ML) ----------------
MIN_CAL_DISTANCE_KM = 60.0    # long enough that endurance pace, not a sprint/commute, dominates
MIN_PLAUSIBLE_KMH = 8.0       # below: MTB crawl / stopped / corrupt - not a road endurance pace
MAX_PLAUSIBLE_KMH = 45.0      # above: corrupt data or sustained motor-pacing
MAX_CLIMB_DENSITY = 4000.0    # m/100km; above this the elevation track is almost certainly junk
MAX_STOP_RATIO = 0.45         # if elapsed is known: drop rides that are mostly stopped
DEFAULT_WINDOW_DAYS = 120     # trailing window for "recent form"
MIN_WINDOW_RIDES = 3          # fewer than this in-window -> widen the window
TARGET_WINDOW_RIDES = 8       # at least this many clean recent rides -> "high" confidence

# Off-road sports are excluded in v1 (road-only; gravel/MTB rolling resistance is unvalidated).
OFFROAD_SPORTS = {"mtb", "gravel", "mountain_biking", "mountain biking", "cyclocross", "offroad"}

# Self-rating buckets -> base_speed (the flat-road intercept), km/h. Conservative on purpose;
# these are a last-ditch cold start, never shown to the rider as a number.
SELF_RATING_BASE = {"casual": 22.0, "steady": 25.0, "strong": 28.0, "racer": 31.0}

# Weight-based physics prior (cold start, no history at all): a flat-road power balance solved
# at a conservative sustained power. FLAT only, so there is no descent root to mis-solve. The
# internal aero/rolling defaults are never exposed to the rider.
PRIOR_W_PER_KG = 1.6          # conservative all-day sustainable power, watts per kg of body mass
PRIOR_CDA = 0.42             # non-aero road hoods (internal default only)
PRIOR_CRR = 0.005            # 25 mm road tyre on tarmac (internal default only)
_G, _RHO, _ETA, _CV = 9.81, 1.2, 0.97, 0.1


def _moving_speed_kmh(ride: Dict[str, Any]) -> Optional[float]:
    t = ride.get("moving_time_s") or 0
    d = ride.get("distance_km") or 0
    if t > 0 and d > 0:
        return d / (t / 3600.0)
    return None


def is_calibration_ride(ride: Dict[str, Any]) -> bool:
    """Rule-based inclusion for the base-fitting set. Returns True only for rides that represent
    the rider's own solo endurance pace on road. Dirty rides (group/headwind/gravel/short) are
    kept OUT of the baseline fit; they remain useful for validation, just not for deriving base."""
    dist = ride.get("distance_km") or 0
    if dist < MIN_CAL_DISTANCE_KM:
        return False
    speed = _moving_speed_kmh(ride)
    if speed is None or not (MIN_PLAUSIBLE_KMH <= speed <= MAX_PLAUSIBLE_KMH):
        return False
    if climb_density_m_per_100km(dist, ride.get("ascent_m") or 0) > MAX_CLIMB_DENSITY:
        return False
    sport = ride.get("sport")
    if isinstance(sport, str) and sport.strip().lower() in OFFROAD_SPORTS:
        return False
    elapsed, moving = ride.get("elapsed_time_s"), ride.get("moving_time_s")
    if elapsed and moving and elapsed > 0 and (1.0 - moving / elapsed) > MAX_STOP_RATIO:
        return False
    if ride.get("is_race") or ride.get("is_group"):  # only if the source flags it; we never guess
        return False
    return True


def _implied_base(ride: Dict[str, Any]) -> Optional[float]:
    """Back-solve base_speed from a single ride by inverting the curve at the ride's whole-ride
    climb density: base = moving_speed + (climb_density / 300) ** 1.2 (the 4 km/h floor is not
    inverted; it only matters on absurdly steep legs, which the inclusion rules exclude)."""
    speed = _moving_speed_kmh(ride)
    if speed is None:
        return None
    cd = climb_density_m_per_100km(ride.get("distance_km") or 0, ride.get("ascent_m") or 0)
    return speed + (cd / CURVE_DIVISOR) ** CURVE_EXPONENT


def _ride_date(ride: Dict[str, Any]) -> Optional[datetime]:
    dt = ride.get("date") or ride.get("start_time")
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=None)
    if isinstance(dt, str):
        try:
            return datetime.fromisoformat(dt.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            try:
                return datetime.strptime(dt[:10], "%Y-%m-%d")
            except ValueError:
                return None
    return None


def calibrate_from_history(rides: List[Dict[str, Any]], now: Optional[datetime] = None,
                           window_days: int = DEFAULT_WINDOW_DAYS) -> Optional[Dict[str, Any]]:
    """Fit base_speed from a trailing window of filtered recent rides. Returns a speed_profile
    dict, or None if there is nothing usable. base = median of per-ride implied base (robust to
    draft/wind outliers); confidence scales with how many clean recent rides backed it."""
    clean = [r for r in rides if is_calibration_ride(r)]
    if not clean:
        return None

    dated = [(_ride_date(r), r) for r in clean]
    ref = now or max((d for d, _ in dated if d), default=None)

    if ref is not None:
        days = window_days
        window = [r for d, r in dated if d and d >= ref - timedelta(days=days)]
        while len(window) < MIN_WINDOW_RIDES and days < 4000:
            days *= 2
            window = [r for d, r in dated if d and d >= ref - timedelta(days=days)]
        if len(window) < MIN_WINDOW_RIDES:
            window = clean  # undated or genuinely sparse history: use every clean ride we have
    else:
        window = clean

    bases = [b for b in (_implied_base(r) for r in window) if b is not None]
    if not bases:
        return None

    n = len(bases)
    confidence = "high" if n >= TARGET_WINDOW_RIDES else ("medium" if n >= MIN_WINDOW_RIDES else "low")
    return {
        "base_speed_kmh": round(statistics.median(bases), 1),
        "confidence": confidence,
        "model_source": "personal",
        "n_recent_rides": n,
    }


def calibrate_from_single_ride(ride: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Cold-start tier 2: one long ride the rider uploads (e.g. a single FIT). One ride is a weak
    anchor, so confidence is 'low', but it places the rider on the curve without any history."""
    base = _implied_base(ride)
    if base is None:
        return None
    return {"base_speed_kmh": round(base, 1), "confidence": "low",
            "model_source": "personal", "n_recent_rides": 1}


def calibrate_from_self_rating(rating: Optional[str]) -> Optional[Dict[str, Any]]:
    """Cold-start tier 3: the rider picks a plain-language bucket (casual/steady/strong/racer)."""
    base = SELF_RATING_BASE.get((rating or "").strip().lower())
    if base is None:
        return None
    return {"base_speed_kmh": base, "confidence": "low",
            "model_source": "generic", "n_recent_rides": 0}


def physics_prior_base(weight_kg: float, bike_weight_kg: float = 10.0,
                       load_weight_kg: float = 0.0) -> Dict[str, Any]:
    """Cold-start tier 4 (last resort): estimate base_speed from body weight via a flat-road
    power balance at a conservative sustained power. Flat only -> the force curve is monotonic in
    speed, so a simple bisection is exact and the descent-root trap can't occur. Low confidence."""
    power = PRIOR_W_PER_KG * weight_kg
    mass = weight_kg + bike_weight_kg + load_weight_kg

    def net(v):  # P*eta - v*(rolling + viscous + aero); root is the steady flat speed
        return power * _ETA - v * (_G * mass * PRIOR_CRR + _CV * v + 0.5 * _RHO * PRIOR_CDA * v * v)

    lo, hi = 0.5, 25.0  # m/s
    for _ in range(60):
        mid = (lo + hi) / 2
        if net(mid) > 0:
            lo = mid
        else:
            hi = mid
    return {"base_speed_kmh": round((lo + hi) / 2 * 3.6, 1), "confidence": "low",
            "model_source": "physics", "n_recent_rides": 0}


def build_speed_profile(rides: Optional[List[Dict[str, Any]]] = None,
                        single_ride: Optional[Dict[str, Any]] = None,
                        self_rating: Optional[str] = None,
                        weight_kg: Optional[float] = None,
                        bike_weight_kg: float = 10.0, load_weight_kg: float = 0.0,
                        now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The cold-start cascade: best available source wins. Returns a speed_profile dict to drop
    onto AthleteInputs.speed_profile, or None if we have nothing at all (estimate_leg then uses
    its conservative placeholder)."""
    if rides:
        profile = calibrate_from_history(rides, now=now)
        if profile:
            return profile
    if single_ride:
        profile = calibrate_from_single_ride(single_ride)
        if profile:
            return profile
    if self_rating:
        profile = calibrate_from_self_rating(self_rating)
        if profile:
            return profile
    if weight_kg:
        return physics_prior_base(weight_kg, bike_weight_kg, load_weight_kg)
    return None


# --- rolling-stop ratio (the "stops" model, 2026-09-18) -------------------------------------
# Stops split into ROLLING stops (eat/pee/refill/checkpoint/short rest) - a near-constant
# FRACTION of ride time - and SLEEP, a discrete block handled separately (never in the ratio).
# André's rides: stop/moving ~16-24% (median ~19%); his ultra friend's Transiberica sheet used
# 16.7%. So the rolling ratio is personal-but-stable, calibrated the same way as base_speed.
DEFAULT_STOP_RATIO = 0.18   # Sommet COLD-START default (stop_time / moving_time). NOT a "population
                            # average" - just a conservative default, replaced by the personal
                            # ratio the moment the rider has history carrying both times.
STOP_RATIO_MAX = 0.60       # sane clamp; above this the ride was mostly stopped (café/social/bad data)
DEFAULT_CONTROL_BASE_S = 600.0  # ~10 min baseline faff at a genuine control


def _stop_ratio_of(ride: Dict[str, Any]) -> Optional[float]:
    """stop_time / moving_time for one ride. Needs BOTH elapsed and moving time - which the local
    Sommet activities DB does NOT store (moving only), so this only works on FIT/intervals rides."""
    el, mv = ride.get("elapsed_time_s"), ride.get("moving_time_s")
    if not el or not mv or el <= 0 or mv <= 0 or el < mv:
        return None
    r = (el - mv) / mv
    return r if 0.0 <= r <= STOP_RATIO_MAX else None


def stop_ratio_from_rides(rides: List[Dict[str, Any]], now: Optional[datetime] = None,
                          window_days: int = DEFAULT_WINDOW_DAYS) -> Dict[str, Any]:
    """Personal rolling-stop ratio = median(stop/moving) over a CURATED, recent set of rides that
    carry both elapsed and moving time. Same recency window + inclusion filter as base_speed.
    Returns {ratio, source, confidence, n_recent_rides}; falls back to the Sommet default when no
    ride carries the elapsed-vs-moving gap (e.g. the rider only has moving-only DB activities)."""
    clean = [r for r in rides if is_calibration_ride(r)]
    dated = [(_ride_date(r), r) for r in clean]
    ref = now or max((d for d, _ in dated if d), default=None)
    window = clean
    if ref is not None:
        days = window_days
        window = [r for d, r in dated if d and d >= ref - timedelta(days=days)]
        while len(window) < MIN_WINDOW_RIDES and days < 4000:
            days *= 2
            window = [r for d, r in dated if d and d >= ref - timedelta(days=days)]
        if len(window) < MIN_WINDOW_RIDES:
            window = clean
    ratios = [x for x in (_stop_ratio_of(r) for r in window) if x is not None]
    if not ratios:
        return {"ratio": DEFAULT_STOP_RATIO, "source": "default", "confidence": "low", "n_recent_rides": 0}
    n = len(ratios)
    confidence = "high" if n >= TARGET_WINDOW_RIDES else ("medium" if n >= MIN_WINDOW_RIDES else "low")
    return {"ratio": round(statistics.median(ratios), 3), "source": "personal",
            "confidence": confidence, "n_recent_rides": n}


def distribute_stops(control_idxs: List[int], leg_moving_s: List[float],
                     stop_ratio: float = DEFAULT_STOP_RATIO,
                     per_control_base_s: float = DEFAULT_CONTROL_BASE_S,
                     overrides: Optional[Dict[int, float]] = None) -> Dict[int, float]:
    """Allocate a rolling-stop budget across a route's genuine CONTROLS. Pure + unit-testable; the
    timeline calls this to turn a stop_ratio into per-control stop seconds.

    Args:
        control_idxs: leg indices that END at a genuine control eligible for a stop (exclude the
            finish leg). Legs not listed get nothing - we do NOT manufacture a stop just because a
            route was segmented (PM rule). Empty -> {} (caller applies the aggregate to elapsed).
        leg_moving_s: full per-leg moving-seconds list (all legs, incl. the finish leg).
        stop_ratio: rolling budget = stop_ratio * sum(leg_moving_s)  (total moving time).
        per_control_base_s: fixed base per eligible control (checkpoint faff).
        overrides: {control_leg_index: seconds} manual values. They WIN, and the TOTAL budget is
            preserved: the remainder is redistributed among non-overridden controls, so editing one
            control never silently changes total race time (PM rule).

    Returns:
        {control_leg_index: stop_seconds} for the controls in control_idxs. Non-control legs are
        absent (=> 0). sum(returned values) == stop_ratio * total_moving (the budget is preserved).
    """
    control_idxs = [i for i in control_idxs if 0 <= i < len(leg_moving_s)]
    overrides = {int(i): max(0.0, float(v))
                 for i, v in (overrides or {}).items() if int(i) in control_idxs}
    out: Dict[int, float] = dict(overrides)  # overridden controls take their fixed value

    eligible = [i for i in control_idxs if i not in overrides]
    budget = max(0.0, stop_ratio) * sum(leg_moving_s)
    remaining = max(0.0, budget - sum(overrides.values()))
    if not eligible:
        return out

    base_total = per_control_base_s * len(eligible)
    if base_total >= remaining:  # budget too small for full bases: split what's left equally
        share = remaining / len(eligible)
        for i in eligible:
            out[i] = share
        return out
    leftover = remaining - base_total
    moving_of_eligible = sum(leg_moving_s[i] for i in eligible) or 1.0
    for i in eligible:
        out[i] = per_control_base_s + leftover * (leg_moving_s[i] / moving_of_eligible)
    return out


# Sleep suggestion tiers (SUGGESTION only - the rider decides; never modelled/optimised).
def suggest_sleep_s(predicted_elapsed_s: float) -> int:
    """PM tiers: <20h none, 20-30h ~1h, 30-40h ~3h, >40h ~5h (midpoints of the suggested ranges)."""
    h = predicted_elapsed_s / 3600.0
    if h < 20:
        return 0
    if h < 30:
        return 1 * 3600
    if h < 40:
        return 3 * 3600
    return 5 * 3600


def ride_summary_from_fit(path: str) -> Optional[Dict[str, Any]]:
    """Load the calibration-relevant summary from one FIT file (the one-FIT cold-start path and
    the --validate mode). Uses fit_decode's moving time (total_timer_time); falls back to elapsed
    only if the device recorded no timer time. Returns None if the file has no usable distance."""
    import fit_decode
    s = fit_decode.decode(path)
    dist_km = (s.get("distanceMeters") or 0) / 1000.0
    if dist_km <= 0:
        return None
    moving_s = s.get("movingSeconds") or s.get("durationSeconds") or 0
    return {
        "date": s.get("startTime"),
        "distance_km": dist_km,
        "ascent_m": s.get("ascentMeters") or 0.0,
        "moving_time_s": moving_s,
        "elapsed_time_s": s.get("durationSeconds") or 0,
        "sport": s.get("sport"),
    }


# --- André's real ride summaries (offline fixture for --selftest) ----------------------------
# (date, distance_km, ascent_m, moving_time_hours). From the FIT/DB validation done 2026-09-17;
# includes deliberately "dirty" rides (headwind BRM300, drafted 600s, a tailwind/bunch ride, a
# 5-day-Alpine mountain day) so the fixture exercises the robustness of the median fit.
_ANDRE_RIDES_RAW = [
    ("2019-01-26", 209.5, 914, 8.86),
    ("2020-07-13", 218.5, 1643, 9.31),
    ("2021-06-07", 204.5, 4263, 13.6),   # mountain (Alps, day 1 of 5)
    ("2022-03-19", 205.7, 1271, 8.4),
    ("2023-02-25", 202.7, 915, 8.1),
    ("2023-04-01", 313.0, 2152, 12.9),
    ("2024-02-17", 280.2, 1906, 11.5),
    ("2024-03-16", 203.2, 1240, 7.0),    # fast bunch / tailwind (upward outlier)
    ("2026-04-04", 300.2, 2335, 14.2),   # BRM 300, headwind (downward outlier)
    ("2026-06-06", 311.8, 2700, 13.24),  # BRM 600 D1, drafted
    ("2026-06-07", 290.4, 2292, 12.53),  # BRM 600 D2, drafted
]


def _andre_fixture() -> List[Dict[str, Any]]:
    return [{"date": d, "distance_km": km, "ascent_m": asc, "moving_time_s": int(h * 3600)}
            for d, km, asc, h in _ANDRE_RIDES_RAW]


# Real (moving_h, elapsed_h) pairs for the rides that recorded auto-pause, for stop-ratio tests.
# The five 2021 Alpine rides are omitted: their FITs have timer==elapsed (auto-pause was off), so
# they carry no usable stop gap - exactly the kind of ride _stop_ratio_of() returns None for.
_ANDRE_STOP_RAW = [
    ("2026-04-04", 300.2, 2335, 14.21, 16.93),
    ("2026-06-06", 311.8, 2700, 13.24, 16.90),
    ("2026-06-07", 290.4, 2292, 12.53, 15.51),
    ("2023-02-25", 202.7, 915, 8.07, 9.60),
    ("2023-04-01", 313.0, 2152, 12.87, 14.97),
    ("2024-02-17", 280.2, 1906, 11.51, 14.12),
    ("2024-03-16", 203.2, 1240, 7.03, 8.35),
    ("2022-03-19", 205.7, 1271, 8.44, 9.00),
]


def _andre_stop_fixture() -> List[Dict[str, Any]]:
    return [{"date": d, "distance_km": km, "ascent_m": asc,
             "moving_time_s": int(mv * 3600), "elapsed_time_s": int(el * 3600)}
            for d, km, asc, mv, el in _ANDRE_STOP_RAW]


def _selftest():
    rides = _andre_fixture()
    print("=== race_calibration selftest (André fixture, offline) ===")

    # Full-history fit (wide window = all clean rides): base should land in a sane band and the
    # curve should reproduce the fixture's moving speeds with a small median residual.
    prof = calibrate_from_history(rides, now=None, window_days=4000)
    assert prof and prof["model_source"] == "personal"
    base = prof["base_speed_kmh"]
    print(f"Full-history fit: base={base} km/h, confidence={prof['confidence']}, n={prof['n_recent_rides']}")
    assert 24.0 <= base <= 28.0, f"base {base} outside sane band"
    assert prof["confidence"] == "high" and prof["n_recent_rides"] == 11

    resids = []
    for r in rides:
        cd = climb_density_m_per_100km(r["distance_km"], r["ascent_m"])
        pred = curve_speed_kmh(base, cd)
        obs = _moving_speed_kmh(r)
        resids.append(abs(pred - obs))
    med_resid = statistics.median(resids)
    print(f"Curve vs observed: median |resid| = {med_resid:.2f} km/h, max = {max(resids):.2f} km/h")
    assert med_resid < 1.5, f"median residual {med_resid:.2f} too high - curve/shape mismatch"

    # Recency window: fitness drift means a recent-window fit differs from the all-time fit.
    recent = calibrate_from_history(rides, now=datetime(2026, 9, 18))
    print(f"Recent-window fit (as of 2026-09-18): base={recent['base_speed_kmh']} km/h, "
          f"confidence={recent['confidence']}, n={recent['n_recent_rides']}")
    assert recent and 23.0 <= recent["base_speed_kmh"] <= 29.0
    assert recent["n_recent_rides"] >= MIN_WINDOW_RIDES

    # Cold-start cascade.
    one = calibrate_from_single_ride({"date": "2023-02-25", "distance_km": 202.7,
                                      "ascent_m": 915, "moving_time_s": int(8.1 * 3600)})
    assert one["model_source"] == "personal" and one["confidence"] == "low" and one["n_recent_rides"] == 1
    print(f"One-ride back-solve: base={one['base_speed_kmh']} km/h ({one['confidence']})")

    steady = calibrate_from_self_rating("steady")
    assert steady["model_source"] == "generic" and steady["base_speed_kmh"] == 25.0

    phys = physics_prior_base(86.0, bike_weight_kg=8.0, load_weight_kg=4.0)
    assert phys["model_source"] == "physics" and 18.0 <= phys["base_speed_kmh"] <= 30.0
    print(f"Physics prior (86 kg): base={phys['base_speed_kmh']} km/h ({phys['confidence']})")

    # Cascade precedence + the empty case.
    assert build_speed_profile(rides=rides)["model_source"] == "personal"
    assert build_speed_profile(single_ride=one and {"distance_km": 202.7, "ascent_m": 915,
                               "moving_time_s": int(8.1 * 3600)})["model_source"] == "personal"
    assert build_speed_profile(self_rating="strong")["model_source"] == "generic"
    assert build_speed_profile(weight_kg=86.0)["model_source"] == "physics"
    assert build_speed_profile() is None

    # Inclusion rules reject the rides they should.
    assert not is_calibration_ride({"distance_km": 30, "ascent_m": 100, "moving_time_s": 3600})   # too short
    assert not is_calibration_ride({"distance_km": 120, "ascent_m": 100, "moving_time_s": 3600})  # 120 km/h -> corrupt
    assert not is_calibration_ride({"distance_km": 120, "ascent_m": 100, "moving_time_s": 200000}) # 2 km/h -> corrupt/MTB
    assert not is_calibration_ride({"distance_km": 120, "ascent_m": 200, "moving_time_s": int(6 * 3600),
                                    "sport": "gravel"})                                           # off-road (v1 road-only)
    assert is_calibration_ride({"distance_km": 120, "ascent_m": 800, "moving_time_s": int(5 * 3600)})  # good road ride
    print("Inclusion rules: short/corrupt/gravel rejected, clean road ride accepted ✓")

    # --- rolling-stop ratio ---
    sp = stop_ratio_from_rides(_andre_stop_fixture(), now=None, window_days=4000)
    print(f"Stop ratio (curated fixture): {sp['ratio']} ({sp['source']}, {sp['confidence']}, n={sp['n_recent_rides']})")
    assert sp["source"] == "personal" and 0.15 <= sp["ratio"] <= 0.24 and sp["n_recent_rides"] == 8
    # Rides without elapsed (e.g. moving-only DB activities) -> Sommet default, not a crash.
    assert stop_ratio_from_rides(_andre_fixture())["source"] == "default"
    assert stop_ratio_from_rides(_andre_fixture())["ratio"] == DEFAULT_STOP_RATIO

    # --- distribute_stops: budget preserved, overrides win + redistribute ---
    legs = [3600.0, 3600.0, 3600.0, 3600.0]  # 4 legs; leg 3 is the finish (not a control)
    controls = [0, 1, 2]
    budget = 0.20 * sum(legs)
    st = distribute_stops(controls, legs, stop_ratio=0.20, per_control_base_s=600.0)
    assert 3 not in st, "finish leg is not a control -> absent"
    assert abs(sum(st.values()) - budget) < 1e-6, "total rolling budget must equal ratio*moving"
    st2 = distribute_stops(controls, legs, stop_ratio=0.20, per_control_base_s=600.0, overrides={1: 1800.0})
    assert st2[1] == 1800.0, "override wins"
    assert abs(sum(st2.values()) - budget) < 1e-6, "budget preserved after override (remainder redistributed)"
    assert distribute_stops([], legs, stop_ratio=0.20) == {}, "no controls -> {} (caller aggregates)"
    print(f"distribute_stops: budget {budget:.0f}s preserved (plain { {k: round(v) for k, v in st.items()} }, "
          f"CP2 override 1800 -> { {k: round(v) for k, v in st2.items()} })")

    # --- sleep suggestion tiers ---
    assert suggest_sleep_s(15 * 3600) == 0
    assert suggest_sleep_s(25 * 3600) == 3600
    assert suggest_sleep_s(35 * 3600) == 3 * 3600
    assert suggest_sleep_s(50 * 3600) == 5 * 3600
    print("Sleep tiers: 15h->0, 25h->1h, 35h->3h, 50h->5h ✓")

    print("\n✓ All race_calibration selftest checks passed")


def _validate(dirpath: str):
    """Optional: fit from real FIT files in a folder (not part of --selftest; needs the files)."""
    paths = [os.path.join(dirpath, f) for f in sorted(os.listdir(dirpath)) if f.lower().endswith(".fit")]
    rides = []
    for p in paths:
        try:
            r = ride_summary_from_fit(p)
            if r:
                rides.append(r)
        except Exception as e:
            print(f"  skip {os.path.basename(p)}: {e}")
    clean = [r for r in rides if is_calibration_ride(r)]
    print(f"{len(paths)} FIT files -> {len(rides)} decoded -> {len(clean)} pass calibration filter")
    prof = calibrate_from_history(rides)
    print(f"speed_profile: {prof}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Personal calibration for the race speed model")
    ap.add_argument("--selftest", action="store_true", help="run offline self-tests (André fixture)")
    ap.add_argument("--validate", metavar="DIR", help="fit from real FIT files in DIR (needs the files)")
    args = ap.parse_args(argv)
    if args.selftest:
        _selftest()
    elif args.validate:
        _validate(args.validate)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
