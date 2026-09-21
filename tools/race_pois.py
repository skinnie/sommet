#!/usr/bin/env python3
"""POIs along a race route, read from a PitStopper export, + the gap analysis that matters on a
brevet: "longest stretch with no refill: 24 km", "longest stretch with no food: 44 km", "bike shop
at km 312". Not a map full of pins - the distilled, actionable numbers.

PitStopper (pitstopper.net) already does the fast server-side spatial search; the rider exports its
POIs as a GPX with waypoints and we read that file. That is instant and offline - a live Overpass
search took 1-3 minutes on a 600 km route (André, 2026-09-21: "no one in 2026 will wait"), so it
was removed.

Each <wpt> carries a category in <cmt> ("water", "food", "coffee", "gas", ...) and, for loops and
out-and-backs, the route km(s) it sits at ("Outbound at 0.00km, Return at 599.77km"). We map those
to water / food / cemetery / sleep / bike / services, snap the rest to the nearest route point, and
compute per-category resupply gaps.

Realistic refill: a rider gets WATER not only at tagged fountains but at cafes, shops, fuel and
cemeteries too, so the "refill" gap credits every such source (reliability comes from realistic
assumptions, not OSM tag purity). Opening-hours / night-safe split: later (the <desc> often carries
"Hours: ...", which is kept on each POI for that).

Input (JSON, file or stdin):
  {"gpx"|"points": <the route>, "poi_gpx": <PitStopper GPX text>, "water_l_per_100km"?, "carry_l"?}
Output: {ok, total_km, imported, categories:{name:{pois:[{name,km,lat,lon,subtype,hours?}], count,
  first_km, longest_gap_km, longest_gap_after_km, ...}}, summary:[lines]}

Stdlib only. `--selftest` is offline.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import geo_util

# categories where gap analysis (resupply) is meaningful
RESUPPLY = {"water", "food"}


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


def _build_output(per_cat: Dict[str, List[Dict[str, Any]]], cats: List[str], total_km: float,
                  water_l_per_100km: float, carry_l: float) -> Dict[str, Any]:
    """Turn per-category POI lists into the result the UI reads: sorted pois, resupply-gap stats and
    the human summary lines."""
    out_cats: Dict[str, Any] = {}
    summary: List[str] = []
    for cat in cats:
        pois = sorted(per_cat.get(cat, []), key=lambda p: p["km"])
        info: Dict[str, Any] = {"pois": pois}
        if cat in RESUPPLY:
            # The "water" gap credits every refill source found (fountains + cemeteries + food/fuel),
            # not just water nodes. Food credits food + fuel.
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
                        "cemetery": "Cemeteries (likely water)"}.get(cat, cat)
                summary.append("%s: %d (first at km %.0f)" % (nice, len(pois), pois[0]["km"]))
        out_cats[cat] = info
    return {"ok": True, "total_km": round(total_km, 1), "categories": out_cats, "summary": summary}


# --- PitStopper GPX ------------------------------------------------------------------------------
# PitStopper <cmt> category -> ours.
_PS_CATEGORY = {
    "water": "water", "cemetery": "cemetery", "graveyard": "cemetery",
    # things that count as a place to refill/eat: cafe, restaurant, shop, fuel
    "food": "food", "coffee": "food", "gas": "food", "convenience_store": "food",
    "lodging": "shelter", "camping": "shelter",
    "bike_shop": "bike", "restroom": "safety", "hospital": "safety", "pharmacy": "safety",
    # deliberately ignored: bike_parking / bikeshare (noise). "shopping" is handled below: only
    # supermarkets count (its other shops aren't a place to refill).
}
_PS_SYM = {"Drinking Water": "water", "Restaurant": "food", "Gas Station": "food",
           "Convenience Store": "food", "Lodging": "shelter", "Campground": "shelter",
           "Restroom": "safety", "Car Repair": "bike"}


def _clean_name(name: str) -> str:
    """Undo PitStopper's name decorations: the "^" it prefixes to waypoints it moved onto the track,
    and the side+distance suffix ("... L20m" / "... R139m") added by "Add direction to name"."""
    n = name.strip().lstrip("^").strip()
    n = re.sub(r"\s+[LR]\d+\s*m?$", "", n).strip()
    return n


def parse_pitstopper_gpx(gpx_text: str) -> List[Dict[str, Any]]:
    """Read <wpt> elements -> [{lat, lon, name, cat, kms:[route km, ...], sub, hours}]. Unknown
    categories are skipped. `kms` is filled from PitStopper's own "at X.XXkm" notes when present
    (a loop passes the same POI more than once), else left empty and computed from the nearest route
    point."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(gpx_text)
    except ET.ParseError:
        return []
    out = []
    for w in root.iter():
        if not w.tag.endswith("}wpt") and w.tag != "wpt":
            continue

        def f(tag: str) -> str:
            for c in w:
                if c.tag.split("}")[-1] == tag:
                    return (c.text or "").strip()
            return ""
        cmt, sym, name, desc = f("cmt"), f("sym"), _clean_name(f("name")), f("desc")
        key = cmt.split(".")[0].strip().lower()
        if key == "shopping":
            cat = "food" if desc.lower().startswith("supermarket") else None
        elif key == "generic":
            # PitStopper exports the rider's CUSTOM TAGS as "generic". André's is cemetery/graveyard
            # (a likely water tap), so read them as cemeteries. The device-style <name> is truncated
            # ("Cimetière  R32m"); the real name is in the description ("Full name: ...").
            cat = "cemetery"
            fm = re.search(r"Full name:\s*(.+?)(?:\.\s*POI\s*$|$)", desc)
            if fm:
                name = fm.group(1).strip()
            elif not name or re.match(r"^POI\d*$", name):   # unnamed custom-tag POI ("POI1 R139m")
                name = "Cemetery"
        else:
            cat = _PS_CATEGORY.get(key) or _PS_SYM.get(sym)
        if not cat:
            continue
        kms = [float(x) for x in re.findall(r"at\s+([0-9]+(?:\.[0-9]+)?)\s*km", cmt)]
        hm = re.search(r"Hours:\s*(.+?)(?:\.\s+(?:Website|Phone)|$)", desc)
        out.append({"lat": float(w.get("lat")), "lon": float(w.get("lon")),
                    "name": name or key.title(), "cat": cat, "kms": kms, "sub": key,
                    "hours": hm.group(1).strip() if hm else ""})
    return out


