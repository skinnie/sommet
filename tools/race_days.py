#!/usr/bin/env python3
"""Days & nights of a race plan: where each night's rest falls, the days between them, and how that
compares with simply cutting the GPX into equal days (the Route page's "Split into days").

A multi-day ride is not cut by distance, it is cut by SLEEP: the day ends where you are when you stop
riding (a no-ride window, a planned sleep block at a control). This links the two views:
  * nights   = every forced rest (race_timeline `rests`) + every sleep block >= 2 h at a control
  * days     = the stretches of riding between them (km from/to, start/end clock)
  * even     = the boundaries an equal-distance split into the same number of days would give, and how
               far each night is from them (so the mismatch is visible, not hidden)
  * sleep_at = accommodation / camping POIs (PitStopper) near each night's km - a night that lands 60 km
               from the nearest bed is a problem the equal-split view never shows.

Input (JSON, file or stdin): {"timeline": <race_timeline result>, "pois"?: <race_pois result>,
  "reach_km"?: 20}
Output: {ok, nights:[{n, km, start, end, hours, source, sleep_at:[{name,km,offset_km,kind}], no_bed}],
  days:[{day, from_km, to_km, km, start, end}], even_km:[...], lines:[...]}
Stdlib only. `--selftest` is offline.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any, Dict, List

MIN_NIGHT_S = 2 * 3600        # a sleep block shorter than this is a nap, not a night


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(str(s)[:19])


def analyze(timeline: Dict[str, Any], pois: Dict[str, Any] | None = None,
            reach_km: float = 20.0) -> Dict[str, Any]:
    rows = timeline.get("controls") or []
    total_km = float(timeline.get("distance_km") or 0.0)
    if not rows or total_km <= 0:
        return {"ok": False, "error": "no timeline"}
    nights: List[Dict[str, Any]] = []
    for r in rows:
        for x in r.get("rests") or []:
            a, b = _dt(x["start"]), _dt(x["end"])
            nights.append({"km": float(x["km"]), "start": a, "end": b, "source": "no-ride hours"})
        if (r.get("sleep_s") or 0) >= MIN_NIGHT_S and r is not rows[-1]:
            a = _dt(r["arrival_dt"])
            b = _dt(r["depart_dt"])
            nights.append({"km": float(r["distance_km"]), "start": a, "end": b, "source": "planned sleep at " + str(r.get("label"))})
    nights = [n for n in nights if (n["end"] - n["start"]).total_seconds() >= MIN_NIGHT_S]
    nights.sort(key=lambda n: n["km"])

    beds: List[Dict[str, Any]] = []
    for cat in ("shelter",):
        for p in ((pois or {}).get("categories") or {}).get(cat, {}).get("pois", []) or []:
            beds.append(p)

    out_nights = []
    for i, n in enumerate(nights, 1):
        near = sorted((b for b in beds if abs(b["km"] - n["km"]) <= reach_km), key=lambda b: abs(b["km"] - n["km"]))
        out_nights.append({
            "n": i, "km": round(n["km"], 1), "start": n["start"].isoformat(), "end": n["end"].isoformat(),
            "hours": round((n["end"] - n["start"]).total_seconds() / 3600.0, 1), "source": n["source"],
            "sleep_at": [{"name": b.get("name"), "km": b["km"], "offset_km": round(b["km"] - n["km"], 1),
                          "kind": b.get("kind") or b.get("subtype")} for b in near[:3]],
            "n_beds": len(near), "no_bed": bool(beds) and not near})

    start = _dt(timeline["start_dt"]); finish = _dt(timeline["finish_eta_dt"])
    edges = [(0.0, start)] + [(n["km"], n["end"]) for n in nights]
    ends = [(n["km"], n["start"]) for n in nights] + [(total_km, finish)]
    days = []
    for i, ((k0, t0), (k1, t1)) in enumerate(zip(edges, ends), 1):
        days.append({"day": i, "from_km": round(k0, 1), "to_km": round(k1, 1), "km": round(k1 - k0, 1),
                     "start": t0.isoformat(), "end": t1.isoformat()})
    n_days = len(days)
    even = [round(total_km * i / n_days, 1) for i in range(1, n_days)]

    lines: List[str] = []
    for i, n in enumerate(out_nights):
        e = even[i] if i < len(even) else None
        bit = ""
        if e is not None:
            d = n["km"] - e
            bit = " - an equal-distance split would end this day at km %.0f (%s%.0f km)" % (e, "+" if d >= 0 else "-", abs(d))
        lines.append("Night %d: rest %s-%s (%.1f h) at km %.0f%s."
                     % (n["n"], n["start"][11:16], n["end"][11:16], n["hours"], n["km"], bit))
        if n["no_bed"]:
            lines.append("   No accommodation within %.0f km of km %.0f - plan a bivouac or move the rest." % (reach_km, n["km"]))
        elif n["sleep_at"]:
            b = n["sleep_at"][0]
            lines.append("   Nearest bed: %s at km %.0f (%+.0f km); %d within %.0f km." % (b["name"], b["km"], b["offset_km"], n["n_beds"], reach_km))
    if out_nights:
        sug = float(timeline.get("sleep_suggested_s") or 0.0)
        per = sug / len(out_nights) if sug else 0.0
        short = [n for n in out_nights if per and n["hours"] * 3600 < per * 0.75]
        if short:
            lines.append("Your rest is shorter than the ~%.1f h a night that a ride this long usually needs (%d night%s)."
                         % (per / 3600.0, len(short), "s" if len(short) > 1 else ""))
    return {"ok": True, "nights": out_nights, "days": days, "even_km": even, "lines": lines}


def _selftest():
    tl = {"distance_km": 200.0, "start_dt": "2026-09-25T20:00:00", "finish_eta_dt": "2026-09-26T13:00:00",
          "sleep_suggested_s": 3 * 3600,
          "controls": [
              {"label": "C1", "distance_km": 100.0, "arrival_dt": "2026-09-26T02:00:00", "depart_dt": "2026-09-26T02:00:00",
               "sleep_s": 0, "rests": [{"start": "2026-09-26T00:00:00", "end": "2026-09-26T03:00:00", "km": 80.0}]},
              {"label": "Finish", "distance_km": 200.0, "arrival_dt": "2026-09-26T13:00:00", "depart_dt": "2026-09-26T13:00:00",
               "sleep_s": 0, "rests": []}]}
    pois = {"categories": {"shelter": {"pois": [{"name": "Hotel A", "km": 85.0, "kind": "Hotel"},
                                               {"name": "Camp B", "km": 150.0, "kind": "Camp"}]}}}
    r = analyze(tl, pois)
    assert r["ok"] and len(r["nights"]) == 1 and r["nights"][0]["km"] == 80.0, r
    assert [d["to_km"] for d in r["days"]] == [80.0, 200.0] and r["even_km"] == [100.0], r
    assert r["nights"][0]["sleep_at"][0]["name"] == "Hotel A" and not r["nights"][0]["no_bed"], r
    r2 = analyze(tl, {"categories": {"shelter": {"pois": [{"name": "Far", "km": 190.0}]}}})
    assert r2["nights"][0]["no_bed"], r2
    print("\n".join(r["lines"]))
    print("\n✓ All race_days selftest checks passed")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Days & nights of a race plan")
    ap.add_argument("input_file", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        _selftest()
        return
    try:
        body = json.load(open(a.input_file)) if a.input_file else json.load(sys.stdin)
        print(json.dumps(analyze(body["timeline"], body.get("pois"), float(body.get("reach_km") or 20.0))))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
