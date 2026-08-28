#!/usr/bin/env python3
"""Plan a bike/trekking route fully offline by querying a local BRouter server, and hand back
a normalised track (lat/lon/elevation) Sommet can draw, colour by climb (`track_color.py`)
and export to the watch as GPX (the existing `write_nav.py route` path).

Why BRouter: it's the open-source (MIT) offline router built for exactly this - customisable
bike/hike profiles with a kinetic model, elevation-aware, routing on its own `.rd5` segment
tiles generated from OpenStreetMap (the same OSM the Garmin `.img` on the eTrex is compiled
from, so a planned route stays consistent with what the device shows). It runs as a small
local HTTP service on :17777 and returns GeoJSON/GPX directly, so this tool never links it -
it just talks to it over the loopback, the same way the backend already talks to intervals /
Garmin. No connectivity is used once the `.rd5` segments for the region are downloaded.

    ./tools/route_plan.py --via 6.10,46.20 --via 6.15,46.25 --profile trekking
    ./tools/route_plan.py --via LON,LAT --via LON,LAT --gpx-out plan.gpx
    ./tools/route_plan.py --health           # is a BRouter server reachable?
    ./tools/route_plan.py --selftest         # offline: parse a canned BRouter reply

Server URL comes from --url or $SOMMET_BROUTER_URL, default http://127.0.0.1:17777.
Points are given as LON,LAT (BRouter's own order); at least two are required.
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

import geo_util

DEFAULT_URL = os.environ.get("SOMMET_BROUTER_URL", "http://127.0.0.1:17777")


def _brouter_url(base, lonlats, profile, alt_idx):
    q = urllib.parse.urlencode({
        "lonlats": "|".join("%s,%s" % (round(lon, 6), round(lat, 6)) for lon, lat in lonlats),
        "profile": profile,
        "alternativeidx": alt_idx,
        "format": "geojson",
    })
    return base.rstrip("/") + "/brouter?" + q


def parse_geojson(doc):
    """Normalise a BRouter GeoJSON reply into {'ok','points':[{lat,lon,ele}],'summary':{...}}.
    BRouter returns a FeatureCollection whose one LineString has [lon, lat, ele] coordinate
    triples and a properties bag with track-length / filtered ascend etc."""
    feats = doc.get("features") or []
    if not feats:
        return {"ok": False, "error": "BRouter returned no route (unreachable points?)"}
    geom = feats[0].get("geometry", {})
    coords = geom.get("coordinates") or []
    if geom.get("type") != "LineString" or len(coords) < 2:
        return {"ok": False, "error": "BRouter reply has no LineString geometry"}
    points = []
    for c in coords:
        lon, lat = float(c[0]), float(c[1])
        ele = float(c[2]) if len(c) > 2 and c[2] is not None else None
        points.append({"lat": lat, "lon": lon, "ele": ele})
    props = feats[0].get("properties", {}) or {}

    def num(key):
        try:
            return float(props[key])
        except (KeyError, TypeError, ValueError):
            return None

    summary = {
        "distance_m": num("track-length"),
        "ascent_m": num("filtered ascend"),
        "total_time_s": num("total-time"),
        "total_energy_j": num("total-energy"),
        "profile": props.get("name"),
    }
    return {"ok": True, "points": points, "summary": summary, "properties": props}


def plan_route(lonlats, profile="trekking", url=DEFAULT_URL, alt_idx=0, timeout=30):
    """Query BRouter and return the normalised dict. Network/HTTP problems become a clean
    {'ok': False, 'error': ...} (a missing local server is the common case - the caller shows
    'start the offline router' rather than a stack trace)."""
    if len(lonlats) < 2:
        return {"ok": False, "error": "need at least two points (start and end)"}
    req = urllib.request.Request(_brouter_url(url, lonlats, profile, alt_idx),
                                 headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300] if hasattr(e, "read") else ""
        return {"ok": False, "error": "BRouter HTTP %s: %s" % (e.code, detail.strip())}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "error": "no offline router at %s (%s)" % (url, e),
                "hint": "start brouter-server and download the region's .rd5 segments"}
    try:
        doc = json.loads(raw)
    except ValueError:
        # BRouter emits a plain-text error line (e.g. "position not mapped in existing datafile")
        return {"ok": False, "error": raw.strip()[:300] or "unparseable BRouter reply"}
    return parse_geojson(doc)


def health(url=DEFAULT_URL, timeout=5):
    """A trivial reachability probe - a malformed /brouter with no params answers with a
    text error, which is enough to prove the server is up without needing real segments."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/brouter", timeout=timeout) as r:
            r.read(64)
        return {"ok": True, "url": url, "reachable": True}
    except urllib.error.HTTPError:
        return {"ok": True, "url": url, "reachable": True}  # answered = up
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "url": url, "reachable": False, "error": str(e)}


_CANNED = {
    "type": "FeatureCollection",
    "features": [{
        "type": "Feature",
        "properties": {"name": "trekking", "track-length": "412", "filtered ascend": "23",
                       "total-time": "128", "total-energy": "41000"},
        "geometry": {"type": "LineString", "coordinates": [
            [6.1000, 46.2000, 400], [6.1010, 46.2005, 405], [6.1020, 46.2012, 412],
            [6.1035, 46.2019, 420], [6.1050, 46.2025, 423]]},
    }],
}


def _selftest():
    r = parse_geojson(_CANNED)
    assert r["ok"], r
    assert len(r["points"]) == 5, r
    assert r["points"][0] == {"lat": 46.2, "lon": 6.1, "ele": 400.0}, r["points"][0]
    assert r["summary"]["distance_m"] == 412.0, r["summary"]
    assert r["summary"]["ascent_m"] == 23.0, r["summary"]
    # a bad reply is a clean failure, not an exception
    assert parse_geojson({"features": []})["ok"] is False
    assert parse_geojson({})["ok"] is False
    # and the GeoJSON points flow straight into the colourizer
    import track_color
    col = track_color.colorize(r["points"], step_m=20.0, window_m=60.0)
    assert col["ok"], col
    print("route_plan selftest OK:", json.dumps(r["summary"]))
    print(json.dumps({"ok": True, "selftest": "passed"}))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Plan a bike/trek route offline via BRouter.")
    ap.add_argument("--via", action="append", default=[], metavar="LON,LAT",
                    help="a waypoint as LON,LAT (repeat; first=start, last=end)")
    ap.add_argument("--profile", default="trekking",
                    help="BRouter profile: trekking, fastbike, gravel, mtb, hiking-mountain, ...")
    ap.add_argument("--url", default=DEFAULT_URL, help="BRouter server URL")
    ap.add_argument("--alt", type=int, default=0, help="alternative route index 0-3")
    ap.add_argument("--gpx-out", help="also write the planned route to this GPX file")
    ap.add_argument("--health", action="store_true", help="probe the server and exit")
    ap.add_argument("--selftest", action="store_true", help="offline self-test and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.health:
        print(json.dumps(health(args.url)))
        return 0

    lonlats = []
    for v in args.via:
        try:
            lon, lat = (float(x) for x in v.split(","))
        except ValueError:
            ap.error("--via must be LON,LAT, got %r" % v)
        lonlats.append((lon, lat))

    result = plan_route(lonlats, profile=args.profile, url=args.url, alt_idx=args.alt)
    if result["ok"] and args.gpx_out:
        gpx = geo_util.points_to_gpx(result["points"], name="Sommet plan (%s)" % args.profile)
        open(args.gpx_out, "w", encoding="utf-8").write(gpx)
        result["gpx_out"] = args.gpx_out
    print(json.dumps(result))  # last JSON line = machine-readable summary
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
