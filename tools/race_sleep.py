#!/usr/bin/env python3
"""Circadian sleep strategy for a brevet/ultra: WHERE and WHEN to sleep, tied to darkness,
moonlight, the body clock and (optionally) the cold - while keeping every cutoff safe.

The idea the benchmark tools miss: on an ultra you should sleep in the hours that are WORST to
ride - deep night, near the circadian low, coldest, and moonless - and ride through the hours
that are easiest. So we build a "riding-difficulty" curve over the ride and put the sleep where
it peaks.

difficulty(t) at the rider's position/time =
    wC * circadian(localtime)         # peaks ~04:30 (window of circadian low), min ~16:30
  + wD * darkness * (1 - 0.6*moonlit) # night is hard to ride; a bright moon UP eases it
  + wX * cold                         # coldest pre-dawn hours (only if weather temps given)

circadian(h) = 0.5*(1 + cos(2*pi*(h - 4.5)/24))   # 1 at 04:30, 0 at 16:30
moonlit      = moon_up ? illumination : 0          # from astro (phase + rise/set)
cold         = clamp((10 - temp_c)/15, 0, 1)       # ramps in below ~10C

For each NIGHT the ride spans we place one window of the suggested per-night duration, centred on
that night's difficulty peak, clamped to the dark period, and only if the later controls' margins
can absorb it. Output maps each window to its km + nearest control, with the reason.

Reuses astro.events (sun + moon per position/day) and geo_util. Pure/offline - it consumes the
timeline (control ETAs + margins) and optional weather that were computed upstream. `--selftest`
runs offline.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import astro
import geo_util

# model weights (circadian + darkness dominate; cold is a modifier when weather is supplied)
W_CIRCADIAN = 0.5
W_DARK = 0.5
W_COLD = 0.2            # added on top, then difficulty is clamped to 1
MOON_RELIEF = 0.6       # a full moon overhead cuts the darkness penalty by up to 60%
CIRCADIAN_NADIR_H = 4.5
SAMPLE_MIN = 20         # ride sampled every 20 min


def _circadian(local_hours: float) -> float:
    return 0.5 * (1.0 + math.cos(2.0 * math.pi * (local_hours - CIRCADIAN_NADIR_H) / 24.0))


def _minutes_of_day(dt: datetime) -> float:
    return dt.hour * 60 + dt.minute + dt.second / 60.0


def _knots(start_dt: datetime, controls: List[Dict[str, Any]]) -> List[Tuple[float, datetime]]:
    """(km, time) knots for interpolation: start at km 0, then each control's arrival."""
    ks = [(0.0, start_dt)]
    for c in controls:
        dt = c.get("arrival_dt")
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if isinstance(dt, datetime):
            ks.append((float(c["distance_km"]), dt))
    ks.sort(key=lambda k: k[1])
    return ks


def _km_at_time(knots: List[Tuple[float, datetime]], t: datetime) -> float:
    if t <= knots[0][1]:
        return knots[0][0]
    if t >= knots[-1][1]:
        return knots[-1][0]
    for i in range(1, len(knots)):
        k0, t0 = knots[i - 1]
        k1, t1 = knots[i]
        if t0 <= t <= t1:
            span = (t1 - t0).total_seconds()
            f = 0.0 if span <= 0 else (t - t0).total_seconds() / span
            return k0 + f * (k1 - k0)
    return knots[-1][0]


def _point_at_km(points: List[Dict[str, Any]], cumul_m: List[float], km: float) -> Tuple[float, float]:
    i = min(range(len(cumul_m)), key=lambda j: abs(cumul_m[j] - km * 1000.0))
    return points[i]["lat"], points[i]["lon"]


