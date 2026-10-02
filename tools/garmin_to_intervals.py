#!/usr/bin/env python3
"""Replace RunGap for weight: read real scale weigh-ins straight from Garmin Connect (your own
OAuth token, via garmin_weight.py) and push each to intervals.icu's wellness as a MERGE
(PUT /api/v1/athlete/{id}/wellness/{date} - only touches weight/bodyFat, leaves HRV/sleep alone).

Free, no RunGap, no iPhone. Meant to run on the NAS on a schedule (e.g. every few hours / daily).

    # dry run - just show what it would push (no intervals creds needed)
    ./tools/garmin_to_intervals.py --days 3 --dry-run
    # real: push to intervals.icu
    ./tools/garmin_to_intervals.py --days 3 <athlete_id> <api_key>

Only INDEX_SCALE measurements are pushed (not the USER_SETTING profile weight). Picks the latest
real weigh-in per day. Garmin OAuth token store: tools/garmin_weight.py DEFAULT_TOKENS.
"""
import argparse, base64, datetime, json, sys, urllib.request, pathlib

API_BASE = "https://intervals.icu/api/v1"
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import garmin_weight as gw


def read_garmin_weighins(days):
    """{date: {'weight': kg, 'bodyFat': pct?}} for the latest INDEX_SCALE measurement each day."""
    c, tok = gw._client(gw.DEFAULT_TOKENS); c.login(str(tok))
    out = {}
    for d in range(days + 1):
        day = (datetime.date.today() - datetime.timedelta(days=d)).isoformat()
        best = None
        for s in c.get_daily_weigh_ins(day).get("dateWeightList", []):
            if s.get("sourceType") != "INDEX_SCALE":      # skip the profile USER_SETTING value
                continue
            ts = s.get("timestampGMT") or 0
            if best is None or ts > best[0]:
                row = {"weight": round(s.get("weight", 0) / 1000.0, 2)}
                if s.get("bodyFat") is not None:
                    row["bodyFat"] = s["bodyFat"]
                best = (ts, row)
        if best:
            out[day] = best[1]
    return out


def put_wellness(athlete_id, api_key, date, fields):
    url = f"{API_BASE}/athlete/{athlete_id}/wellness/{date}"
    data = json.dumps(fields).encode()
    req = urllib.request.Request(url, data=data, method="PUT")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Basic " + base64.b64encode(f"API_KEY:{api_key}".encode()).decode())
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("athlete_id", nargs="?")
    ap.add_argument("api_key", nargs="?")
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    weighins = read_garmin_weighins(a.days)
    if not weighins:
        print("No INDEX_SCALE weigh-ins in the last %d day(s)." % a.days); return
    for date, fields in sorted(weighins.items()):
        if a.dry_run or not (a.athlete_id and a.api_key):
            print("WOULD push %s -> intervals wellness: %s" % (date, fields))
        else:
            st = put_wellness(a.athlete_id, a.api_key, date, fields)
            print("pushed %s -> %s (HTTP %s)" % (date, fields, st))


if __name__ == "__main__":
    main()
