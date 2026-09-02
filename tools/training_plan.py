#!/usr/bin/env python3
"""Training Program: schedules real workouts on real calendar dates, as date-gated Suunto
Apps - the from-scratch replacement for the Movescount-era "Training programs / planned
moves" feature (`docs/training_program_andre.md` Finding 57).

Why this design, given the native route is walled off: the watch's dedicated
`TrainingProgram` flash region is fully decoded and writable, but the firmware never
surfaces what we write there (Findings 30-32 - the load/display trigger needs ground truth
only a live Movescount could give). This tool takes the other, fully-proven road instead:
the App Zone language exposes the watch's own date as `SUUNTO_DAYS_AFTER_1_1_2000` (manual
page "TIME"; 22 real published Movescount-era apps in `appzone_corpus/` - calendars,
sunrise/sunset, moon phase - drive off it, and their weekday math pins it 0-based on
2000-01-01, a Saturday). So a scheduled workout is an ordinary compiled app whose whole body
is gated on `SUUNTO_DAYS_AFTER_1_1_2000 == <planned day>`: on the planned date, starting a
recording runs that day's workout guidance; on any other day the app shows a countdown
(days until the next planned workout). The watch schedules itself from its own clock -
nothing needs to be connected on the day.

Every mechanism underneath is already hardware-proven elsewhere in this project:
`workout.py`'s generator + the live community compiler (Finding 53) and
`workout_install.py`'s Apps+CustomModes writer (Findings 44/45/54/55/56). This file only
adds the date layer and the packing.

Packing, from live compiler probing (2026-08-12): the compiler rejects binaries past ~4 KB
(`BINARY_TOO_LARGE` - two 12-phase interval workouts gated in one app compiled to 3332 B,
three did not compile). So a plan is split across as few apps as fit: entries are packed
greedily and each pack is verified by actually compiling it, backing off to smaller packs on
`BINARY_TOO_LARGE` rather than trusting any local size estimate. Each app then occupies one
of the sport mode's 5 app slots (Finding 11's real per-mode ceiling), cycling on the same
display field.

Plan JSON schema (dates are ISO `YYYY-MM-DD`; `workout` is exactly `workout.py`'s own
schema, unchanged):

    {
      "name": "Marathon prep",
      "entries": [
        {"date": "2026-08-14", "workout": {"name": "5x3min intervals", "steps": [...]}},
        {"date": "2026-08-16", "workout": {"name": "Long run", "steps": [...]}}
      ]
    }

    ./tools/training_plan.py PLAN.json --print-source
    ./tools/training_plan.py PLAN.json --compile --out-dir DIR [--json]
"""

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workout import (build_compile_request, compile_source, generate_source)  # noqa: E402

EPOCH_2000 = datetime.date(2000, 1, 1)

# The community compiler's own hard cap, found by live probing (see module docstring). Used
# only as a first-guess pack size - the real arbiter is the compiler's own BINARY_TOO_LARGE.
MAX_WORKOUTS_PER_APP_GUESS = 2


# Hardware-verified 2026-09-02 on André's Ambit3 Sport (Finch): the firmware's actual
# SUUNTO_DAYS_AFTER_1_1_2000 for a given calendar date is ONE LESS than (date - 2000-01-01).days.
# A date-gated plan whose entry was today showed "In 1 d" with the watch clock confirmed correct
# (set over USB the same minute), i.e. the firmware reported today as our_value - 1. The corpus's
# 0-based-on-2000-01-01 assumption (from weekday math alone, never date-verified) was off by one:
# the built-in counts days STRICTLY AFTER 2000-01-01, so 2000-01-02 = 0, and today = ours - 1.
FIRMWARE_DAYS_OFFSET = 1


def date_to_days2000(iso_date):
    """ISO date -> the watch's real SUUNTO_DAYS_AFTER_1_1_2000 value for that date (hardware-
    verified, see FIRMWARE_DAYS_OFFSET). The built-in counts days strictly after 2000-01-01."""
    d = datetime.date.fromisoformat(iso_date)
    return (d - EPOCH_2000).days - FIRMWARE_DAYS_OFFSET


def _indent(source, prefix="\t"):
    return "\n".join(prefix + line if line else line for line in source.splitlines())


def build_app_source(entries):
    """One app's source: each entry's already-proven workout body gated on its planned day,
    plus a countdown (days to the next planned workout in this app) for every other day.

    The countdown branches are emitted latest-date-first so the earliest still-upcoming date
    wins (later assignments overwrite earlier ones in the same tick), giving "days until the
    NEXT workout" with plain chained ifs - App Zone has no min() over data. Labels carry no
    digits (the compiler's string lexer rejects digit characters in string literals -
    `workout.py`'s own documented quirk); the day count itself is the numeric RESULT.

    Label lengths are load-bearing, found by live compiler bisection (2026-08-12): a script
    compiles only while the LONGEST prefix string anywhere in it plus the LONGEST postfix
    string anywhere in it total <= 6 characters - 6 compiles, 7 fails with
    COMPILATION_FAILED at line -1. The budget is script-global, not per-branch: "Warm"+"s"
    and "In"+"days" each pass alone but fail together in one script (4+4=8). Presumably the
    watch's real display-row width around the numeric value. Hence countdown labels
    "In" + "d" below - the workout phases' own labels (up to "Warm"/"Cool" prefix + "m+"
    postfix, from workout.py) already spend 4+2 of the 6.

    Own-variables (PHASE etc.) are shared by all entries in one app on purpose: only one
    date gate can be active during any given recording, and App Zone own-variables reset to
    their compiled-in zero at each exercise start (Finding 8 - the reason Seb's apps re-seed
    them), so there is no cross-day state to collide."""
    merged_vars = []
    gates = []
    for entry in sorted(entries, key=lambda e: e["date"]):
        body, own_vars = generate_source(entry["workout"])
        for v in own_vars:
            if v not in merged_vars:
                merged_vars.append(v)
        day = date_to_days2000(entry["date"])
        gates.append(f"if (SUUNTO_DAYS_AFTER_1_1_2000 == {day}) {{\n"
                     f"{_indent(body)}\n}}\n")

    days = sorted(date_to_days2000(e["date"]) for e in entries)
    countdown = []
    for day in reversed(days):
        countdown.append(
            f"if (SUUNTO_DAYS_AFTER_1_1_2000 < {day}) {{\n"
            f"\tRESULT = {day} - SUUNTO_DAYS_AFTER_1_1_2000;\n"
            f"\tprefix = \"In\";\n"
            f"\tpostfix = \"d\";\n"
            f"}}\n")
    done = (f"if (SUUNTO_DAYS_AFTER_1_1_2000 > {days[-1]}) {{\n"
            f"\tRESULT = 0;\n"
            f"\tprefix = \"Done\";\n"
            f"}}\n")

    source = "".join(gates) + "".join(countdown) + done
    return source, merged_vars


