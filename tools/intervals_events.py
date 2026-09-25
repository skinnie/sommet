#!/usr/bin/env python3
"""Mirror a Training Program workout onto the athlete's intervals.icu calendar (André, 2026-09-25:
"yes build it. attention to not duplicate"). The plan is local first - a person without
intervals.icu keeps it on the device - and when intervals.icu is connected each workout made in
Sommet is ALSO a planned workout there, so the plan is the same on the desktop, the phone and
intervals.icu.

    ./tools/intervals_events.py upsert <athlete_id> <api_key> '<entry JSON>'   -> {ok, eventId}
    ./tools/intervals_events.py delete <athlete_id> <api_key> <event_id>
    ./tools/intervals_events.py doc '<workout JSON>'                           -> workout_doc

No duplicates, by construction:
  * every Sommet entry has a permanent `uid`; its event carries external_id "sommet:<uid>";
  * the first push CREATES the event and returns its id, which the entry keeps (`icuEventId`);
    every later push UPDATES that same event (PUT /events/<id>). If that event was deleted on
    intervals.icu meanwhile (404), it's created again, once.
  * the importer (intervals_workout.py --from-intervals) passes eventId/externalId through, so
    the calendar recognises its own events and never adds them twice.

The workout goes as the structured `workout_doc` (never free text - intervals.icu's text parser
drops targets): steps with duration (s) or distance (m), warmup/cooldown flags, nested reps, and
targets in absolute units - power "w", hr "bpm", cadence "rpm" (verified round-trip on André's
calendar 2026-09-25 with a throw-away event). The exact inverse of intervals_workout.convert_steps.
"""

import json
import sys
import urllib.error

from intervals_athlete import _req

TYPE_TEXT = {"warmup": "Warm up", "interval": "Interval", "recovery": "Recovery",
             "cooldown": "Cool down", "work": "Interval", "rest": "Recovery"}
TARGETS = {"power": ("power", "w"), "hr": ("hr", "bpm"), "cadence": ("cadence", "rpm")}


def _step(st):
    tn = st["type"]["typeName"]
    out = {"text": st.get("text") or TYPE_TEXT.get(tn, tn.capitalize())}
    if tn == "warmup":
        out["warmup"] = True
    elif tn == "cooldown":
        out["cooldown"] = True
    d = st.get("duration") or {}
    if d.get("durationName") == "time":
        out["duration"] = int(d.get("value") or 0)
    elif d.get("durationName") == "distance":
        out["distance"] = int(d.get("value") or 0)
    # lap / ascent have no intervals.icu equivalent: the step keeps its name, no length.
    t = st.get("target") or {}
    if t.get("targetName") in TARGETS:
        key, units = TARGETS[t["targetName"]]
        rng = t.get("valueRange") or {}
        lo = rng.get("min", t.get("value"))
        hi = rng.get("max", lo)
        if lo is not None:
            out[key] = {"start": lo, "end": hi, "units": units}
    return out


def workout_doc(workout):
    """Project schema (flat repeatStart/repeatEnd) -> intervals.icu workout_doc (nested reps)."""
    steps, i, src = [], 0, workout.get("steps") or []
    while i < len(src):
        st = src[i]
        tn = st["type"]["typeName"]
        if tn == "repeatStart":
            reps = int(st["type"].get("value") or 1)
            inner = []
            i += 1
            while i < len(src) and src[i]["type"]["typeName"] != "repeatEnd":
                inner.append(_step(src[i]))
                i += 1
            steps.append({"text": f"{reps}x", "reps": reps, "steps": inner})
        elif tn != "repeatEnd":
            steps.append(_step(st))
        i += 1
    return {"steps": steps}


def sport_type(entry):
    """intervals.icu activity type for the event: bike computers ride; a watch workout follows its
    sport mode's name; anything unknown is a Ride (the app's bike-first default)."""
    if entry.get("device") in ("bryton", "magene"):
        return "Ride"
    mode = (entry.get("mode") or "").lower()
    for word, t in (("run", "Run"), ("trail", "Run"), ("walk", "Walk"), ("hik", "Walk"),
                    ("swim", "Swim"), ("row", "Rowing"), ("ski", "NordicSki")):
        if word in mode:
            return t
    return "Ride"


def event_body(entry):
    workout = entry["workout"]
    return {
        "category": "WORKOUT",
        "start_date_local": f"{entry['date']}T00:00:00",
        "name": workout.get("name") or "Workout",
        "type": sport_type(entry),
        "external_id": f"sommet:{entry['uid']}",
        "workout_doc": workout_doc(workout),
    }


def upsert(athlete_id, api_key, entry):
    if not entry.get("uid") or not entry.get("date") or not entry.get("workout"):
        raise ValueError("entry needs uid, date and workout")
    body = event_body(entry)
    ev_id = entry.get("icuEventId")
    if ev_id:
        try:
            ev = _req("PUT", f"/events/{ev_id}", athlete_id, api_key, body)
            return {"ok": True, "eventId": ev.get("id", ev_id), "updated": True}
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            # deleted on intervals.icu meanwhile: create it again (once, below)
    ev = _req("POST", "/events", athlete_id, api_key, body)
    return {"ok": True, "eventId": ev.get("id"), "updated": False}


def delete(athlete_id, api_key, event_id):
    try:
        _req("DELETE", f"/events/{event_id}", athlete_id, api_key)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:                            # already gone = done
            raise
    return {"ok": True}


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    try:
        cmd = argv[0]
        if cmd == "doc":
            print(json.dumps(workout_doc(json.loads(argv[1]))))
        elif cmd == "upsert":
            print(json.dumps(upsert(argv[1], argv[2], json.loads(argv[3]))))
        elif cmd == "delete":
            print(json.dumps(delete(argv[1], argv[2], argv[3])))
        else:
            raise ValueError(f"unknown command {cmd}")
        return 0
    except Exception as exc:                            # noqa: BLE001 - CLI surface
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
