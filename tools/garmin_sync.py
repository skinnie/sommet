#!/usr/bin/env python3
"""Pulls activities and daily health data from Garmin Connect, for the desktop app's Garmin
import + Health screen (André, 2026-08-24). Shares the OAuth token store that garmin_weight.py
creates (log in once there, or with --login here), so no password is needed after the first
sign-in. Read-only pulls; nothing is written back to Garmin.

    ./tools/garmin_sync.py --login you@example.com 'pass' [--mfa CODE] --tokens DIR
    ./tools/garmin_sync.py --activities --days 30 --tokens DIR --json
    ./tools/garmin_sync.py --health --days 30 --tokens DIR --json

`--activities` -> normalized moves {id,name(sport),start,duration,distance,ascent,calories,
device,typeKey}. `--health` -> daily series {rhr:[{date,value}], steps:[{date,value}]} (both
come from Garmin range endpoints, so it's a couple of calls, not one-per-day).
"""

import argparse
import datetime
import json
import pathlib
import sys

DEFAULT_TOKENS = pathlib.Path.home() / "AmbitAppBackups" / "garmin_tokens"


def _login(email, password, mfa, tokens_dir):
    from garminconnect import Garmin
    tokens_dir = pathlib.Path(tokens_dir)
    tokens_dir.mkdir(parents=True, exist_ok=True)

    def _mfa():
        if not mfa:
            raise RuntimeError("Garmin asked for a 2FA code - re-run with --mfa CODE")
        return mfa
    client = Garmin(email=email, password=password, prompt_mfa=_mfa)
    # login(tokenstore=DIR) logs in AND persists tokens to DIR (garminconnect >=0.2.x).
    client.login(tokenstore=str(tokens_dir))
    return {"ok": True, "loggedIn": True}


def _client(tokens_dir):
    from garminconnect import Garmin
    tokens_dir = pathlib.Path(tokens_dir)
    if not tokens_dir.exists():
        return None
    client = Garmin()
    try:
        client.login(tokenstore=str(tokens_dir))   # loads the cached OAuth tokens
    except Exception:
        return None
    return client


def _range(days):
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days if days > 0 else 3650)
    return start.isoformat(), today.isoformat()


def do_activities(days, tokens_dir):
    client = _client(tokens_dir)
    if client is None:
        return {"ok": False, "needLogin": True, "error": "not logged in"}
    start, end = _range(days)
    raw = client.get_activities_by_date(start, end) or []
    out = []
    for a in raw:
        atype = (a.get("activityType") or {}).get("typeKey", "")
        out.append({
            "id": str(a.get("activityId", "")),
            "name": a.get("activityName") or atype,
            "typeKey": atype,
            "start": a.get("startTimeLocal") or a.get("startTimeGMT"),
            "duration": int(a.get("duration") or 0),
            "distance": float(a.get("distance") or 0.0),
            "ascent": float(a.get("elevationGain") or 0.0),
            "calories": int(a.get("calories") or 0),
            "device": a.get("deviceName") or "Garmin",
        })
    return {"ok": True, "source": "garmin", "activities": out}