def compile_pack(entries, name):
    """Compiles one pack of entries into one app. Returns the compiler's response dict with
    `name` overridden (the on-watch app name is written by our installer, not the compiler,
    so digits ARE allowed here - real catalog apps like "Couch-to-5K Week1" prove it).
    Raises RuntimeError (with BINARY_TOO_LARGE in the text when that's the cause)."""
    source, own_vars = build_app_source(entries)
    request = build_compile_request(source, own_vars, name)
    result = compile_source(request)
    result["name"] = name
    result["dates"] = sorted(e["date"] for e in entries)
    return result


def compile_plan(plan):
    """Packs the plan's entries into as few apps as actually compile, verified against the
    live compiler itself. Greedy: try MAX_WORKOUTS_PER_APP_GUESS per app, and on
    BINARY_TOO_LARGE halve the pack until it fits (a single workout that still doesn't fit
    is a real error for the user - their workout is too long for one app slot).

    Returns a list of compiled-app dicts (each: workout_install.py-compatible name/
    activityId/binary, plus `dates`)."""
    entries = sorted(plan.get("entries", []), key=lambda e: e["date"])
    if not entries:
        raise ValueError("plan has no entries")
    plan_name = (plan.get("name") or "Program").strip() or "Program"

    compiled = []
    i = 0
    part = 1
    while i < len(entries):
        take = min(MAX_WORKOUTS_PER_APP_GUESS, len(entries) - i)
        while True:
            pack = entries[i:i + take]
            # Installer's wrapper name field is 32 bytes; keep the part tag and truncate the
            # plan name to fit rather than letting the wrapper truncate blindly.
            tag = f" {part}" if (len(entries) > take or part > 1) else ""
            name = plan_name[:32 - len(tag)] + tag
            try:
                compiled.append(compile_pack(pack, name))
                break
            except RuntimeError as e:
                if "BINARY_TOO_LARGE" in str(e) and take > 1:
                    take -= 1
                    continue
                raise
        i += take
        part += 1
    return compiled


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plan", help="path to a plan JSON file (see this file's docstring)")
    ap.add_argument("--print-source", action="store_true",
                    help="print each app's generated App Zone source and exit, no network")
    ap.add_argument("--compile", action="store_true",
                    help="compile the plan through the live community compiler")
    ap.add_argument("--out-dir", metavar="DIR",
                    help="save each compiled app as DIR/app_N.json (with --compile)")
    ap.add_argument("--json", action="store_true",
                    help="print one final JSON summary line (for the desktop backend)")
    args = ap.parse_args()

    with open(args.plan) as f:
        plan = json.load(f)

    if args.print_source or not args.compile:
        entries = sorted(plan.get("entries", []), key=lambda e: e["date"])
        i = 0
        while i < len(entries):
            pack = entries[i:i + MAX_WORKOUTS_PER_APP_GUESS]
            source, own_vars = build_app_source(pack)
            print(f"/* --- app covering {', '.join(e['date'] for e in pack)} "
                  f"(own vars: {', '.join(own_vars)}) --- */")
            print(source)
            i += len(pack)
        if not args.compile:
            return 0

    try:
        compiled = compile_plan(plan)
    # Broad on purpose for the --json (backend) path: a malformed workout (e.g. an
    # unclosed repeatStart walking expand_steps off the list) must come back as a clean
    # {"ok": false} the GUI can show, never a traceback the backend can't parse.
    except Exception as e:
        if args.json:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
        raise

    paths = []
    if args.out_dir:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for n, app in enumerate(compiled, 1):
            path = out_dir / f"app_{n}.json"
            with open(path, "w") as f:
                json.dump(app, f)
            paths.append(str(path))

    summary = {"ok": True, "apps": [
        {"name": app["name"], "dates": app["dates"],
         "binaryLength": len(app.get("binary", [])),
         **({"path": paths[n]} if paths else {})}
        for n, app in enumerate(compiled)]}
    if args.json:
        print(json.dumps(summary))
    else:
        for app in summary["apps"]:
            print(f"{app['name']!r}: {len(app['dates'])} workout(s) on "
                  f"{', '.join(app['dates'])}, binary {app['binaryLength']} B"
                  + (f" -> {app.get('path')}" if paths else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
