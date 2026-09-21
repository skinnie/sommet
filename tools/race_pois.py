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
assumptions, not OSM tag purity). Opening hours: `open_refill_analysis` re-reads a result against
the rider's planned ETAs (the <desc> "Hours: ..." kept on each POI, else an assumption per type).

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
                # (No "next refill at km N" - it is always ~0 when the route starts in a town, so it
                # says nothing.) The litres check lives in the UI, where it recalculates live as the
                # rider edits the drink / carry numbers; the numbers are still returned for alerts.
                summary.append("Longest stretch with no %s: %.0f km (after km %.0f)"
                               % (short, g["longest_gap_km"], g["longest_gap_after_km"]))
        elif cat == "other":
            # Not "Other places: 108" - say WHAT they are (e.g. "Bicycle Parking 100 · Bicycle Rental 8").
            info["count"] = len(pois)
            kinds: Dict[str, int] = {}
            for p_ in pois:
                kinds[p_.get("kind") or p_.get("subtype") or "Other"] = kinds.get(p_.get("kind") or p_.get("subtype") or "Other", 0) + 1
            info["kinds"] = kinds
            if pois:
                top = sorted(kinds.items(), key=lambda kv: -kv[1])
                txt = " · ".join("%s %d" % (k, n) for k, n in top[:4])
                if len(top) > 4:
                    txt += " · +%d more types" % (len(top) - 4)
                summary.append("Also on the route: " + txt)
        else:
            info["count"] = len(pois)
            if pois:
                nice = {"bike": "Bike shop/repair", "shelter": "Accommodation", "safety": "Services",
                        "cemetery": "Cemeteries (likely water)", "other": "Other places"}.get(cat, cat)
                summary.append("%s: %d" % (nice, len(pois)))
        out_cats[cat] = info
    return {"ok": True, "total_km": round(total_km, 1), "categories": out_cats, "summary": summary}


# --- PitStopper GPX ------------------------------------------------------------------------------
# PitStopper writes ONE of 25 <cmt> keys per waypoint (read from pitstopper.net's own script on
# 2026-09-21; its internal ~90 categories in 12 groups collapse onto these keys). We keep EVERY
# waypoint - a rider may export any of them - and only decide (a) which of our categories it feeds
# and (b) whether it is a "cyclist" type (PitStopper's Cyclist preset), which is what gets its own
# map icon for now.
#
#   PitStopper groups: Water, Food & Drink, Accommodation, Transport, Cycling, Services, Shopping,
#   Amenities, Recreation, Historical, Emergency, Weather Shelter (+ the rider's Custom Tags).
#   Cyclist preset = drinking_water, water_point, water_tap, spring, fountain, watering_place, fuel,
#   cafe, restaurant, toilet, bicycle_parking, bicycle_repair, bicycle_rental, convenience.
#
# <cmt> key -> our category. Anything not listed (and every shop that isn't a supermarket) is kept as
# "other": shown on request, never used for the refill/food gaps.
_PS_CATEGORY = {
    "water": "water",
    # a place to refill/eat: cafe, restaurant, convenience store, fuel ("shopping" -> see supermarket rule)
    "food": "food", "coffee": "food", "bar": "food", "gas": "food", "convenience_store": "food",
    "lodging": "shelter", "camping": "shelter",
    "bike_shop": "bike",
    "restroom": "safety", "shower": "safety", "hospital": "safety", "first_aid": "safety", "atm": "safety",
}
# <cmt> key -> the PitStopper group it belongs to (for the "what's in this file" summary).
_PS_GROUP = {
    "water": "Water", "food": "Food & Drink", "coffee": "Food & Drink", "bar": "Food & Drink",
    "gas": "Food & Drink", "lodging": "Accommodation", "camping": "Accommodation",
    "transit": "Transport", "parking": "Transport", "caution": "Transport",
    "bike_parking": "Cycling", "bikeshare": "Cycling", "bike_shop": "Cycling",
    "hospital": "Emergency", "first_aid": "Emergency", "atm": "Services",
    "shopping": "Shopping", "convenience_store": "Shopping",
    "restroom": "Amenities", "shower": "Amenities", "rest_stop": "Amenities",
    "viewpoint": "Recreation", "park": "Recreation", "swimming": "Recreation",
    "generic": "Other",
}
_CYCLIST_KEYS = {"water", "gas", "coffee", "restroom", "bike_parking", "bike_shop", "bikeshare",
                 "convenience_store"}