def analyze_waypoints(points: List[Dict[str, Any]], wpts: List[Dict[str, Any]],
                      water_l_per_100km: float = 2.0, carry_l: float = 1.5) -> Dict[str, Any]:
    """Resupply gaps + per-category POI lists from imported PitStopper waypoints."""
    if len(points) < 2:
        return {"ok": False, "error": "route needs >= 2 points"}
    cumul_m = geo_util.cumulative_distances([(p["lat"], p["lon"]) for p in points])
    total_km = cumul_m[-1] / 1000.0
    cats = ["water", "food", "cemetery", "bike", "shelter", "safety"]
    per_cat: Dict[str, List[Dict[str, Any]]] = {c: [] for c in cats}
    seen = set()

    # Spatial grid over the route (0.01 deg ~ 1 km cells) so each waypoint only checks the handful
    # of route points near it instead of all of them - the naive scan took ~10 s on a 600 km route.
    CELL = 0.01
    grid: Dict[Tuple[int, int], List[int]] = {}
    for i, p in enumerate(points):
        grid.setdefault((int(p["lat"] / CELL), int(p["lon"] / CELL)), []).append(i)

    def nearest_km_fast(lat: float, lon: float) -> float:
        cx, cy = int(lat / CELL), int(lon / CELL)
        best_i, best_d = -1, float("inf")
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in grid.get((cx + dx, cy + dy), ()):
                    d = geo_util.haversine_m(lat, lon, points[i]["lat"], points[i]["lon"])
                    if d < best_d:
                        best_d, best_i = d, i
        if best_i < 0:                      # nothing in the neighbourhood: fall back to a full scan
            return _nearest_km(points, cumul_m, lat, lon)[0]
        return cumul_m[best_i] / 1000.0

    for w in wpts:
        # Placed at PitStopper's own route km(s) when it gave them (correct for loops/out-and-backs);
        # otherwise at the nearest point of our route.
        kms = w["kms"] or [round(nearest_km_fast(w["lat"], w["lon"]), 1)]
        for km in kms:
            key = (w["cat"], round(w["lat"], 5), round(w["lon"], 5), round(km, 1))
            if key in seen:
                continue
            seen.add(key)
            poi = {"name": w["name"], "km": round(km, 1), "lat": w["lat"], "lon": w["lon"],
                   "subtype": w["sub"]}
            if w.get("hours"):
                poi["hours"] = w["hours"]
            per_cat[w["cat"]].append(poi)
    return _build_output(per_cat, [c for c in cats if per_cat[c] or c in RESUPPLY],
                         total_km, water_l_per_100km, carry_l)


# --- self test (offline) --------------------------------------------------------------------

def _synthetic_route():
    # ~100 km straight line; each 0.001 lat ~ 111 m so 900 pts ~ 100 km.
    return [{"lat": 45.0 + i * 0.001, "lon": 3.0} for i in range(900)]


