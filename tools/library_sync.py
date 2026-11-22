#!/usr/bin/env python3
"""The route library and the POI library shared between devices through Sommet Sync (André,
2026-11-22: "these two should be similar and also synced to the nas").

One library, the same on every Sommet install: what is saved, renamed or deleted on one reaches
the others. It never touches a watch.

    ./tools/library_sync.py <routes dir> <pois dir> <sync.php url>     (token in SOMMET_SYNC_TOKEN)

The rules, the same for both libraries and for the Android twin (LibrarySync.ts):
- One record per entry: its id is the `uid`, `updated_at` (ms) says when it last changed. A
  route's GPX travels as a blob under the uid "route:<id>".
- Pull what changed on the server since the last pull. A newer `updated_at` wins. A delete
  removes the entry here. Something deleted here stays deleted unless it was changed elsewhere
  after that.
- The same thing saved on two devices before they met (a route with the same GPX; a POI with
  the same name within 10 m): the older one stays, and the other one is deleted everywhere.
- Then send what is new or changed here, and the deletes made here.
- A server that predates this answers 400 "unknown collection": nothing is done, no error.
State (the pull cursor, what was already sent) is in sync.json inside each library's folder.
stdlib only.
"""

import hashlib
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

import poi_library
import route_library

STATE = "sync.json"
_ONE_AT_A_TIME = threading.Lock()


class OlderServer(Exception):
    """The server does not hold this collection yet (its sync.php predates it)."""


