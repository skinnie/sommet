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


MAP_WINDOW = "90d"      # power-curve window for MAP
MAP_SECS = 300          # MAP = best 5-minute power


def estimate_map(athlete_id: str, api_key: str, ftp=None):
    """Maximal Aerobic Power for the Bryton (its profile has a MAP field; intervals.icu has none).
    The standard field estimate: MAP ~= best 5-minute power - here from the athlete's Ride power
    curve over the last 90 days. Falls back to FTP / 0.75 (FTP ~ 75% of MAP) when there's no power
    data. The Bryton app itself has no formula: MAP is typed in by hand there.
    Returns (watts or None, source)."""
    try:
        d = _req("GET", f"/power-curves?type=Ride&curves={MAP_WINDOW}", athlete_id, api_key)
        cur = (d.get("list") if isinstance(d, dict) else d)[0]
        secs, watts = cur.get("secs") or [], cur.get("watts") or []
        if MAP_SECS in secs:
            w = watts[secs.index(MAP_SECS)]
            if w:
                return int(round(float(w))), f"best 5-min power, last {MAP_WINDOW[:-1]} days"
    except Exception:                          # noqa: BLE001 - fall back to the FTP estimate
        pass
    if ftp:
        return int(round(float(ftp) / 0.75)), "FTP / 0.75 (no power data)"
    return None, None


def get(athlete_id: str, api_key: str) -> dict:
    """{ftp, lthr, max_hr, weight, height, gender, age, map} from intervals.icu (None where unset).
    `map` is estimated (estimate_map) - intervals.icu doesn't store a MAP."""
    prof = _req("GET", "", athlete_id, api_key)
    if isinstance(prof, list):
        prof = prof[0]
    ride = _ride_group(prof) or {}
    weight = prof.get("icu_weight") or prof.get("weight")
    height_m = prof.get("height")
    # intervals.icu's field is icu_date_of_birth ("YYYY-MM-DD"); plain date_of_birth doesn't exist,
    # which silently left age empty for every device sync (André, 2026-09-25).
    dob = prof.get("icu_date_of_birth") or prof.get("date_of_birth")
    age = None
    if dob:
        try:
            born = datetime.date.fromisoformat(str(dob)[:10])
            today = datetime.date.today()
            age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        except ValueError:
            age = None
    sex = prof.get("sex")
    map_w, map_source = estimate_map(athlete_id, api_key, ride.get("ftp"))
    return {
        "ftp": ride.get("ftp"),
        "lthr": ride.get("lthr"),
        "max_hr": ride.get("max_hr"),
        "weight": round(float(weight), 1) if weight else None,
        "height": int(round(float(height_m) * 100)) if height_m else None,   # m -> cm
        "gender": (1 if sex == "M" else 0) if sex in ("M", "F") else None,
        "age": age,
        "map": map_w,
        "map_source": map_source,
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
