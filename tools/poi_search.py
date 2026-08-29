#!/usr/bin/env python3
"""Offline POI search - the "type a name / find what's near here / what's along my route"
box, the Komoot-ish bit, with zero network. Backed by a plain SQLite file, which stdlib
`sqlite3` reads directly, so nothing to install and nothing to host.

Two jobs:
  * build  - ingest POIs (from GeoJSON or CSV extracted from OpenStreetMap) into an indexed
              SQLite DB. In practice you make the input once per region with osmium, e.g.
                osmium tags-filter region.osm.pbf \\
                  n/amenity=drinking_water,shelter,cafe n/tourism=alpine_hut,viewpoint,camp_site \\
                  n/natural=peak,spring -o pois.osm.pbf
                osmium export pois.osm.pbf -f geojson -o pois.geojson
              then `poi_search.py build --from pois.geojson --db region.poi.sqlite`.
  * search  - by name (prefix/substring), by nearest to a point, or along a route corridor.

Uses SQLite FTS5 for name search and R*Tree for the spatial index when the local sqlite has
them (it usually does); otherwise it falls back to LIKE + an indexed lat/lon bounding box, so
it still works on a stripped build. Exact distances are always finished with haversine.

    ./tools/poi_search.py build --from pois.geojson --db region.poi.sqlite
    ./tools/poi_search.py search --db region.poi.sqlite --name "refuge"
    ./tools/poi_search.py search --db region.poi.sqlite --near 6.12,46.20 --radius 3000
    ./tools/poi_search.py --selftest
"""

import argparse
import csv
import json
import sqlite3
import sys

import geo_util


def _has_module(con, create_sql):
    try:
        con.execute(create_sql)
        return True
    except sqlite3.OperationalError:
        return False


