#!/usr/bin/env python3
"""The POI library behind the POIs page (André, 2026-11-22: "we have a library for the routes
but not for the POIs... for me these two should be similar").

Sommet's library is one thing and what is on a watch is another: a POI kept here stays here
whatever happens on a watch, and nothing here changes a watch until you send a POI to it.

    ./tools/poi_library.py list   <dir> [ambit|traverse]
    ./tools/poi_library.py save   <dir> <name> <lat> <lon> [type] [ambit|traverse] [ele]
    ./tools/poi_library.py rename <dir> <id> <new name>
    ./tools/poi_library.py delete <dir> <id>

Storage: one file, <dir>/index.json (the backend passes ~/SommetBackups/Pois):
    {"version": 1,
     "pois":    [{id, name, lat, lon, ele, type, typeList, created, updated}],
     "deleted": [{id, at}]}
- `type` is the POI type number (the icon) and `typeList` says which list it is a number in:
  "ambit" (the Ambit3 family's 18) or "traverse" (the 48 of a Traverse / Traverse Alpha, which an
  Ambit1/2 also stores). The two number their types differently, so a POI is sent to a watch of
  the other kind by its icon (ambit_format.waypoint_type_between).
- `ele` is metres or null. An Ambit3 POI has no altitude; a Traverse keeps it.
- `deleted` remembers what was removed, for the day the library is shared between devices.
Saving the same POI again (same name, within a few metres) keeps one. stdlib only.
"""

import json
import math
import os
import sys
import tempfile
import threading
import time
import uuid

import ambit_format

INDEX = "index.json"
SAME_PLACE_M = 10.0
LISTS = ("ambit", "traverse")
# One model of each kind, for ambit_format.waypoint_type_between (it takes model names).
_MODEL_OF = {"ambit": "Emu", "traverse": "Loon"}
_LOCK = threading.Lock()          # the backend serves each request on its own thread


def _load(d):
    try:
        with open(os.path.join(d, INDEX), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    pois = data.get("pois")
    deleted = data.get("deleted")
    return {"version": 1,
            "pois": pois if isinstance(pois, list) else [],
            "deleted": deleted if isinstance(deleted, list) else []}


def _store(d, data):
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(d, INDEX))


def _metres(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    x = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * 6371000 * math.asin(min(1.0, math.sqrt(x)))


def _same(p, name, lat, lon):
    # lower(), not casefold(): the Android twin has only toLowerCase(), and the two must agree.
    return (p.get("name", "").strip().lower() == name.lower()
            and _metres(p["lat"], p["lon"], lat, lon) <= SAME_PLACE_M)


def plain_type(type_list):
    return (ambit_format.TRAVERSE_WAYPOINT_TYPE_DEFAULT if type_list == "traverse"
            else ambit_format.WAYPOINT_TYPE_DEFAULT)


def type_in(type_id, from_list, to_list):
    """The type number that draws this POI's icon in the other kind of watch's list."""
    return ambit_format.waypoint_type_between(type_id, _MODEL_OF[from_list], _MODEL_OF[to_list])


def _clean(name, lat, lon, ele, type_id, type_list):
    """Checked values for one POI, or raises ValueError with words for the user."""
    name = str(name or "").strip()
    if not name:
        raise ValueError("a POI needs a name")
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise ValueError("the position is not a number") from None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("that position is not a place on Earth")
    if type_list not in LISTS:
        type_list = "ambit"
    known = (ambit_format.TRAVERSE_WAYPOINT_TYPES if type_list == "traverse"
             else ambit_format.WAYPOINT_TYPES)
    try:
        type_id = int(type_id)
    except (TypeError, ValueError):
        type_id = plain_type(type_list)
    if type_id not in known:
        type_id = plain_type(type_list)
    try:
        ele = None if ele is None or ele == "" else float(ele)
    except (TypeError, ValueError):
        ele = None
    if ele is not None and not math.isfinite(ele):
        ele = None
    return name, round(lat, 7), round(lon, 7), ele, type_id, type_list


def list_pois(d, for_list=None):
    """Every POI, by name. With for_list ("ambit" / "traverse") each also carries `typeHere`:
    its type as a number in that kind of watch's list, ready to send."""
    with _LOCK:
        pois = _load(d)["pois"]
    out = []
    for p in sorted(pois, key=lambda x: (str(x.get("name", "")).lower(), x.get("created", 0))):
        p = dict(p)
        if for_list in LISTS:
            p["typeHere"] = type_in(p.get("type"), p.get("typeList", "ambit"), for_list)
        out.append(p)
    return out


def save_pois(d, items):
    """Keep each of `items` ({name, lat, lon, ele?, type?, typeList?}). Returns one result per
    item, in order: {ok, id, existed} or {ok: False, error}. One already kept (same name, within
    SAME_PLACE_M) is left as it is."""
    results = []
    with _LOCK:
        data = _load(d)
        changed = False
        for item in items:
            try:
                name, lat, lon, ele, type_id, type_list = _clean(
                    item.get("name"), item.get("lat"), item.get("lon"), item.get("ele"),
                    item.get("type"), item.get("typeList"))
            except ValueError as exc:
                results.append({"ok": False, "error": str(exc)})
                continue
            have = next((p for p in data["pois"] if _same(p, name, lat, lon)), None)
            if have is not None:
                results.append({"ok": True, "id": have["id"], "existed": True})
                continue
            now = int(time.time())
            entry = {"id": uuid.uuid4().hex, "name": name, "lat": lat, "lon": lon, "ele": ele,
                     "type": type_id, "typeList": type_list, "created": now, "updated": now}
            data["pois"].append(entry)
            changed = True
            results.append({"ok": True, "id": entry["id"], "existed": False})
        if changed:
            _store(d, data)
    return results


def save_poi(d, name, lat, lon, ele=None, type_id=None, type_list="ambit"):
    return save_pois(d, [{"name": name, "lat": lat, "lon": lon, "ele": ele,
                          "type": type_id, "typeList": type_list}])[0]


def rename_poi(d, poi_id, name):
    name = str(name or "").strip()
    if not name:
        return {"ok": False, "error": "a POI needs a name"}
    with _LOCK:
        data = _load(d)
        for p in data["pois"]:
            if p.get("id") == poi_id:
                p["name"] = name
                p["updated"] = int(time.time())
                _store(d, data)
                return {"ok": True}
    return {"ok": False, "error": "that POI is no longer in the library"}


def delete_poi(d, poi_id):
    with _LOCK:
        data = _load(d)
        keep = [p for p in data["pois"] if p.get("id") != poi_id]
        if len(keep) == len(data["pois"]):
            return {"ok": False, "error": "that POI is no longer in the library"}
        data["pois"] = keep
        data["deleted"].append({"id": poi_id, "at": int(time.time())})
        _store(d, data)
    return {"ok": True}


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    action, d, rest = argv[1], argv[2], argv[3:]
    if action == "list":
        out = {"ok": True, "pois": list_pois(d, rest[0] if rest else None)}
    elif action == "save" and len(rest) >= 3:
        out = save_poi(d, rest[0], rest[1], rest[2],
                       type_id=rest[3] if len(rest) > 3 else None,
                       type_list=rest[4] if len(rest) > 4 else "ambit",
                       ele=rest[5] if len(rest) > 5 else None)
    elif action == "rename" and len(rest) >= 2:
        out = rename_poi(d, rest[0], rest[1])
    elif action == "delete" and len(rest) >= 1:
        out = delete_poi(d, rest[0])
    else:
        print(__doc__)
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
