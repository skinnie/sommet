#!/usr/bin/env python3
"""Generates App Zone source for a structured, multi-step workout, and compiles it through
the live community compiler - the concrete design from `docs/training_program_andre.md` Finding 8
(GUI -> source generator -> live compiler -> IAMRULE install), prototyped here.

**This tool stops at "produce a real, compiled IAMRULE binary."** It does not install
anything on a watch. Correction to an earlier claim in this session's own notes: the one real
Suunto App install this project has ever seen ("Climb counter", `docs/custom_modes_andre.md`) was
done by real SuuntoLink software, not by any tool in this project - `apps.py` only decodes the
result. Writing a compiled app into the `Apps` flash region and wiring it into a `CustomModes`
display slot is real, separate, not-yet-built work.

Input: a JSON workout, the schema this project reverse-engineered from
`openambitproject/openambit#257` and independently confirmed against wanarun.net and Suunto's
own French tutorial (`docs/training_program_andre.md` Findings 5/9/10):

    {
      "name": "5x3min power intervals",
      "steps": [
        {"type": {"typeName": "warmup"},
         "duration": {"durationName": "time", "value": 600},
         "target": {"targetName": "hr", "valueRange": {"min": 120, "max": 140}},
         "notify": {"beep": true, "light": true}},
        {"type": {"typeName": "repeatStart", "value": 5}},
        {"type": {"typeName": "interval"},
         "duration": {"durationName": "time", "value": 180},
         "target": {"targetName": "power", "valueRange": {"min": 200, "max": 220}}},
        {"type": {"typeName": "recovery"},
         "duration": {"durationName": "time", "value": 90},
         "target": {"targetName": "none"}},
        {"type": {"typeName": "repeatEnd"}},
        {"type": {"typeName": "cooldown"},
         "duration": {"durationName": "time", "value": 300},
         "target": {"targetName": "none"}}
      ]
    }

`duration.durationName`: `time` (seconds) / `distance` (meters) / `ascent` (meters) / `lap`
(advance on the next Back Lap press - `SUUNTO_LAP_NUMBER`, same signal Seb's real IntervalAIO
app uses) / `energy` (kcal, `SUUNTO_ENERGY`) / `hr_below` / `hr_above` (step ends when
`SUUNTO_HR` crosses that bpm value - instantaneous threshold, no counter). The last three come
from the decompiled Movescount Android app's own `WorkoutStepDuration` enum (2026-08-19
cross-check) and are all real App-Zone built-ins per the manual. `target.targetName`: `none` /
`hr` / `pace` / `speed` / `vertical_speed` / `power` / `cadence` (`SUUNTO_CADENCE`, likewise
from the Android `WorkoutStepTarget` enum) - all real built-in App Zone variables
(`SuuntoAppZoneDeveloperManual.pdf`), not repurposed
personal-settings fields (`docs/training_program_andre.md` Finding 8's whole point). `notify`
(optional, on a step - it governs the alert fired on *entering* that step): `{"beep": bool,
"light": bool}`, both default `true` if omitted.

Generates one unrolled `PHASE == k` branch per expanded step - repeat blocks are flattened at
*generation* time, not looped at *runtime*, because App Zone has no arrays or loops over data
(confirmed empirically against all six of `assets/Intervals`' real scripts - see Finding 8).
Same architecture Sebastien Chastang's real `IntervalAIO` app uses for its own fixed 2-phase
loop, generalized here to N literal phases with a duration-type per phase instead of one fixed
fast/slow shape.

Target ranges now do something real, not just cosmetic labels: whenever a phase has a
`target` other than `none`, the generated script watches the matching live built-in
(`SUUNTO_HR`/`SUUNTO_PACE`/`SUUNTO_SPEED`/`SUUNTO_VERTICAL_SPD`/`SUUNTO_BIKE_POWER`) every tick
and fires one `Suunto.alarmBeep()` the moment it leaves `valueRange`, debounced (`OUT_OF_RANGE`)
so it beeps once per excursion rather than continuously. The live numeric display itself still
shows remaining time/distance, not the target value - App Zone's single `RESULT` field can't
show both at once, and an audible alert is the more actionable signal of the two anyway.

Honest limitation, not hidden: this drives Seb's plain `prefix`/`RESULT`/`postfix` text field,
not the bounded live graph the authentic Movescount-era guided-workout screen used
(`docs/training_program_andre.md` Finding 9) - that looks like a native firmware display type this
project hasn't found a way to drive from App Zone source.

    ./tools/workout.py WORKOUT.json --print-source
    ./tools/workout.py WORKOUT.json --compile --out compiled.json
"""

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