_PS_SYM = {"Drinking Water": "water", "Restaurant": "food", "Gas Station": "food",
           "Convenience Store": "food", "Lodging": "shelter", "Campground": "shelter",
           "Restroom": "safety", "Car Repair": "bike"}


_SUPERMARKET_WORDS = ("supermarket", "supermarch", "supermerc", "supermarkt", "supermärkt", "grocer")


def _generic_category(kind: str) -> str:
    k = kind.lower()
    if k.startswith(("alpine hut", "wilderness hut")):
        return "shelter"                      # somewhere to sleep
    if k.startswith("motorway service"):
        return "food"                         # fuel + food + toilets
    if "repair station" in k or k.startswith("compressed air"):
        return "bike"
    if k.startswith(("first aid", "emergency phone", "emergency ward", "lifeguard", "mountain rescue")):
        return "safety"
    return "other"


def _is_placeholder_name(name: str, kind: str) -> bool:
    """PitStopper names an UNNAMED place after its type, cut to 10 chars for bike computers:
    "drinking_w", "guest_hou", "bicycle_pa", "toilets", "Spring1". Real names never look like a
    shortened type, so such a name should be replaced by the proper type ("Drinking Water")."""
    if not name or not kind:
        return False
    n = name.strip()
    if re.fullmatch(r"[a-z]+(?:_[a-z]+)+\d*", n):        # a raw OSM value ("camp_site", "drinking_water")
        return True
    norm = re.sub(r"\d+$", "", n).lower().replace(" ", "_")
    snake = kind.strip().lower().replace(" ", "_")
    # the type itself ("Spring1"), its plural ("toilets"), or a cut-off of it ("drinking_w") - but NOT a
    # real name that merely starts with the type ("Fountain A")
    return bool(norm) and (norm in (snake, snake + "s") or snake.startswith(norm))


def _is_cyclist(key: str, kind: str) -> bool:
    """Is this one of PitStopper's Cyclist-preset types? ("food" covers restaurants AND fast food /
    bakeries, only the first is in the preset; "water" excludes non-potable and bottle-refill.)"""
    k = kind.lower()
    if key == "water":
        return not (("non" in k and "potable" in k) or "refill" in k)
    if key == "food":
        return k.startswith("restaurant")
    return key in _CYCLIST_KEYS


def _clean_name(name: str) -> str:
    """Undo PitStopper's name decorations: the "^" it prefixes to waypoints it moved onto the track,
    and the side+distance suffix ("... L20m" / "... R139m") added by "Add direction to name"."""
    n = name.strip().lstrip("^").strip()
    n = re.sub(r"\s+[LR]\d+\s*m?$", "", n).strip()
    return n


# What the rider's PitStopper CUSTOM TAG means. PitStopper does not export the tag's name (every custom
# place arrives as <cmt>generic</cmt> + description "POI"), so it is a setting: {value: (our category,
# the type shown to the rider)}. André's tag is cemetery (a likely water tap), hence the default.
CUSTOM_TAGS = {"cemetery": ("cemetery", "Cemetery"), "water": ("water", "Water"),
               "food": ("food", "Food or drink"), "other": ("other", "Custom place")}


