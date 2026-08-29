#!/usr/bin/env python3
"""Sun and moon events for a place and day - sunrise/sunset, the three twilights, solar noon,
moonrise/moonset/transit, and moon phase/illumination/age.

This is the "will I finish in the light?" maths behind the route-weather planner: given where
the route is and when you set off, it turns raw astronomy into the times the planner needs and
the verdict layer explains in plain language ("you'll finish ~30 min after dark").

Method: low-precision but well-tested closed-form solar/lunar positions (Paul Schlyter's
formulae, with the main lunar perturbation terms so moonrise is good to a few minutes), then a
simple altitude *sampling* pass over the local day to pull rise/set/twilight crossings and the
transits. Sampling instead of the closed-form rise equation keeps sun and moon on the exact
same code path and copes with the awkward cases (a body that never rises or never sets) by just
reporting None rather than blowing up.

Stdlib only, fully offline and hardware-free - `--selftest` proves the maths with no network.

    ./tools/astro.py --date 2026-08-29 --lat 40.32 --lon -7.61 --tz 1   # -> JSON
    ./tools/astro.py --selftest
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta

DEG = math.pi / 180.0

# altitude of a body's centre at the moment it "rises"/"sets", degrees. The sun/stars use the
# standard -0.833 (34' refraction + 16' semidiameter). The moon is close to the horizon-parallax
# cancelling most of that, so its centre is ~ +0.125 at rise/set.
H_SUN = -0.833
H_CIVIL = -6.0
H_NAUTICAL = -12.0
H_ASTRO = -18.0
H_MOON = 0.125

SYNODIC_MONTH = 29.530588853  # days, new moon to new moon


def _rev(x):
    return x % 360.0


def _daynum(dt):
    """Schlyter day number: days since 2000-01-00 00:00 UT (dt is naive UTC)."""
    y, mo, d = dt.year, dt.month, dt.day
    n = 367 * y - (7 * (y + ((mo + 9) // 12))) // 4 + (275 * mo) // 9 + d - 730530
    return n + (dt.hour + dt.minute / 60.0 + dt.second / 3600.0) / 24.0


def _sun(d):
    """Sun ecliptic longitude + equatorial RA/Dec (deg) and the mean longitude L (for sidereal
    time), at day number d."""
    w = 282.9404 + 4.70935e-5 * d
    e = 0.016709 - 1.151e-9 * d
    M = _rev(356.0470 + 0.9856002585 * d)
    obl = 23.4393 - 3.563e-7 * d
    L = _rev(w + M)
    Mr = M * DEG
    E = M + (180.0 / math.pi) * e * math.sin(Mr) * (1 + e * math.cos(Mr))
    Er = E * DEG
    xv = math.cos(Er) - e
    yv = math.sqrt(1 - e * e) * math.sin(Er)
    v = _rev(math.degrees(math.atan2(yv, xv)))
    lon = _rev(v + w)
    xs, ys = math.cos(lon * DEG), math.sin(lon * DEG)
    xe = xs
    ye = ys * math.cos(obl * DEG)
    ze = ys * math.sin(obl * DEG)
    ra = _rev(math.degrees(math.atan2(ye, xe)))
    dec = math.degrees(math.atan2(ze, math.hypot(xe, ye)))
    return {"L": L, "lon": lon, "ra": ra, "dec": dec, "obl": obl, "Msun": M}


def _moon(d, sun):
    """Moon ecliptic lon/lat + equatorial RA/Dec (deg) at day number d, including the main
    perturbation terms so it's good to a few arcminutes (a few minutes of rise/set time)."""
    N = _rev(125.1228 - 0.0529538083 * d)
    inc = 5.1454
    w = _rev(318.0634 + 0.1643573223 * d)
    e = 0.054900
    M = _rev(115.3654 + 13.0649929509 * d)
    obl = sun["obl"]

    E = M + (180.0 / math.pi) * e * math.sin(M * DEG) * (1 + e * math.cos(M * DEG))
    for _ in range(6):
        E = E - (E - (180.0 / math.pi) * e * math.sin(E * DEG) - M) / (1 - e * math.cos(E * DEG))
    Er = E * DEG
    xv = math.cos(Er) - e
    yv = math.sqrt(1 - e * e) * math.sin(Er)
    v = _rev(math.degrees(math.atan2(yv, xv)))
    r = math.hypot(xv, yv)  # in Earth radii units (a factored out)

    # position in the ecliptic (unit-scaled r; distance not needed for angles)
    vw = (v + w) * DEG
    xh = r * (math.cos(N * DEG) * math.cos(vw) - math.sin(N * DEG) * math.sin(vw) * math.cos(inc * DEG))
    yh = r * (math.sin(N * DEG) * math.cos(vw) + math.cos(N * DEG) * math.sin(vw) * math.cos(inc * DEG))
    zh = r * (math.sin(vw) * math.sin(inc * DEG))
    lon = _rev(math.degrees(math.atan2(yh, xh)))
    lat = math.degrees(math.atan2(zh, math.hypot(xh, yh)))

    # perturbations (Schlyter): need mean elongation D and argument of latitude F
    Ls = sun["L"]
    Ms = sun["Msun"]
    Lm = _rev(N + w + M)
    Dm = _rev(Lm - Ls)
    F = _rev(Lm - N)
    lon += (-1.274 * math.sin((M - 2 * Dm) * DEG)
            + 0.658 * math.sin((2 * Dm) * DEG)
            - 0.186 * math.sin((Ms) * DEG)
            - 0.059 * math.sin((2 * M - 2 * Dm) * DEG)
            - 0.057 * math.sin((M - 2 * Dm + Ms) * DEG)
            + 0.053 * math.sin((M + 2 * Dm) * DEG)
            + 0.046 * math.sin((2 * Dm - Ms) * DEG)
            + 0.041 * math.sin((M - Ms) * DEG)
            - 0.035 * math.sin((Dm) * DEG)
            - 0.031 * math.sin((M + Ms) * DEG)
            - 0.015 * math.sin((2 * F - 2 * Dm) * DEG)
            + 0.011 * math.sin((M - 4 * Dm) * DEG))
    lat += (-0.173 * math.sin((F - 2 * Dm) * DEG)
            - 0.055 * math.sin((M - F - 2 * Dm) * DEG)
            - 0.046 * math.sin((M + F - 2 * Dm) * DEG)
            + 0.033 * math.sin((F + 2 * Dm) * DEG)
            + 0.017 * math.sin((2 * M + F) * DEG))
    lon = _rev(lon)

    # ecliptic -> equatorial
    xg = math.cos(lon * DEG) * math.cos(lat * DEG)
    yg = math.sin(lon * DEG) * math.cos(lat * DEG)
    zg = math.sin(lat * DEG)
    xe = xg
    ye = yg * math.cos(obl * DEG) - zg * math.sin(obl * DEG)
    ze = yg * math.sin(obl * DEG) + zg * math.cos(obl * DEG)
    ra = _rev(math.degrees(math.atan2(ye, xe)))
    dec = math.degrees(math.atan2(ze, math.hypot(xe, ye)))
    return {"lon": lon, "lat": lat, "ra": ra, "dec": dec}


