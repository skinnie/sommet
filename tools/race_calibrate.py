#!/usr/bin/env python3
"""Calibrate a rider's base_speed from ONE ride file (a FIT), so the race planner can predict the
rider instead of asking them to guess a number. Cold-start tier 2 from the design: back-solve the
curve's base_speed from a real long ride.

Reuses race_calibration.ride_summary_from_fit (via tools/fit_decode) + calibrate_from_single_ride.

Input (JSON, file or stdin): {"fit_path": "/abs/path/to/ride.fit"}
Output: {ok, profile:{base_speed_kmh,confidence,model_source,n_recent_rides}, ride:{distance_km,
  ascent_m,moving_time_s}}  (profile is what goes on athlete.speed_profile)

Stdlib only. `--selftest` runs offline against a synthetic ride dict.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

import race_calibration


def calibrate_fit(fit_path: str) -> Dict[str, Any]:
    ride = race_calibration.ride_summary_from_fit(fit_path)
    if not ride:
        return {"ok": False, "error": "could not read distance/moving-time from that FIT"}
    prof = race_calibration.calibrate_from_single_ride(ride)
    if not prof:
        return {"ok": False, "error": "ride too short/steep to place you on the curve"}
    return {"ok": True, "profile": prof,
            "ride": {"distance_km": round(ride.get("distance_km", 0), 1),
                     "ascent_m": round(ride.get("ascent_m", 0)),
                     "moving_time_s": round(ride.get("moving_time_s", 0))}}


def _selftest():
    # exercise the calibration math directly (no FIT needed): a 200 km / 1000 m / 8 h ride.
    ride = {"distance_km": 200.0, "ascent_m": 1000.0, "moving_time_s": 8 * 3600,
            "elapsed_time_s": 9 * 3600, "sport": "cycling"}
    prof = race_calibration.calibrate_from_single_ride(ride)
    print("ride 200km/1000m/8h ->", prof)
    assert prof and prof["base_speed_kmh"] > 20, prof
    assert prof["model_source"] == "personal" and prof["confidence"] == "low"
    # moving speed 25 km/h at ~500 m/100km climb density -> base a bit above 25.
    assert 25 <= prof["base_speed_kmh"] <= 30, prof["base_speed_kmh"]
    print("✓ race_calibrate selftest passed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Calibrate base_speed from one FIT ride")
    parser.add_argument("input_file", nargs="?", help='JSON {"fit_path": "..."}')
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        _selftest()
        return
    try:
        body = json.load(open(args.input_file)) if args.input_file else json.load(sys.stdin)
        path = body.get("fit_path")
        if not path:
            print(json.dumps({"ok": False, "error": '"fit_path" is required'})); return
        print(json.dumps(calibrate_fit(path)))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
