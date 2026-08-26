#!/usr/bin/env python3
"""Convert an intervals.icu workout export (.json) into this project's Ambit3 workout schema,
ready for `guided_workout.compile_workout()` -> the native guidance binary -> the watch's
WORKOUT menu.

Feature request (Andre, 2026-08-26): intervals.icu can download a planned workout as JSON;
that is a DIFFERENT schema from ours, but a nearly 1:1 one, so a direct converter is small.

**The two schemas, side by side** (left: intervals.icu export, right: `workout.py`):

    {"target": "HR",                          {"name": "...",
     "steps": [                                "steps": [
       {"text": "Brisk Walk",                    {"type": {"typeName": "warmup"},
        "duration": 300, "warmup": true,          "duration": {"durationName": "time",
        "hr": {"value": 1,                                     "value": 300},
               "units": "hr_zone"},               "target": {"targetName": "hr",
        "_hr": {"start": 125.0,                              "valueRange": {"min": 125,
                "end": 157.0}},                                             "max": 157}},
                                                  "text": "Brisk Walk"},
       {"text": "8x", "reps": 8,                 {"type": {"typeName": "repeatStart",
        "duration": 1200,                                  "value": 8}},
        "steps": [ ...2 sub-steps... ]}          ...the 2 sub-steps...
     ]}                                          {"type": {"typeName": "repeatEnd"}}]}

Three real differences, all handled here:

1. **Repeats nest, ours flatten.** intervals.icu puts the repeated steps in a child `steps[]`
   array on a step carrying `reps`. Ours uses a flat `repeatStart(N) ... repeatEnd` bracket
   (`workout.py:expand_steps`). One level is all intervals.icu emits and all ours supports.

2. **Step TYPE is implied, not stated.** intervals.icu labels a step with free text ("Jog",
   "Walk", "Brisk Walk") plus an optional `warmup`/`cooldown` boolean; it has no
   warmup/interval/recovery/cooldown enum. We keep the text verbatim as the on-watch label and
   GUESS the type from it (see `guess_type`) - the guess only picks the fallback phase word and
   has no effect on what the watch actually does, because the real intensity gate is the HR
   target, which is carried exactly.

3. **HR arrives twice, resolved and unresolved.** `hr` is a ZONE INDEX (`{"value": 1, "units":
   "hr_zone"}`); `_hr` is intervals.icu's own resolution of that zone against the athlete's
   `sportSettings.hr_zones` into ABSOLUTE bpm (`{"start": 125.0, "end": 157.0}`). We take `_hr`
   and ignore the zone index - it is already the athlete's real numbers, so the converted
   workout needs no zone table of its own. Plain bpm is correct for our compiler path; the
   bpm/60 encoding seen in some captured Movescount-era workout JSON is a different era's
   quirk and is NOT what our compiler wants.

**A missing cooldown, deliberately not invented by default.** Compared against the real
SuuntoLink catalogue app of the same program ("Couch-to-5K Week1": *"Total 30min - Alternate
1:00min jog / 1:30min walk - 5min walk beginning / end"*), an intervals.icu C25K export is
short by exactly its final 5min walk: 300s warmup + 8x(60+90) = 1500s = 25min, and its own
top-level `duration` field agrees at 1500. The jog/walk block and the warmup match the real app
exactly. So the export really does drop the trailing cooldown. Reconstructing it is a judgement
call about data that is not in the file, so it is opt-in: `--cooldown SECONDS`, or
`--cooldown auto` to take `sportSettings.cooldown_time`.

    ./tools/intervals_workout.py W1_Workout.json                       # convert, print schema
    ./tools/intervals_workout.py W1_Workout.json --cooldown 300 -o w1.json
    ./tools/intervals_workout.py C25K/*.json --out-dir converted/      # batch
    ./tools/intervals_workout.py W1_Workout.json --compile             # + guidance binary
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

# Ours is the schema `workout.py` documents; these are its enum values, kept here so a bad
# conversion fails loudly in this file rather than as a compiler error later.
TYPE_WARMUP, TYPE_INTERVAL = "warmup", "interval"
TYPE_RECOVERY, TYPE_COOLDOWN = "recovery", "cooldown"

# Free-text -> phase type. intervals.icu has no type enum, so this reads the label the athlete
# (or the plan author) wrote. Order matters: "Brisk Walk" must hit "walk", not a generic default.
TEXT_TYPE_HINTS = (
    (("warm", "wu"), TYPE_WARMUP),
    (("cool", "cd"), TYPE_COOLDOWN),
    (("walk", "rest", "recover", "easy", "rec"), TYPE_RECOVERY),
    (("jog", "run", "fast", "hard", "work", "interval", "tempo", "effort"), TYPE_INTERVAL),
)

# intervals.icu's resolved (underscore-prefixed) target keys -> our targetName. It writes the
# resolved absolute value alongside the zone/%-of-threshold the user actually typed; only the
# resolved one is portable to a watch that knows nothing about the athlete's zones.
RESOLVED_TARGETS = (("_hr", "hr"), ("_power", "power"), ("_pace", "pace"))


def guess_type(step: dict) -> str:
    """The phase type for an intervals.icu step. Explicit flags win; otherwise read the label.

    This only decides the FALLBACK on-watch word for a step with no text of its own, and how the
    step reads in the converted JSON - the watch's behaviour comes from the duration and the HR
    target, both of which are carried exactly. So a wrong guess here is cosmetic, not functional.
    """
    if step.get("warmup"):
        return TYPE_WARMUP
    if step.get("cooldown"):
        return TYPE_COOLDOWN
    text = (step.get("text") or "").strip().lower()
    for needles, type_name in TEXT_TYPE_HINTS:
        if any(n in text for n in needles):
            return type_name
    return TYPE_INTERVAL


def convert_target(step: dict) -> dict:
    """intervals.icu step target -> our `target` object.

    Prefers the RESOLVED absolute value (`_hr`/`_power`/`_pace`) over the zone index in `hr`,
    because the resolved one is real bpm/watts and needs no zone table on the far side. Returns
    `{"targetName": "none"}` for a step with no target at all - a legitimate, common case.
    """
    for key, target_name in RESOLVED_TARGETS:
        band = step.get(key)
        if not isinstance(band, dict):
            continue
        lo, hi = band.get("start"), band.get("end")
        if lo is None or hi is None:
            continue
        lo, hi = round(float(lo)), round(float(hi))
        if lo > hi:
            lo, hi = hi, lo
        return {"targetName": target_name, "valueRange": {"min": lo, "max": hi}}
    return {"targetName": "none"}


def convert_duration(step: dict) -> dict:
    """intervals.icu step duration -> our `duration` object.

    `duration` is seconds. A step can instead be distance-bound (`distance` in metres, with
    `duration` absent or zero) - intervals.icu emits `distance: 0.0` on time steps, so a
    zero distance is NOT a distance step.
    """
    seconds = step.get("duration")
    if seconds:
        return {"durationName": "time", "value": int(round(float(seconds)))}
    metres = step.get("distance")
    if metres:
        return {"durationName": "distance", "value": int(round(float(metres)))}
    raise ValueError(f"step has neither a duration nor a distance: {step!r}")


def convert_step(step: dict) -> dict:
    """One leaf (non-repeat) intervals.icu step -> one of our steps."""
    out = {
        "type": {"typeName": guess_type(step)},
        "duration": convert_duration(step),
        "target": convert_target(step),
    }
    text = (step.get("text") or "").strip()
    if text:
        # Kept verbatim; workout.py's _phase_label does the compiler's own sanitising (strips
        # digits, truncates to the 6-char label budget). Doing it here too would only lose
        # information that a future non-app-zone consumer of this JSON might want.
        out["text"] = text
    return out


def convert_steps(icu_steps: list) -> list:
    """The step list, with intervals.icu's nested repeat blocks turned into our flat brackets."""
    out = []
    for step in icu_steps:
        children, reps = step.get("steps"), step.get("reps")
        if children:
            count = int(reps or 1)
            if any(child.get("steps") for child in children):
                raise NotImplementedError(
                    "nested repeat inside a repeat - neither intervals.icu exports these nor "
                    "does workout.py's expand_steps support them")
            if count > 1:
                out.append({"type": {"typeName": "repeatStart", "value": count}})
                out.extend(convert_step(c) for c in children)
                out.append({"type": {"typeName": "repeatEnd"}})
            else:
                # A 1x block is just its contents; a repeatStart(1) bracket would be noise.
                out.extend(convert_step(c) for c in children)
        else:
            out.append(convert_step(step))
    return out


