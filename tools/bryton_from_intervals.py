#!/usr/bin/env python3
"""Convert a workout in this project's schema into a Bryton Aero 60 planned workout (.fit).

    ./tools/bryton_from_intervals.py plan.json --ftp 240 -o "My Workout.fit"
    ./tools/intervals_workout.py W1.json | ./tools/bryton_from_intervals.py - --max-hr 190

This is the Bryton twin of `guided_workout.py` (which takes the same schema to the Ambit3). The
INPUT is the project's normalized workout schema - the one `workout.py` documents, `intervals_workout.py`
emits from an intervals.icu export, and the app's workout UI builds:

    {"name": "...", "steps": [
        {"type": {"typeName": "warmup"},
         "duration": {"durationName": "time", "value": 600},         # seconds, or metres for distance
         "target": {"targetName": "power", "valueRange": {"min": 200, "max": 220}}},   # WATTS / BPM / ...
        {"type": {"typeName": "repeatStart", "value": 5}}, ...steps..., {"type": {"typeName": "repeatEnd"}},
    ]}

The OUTPUT is `bryton_workout.encode()` (proven byte-exact against the device's own files).

**Why a real conversion is needed, not a copy.** The two models differ in three ways, and the
Aero 60 is the more constrained side, so this file adapts down to it:

1. **Targets are ABSOLUTE here, RELATIVE on the Bryton.** Our schema carries power in watts and HR
   in bpm; the Aero 60 stores %FTP / %MHR / %LTHR and lets the watch multiply by the athlete's own
   stored threshold. So watts/bpm are converted to a percentage against the athlete's numbers,
   supplied with --ftp / --max-hr / --lthr (the device holds test PROTOCOLS, not the resulting
   threshold values, so they cannot be read back off it). Speed and cadence are already absolute
   and pass straight through (km/h, rpm).

2. **One unit + one interval mode PER WORKOUT.** The Aero 60's "Plan Workout" screen picks UNIT
   (FTP/MHR/LTHR/Speed/Cadence) and INTERVAL (Time/Distance) once for the whole workout. We pick
   the unit from the work/interval steps (--unit overrides; HR maps to MHR by default, --hr-unit
   lthr to switch) and require every step to share one duration mode. A step whose own target is a
   different quantity than the chosen unit (e.g. an HR warm-up in a power workout) can't be
   expressed in that unit, so it falls back to a per-phase default band (--warmup/--recovery/etc.)
   and a warning is printed - nothing is silently mis-encoded.

3. **Repeats FLATTEN.** Our repeatStart(N)/repeatEnd bracket is expanded into N copies of the
   enclosed steps, exactly as the Bryton app itself stores its "Repeats N times" blocks.
"""
from __future__ import annotations

import argparse
import json
import sys

import bryton_workout as bw

# phase typeName -> Bryton intensity name
PHASE_TO_INTENSITY = {
    "warmup": "warmup", "cooldown": "cooldown",
    "recovery": "recovery", "rest": "recovery",
    "interval": "work", "work": "work", "active": "work", "steady": "work",
}

# our targetName -> the quantity family it measures
TARGET_FAMILY = {"power": "power", "hr": "hr", "pace": "speed", "speed": "speed", "cadence": "cadence"}

# a work/interval step's target family -> Bryton unit (hr resolved separately via --hr-unit)
FAMILY_TO_UNIT = {"power": "ftp", "speed": "speed", "cadence": "cadence"}

# default target band (percent, rpm, or km/h) per phase, used only when a step has no usable
# target in the workout's unit. Percent-ish defaults suit ftp/mhr/lthr; overridden per-unit below.
DEFAULT_BANDS_PCT = {"warmup": (50, 60), "recovery": (45, 55), "cooldown": (45, 55), "work": (85, 95)}
DEFAULT_BANDS_CAD = {"warmup": (75, 85), "recovery": (75, 85), "cooldown": (75, 85), "work": (85, 95)}
DEFAULT_BANDS_SPD = {"warmup": (18, 22), "recovery": (16, 20), "cooldown": (16, 20), "work": (28, 32)}


def _warn(msg: str):
    print(f"bryton_from_intervals: {msg}", file=sys.stderr)


def _flatten(steps: list) -> list:
    """Expand repeatStart(N)/repeatEnd brackets into flat, repeated leaf steps."""
    out, i = [], 0
    while i < len(steps):
        st = steps[i]
        tn = st.get("type", {}).get("typeName")
        if tn == "repeatStart":
            count = int(st["type"].get("value", 1))
            j, depth, inner = i + 1, 1, []
            while j < len(steps) and depth:
                t2 = steps[j].get("type", {}).get("typeName")
                if t2 == "repeatStart":
                    raise NotImplementedError("nested repeats are not supported")
                if t2 == "repeatEnd":
                    depth -= 1
                    if depth == 0:
                        break
                inner.append(steps[j]); j += 1
            for _ in range(count):
                out.extend(inner)
            i = j + 1
        elif tn == "repeatEnd":
            i += 1
        else:
            out.append(st); i += 1
    return out