COMPILE_URL = "https://ambitappscompiler.azurewebsites.net/api/compile"


# pavel.samokha published this x-functions-key himself, in the open, on the Suunto community
# forum (https://forum.suunto.com/topic/7592/ambit-apps-compilation) as the documented way for
# anyone to call his Ambit Apps compiler. It is not a secret we hold - it is his public service
# key - so shipping it just lets the app compile out of the box the way he intended. A local key
# (env / .compile_key / ~/.config) still overrides it, e.g. if he ever rotates it.
PUBLIC_COMPILE_KEY = "qakj8vb/62VgzmSl19mDQzRmW7nCSDv0zAo2A1aC3ldfd8U8r5jksA=="


def _load_compile_key():
    """The compiler's x-functions-key. In priority:
      1. env var COMPILE_KEY
      2. tools/.compile_key
      3. ~/.config/ambitapp/compile_key
      4. PUBLIC_COMPILE_KEY - pavel's own forum-published key, so it works with no setup."""
    if os.environ.get("COMPILE_KEY"):
        return os.environ["COMPILE_KEY"].strip()
    for p in (pathlib.Path(__file__).with_name(".compile_key"),
              pathlib.Path.home() / ".config" / "ambitapp" / "compile_key"):
        try:
            return p.read_text().strip()
        except OSError:
            continue
    return PUBLIC_COMPILE_KEY


COMPILE_KEY = _load_compile_key()

DURATION_VARS = {
    "time": "SUUNTO_DURATION",
    "distance": "SUUNTO_DISTANCE",
    "ascent": "SUUNTO_ASCENT",
    "energy": "SUUNTO_ENERGY",      # kcal, App-Zone built-in (manual: range 0-60000)
}
DURATION_POSTFIX = {"time": "s", "distance": "m", "ascent": "m+", "lap": "",
                    # ≤2 chars: the compiler caps prefix+postfix at 6 chars/phase AND postfix at 2
                    # (empirically, 2026-08-19), so "kcal"/"bpm" are abbreviated. HR steps show the
                    # live bpm value as RESULT with no postfix - the number reads clearly on its own.
                    "energy": "kc", "hr_below": "", "hr_above": ""}
LABEL_BUDGET = 6   # compiler limit: prefix + postfix per phase must be <= this many chars
# durations that end on an INSTANTANEOUS condition, not an accumulated counter (no START_COUNTER):
# lap (lap-button press) and the two HR-threshold triggers (step ends when HR crosses the bpm value).
COUNTER_LESS_DURATIONS = {"lap", "hr_below", "hr_above"}
TARGET_VARS = {
    "hr": "SUUNTO_HR",
    "pace": "SUUNTO_PACE",
    "speed": "SUUNTO_SPEED",
    "vertical_speed": "SUUNTO_VERTICAL_SPD",
    "power": "SUUNTO_BIKE_POWER",
    "cadence": "SUUNTO_CADENCE",     # App-Zone built-in
}
TYPE_LABELS = {"warmup": "Warm", "interval": "Fast", "recovery": "Rec", "cooldown": "Cool"}


def expand_steps(workout):
    """Flattens repeatStart/repeatEnd blocks into a plain list of real steps. Nested repeats
    aren't supported (none of the real examples this project has seen use them)."""
    flat = []
    steps = workout["steps"]
    i = 0
    while i < len(steps):
        step = steps[i]
        type_name = step["type"]["typeName"]
        if type_name == "repeatStart":
            count = step["type"]["value"]
            block = []
            i += 1
            while steps[i]["type"]["typeName"] != "repeatEnd":
                if steps[i]["type"]["typeName"] == "repeatStart":
                    raise NotImplementedError("nested repeat blocks aren't supported")
                block.append(steps[i])
                i += 1
            flat.extend(block * count)
        elif type_name == "repeatEnd":
            raise ValueError("repeatEnd without a matching repeatStart")
        else:
            flat.append(step)
        i += 1
    return flat


def _phase_condition(step):
    """The App-Zone expression that becomes true once this phase's own duration is complete."""
    duration = step["duration"]
    kind = duration["durationName"]
    if kind == "lap":
        return "SUUNTO_LAP_NUMBER > CURRENT_LAP_NUMBER"
    if kind == "hr_below":
        return f"SUUNTO_HR < {duration['value']}"
    if kind == "hr_above":
        return f"SUUNTO_HR > {duration['value']}"
    return f"({DURATION_VARS[kind]} - START_COUNTER) >= {duration['value']}"