def _cooldown_seconds(icu: dict, spec) -> int | None:
    """Resolve --cooldown. `auto` reads sportSettings.cooldown_time (intervals.icu's own default
    for this sport); an integer is taken literally. Returns None when no cooldown was asked for."""
    if spec is None:
        return None
    if str(spec).lower() == "auto":
        seconds = (icu.get("sportSettings") or {}).get("cooldown_time")
        if not seconds:
            raise ValueError("--cooldown auto, but this file has no sportSettings.cooldown_time")
        return int(seconds)
    seconds = int(spec)
    if seconds <= 0:
        raise ValueError("--cooldown must be a positive number of seconds, or 'auto'")
    return seconds


def convert(icu: dict, name: str, cooldown=None, activity_id: int = 3) -> dict:
    """A parsed intervals.icu export -> a workout dict in this project's schema.

    `cooldown` is opt-in reconstruction of the trailing recovery the export drops (see the module
    docstring): seconds, or "auto" for sportSettings.cooldown_time. It reuses the HR target of the
    workout's last easy step so the added step is consistent with the rest of the plan rather than
    untargeted.
    """
    icu_steps = icu.get("steps")
    if not icu_steps:
        raise ValueError("no steps in this file - is it really an intervals.icu workout export?")

    steps = convert_steps(icu_steps)
    seconds = _cooldown_seconds(icu, cooldown)
    if seconds:
        # Borrow the target of the last recovery-ish step (the walk), so a reconstructed 5min
        # walk cools down in the same HR band the plan already walks in.
        target = {"targetName": "none"}
        for step in reversed(steps):
            if step["type"]["typeName"] == TYPE_RECOVERY:
                target = step["target"]
                break
        steps.append({
            "type": {"typeName": TYPE_COOLDOWN},
            "duration": {"durationName": "time", "value": seconds},
            "target": target,
            "text": "Cool Down",
        })

    return {"name": name, "activityId": activity_id, "steps": steps}