def _synthetic_gpx() -> str:
    def wpt(latoff, name, cmt, desc, sym="Restaurant", lon=3.0):
        return ('<wpt lat="%.6f" lon="%.6f"><name>%s</name><cmt>%s</cmt><desc>%s</desc>'
                '<sym>%s</sym></wpt>' % (45.0 + latoff, lon, name, cmt, desc, sym))
    return ('<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">' + "".join([
        # water at ~km 0,10,92; food/coffee at ~km 5,50; a supermarket ("shopping") at km 30
        wpt(0.001, "Fountain A", "water", "Fountain", "Drinking Water"),
        wpt(0.090, "Fountain B", "water", "Drinking Water", "Drinking Water"),
        wpt(0.826, "Fountain C", "water", "Fountain", "Drinking Water"),
        wpt(0.045, "Carrefour", "food", "Restaurants. Hours: 24/7. Website: x"),
        wpt(0.450, "Boulangerie", "coffee", "Cafes"),
        wpt(0.270, "Norma", "shopping", "Supermarkets", "Shopping Center"),
        wpt(0.271, "Clothes", "shopping", "Clothing", "Shopping Center"),      # ignored
        wpt(0.540, "Cycles Pro", "bike_shop", "Bicycle Repair", "Car Repair"),
        wpt(0.300, "Parking", "bike_parking", "Bicycle Parking", "Parking Area"),  # ignored
        # a custom-tag POI (PitStopper exports these as "generic"); real name is in the description
        wpt(0.600, "Cimetière  R32m", "generic", "Full name: Cimetière de Test. POI", "Dot"),
        wpt(0.601, "POI1 R10m", "generic", "POI", "Dot"),                       # unnamed custom-tag POI
        # a loop POI PitStopper says is at two route kms
        '<wpt lat="45.720000" lon="3.0"><name>Loop cafe</name>'
        '<cmt>coffee. Multi-pass POI: encountered 2 times. Outbound at 80.00km, Return at 99.00km</cmt>'
        '<desc>Cafes</desc><sym>Restaurant</sym></wpt>',
    ]) + '</gpx>')


def _selftest():
    pts = _synthetic_route()
    wpts = parse_pitstopper_gpx(_synthetic_gpx())
    cats = {w["name"]: w["cat"] for w in wpts}
    assert "Parking" not in cats and "Clothes" not in cats, cats      # noise + non-food shopping ignored
    assert cats["Norma"] == "food", cats                              # supermarkets DO count
    r = analyze_waypoints(pts, wpts, water_l_per_100km=2.0, carry_l=1.5)
    assert r["ok"], r
    print("=== POIs + gaps (synthetic ~100 km, from a PitStopper-style GPX) ===")
    for line in r["summary"]:
        print("  " + line)
    w = r["categories"]["water"]
    print("water pois:", [(p["name"], p["km"]) for p in w["pois"]])
    print("refill longest gap:", w["longest_gap_km"], "km after km", w["longest_gap_after_km"])

    assert len(w["pois"]) == 3, w["pois"]                              # fountains still listed as water
    # Refill gap credits food AND the cemetery: sources at ~0,5,10,30,50,60,80,92,99 -> biggest gap
    # is 20 km. WITHOUT the cemetery at km 60 it would be 30 km (50->80), which is the point of it.
    assert 18 <= w["longest_gap_km"] <= 22, w["longest_gap_km"]
    assert w["longest_gap_over_carry"] is False
    c = r["categories"]["cemetery"]
    assert c["count"] == 2, c
    assert c["pois"][0]["name"] == "Cimetière de Test", c["pois"]     # real name, not the truncated one
    f = r["categories"]["food"]
    assert f["count"] == 5, f["count"]        # Carrefour, Boulangerie, Norma, Loop cafe (x2 passes)
    hours = [p for p in f["pois"] if p.get("hours")]
    assert hours and hours[0]["hours"] == "24/7", hours                # opening hours kept for later
    assert sorted(p["km"] for p in f["pois"] if p["name"] == "Loop cafe") == [80.0, 99.0]  # both loop passes
    assert r["categories"]["bike"]["count"] == 1

    # PitStopper name decorations are stripped: "^" (waypoint moved onto the track) and the
    # side+distance suffix; a custom-tag POI exported with FULL names has no "Full name:" note.
    assert _clean_name("^Café de la Place L105m") == "Café de la Place"
    assert _clean_name("Grand vélo vert R166m") == "Grand vélo vert"
    assert _clean_name("Route de Lille") == "Route de Lille"           # not a suffix: kept
    gx = ('<gpx xmlns="http://www.topografix.com/GPX/1/1">'
          '<wpt lat="45.1" lon="3.0"><name>^Cimetière de Foo L12m</name><cmt>generic</cmt>'
          '<desc>POI</desc></wpt></gpx>')
    g = parse_pitstopper_gpx(gx)
    assert g and g[0]["name"] == "Cimetière de Foo" and g[0]["cat"] == "cemetery", g
    print("\n✓ All race_pois selftest checks passed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="POIs + resupply gaps from a PitStopper GPX")
    parser.add_argument("input_file", nargs="?", help="JSON {gpx|points, poi_gpx, water_l_per_100km?, carry_l?}")
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
        if not body.get("poi_gpx"):
            print(json.dumps({"ok": False, "error": "poi_gpx (a PitStopper GPX export) is required"}))
            return
        wpts = parse_pitstopper_gpx(body["poi_gpx"])
        r = analyze_waypoints(points or [], wpts,
                              water_l_per_100km=float(body.get("water_l_per_100km") or 2.0),
                              carry_l=float(body.get("carry_l") or 1.5))
        if r.get("ok"):
            r["imported"] = len(wpts)
        print(json.dumps(r))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