class _AstroCache:
    """astro.events is a bit heavy; cache per (calendar-day, ~0.5deg cell)."""
    def __init__(self, tz_offset_h: float):
        self.tz = tz_offset_h
        self._c: Dict[Tuple, Dict[str, Any]] = {}

    def get(self, dt: datetime, lat: float, lon: float) -> Dict[str, Any]:
        key = (dt.date(), round(lat * 2) / 2, round(lon * 2) / 2)
        ev = self._c.get(key)
        if ev is None:
            ev = astro.events(dt.date(), lat, lon, self.tz)
            self._c[key] = ev
        return ev


def _is_dark(ev: Dict[str, Any], mod: float) -> bool:
    sr, ss = ev["sun_min"].get("sunrise"), ev["sun_min"].get("sunset")
    if sr is None or ss is None:
        return False  # polar day (or no event) - treat as light
    return mod < sr or mod > ss


def _moon_up(ev: Dict[str, Any], mod: float) -> bool:
    mr, ms = ev["moon_min"].get("moonrise"), ev["moon_min"].get("moonset")
    if mr is None and ms is None:
        return ev["moon_min"].get("transit") is not None  # up all day
    if mr is not None and ms is not None:
        return (mr <= mod <= ms) if mr <= ms else (mod >= mr or mod <= ms)  # handle wrap
    if mr is not None:
        return mod >= mr
    return mod <= ms


def _temp_at(weather_pts: Optional[List[Dict[str, Any]]], km: float) -> Optional[float]:
    if not weather_pts:
        return None
    best = min(weather_pts, key=lambda w: abs(float(w.get("km", 0)) - km))
    return best.get("temp_c")