def parse_pitstopper_gpx(gpx_text: str, custom_tag: str = "cemetery") -> List[Dict[str, Any]]:
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

        # The description is "[Full name: X. ]Type[. Hours: ...][. Website: ...][. Phone: ...]".
        # "Full name" is the real, untruncated name (present whenever PitStopper shortened the
        # <name> for a bike computer); the type is the last part before the Hours/Website/Phone tail.
        core = re.split(r"\.\s*(?:Hours|Website|Phone):", desc, maxsplit=1)[0].strip().rstrip(".")
        full, kind = "", core
        if core.startswith("Full name:"):
            rest = core[len("Full name:"):].strip()
            if ". " in rest:
                full, kind = rest.rsplit(". ", 1)
            else:
                full, kind = rest, ""
        full, kind = full.strip(), kind.strip()

        is_custom = False
        if key == "shopping":
            # The type name follows PitStopper's interface language, so accept the main spellings:
            # supermarket / supermarché(s) / supermercado(s) / supermercato-i / Supermarkt.
            cat = "food" if kind.lower().startswith(_SUPERMARKET_WORDS) else "other"
        elif key == "generic":
            # "generic" is shared by the rider's CUSTOM TAGS and PitStopper's own catch-all types (post
            # office, castle, monument, EV charging...). A custom tag's description is just "POI"; the
            # built-in ones carry their type name. André's custom tag is cemetery/graveyard (a likely
            # water tap), so custom tags are read as cemeteries - and a castle is NOT a cemetery.
            if kind == "POI":
                cat, kind = CUSTOM_TAGS.get(custom_tag, CUSTOM_TAGS["cemetery"])
                is_custom = True
            else:
                # 18 of PitStopper's ~87 types have no export label of their own and arrive as
                # "generic" (beer gardens, rest areas, motorway services, ferry terminals, toll booths,
                # bike repair stations, compressed air, motorcycle types, mountain passes, peaks,
                # huts, and four emergency types). They are named in the description, so the few that
                # matter to a rider are recognised by that name (English); the rest stay "other".
                cat = _generic_category(kind)
        else:
            cat = _PS_CATEGORY.get(key) or _PS_SYM.get(sym) or "other"
            # PitStopper files "non-potable water" under the same "water" label as drinking water; it
            # must NOT count as a refill (it would wrongly shorten the dry stretches).
            if key == "water" and "non" in kind.lower() and "potable" in kind.lower():
                cat = "other"
        if full:
            name = full
        elif is_custom and (not name or re.match(r"^POI\d*$", name)):   # unnamed ("POI1 R139m")
            name = CUSTOM_TAGS.get(custom_tag, CUSTOM_TAGS["cemetery"])[1]
        use_kind_as_name = (not full) and key != "generic" and _is_placeholder_name(name, kind)
        kind = re.split(r"\s+-\s+(?:Outbound|Return|Pass)\b", kind)[0].strip()   # loop-pass note
        if kind.endswith("s") and not kind.endswith("ss") and len(kind) > 3:
            kind = kind[:-1]                                                    # "Guest Houses" -> "Guest House"
        if use_kind_as_name:
            name = kind                          # unnamed place: show its type, not "drinking_w"
        kms = [float(x) for x in re.findall(r"at\s+([0-9]+(?:\.[0-9]+)?)\s*km", cmt)]
        hm = re.search(r"Hours:\s*(.+?)(?:\.\s+(?:Website|Phone)|$)", desc)
        group = "Custom tag" if is_custom else _PS_GROUP.get(key, "Other")
        out.append({"lat": float(w.get("lat")), "lon": float(w.get("lon")),
                    "name": name or key.title(), "cat": cat, "kms": kms, "sub": key,
                    "kind": kind, "group": group, "cyclist": _is_cyclist(key, kind),
                    "hours": hm.group(1).strip() if hm else ""})
    return out


def analyze_waypoints(points: List[Dict[str, Any]], wpts: List[Dict[str, Any]],
                      water_l_per_100km: float = 2.0, carry_l: float = 1.5,
                      mirror_kms: bool = False) -> Dict[str, Any]:
    """Resupply gaps + per-category POI lists from imported PitStopper waypoints.

    `points` is the route in the direction being planned. When that is the REVERSE of the direction
    the export was made in, pass mirror_kms=True: the route km(s) PitStopper wrote down ("Outbound at
    80 km") are measured in its own direction, so they become total - km; POIs without them are
    simply snapped to the (already reversed) `points`."""
    if len(points) < 2:
        return {"ok": False, "error": "route needs >= 2 points"}
    cumul_m = geo_util.cumulative_distances([(p["lat"], p["lon"]) for p in points])
    total_km = cumul_m[-1] / 1000.0
    cats = ["water", "food", "cemetery", "bike", "shelter", "safety", "other"]
    per_cat: Dict[str, List[Dict[str, Any]]] = {c: [] for c in cats}
    seen = set()
    groups: Dict[str, int] = {}
    for w in wpts:
        groups[w.get("group", "Other")] = groups.get(w.get("group", "Other"), 0) + 1

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
        given = [total_km - k for k in w["kms"]] if mirror_kms else w["kms"]
        kms = given or [round(nearest_km_fast(w["lat"], w["lon"]), 1)]
        for km in kms:
            key = (w["cat"], round(w["lat"], 5), round(w["lon"], 5), round(km, 1))
            if key in seen:
                continue
            seen.add(key)
            poi = {"name": w["name"], "km": round(km, 1), "lat": w["lat"], "lon": w["lon"],
                   "subtype": w["sub"], "kind": w.get("kind", ""),
                   "group": w.get("group", "Other"), "cyclist": bool(w.get("cyclist"))}
            if w.get("hours"):
                poi["hours"] = w["hours"]
            per_cat[w["cat"]].append(poi)
    out = _build_output(per_cat, [c for c in cats if per_cat[c] or c in RESUPPLY],
                        total_km, water_l_per_100km, carry_l)
    out["groups"] = groups          # what the file holds, by PitStopper group (all kept)
    return out