class Http:
    """Sommet Sync's API (sync-server/sync.php). The token goes in a header and is never logged."""

    def __init__(self, url, token, timeout=20):
        self.url, self.token, self.timeout = url, token, timeout

    def _call(self, method, params, data=None, content_type=None):
        req = urllib.request.Request(self.url + "?" + urllib.parse.urlencode(params), data=data,
                                     method=method, headers={"X-Sommet-Token": self.token})
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 400 and "unknown collection" in body:
                raise OlderServer(params.get("c", "")) from None
            if e.code == 404:
                raise FileNotFoundError(params.get("uid", "")) from None
            raise RuntimeError(f"the sync server answered {e.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise RuntimeError(f"the sync server could not be reached ({getattr(e, 'reason', e)})") from None

    def pull(self, collection, since):
        return json.loads(self._call("GET", {"c": collection, "since": int(since)}))

    def push(self, collection, records, deleted):
        body = json.dumps({"records": records, "deleted": deleted}).encode()
        self._call("POST", {"c": collection}, body, "application/json")

    def get_blob(self, uid):
        return self._call("GET", {"blob": 1, "uid": uid, "fmt": "gpx"})

    def put_blob(self, uid, data):
        self._call("POST", {"blob": 1, "uid": uid, "fmt": "gpx"}, data, "application/octet-stream")


# ---- the two libraries, seen the same way -----------------------------------------------------

class Pois:
    collection = "pois"

    def __init__(self, d):
        self.d = d

    @staticmethod
    def _item(p):
        return {"uid": p["id"], "updated_at": int(p.get("updated") or p.get("created") or 0) * 1000,
                "created": int(p.get("created") or 0), "name": p.get("name", ""),
                "lat": p.get("lat"), "lon": p.get("lon"), "ele": p.get("ele"),
                "type": p.get("type"), "typeList": p.get("typeList", "ambit")}

    def items(self):
        return [self._item(p) for p in poi_library.list_pois(self.d)]

    def record(self, item, _http, _first):
        return dict(item)

    def prefetch(self, _records, _http):
        return {}

    def apply(self, plan, _fetched):
        with poi_library.edit(self.d) as data:
            by_id = {p["id"]: p for p in data["pois"]}
            for uid in plan["remove"]:
                by_id.pop(uid, None)
            for uid, r in plan["put"].items():
                try:
                    name, lat, lon, ele, type_id, type_list = poi_library.clean(r)
                except ValueError:
                    continue
                by_id[uid] = {"id": uid, "name": name, "lat": lat, "lon": lon, "ele": ele,
                              "type": type_id, "typeList": type_list,
                              "created": int(r.get("created") or 0),
                              "updated": int(r.get("updated_at") or 0) // 1000}
            data["pois"] = list(by_id.values())
            gone = set(plan["forget_tombstones"])
            data["deleted"] = [t for t in data["deleted"] if t.get("id") not in gone]
        return set(plan["put"])

    def tombstones(self):
        with poi_library._LOCK:
            return [dict(t) for t in poi_library._load(self.d)["deleted"]]

    @staticmethod
    def same(mine, theirs):
        try:
            return poi_library.same_place({"name": mine["name"], "lat": mine["lat"], "lon": mine["lon"]},
                                          theirs.get("name"), theirs.get("lat"), theirs.get("lon"))
        except (TypeError, ValueError):
            return False


class Routes:
    collection = "routes"

    def __init__(self, d):
        self.d = d

    @staticmethod
    def _item(r):
        return {"uid": r["id"], "created": int(r.get("created") or 0),
                "updated_at": int(r.get("updated") or r.get("created") or 0) * 1000,
                "name": r.get("name", ""), "sha1": r.get("sha1", "")}

    def items(self):
        return [self._item(r) for r in route_library._index(self.d)]

    def record(self, item, http, first):
        """The record to send; a route's GPX goes up first, the first time."""
        if first:
            with open(os.path.join(self.d, item["uid"] + ".gpx"), "rb") as f:
                http.put_blob("route:" + item["uid"], f.read())
        return dict(item)

    def prefetch(self, records, http):
        """The GPX of each route this device lacks, before the library is touched. One that
        cannot be fetched is left out, and the pull is asked again next time."""
        have = {r["id"] for r in route_library._index(self.d)}
        out = {}
        for r in records:
            uid = r.get("uid")
            if uid and uid not in have:
                try:
                    out[uid] = http.get_blob("route:" + uid).decode("utf-8", "replace")
                except (FileNotFoundError, RuntimeError):
                    pass
        return out

    def apply(self, plan, fetched):
        items = route_library._index(self.d)
        by_id = {r["id"]: r for r in items}
        for uid in plan["remove"]:
            if by_id.pop(uid, None) is not None:
                try:
                    os.remove(os.path.join(self.d, uid + ".gpx"))
                except OSError:
                    pass
        done = set()
        for uid, r in plan["put"].items():
            entry = by_id.get(uid)
            if entry is None:
                gpx = fetched.get(uid)
                if gpx is None:
                    continue                           # its GPX did not come: next time
                os.makedirs(self.d, exist_ok=True)
                with open(os.path.join(self.d, uid + ".gpx"), "w", encoding="utf-8") as f:
                    f.write(gpx)
                entry = {"id": uid, "sha1": hashlib.sha1(gpx.encode("utf-8")).hexdigest()}
                by_id[uid] = entry                     # list_routes() works out its figures
            entry["name"] = (str(r.get("name") or "Route").strip() or "Route")[:80]
            entry["created"] = int(r.get("created") or entry.get("created") or 0)
            entry["updated"] = int(r.get("updated_at") or 0) // 1000
            done.add(uid)
        route_library._write_index(self.d, list(by_id.values()))
        gone = set(plan["forget_tombstones"])
        if gone:
            route_library.write_deleted(self.d, [t for t in route_library.deleted(self.d)
                                                 if t.get("id") not in gone])
        return done

    def tombstones(self):
        return [dict(t) for t in route_library.deleted(self.d)]

    @staticmethod
    def same(mine, theirs):
        return bool(mine.get("sha1")) and mine.get("sha1") == theirs.get("sha1")


# ---- the rules ----------------------------------------------------------------------------------

def plan_pull(local, tombstones, pulled, same):
    """What a pull changes here. `local`: this device's items ({uid, updated_at, created, ...});
    `tombstones`: [{id, at}] deleted here (seconds); `pulled`: the server's answer.
    Returns {"put": {uid: record}, "remove": [uid], "kill": [uid], "forget_tombstones": [uid]}:
    entries to add or replace, entries to remove, uids to delete on the server (the younger of
    two copies of the same thing), and local delete marks that no longer apply. No file access:
    the Android twin is checked against the same cases."""
    mine = {x["uid"]: x for x in local}
    dead = {t["id"]: int(t.get("at") or 0) * 1000 for t in tombstones}
    put, remove, kill, forget = {}, [], [], []
    for uid in pulled.get("deleted") or []:
        if uid in mine:
            remove.append(uid)
            del mine[uid]
        if uid in dead:
            forget.append(uid)
            del dead[uid]
    for r in pulled.get("records") or []:
        uid = r.get("uid")
        if not uid:
            continue
        updated_at = int(r.get("updated_at") or 0)
        if uid in dead:
            if dead[uid] >= updated_at:
                continue                               # deleted here after that change: stays deleted
            forget.append(uid)
            del dead[uid]
        if uid in mine:
            if updated_at > mine[uid]["updated_at"]:
                put[uid] = r
                mine[uid] = {**mine[uid], **r}
            continue
        twin = next((x for x in mine.values() if same(x, r)), None)
        if twin is None:
            put[uid] = r
            mine[uid] = dict(r)
            continue
        # The same thing under two ids. The older one stays (the uid breaks a tie).
        if (int(r.get("created") or 0), uid) < (int(twin.get("created") or 0), twin["uid"]):
            put[uid] = r
            remove.append(twin["uid"])
            kill.append(twin["uid"])
            del mine[twin["uid"]]
            mine[uid] = dict(r)
        else:
            kill.append(uid)
    return {"put": put, "remove": remove, "kill": kill, "forget_tombstones": forget}


def _load_state(d):
    try:
        with open(os.path.join(d, STATE), encoding="utf-8") as f:
            v = json.load(f)
    except (OSError, json.JSONDecodeError):
        v = {}
    if not isinstance(v, dict):
        v = {}
    return {"since": int(v.get("since") or 0),
            "pushed": v["pushed"] if isinstance(v.get("pushed"), dict) else {}}


def _store_state(d, state):
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, os.path.join(d, STATE))


def sync_library(lib, http):
    """One library against the server. Returns {"ok", "changed", "sent", "deleted_sent"} or
    {"ok": True, "older_server": True}. Raises RuntimeError with words for the user."""
    state = _load_state(lib.d)
    try:
        pulled = http.pull(lib.collection, state["since"])
    except OlderServer:
        return {"ok": True, "older_server": True, "changed": 0, "sent": 0, "deleted_sent": 0}
    fetched = lib.prefetch(pulled.get("records") or [], http)
    plan = plan_pull(lib.items(), lib.tombstones(), pulled, lib.same)
    applied = lib.apply(plan, fetched)
    complete = set(plan["put"]) <= applied             # a route whose GPX did not come: ask again
    pushed = state["pushed"]
    for uid in plan["remove"]:
        pushed.pop(uid, None)
    items = lib.items()
    for x in items:
        if x["uid"] in applied:
            pushed[x["uid"]] = x["updated_at"]         # as this device now holds it: not sent back
    out = [lib.record(x, http, x["uid"] not in pushed) for x in items
           if pushed.get(x["uid"]) != x["updated_at"]]
    dead = sorted(set(plan["kill"]) | {t["id"] for t in lib.tombstones()})
    if out or dead:
        http.push(lib.collection, out, dead)
        for rec in out:
            pushed[rec["uid"]] = rec["updated_at"]
        for uid in dead:
            pushed.pop(uid, None)
        lib.apply({"put": {}, "remove": [], "kill": [], "forget_tombstones": dead}, {})
    if complete:
        state["since"] = int(pulled.get("now") or state["since"])
    _store_state(lib.d, state)
    return {"ok": True, "changed": len(applied) + len(plan["remove"]), "sent": len(out),
            "deleted_sent": len(dead)}


def sync_all(routes_dir, pois_dir, url, token):
    """Both libraries. One run at a time. {"ok", "routes": {...}, "pois": {...}} or
    {"ok": False, "error"}."""
    if not url or not token:
        return {"ok": False, "error": "Sommet Sync is not set up"}
    http = Http(url, token)
    with _ONE_AT_A_TIME:
        try:
            return {"ok": True, "routes": sync_library(Routes(routes_dir), http),
                    "pois": sync_library(Pois(pois_dir), http)}
        except (RuntimeError, OSError) as e:
            return {"ok": False, "error": str(e)}


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    result = sync_all(sys.argv[1], sys.argv[2], sys.argv[3], os.environ.get("SOMMET_SYNC_TOKEN", ""))
    print(json.dumps(result))
    sys.exit(0 if result.get("ok") else 1)