def plan_sleep(controls: List[Dict[str, Any]], points: List[Dict[str, Any]],
               start_dt: datetime, tz_offset_h: float = 0.0,
               suggested_total_s: float = 0.0,
               weather_pts: Optional[List[Dict[str, Any]]] = None,
               min_window_s: float = 1800.0,
               min_ride_before_sleep_s: float = 3 * 3600.0,
               min_ride_after_sleep_s: float = 2.5 * 3600.0) -> Dict[str, Any]:
    """Recommend one sleep window per night. `controls` carry distance_km, arrival_dt and margin_s
    (from the timeline). `suggested_total_s` is split across the nights the ride spans."""
    if len(controls) < 1 or len(points) < 2:
        return {"ok": False, "error": "need a route and at least a finish control"}

    knots = _knots(start_dt, controls)
    finish_dt = knots[-1][1]
    if finish_dt <= start_dt:
        return {"ok": False, "error": "finish is not after start"}
    cumul_m = geo_util.cumulative_distances([(p["lat"], p["lon"]) for p in points])
    cache = _AstroCache(tz_offset_h)

    # sample the whole ride: (t, km, lat, lon, difficulty, dark, temp)
    samples = []
    t = start_dt
    step = timedelta(minutes=SAMPLE_MIN)
    while t <= finish_dt:
        km = _km_at_time(knots, t)
        lat, lon = _point_at_km(points, cumul_m, km)
        ev = cache.get(t, lat, lon)
        mod = _minutes_of_day(t)
        dark = _is_dark(ev, mod)
        moonlit = (ev.get("moon_illumination", 0.0) if _moon_up(ev, mod) else 0.0)
        circ = _circadian(t.hour + t.minute / 60.0)
        temp = _temp_at(weather_pts, km)
        cold = 0.0 if temp is None else max(0.0, min(1.0, (10.0 - float(temp)) / 15.0))
        diff = W_CIRCADIAN * circ + W_DARK * (1.0 if dark else 0.0) * (1.0 - MOON_RELIEF * moonlit)
        diff = min(1.0, diff + W_COLD * cold)
        samples.append({"t": t, "km": km, "dark": dark, "diff": diff, "temp": temp,
                        "moon": round(ev.get("moon_illumination", 0.0), 2)})
        t += step

    # group contiguous dark samples into nights
    nights: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    for s in samples:
        if s["dark"]:
            cur.append(s)
        elif cur:
            nights.append(cur); cur = []
    if cur:
        nights.append(cur)
    # Keep nights long enough to sleep in, and drop the pre-dawn sliver at the start (you don't
    # sleep in the first hours of a brevet) - a night must extend past start + min_ride_before.
    ride_floor = start_dt + timedelta(seconds=min_ride_before_sleep_s)
    nights = [n for n in nights
              if (n[-1]["t"] - n[0]["t"]).total_seconds() >= min_window_s
              and n[-1]["t"] >= ride_floor]

    if not nights:
        return {"ok": True, "windows": [],
                "note": "No usable night on this ride - it finishes before real darkness."}

    per_night_s = max(min_window_s, suggested_total_s / len(nights)) if suggested_total_s > 0 \
        else min_window_s

    windows = []
    for idx, night in enumerate(nights):
        peak = max(night, key=lambda s: s["diff"])       # the nadir of this night
        half = timedelta(seconds=per_night_s / 2.0)
        w_start = peak["t"] - half
        w_end = peak["t"] + half
        # clamp inside the dark period
        if w_start < night[0]["t"]:
            w_start, w_end = night[0]["t"], night[0]["t"] + timedelta(seconds=per_night_s)
        if w_end > night[-1]["t"]:
            w_end, w_start = night[-1]["t"], night[-1]["t"] - timedelta(seconds=per_night_s)

        # Don't recommend sleeping right before the finish - you'd just push through. If less than
        # min_ride_after_sleep remains after you'd wake, skip this night (the classic "1h nap 2h
        # from the line" nonsense). This is why a ~24h ride that only meets darkness near the end
        # gets NO sleep suggestion - you ride the one night out.
        if (finish_dt - w_end).total_seconds() < min_ride_after_sleep_s:
            continue

        km = _km_at_time(knots, w_start)
        # tightest margin among controls AFTER the window
        after = [c for c in controls if float(c["distance_km"]) >= km and c.get("margin_s") is not None]
        tightest = min((float(c["margin_s"]) for c in after), default=None)
        cutoff_ok = tightest is None or tightest > per_night_s
        # Location label: name an intermediate control only if one is genuinely near (<=20 km) and
        # it isn't the finish; otherwise just give the km so we never imply "sleep at the finish".
        finish_km = max((float(c["distance_km"]) for c in controls), default=km)
        near_ctrl = None
        cand = min(controls, key=lambda c: abs(float(c["distance_km"]) - km))
        if abs(float(cand["distance_km"]) - km) <= 20.0 and float(cand["distance_km"]) < finish_km - 1.0:
            near_ctrl = cand.get("label")

        temp = _temp_at(weather_pts, km)
        reasons = ["circadian low"]
        if peak["dark"]:
            reasons.append("dark")
        if peak["moon"] >= 0.5:
            reasons.append("bright moon" if peak["moon"] >= 0.8 else "half moon")
        if temp is not None and temp <= 8:
            reasons.append("cold (%d°C)" % round(temp))

        windows.append({
            "night": idx + 1,
            "start_local": w_start.strftime("%H:%M"),
            "end_local": w_end.strftime("%H:%M"),
            "start_dt": w_start.isoformat(),
            "duration_s": round((w_end - w_start).total_seconds()),
            "km": round(km, 1),
            "near_control": near_ctrl,
            "temp_c": None if temp is None else round(float(temp), 1),
            "moon_illumination": peak["moon"],
            "reason": ", ".join(reasons),
            "cutoff_ok": cutoff_ok,
            "tightest_margin_after_s": None if tightest is None else round(tightest),
        })

    total = sum(w["duration_s"] for w in windows)
    return {"ok": True, "windows": windows, "n_nights": len(nights),
            "total_sleep_s": total}


# --- self test (offline) --------------------------------------------------------------------

