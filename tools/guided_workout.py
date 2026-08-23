#!/usr/bin/env python3
"""Install a structured interval workout into the Ambit3's native WORKOUT menu — the guided-
workout feature Movescount owned and Suunto retired. One tool, the whole flow, backed up and
reversible.

  ./tools/guided_workout.py my_workout.json --mode Running --write
  ./tools/guided_workout.py --list                      # show installed workouts on the watch
  ./tools/guided_workout.py --restore backup.bin --write

On the watch: sport mode -> hold [Next] 3s -> WORKOUT -> pick it -> [Start Stop]. The workout
runs its phases and beeps at every step transition (audio interval guidance).

WHAT MAKES A WORKOUT APPEAR NATIVELY (the two keys this project reverse-engineered from the
Movescount Android app's libkomposti, both previously thought impossible/AES-locked):
  1. A GUIDANCE DISPLAY on the sport mode: EXERCISE_MODES_DISPLAY whose DISP_SETTING is
     [u16 Template=0x127=295][u16 Type=0x0f=15]. Template 295 is
     BluebirdCustomModeConverter::createCustomModeGuidanceDisplay's TEMPLATE value.
  2. The workout's Apps-region entry header BYTE 0 = 1 (guidance), not 0 (generic) —
     BinaryAreaAppsConverter::typeMapping = {"generic":0, "guidance":1}.
That's it - NO rule in the mode's RULES list. A rule there is an ACTIVE engine slot, so the
workout would run on every recording and beep even unselected; dropping it makes the workout
DORMANT until picked from the menu (hardware-confirmed 2026-08-19). The IAMRULE binary itself is
the SAME format for guidance and generic; only these two markers differ.

FULL NATIVE VISUAL (hardware-proven 2026-08-19): the on-screen TARGET BAND, step TEXT, step
progression and end-of-workout all render natively - the real Movescount interval screen. The key
was feeding the community/Komposti compiler the workout JSON directly (see compile_workout): it
returns the genuine ~2.3 KB guidance binary with the per-step target bands encoded. (The earlier
"visual can't render" belief came from compiling our own hand-written app-zone source instead, which
produced a beeps-only binary with no band.)

WORKOUT JSON (see tools/workout.py for the full schema; durations in seconds):
  {"name": "Test 5-10-5",
   "steps": [{"type": {"typeName": "warmup"},   "duration": {"durationName": "time", "value": 5},
              "target": {"targetName": "none"}},
             {"type": {"typeName": "interval"}, "duration": {"durationName": "time", "value": 10},
              "target": {"targetName": "none"}},
             {"type": {"typeName": "cooldown"}, "duration": {"durationName": "time", "value": 5},
              "target": {"targetName": "none"}}]}
The guidance binary is compiled from this JSON by the community/Komposti compiler (compile_workout),
which needs its key set (env COMPILE_KEY / tools/.compile_key / ~/.config/ambitapp/compile_key).
"""
import argparse
import datetime
import json
import sys
import time
import urllib.request
import urllib.error

import ambit_format as F
import custom_modes as cm
import custom_modes_write as cmw
import workout_install as WI
from ambit_pcap import FlashImage
from write_nav import (CMD_DEVICE_INFO, Link, read_flash, read_memory_map, send_plan)

GUIDANCE_TEMPLATE = 295   # 0x127, PID_RUNNER_GPS_TEMPLATE_GUIDANCE
GUIDANCE_DISP_TYPE = 15   # 0x0f
GUIDANCE_ENTRY_TYPE = 1   # Apps-entry byte0: 1=guidance, 0=generic

# The native guidance screen only prints a step label when the step carries a `text` - a step
# with no text renders blank (hardware-confirmed 2026-08-20: a no-text workout showed no words).
# So fill any blank step with its phase word, giving every step a visible label by default -
# matching how Movescount filled it: the step's own name.
#
# Localized: the labels are a variable keyed by the watch's own language (Units.Language) so a
# watch set to e.g. Portuguese shows native phase words, not hardcoded English. English is the
# fallback for any language not yet in the table; add a language by dropping in its four words.
# Units.Language enum (watch setting) -> short code (android AmbitPersonalSettingsReader.ts).
UNITS_LANGUAGE_CODES = {0: "da", 1: "de", 2: "en", 3: "es", 4: "fr", 5: "it", 6: "nl", 7: "no",
                        8: "pt", 9: "fi", 10: "sv", 11: "zh", 12: "ja", 13: "ko", 14: "cs",
                        15: "pl", 16: "ru"}

