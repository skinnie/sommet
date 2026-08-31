#!/usr/bin/env python3
"""Small, dependency-free geo helpers shared by the route-weather tools
(`track_color.py`, `weather_route.py`).

Stdlib only, on purpose - same rule as the rest of `tools/`: the backend ships frozen with
PyInstaller and must never need a pip install. Everything here is plain math or `struct`.

Nothing in this file touches a watch or the network; it is pure computation, so it is the
one place the offline-routing feature is fully unit-testable without any hardware, server or
downloaded data (see `test_offline_routing.py`).
"""

import math
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

EARTH_RADIUS_M = 6371000.0


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres. Good to well under a metre at trail scale, which is
    all the climb-gradient and POI-proximity maths here ever need."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def cumulative_distances(points):
    """[(lat, lon), ...] -> [0, d01, d01+d12, ...] cumulative horizontal metres."""
    out = [0.0]
    for i in range(1, len(points)):
        out.append(out[-1] + haversine_m(points[i - 1][0], points[i - 1][1],
                                          points[i][0], points[i][1]))
    return out


def meters_per_degree(lat):
    """(m per degree latitude, m per degree longitude) at this latitude - used to turn a
    metre radius into a cheap lat/lon bounding box before doing exact haversine."""
    lat_r = math.radians(lat)
    m_per_deg_lat = 111132.92 - 559.82 * math.cos(2 * lat_r) + 1.175 * math.cos(4 * lat_r)
    m_per_deg_lon = 111412.84 * math.cos(lat_r) - 93.5 * math.cos(3 * lat_r)
    return m_per_deg_lat, max(1.0, abs(m_per_deg_lon))


# --- GPX ------------------------------------------------------------------------------

def parse_gpx_points(gpx_text):
    """Pull an ordered [{'lat','lon','ele'}] track out of a GPX string. Reads <trkpt> first
    (a recorded/planned track), falling back to <rtept> then <wpt> so it also accepts the
    route/waypoint GPX shapes Sommet already exports. Namespace-agnostic (matches any
    xmlns) because GPX in the wild is written by everyone. `ele` is None when absent."""
    root = ET.fromstring(gpx_text.encode("utf-8") if isinstance(gpx_text, str) else gpx_text)

    def local(tag):
        return tag.rsplit("}", 1)[-1]

    for wanted in ("trkpt", "rtept", "wpt"):
        pts = []
        for el in root.iter():
            if local(el.tag) != wanted:
                continue
            try:
                lat = float(el.attrib["lat"])
                lon = float(el.attrib["lon"])
            except (KeyError, ValueError):
                continue
            ele = None
            for child in el:
                if local(child.tag) == "ele":
                    try:
                        ele = float((child.text or "").strip())
                    except ValueError:
                        ele = None
                    break
            pts.append({"lat": lat, "lon": lon, "ele": ele})
        if pts:
            return pts
    return []


def points_to_gpx(points, name="Sommet route"):
    """Emit a minimal GPX 1.1 <trk> from [{'lat','lon','ele'?}] - enough for Sommet's
    existing `write_nav.py route GPX` export path to push a planned route to the watch, and
    for any other GPX consumer. Coordinates only; the four device-meta values (distance,
    ascent, descent, timestamp) are filled by write_nav's own --meta handling as before."""
    esc = (name or "route").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    rows = []
    for p in points:
        ele = p.get("ele")
        if ele is None:
            rows.append('      <trkpt lat="%.6f" lon="%.6f"/>' % (p["lat"], p["lon"]))
        else:
            rows.append('      <trkpt lat="%.6f" lon="%.6f"><ele>%.1f</ele></trkpt>'
                        % (p["lat"], p["lon"], ele))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">\n'
            '  <trk><name>%s</name><trkseg>\n%s\n  </trkseg></trk>\n</gpx>\n'
            % (esc, "\n".join(rows)))


# --- SRTM / .hgt elevation (only used when a track has no ele of its own) ---------------

def _hgt_filename(lat, lon):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return "%s%02d%s%03d.hgt" % (ns, int(abs(math.floor(lat))), ew, int(abs(math.floor(lon))))


def sample_hgt(lat, lon, hgt_dir):
    """Bilinearly sample SRTM elevation (metres) for one point from a directory of .hgt
    tiles - the *same* free elevation data BRouter itself routes on, so colouring an imported
    GPX that carries no <ele> matches a BRouter-planned one. Auto-detects 1"/3" tile size
    from file length. Returns None if the covering tile isn't present or the sample is void
    (SRTM's -32768). This is the offline fallback; a BRouter route already carries ele."""
    path = Path(hgt_dir) / _hgt_filename(lat, lon)
    if not path.exists():
        return None
    size = path.stat().st_size
    dim = 3601 if size >= 3601 * 3601 * 2 else 1201  # 1-arcsec vs 3-arcsec
    lat0 = math.floor(lat)
    lon0 = math.floor(lon)
    # row 0 is the north (top) edge; col 0 is the west (left) edge
    y = (lat0 + 1 - lat) * (dim - 1)
    x = (lon - lon0) * (dim - 1)
    r0, c0 = int(math.floor(y)), int(math.floor(x))
    r0 = max(0, min(dim - 2, r0))
    c0 = max(0, min(dim - 2, c0))
    fy, fx = y - r0, x - c0

    def read(r, c):
        with open(path, "rb") as fh:
            fh.seek((r * dim + c) * 2)
            (v,) = struct.unpack(">h", fh.read(2))
        return None if v == -32768 else v

    v00, v01, v10, v11 = read(r0, c0), read(r0, c0 + 1), read(r0 + 1, c0), read(r0 + 1, c0 + 1)
    vals = [v for v in (v00, v01, v10, v11) if v is not None]
    if not vals:
        return None
    if None in (v00, v01, v10, v11):
        return sum(vals) / len(vals)  # a void corner: fall back to a plain average
    top = v00 * (1 - fx) + v01 * fx
    bot = v10 * (1 - fx) + v11 * fx
    return top * (1 - fy) + bot * fy