def _selftest():
    # An ~870 km ride starting 05:00 Fri that runs through two nights AND keeps going past the
    # second dawn (finishes Sun afternoon), so both nights have real riding after them.
    start = datetime(2026, 9, 25, 5, 0)
    pts = [{"lat": 48.0 - i * 0.02, "lon": 2.0, "ele": 100} for i in range(440)]  # ~970 km span
    def at(hours):
        return (start + timedelta(hours=hours)).isoformat()
    controls = [
        {"label": "C1", "distance_km": 150.0, "arrival_dt": at(10), "margin_s": 6 * 3600},
        {"label": "C2", "distance_km": 350.0, "arrival_dt": at(24), "margin_s": 6 * 3600},
        {"label": "C3", "distance_km": 550.0, "arrival_dt": at(40), "margin_s": 6 * 3600},
        {"label": "Finish", "distance_km": 800.0, "arrival_dt": at(58), "margin_s": 6 * 3600},
    ]
    r = plan_sleep(controls, pts, start, tz_offset_h=2.0, suggested_total_s=4 * 3600)
    assert r["ok"], r
    print("=== Circadian sleep plan (synthetic 800k, 2 nights, finishes Sun afternoon) ===")
    print(f"nights={r['n_nights']} total sleep={r['total_sleep_s']/3600:.1f}h")
    for w in r["windows"]:
        print(f"  Night {w['night']}: {w['start_local']}-{w['end_local']} "
              f"(~{w['duration_s']/3600:.1f}h) km {w['km']}"
              f"{' (' + w['near_control'] + ')' if w['near_control'] else ''} "
              f"moon {w['moon_illumination']} · {w['reason']} · cutoff_ok={w['cutoff_ok']}")

    # two nights, both with real riding after them -> both windows kept, in the night.
    assert r["n_nights"] == 2, r["n_nights"]
    assert len(r["windows"]) == 2, [w["start_local"] for w in r["windows"]]
    for w in r["windows"]:
        h = int(w["start_local"][:2])
        assert (h >= 22 or h <= 6), "sleep window should sit in the night, got %s" % w["start_local"]
        assert w["cutoff_ok"] is True
    assert abs(r["total_sleep_s"] - 4 * 3600) < 60, r["total_sleep_s"]

    # near-finish suppression: a ride that only meets darkness near the end gets NO sleep window.
    short_start = datetime(2026, 9, 25, 6, 0)
    short_ctrls = [{"label": "Finish", "distance_km": 360.0,
                    "arrival_dt": (short_start + timedelta(hours=24)).isoformat(), "margin_s": 6 * 3600}]
    rs = plan_sleep(short_ctrls, pts, short_start, tz_offset_h=2.0, suggested_total_s=3600)
    assert rs["windows"] == [], "a 24h dawn-finish ride should suggest no sleep, got %s" % rs["windows"]
    print("Near-finish suppression: 24h dawn-finish ride -> no sleep window (push through). OK")

    # cutoff safety: a tight margin (< per-night sleep) must flag cutoff_ok False.
    tight = [dict(c) for c in controls]
    for c in tight:
        c["margin_s"] = 1800  # 30 min - can't absorb a 2h sleep
    r2 = plan_sleep(tight, pts, start, tz_offset_h=2.0, suggested_total_s=4 * 3600)
    assert any(w["cutoff_ok"] is False for w in r2["windows"]), "tight margins must flag cutoff risk"
    print("Cutoff-risk case correctly flagged.")

    print("\n✓ All race_sleep selftest checks passed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Circadian sleep strategy for a brevet/ultra")
    parser.add_argument("input_file", nargs="?",
                        help="JSON {gpx|points, controls[{distance_km,arrival_dt,margin_s}], "
                             "start_dt, tz, suggested_total_s, weather?}")
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
        start = body.get("start_dt")
        start = datetime.fromisoformat(start) if isinstance(start, str) else start
        r = plan_sleep(body.get("controls") or [], points or [], start,
                       tz_offset_h=float(body.get("tz") or 0.0),
                       suggested_total_s=float(body.get("suggested_total_s") or 0.0),
                       weather_pts=body.get("weather"))
        print(json.dumps(r))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