def _phase_label(step, max_len=LABEL_BUDGET):
    """The label shown as the field prefix while this step runs. Honors the step's own `text`
    (the tutorial's "text pops up at the beginning of the segment") when present, else falls
    back to the phase-type word ("Warm"/"Fast"/"Rec"/"Cool"). CONSTRAINTS: the live community
    compiler rejects any string literal containing a DIGIT (confirmed 2026-08-05 - "Warm"/"Warm X"
    compile, "Warm140"/"Warm 120" fail COMPILATION_FAILED), and prefix+postfix per phase must fit
    the compiler's LABEL_BUDGET (6 chars) - so user text is stripped of digits and truncated to
    `max_len` (the caller passes 6 minus this phase's postfix length). (The numeric target range /
    native step graph still aren't shown live - app-zone limit, see the module docstring.)"""
    text = (step.get("text") or "").strip()
    if text:
        clean = "".join(c for c in text if not c.isdigit()).strip()
        if clean:
            return clean[:max_len]
    return TYPE_LABELS.get(step["type"]["typeName"],
                           step["type"]["typeName"][:4].capitalize())[:max_len]


def generate_source(workout):
    """Returns (app_zone_source_text, own_variable_names) for the given workout JSON."""
    steps = expand_steps(workout)
    if not steps:
        raise ValueError("workout has no real steps")

    lines = ["if (PHASE <= 0) {", "\tPHASE = 1;",
              f"\tSTART_COUNTER = {DURATION_VARS.get(steps[0]['duration']['durationName'], 'SUUNTO_DURATION')};",
              "\tCURRENT_LAP_NUMBER = SUUNTO_LAP_NUMBER;", "} else {"]

    for idx, step in enumerate(steps):
        phase_num = idx + 1
        keyword = "\tif" if idx == 0 else "\t} else if"
        lines.append(f"{keyword} (PHASE == {phase_num} && {_phase_condition(step)}) {{")
        lines.append(f"\t\tPHASE = {phase_num + 1};")
        if idx + 1 < len(steps):
            next_kind = steps[idx + 1]["duration"]["durationName"]
            next_var = DURATION_VARS.get(next_kind, "SUUNTO_DISTANCE")
            if next_kind not in COUNTER_LESS_DURATIONS:
                lines.append(f"\t\tSTART_COUNTER = {next_var};")
        lines.append("\t\tCURRENT_LAP_NUMBER = SUUNTO_LAP_NUMBER;")
        # notify() defaults both on - matches this generator's original always-on behavior,
        # so older/imported workouts without a "notify" key still alert exactly as before.
        entering = steps[idx + 1] if idx + 1 < len(steps) else None
        notify = (entering or {}).get("notify", {"beep": True, "light": True})
        if notify.get("beep", True):
            lines.append("\t\tSuunto.alarmBeep();")
        if notify.get("light", True):
            lines.append("\t\tSuunto.light();")
    lines.append("\t}")
    lines.append("}")
    lines.append("")

    lines.append("if (PHASE < 1) {")
    lines.append('\tprefix = "";')
    lines.append("\tRESULT = 0;")
    for idx, step in enumerate(steps):
        phase_num = idx + 1
        duration = step["duration"]
        kind = duration["durationName"]
        postfix = DURATION_POSTFIX.get(kind, "")[:2]              # postfix cap: 2 chars
        label = _phase_label(step, max_len=LABEL_BUDGET - len(postfix))  # keep prefix+postfix <= 6
        lines.append(f"}} else if (PHASE == {phase_num}) {{")
        lines.append(f'\tprefix = "{label}";')
        lines.append(f'\tpostfix = "{postfix}";')
        if kind == "lap":
            lines.append("\tRESULT = SUUNTO_LAP_NUMBER - CURRENT_LAP_NUMBER;")
        elif kind in ("hr_below", "hr_above"):
            # threshold step: no countdown to show; display live HR (step ends when it crosses)
            lines.append("\tRESULT = SUUNTO_HR;")
        else:
            lines.append(f"\tRESULT = {duration['value']} - "
                          f"({DURATION_VARS[kind]} - START_COUNTER);")
    lines.append("} else {")
    lines.append('\tprefix = "Done";')
    lines.append('\tpostfix = "";')
    lines.append("\tRESULT = 0;")
    lines.append("}")

    own_vars = ["PHASE", "START_COUNTER", "CURRENT_LAP_NUMBER"]

    has_targets = any(
        step.get("target", {}).get("targetName", "none") in TARGET_VARS for step in steps)
    if has_targets:
        # One alarmBeep() the moment the live target variable (real built-in - SUUNTO_HR/PACE/
        # SPEED/VERTICAL_SPD/BIKE_POWER, not a personal-settings field) leaves this phase's
        # range, debounced by OUT_OF_RANGE so it beeps once per excursion, not every tick.
        lines.append("")
        lines.append("if (PHASE < 1) {")
        lines.append("\tOUT_OF_RANGE = 0;")
        for idx, step in enumerate(steps):
            phase_num = idx + 1
            target = step.get("target", {"targetName": "none"})
            target_name = target.get("targetName", "none")
            lines.append(f"}} else if (PHASE == {phase_num}) {{")
            if target_name not in TARGET_VARS:
                lines.append("\tOUT_OF_RANGE = 0;")
                continue
            var = TARGET_VARS[target_name]
            rng = target["valueRange"]
            lines.append(f"\tif ({var} < {rng['min']} || {var} > {rng['max']}) {{")
            lines.append("\t\tif (OUT_OF_RANGE == 0) {")
            lines.append("\t\t\tSuunto.alarmBeep();")
            lines.append("\t\t\tOUT_OF_RANGE = 1;")
            lines.append("\t\t}")
            lines.append("\t} else {")
            lines.append("\t\tOUT_OF_RANGE = 0;")
            lines.append("\t}")
        lines.append("} else {")
        lines.append("\tOUT_OF_RANGE = 0;")
        lines.append("}")
        own_vars.append("OUT_OF_RANGE")

    return "\n".join(lines) + "\n", own_vars