def _altitude(ra, dec, dt, lat, lon, sunL):
    """Geocentric altitude (deg) of a body of given RA/Dec, at naive-UTC dt, seen from lat/lon."""
    ut = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    gmst0 = _rev(sunL + 180.0) / 15.0            # hours
    lst = (gmst0 + ut + lon / 15.0) * 15.0       # degrees
    ha = _rev(lst - ra)
    alt = math.degrees(math.asin(
        math.sin(lat * DEG) * math.sin(dec * DEG)
        + math.cos(lat * DEG) * math.cos(dec * DEG) * math.cos(ha * DEG)))
    return alt


def _sample_day(date, lat, lon, tz_h, step_min=4):
    """Sample sun & moon altitude across the local day. Returns parallel lists:
    minutes-of-local-day, sun_alt, moon_alt, plus sun/moon ecliptic longitude at local noon."""
    local_midnight_utc = datetime(date.year, date.month, date.day) - timedelta(hours=tz_h)
    mins, sun_alt, moon_alt = [], [], []
    t = 0
    while t <= 1440:
        dt = local_midnight_utc + timedelta(minutes=t)
        d = _daynum(dt)
        s = _sun(d)
        m = _moon(d, s)
        mins.append(t)
        sun_alt.append(_altitude(s["ra"], s["dec"], dt, lat, lon, s["L"]))
        moon_alt.append(_altitude(m["ra"], m["dec"], dt, lat, lon, s["L"]))
        t += step_min
    return mins, sun_alt, moon_alt


def _crossings(mins, alts, h):
    """(minute, direction) for every crossing of altitude threshold h. direction 'up'/'down'."""
    out = []
    for i in range(1, len(alts)):
        a, b = alts[i - 1] - h, alts[i] - h
        if a == 0.0:
            out.append((mins[i - 1], "up" if alts[i] > alts[i - 1] else "down"))
        elif (a < 0) != (b < 0):
            frac = a / (a - b)
            mn = mins[i - 1] + frac * (mins[i] - mins[i - 1])
            out.append((mn, "up" if b > a else "down"))
    return out


def _first(crossings, direction):
    for mn, dr in crossings:
        if dr == direction:
            return mn
    return None


