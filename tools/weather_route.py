#!/usr/bin/env python3
"""Weather (and sun/moon) along a planned route - the "what am I walking into?" layer of the
Plan page. Same idea as `track_color.py`, but the value classified along the polyline is the
*forecast at the time you'll actually be there* instead of the climb gradient.

Given a planned track plus a start time and a pace, this:
  1. resamples the track to even steps (one sample roughly every 30 min of travel),
  2. stamps each sample with an ETA (start + distance / pace),
  3. asks a `fetch` callback for the forecast at each point/ETA (the default hits Open-Meteo;
     tests inject a synthetic one so the maths stay offline),
  4. emits the same ready-to-draw shapes the climb colouring uses - coloured polyline
     `segments` (here coloured by temperature) and a `profile` series - plus wind arrows and a
     sun/moon summary with a plain-language verdict ("you'll finish ~30 min after dark").

So the map's `coloredSegments` overlay and the Plan-page profile canvas render weather with the
exact machinery they already render gradient with; only the colour/value source changes.

Stdlib only. `--selftest` proves the maths on a synthetic hill + synthetic forecast, no network.
"""

import argparse
import json
import math
import sys
from datetime import date as _date, datetime, timedelta

import astro
import geo_util

# temperature -> colour, cool->warm, upper bound in degC. Blue/green cold, yellow/orange/red hot,
# a warm-luminance ramp on the hot side so it stays legible for red-green colour blindness.
TEMP_BUCKETS = [
    {"key": "freezing", "label": "< 0 degC",   "upper": 0.0,  "color": "#4575b4"},
    {"key": "cold",     "label": "0-5 degC",   "upper": 5.0,  "color": "#74add1"},
    {"key": "cool",     "label": "5-10 degC",  "upper": 10.0, "color": "#abd9e9"},
    {"key": "mild",     "label": "10-15 degC", "upper": 15.0, "color": "#fee090"},
    {"key": "warm",     "label": "15-20 degC", "upper": 20.0, "color": "#fdae61"},
    {"key": "hot",      "label": "20-25 degC", "upper": 25.0, "color": "#f46d43"},
    {"key": "scorching", "label": ">= 25 degC", "upper": float("inf"), "color": "#d73027"},
]

# wind direction relative to your heading -> label + colour (matches the map/profile arrows)
WIND_REL = {
    "headwind":  {"color": "#d6453f"},
    "crosswind": {"color": "#e0912f"},
    "tailwind":  {"color": "#2e9e6b"},
}


def _temp_bucket(temp_c):
    for b in TEMP_BUCKETS:
        if temp_c < b["upper"]:
            return b
    return TEMP_BUCKETS[-1]