PHASE_LABELS_BY_LANG = {
    "en": {"warmup": "Warmup", "interval": "Interval", "recovery": "Recovery",
           "rest": "Rest", "cooldown": "Cooldown"},
    # Add a language's four exact Suunto words to localize it, e.g.
    #   "pt": {"warmup": "Aquecimento", "interval": "Intervalo", "recovery": "Recuperacao",
    #          "rest": "Descanso", "cooldown": "Arrefecimento"},
    # until then any non-English watch falls back to English (safe, just not localized).
}
DEFAULT_LANG = "en"


def phase_labels(lang=None):
    """The phase-word label set for a watch language code, English if it's not in the table."""
    return PHASE_LABELS_BY_LANG.get(lang or DEFAULT_LANG, PHASE_LABELS_BY_LANG[DEFAULT_LANG])


def read_watch_language(link):
    """Read the watch's Units.Language so default step labels match its language. Returns a code
    like 'en'/'pt' (or None if it can't be read - callers fall back to English)."""
    try:
        import settings_write as SW, sbem_schema
        from write_nav import descriptor_for_product_id
        pid = getattr(link, "opened_product_id", None)
        descriptor = descriptor_for_product_id(pid) or sbem_schema.default_descriptor()
        r = SW.read_all(link.command(SW.CMD_SETTINGS_READ, b"\0\0\0\0"), descriptor, pid)
        value = r.get("settings", {}).get("language", {}).get("value")
        return UNITS_LANGUAGE_CODES.get(value)   # int enum -> "en"/"pt"/... (None if unknown)
    except Exception:
        return None


def with_default_labels(workout, lang=None):
    """Return a copy of the workout where every real (non-repeat) step that has no `text` gets
    its phase word (in the watch's language) as the on-watch label, so no step renders blank."""
    import copy
    labels = phase_labels(lang)
    wk = copy.deepcopy(workout)
    for s in wk.get("steps", []):
        t = (s.get("type") or {}).get("typeName")
        if t in ("repeatStart", "repeatEnd"):
            continue
        if not (s.get("text") or "").strip():
            s["text"] = labels.get(t, (t or "Step").capitalize())
    return wk


def compile_workout(workout, lang=None):
    """Workout JSON -> compiled {name, activityId, binary, ruleId} = the GENUINE native guidance
    binary, by POSTing the workout JSON straight to the community/Komposti compiler.

    This is the real Movescount guidance-compilation path (hardware-proven 2026-08-19): the compiler
    parses the workout JSON and returns a ~2.3 KB IAMRULE guidance binary with `INTERVAL_DURATION_
    ELAPSED` and every per-step TARGET BAND encoded, so the firmware draws the native interval step
    screen - target band + step text + step progression + end-of-workout - not just beeps. It is the
    SAME compiler WorkoutSyncer.java uploaded to (<Type>guidance</Type>, <Source>=workout JSON); we
    just feed it JSON instead of our old hand-written app-zone source (which produced a tiny
    beeps-only binary and no band - that was the whole miss). Needs the compiler key (env COMPILE_KEY
    / tools/.compile_key / ~/.config/ambitapp/compile_key, all gitignored)."""
    import workout as W
    if not W.COMPILE_KEY or W.COMPILE_KEY == "***REMOVED***":
        raise SystemExit(
            "no compiler key. The guidance binary is produced by the community/Komposti compiler; "
            "provide its x-functions-key via env COMPILE_KEY, tools/.compile_key, or "
            "~/.config/ambitapp/compile_key (all gitignored - never commit it).")
    activity_id = workout.get("activityId", 3)
    workout = with_default_labels(workout, lang)  # blank step -> phase word in the watch's language
    req = urllib.request.Request(
        W.COMPILE_URL, data=json.dumps(workout).encode("utf-8"), method="POST",
        headers={"Content-Type": "text/plain", "x-functions-key": W.COMPILE_KEY})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            j = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"guidance compile failed: HTTP {e.code}: "
                         f"{e.read().decode('utf-8', 'replace')[:200]}") from None
    except urllib.error.URLError as e:
        raise SystemExit("couldn't reach the compiler (ambitappscompiler.azurewebsites.net) - "
                         f"the guidance binary needs an internet connection. ({e.reason})") from None
    return {"name": workout["name"][:apps_name_len()], "activityId": activity_id,
            "binary": j["binary"], "ruleId": j.get("ruleId")}