def total_seconds(workout: dict) -> int:
    """Wall-clock length of a converted workout, repeats expanded - for the summary line and for
    checking a conversion against the source file's own top-level `duration`."""
    total, i, steps = 0, 0, workout["steps"]
    while i < len(steps):
        type_name = steps[i]["type"]["typeName"]
        if type_name == "repeatStart":
            count, block, i = steps[i]["type"]["value"], 0, i + 1
            while steps[i]["type"]["typeName"] != "repeatEnd":
                if steps[i]["duration"]["durationName"] == "time":
                    block += steps[i]["duration"]["value"]
                i += 1
            total += block * count
        elif type_name != "repeatEnd" and steps[i]["duration"]["durationName"] == "time":
            total += steps[i]["duration"]["value"]
        i += 1
    return total


def default_name(path: pathlib.Path) -> str:
    """A workout name from the filename - intervals.icu's export carries no name field of its own
    (its `description` is a literal "# Description" placeholder). "W1_Workout (2).json" -> "W1"."""
    stem = path.stem
    for suffix in ("_Workout", "_workout"):
        stem = stem.replace(suffix, "")
    return " ".join(stem.replace("_", " ").split()) or path.stem


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=pathlib.Path,
                    help="intervals.icu workout export .json file(s)")
    ap.add_argument("--name", help="workout name (default: from the filename)")
    ap.add_argument("--cooldown", metavar="SECONDS|auto",
                    help="append a cooldown the export omits; 'auto' uses "
                         "sportSettings.cooldown_time")
    ap.add_argument("--activity-id", type=int, default=3,
                    help="Suunto activity id for the workout (default 3 = Running)")
    ap.add_argument("-o", "--out", type=pathlib.Path, help="write the converted JSON here")
    ap.add_argument("--out-dir", type=pathlib.Path,
                    help="write one converted JSON per input file into this directory")
    ap.add_argument("--compile", action="store_true",
                    help="also compile via the community compiler and report the binary size")
    args = ap.parse_args()

    if args.out and len(args.files) > 1:
        ap.error("--out takes a single input file; use --out-dir for several")
    if args.name and len(args.files) > 1:
        ap.error("--name takes a single input file")
    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    for path in args.files:
        try:
            icu = json.loads(path.read_text())
            workout = convert(icu, args.name or default_name(path),
                              cooldown=args.cooldown, activity_id=args.activity_id)
        except (OSError, ValueError, NotImplementedError) as e:
            print(f"{path.name}: {type(e).__name__}: {e}", file=sys.stderr)
            failures += 1
            continue

        blob = json.dumps(workout, indent=2)
        out_path = args.out or (args.out_dir / f"{path.stem}.ambit.json" if args.out_dir else None)
        if out_path:
            out_path.write_text(blob + "\n")

        # The source file's own `duration` is intervals.icu's total; comparing it against ours is
        # a free end-to-end check that no step or repeat was lost in translation.
        ours, theirs = total_seconds(workout), icu.get("duration")
        added = _cooldown_seconds(icu, args.cooldown) or 0
        drift = "" if theirs is None else f" (source says {theirs}s"
        if theirs is not None:
            expected = theirs + added
            drift += ")" if ours == expected else f", WE SAY {ours}s - MISMATCH)"
        summary = (f"{path.name}: {len(workout['steps'])} steps, {ours}s"
                   f"{drift if theirs is not None else ''}")

        if args.compile:
            sys.path.insert(0, str(pathlib.Path(__file__).parent))
            import guided_workout
            try:
                compiled = guided_workout.compile_workout(workout)
            except SystemExit as e:      # compile_workout raises SystemExit on compiler errors
                print(f"{summary} -> compile FAILED: {e}", file=sys.stderr)
                failures += 1
                continue
            summary += f" -> {len(compiled['binary'])} B guidance binary"

        print(summary)
        if not out_path and not args.compile:
            print(blob)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