def _bearing(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing (deg, 0=N) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def _wind_relation(wind_from_deg, heading_deg):
    """Classify wind (meteorological 'from' direction) against travel heading -> word.
    Headwind when the wind comes from roughly ahead."""
    diff = abs(((wind_from_deg - heading_deg + 180) % 360) - 180)
    if diff < 60:
        return "headwind"
    if diff > 120:
        return "tailwind"
    return "crosswind"


def _resample(points, dists, step_m):
    """Even-step resample of the polyline (lat/lon/ele/dist), same approach as track_color."""
    total = dists[-1]
    lats, lons, eles, ds = [], [], [], []
    j = 0
    d = 0.0
    while d <= total + 1e-6:
        while j < len(dists) - 2 and dists[j + 1] < d:
            j += 1
        seg = dists[j + 1] - dists[j]
        t = 0.0 if seg <= 0 else max(0.0, min(1.0, (d - dists[j]) / seg))
        a, b = points[j], points[j + 1]
        lats.append(a["lat"] + (b["lat"] - a["lat"]) * t)
        lons.append(a["lon"] + (b["lon"] - a["lon"]) * t)
        if a.get("ele") is None or b.get("ele") is None:
            eles.append(None)
        else:
            eles.append(a["ele"] + (b["ele"] - a["ele"]) * t)
        ds.append(d)
        d += step_m
    last = points[-1]
    if ds and total - ds[-1] > step_m * 0.25:
        lats.append(last["lat"]); lons.append(last["lon"])
        eles.append(last.get("ele")); ds.append(total)
    return lats, lons, eles, ds


def _hhmm(mins):
    m = int(round(mins)) % 1440
    return "%02d:%02d" % (m // 60, m % 60)


def plan(points, start="09:00", date=None, pace_kmh=4.5, tz_offset_h=0.0,
         step_m=None, sport=None, fetch=None):
    """Plan weather along a track. Returns segments (temp-coloured), profile, wind arrows,
    astro + verdict, and a summary. `fetch(samples)` maps [{lat,lon,eta_dt(UTC)}] -> per-sample
    [{temp_c, feels_c, rain_mm, wind_kmh, wind_dir_deg}]; defaults to Open-Meteo."""
    pts = [{"lat": float(p["lat"]), "lon": float(p["lon"]),
            "ele": (None if p.get("ele") in (None, "") else float(p["ele"]))} for p in points]
    if len(pts) < 2:
        return {"ok": False, "error": "a route needs at least 2 points"}

    dists = geo_util.cumulative_distances([(p["lat"], p["lon"]) for p in pts])
    total = dists[-1]
    if total <= 0:
        return {"ok": False, "error": "route has zero length"}

    if step_m is None:
        step_m = min(2000.0, max(200.0, pace_kmh * 1000.0 * 0.5))  # ~30 min of travel, bounded
    lats, lons, eles, ds = _resample(pts, dists, step_m)
    n = len(ds)

    d0 = date or datetime.utcnow().date()
    if isinstance(d0, str):
        d0 = datetime.strptime(d0, "%Y-%m-%d").date()
    sh, sm = (int(x) for x in start.split(":"))
    start_min = sh * 60 + sm
    midnight_utc = datetime(d0.year, d0.month, d0.day) - timedelta(hours=tz_offset_h)

    etas = [start_min + (ds[i] / 1000.0 / pace_kmh) * 60.0 for i in range(n)]
    samples = [{"lat": lats[i], "lon": lons[i],
                "eta_dt": midnight_utc + timedelta(minutes=etas[i])} for i in range(n)]

    wx = (fetch or _open_meteo_fetch)(samples)
    if wx is None or len(wx) != n:
        return {"ok": False, "error": "forecast fetch failed or returned wrong length"}

    # per-sample heading (bearing to the next sample; last inherits the previous)
    headings = []
    for i in range(n):
        a, b = (i, i + 1) if i < n - 1 else (i - 1, i)
        headings.append(_bearing(lats[a], lons[a], lats[b], lons[b]))

    # profile rows + temp-coloured segments (merge consecutive same-bucket samples)
    profile, segments = [], []
    cur = None
    for i in range(n):
        w = wx[i]
        rel = _wind_relation(w["wind_dir_deg"], headings[i])
        bucket = _temp_bucket(w["temp_c"])
        profile.append({
            "dist_m": round(ds[i], 1),
            "eta_min": round(etas[i], 1),
            "eta": _hhmm(etas[i]),
            "ele_m": None if eles[i] is None else round(eles[i], 1),
            "temp_c": round(w["temp_c"], 1),
            "feels_c": round(w["feels_c"], 1),
            "rain_mm": round(w["rain_mm"], 2),
            "wind_kmh": round(w["wind_kmh"], 1),
            "wind_rel": rel,
            "color": bucket["color"],
        })
        if cur is None or cur["key"] != bucket["key"]:
            if cur is not None:
                segments.append({"bucket": cur["key"], "color": cur["color"], "coords": cur["coords"]})
            cur = {"key": bucket["key"], "color": bucket["color"], "coords": []}
        cur["coords"].append([round(lats[i], 6), round(lons[i], 6)])
    if cur is not None:
        segments.append({"bucket": cur["key"], "color": cur["color"], "coords": cur["coords"]})

    # wind arrows: one per sample, coloured by relation, sized by speed (caller scales)
    wind_arrows = [{
        "lat": round(lats[i], 6), "lon": round(lons[i], 6),
        "wind_kmh": round(wx[i]["wind_kmh"], 1),
        "wind_dir_deg": round(wx[i]["wind_dir_deg"], 1),
        "heading_deg": round(headings[i], 1),
        "rel": profile[i]["wind_rel"],
        "color": WIND_REL[profile[i]["wind_rel"]]["color"],
    } for i in range(n)]

    # rain markers: a raindrop on the map wherever meaningful rain is forecast (>= 0.1 mm),
    # so the Plan map can show rain icons over the climb-coloured route (André, 2026-08-31).
    rain_marks = [{
        "lat": round(lats[i], 6), "lon": round(lons[i], 6),
        "rain_mm": round(wx[i]["rain_mm"], 2),
    } for i in range(n) if wx[i]["rain_mm"] >= 0.1]

    # temperature markers: °C / feels-like along the route, for the map's "climb + temperature"
    # overlay (André, 2026-08-31). Thinned to at most ~8 evenly-spaced points so labels don't
    # pile up on a long route.
    _tstep = max(1, n // 8)
    temp_marks = [{
        "lat": round(lats[i], 6), "lon": round(lons[i], 6),
        "temp_c": round(wx[i]["temp_c"], 1), "feels_c": round(wx[i]["feels_c"], 1),
    } for i in range(0, n, _tstep)]

    # sun/moon at the route midpoint, on the start date
    mid = n // 2
    ev = astro.events(d0, lats[mid], lons[mid], tz_offset_h)
    verdict = _verdict(etas, ds, pace_kmh, start_min, ev)

    summary = {
        "distance_m": round(total, 1),
        "start": start,
        "finish": _hhmm(etas[-1]),
        "finish_min": round(etas[-1], 1),
        "pace_kmh": pace_kmh,
        "temp_min_c": round(min(w["temp_c"] for w in wx), 1),
        "temp_max_c": round(max(w["temp_c"] for w in wx), 1),
        "rain_max_mm": round(max(w["rain_mm"] for w in wx), 2),
        "wind_max_kmh": round(max(w["wind_kmh"] for w in wx), 1),
    }
    return {"ok": True, "segments": segments, "profile": profile, "wind_arrows": wind_arrows,
            "rain_marks": rain_marks, "temp_marks": temp_marks,
            "astro": ev, "verdict": verdict, "summary": summary,
            "legend": [{"key": b["key"], "label": b["label"], "color": b["color"]}
                       for b in TEMP_BUCKETS]}


def _verdict(etas, ds, pace_kmh, start_min, ev):
    """Plain-language finish-vs-daylight verdict, plus the km where daylight runs out."""
    finish = etas[-1]
    sunset = ev["sun_min"].get("sunset")
    dusk = ev["sun_min"].get("civil_dusk")
    moon = ""
    if ev.get("moon_illumination") is not None:
        pct = int(round(ev["moon_illumination"] * 100))
        moon = " A %d%% %s rises %s." % (pct, ev.get("moon_phase", "moon"),
                                         ev["moon"].get("moonrise") or "--")
    if sunset is None:
        return {"state": "ok", "headline": "Finish %s." % _hhmm(finish),
                "detail": "No sunset today at this latitude.", "dark_km": None}
    if finish > sunset:
        after = int(round(finish - sunset))
        dark_km = (sunset - start_min) / 60.0 * pace_kmh  # km reached at sunset
        detail = "Daylight runs out"
        if 0 < dark_km < ds[-1] / 1000.0:
            detail += " around km %.1f." % dark_km
        else:
            detail = "Civil dusk %s." % (ev["sun"].get("civil_dusk") or "--")
            dark_km = None
        return {"state": "critical" if (dusk and finish > dusk) else "warn",
                "headline": "You'll finish ~%d min after dark (%s) - bring a headlamp."
                            % (after, _hhmm(finish)),
                "detail": detail + moon, "dark_km": None if dark_km is None else round(dark_km, 1)}
    spare = int(round(sunset - finish))
    return {"state": "ok",
            "headline": "You'll finish %s - about %d min of daylight to spare." % (_hhmm(finish), spare),
            "detail": "Sunset %s.%s" % (ev["sun"].get("sunset") or "--", moon), "dark_km": None}


def _open_meteo_fetch(samples):
    """Default fetch: one Open-Meteo call for all sampled points, picking each point's forecast
    at the hour nearest its ETA. Needs network - the only online part of this module."""
    import urllib.parse
    import urllib.request

    if not samples:
        return []
    lats = ",".join("%.4f" % s["lat"] for s in samples)
    lons = ",".join("%.4f" % s["lon"] for s in samples)
    dts = [s["eta_dt"] for s in samples]
    start_d = min(dts).date().isoformat()
    end_d = max(dts).date().isoformat()
    q = urllib.parse.urlencode({
        "latitude": lats, "longitude": lons,
        "hourly": "temperature_2m,apparent_temperature,precipitation,wind_speed_10m,wind_direction_10m,weather_code",
        "timezone": "UTC", "start_date": start_d, "end_date": end_d,
    })
    url = "https://api.open-meteo.com/v1/forecast?" + q
    with urllib.request.urlopen(url, timeout=20) as resp:
        doc = json.loads(resp.read().decode("utf-8"))
    locs = doc if isinstance(doc, list) else [doc]  # multi-point returns a list
    out = []
    for i, s in enumerate(samples):
        loc = locs[i] if i < len(locs) else locs[-1]
        h = loc["hourly"]
        times = [datetime.strptime(t, "%Y-%m-%dT%H:%M") for t in h["time"]]
        eta = s["eta_dt"].replace(tzinfo=None)
        k = min(range(len(times)), key=lambda j: abs((times[j] - eta).total_seconds()))
        out.append({
            "temp_c": h["temperature_2m"][k],
            "feels_c": h["apparent_temperature"][k],
            "rain_mm": h["precipitation"][k] or 0.0,
            "wind_kmh": h["wind_speed_10m"][k],
            "wind_dir_deg": h["wind_direction_10m"][k],
        })
    return out


# --- self-test -------------------------------------------------------------------------

def _synthetic_route():
    """A ~6 km line climbing 600 m then dropping - enough for several samples with varied ele."""
    lat0, lon0 = 40.30, -7.60
    _, mpl = geo_util.meters_per_degree(lat0)
    pts = []
    for i in range(61):  # 100 m spacing over 6 km
        east = i * 100.0
        ele = 800 + (east * 0.10 if east < 3000 else 300 - (east - 3000) * 0.10)
        pts.append({"lat": lat0, "lon": lon0 + east / mpl, "ele": round(ele)})
    return pts


def _synthetic_fetch(samples):
    """Deterministic forecast: cooler as the route climbs (proxy via index), a rain bump in the
    middle, wind from the west (270) so the eastbound outbound leg is a headwind."""
    n = len(samples)
    out = []
    for i, s in enumerate(samples):
        frac = i / max(1, n - 1)
        temp = 16 - 8 * math.sin(frac * math.pi)          # dips mid-route (up high)
        rain = max(0.0, 2.4 * math.sin(frac * math.pi) - 0.6)
        wind = 10 + 14 * frac
        out.append({"temp_c": temp, "feels_c": temp - min(6, wind / 4.0),
                    "rain_mm": rain, "wind_kmh": wind, "wind_dir_deg": 270.0})
    return out


def _selftest():
    r = plan(_synthetic_route(), start="15:30", date=_date(2026, 8, 29), pace_kmh=3.5,
             tz_offset_h=1.0, fetch=_synthetic_fetch)
    assert r["ok"], r
    assert 5900 <= r["summary"]["distance_m"] <= 6100, r["summary"]
    # profile has weather on every row and a monotone ETA
    etas = [p["eta_min"] for p in r["profile"]]
    assert etas == sorted(etas), "ETA must increase along the route"
    assert all("temp_c" in p and "rain_mm" in p and "wind_kmh" in p for p in r["profile"])
    # temperature-coloured segments cover the route and colours come from the bucket table
    palette = {b["color"] for b in TEMP_BUCKETS}
    assert r["segments"] and all(s["color"] in palette for s in r["segments"]), r["segments"]
    # wind relation maths: heading east (90), wind FROM east=head, west=tail, north=cross
    assert _wind_relation(90, 90) == "headwind"
    assert _wind_relation(270, 90) == "tailwind"
    assert _wind_relation(0, 90) == "crosswind"
    # eastbound route + wind from the west (270) -> a tailwind the whole way
    rels = {a["rel"] for a in r["wind_arrows"]}
    assert "tailwind" in rels, rels
    # this slow 6 km from 15:30 finishes before dark here; a very slow pace should flip to "dark"
    slow = plan(_synthetic_route(), start="18:30", date=_date(2026, 8, 29), pace_kmh=1.2,
                tz_offset_h=1.0, fetch=_synthetic_fetch)
    assert slow["verdict"]["state"] in ("warn", "critical"), slow["verdict"]
    assert "headlamp" in slow["verdict"]["headline"], slow["verdict"]
    print("weather_route selftest OK:", json.dumps({
        "segments": len(r["segments"]), "samples": len(r["profile"]),
        "verdict": r["verdict"]["state"], "finish": r["summary"]["finish"]}))
    print(json.dumps({"ok": True, "selftest": "passed"}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Weather + sun/moon along a route (offline maths).")
    ap.add_argument("track", nargs="?", help="GPX file or JSON [{lat,lon,ele?}] / {points:[...]}")
    ap.add_argument("--start", default="09:00", help="local start time HH:MM")
    ap.add_argument("--date", help="local start date YYYY-MM-DD (default today)")
    ap.add_argument("--pace", type=float, default=4.5, help="pace in km/h")
    ap.add_argument("--tz", type=float, default=0.0, help="UTC offset hours")
    ap.add_argument("--reverse", action="store_true", help="reverse the route direction")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.track:
        ap.error("a track file is required (or use --selftest)")
    raw = open(args.track, "r", encoding="utf-8", errors="replace").read()
    if args.track.lower().endswith(".gpx") or raw.lstrip().startswith("<"):
        points = geo_util.parse_gpx_points(raw)
    else:
        doc = json.loads(raw)
        points = doc["points"] if isinstance(doc, dict) else doc
    if not points:
        print(json.dumps({"ok": False, "error": "no points in track"}))
        return 2
    if args.reverse:
        points = list(reversed(points))
    d = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    result = plan(points, start=args.start, date=d, pace_kmh=args.pace, tz_offset_h=args.tz)
    print(json.dumps(result))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