def do_health(days, tokens_dir):
    client = _client(tokens_dir)
    if client is None:
        return {"ok": False, "needLogin": True, "error": "not logged in"}
    start, end = _range(days if days > 0 else 365)

    def _series(fetch, value_keys):
        """Normalize one Garmin range endpoint into [{date, value}], picking the first present
        of value_keys per row; skips null values. Never raises - a metric the account doesn't
        have just comes back empty."""
        out = []
        try:
            for row in fetch() or []:
                date = row.get("calendarDate") or row.get("date")
                val = next((row[k] for k in value_keys if row.get(k) is not None), None)
                if date and val is not None:
                    out.append({"date": date, "value": val})
        except Exception:
            return []
        out.sort(key=lambda r: r["date"])
        return out

    rhr = _series(lambda: client.get_rhr_daily(start, end),
                  ["restingHeartRate", "value"])
    steps = _series(lambda: client.get_daily_steps(start, end),
                    ["totalSteps", "steps", "value"])

    # HRV (last-night average): get_hrv_data_range returns a dict whose hrvSummaries hold a
    # per-day record. Best-effort - shapes vary by account/firmware, so pull defensively.
    hrv = []
    try:
        hr = client.get_hrv_data_range(start, end) or {}
        for row in (hr.get("hrvSummaries") or hr.get("hrvSummary") or []):
            date = row.get("calendarDate") or row.get("date")
            val = row.get("lastNightAvg") or row.get("weeklyAvg")
            if date and val is not None:
                hrv.append({"date": date, "value": val})
        hrv.sort(key=lambda r: r["date"])
    except Exception:
        hrv = []

    # Body Battery: get_body_battery returns per-day rows; take the day's peak value from the
    # values array (or a summary field) as a single daily point.
    battery = []
    try:
        for row in client.get_body_battery(start, end) or []:
            date = row.get("calendarDate") or row.get("date")
            arr = row.get("bodyBatteryValuesArray") or []
            peak = max((p[1] for p in arr if isinstance(p, list) and len(p) > 1
                        and p[1] is not None), default=None)
            if peak is None:
                peak = row.get("charged")
            if date and peak is not None:
                battery.append({"date": date, "value": peak})
        battery.sort(key=lambda r: r["date"])
    except Exception:
        battery = []

    return {"ok": True, "source": "garmin", "rhr": rhr, "steps": steps,
            "hrv": hrv, "bodyBattery": battery}


def do_sleep(days, tokens_dir):
    """Nightly sleep hours from Garmin. get_sleep_data is per-day, so this loops the window
    (kept modest); returns [{date, value(hours)}]. Best-effort - a day with no sleep record is
    skipped."""
    client = _client(tokens_dir)
    if client is None:
        return {"ok": False, "needLogin": True, "error": "not logged in"}
    out = []
    today = datetime.date.today()
    for i in range(min(days if days > 0 else 30, 60)):
        d = (today - datetime.timedelta(days=i)).isoformat()
        try:
            data = client.get_sleep_data(d) or {}
            dto = data.get("dailySleepDTO") or {}
            secs = dto.get("sleepTimeSeconds")
            if secs:
                out.append({"date": dto.get("calendarDate") or d,
                            "value": round(secs / 3600.0, 2)})
        except Exception:
            continue
    out.sort(key=lambda r: r["date"])
    return {"ok": True, "source": "garmin", "sleep": out}


def do_upload(path, tokens_dir):
    """Upload one activity file (FIT/GPX/TCX) to Garmin Connect. Garmin dedups by start time,
    so re-uploading the same move is a no-op / 409 on their side."""
    client = _client(tokens_dir)
    if client is None:
        return {"ok": False, "needLogin": True, "error": "not logged in"}
    try:
        client.upload_activity(path)
        return {"ok": True, "uploaded": True}
    except Exception as exc:
        # A duplicate (already on Garmin) is a success for our purposes, not a failure.
        msg = str(exc)
        if "409" in msg or "duplicate" in msg.lower():
            return {"ok": True, "uploaded": False, "duplicate": True}
        return {"ok": False, "error": f"{type(exc).__name__}: {msg}"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--login", nargs="+", metavar=("EMAIL", "PASSWORD"))
    ap.add_argument("--mfa")
    ap.add_argument("--activities", action="store_true", help="fetch activities")
    ap.add_argument("--health", action="store_true", help="fetch daily health series")
    ap.add_argument("--sleep", action="store_true", help="fetch nightly sleep hours")
    ap.add_argument("--upload", metavar="FILE", help="upload an activity file to Garmin Connect")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--tokens", default=str(DEFAULT_TOKENS))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        if args.login:
            if len(args.login) < 2:
                ap.error("--login needs EMAIL PASSWORD")
            result = _login(args.login[0], args.login[1], args.mfa, args.tokens)
        elif args.activities:
            result = do_activities(args.days, args.tokens)
        elif args.health:
            result = do_health(args.days, args.tokens)
        elif args.sleep:
            result = do_sleep(args.days, args.tokens)
        elif args.upload:
            result = do_upload(args.upload, args.tokens)
        else:
            ap.error("pass one of --login / --activities / --health / --upload")
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if args.json:
        print(json.dumps(result))
    else:
        print(json.dumps(result, indent=2)[:2000])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