def apps_name_len():
    import apps
    return apps.NAME_LEN - 1


def guidance_display():
    """The native guided-workout display (no fields): its presence + the workout's guidance
    Apps flag surface the workout in the [Next]-3s WORKOUT menu, dormant until selected."""
    return {"Template": GUIDANCE_TEMPLATE, "TemplateName": "PID_RUNNER_GPS_TEMPLATE_GUIDANCE",
            "Type": GUIDANCE_DISP_TYPE, "Fields": []}


def find_mode_index(decoded, name):
    for i, m in enumerate(decoded["exercise_modes"]):
        if (m.get("Settings", {}).get("Name") or "").lower() == name.lower():
            return i
    names = [m.get("Settings", {}).get("Name") for m in decoded["exercise_modes"]]
    raise SystemExit(f"no sport mode named {name!r}; this watch has: {names}")


def build_regions(current_custom_modes, current_apps, workout, mode_name, append=False, lang=None):
    """Returns (new_apps_bytes, new_custom_modes_bytes). Adds ONE guidance workout: the compiled
    binary into the Apps region (byte0=1) and a guidance display into the sport mode (no RULE -
    see the note below on why).

    append=False (default): the Apps region is reset to JUST this workout (the original, proven
    single-install recipe). append=True: keep every app already on the watch and add this workout
    to the end - so several guided workouts stack in the same mode's WORKOUT menu (the firmware
    lists them all) and existing apps survive. In append mode a guidance display that's already
    on the mode is reused, not duplicated."""
    compiled = compile_workout(workout, lang)
    existing = WI.apps_entries_with_raw_blocks(current_apps) if append else []
    new_apps = WI.build_apps_region(existing, compiled, entry_type=GUIDANCE_ENTRY_TYPE)

    decoded = cm.decode(current_custom_modes)
    mode_index = find_mode_index(decoded, mode_name)
    mode = decoded["exercise_modes"][mode_index]
    has_guidance_display = any(d.get("Template") == GUIDANCE_TEMPLATE
                               for d in mode.get("Displays", []))
    if has_guidance_display and not append:
        raise SystemExit(f"{mode_name!r} already has a guidance display - restore first "
                         "(this tool installs one workout at a time), or use --append.")
    if has_guidance_display:
        pass  # append: the mode already surfaces the WORKOUT menu, just add the app
    # ONLY the guidance display goes into the mode - NO rule in the RULES list. That was the
    # gating bug (2026-08-19, hardware-confirmed): a rule in RULES is an ACTIVE engine slot, so
    # the workout ran on every recording and beeped even when unselected. Dropping it makes the
    # workout DORMANT until picked from the WORKOUT menu: the Apps-entry guidance flag (byte0=1)
    # + this display are what surface it; the firmware activates it only on selection - clean,
    # native, menu-gated audio guidance.
    if not has_guidance_display:
        mode.setdefault("Displays", []).append(guidance_display())
    new_custom_modes = cmw.build_custom_modes_body(decoded, decoded.get("format_type", 2))
    return new_apps, new_custom_modes, compiled["name"], mode_name


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("workout", nargs="?", help="workout JSON file (see this file's docstring)")
    ap.add_argument("--mode", help="sport mode name to install into (e.g. Running)")
    ap.add_argument("--list", action="store_true", help="list guidance workouts on the watch")
    ap.add_argument("--restore", metavar="FILE", help="restore CustomModes from a backup .bin")
    ap.add_argument("--backup-to", metavar="FILE", help="where to save the pre-write backup")
    ap.add_argument("--append", action="store_true",
                    help="keep existing apps and add this workout to the mode's WORKOUT menu "
                         "(default: reset the Apps region to just this one workout)")
    ap.add_argument("--json", action="store_true", help="print a one-line JSON result (for the GUI)")
    ap.add_argument("--compile-only", action="store_true",
                    help="just compile the workout JSON and report the binary (no watch needed)")
    ap.add_argument("--write", action="store_true", help="actually write (else dry-run)")
    args = ap.parse_args()

    # --compile-only needs no watch at all: compile the JSON on the community compiler and report
    # the resulting guidance binary. Lets a UI show "compiled, N bytes" before an install.
    if args.compile_only:
        if not args.workout:
            ap.error("--compile-only needs a workout JSON file")
        compiled = compile_workout(json.load(open(args.workout)))
        print(json.dumps({"ok": True, "name": compiled["name"],
                          "activityId": compiled.get("activityId"),
                          "binaryBytes": len(compiled["binary"])}))
        return 0

    # Always a live link: we must READ the watch's current regions to build/list/restore. Only
    # the WRITES (send_plan, below) are gated on --write, so a no --write run is a real dry-run.
    link = Link(dry_run=False)
    link.open()
    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    mm = read_memory_map(link)
    cm_base, cm_size = mm["CustomModes"]
    apps_base, apps_size = mm["Apps"]
    current_cm = read_flash(link, cm_base, cm_size, label="CustomModes")
    current_apps = read_flash(link, apps_base, apps_size, label="Apps")

    if args.list:
        dec = cm.decode(current_cm)
        for m in dec["exercise_modes"]:
            gd = [d for d in m.get("Displays", []) if d.get("Template") == GUIDANCE_TEMPLATE]
            if gd:
                print(f"  {m['Settings']['Name']}: guidance workout installed "
                      f"({len(m.get('Rules', []))} rule(s))")
        return 0

    if args.restore:
        blob = open(args.restore, "rb").read()
        print(f"restoring CustomModes from {args.restore} ({len(blob)} bytes)")
        if args.write:
            fi = FlashImage(); fi.write(cm_base, blob)
            send_plan(link, fi, [("CustomModes", cm_base, blob), ("t", cm_base, None)], commit=False)
            print("restored.")
        return 0

    if not args.workout or not args.mode:
        ap.error("a workout JSON and --mode are required (or use --list / --restore)")
    workout = json.load(open(args.workout))

    lang = read_watch_language(link)   # so default step labels match the watch's language
    new_apps, new_cm, wk_name, mode_name = build_regions(
        current_cm, current_apps, workout, args.mode, append=args.append, lang=lang)

    def emit(ok, **extra):
        if args.json:
            print(json.dumps({"ok": ok, "name": wk_name, "mode": mode_name,
                              "binaryBytes": len(new_apps), **extra}))
        return 0

    if not args.json:
        print(f"workout {wk_name!r} -> {mode_name}'s WORKOUT menu "
              f"(audio-guided, dormant until selected)")
        print(f"  Apps: {len(new_apps)} B   CustomModes: {len(new_cm)} B")

    if not args.write:
        if not args.json:
            print("dry-run: pass --write to install (and --backup-to to save a restore point)")
        return emit(False, written=False, dryRun=True)

    import pathlib
    backup = args.backup_to or f"backups/CustomModes_pre_workout_{int(time.time())}.bin"
    pathlib.Path(backup).parent.mkdir(parents=True, exist_ok=True)
    open(backup, "wb").write(current_cm)
    if not args.json:
        print(f"  backed up CustomModes -> {backup}")
    for name, base, blob in [("Apps", apps_base, new_apps), ("CustomModes", cm_base, new_cm)]:
        fi = FlashImage(); fi.write(base, blob)
        send_plan(link, fi, [(name, base, blob), ("t", base, None)], commit=False)
    if not args.json:
        print(f"installed. On the watch: {mode_name} -> [Next] 3s -> WORKOUT -> {wk_name!r}.")
    return emit(True, written=True, backup=backup)


if __name__ == "__main__":
    sys.exit(main())
