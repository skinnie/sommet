#!/usr/bin/env python3
"""Weather + daylight AT EACH CONTROL, stamped with the real arrival time from the race timeline.

This is the race-planner counterpart to the Plan page's weather-along-a-route: same underlying
data (Open-Meteo forecast + sun/moon), but instead of a constant pace it uses the timeline's
actual per-control ETAs, and it answers the brevet questions the Plan page doesn't: "what's the
weather when I actually reach Control 5?" and "which controls do I hit in the dark?".

Reuses the existing machinery rather than duplicating it:
  - weather_route._open_meteo_fetch  (the one online call; injectable for offline tests)
  - weather_route._wind_relation / _bearing / _temp_bucket
  - astro.events                     (per-control sunrise/sunset, so multi-day rides are correct)
  - geo_util                         (GPX parse + cumulative distance)

Input (JSON, file or stdin):
  {"gpx": str | "points": [{lat,lon,ele?}],
   "controls": [{"label", "distance_km", "arrival_dt": local ISO}], "tz": UTC-offset-hours}
Output: {ok, controls:[{label,km,temp_c,feels_c,rain_mm,wind_kmh,wind_rel,is_dark,sunrise,sunset}],
         summary:{temp_min_c,temp_max_c,wind_max_kmh,rain_max_mm,dark_controls}, verdict}

Stdlib only; the forecast is the only online part (like weather_route). `--selftest` is offline.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import astro
import geo_util
import weather_route


def _nearest_idx(cumul_m: List[float], km: float) -> int:
    target = km * 1000.0
    return min(range(len(cumul_m)), key=lambda i: abs(cumul_m[i] - target))


def race_weather(controls: List[Dict[str, Any]], gpx: Optional[str] = None,
                 points: Optional[List[Dict[str, Any]]] = None, tz_offset_h: float = 0.0,
                 fetch=None) -> Dict[str, Any]:
    if points is None and gpx:
        points = geo_util.parse_gpx_points(gpx)
    if not points or len(points) < 2:
        return {"ok": False, "error": "route needs >= 2 points"}
    if not controls:
        return {"ok": False, "error": "no controls"}

    cumul_m = geo_util.cumulative_distances([(p["lat"], p["lon"]) for p in points])

    samples, meta = [], []
    for c in controls:
        km = float(c["distance_km"])
        i = _nearest_idx(cumul_m, km)
        lat, lon = points[i]["lat"], points[i]["lon"]
        j = min(i + 1, len(points) - 1)
        heading = weather_route._bearing(lat, lon, points[j]["lat"], points[j]["lon"])
        arr = c.get("arrival_dt")
        arr_dt = datetime.fromisoformat(arr) if isinstance(arr, str) else arr
        if not isinstance(arr_dt, datetime):
            continue
        samples.append({"lat": lat, "lon": lon, "eta_dt": arr_dt - timedelta(hours=tz_offset_h)})
        meta.append({"label": c.get("label"), "lat": lat, "lon": lon,
                     "heading": heading, "arr_dt": arr_dt, "km": km})

    if not samples:
        return {"ok": False, "error": "no controls with an arrival time"}

    wx = (fetch or weather_route._open_meteo_fetch)(samples)
    if wx is None or len(wx) != len(samples):
        return {"ok": False, "error": "forecast fetch failed"}

    out, dark = [], []
    for i, m in enumerate(meta):
        w = wx[i]
        rel = weather_route._wind_relation(w["wind_dir_deg"], m["heading"])
        ev = astro.events(m["arr_dt"].date(), m["lat"], m["lon"], tz_offset_h)
        srise = ev["sun_min"].get("sunrise")
        sset = ev["sun_min"].get("sunset")
        tod = m["arr_dt"].hour * 60 + m["arr_dt"].minute
        is_dark = bool(srise is not None and sset is not None and (tod < srise or tod > sset))
        if is_dark and m["label"]:
            dark.append(m["label"])
        out.append({
            "label": m["label"], "km": round(m["km"], 1),
            "temp_c": round(w["temp_c"], 1), "feels_c": round(w["feels_c"], 1),
            "rain_mm": round(w["rain_mm"], 2), "wind_kmh": round(w["wind_kmh"], 1),
            "wind_rel": rel, "is_dark": is_dark,
            "sunrise": ev["sun"].get("sunrise"), "sunset": ev["sun"].get("sunset"),
        })

    temps = [c["temp_c"] for c in out]
    summary = {
        "temp_min_c": min(temps), "temp_max_c": max(temps),
        "wind_max_kmh": round(max(c["wind_kmh"] for c in out), 1),
        "rain_max_mm": round(max(c["rain_mm"] for c in out), 2),
        "dark_controls": dark,
    }
    if dark:
        verdict = "In the dark at: " + ", ".join(dark)
    else:
        verdict = "All controls reached in daylight."
    return {"ok": True, "controls": out, "summary": summary, "verdict": verdict}


# --- self test (offline, synthetic forecast) ------------------------------------------------

def _synthetic_fetch(samples):
    # deterministic: cooler + windier the later the ETA, a little rain on the 2nd sample
    out = []
    for k, s in enumerate(samples):
        hour = s["eta_dt"].hour
        out.append({"temp_c": 20.0 - k * 2, "feels_c": 18.0 - k * 2,
                    "rain_mm": 0.4 if k == 1 else 0.0,
                    "wind_kmh": 10.0 + k * 5, "wind_dir_deg": 90.0})
    return out


def _selftest():
    start = datetime(2026, 9, 25, 6, 0)
    # ~120 km roughly west->east line so bearings are ~90deg (east); controls at 40/80/120 km,
    # the last one arriving at 22:30 (after dark) to exercise the daylight flag.
    pts = [{"lat": 45.0, "lon": 3.0 + i * 0.015, "ele": 100} for i in range(80)]
    controls = [
        {"label": "CP1", "distance_km": 40.0, "arrival_dt": (start + timedelta(hours=3)).isoformat()},
        {"label": "CP2", "distance_km": 80.0, "arrival_dt": (start + timedelta(hours=7)).isoformat()},
        {"label": "Finish", "distance_km": 118.0, "arrival_dt": (start + timedelta(hours=16, minutes=30)).isoformat()},
    ]
    r = race_weather(controls, points=pts, tz_offset_h=2.0, fetch=_synthetic_fetch)
    assert r["ok"], r

    print("=== Race weather @ controls (synthetic) ===")
    for c in r["controls"]:
        print(f"  {c['label']:<8} km {c['km']:<6} {c['temp_c']}C (feels {c['feels_c']}) "
              f"wind {c['wind_kmh']} {c['wind_rel']} rain {c['rain_mm']}  "
              f"{'DARK' if c['is_dark'] else 'day'} (sun {c['sunrise']}-{c['sunset']})")
    print("Summary:", r["summary"])
    print("Verdict:", r["verdict"])

    assert len(r["controls"]) == 3
    # wind blowing FROM 90 (east) while heading east (~90) => headwind.
    assert r["controls"][0]["wind_rel"] == "headwind", r["controls"][0]["wind_rel"]
    # CP2 got the rain.
    assert r["controls"][1]["rain_mm"] == 0.4
    # 22:30 finish is after sunset => dark, and it's listed.
    assert r["controls"][2]["is_dark"] is True
    assert "Finish" in r["summary"]["dark_controls"]
    # early controls in daylight.
    assert r["controls"][0]["is_dark"] is False
    print("\n✓ All race_weather selftest checks passed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Weather + daylight at race controls")
    parser.add_argument("input_file", nargs="?", help="JSON {gpx|points, controls, tz}")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        _selftest()
        return

    try:
        body = json.load(open(args.input_file)) if args.input_file else json.load(sys.stdin)
        r = race_weather(body.get("controls") or [], gpx=body.get("gpx"),
                         points=body.get("points"), tz_offset_h=float(body.get("tz") or 0.0))
        print(json.dumps(r))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
