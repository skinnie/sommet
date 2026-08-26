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
   (plain bpm is correct for our compiler path; the bpm/60 encoding seen in some captured
   Movescount-era workout JSON is a different era's quirk and is NOT what our compiler wants).

   By default (`--hr-source watch`) each HR band is then RE-RESOLVED into the watch's own zone
   model: the real Suunto HR-zone apps compute zones on heart-rate RESERVE from the watch's
   stored `SUUNTO_USER_MAX_HR` + `SUUNTO_USER_REST_HR` (Karvonen), so we keep each target's
   intensity fraction and re-express it against the watch's max/rest (`karvonen_rescale`) - the
   band lands where the watch itself would draw it. This is the identity when the watch's max
   HR equals intervals.icu's, so it only ever adjusts for a real mismatch. Max/rest come from a
   connected watch (read once), or from `--max-hr`/`--rest-hr` for offline use; if neither is
   available it falls back to intervals.icu's own bpm. `--hr-source intervals` keeps
   intervals.icu's resolved bpm verbatim (no watch needed).

   NOTE the real Couch-to-5K catalogue apps used NO heart rate at all - 37/37 are pure
   SUUNTO_DURATION timers (WARMUP/JOG/WALK/DONE), 35 advancing on the lap button. Carrying
   intervals.icu's HR bands as native guidance target bands is therefore an enhancement over
   the 2015 apps, not a reproduction of them.

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


def convert_target(step: dict, hr_resolve=None) -> dict:
    """intervals.icu step target -> our `target` object.

    Prefers the RESOLVED absolute value (`_hr`/`_power`/`_pace`) over the zone index in `hr`,
    because the resolved one is real bpm/watts and needs no zone table on the far side. Returns
    `{"targetName": "none"}` for a step with no target at all - a legitimate, common case.

    `hr_resolve`, when given, is a `bpm -> bpm` callable applied to BOTH ends of an `hr` band -
    used to re-resolve intervals.icu's zone bpm into the watch's own zone model (see
    `karvonen_rescale`). It only touches HR targets; power/pace bands pass through unchanged.
    """
    for key, target_name in RESOLVED_TARGETS:
        band = step.get(key)
        if not isinstance(band, dict):
            continue
        lo, hi = band.get("start"), band.get("end")
        if lo is None or hi is None:
            continue
        lo, hi = float(lo), float(hi)
        if target_name == "hr" and hr_resolve is not None:
            lo, hi = hr_resolve(lo), hr_resolve(hi)
        lo, hi = round(lo), round(hi)
        if lo > hi:
            lo, hi = hi, lo
        return {"targetName": target_name, "valueRange": {"min": lo, "max": hi}}
    return {"targetName": "none"}


def read_watch_hr(link=None):
    """The watch's stored (MaxHR, RestHR) in bpm, for resolving intervals.icu zones the way the
    watch itself does. `link` is an open write_nav.Link; when None this opens one (mirroring
    guided_workout.py's own settings read). Returns (max_hr, rest_hr); either may be None if the
    watch has no value stored. Raises on no connection - callers decide whether that is fatal."""
    import contextlib
    close = False
    # write_nav.Link logs its device I/O to stdout; keep that off our own stdout (which carries
    # the converted JSON) by routing everything here to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        if link is None:
            from write_nav import CMD_DEVICE_INFO, Link
            link = Link(dry_run=False)
            link.open()
            link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
            close = True
        try:
            import settings_write as SW
            import sbem_schema
            from write_nav import descriptor_for_product_id
            pid = getattr(link, "opened_product_id", None)
            descriptor = descriptor_for_product_id(pid) or sbem_schema.default_descriptor()
            r = SW.read_all(link.command(SW.CMD_SETTINGS_READ, b"\0\0\0\0"), descriptor, pid)
            settings = r.get("settings", {})   # read_all nests the decoded values under "settings"
            max_hr = (settings.get("max_hr") or {}).get("value")
            rest_hr = (settings.get("rest_hr") or {}).get("value")
            return max_hr, rest_hr
        finally:
            if close:
                try:
                    link.close()
                except Exception:
                    pass


def karvonen_rescale(bpm: float, icu_max: float, watch_max: float, watch_rest: float) -> int:
    """Re-express one bpm target from the intervals.icu athlete model into the WATCH's own zone
    model, so the band lands where the watch's Karvonen zone apps (SUUNTO_USER_MAX_HR +
    SUUNTO_USER_REST_HR) would draw it.

    The watch computes zones on heart-rate RESERVE (Karvonen): intensity = (HR - rest)/(max -
    rest). We keep the target's intensity fraction constant and map it onto the watch's reserve:

        frac  = (bpm - rest) / (icu_max - rest)          # intensity in the source model
        out   = rest + frac * (watch_max - rest)         # same intensity on the watch's reserve

    We use the watch's OWN rest HR as the shared resting point - intervals.icu's C25K export
    carries a max HR and LTHR but no resting HR, and it is the same athlete, so the watch's rest
    is the right common floor. When the watch's max HR equals intervals.icu's, this is the
    identity (out == bpm) - nothing is "corrected" that was already in agreement.
    """
    denom = icu_max - watch_rest
    if denom <= 0:               # nonsensical inputs; leave the value untouched rather than divide
        return int(round(bpm))
    frac = (bpm - watch_rest) / denom
    return int(round(watch_rest + frac * (watch_max - watch_rest)))


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


def convert_step(step: dict, hr_resolve=None) -> dict:
    """One leaf (non-repeat) intervals.icu step -> one of our steps."""
    out = {
        "type": {"typeName": guess_type(step)},
        "duration": convert_duration(step),
        "target": convert_target(step, hr_resolve=hr_resolve),
    }
    text = (step.get("text") or "").strip()
    if text:
        # Kept verbatim; workout.py's _phase_label does the compiler's own sanitising (strips
        # digits, truncates to the 6-char label budget). Doing it here too would only lose
        # information that a future non-app-zone consumer of this JSON might want.
        out["text"] = text
    return out


def convert_steps(icu_steps: list, hr_resolve=None) -> list:
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
                out.extend(convert_step(c, hr_resolve) for c in children)
                out.append({"type": {"typeName": "repeatEnd"}})
            else:
                # A 1x block is just its contents; a repeatStart(1) bracket would be noise.
                out.extend(convert_step(c, hr_resolve) for c in children)
        else:
            out.append(convert_step(step, hr_resolve))
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


def convert(icu: dict, name: str, cooldown=None, activity_id: int = 3,
            watch_max_hr=None, watch_rest_hr=None) -> dict:
    """A parsed intervals.icu export -> a workout dict in this project's schema.

    `cooldown` is opt-in reconstruction of the trailing recovery the export drops (see the module
    docstring): seconds, or "auto" for sportSettings.cooldown_time. It reuses the HR target of the
    workout's last easy step so the added step is consistent with the rest of the plan rather than
    untargeted.

    `watch_max_hr`/`watch_rest_hr`: when both are given, every HR band is re-resolved from
    intervals.icu's athlete model into the WATCH's own Karvonen zone model (see `karvonen_rescale`)
    so the target lands where the watch's zone display would put it. Needs intervals.icu's own max
    HR (`sportSettings.max_hr`) as the source reference; without it the bands can't be rescaled and
    a ValueError is raised. When omitted, intervals.icu's resolved bpm is used as-is.
    """
    icu_steps = icu.get("steps")
    if not icu_steps:
        raise ValueError("no steps in this file - is it really an intervals.icu workout export?")

    hr_resolve = None
    if watch_max_hr is not None and watch_rest_hr is not None:
        icu_max = (icu.get("sportSettings") or {}).get("max_hr")
        if not icu_max:
            raise ValueError(
                "cannot resolve HR against the watch's zones: this file has no "
                "sportSettings.max_hr to use as the source reference")
        hr_resolve = lambda bpm: karvonen_rescale(
            bpm, float(icu_max), float(watch_max_hr), float(watch_rest_hr))

    steps = convert_steps(icu_steps, hr_resolve)
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


def open_watch_link():
    """Open a live write_nav.Link to the watch (device-info handshake done). Device I/O is logged
    to stderr, not stdout, so this tool's own stdout stays clean. Caller must close it."""
    import contextlib
    with contextlib.redirect_stdout(sys.stderr):
        from write_nav import CMD_DEVICE_INFO, Link
        link = Link(dry_run=False)
        link.open()
        link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    return link


def install_to_watch(workout: dict, mode: str, link, write: bool = False,
                     append: bool = False, backup_to=None) -> dict:
    """Install one converted workout into a sport mode's WORKOUT menu, reusing guided_workout's
    proven build+write path (compile -> Apps byte0=1 guidance entry + a guidance display on the
    mode, NO rule, so it's dormant until picked). `link` is an open Link (see open_watch_link).
    Without `write` this is a real dry-run (reads + builds, writes nothing). All device chatter
    goes to stderr; returns a small status dict. Mirrors guided_workout.py's own main() install
    block - the single source of truth for the byte layout stays there."""
    import contextlib
    import time
    with contextlib.redirect_stdout(sys.stderr):
        import guided_workout as GW
        from write_nav import read_flash, read_memory_map, send_plan
        from ambit_pcap import FlashImage

        mm = read_memory_map(link)
        cm_base, cm_size = mm["CustomModes"]
        apps_base, apps_size = mm["Apps"]
        current_cm = read_flash(link, cm_base, cm_size, label="CustomModes")
        current_apps = read_flash(link, apps_base, apps_size, label="Apps")

        lang = GW.read_watch_language(link)   # default step labels in the watch's language
        new_apps, new_cm, wk_name, mode_name = GW.build_regions(
            current_cm, current_apps, workout, mode, append=append, lang=lang)

        result = {"name": wk_name, "mode": mode_name,
                  "appsBytes": len(new_apps), "written": False}
        if not write:
            result["dryRun"] = True
            return result

        backup = backup_to or f"backups/CustomModes_pre_workout_{int(time.time())}.bin"
        pathlib.Path(backup).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(backup).write_bytes(current_cm)
        for name, base, blob in [("Apps", apps_base, new_apps),
                                 ("CustomModes", cm_base, new_cm)]:
            fi = FlashImage(); fi.write(base, blob)
            send_plan(link, fi, [(name, base, blob), ("t", base, None)], commit=False)
        result.update(written=True, backup=backup)
        return result


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
    ap.add_argument("--hr-source", choices=("intervals", "watch"), default="watch",
                    help="how to set HR bands: 'watch' (default) re-resolves intervals.icu's "
                         "zones against the watch's stored max/rest HR the way the watch's own "
                         "zone display does; 'intervals' keeps intervals.icu's resolved bpm as-is")
    ap.add_argument("--max-hr", type=int, help="watch max HR (bpm) for --hr-source watch, "
                                               "instead of reading it from a connected watch")
    ap.add_argument("--rest-hr", type=int, help="watch rest HR (bpm) for --hr-source watch, "
                                                "instead of reading it from a connected watch")
    ap.add_argument("-o", "--out", type=pathlib.Path, help="write the converted JSON here")
    ap.add_argument("--out-dir", type=pathlib.Path,
                    help="write one converted JSON per input file into this directory")
    ap.add_argument("--compile", action="store_true",
                    help="also compile via the community compiler and report the binary size")
    ap.add_argument("--mode", metavar="SPORT_MODE",
                    help="install the converted workout into this sport mode's WORKOUT menu "
                         "(needs a connected watch; a single input file)")
    ap.add_argument("--append", action="store_true",
                    help="with --mode: keep the mode's existing workouts and add this one "
                         "(default: replace the Apps region with just this workout)")
    ap.add_argument("--write", action="store_true",
                    help="with --mode: actually write to the watch (else a real dry-run)")
    ap.add_argument("--backup-to", metavar="FILE",
                    help="with --mode --write: where to save the pre-write CustomModes backup")
    args = ap.parse_args()

    if args.out and len(args.files) > 1:
        ap.error("--out takes a single input file; use --out-dir for several")
    if args.name and len(args.files) > 1:
        ap.error("--name takes a single input file")
    if args.mode and len(args.files) > 1:
        ap.error("--mode installs one workout; give a single input file "
                 "(use --append and rerun for several, or training_calendar.py for a plan)")
    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve the watch's HR reference ONCE (a connection/read is expensive; the same athlete
    # applies to every file in a batch). Explicit --max-hr/--rest-hr win; otherwise, for
    # --hr-source watch, read them from a connected watch. --hr-source intervals skips this.
    watch_max_hr = args.max_hr
    watch_rest_hr = args.rest_hr
    # One connection for the whole run: if we're installing, open the link now and reuse it for
    # both the HR read and the write, so the watch is touched once.
    link = open_watch_link() if args.mode else None
    if args.hr_source == "watch" and (watch_max_hr is None or watch_rest_hr is None):
        try:
            read_max, read_rest = read_watch_hr(link)
            watch_max_hr = watch_max_hr if watch_max_hr is not None else read_max
            watch_rest_hr = watch_rest_hr if watch_rest_hr is not None else read_rest
        except Exception as e:
            print(f"note: could not read the watch's HR ({e}); keeping intervals.icu's own bpm. "
                  f"Pass --max-hr/--rest-hr to resolve offline, or --hr-source intervals to "
                  f"silence this.", file=sys.stderr)
    if args.hr_source == "watch" and watch_max_hr and watch_rest_hr:
        print(f"resolving HR bands against watch zones (max {watch_max_hr}, rest {watch_rest_hr} bpm)",
              file=sys.stderr)
    elif args.hr_source == "watch":
        # Reached the watch but it has no max/rest HR stored (a fresh/Emu watch), or only one -
        # Karvonen needs both. Say so, and keep intervals.icu's own bpm rather than guessing.
        missing = "max HR" if not watch_max_hr else "rest HR" if not watch_rest_hr else "max/rest HR"
        print(f"note: the watch has no {missing} stored, so HR bands keep intervals.icu's own bpm. "
              f"Sync personal stats first (tools/intervals_stats.py -> settings_write.py), or pass "
              f"--max-hr/--rest-hr, to resolve against the watch's zones.", file=sys.stderr)
        watch_max_hr = watch_rest_hr = None
    else:
        watch_max_hr = watch_rest_hr = None   # --hr-source intervals: keep intervals.icu's bpm

    failures = 0
    mismatch_warned = False
    for path in args.files:
        try:
            icu = json.loads(path.read_text())
            workout = convert(icu, args.name or default_name(path),
                              cooldown=args.cooldown, activity_id=args.activity_id,
                              watch_max_hr=watch_max_hr, watch_rest_hr=watch_rest_hr)
            # If we're resolving against the watch AND intervals.icu's own max HR disagrees with
            # the watch's, the bands were SHIFTED (a lower watch max lowers every target). Same
            # athlete on both ends should mean the same max HR, so a mismatch usually just means
            # the watch's Personal.MaxHR is stale - point at the one-command sync that fixes it,
            # once per run rather than per file.
            if watch_max_hr is not None and not mismatch_warned:
                icu_max = (icu.get("sportSettings") or {}).get("max_hr")
                if icu_max and int(icu_max) != int(watch_max_hr):
                    print(f"note: intervals.icu max HR is {int(icu_max)} but the watch's is "
                          f"{int(watch_max_hr)}, so HR bands were rescaled to the watch. To keep "
                          f"them faithful, sync your max/rest HR onto the watch first "
                          f"(tools/intervals_stats.py -> settings_write.py) so the two agree, or "
                          f"pass --hr-source intervals.", file=sys.stderr)
                    mismatch_warned = True
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

        if args.mode:
            try:
                res = install_to_watch(workout, args.mode, link, write=args.write,
                                       append=args.append, backup_to=args.backup_to)
            except SystemExit as e:      # build_regions/compile raise SystemExit on real errors
                print(f"{summary} -> install FAILED: {e}", file=sys.stderr)
                failures += 1
                continue
            if res.get("written"):
                summary += (f" -> installed into {res['mode']}'s WORKOUT menu as {res['name']!r} "
                            f"(backup {res['backup']})")
            else:
                summary += (f" -> dry-run: would install {res['name']!r} into {res['mode']} "
                            f"({res['appsBytes']} B Apps); pass --write to install")
            print(summary)
            if res.get("written"):
                print(f"  on the watch: {res['mode']} -> [Next] 3s -> WORKOUT -> {res['name']!r}",
                      file=sys.stderr)
            continue

        print(summary)
        if not out_path and not args.compile:
            print(blob)

    if link is not None:
        try:
            link.close()
        except Exception:
            pass
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
