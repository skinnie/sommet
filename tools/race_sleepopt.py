#!/usr/bin/env python3
"""Suggest my sleep: try many sleep plans on the rider's real ride and rank them.

Each candidate is a nightly no-ride window (bedtime + length). The whole timeline is rebuilt for it with
the two-process alertness model (race_alertness), so a plan is judged on what matters: the finish time,
how sleepy the rider gets at the worst moment, and whether a bed is near where each night lands
(race_days). Plans that let alertness fall into the dangerous zone (< 35) or miss a cutoff are set aside.

Input (JSON, file or stdin): the same body as race_timeline ({event, athlete, bike, stop_total_s|...,
  cutoffs inside event}) + {"habit": {"bed_h": 22, "wake_h": 6}, "pois"?: <race_pois result>}.
Output: {ok, options:[{bed, wake, hours, finish, elapsed_h, min_alertness, min_at, band, nights:[...],
  worst_margin_s}], baseline:{... no sleep ...}, recommended: index|None, note}
The parameters are published sleep science, not fitted to the rider (see race_alertness).
Stdlib only. `--selftest` is offline.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, List

import race_alertness
import race_days
import race_event
import race_timeline
from race_event import AthleteInputs, BikeInputs, RaceEvent

BEDS_H = [21.5, 22.0, 22.5, 23.0, 23.5, 0.0, 0.5, 1.0, 1.5, 2.0]
LENGTHS_H = [1.5, 3.0, 4.5, 6.0, 7.5]
TIME_BUDGET_S = 75.0


def _hhmm(h: float) -> str:
    h = h % 24.0
    return "%02d:%02d" % (int(h), int(round((h - int(h)) * 60)) % 60)


def _run(body: Dict[str, Any], event: RaceEvent, athlete, bike, habit: Dict[str, Any], nr) -> Dict[str, Any]:
    fatigue = {"enabled": True, "model": "twoprocess", "bed_h": habit.get("bed_h", 22.0),
               "wake_h": habit.get("wake_h", 6.0)}
    return race_timeline.build_timeline(
        event, athlete, bike, stops_s=body.get("stops_s"), stop_total_s=body.get("stop_total_s"),
        stop_profile=body.get("stop_profile"), stop_events=body.get("stop_events"),
        wind_speed_delta_kmh=float(body.get("wind_speed_delta_kmh") or 0.0),
        fatigue=fatigue, no_ride=nr)


def suggest(body: Dict[str, Any]) -> Dict[str, Any]:
    event = RaceEvent.from_dict(body.get("event", {}))
    if not event.points and event.gpx:                      # parse once, not once per candidate
        import geo_util
        event.points = geo_util.parse_gpx_points(event.gpx)
    athlete = AthleteInputs.from_dict(body["athlete"]) if body.get("athlete") else None
    bike = BikeInputs.from_dict(body["bike"]) if body.get("bike") else None
    habit = body.get("habit") or {}
    pois = body.get("pois")
    t0 = time.time()

    def summarize(tl: Dict[str, Any], bed: float, length: float) -> Dict[str, Any]:
        al = tl.get("alertness") or {}
        days = race_days.analyze(tl, pois) if tl.get("no_ride") or any(r.get("sleep_s") for r in tl["controls"]) else {}
        nights = [{"km": n["km"], "start": n["start"][11:16], "end": n["end"][11:16], "hours": n["hours"],
                   "no_bed": n["no_bed"], "nearest_bed": (n["sleep_at"][0] if n["sleep_at"] else None)}
                  for n in (days.get("nights") or [])]
        return {"bed": _hhmm(bed), "wake": _hhmm(bed + length), "hours": length,
                "finish": tl["finish_eta_dt"], "elapsed_h": round(tl["elapsed_time_s"] / 3600.0, 1),
                "min_alertness": al.get("min"), "min_at": al.get("min_at"),
                "band": race_alertness.band(al.get("min") or 0.0),
                "worst_margin_s": tl.get("worst_margin_s"), "nights": nights}

    base_tl = _run(body, event, athlete, bike, habit, None)
    if not base_tl.get("ok"):
        return {"ok": False, "error": base_tl.get("error", "timeline failed")}
    baseline = summarize(base_tl, 0.0, 0.0)
    baseline.update({"bed": None, "wake": None})

    results: List[Dict[str, Any]] = []
    for bed in BEDS_H:
        for length in LENGTHS_H:
            if time.time() - t0 > TIME_BUDGET_S:
                break
            tl = _run(body, event, athlete, bike, habit, {"start_h": bed, "end_h": (bed + length) % 24.0})
            if tl.get("ok"):
                results.append(summarize(tl, bed, length))

    def ok(r):
        return (r["min_alertness"] or 0) >= race_alertness.DANGER_ALERTNESS and (r["worst_margin_s"] is None or r["worst_margin_s"] >= 0)
    feasible = sorted([r for r in results if ok(r)], key=lambda r: r["elapsed_h"])
    safe = [r for r in feasible if (r["min_alertness"] or 0) >= race_alertness.TIRED_ALERTNESS]
    options = (safe or feasible)[:6]
    # Always show the trade-off's other end: the most alert plan that is not already listed.
    if results:
        best_alert = max(results, key=lambda r: (r["min_alertness"] or 0, -r["elapsed_h"]))
        if best_alert not in options:
            options.append(best_alert)
    return {"ok": True, "options": options, "baseline": baseline,
            "recommended": 0 if options and options[0] in safe else None,
            "tried": len(results),
            "note": "Sleep-science model (sleep pressure + body clock shifted to your usual %s-%s), not fitted to your rides; treat alertness as relative."
                    % (_hhmm(habit.get("bed_h", 22.0)), _hhmm(habit.get("wake_h", 6.0)))}


def _selftest():
    ev = race_timeline._synthetic_event_with_controls()
    from datetime import datetime, timedelta
    ev.start_dt = datetime(2026, 9, 25, 14, 0)
    for c in ev.cutoffs:
        c.cutoff_dt = ev.start_dt + timedelta(hours=80)
    global BEDS_H, LENGTHS_H
    BEDS_H, LENGTHS_H = [22.0, 0.0], [3.0, 6.0]
    body = {"event": ev.to_dict(), "athlete": {"weight_kg": 75, "speed_profile": {"base_speed_kmh": 18.0, "confidence": "low", "model_source": "generic", "n_recent_rides": 0}},
            "stop_total_s": 3600, "habit": {"bed_h": 22.0, "wake_h": 6.0}}
    r = suggest(body)
    assert r["ok"] and r["options"] and r["baseline"]["min_alertness"] is not None, r
    assert r["baseline"]["min_alertness"] <= r["options"][0]["min_alertness"] + 40
    print("options:", [(o["bed"], o["wake"], o["elapsed_h"], o["min_alertness"], o["band"]) for o in r["options"]])
    print("\n✓ All race_sleepopt selftest checks passed")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Suggest my sleep")
    ap.add_argument("input_file", nargs="?")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        _selftest()
        return
    try:
        body = json.load(open(a.input_file)) if a.input_file else json.load(sys.stdin)
        print(json.dumps(suggest(body)))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
