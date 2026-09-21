#!/usr/bin/env python3
"""POIs along a race route + the gap analysis that actually matters on a brevet:
"next reliable water: 34 km", "longest water gap: 82 km", "no food for 117 km", "bike shop at
km 312". Not a map full of pins - the distilled, actionable numbers.

Finds POIs in a corridor around the route via OSM Overpass (the online-enrich source; the fetch
is injectable so tests run offline), snaps each to its distance along the route, and computes
per-category gaps. Categories cover the brevet needs: water, food, bike, shelter, safety.

Also a distance-based water budget: given a consumption rate, how many litres each water-to-water
gap needs, and which gaps exceed what you can carry.

Input (JSON, file or stdin):
  {"gpx"|"points", "categories": ["water","food",...], "radius_m"?, "water_l_per_100km"?,
   "carry_l"?}
Output: {ok, total_km, categories:{name:{pois:[{name,km,lat,lon,subtype}], count, first_km,
  longest_gap_km, longest_gap_after_km, gaps_over_carry?}}, summary:[lines]}

Stdlib only; Overpass is the only online part. `--selftest` is offline.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

import geo_util

# category -> OSM selectors (Overpass QL, matched on node/way/relation). Kept deliberately tight
# to "reliable" resupply where it matters (e.g. fountains only when tagged drinkable).
CATEGORIES: Dict[str, List[str]] = {
    "water": [
        "[amenity=drinking_water]",
        "[man_made=water_tap][drinking_water!=no]",
        "[amenity=water_point]",
        "[amenity=fountain][drinking_water=yes]",
    ],
    "food": [
        "[shop=supermarket]", "[shop=convenience]", "[shop=bakery]",
        "[amenity=cafe]", "[amenity=restaurant]", "[amenity=fast_food]", "[amenity=fuel]",
    ],
    # Cemeteries almost always have a water tap - a randonneur staple for refills (André, 2026-09-21).
    "cemetery": ["[landuse=cemetery]", "[amenity=grave_yard]"],
    "bike": ["[shop=bicycle]", "[amenity=bicycle_repair_station]"],
    "shelter": ["[tourism=hotel]", "[tourism=hostel]", "[tourism=guest_house]",
                "[tourism=motel]", "[tourism=camp_site]"],
    "safety": ["[amenity=pharmacy]", "[amenity=hospital]", "[amenity=toilets]",
               "[amenity=atm]", "[railway=station]"],
}
# categories where gap analysis (resupply) is meaningful
RESUPPLY = {"water", "food"}
DEFAULT_RADIUS_M = 250
OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def _downsample(points: List[Dict[str, Any]], cumul_m: List[float],
                step_m: float = 2500.0, cap: int = 260) -> List[Tuple[float, float]]:
    """Thin the route to ~one coord per step_m (capped) for a compact Overpass corridor filter."""
    out = [(points[0]["lat"], points[0]["lon"])]
    last = 0.0
    for i in range(1, len(points)):
        if cumul_m[i] - last >= step_m:
            out.append((points[i]["lat"], points[i]["lon"]))
            last = cumul_m[i]
    if (points[-1]["lat"], points[-1]["lon"]) != out[-1]:
        out.append((points[-1]["lat"], points[-1]["lon"]))
    if len(out) > cap:  # even stride down to the cap
        stride = len(out) / cap
        out = [out[int(i * stride)] for i in range(cap)]
    return out


def _custom_selectors(term: str) -> List[str]:
    """Overpass selectors for a free-text category term (PitStopper-style). A plain word matches the
    common OSM keys it could live under; a raw "key=value" is used verbatim."""
    t = term.strip()
    if not t:
        return []
    if "=" in t:                     # power users can pass an exact "amenity=pharmacy"
        return ["[%s]" % t]
    t = t.replace(" ", "_").lower()
    return ["[amenity=%s]" % t, "[shop=%s]" % t, "[tourism=%s]" % t, "[leisure=%s]" % t]


def build_query(coords: List[Tuple[float, float]], radius_m: int,
                selectors: List[str]) -> str:
    poly = ",".join("%.5f,%.5f" % (lat, lon) for lat, lon in coords)
    around = "(around:%d,%s)" % (radius_m, poly)
    lines = ["[out:json][timeout:90];", "("]
    for sel in selectors:
        lines.append("  nwr%s%s;" % (sel, around))
    lines.append(");")
    lines.append("out center tags;")
    return "\n".join(lines)


def _overpass_fetch(query: str) -> Dict[str, Any]:
    import urllib.parse
    import urllib.request
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(OVERPASS_URL, data=data,
                                 headers={"User-Agent": "Sommet-RacePlanner/1.0"})
    with urllib.request.urlopen(req, timeout=95) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _subtype(tags: Dict[str, str]) -> str:
    for k in ("amenity", "shop", "tourism", "man_made", "railway", "landuse"):
        if k in tags:
            return tags[k]
    return "poi"


def _categorize(tags: Dict[str, str]) -> Optional[str]:
    a, s, t = tags.get("amenity"), tags.get("shop"), tags.get("tourism")
    if a == "drinking_water" or tags.get("man_made") == "water_tap" or a == "water_point" \
            or (a == "fountain" and tags.get("drinking_water") == "yes"):
        return "water"
    if s in ("supermarket", "convenience", "bakery") or a in ("cafe", "restaurant", "fast_food", "fuel"):
        return "food"
    if tags.get("landuse") == "cemetery" or a == "grave_yard":
        return "cemetery"
    if s == "bicycle" or a == "bicycle_repair_station":
        return "bike"
    if t in ("hotel", "hostel", "guest_house", "motel", "camp_site"):
        return "shelter"
    if a in ("pharmacy", "hospital", "toilets", "atm") or tags.get("railway") == "station":
        return "safety"
    return None


def _nearest_km(points: List[Dict[str, Any]], cumul_m: List[float], lat: float, lon: float) -> Tuple[float, float]:
    """Return (km_along_route, offset_m) of the route point nearest to (lat,lon)."""
    best_i, best_d = 0, float("inf")
    for i, p in enumerate(points):
        d = geo_util.haversine_m(lat, lon, p["lat"], p["lon"])
        if d < best_d:
            best_d, best_i = d, i
    return cumul_m[best_i] / 1000.0, best_d


def _gaps(kms: List[float], total_km: float) -> Dict[str, Any]:
    """Gap stats for a sorted list of resupply POI kms: first, longest gap (incl. start->first
    and last->finish), and where the longest gap begins."""
    ks = sorted(kms)
    if not ks:
        return {"count": 0, "first_km": None, "longest_gap_km": round(total_km, 1),
                "longest_gap_after_km": 0.0}
    edges = [0.0] + ks + [total_km]
    longest = 0.0
    after = 0.0
    for i in range(1, len(edges)):
        g = edges[i] - edges[i - 1]
        if g > longest:
            longest = g
            after = edges[i - 1]
    return {"count": len(ks), "first_km": round(ks[0], 1),
            "longest_gap_km": round(longest, 1), "longest_gap_after_km": round(after, 1)}


def analyze(points: List[Dict[str, Any]], categories: List[str], radius_m: int = DEFAULT_RADIUS_M,
            water_l_per_100km: float = 2.0, carry_l: float = 1.5,
            custom_tags: Optional[List[str]] = None,
            fetch: Optional[Callable[[str], Dict[str, Any]]] = None) -> Dict[str, Any]:
    if len(points) < 2:
        return {"ok": False, "error": "route needs >= 2 points"}
    categories = [c for c in categories if c in CATEGORIES] or list(CATEGORIES.keys())
    # Free-text extra categories (André, 2026-09-21: "add categories, like pitstopper"). Each term
    # becomes its own bucket, keyed "custom:<term>", matched against the common OSM keys.
    custom = [t.strip() for t in (custom_tags or []) if t and t.strip()]
    custom_norm = {t: t.replace(" ", "_").lower() for t in custom}
    cumul_m = geo_util.cumulative_distances([(p["lat"], p["lon"]) for p in points])
    total_km = cumul_m[-1] / 1000.0

    coords = _downsample(points, cumul_m)
    # Audit fix #5: a single Overpass query over a 300+ km corridor 504s. Split the corridor into
    # overlapping chunks and union the results, so brevet-length routes work. A failed chunk is
    # skipped (partial results still useful) rather than failing the whole search.
    fetch = fetch or _overpass_fetch
    selectors: List[str] = []
    for c in categories:
        selectors.extend(CATEGORIES[c])
    for t in custom:
        selectors.extend(_custom_selectors(t))
    CHUNK, OVERLAP = 55, 1
    elements = []
    i = 0
    errors = 0
    while i < len(coords):
        chunk = coords[i:i + CHUNK]
        if len(chunk) < 2 and elements:
            break
        try:
            doc = fetch(build_query(chunk, radius_m, selectors))
            if isinstance(doc, dict):
                elements.extend(doc.get("elements", []))
        except Exception:
            errors += 1
        if i + CHUNK >= len(coords):
            break
        i += CHUNK - OVERLAP

    per_cat: Dict[str, List[Dict[str, Any]]] = {c: [] for c in categories}
    for t in custom:
        per_cat["custom:" + t] = []

    def _match_custom(tags: Dict[str, str]) -> Optional[str]:
        vals = {tags.get(k) for k in ("amenity", "shop", "tourism", "leisure")}
        for t, norm in custom_norm.items():
            if norm in vals or ("=" in t and tags.get(t.split("=", 1)[0]) == t.split("=", 1)[1]):
                return "custom:" + t
        return None

    seen = set()
    for el in elements:
        tags = el.get("tags") or {}
        cat = _match_custom(tags) or _categorize(tags)
        if cat not in per_cat:
            continue
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        km, off = _nearest_km(points, cumul_m, lat, lon)
        if off > radius_m * 1.5:  # centroid slop guard
            continue
        key = (cat, round(lat, 5), round(lon, 5))
        if key in seen:
            continue
        seen.add(key)
        per_cat[cat].append({"name": tags.get("name") or _subtype(tags).replace("_", " ").title(),
                             "km": round(km, 1), "lat": lat, "lon": lon,
                             "subtype": _subtype(tags)})

    out_cats: Dict[str, Any] = {}
    summary: List[str] = []
    for cat in list(categories) + ["custom:" + t for t in custom]:
        pois = sorted(per_cat[cat], key=lambda p: p["km"])
        info: Dict[str, Any] = {"pois": pois}
        if cat in RESUPPLY:
            # Realistic refill: a rider gets WATER not only at tagged fountains but at cafes/shops/
            # fuel and cemeteries too. So the "water" gap credits every refill source that was found,
            # not just water nodes (André, 2026-09-21: reliability comes from realistic assumptions,
            # not tag purity). Food credits food + fuel. (Opening-hours / night-safe split: later.)
            gap_kms = [p["km"] for p in pois]
            if cat == "water":
                for extra in ("cemetery", "food"):
                    if extra in per_cat:
                        gap_kms += [p["km"] for p in per_cat[extra]]
            g = _gaps(gap_kms, total_km)
            info.update(g)
            if cat == "water":
                # distance-based budget: litres for the longest gap, and gaps over carry capacity.
                need_longest = round(water_l_per_100km * g["longest_gap_km"] / 100.0, 1)
                info["longest_gap_litres"] = need_longest
                info["carry_l"] = carry_l
                info["longest_gap_over_carry"] = need_longest > carry_l
            label = {"water": "refill (water/café/shop)", "food": "food"}[cat]
            short = {"water": "refill", "food": "food"}[cat]
            if g["count"] == 0:
                summary.append("No %s found on this route." % label)
            else:
                summary.append("Next %s: %.0f km · longest stretch with no %s: %.0f km (after km %.0f)"
                               % (short, g["first_km"], short, g["longest_gap_km"], g["longest_gap_after_km"]))
                if cat == "water" and info["longest_gap_over_carry"]:
                    summary.append("  ⚠ that %.0f km dry stretch needs ~%.1f L (> %.1f L carried) — top up early."
                                   % (g["longest_gap_km"], info["longest_gap_litres"], carry_l))
        else:
            info["count"] = len(pois)
            if pois:
                nice = {"bike": "Bike shop/repair", "shelter": "Accommodation", "safety": "Services",
                        "cemetery": "Cemeteries (likely water)"}.get(cat, cat[7:].title() if cat.startswith("custom:") else cat)
                summary.append("%s: %d (first at km %.0f)" % (nice, len(pois), pois[0]["km"]))
        out_cats[cat] = info

    return {"ok": True, "total_km": round(total_km, 1), "categories": out_cats, "summary": summary}


# --- self test (offline) --------------------------------------------------------------------

def _synthetic_route():
    # ~100 km straight line; each 0.001 lat ~ 111 m so 900 pts ~ 100 km.
    return [{"lat": 45.0 + i * 0.001, "lon": 3.0} for i in range(900)]


def _synthetic_overpass(query: str) -> Dict[str, Any]:
    # water at ~km 0,10,92 (a big 82 km gap in the middle); food at ~km 5,50; a bike shop at km 60.
    def node(latoff, tags):
        return {"type": "node", "lat": 45.0 + latoff, "lon": 3.0, "tags": tags}
    els = [
        node(0.001, {"amenity": "drinking_water", "name": "Fountain A"}),
        node(0.090, {"amenity": "drinking_water", "name": "Fountain B"}),   # ~km 10
        node(0.826, {"amenity": "drinking_water", "name": "Fountain C"}),   # ~km 92
        node(0.045, {"shop": "supermarket", "name": "Carrefour"}),          # ~km 5
        node(0.450, {"shop": "bakery", "name": "Boulangerie"}),             # ~km 50
        node(0.540, {"shop": "bicycle", "name": "Cycles Pro"}),            # ~km 60
    ]
    return {"elements": els}


def _selftest():
    pts = _synthetic_route()
    r = analyze(pts, ["water", "food", "bike"], radius_m=300,
                water_l_per_100km=2.0, carry_l=1.5, fetch=_synthetic_overpass)
    assert r["ok"], r
    print("=== POIs + gaps (synthetic ~100 km) ===")
    for line in r["summary"]:
        print("  " + line)
    w = r["categories"]["water"]
    print("water pois:", [(p["name"], p["km"]) for p in w["pois"]])
    print("water longest gap:", w["longest_gap_km"], "km after km", w["longest_gap_after_km"],
          "→ needs", w["longest_gap_litres"], "L over-carry:", w["longest_gap_over_carry"])

    # 3 water fountains are still listed as water pins...
    assert len(w["pois"]) == 3, w["pois"]
    # ...but the REFILL gap now credits food too (water 0/10/92 + food 5/50), so the biggest gap is
    # km50 -> km92 = ~42 km, and 42 km * 2 L/100 = 0.84 L < 1.5 carried -> no longer over-carry.
    assert 38 <= w["longest_gap_km"] <= 46, w["longest_gap_km"]
    assert w["longest_gap_over_carry"] is False
    assert r["categories"]["food"]["count"] == 2
    assert r["categories"]["bike"]["count"] == 1
    assert abs(r["categories"]["bike"]["pois"][0]["km"] - 60) < 3
    print("\n✓ All race_pois selftest checks passed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="POIs + resupply gaps along a race route")
    parser.add_argument("input_file", nargs="?", help="JSON {gpx|points, categories, radius_m?, ...}")
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
        r = analyze(points or [], body.get("categories") or list(CATEGORIES.keys()),
                    radius_m=int(body.get("radius_m") or DEFAULT_RADIUS_M),
                    water_l_per_100km=float(body.get("water_l_per_100km") or 2.0),
                    carry_l=float(body.get("carry_l") or 1.5),
                    custom_tags=body.get("custom_tags"))
        print(json.dumps(r))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