def open_db(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def build(db_path, features):
    """features: iterable of {'name','category','lat','lon'} -> a fresh indexed DB."""
    con = open_db(db_path)
    cur = con.cursor()
    cur.executescript("""
        DROP TABLE IF EXISTS poi;
        CREATE TABLE poi (id INTEGER PRIMARY KEY, name TEXT, category TEXT,
                          lat REAL, lon REAL);
    """)
    # optional accelerators, each guarded so a stripped sqlite still builds
    has_fts = _has_module(con, "CREATE VIRTUAL TABLE poi_fts USING fts5(name, content='poi', content_rowid='id')")
    has_rt = _has_module(con, "CREATE VIRTUAL TABLE poi_rt USING rtree(id, minLat, maxLat, minLon, maxLon)")
    if not has_rt:
        cur.execute("CREATE INDEX idx_poi_lat ON poi(lat)")
        cur.execute("CREATE INDEX idx_poi_lon ON poi(lon)")

    n = 0
    for f in features:
        try:
            lat, lon = float(f["lat"]), float(f["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        name = (f.get("name") or "").strip()
        cat = (f.get("category") or "").strip()
        if not name and not cat:
            continue
        cur.execute("INSERT INTO poi(name, category, lat, lon) VALUES (?,?,?,?)",
                    (name, cat, lat, lon))
        rid = cur.lastrowid
        if has_fts:
            cur.execute("INSERT INTO poi_fts(rowid, name) VALUES (?,?)", (rid, name))
        if has_rt:
            cur.execute("INSERT INTO poi_rt VALUES (?,?,?,?,?)", (rid, lat, lat, lon, lon))
        n += 1
    con.commit()
    meta = {"count": n, "fts": has_fts, "rtree": has_rt}
    con.execute("CREATE TABLE IF NOT EXISTS poi_meta(k TEXT PRIMARY KEY, v TEXT)")
    con.execute("INSERT OR REPLACE INTO poi_meta VALUES ('build', ?)", (json.dumps(meta),))
    con.commit()
    con.close()
    return meta


def _row(r, dist=None):
    d = {"id": r["id"], "name": r["name"], "category": r["category"],
         "lat": r["lat"], "lon": r["lon"]}
    if dist is not None:
        d["distance_m"] = round(dist, 1)
    return d


def _fts_available(con):
    try:
        con.execute("SELECT 1 FROM poi_fts LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def search_name(con, query, limit=20, category=None):
    q = (query or "").strip()
    if not q:
        return []
    rows = None
    if _fts_available(con):
        try:
            rows = con.execute(
                "SELECT p.* FROM poi_fts f JOIN poi p ON p.id=f.rowid "
                "WHERE poi_fts MATCH ? LIMIT ?", (q + "*", limit * 4)).fetchall()
        except sqlite3.OperationalError:
            rows = None
    if rows is None:  # fallback: substring
        rows = con.execute("SELECT * FROM poi WHERE name LIKE ? LIMIT ?",
                           ("%" + q + "%", limit * 4)).fetchall()
    out = [_row(r) for r in rows]
    if category:
        out = [r for r in out if r["category"] == category]
    return out[:limit]


def _rtree_available(con):
    try:
        con.execute("SELECT 1 FROM poi_rt LIMIT 1")
        return True
    except sqlite3.OperationalError:
        return False


def _bbox_candidates(con, lat, lon, radius_m):
    m_lat, m_lon = geo_util.meters_per_degree(lat)
    dlat = radius_m / m_lat
    dlon = radius_m / m_lon
    lo_la, hi_la, lo_lo, hi_lo = lat - dlat, lat + dlat, lon - dlon, lon + dlon
    if _rtree_available(con):
        return con.execute(
            "SELECT p.* FROM poi_rt r JOIN poi p ON p.id=r.id "
            "WHERE r.maxLat>=? AND r.minLat<=? AND r.maxLon>=? AND r.minLon<=?",
            (lo_la, hi_la, lo_lo, hi_lo)).fetchall()
    return con.execute(
        "SELECT * FROM poi WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
        (lo_la, hi_la, lo_lo, hi_lo)).fetchall()


def search_near(con, lat, lon, radius_m=5000, limit=20, category=None):
    """Nearest POIs within radius, exact-distance sorted. Bounding box prefilter (R*Tree if
    present) then haversine, so it stays fast on a big regional DB."""
    hits = []
    for r in _bbox_candidates(con, lat, lon, radius_m):
        if category and r["category"] != category:
            continue
        d = geo_util.haversine_m(lat, lon, r["lat"], r["lon"])
        if d <= radius_m:
            hits.append(_row(r, d))
    hits.sort(key=lambda x: x["distance_m"])
    return hits[:limit]


def search_along(con, points, buffer_m=500, limit=50, category=None):
    """POIs within buffer_m of a route polyline (the "highlights along your route" query).
    Prefilters by the route's bounding box, then keeps a POI if any track vertex is within
    buffer_m of it; reports the closest such distance. `points` is [{lat,lon}] or [[lat,lon]]."""
    pts = [(p["lat"], p["lon"]) if isinstance(p, dict) else (p[0], p[1]) for p in points]
    if not pts:
        return []
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    m_lat, m_lon = geo_util.meters_per_degree(sum(lats) / len(lats))
    dlat, dlon = buffer_m / m_lat, buffer_m / m_lon
    if _rtree_available(con):
        cand = con.execute(
            "SELECT p.* FROM poi_rt r JOIN poi p ON p.id=r.id "
            "WHERE r.maxLat>=? AND r.minLat<=? AND r.maxLon>=? AND r.minLon<=?",
            (min(lats) - dlat, max(lats) + dlat, min(lons) - dlon, max(lons) + dlon)).fetchall()
    else:
        cand = con.execute(
            "SELECT * FROM poi WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
            (min(lats) - dlat, max(lats) + dlat, min(lons) - dlon, max(lons) + dlon)).fetchall()
    out = []
    for r in cand:
        if category and r["category"] != category:
            continue
        best = min(geo_util.haversine_m(r["lat"], r["lon"], la, lo) for la, lo in pts)
        if best <= buffer_m:
            out.append(_row(r, best))
    out.sort(key=lambda x: x["distance_m"])
    return out[:limit]


# --- ingest sources --------------------------------------------------------------------

def features_from_geojson(doc):
    """OSM-export GeoJSON (osmium export) -> build rows. Uses Point geometry; name from
    properties.name, category from the first of amenity/tourism/natural/shop/leisure."""
    cat_keys = ("amenity", "tourism", "natural", "shop", "leisure", "historic")
    for feat in doc.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        lon, lat = geom["coordinates"][0], geom["coordinates"][1]
        props = feat.get("properties") or {}
        cat = next((("%s=%s" % (k, props[k])) for k in cat_keys if props.get(k)), "")
        yield {"name": props.get("name", ""), "category": cat, "lat": lat, "lon": lon}


def features_from_csv(fh):
    """CSV with columns name,category,lat,lon (header row required)."""
    for row in csv.DictReader(fh):
        yield {"name": row.get("name", ""), "category": row.get("category", ""),
               "lat": row.get("lat"), "lon": row.get("lon")}


# --- selftest --------------------------------------------------------------------------

_SAMPLE = [
    {"name": "Refuge du Goûter", "category": "tourism=alpine_hut", "lat": 45.851, "lon": 6.831},
    {"name": "Fontaine Saint-Gervais", "category": "amenity=drinking_water", "lat": 45.850, "lon": 6.833},
    {"name": "Cafe du Mont", "category": "amenity=cafe", "lat": 46.200, "lon": 6.150},
    {"name": "Viewpoint Aiguille", "category": "tourism=viewpoint", "lat": 45.878, "lon": 6.887},
]


def _selftest():
    import tempfile, os
    db = tempfile.mktemp(suffix=".poi.sqlite")
    try:
        meta = build(db, _SAMPLE)
        assert meta["count"] == 4, meta
        con = open_db(db)
        # name search finds the hut by prefix
        byname = search_name(con, "Refuge")
        assert any("Goûter" in r["name"] for r in byname), byname
        # nearest to a point near the two Saint-Gervais POIs, in distance order
        near = search_near(con, 45.8505, 6.832, radius_m=500)
        assert len(near) == 2, near
        assert near[0]["distance_m"] <= near[1]["distance_m"], near
        # category filter
        water = search_near(con, 45.8505, 6.832, radius_m=1000, category="amenity=drinking_water")
        assert len(water) == 1 and "Fontaine" in water[0]["name"], water
        # along a short route past the cafe
        route = [{"lat": 46.199, "lon": 6.149}, {"lat": 46.201, "lon": 6.151}]
        along = search_along(con, route, buffer_m=400)
        assert any("Cafe" in r["name"] for r in along), along
        con.close()
        print("poi_search selftest OK:", json.dumps(meta))
        print(json.dumps({"ok": True, "selftest": "passed"}))
        return 0
    finally:
        if os.path.exists(db):
            os.unlink(db)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Offline OSM POI search (SQLite).")
    sub = ap.add_subparsers(dest="cmd")

    b = sub.add_parser("build", help="ingest POIs into an indexed SQLite DB")
    b.add_argument("--db", required=True)
    b.add_argument("--from", dest="src", required=True, help="pois.geojson or pois.csv")

    s = sub.add_parser("search", help="query a POI DB")
    s.add_argument("--db", required=True)
    s.add_argument("--name")
    s.add_argument("--near", metavar="LON,LAT")
    s.add_argument("--along", metavar="FILE",
                   help="JSON [{lat,lon}] / {points:[...]} route file: POIs along the corridor")
    s.add_argument("--buffer", type=float, default=500.0, help="--along corridor width, m")
    s.add_argument("--radius", type=float, default=5000.0)
    s.add_argument("--category")
    s.add_argument("--limit", type=int, default=20)

    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    if args.cmd == "build":
        if args.src.lower().endswith(".csv"):
            with open(args.src, newline="", encoding="utf-8") as fh:
                meta = build(args.db, list(features_from_csv(fh)))
        else:
            doc = json.load(open(args.src, encoding="utf-8"))
            meta = build(args.db, list(features_from_geojson(doc)))
        print(json.dumps({"ok": True, **meta}))
        return 0

    if args.cmd == "search":
        con = open_db(args.db)
        if args.name:
            res = search_name(con, args.name, limit=args.limit, category=args.category)
        elif args.near:
            lon, lat = (float(x) for x in args.near.split(","))
            res = search_near(con, lat, lon, radius_m=args.radius, limit=args.limit,
                              category=args.category)
        elif args.along:
            doc = json.load(open(args.along, encoding="utf-8"))
            pts = doc["points"] if isinstance(doc, dict) else doc
            res = search_along(con, pts, buffer_m=args.buffer, limit=args.limit,
                               category=args.category)
        else:
            ap.error("search needs --name, --near LON,LAT, or --along FILE")
        print(json.dumps({"ok": True, "results": res}))
        return 0

    ap.error("a subcommand is required (build / search) or --selftest")


if __name__ == "__main__":
    sys.exit(main())