# --- opening hours: which refill points are actually open when you get there ---------------------
# André, 2026-09-21: a shop that is shut at 03:00 is not a refill. The rider's planned ETA at each
# POI is interpolated between the timeline's controls, and each refill source is checked against its
# OSM "Hours:" text (kept on the POI). NO GUESSING (André): a place without published hours is "unknown",
# never assumed open or shut - the result shows both the confirmed-open and the maybe-open picture.
# The only exception is outdoor water (a fountain/tap has no opening hours). Checked 2026-09-21: the
# export already carries every opening_hours OSM has (re-querying 150 no-hours shops on Overpass by id
# found none), so there is no better free source. Public holidays are NOT considered.

_DAYS = ["mo", "tu", "we", "th", "fr", "sa", "su"]
def parse_hours(text: str):
    """OSM opening_hours (common subset) -> f(weekday 0=Mon, minute-of-day) -> True/False, or None if
    the text is not understood. Handles "24/7", "Mo-Sa 08:30-19:30; Su off", "Mo-Fr 07:00-12:00,14:00-19:00",
    overnight ranges and "off". Later rules override earlier ones for the days they name."""
    t = (text or "").strip()
    if not t:
        return None
    if t.lower() in ("24/7", "24h"):
        return lambda wd, m: True
    week: List[Optional[List[Tuple[int, int]]]] = [None] * 7      # None = no rule mentions this day
    for rule in re.split(r"\s*;\s*", t):
        rule = rule.strip()
        if not rule:
            continue
        d_ = "(?:Mo|Tu|We|Th|Fr|Sa|Su)"
        mm = re.match(r"^((?:%s(?:\s*-\s*%s)?\s*,?\s*)*)(.*)$" % (d_, d_), rule, re.I)
        dayspec, rest = mm.group(1).strip(), mm.group(2).strip()
        days: List[int] = []
        if dayspec:
            for part in re.split(r"\s*,\s*", dayspec.strip(", ")):
                ab = [x.strip().lower()[:2] for x in part.split("-")]
                if not all(x in _DAYS for x in ab):
                    return None
                a = _DAYS.index(ab[0]); b = _DAYS.index(ab[-1])
                days += list(range(a, b + 1)) if a <= b else list(range(a, 7)) + list(range(0, b + 1))
        else:
            days = list(range(7))
        if re.fullmatch(r"(?i)off|closed", rest):
            spans: List[Tuple[int, int]] = []
        else:
            spans = []
            for a, b in re.findall(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", rest):
                h1, m1 = a.split(":"); h2, m2 = b.split(":")
                spans.append((int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)))
            if not spans:
                return None
        for d in days:
            week[d] = spans

    def is_open(wd: int, minute: int) -> bool:
        for back, day in ((0, wd), (1, (wd - 1) % 7)):
            for a, b in (week[day] or []):
                if b > a:
                    if back == 0 and a <= minute < b:
                        return True
                else:                                   # overnight (22:00-02:00): spills into the next day
                    if back == 0 and minute >= a:
                        return True
                    if back == 1 and minute < b:
                        return True
        return False
    return is_open