def _hhmm(mn):
    if mn is None:
        return None
    mn = int(round(mn)) % 1440
    return "%02d:%02d" % (mn // 60, mn % 60)


def _phase_name(elong):
    """Waxing/waning phase name from sun->moon elongation in degrees (0 new, 180 full)."""
    e = _rev(elong)
    names = [(22.5, "new moon"), (67.5, "waxing crescent"), (112.5, "first quarter"),
             (157.5, "waxing gibbous"), (202.5, "full moon"), (247.5, "waning gibbous"),
             (292.5, "last quarter"), (337.5, "waning crescent")]
    for upper, name in names:
        if e < upper:
            return name
    return "new moon"


def events(date, lat, lon, tz_h):
    """All sun/moon events for a local calendar `date` (a date/datetime) at lat/lon and UTC
    offset tz_h (hours, may be fractional). Times are "HH:MM" local, plus *_min minutes-of-day
    for computation. None where the event doesn't occur that day (e.g. polar day)."""
    mins, sun_alt, moon_alt = _sample_day(date, lat, lon, tz_h)

    sun_x = {h: _crossings(mins, sun_alt, h) for h in (H_SUN, H_CIVIL, H_NAUTICAL, H_ASTRO)}
    noon_i = max(range(len(sun_alt)), key=lambda i: sun_alt[i])
    moon_x = _crossings(mins, moon_alt, H_MOON)
    transit_i = max(range(len(moon_alt)), key=lambda i: moon_alt[i])

    # moon phase/illumination/age at local noon
    noon_utc = datetime(date.year, date.month, date.day, 12) - timedelta(hours=tz_h)
    s = _sun(_daynum(noon_utc))
    m = _moon(_daynum(noon_utc), s)
    elong = _rev(m["lon"] - s["lon"])
    illum = (1 - math.cos(elong * DEG)) / 2.0
    age = SYNODIC_MONTH * elong / 360.0

    sun_min = {
        "astronomical_dawn": _first(sun_x[H_ASTRO], "up"),
        "nautical_dawn": _first(sun_x[H_NAUTICAL], "up"),
        "civil_dawn": _first(sun_x[H_CIVIL], "up"),
        "sunrise": _first(sun_x[H_SUN], "up"),
        "solar_noon": float(mins[noon_i]),
        "sunset": _first(sun_x[H_SUN], "down"),
        "civil_dusk": _first(sun_x[H_CIVIL], "down"),
        "nautical_dusk": _first(sun_x[H_NAUTICAL], "down"),
        "astronomical_dusk": _first(sun_x[H_ASTRO], "down"),
    }
    moon_min = {
        "moonrise": _first(moon_x, "up"),
        "moonset": _first(moon_x, "down"),
        "transit": float(mins[transit_i]) if moon_alt[transit_i] > H_MOON else None,
    }
    return {
        "sun": {k: _hhmm(v) for k, v in sun_min.items()},
        "sun_min": sun_min,
        "moon": {k: _hhmm(v) for k, v in moon_min.items()},
        "moon_min": moon_min,
        "moon_phase": _phase_name(elong),
        "moon_illumination": round(illum, 3),
        "moon_age_days": round(age, 1),
    }


# --- self-test -------------------------------------------------------------------------

def _selftest():
    # Serra da Estrela, Portugal, late summer, UTC+1
    from datetime import date as _date
    r = events(_date(2026, 8, 29), 40.32, -7.61, 1.0)
    sm = r["sun_min"]
    # ordering of the day must hold
    order = ["astronomical_dawn", "nautical_dawn", "civil_dawn", "sunrise", "solar_noon",
             "sunset", "civil_dusk", "nautical_dusk", "astronomical_dusk"]
    vals = [sm[k] for k in order]
    assert all(v is not None for v in vals), sm
    assert vals == sorted(vals), ("sun events out of order", dict(zip(order, vals)))
    # solar noon altitude sanity: ~ 90 - |lat - dec|; at this date/lat sun is high (>50)
    mins, sun_alt, _ = _sample_day(_date(2026, 8, 29), 40.32, -7.61, 1.0)
    assert max(sun_alt) > 50.0, max(sun_alt)
    # late August in Portugal: sunrise early-morning, sunset evening (loose windows)
    assert 5 * 60 < sm["sunrise"] < 8 * 60, _hhmm(sm["sunrise"])
    assert 19 * 60 < sm["sunset"] < 21 * 60, _hhmm(sm["sunset"])
    # illumination in range and consistent with a named phase
    assert 0.0 <= r["moon_illumination"] <= 1.0, r["moon_illumination"]
    assert 0.0 <= r["moon_age_days"] <= SYNODIC_MONTH + 0.1, r["moon_age_days"]
    assert r["moon_phase"], r
    # a polar-day guard: northern midsummer above the Arctic circle -> no sunset
    rp = events(_date(2026, 6, 21), 78.0, 15.0, 1.0)
    assert rp["sun_min"]["sunset"] is None, rp["sun"]
    print("astro selftest OK:", json.dumps({"sunrise": r["sun"]["sunrise"],
          "sunset": r["sun"]["sunset"], "moon": r["moon_phase"],
          "illum": r["moon_illumination"]}))
    print(json.dumps({"ok": True, "selftest": "passed"}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Sun & moon events for a place/day (offline).")
    ap.add_argument("--date", help="local date YYYY-MM-DD (default: today)")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--tz", type=float, default=0.0, help="UTC offset in hours (e.g. 1, -7, 5.5)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if args.lat is None or args.lon is None:
        ap.error("--lat and --lon are required (or use --selftest)")
    d = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.utcnow().date()
    print(json.dumps(events(d, args.lat, args.lon, args.tz)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
