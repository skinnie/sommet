#!/usr/bin/env python3
"""Read and write an athlete's training thresholds on intervals.icu, stdlib only.

    ./tools/intervals_athlete.py get  <athlete_id> <api_key>
    ./tools/intervals_athlete.py put  <athlete_id> <api_key> --ftp 225 --lthr 186 --max-hr 205

Used by the Bryton profile reconciliation (see tools/bryton_profile.py + server.py): intervals.icu
is Andre's source of truth for FTP / LTHR / Max HR / weight, and this pulls those to compare with
the device, or pushes a chosen value back so the two stay in sync.

The threshold values live in the Ride `sportSettings` group (`ftp`, `lthr`, `max_hr`), weight in
wellness/profile. Auth is HTTP Basic `API_KEY:<key>` exactly like intervals_stats.py. `put` MERGES
- it PUTs only the fields given, back into the existing Ride group (found by id), and posts weight
to today's wellness. Gender / birthday / height are athlete-profile fields intervals.icu does not
expose for write through this path, so they are read-only here (sync Bryton<->Sommet only).
"""

import argparse
import base64
import datetime
import json
import sys
import urllib.request

API_BASE = "https://intervals.icu/api/v1"


def _req(method: str, path: str, athlete_id: str, api_key: str, body=None):
    url = f"{API_BASE}/athlete/{athlete_id}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    token = base64.b64encode(f"API_KEY:{api_key}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    req.add_header("User-Agent", "Sommet/1.0 (+intervals.icu sync)")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else {}


def _ride_group(prof: dict) -> dict | None:
    """The sportSettings group that covers Ride."""
    for ss in prof.get("sportSettings", []) or []:
        types = ss.get("types") or []
        if "Ride" in types or ss.get("id") == "Ride":
            return ss
    # fall back to the first group if none is explicitly tagged Ride
    groups = prof.get("sportSettings") or []
    return groups[0] if groups else None


def get(athlete_id: str, api_key: str) -> dict:
    """{ftp, lthr, max_hr, weight, height, gender, age} from intervals.icu (None where unset)."""
    prof = _req("GET", "", athlete_id, api_key)
    if isinstance(prof, list):
        prof = prof[0]
    ride = _ride_group(prof) or {}
    weight = prof.get("icu_weight") or prof.get("weight")
    height_m = prof.get("height")
    dob = prof.get("date_of_birth")
    age = None
    if dob:
        try:
            age = datetime.date.today().year - int(str(dob)[:4])
        except ValueError:
            age = None
    sex = prof.get("sex")
    return {
        "ftp": ride.get("ftp"),
        "lthr": ride.get("lthr"),
        "max_hr": ride.get("max_hr"),
        "weight": round(float(weight), 1) if weight else None,
        "height": int(round(float(height_m) * 100)) if height_m else None,   # m -> cm
        "gender": (1 if sex == "M" else 0) if sex in ("M", "F") else None,
        "age": age,
        "_ride_group_id": ride.get("id"),
    }


def put(athlete_id: str, api_key: str, ftp=None, lthr=None, max_hr=None, weight=None) -> dict:
    """Push the given thresholds back. FTP/LTHR/MaxHR -> the Ride sportSettings group (merge);
    weight -> today's wellness. Returns what was sent."""
    sent = {}
    ss_body = {}
    if ftp is not None:
        ss_body["ftp"] = int(ftp)
    if lthr is not None:
        ss_body["lthr"] = int(lthr)
    if max_hr is not None:
        ss_body["max_hr"] = int(max_hr)
    if ss_body:
        prof = _req("GET", "", athlete_id, api_key)
        if isinstance(prof, list):
            prof = prof[0]
        ride = _ride_group(prof)
        if not ride or not ride.get("id"):
            raise RuntimeError("could not find the Ride sport-settings group to update")
        _req("PUT", f"/sport-settings/{ride['id']}", athlete_id, api_key, ss_body)
        sent.update(ss_body)
    if weight is not None:
        today = datetime.date.today().isoformat()
        _req("PUT", f"/wellness/{today}", athlete_id, api_key, {"weight": float(weight)})
        sent["weight"] = float(weight)
    return sent


def main(argv=None):
    ap = argparse.ArgumentParser(description="intervals.icu athlete thresholds get/put")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("get"); g.add_argument("athlete_id"); g.add_argument("api_key")
    p = sub.add_parser("put"); p.add_argument("athlete_id"); p.add_argument("api_key")
    p.add_argument("--ftp", type=int); p.add_argument("--lthr", type=int)
    p.add_argument("--max-hr", dest="max_hr", type=int); p.add_argument("--weight", type=float)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "get":
            print(json.dumps(get(args.athlete_id, args.api_key), indent=2))
        else:
            sent = put(args.athlete_id, args.api_key, ftp=args.ftp, lthr=args.lthr,
                       max_hr=args.max_hr, weight=args.weight)
            print(json.dumps({"ok": True, "sent": sent}))
        return 0
    except Exception as exc:                       # noqa: BLE001 - CLI surface
        print(f"intervals_athlete: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
