#!/usr/bin/env python3
"""Push an Ember daily summary to intervals.icu wellness.

André, 2026-08-25: Sommet already holds the intervals.icu connection, so Ember's
fasting/food/coffee/water can ride onward to intervals as a daily wellness row. The
field mapping below was verified live against the real API (2026-08-25):

  calories  -> kcalConsumed        (native)
  water     -> hydrationVolume     (native, LITRES - not ml)
  macros    -> protein / carbohydrates / fatTotal   (native)
  fasting   -> custom field "FastingHours"   (created if missing)
  coffee    -> custom field "Coffee"         (created if missing)

Everything goes in ONE  PUT /api/v1/athlete/{id}/wellness/{date}  (a merge - it only
touches the fields we send, leaving HRV/sleep/weight untouched). Custom wellness fields
are addressed by their `code` as a top-level key in that same PUT.

Usage:
  ember_to_intervals.py <athlete_id> <api_key> [--summary FILE|-] [--date YYYY-MM-DD]
                        [--dry-run] [--json]

<summary> is Ember's buildSommetPayload() JSON:
  {date, totals:{kcal,coffees,waterMl,...}, fasting:{completedToday:[{hours},...]},
   entries:[{type,protein,carbs,fat,...}]}
Reads the summary from --summary FILE, or stdin when FILE is "-".
"""
import sys, json, argparse, urllib.request, urllib.error, base64

API = "https://intervals.icu/api/v1/athlete"

# André already has these custom wellness fields on intervals.icu (verified 2026-08-25),
# so we reuse their codes rather than making duplicates. A fresh account that lacks them
# gets them auto-created with the same codes.
CUSTOM_FIELDS = [
    {"code": "FastingTime", "name": "Fasting Time", "units": "", "fmt": ".1f"},
    {"code": "Coffees",     "name": "Coffees",      "units": "", "fmt": ".1f"},
]


def _req(method, url, api_key, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", "Basic " + base64.b64encode(f"API_KEY:{api_key}".encode()).decode())
    r.add_header("User-Agent", "Sommet-Ember/1.0")  # intervals.icu 403s the default python-urllib UA
    if data is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            body = resp.read().decode()
            return resp.status, (json.loads(body) if body.strip() else None)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:300]}


def build_wellness(summary):
    """Map an Ember summary onto an intervals wellness body (only non-empty fields)."""
    totals = summary.get("totals", {}) or {}
    fasting = summary.get("fasting", {}) or {}
    entries = summary.get("entries", []) or []

    fasted_h = round(sum(f.get("hours", 0) for f in fasting.get("completedToday", [])), 2)
    p = round(sum(e.get("protein", 0) for e in entries if e.get("type") == "meal"))
    c = round(sum(e.get("carbs", 0)   for e in entries if e.get("type") == "meal"))
    f = round(sum(e.get("fat", 0)     for e in entries if e.get("type") == "meal"))

    body = {}
    if totals.get("kcal"):    body["kcalConsumed"] = round(totals["kcal"])
    if totals.get("waterMl"): body["hydrationVolume"] = round(totals["waterMl"] / 1000.0, 2)  # litres
    if p: body["protein"] = p
    if c: body["carbohydrates"] = c
    if f: body["fatTotal"] = f
    if fasted_h:              body["FastingTime"] = fasted_h
    if totals.get("coffees") is not None: body["Coffees"] = totals["coffees"]
    return body


def ensure_custom_fields(athlete_id, api_key, needed_codes, dry_run):
    """Create any FastingHours/Coffee custom wellness field that doesn't exist yet."""
    status, items = _req("GET", f"{API}/{athlete_id}/custom-item", api_key)
    have = set()
    if isinstance(items, list):
        for it in items:
            code = (it.get("content") or {}).get("code")
            if code:
                have.add(code)
    created = []
    for spec in CUSTOM_FIELDS:
        if spec["code"] in needed_codes and spec["code"] not in have:
            payload = {"type": "INPUT_FIELD", "visibility": "PRIVATE", "name": spec["name"],
                       "content": {"code": spec["code"], "type": "numeric",
                                   "units": spec["units"], "number_format": spec["fmt"]}}
            if dry_run:
                created.append(spec["code"] + " (would create)")
            else:
                st, _ = _req("POST", f"{API}/{athlete_id}/custom-item", api_key, payload)
                created.append(f"{spec['code']} ({'ok' if st < 300 else 'FAILED ' + str(st)})")
    return created


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("athlete_id")
    ap.add_argument("api_key")
    ap.add_argument("--summary", default="-", help="Ember summary JSON file, or - for stdin")
    ap.add_argument("--date", help="override date (default: summary's date)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent; no writes")
    ap.add_argument("--json", action="store_true", help="machine-readable result")
    a = ap.parse_args()

    raw = sys.stdin.read() if a.summary == "-" else open(a.summary).read()
    summary = json.loads(raw)
    date = a.date or summary.get("date")
    if not date:
        print(json.dumps({"ok": False, "error": "no date"})); return 2

    body = build_wellness(summary)
    needed = {k for k in ("FastingTime", "Coffees") if k in body}
    created = ensure_custom_fields(a.athlete_id, a.api_key, needed, a.dry_run)

    result = {"ok": True, "date": date, "dry_run": a.dry_run,
              "custom_fields": created, "wellness": body}
    if not a.dry_run:
        st, resp = _req("PUT", f"{API}/{a.athlete_id}/wellness/{date}", a.api_key, body)
        result["ok"] = st < 300
        result["status"] = st
        if st >= 300:
            result["error"] = resp

    if a.json:
        print(json.dumps(result))
    else:
        print(("DRY-RUN " if a.dry_run else "") + f"wellness {date}:")
        for k, v in body.items():
            print(f"  {k} = {v}")
        if created:
            print("  custom fields:", ", ".join(created))
        if not a.dry_run:
            print("  ->", "OK" if result["ok"] else f"FAILED {result.get('status')}: {result.get('error')}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