def _pick_unit(steps: list, hr_unit: str) -> str:
    """Choose the whole-workout Bryton unit from the work/interval steps' targets."""
    fams = []
    for st in steps:
        phase = PHASE_TO_INTENSITY.get(st.get("type", {}).get("typeName"), "work")
        fam = TARGET_FAMILY.get(st.get("target", {}).get("targetName"))
        if phase == "work" and fam:
            fams.append(fam)
    if not fams:  # no work targets - fall back to any targeted step
        for st in steps:
            fam = TARGET_FAMILY.get(st.get("target", {}).get("targetName"))
            if fam:
                fams.append(fam)
    if not fams:
        raise ValueError("no usable target in any step; pass --unit to choose one explicitly")
    fam = max(set(fams), key=fams.count)
    return hr_unit if fam == "hr" else FAMILY_TO_UNIT[fam]


def _target_to_unit(target: dict, unit: str, athlete: dict):
    """A normalized target -> (low, high) in the workout's unit, or None if not expressible."""
    name = target.get("targetName")
    fam = TARGET_FAMILY.get(name)
    vr = target.get("valueRange") or {}
    lo, hi = vr.get("min"), vr.get("max")
    if fam is None or lo is None or hi is None:
        return None
    lo, hi = float(lo), float(hi)
    if lo > hi:
        lo, hi = hi, lo

    if unit == "ftp":
        if fam != "power":
            return None
        ftp = athlete.get("ftp")
        if not ftp:
            raise ValueError("power target needs --ftp")
        return (round(lo / ftp * 100), round(hi / ftp * 100))
    if unit in ("mhr", "lthr"):
        if fam != "hr":
            return None
        ref = athlete.get("max_hr") if unit == "mhr" else athlete.get("lthr")
        if not ref:
            raise ValueError(f"HR target needs --{'max-hr' if unit == 'mhr' else 'lthr'}")
        return (round(lo / ref * 100), round(hi / ref * 100))
    if unit == "speed":
        if fam != "speed":
            return None
        # pace/speed values in the schema are m/s -> km/h
        return (round(lo * 3.6, 1), round(hi * 3.6, 1))
    if unit == "cadence":
        if fam != "cadence":
            return None
        return (round(lo), round(hi))
    return None


def convert(workout: dict, athlete: dict, unit: str | None = None,
            hr_unit: str = "mhr", overrides: dict | None = None) -> dict:
    """Project workout schema -> a `bryton_workout` dict."""
    raw_steps = workout.get("steps")
    if not raw_steps:
        raise ValueError("workout has no steps")
    steps = _flatten(raw_steps)

    if unit is None:
        unit = _pick_unit(steps, hr_unit)

    # one duration mode for the whole workout
    modes = {("distance" if s.get("duration", {}).get("durationName") == "distance" else "time")
             for s in steps if s.get("duration")}
    if len(modes) > 1:
        raise ValueError("Bryton needs one interval mode per workout, but steps mix time and "
                         "distance; split the workout or make them uniform")
    mode = modes.pop() if modes else "time"

    default_bands = (DEFAULT_BANDS_CAD if unit == "cadence"
                     else DEFAULT_BANDS_SPD if unit == "speed" else DEFAULT_BANDS_PCT)
    if overrides:
        default_bands = {**default_bands, **overrides}

    out_steps = []
    for st in steps:
        phase = PHASE_TO_INTENSITY.get(st.get("type", {}).get("typeName"), "work")
        dur = st.get("duration") or {}
        val = dur.get("value")
        if not val:
            raise ValueError(f"step has no duration value: {st!r}")
        duration = float(val)  # seconds (time) or metres (distance)

        band = _target_to_unit(st.get("target", {}), unit, athlete)
        if band is None:
            lo, hi = default_bands[phase]
            tname = st.get("target", {}).get("targetName", "none")
            _warn(f"{phase} step target ({tname}) not expressible in {unit}; "
                  f"using default {lo}-{hi}")
        else:
            lo, hi = band
        out_steps.append({"intensity": phase, "duration": duration, "low": lo, "high": hi})

    return {
        "name": workout.get("name", "Workout"),
        "sport": 2,
        "unit": unit,
        "based_on": "range",
        "interval_mode": mode,
        "steps": out_steps,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workout", help="workout JSON in the project schema, or - for stdin")
    ap.add_argument("-o", "--out", help="output .fit (default: <name>.fit)")
    ap.add_argument("--ftp", type=float, help="athlete FTP in watts (for power targets)")
    ap.add_argument("--max-hr", type=float, help="athlete max HR in bpm (for MHR%% targets)")
    ap.add_argument("--lthr", type=float, help="athlete LTHR in bpm (for LTHR%% targets)")
    ap.add_argument("--unit", choices=list(bw.UNIT_TO_TARGET), help="force the workout unit")
    ap.add_argument("--hr-unit", choices=["mhr", "lthr"], default="mhr",
                    help="which Bryton unit HR targets become (default: mhr)")
    args = ap.parse_args(argv)

    src = sys.stdin.read() if args.workout == "-" else open(args.workout).read()
    doc = json.loads(src)
    doc = doc.get("workout", doc)  # accept a {"workout": {...}} wrapper
    athlete = {"ftp": args.ftp, "max_hr": args.max_hr, "lthr": args.lthr}

    wk = convert(doc, athlete, unit=args.unit, hr_unit=args.hr_unit)
    blob = bw.encode(wk)
    out = args.out or f"{wk['name']}.fit"
    with open(out, "wb") as fh:
        fh.write(blob)
    print(f"wrote {out} ({len(blob)} bytes) - {len(wk['steps'])} steps, "
          f"{wk['unit']}/{wk['interval_mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