def _eta_at(km: float, anchors: List[Tuple[float, Any]]):
    """Planned clock time at route km: linear between the timeline's (km, datetime) anchors."""
    if km <= anchors[0][0]:
        return anchors[0][1]
    for (k0, t0), (k1, t1) in zip(anchors, anchors[1:]):
        if km <= k1:
            f = 0.0 if k1 == k0 else (km - k0) / (k1 - k0)
            return t0 + (t1 - t0) * f
    return anchors[-1][1]


def refill_open_status(poi: Dict[str, Any], cat: str, when) -> str:
    """"open" | "closed" | "unknown" for a refill source at `when`. Only published hours decide; outdoor
    water (fountains, taps, springs) has no hours and counts as open."""
    fn = parse_hours(poi.get("hours", ""))
    if fn is not None:
        return "open" if fn(when.weekday(), when.hour * 60 + when.minute) else "closed"
    return "open" if cat == "water" else "unknown"


def open_refill_analysis(result: Dict[str, Any], eta: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Refill / food gaps counting only the places that are open at the rider's ETA.
    `result` = an analyze_waypoints output; `eta` = [{km, dt}] (local naive ISO), e.g. start + each
    control. Returns {ok, lines, refill:{...}, food:{...}, closed, total}."""
    from datetime import datetime
    anchors = sorted(((float(a["km"]), datetime.fromisoformat(str(a["dt"])[:19])) for a in eta if a.get("dt")),
                     key=lambda x: x[0])
    if len(anchors) < 2:
        return {"ok": False, "error": "need at least two ETA points (start + a control)"}
    total_km = float(result.get("total_km") or anchors[-1][0])
    cats = result.get("categories") or {}

    def collect(cat_names: List[str]):
        confirmed, maybe, n = [], [], {"open": 0, "closed": 0, "unknown": 0}
        for c in cat_names:
            for p in (cats.get(c) or {}).get("pois", []):
                st = refill_open_status(p, c, _eta_at(p["km"], anchors))
                n[st] += 1
                if st == "open":
                    confirmed.append(p["km"])
                if st != "closed":
                    maybe.append(p["km"])
        return confirmed, maybe, n

    c_ref, m_ref, n_ref = collect(["water", "cemetery", "food"])
    c_food, m_food, n_food = collect(["food"])
    g_ref, gm_ref = _gaps(c_ref, total_km), _gaps(m_ref, total_km)
    g_food, gm_food = _gaps(c_food, total_km), _gaps(m_food, total_km)
    lines = []

    def at(km):
        return _eta_at(km, anchors).strftime("%a %H:%M")
    tot = sum(n_ref.values())
    if tot:
        lines.append("At your planned times: longest stretch with no CONFIRMED-open refill %.0f km (after km %.0f, ~%s); "
                     "if places with unknown hours are open, %.0f km. Of %d refill points: %d open, %d closed, %d unknown hours."
                     % (g_ref["longest_gap_km"], g_ref["longest_gap_after_km"], at(g_ref["longest_gap_after_km"]),
                        gm_ref["longest_gap_km"], tot, n_ref["open"], n_ref["closed"], n_ref["unknown"]))
    if sum(n_food.values()):
        lines.append("Food: %.0f km with none confirmed open (after km %.0f, ~%s); %.0f km if unknown-hours places are open."
                     % (g_food["longest_gap_km"], g_food["longest_gap_after_km"], at(g_food["longest_gap_after_km"]),
                        gm_food["longest_gap_km"]))
    return {"ok": True, "lines": lines, "refill": g_ref, "refill_if_unknown_open": gm_ref,
            "food": g_food, "food_if_unknown_open": gm_food, "counts": n_ref,
            "note": "Opening hours are OpenStreetMap's, where published (none are guessed); public holidays are not considered."}


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
        # truncated <name>, real name in the description - even when the name itself contains ". "
        wpt(0.280, "Centre Co R115m", "shopping",
            "Full name: Centre Commercial E. Leclerc. Supermarkets. Hours: Mo-Sa 08:30-19:30; Su off. "
            "Website: https://x. Phone: +33", "Shopping Center"),
        wpt(0.271, "Clothes", "shopping", "Clothing", "Shopping Center"),      # ignored
        wpt(0.540, "Cycles Pro", "bike_shop", "Bicycle Repair", "Car Repair"),
        wpt(0.300, "Parking", "bike_parking", "Bicycle Parking", "Parking Area"),  # ignored
        # a BUILT-IN generic type (castle): shares the "generic" key with custom tags but is not one
        wpt(0.350, "Château de T R20m", "generic", "Full name: Château de Test. Castles", "Dot"),
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
    # EVERYTHING is kept now; what isn't refill/food/etc. goes to "other" (never used for the gaps)
    assert cats["Parking"] == "other" and cats["Clothes"] == "other", cats
    assert cats["Château de Test"] == "other", cats                   # a castle is NOT a cemetery
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
    assert c["count"] == 2, c                                          # custom tags only, not the castle
    oth = {p["name"]: p for p in r["categories"]["other"]["pois"]}
    assert set(oth) == {"Parking", "Clothes", "Château de Test"}, set(oth)
    assert oth["Parking"]["cyclist"] is True and oth["Clothes"]["cyclist"] is False, oth
    assert oth["Château de Test"]["kind"] == "Castle" and oth["Château de Test"]["group"] == "Other", oth
    assert r["groups"]["Water"] == 3 and r["groups"]["Custom tag"] == 2, r["groups"]
    assert c["pois"][0]["name"] == "Cimetière de Test", c["pois"]     # real name, not the truncated one
    f = r["categories"]["food"]
    assert f["count"] == 6, f["count"]        # Carrefour, Boulangerie, Norma, Leclerc, Loop cafe (x2 passes)
    hours = [p for p in f["pois"] if p.get("hours")]
    assert hours and hours[0]["hours"] == "24/7", hours                # opening hours kept for later
    assert sorted(p["km"] for p in f["pois"] if p["name"] == "Loop cafe") == [80.0, 99.0]  # both loop passes
    assert r["categories"]["bike"]["count"] == 1

    # Reversed route: the loop POI PitStopper put at 80 / 99 km is at total-80 / total-99, and a POI
    # snapped to the route moves to total-km.
    rev = analyze_waypoints(list(reversed(pts)), wpts, mirror_kms=True)
    total = rev["total_km"]
    loop = sorted(p["km"] for p in rev["categories"]["food"]["pois"] if p["name"] == "Loop cafe")
    assert loop == sorted([round(total - 99.0, 1), round(total - 80.0, 1)]), (loop, total)
    fa = next(p for p in r["categories"]["water"]["pois"] if p["name"] == "Fountain B")["km"]
    fb = next(p for p in rev["categories"]["water"]["pois"] if p["name"] == "Fountain B")["km"]
    assert abs((total - fa) - fb) < 0.5, (fa, fb, total)
    assert abs(rev["categories"]["water"]["longest_gap_km"] - r["categories"]["water"]["longest_gap_km"]) < 1.0

    # Supermarkets are recognised in the main PitStopper interface languages (they count as a refill)
    for word in ("Supermarkets", "Supermarchés", "Supermercados", "Supermercati", "Supermärkte"):
        gx = ('<gpx xmlns="http://www.topografix.com/GPX/1/1"><wpt lat="45.1" lon="3.0"><name>Shop</name>'
              '<cmt>shopping</cmt><desc>%s</desc></wpt></gpx>' % word)
        assert parse_pitstopper_gpx(gx)[0]["cat"] == "food", word

    # Non-potable water is not a refill; grocery stores are.
    nx = ('<gpx xmlns="http://www.topografix.com/GPX/1/1">'
          '<wpt lat="45.1" lon="3.0"><name>a</name><cmt>water</cmt><desc>Non-potable Water</desc></wpt>'
          '<wpt lat="45.2" lon="3.0"><name>b</name><cmt>shopping</cmt><desc>Grocery Stores</desc></wpt></gpx>')
    nxp = parse_pitstopper_gpx(nx)
    assert nxp[0]["cat"] == "other" and nxp[1]["cat"] == "food", nxp

    # The human type is kept (singularised) for the map label.
    kinds = {p["name"]: p.get("kind") for cat in r["categories"].values() for p in cat["pois"]}
    assert kinds["Fountain A"] == "Fountain" and kinds["Carrefour"] == "Restaurant", kinds
    assert kinds["Norma"] == "Supermarket" and kinds["Cimetière de Test"] == "Cemetery", kinds
    # real name + type + opening hours recovered from a "Full name: ..." description
    lec = next(p for p in r["categories"]["food"]["pois"] if p["name"].startswith("Centre Commercial"))
    assert lec["name"] == "Centre Commercial E. Leclerc" and lec["kind"] == "Supermarket", lec
    assert lec["hours"] == "Mo-Sa 08:30-19:30; Su off", lec

    # unnamed places are shown by their type, not PitStopper's 10-char raw type
    assert _is_placeholder_name("drinking_w", "Drinking Water") and _is_placeholder_name("Spring1", "Spring")
    assert _is_placeholder_name("toilets", "Toilet") and _is_placeholder_name("guest_hou", "Guest House")
    assert not _is_placeholder_name("Café de la Place", "Cafe") and not _is_placeholder_name("Le Spring Bar", "Spring")

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
    # custom tag is a setting; pubs are a refill
    gw = parse_pitstopper_gpx(gx, custom_tag="water")
    assert gw[0]["cat"] == "water" and gw[0]["group"] == "Custom tag", gw
    assert parse_pitstopper_gpx(gx, custom_tag="other")[0]["cat"] == "other"
    pub = parse_pitstopper_gpx('<gpx><wpt lat="1" lon="1"><name>Le Pub</name><cmt>bar</cmt><desc>Pub</desc></wpt></gpx>')
    assert pub[0]["cat"] == "food", pub
    # opening hours
    from datetime import datetime
    h = parse_hours("Mo-Sa 08:30-19:30; Su off")
    assert h(0, 9 * 60) and not h(0, 20 * 60) and not h(6, 12 * 60)      # Mon 09:00 open, Mon 20:00 shut, Sun shut
    assert parse_hours("24/7")(3, 3 * 60)
    n = parse_hours("Mo-Su 22:00-02:00")
    assert n(2, 23 * 60) and n(3, 60) and not n(3, 3 * 60)               # overnight spills into next day
    assert parse_hours("Mo-Fr 07:00-12:00,14:00-19:00")(1, 15 * 60) and not parse_hours("Mo-Fr 07:00-12:00,14:00-19:00")(1, 13 * 60)
    assert parse_hours("sunrise-sunset") is None
    # night: outdoor water stays a refill; a shop with NO hours is "unknown" (never guessed); a shop
    # whose hours say shut is "closed"
    fake = {"total_km": 100.0, "categories": {
        "water": {"pois": [{"km": 10.0, "subtype": "water"}]},
        "food": {"pois": [{"km": 50.0, "subtype": "food"}, {"km": 60.0, "subtype": "food", "hours": "Mo-Su 08:00-20:00"},
                          {"km": 90.0, "subtype": "food", "hours": "24/7"}]}}}
    eta = [{"km": 0, "dt": "2026-09-19T20:00:00"}, {"km": 100, "dt": "2026-09-20T06:00:00"}]
    r = open_refill_analysis(fake, eta)
    assert r["counts"] == {"open": 2, "closed": 1, "unknown": 1}, r["counts"]
    assert abs(r["refill"]["longest_gap_km"] - 80.0) < 0.1, r                 # confirmed: fountain km10 -> shop km90
    assert abs(r["refill_if_unknown_open"]["longest_gap_km"] - 40.0) < 0.1, r  # if the unknown km50 shop is open
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
        if body.get("pois") and body.get("eta"):         # re-read of a finished result against the ETAs
            print(json.dumps(open_refill_analysis(body["pois"], body["eta"])))
            return
        points = body.get("points")
        if not points and body.get("gpx"):
            points = geo_util.parse_gpx_points(body["gpx"])
        if not body.get("poi_gpx"):
            print(json.dumps({"ok": False, "error": "poi_gpx (a PitStopper GPX export) is required"}))
            return
        wpts = parse_pitstopper_gpx(body["poi_gpx"], custom_tag=str(body.get("custom_tag") or "cemetery"))
        rev = bool(body.get("reverse"))
        if rev:
            points = list(reversed(points or []))     # plan the route the other way round
        r = analyze_waypoints(points or [], wpts,
                              water_l_per_100km=float(body.get("water_l_per_100km") or 2.0),
                              carry_l=float(body.get("carry_l") or 1.5),
                              mirror_kms=rev)
        if r.get("ok"):
            r["imported"] = len(wpts)
        print(json.dumps(r))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
