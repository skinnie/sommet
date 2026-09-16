#!/usr/bin/env python3
"""Smoke-test the Sommet Sync endpoint (#SYNC-1) end to end, stdlib only.

Exercises exactly what a client does: upsert records, pull since a cursor, push/fetch a track
blob, prove last-writer-wins ignores an older update, and prove a tombstone removes a record
everywhere. Run against a deployed sync.php (LAN or NAS).

    python3 tools/sommet_sync_smoketest.py --url http://192.168.1.102/sommet/sync.php --token <token>

Exits non-zero on the first failed assertion so it is CI/curl-friendly.
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.error
import urllib.parse


def http(url, token, method, params=None, body=None, raw=None):
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    if raw is not None:
        data = raw
        ctype = "application/octet-stream"
    elif body is not None:
        data = json.dumps(body).encode()
        ctype = "application/json"
    else:
        data = None
        ctype = None
    req = urllib.request.Request(url + q, data=data, method=method)
    req.add_header("X-Sommet-Token", token)
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read()


def check(cond, msg):
    print(("  ok  " if cond else " FAIL ") + msg)
    if not cond:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="full URL to sync.php")
    ap.add_argument("--token", required=True)
    a = ap.parse_args()
    url, tok = a.url, a.token

    now = int(time.time() * 1000)
    uid = "smoketest|%d" % now  # unique so reruns don't collide with real data

    print("1. auth: a bad token is rejected")
    st, _ = http(url, "definitely-wrong", "GET", {"c": "activities", "since": 0})
    check(st == 401, "bad token -> 401 (got %d)" % st)

    print("2. upsert one activity record")
    rec = {"uid": uid, "updated_at": now, "name": "Smoke run", "distance_m": 10000,
           "start_time": "2026-09-13T07:14:22Z", "device": "smoketest", "has_track": True,
           "track_fmt": "gpx"}
    st, b = http(url, tok, "POST", {"c": "activities"}, {"records": [rec], "deleted": []})
    check(st == 200 and json.loads(b).get("ok"), "POST accepted (got %d)" % st)

    print("3. pull since=0 returns it")
    st, b = http(url, tok, "GET", {"c": "activities", "since": 0})
    got = json.loads(b)
    mine = [r for r in got.get("records", []) if r.get("uid") == uid]
    check(st == 200 and len(mine) == 1 and mine[0]["name"] == "Smoke run", "record round-trips")
    server_now = got["now"]

    print("4. last-writer-wins: an OLDER update is ignored")
    old = dict(rec, updated_at=now - 60000, name="STALE should not win")
    http(url, tok, "POST", {"c": "activities"}, {"records": [old], "deleted": []})
    st, b = http(url, tok, "GET", {"c": "activities", "since": 0})
    mine = [r for r in json.loads(b)["records"] if r.get("uid") == uid][0]
    check(mine["name"] == "Smoke run", "stale update rejected, newer kept")

    print("5. incremental pull: since=server_now hides the already-seen record")
    st, b = http(url, tok, "GET", {"c": "activities", "since": server_now})
    mine = [r for r in json.loads(b)["records"] if r.get("uid") == uid]
    check(len(mine) == 0, "since-cursor excludes unchanged rows")

    print("6. blob: push a GPX and fetch it back byte-for-byte")
    gpx = b"<gpx>smoke</gpx>"
    st, _ = http(url, tok, "POST", {"blob": 1, "uid": uid, "fmt": "gpx"}, raw=gpx)
    check(st == 200, "blob upload accepted (got %d)" % st)
    st, b = http(url, tok, "GET", {"blob": 1, "uid": uid, "fmt": "gpx"})
    check(st == 200 and b == gpx, "blob round-trips identical")

    print("7. tombstone: delete removes it and reports it in `deleted`")
    http(url, tok, "POST", {"c": "activities"}, {"records": [], "deleted": [uid]})
    st, b = http(url, tok, "GET", {"c": "activities", "since": 0})
    got = json.loads(b)
    live = [r for r in got["records"] if r.get("uid") == uid]
    check(len(live) == 0 and uid in got["deleted"], "record gone, tombstone advertised")

    print("\nAll checks passed ✓")


if __name__ == "__main__":
    main()
