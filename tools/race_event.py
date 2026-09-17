#!/usr/bin/env python3
"""Race event and race plan data models + naive baseline ETA calculation.

This is the foundation for the BRM/ultra-distance race planner. It defines:
  - RaceEvent: name, date, route, cutoffs
  - RacePlan: the event + athlete/bike inputs + provisional baseline output (distance/ETA/moving time)
  - AthleteInputs: weight, FTP, RMR (required, optional, optional)
  - BikeInputs: bike weight, load weight, type, aero category

The baseline_plan() function does pure distance/time math (constant speed), with a stub
estimate_leg() interface for the future performance model (BRouter-physics-based ETA).

No network, no geometry -- just JSON round-tripping + arithmetic on distances/times.
Stdlib only. `--selftest` proves the maths on a synthetic event.
"""

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

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
    cutoff_dt: Optional[datetime] = None  # ISO string or datetime; set by baseline_plan if not given

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "distance_km": self.distance_km,
            "cutoff_dt": self.cutoff_dt.isoformat() if isinstance(self.cutoff_dt, datetime) else self.cutoff_dt,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Cutoff":
        cutoff_dt = d.get("cutoff_dt")
        if isinstance(cutoff_dt, str):
            cutoff_dt = datetime.fromisoformat(cutoff_dt)
        return Cutoff(
            label=d["label"],
            distance_km=d["distance_km"],
            cutoff_dt=cutoff_dt,
        )


@dataclass
class AthleteInputs:
    """User-provided athlete physical parameters."""
    weight_kg: float
    ftp_w: Optional[float] = None  # threshold power; optional
    rmr_kcal_day: Optional[float] = None  # resting metabolic rate; optional

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


def baseline_plan(event: RaceEvent, athlete: Optional[AthleteInputs] = None,
                  bike: Optional[BikeInputs] = None) -> RacePlan:
    """Compute a baseline race plan: naive distance/time math (constant speed).

    This is the provisional calculation for the foundation phase:
    - Extract route distance from event.points or event.gpx.
    - Derive required average speed from event.target_finish_dt or the tightest cutoff.
    - Compute finish ETA at that speed.
    - Mark as provisional=True so the UI knows a real performance model will replace this.

    athlete/bike default to sensible minimums if not provided.
    """
    if athlete is None:
        athlete = AthleteInputs(weight_kg=75.0)  # sensible default
    if bike is None:
        bike = BikeInputs(bike_weight_kg=10.0, load_weight_kg=5.0)  # sensible touring defaults

    # Extract route distance.
    distance_m = 0.0
    if event.points:
        cumul, total = _cumulative_distances(event.points)
        distance_m = total
    elif event.gpx:
        # Parse GPX if provided (backend writes GPX to a temp file; this script reads it)
        try:
            import geo_util
            points = geo_util.parse_gpx_points(event.gpx)
            if points:
                cumul, total = _cumulative_distances(points)
                distance_m = total
        except Exception:
            pass  # Fall through to error message below

    if distance_m == 0.0:
        return RacePlan(
            event=event, athlete=athlete, bike=bike,
            provisional=True,
            summary={"error": "route has no distance (no GPX or points)"},
        )

    # Derive required average speed from either target_finish_dt or the tightest cutoff.
    required_avg_kmh = 0.0
    finish_dt = None

    if event.target_finish_dt:
        elapsed = event.target_finish_dt - event.start_dt
        elapsed_hours = elapsed.total_seconds() / 3600.0
        if elapsed_hours > 0:
            required_avg_kmh = (distance_m / 1000.0) / elapsed_hours
            finish_dt = event.target_finish_dt
    elif event.cutoffs:
        # Use the tightest (earliest) cutoff as the required finish time.
        # This is a simplification; a real implementation might offer multiple strategies.
        tightest = min(event.cutoffs, key=lambda c: c.distance_km if c.distance_km == distance_m / 1000.0 else float("inf"))
        if tightest.cutoff_dt:
            elapsed = tightest.cutoff_dt - event.start_dt
            elapsed_hours = elapsed.total_seconds() / 3600.0
            if elapsed_hours > 0:
                required_avg_kmh = (distance_m / 1000.0) / elapsed_hours
                finish_dt = event.start_dt + timedelta(hours=elapsed_hours)

    # If no target or cutoff, assume a conservative 15 km/h touring pace.
    if required_avg_kmh == 0.0:
        required_avg_kmh = 15.0

    moving_time_s = (distance_m / 1000.0) / required_avg_kmh * 3600.0
    if finish_dt is None:
        finish_dt = event.start_dt + timedelta(seconds=moving_time_s)

    summary = {
        "distance_km": round(distance_m / 1000.0, 2),
        "required_avg_speed_kmh": round(required_avg_kmh, 1),
        "moving_time_hours": round(moving_time_s / 3600.0, 1),
        "finish_time": finish_dt.strftime("%H:%M") if finish_dt else None,
    }

    return RacePlan(
        event=event,
        athlete=athlete,
        bike=bike,
        provisional=True,
        distance_m=distance_m,
        finish_eta_dt=finish_dt,
        moving_time_s=moving_time_s,
        required_avg_speed_kmh=required_avg_kmh,
        summary=summary,
    )