def build_compile_request(source, own_vars, name):
    """Wraps generated source in the compiler's `/***HEADER***/` block - reverse-engineered
    from the compiler UI's own default placeholder (`main.js`), which shows own-variable
    declarations (`print = 0`, `myCounter = 0`, ...) inside that block, not script metadata."""
    header_lines = [f"{v} = 0" for v in own_vars]
    header = "/***HEADER***/\n" + "\n".join(header_lines) + "\n/***ENDHEADER***/\n"
    return header + source


def compile_source(request_text, verbose=False):
    if not COMPILE_KEY or COMPILE_KEY == "***REMOVED***":
        raise RuntimeError(
            "no compiler key. Provide the community/pavel App-Zone compiler's x-functions-key "
            "via env COMPILE_KEY, or tools/.compile_key, or ~/.config/ambitapp/compile_key "
            "(all gitignored - never commit it).")
    req = urllib.request.Request(
        COMPILE_URL, data=request_text.encode("utf-8"), method="POST",
        headers={"Content-Type": "text/plain", "x-functions-key": COMPILE_KEY},
    )
    if verbose:
        print("--- POST body ---", file=sys.stderr)
        print(request_text, file=sys.stderr)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"compile failed: HTTP {e.code}: {body}") from None
    except urllib.error.URLError as e:
        # No HTTP response at all - no connection, DNS failure, timeout. Compiling needs the
        # live community compiler (this project doesn't run its own); everything else in the
        # packaged app (building a workout, History, exporting, "Add to SuuntoLink") works
        # offline, only this network call can't. Caught specifically so the GUI shows a plain
        # "you're offline" message instead of an unhandled exception / raw 500.
        raise RuntimeError(
            "couldn't reach the compiler (ambitappscompiler.azurewebsites.net) - this needs "
            f"an internet connection; everything else here works offline. ({e.reason})") from None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("workout", help="path to a workout JSON file (see this file's docstring"
                                     " for the schema)")
    ap.add_argument("--print-source", action="store_true", help="print the generated"
                     " App Zone source and exit, no network call")
    ap.add_argument("--compile", action="store_true", help="POST the generated source to the"
                     " live community compiler and print the result")
    ap.add_argument("--out", metavar="FILE", help="save the compile response JSON here"
                     " (with --compile)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    with open(args.workout) as f:
        workout = json.load(f)

    source, own_vars = generate_source(workout)
    request_text = build_compile_request(source, own_vars, workout.get("name", "Workout"))

    if args.print_source or not args.compile:
        print(request_text)
        if not args.compile:
            return 0

    result = compile_source(request_text, verbose=args.verbose)
    binary = result.get("binary", [])
    print(f"compiled OK: name={result.get('name')!r} activityId={result.get('activityId')}"
          f" categoryId={result.get('categoryId')} binary_length={len(binary)}"
          f" compatibleVariants={result.get('compatibleVariants')}")
    if args.out:
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
