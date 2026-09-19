#!/usr/bin/env python3
"""Race event and race plan data models + naive baseline ETA calculation.

This is the foundation for the BRM/ultra-distance race planner. It defines:
  - RaceEvent: name, date, route, cutoffs
  - RacePlan: the event + athlete/bike inputs + provisional baseline output (distance/ETA/moving time)
  - AthleteInputs: weight, FTP, RMR (required, optional, optional)
  - BikeInputs: bike weight, load weight, type, aero category

baseline_plan() assembles a plan; the PREDICTED moving time comes from the estimate_leg() seam,
which now carries the curve-first empirical speed model (engine, 2026-09-18): a rider's moving
speed as a function of how much a route climbs, calibrated per rider from ride history by
tools/race_calibration.py. estimate_leg() stays PURE (reads athlete.speed_profile; never fetches).

No network, no geometry -- just JSON round-tripping + arithmetic on distances/times.
Stdlib only. `--selftest` proves the maths + the seam on a synthetic event.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

# Bike type -> default effective frontal area (m²) per BRouter Longdistance profile
BIKE_TYPE_FRONTAL_AREA = {
    "road": 0.50,
    "gravel": 0.50,
    "mtb": 0.57,
    "tour": 0.65,
}

# Aero category (if rider specifies) -> CdA adder (relative to bike-type default, simplification)
AERO_CATEGORY_CDA = {
    "tops": 0.0,  # default position for the bike type
    "hoods": -0.05,  # hands on hoods, slightly better
    "drops": -0.10,  # road-bike drops (if applicable)
}


@dataclass
class Cutoff:
    """A control/aid station along the route with a time limit."""
    label: str
    distance_km: float
    cutoff_dt: Optional[datetime] = None  # closing time (fermeture); set by baseline_plan if not given
    open_dt: Optional[datetime] = None    # opening time (ouverture): arrival-not-before at a manned control

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "distance_km": self.distance_km,
            "cutoff_dt": self.cutoff_dt.isoformat() if isinstance(self.cutoff_dt, datetime) else self.cutoff_dt,
            "open_dt": self.open_dt.isoformat() if isinstance(self.open_dt, datetime) else self.open_dt,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Cutoff":
        cutoff_dt = d.get("cutoff_dt")
        if isinstance(cutoff_dt, str):
            cutoff_dt = datetime.fromisoformat(cutoff_dt)
        open_dt = d.get("open_dt")
        if isinstance(open_dt, str):
            open_dt = datetime.fromisoformat(open_dt)
        return Cutoff(
            label=d["label"],
            distance_km=d["distance_km"],
            cutoff_dt=cutoff_dt,
            open_dt=open_dt,
        )


@dataclass
class AthleteInputs:
    """User-provided athlete physical parameters."""
    weight_kg: float
    ftp_w: Optional[float] = None  # threshold power; optional, dormant in the v1 prediction path
    rmr_kcal_day: Optional[float] = None  # resting metabolic rate; optional, dormant in v1
    # Resolved, calibrated riding profile — the carrier that reaches estimate_leg (agreed with the
    # engine session, 2026-09-18). estimate_leg stays PURE: it READS this, it does not compute it.
    # An upstream calibration component (engine's Task B, separate from this seam) fits it from a
    # recent trailing window of filtered rides and populates it here. Shape:
    #   {"base_speed_kmh": float, "confidence": "low"|"medium"|"high",
    #    "model_source": "personal"|"generic"|"physics", "n_recent_rides": int}
    # None until calibration has run (cold start) -> estimate_leg falls back to its placeholder.
    speed_profile: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "AthleteInputs":
        return AthleteInputs(**d)


@dataclass
class BikeInputs:
    """User-provided bike physical parameters."""
    bike_weight_kg: float
    load_weight_kg: float = 0.0  # luggage, bottles, etc.
    bike_type: str = "tour"  # road, gravel, mtb, tour
    aero_category: Optional[str] = None  # tops, hoods, drops; optional override

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "BikeInputs":
        return BikeInputs(**d)

    def system_mass_kg(self, athlete_weight_kg: float) -> float:
        """Total mass: rider + bike + load."""
        return athlete_weight_kg + self.bike_weight_kg + self.load_weight_kg


@dataclass
class RaceEvent:
    """A brevet/ultra/race event definition."""
    name: str
    event_type: str  # "BRM", "ultra", "other"
    start_dt: datetime
    # Route is represented as either:
    # - gpx: raw GPX text (parsed downstream by weather_route.py etc.)
    # - points: list of {lat, lon, ele?} dicts (parsed from GPX upstream)
    # We store one or the other, but the model accepts both (mirrors weather_route.py).
    gpx: Optional[str] = None
    points: List[Dict[str, Any]] = field(default_factory=list)
    target_finish_dt: Optional[datetime] = None  # user's desired finish time (if not using cutoffs)
    cutoffs: List[Cutoff] = field(default_factory=list)  # control points with time limits

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "event_type": self.event_type,
            "start_dt": self.start_dt.isoformat() if isinstance(self.start_dt, datetime) else self.start_dt,
            "gpx": self.gpx,
            "points": self.points,
            "target_finish_dt": self.target_finish_dt.isoformat() if isinstance(self.target_finish_dt, datetime) else self.target_finish_dt,
            "cutoffs": [c.to_dict() if isinstance(c, Cutoff) else c for c in self.cutoffs],
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "RaceEvent":
        start_dt = d.get("start_dt")
        if isinstance(start_dt, str):
            start_dt = datetime.fromisoformat(start_dt)
        target_finish_dt = d.get("target_finish_dt")
        if isinstance(target_finish_dt, str):
            target_finish_dt = datetime.fromisoformat(target_finish_dt)
        cutoffs = [Cutoff.from_dict(c) if isinstance(c, dict) else c for c in d.get("cutoffs", [])]
        return RaceEvent(
            name=d["name"],
            event_type=d["event_type"],
            start_dt=start_dt,
            gpx=d.get("gpx"),
            points=d.get("points", []),
            target_finish_dt=target_finish_dt,
            cutoffs=cutoffs,
        )


@dataclass
class RacePlan:
    """A planned race: event + athlete/bike inputs + baseline output (provisional until real engine)."""
    event: RaceEvent
    athlete: AthleteInputs
    bike: BikeInputs
    # Baseline output (naive constant-speed math, marked provisional):
    provisional: bool = True
    distance_m: float = 0.0
    finish_eta_dt: Optional[datetime] = None
    moving_time_s: float = 0.0  # total time at constant "baseline speed"
    required_avg_speed_kmh: float = 0.0
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event.to_dict(),
            "athlete": self.athlete.to_dict(),
            "bike": self.bike.to_dict(),
            "provisional": self.provisional,
            "distance_m": self.distance_m,
            "finish_eta_dt": self.finish_eta_dt.isoformat() if isinstance(self.finish_eta_dt, datetime) else self.finish_eta_dt,
            "moving_time_s": self.moving_time_s,
            "required_avg_speed_kmh": self.required_avg_speed_kmh,
            "summary": self.summary,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "RacePlan":
        event = RaceEvent.from_dict(d["event"])
        athlete = AthleteInputs.from_dict(d["athlete"])
        bike = BikeInputs.from_dict(d["bike"])
        finish_eta_dt = d.get("finish_eta_dt")
        if isinstance(finish_eta_dt, str):
            finish_eta_dt = datetime.fromisoformat(finish_eta_dt)
        return RacePlan(
            event=event,
            athlete=athlete,
            bike=bike,
            provisional=d.get("provisional", True),
            distance_m=d.get("distance_m", 0.0),
            finish_eta_dt=finish_eta_dt,
            moving_time_s=d.get("moving_time_s", 0.0),
            required_avg_speed_kmh=d.get("required_avg_speed_kmh", 0.0),
            summary=d.get("summary", {}),
        )


def _cumulative_distances(points: List[Dict[str, Any]]) -> tuple[List[float], float]:
    """Compute cumulative distance (metres) for a list of {lat, lon} points.
    Returns (cumul_dists, total_m)."""
    if len(points) < 2:
        return [], 0.0

    def haversine_m(lat1, lon1, lat2, lon2):
        """Distance in metres."""
        R = 6371000  # Earth radius
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return 2 * R * math.asin(math.sqrt(a))

    cumul = [0.0]
    for i in range(1, len(points)):
        p1, p2 = points[i - 1], points[i]
        d = haversine_m(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
        cumul.append(cumul[-1] + d)

    return cumul, cumul[-1] if cumul else 0.0


def _ascent_descent_m(points: List[Dict[str, Any]]) -> tuple[float, float]:
    """Sum positive/negative elevation deltas across points. Returns (ascent_m, descent_m).
    Points without a usable 'ele' contribute nothing."""
    ascent = descent = 0.0
    prev = None
    for p in points:
        ele = p.get("ele")
        if ele in (None, ""):
            continue
        ele = float(ele)
        if prev is not None:
            d = ele - prev
            if d > 0:
                ascent += d
            else:
                descent += -d
        prev = ele
    return ascent, descent


def baseline_plan(event: RaceEvent, athlete: Optional[AthleteInputs] = None,
                  bike: Optional[BikeInputs] = None,
                  estimator: Optional[Callable[..., LegEstimate]] = None) -> RacePlan:
    """Assemble a race plan. The PREDICTED moving time comes from the speed-model seam
    (estimate_leg), NOT computed inline here - so when the engine replaces estimate_leg with
    the real curve-first model, this plan improves with no change on the foundation side.

    `estimator` is injectable purely so tests can pass a mocked LegEstimate; production always
    uses estimate_leg. The route is treated as a single leg for now; control-segmented legs
    come later with the Race Timeline layer (deliberately NOT built yet, per the PM).

    The cutoff/target only yields required_avg_speed_kmh as an informational CONSTRAINT
    ("you must average this to make it") - it is not the prediction. Cutoff-margin logic is
    timeline intelligence and is intentionally deferred.
    """
    if athlete is None:
        athlete = AthleteInputs(weight_kg=75.0)  # sensible default
    if bike is None:
        bike = BikeInputs(bike_weight_kg=10.0, load_weight_kg=5.0)  # sensible touring defaults
    if estimator is None:
        estimator = estimate_leg

    # Extract route points (from explicit points, else parse the GPX).
    points = event.points
    if not points and event.gpx:
        try:
            import geo_util
            points = geo_util.parse_gpx_points(event.gpx)
        except Exception:
            points = []

    _, distance_m = _cumulative_distances(points) if points else ([], 0.0)
    if distance_m == 0.0:
        return RacePlan(
            event=event, athlete=athlete, bike=bike,
            provisional=True,
            summary={"error": "route has no distance (no GPX or points)"},
        )

    distance_km = distance_m / 1000.0
    ascent_m, descent_m = _ascent_descent_m(points)

    # PREDICTED moving time via the seam (single leg = whole route for now).
    est = estimator(distance_km, ascent_m, descent_m, athlete, bike)
    moving_time_s = est.moving_time_s
    finish_dt = event.start_dt + timedelta(seconds=moving_time_s)

    # Informational cutoff/target CONSTRAINT (not the prediction): the average speed the rider
    # would have to hold to arrive by their target or tightest cutoff.
    required_avg_kmh = None
    deadline_dt = event.target_finish_dt
    if deadline_dt is None and event.cutoffs:
        cutoff_dts = [c.cutoff_dt for c in event.cutoffs if c.cutoff_dt]
        deadline_dt = min(cutoff_dts) if cutoff_dts else None
    if deadline_dt:
        hours = (deadline_dt - event.start_dt).total_seconds() / 3600.0
        if hours > 0:
            required_avg_kmh = round(distance_km / hours, 1)

    summary = {
        "distance_km": round(distance_km, 2),
        "ascent_m": round(ascent_m),
        "predicted_avg_speed_kmh": est.avg_speed_kmh,
        "moving_time_hours": round(moving_time_s / 3600.0, 1),
        "finish_time": finish_dt.strftime("%H:%M"),
        "confidence": est.confidence,
        "model_source": est.model_source,
        "required_avg_speed_kmh": required_avg_kmh,  # None when no target/cutoff set
    }

    return RacePlan(
        event=event,
        athlete=athlete,
        bike=bike,
        provisional=True,
        distance_m=distance_m,
        finish_eta_dt=finish_dt,
        moving_time_s=moving_time_s,
        required_avg_speed_kmh=required_avg_kmh or 0.0,
        summary=summary,
    )


@dataclass
class LegEstimate:
    """What the speed model returns for one route leg. This is the engine<->foundation seam:
    the foundation asks for a leg's time and never has to know how the number was produced.
    `model_source` lets the UI explain the ETA ("based on your recent riding" vs "generic
    estimate; limited data") without exposing the underlying maths."""
    moving_time_s: float
    avg_speed_kmh: float
    confidence: str = "low"          # "low" | "medium" | "high"
    model_source: str = "placeholder"  # "personal" | "generic" | "physics" | "placeholder"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --- Curve-first speed model (engine, 2026-09-18) -------------------------------------------
# Empirical moving-speed-vs-climbing model. Validated against André's ride history AND his
# experienced ultra friend's hardware-proven Transiberica planner (which predicted a real
# 168 h / 2841 km / 40860 m finish within ~2 h). The model:
#
#     climb_density   = ascent_m / distance_km * 100          # metres climbed per 100 km
#     moving_speed_kmh = max(base_speed_kmh - (climb_density / 300) ** 1.2, 4)
#
# base_speed_kmh is the ONLY rider-specific term (calibrated from history by race_calibration).
# The SHAPE - the 300 divisor, the 1.2 exponent, the 4 km/h floor - is a fixed constant: it fit
# two different riders with only `base` changing, so it is deliberately NOT per-rider in v1.
#
# NOTE 1 (UI): base is a flat-road (zero-climb) INTERCEPT the rider never actually rides - for
# André it fits ~27 while his real rolling rides sit ~24 km/h. The UI must show the predicted
# speed AT the route's real climb density, never this raw base.
# NOTE 2 (granularity, important): v1 treats the whole route as ONE leg, so the curve sees the
# route's AVERAGE climb density. The curve is convex, so calibration MUST fit base at the SAME
# granularity it predicts at (whole-ride climb density vs whole-ride moving speed) or the bias
# will not cancel. Per-control segmentation + matching calibration arrive with the Timeline layer.
CURVE_DIVISOR = 300.0
CURVE_EXPONENT = 1.2
CURVE_FLOOR_KMH = 4.0


def climb_density_m_per_100km(distance_km: float, ascent_m: float) -> float:
    """Metres climbed per 100 km - the single input the speed curve is shaped on."""
    if distance_km <= 0:
        return 0.0
    return (ascent_m or 0.0) / distance_km * 100.0


def curve_speed_kmh(base_speed_kmh: float, climb_density: float) -> float:
    """The curve: base speed minus a convex climbing penalty, floored so it never predicts a
    stall. Shared with race_calibration (which inverts it to back-solve base from a ride)."""
    return max(base_speed_kmh - (climb_density / CURVE_DIVISOR) ** CURVE_EXPONENT, CURVE_FLOOR_KMH)


def estimate_leg(distance_km: float, ascent_m: float, descent_m: float,
                 athlete: AthleteInputs, bike: BikeInputs,
                 conditions: Optional[Dict[str, Any]] = None) -> LegEstimate:
    """Estimate moving time for one route leg. THE engine<->foundation seam.

    Applies the curve-first speed model: reads the rider's calibrated base_speed from
    athlete.speed_profile and shapes it by the leg's climb density
    (moving_speed = max(base - (climb_density/300)^1.2, 4)). Stays PURE - it consumes the
    profile that race_calibration produced upstream; it never loads ride history itself.

    Contract (agreed with the PM, 2026-09-18):
      - per-leg is the primitive; the thin estimate_route() aggregator sits just below.
      - v1 is road-only: NO surface parameter (gravel Crr unvalidated).
      - NO CdA / Crr / RMR / drivetrain / FTP dependence in the v1 prediction path
        (athlete.weight & bike are accepted but the curve uses only distance + ascent).

    Args:
        distance_km: leg horizontal distance (km)
        ascent_m: leg climbing (m)
        descent_m: leg descent (m) - accepted for the seam; the v1 curve does not use it
        athlete: AthleteInputs; athlete.speed_profile carries the calibrated base_speed
        bike: BikeInputs (weight/load/type; engineering coeffs dormant)
        conditions: optional {"wind_kmh", "temp_c", ...} a later model may use (unused in v1)

    Returns:
        LegEstimate(moving_time_s, avg_speed_kmh, confidence, model_source)
    """
    profile = athlete.speed_profile if athlete else None
    if profile and profile.get("base_speed_kmh"):
        base = float(profile["base_speed_kmh"])
        climb_density = climb_density_m_per_100km(distance_km, ascent_m)
        speed_kmh = curve_speed_kmh(base, climb_density)
        confidence = profile.get("confidence", "medium")
        model_source = profile.get("model_source", "personal")
    else:
        # Cold start: calibration has not run (no history / not yet fitted). Deliberately
        # conservative flat guess, labelled so the UI says "generic estimate, limited data".
        # race_calibration.build_speed_profile() is what fills speed_profile upstream (history
        # fit -> one-FIT back-solve -> self-rating bucket -> weight-based physics prior).
        speed_kmh = 15.0
        confidence = "low"
        model_source = "placeholder"

    moving_time_s = (distance_km / speed_kmh) * 3600.0 if speed_kmh > 0 else 0.0

    return LegEstimate(
        moving_time_s=round(moving_time_s, 1),
        avg_speed_kmh=round(speed_kmh, 1),
        confidence=confidence,
        model_source=model_source,
    )


def estimate_route(legs: List[tuple], athlete: AthleteInputs, bike: BikeInputs,
                   conditions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Thin aggregator ABOVE the per-leg primitive. `legs` is a list of
    (distance_km, ascent_m, descent_m) tuples - e.g. control-to-control segments. Sums
    estimate_leg over them. v1 callers pass a single whole-route leg; the Timeline layer will
    pass control segments (which also makes the curve more accurate on very varied routes, since
    each segment then sees its own climb density instead of the route average).

    Returns totals + the per-leg breakdown. Confidence is the WORST leg's (a plan is only as
    trustworthy as its least-known segment); model_source is the shared athlete profile's.
    """
    leg_estimates: List[LegEstimate] = []
    total_time_s = 0.0
    total_km = 0.0
    for dist_km, ascent, descent in legs:
        e = estimate_leg(dist_km, ascent, descent, athlete, bike, conditions)
        leg_estimates.append(e)
        total_time_s += e.moving_time_s
        total_km += dist_km

    rank = {"low": 0, "medium": 1, "high": 2}
    confidence = min((e.confidence for e in leg_estimates),
                     key=lambda c: rank.get(c, 0), default="low")
    model_source = leg_estimates[0].model_source if leg_estimates else "placeholder"
    avg_kmh = round(total_km / (total_time_s / 3600.0), 1) if total_time_s > 0 else 0.0

    return {
        "moving_time_s": round(total_time_s, 1),
        "distance_km": round(total_km, 2),
        "avg_speed_kmh": avg_kmh,
        "confidence": confidence,
        "model_source": model_source,
        "legs": [e.to_dict() for e in leg_estimates],
    }


def _synthetic_event() -> RaceEvent:
    """Synthetic test event: a 200 km route starting at 08:00 with a cutoff at 21:30 (13.5 hours)."""
    start = datetime(2026, 9, 25, 8, 0)
    # Synthetic route: 200 km in ~200 evenly-spaced points along a straight line.
    # Each ~0.01° ≈ 1.1 km, so 200 points covers ~200 km.
    points = [
        {"lat": 45.5 + i * 0.01, "lon": 3.0, "ele": 400}
        for i in range(200)
    ]
    return RaceEvent(
        name="Test 200k BRM",
        event_type="BRM",
        start_dt=start,
        points=points,
        cutoffs=[Cutoff(label="Finish", distance_km=200.0, cutoff_dt=start + timedelta(hours=13, minutes=30))],
    )


def _selftest():
    """Prove the baseline assembly + the engine<->foundation seam on a synthetic 200k BRM."""
    event = _synthetic_event()
    plan = baseline_plan(event)

    print("=== Synthetic 200k BRM ===")
    print(f"Distance: {plan.summary.get('distance_km')} km")
    print(f"Ascent: {plan.summary.get('ascent_m')} m")
    print(f"Predicted avg speed: {plan.summary.get('predicted_avg_speed_kmh')} km/h ({plan.summary.get('model_source')}, {plan.summary.get('confidence')})")
    print(f"Required avg speed (cutoff constraint): {plan.summary.get('required_avg_speed_kmh')} km/h")
    print(f"Moving time: {plan.summary.get('moving_time_hours')} hours")
    print(f"Finish time: {plan.summary.get('finish_time')}")

    # Baseline assembly sanity:
    assert plan.distance_m > 100_000, f"Expected >100km, got {plan.distance_m}m"
    assert plan.moving_time_s > 0
    assert plan.finish_eta_dt is not None
    assert plan.provisional is True
    # Placeholder prediction goes through the seam (15 km/h) and is labelled as such:
    assert plan.summary["model_source"] == "placeholder"
    assert abs(plan.summary["predicted_avg_speed_kmh"] - 15.0) < 0.01
    # The cutoff still surfaces as an informational required-speed constraint:
    assert plan.summary["required_avg_speed_kmh"] and plan.summary["required_avg_speed_kmh"] > 10

    # estimate_leg seam returns a LegEstimate of the agreed shape:
    leg = estimate_leg(50.0, 500.0, 500.0, plan.athlete, plan.bike)
    print(f"\nestimate_leg(50km, 500m): {leg.to_dict()}")
    assert isinstance(leg, LegEstimate)
    assert leg.moving_time_s > 0 and leg.avg_speed_kmh > 0
    assert leg.model_source == "placeholder"

    # SEAM FIELD: a calibrated speed_profile on the athlete flows through estimate_leg and into
    # the plan (proves the engine's calibration output has a defined home). Still flat here -
    # the climb-density curve is the engine's to add in estimate_leg's body.
    calibrated = AthleteInputs(weight_kg=86.0, speed_profile={
        "base_speed_kmh": 24.0, "confidence": "high", "model_source": "personal",
        "n_recent_rides": 40})
    # Flat leg (0 ascent): the curve returns base exactly.
    leg_flat = estimate_leg(50.0, 0.0, 0.0, calibrated, plan.bike)
    assert abs(leg_flat.avg_speed_kmh - 24.0) < 0.01, "flat leg must equal base_speed"
    # Hilly leg (1000 m/100km): the CURVE bites - speed = 24 - (1000/300)^1.2 ~= 19.76.
    leg2 = estimate_leg(50.0, 500.0, 500.0, calibrated, plan.bike)
    expected = curve_speed_kmh(24.0, climb_density_m_per_100km(50.0, 500.0))
    assert abs(leg2.avg_speed_kmh - round(expected, 1)) < 0.05, "climb-density curve must apply"
    assert leg2.avg_speed_kmh < 24.0, "a leg with ascent must be slower than the flat base"
    assert leg2.model_source == "personal" and leg2.confidence == "high"
    print(f"Curve: base 24.0, flat leg -> {leg_flat.avg_speed_kmh} km/h, "
          f"1000 m/100km leg -> {leg2.avg_speed_kmh} km/h")
    # Synthetic route is flat (all ele 400) -> plan predicts base exactly.
    plan3 = baseline_plan(event, athlete=calibrated)
    assert plan3.summary["model_source"] == "personal", "profile metadata must reach the plan"
    assert abs(plan3.summary["predicted_avg_speed_kmh"] - 24.0) < 0.01, "flat route -> base speed"
    print(f"Profile flow: base 24.0 -> plan predicted {plan3.summary['predicted_avg_speed_kmh']} km/h, "
          f"source={plan3.summary['model_source']}")

    # estimate_route: aggregate over control-to-control legs; worst-leg confidence wins.
    legs = [(100.0, 400.0, 400.0), (100.0, 2000.0, 1800.0)]  # rolling leg + a big-climb leg
    route = estimate_route(legs, calibrated, plan.bike)
    assert abs(route["distance_km"] - 200.0) < 0.01
    per_leg = [e["avg_speed_kmh"] for e in route["legs"]]
    assert per_leg[0] > per_leg[1], "the climbier leg must be the slower one"
    assert route["moving_time_s"] > 0 and route["model_source"] == "personal"
    print(f"estimate_route(200km, 2 legs): {route['moving_time_s']/3600:.1f} h total, "
          f"per-leg {per_leg} km/h, confidence={route['confidence']}")

    # CONTRACT TEST: baseline_plan consumes an injected estimator without knowing its internals.
    # This proves the engine's real curve-first model will drop in cleanly at this seam.
    def mock_estimator(distance_km, ascent_m, descent_m, athlete, bike, conditions=None):
        return LegEstimate(moving_time_s=7200.0, avg_speed_kmh=distance_km / 2.0,
                           confidence="high", model_source="personal")
    mocked = baseline_plan(event, estimator=mock_estimator)
    assert mocked.moving_time_s == 7200.0, "baseline_plan must use the injected estimator's time"
    assert mocked.summary["model_source"] == "personal", "seam metadata must propagate to the plan"
    assert mocked.summary["confidence"] == "high"
    print(f"Contract test (mocked estimator): moving_time={mocked.moving_time_s}s, "
          f"source={mocked.summary['model_source']} → seam wired correctly")

    print("\n✓ All selftest checks passed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Race event data + baseline planning")
    parser.add_argument("input_file", nargs="?", help="JSON file with event/athlete/bike data")
    parser.add_argument("--selftest", action="store_true", help="Run self-tests")
    args = parser.parse_args(argv)

    if args.selftest:
        _selftest()
    else:
        # Read JSON event from file (passed by server.py's run_tool call) or stdin.
        # Expected: {"event": {...}, "athlete": {...}, "bike": {...}}
        try:
            if args.input_file:
                with open(args.input_file, "r") as f:
                    body = json.load(f)
            else:
                body = json.load(sys.stdin)

            event = RaceEvent.from_dict(body.get("event", {}))
            athlete = AthleteInputs.from_dict(body.get("athlete", {})) if "athlete" in body else None
            bike = BikeInputs.from_dict(body.get("bike", {})) if "bike" in body else None

            plan = baseline_plan(event, athlete, bike)
            result = {"ok": True, "plan": plan.to_dict()}
        except Exception as e:
            result = {"ok": False, "error": str(e)}

        print(json.dumps(result))


if __name__ == "__main__":
    main()