def estimate_leg(distance_m: float, elevation_gain_m: float, elevation_loss_m: float,
                 surface: str, athlete: AthleteInputs, bike: BikeInputs,
                 weather: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Estimate time/energy for a route leg using the performance model.

    STUB (to be implemented with BRouter-physics later): currently just returns naive
    distance/15kmh constant speed. The signature and return shape are fixed so the future
    physics model can replace this without changing callers.

    Args:
        distance_m: horizontal distance (metres)
        elevation_gain_m: climbing (metres)
        elevation_loss_m: descent (metres)
        surface: "asphalt", "gravel", "dirt", etc. (for rolling resistance scaling)
        athlete: AthleteInputs (weight, FTP, RMR)
        bike: BikeInputs (weight, load, type, aero)
        weather: optional {"wind_kmh", "wind_dir_deg", "temp_c", ...}

    Returns:
        {
            "duration_s": float,
            "energy_wh": float (optional, when FTP given),
            "speed_kmh": float (average),
            "notes": str (for "provisional"/interim status),
        }
    """
    # Naive baseline: 15 km/h constant speed, ignore elevation/weather/physics.
    # Mark as clearly provisional.
    speed_kmh = 15.0
    duration_s = (distance_m / 1000.0) / speed_kmh * 3600.0

    return {
        "duration_s": round(duration_s, 1),
        "speed_kmh": round(speed_kmh, 1),
        "energy_wh": None,  # Would compute from FTP if present, in the real model.
        "notes": "PROVISIONAL: naive 15 km/h baseline, no physics/weather yet",
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
    """Prove the baseline math on a synthetic 200k BRM."""
    event = _synthetic_event()
    plan = baseline_plan(event)

    print("=== Synthetic 200k BRM ===")
    print(f"Distance: {plan.summary.get('distance_km')} km")
    print(f"Required avg speed: {plan.summary.get('required_avg_speed_kmh')} km/h")
    print(f"Moving time: {plan.summary.get('moving_time_hours')} hours")
    print(f"Finish time: {plan.summary.get('finish_time')}")
    print(f"Provisional: {plan.provisional}")

    # Sanity checks:
    assert plan.distance_m > 100_000, f"Expected >100km, got {plan.distance_m}m"
    assert plan.required_avg_speed_kmh > 10, f"Expected >10 km/h, got {plan.required_avg_speed_kmh}"
    assert plan.finish_eta_dt is not None
    assert plan.provisional is True

    # Test estimate_leg stub:
    leg = estimate_leg(50_000, 500, 500, "asphalt", plan.athlete, plan.bike)
    print(f"\nEstimate leg (50km, 500m gain): {leg}")
    assert leg["duration_s"] > 0
    assert "PROVISIONAL" in leg["notes"]

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
