#!/usr/bin/env python3
"""Calendar of dated native guided workouts, named "Www dd/mm name" in the WORKOUT menu — the
locked design (André, 2026-08-21) for the "Today"/planned-training experience, taken instead
of the native `TrainingProgram` flash region (CORRECTED 2026-10-20: it is NOT unreachable — it
surfaces a "Today" card when Sports.Plans.Source != 0, hardware-confirmed; it just can't hold
complex steps, so complex workouts take this menu route. See
docs/reference/training-workouts-stock-vs-patch.md) (see
`assets/Firmware/re-out/training_program_CONCLUSION.md`).

Flow: pick a date -> pick an activity -> build a workout (tools/workout.py schema) -> this
tool compiles it through the real native guidance path (guided_workout.py's compile_workout,
the JSON->Komposti-binary breakthrough, hardware-confirmed 2026-08-19/20) and installs it into
the browsable WORKOUT menu, named "Www dd/mm <workout name>" (e.g. "Tue 20/10 ...") so the
rider can tell entries apart and pick the right one by hand. Without --auto-start there is NO on-watch date logic involved —
the date is just a label a human reads, sidestepping the firmware wall entirely rather than
working around it with an App-Zone countdown app (training_plan.py's older design, superseded
for this feature by André's explicit call).

With --auto-start (2026-10-10) the date logic is back on the watch, but inside the NATIVE
stepped workout: each compiled workout gets `if (SUUNTO_DAYS_AFTER_1_1_2000 == its day)` in
front of its code (iamrule_code.add_date_gate, a byte-exact copy of what the compiler emits
for that line) and every installed workout is an active rule of the sport mode the plan names
(plain "Running") at once. The watch's own clock runs the day's one; the others do nothing. No
computer needed on the day - the sync only keeps the five slots filled as weeks pass.

Rotation, per André ("on the sync we erase the ones past #today and put another ones"): each
--sync erases every managed (weekday+date prefixed) entry whose date has passed and installs
whatever's next from the plan that isn't already installed. Managed entries are recognized by
MANAGED_RE (the "Www dd/mm " prefix this tool writes, and the older "dd/mm_" so a re-sync
renames rather than duplicates them); anything else already on the watch (a manually installed
guided workout, a generic Suunto App) is left alone.

Known v1 simplification: the watch only stores "dd/mm" in the app name, no year. Expiry is
therefore judged by comparing (month, day) against today's (month, day) — correct for the
intended use (planning days/weeks/months ahead within one calendar year), but will misjudge an
entry planned to survive a Dec->Jan year boundary. Not worth solving until it's a real need.

Plan JSON (dates are ISO YYYY-MM-DD; `workout` is workout.py's own schema unchanged):

    {
      "name": "Marathon prep",
      "entries": [
        {"date": "2026-08-25", "mode": "Running", "workout": {"name": "Long run", "steps": [...]}},
        {"date": "2026-08-28", "mode": "Running", "workout": {"name": "5x3min intervals", ...}}
      ]
    }

    ./tools/training_calendar.py PLAN.json --sync                 # dry-run, shows the diff
    ./tools/training_calendar.py PLAN.json --sync --write         # actually erase + install
    ./tools/training_calendar.py PLAN.json --sync --today 2026-08-27 --write   # override "today"
    ./tools/training_calendar.py --list                           # show what's on the watch now
    ./tools/training_calendar.py --remove "Light workout" --write # drop one manual native workout
"""
import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import struct
import sys
import time

import apps
import custom_modes as cm
import custom_modes_write as cmw
import guided_workout as GW
import iamrule_code as IC
import training_plan as TP
import workout_install as WI
from training_program import watch_holds
from ambit_pcap import FlashImage
from write_nav import CMD_DEVICE_INFO, Link, read_flash, read_memory_map, send_plan

# Managed (calendar-installed) entries read "Www dd/mm name" (e.g. "Tue 20/10 Beep5s"); older
# syncs wrote "dd/mm_name", still matched here so a re-sync RENAMES them (plan_diff drops the
# old-named entry as out-of-window and adds the weekday one) rather than leaving a duplicate.
MANAGED_RE = re.compile(r"^(?:[A-Z][a-z]{2} )?(\d{2})/(\d{2})[ _]")
WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # datetime.date.weekday(): Mon=0
DATE_GATE_MARK = b"SUUNTO_DAYS_AFTER_1_1_2000\0"


def is_gated(binary):
    """True if a compiled workout reads the watch's date (iamrule_code.add_date_gate)."""
    return DATE_GATE_MARK in bytes(binary)


def gate_day(binary):
    """The day value a gated, magic-less compiled workout runs on (None if not gated)."""
    b = bytes(binary)
    if b[:len(apps.MAGIC)] != apps.MAGIC:
        b = apps.MAGIC + b
    try:
        return IC.date_gate_days(b)
    except ValueError:
        return None


def needs_regate(binary):
    """True if a compiled workout was date-gated before 2026-10-15 (its native references
    point one slot too low, iamrule_code.needs_regate) and must be reinstalled."""
    b = bytes(binary)
    if b[:len(apps.MAGIC)] != apps.MAGIC:
        b = apps.MAGIC + b
    try:
        return IC.needs_regate(b)
    except ValueError:
        return False


def needs_hr_fix(binary):
    """True if a compiled workout still stops on a missing heart rate (installed before
    iamrule_code.tolerate_missing_hr, 2026-10-14)."""
    b = bytes(binary)
    if b[:len(apps.MAGIC)] != apps.MAGIC:
        b = apps.MAGIC + b
    try:
        return IC.tolerate_missing_hr(b)[1]
    except ValueError:
        return False


def gated_binary(binary, date_iso):
    """The magic-less compiled workout, run only on `date_iso` by the watch's own clock
    (iamrule_code.add_date_gate; the day value is training_plan.date_to_days2000's, hardware-
    verified on the Sport 2026-09-02)."""
    b = bytes(binary)
    if b[:len(apps.MAGIC)] == apps.MAGIC:
        b = b[len(apps.MAGIC):]
    return IC.add_date_gate(apps.MAGIC + b, TP.date_to_days2000(date_iso))[len(apps.MAGIC):]


def native_block(raw_block, binary):
    """An Apps entry block (header + name + magic + binary) for the same name with a new
    binary: any-mode sport byte, fresh marker."""
    return (bytes([GW.GUIDANCE_ENTRY_TYPE, GW.ANY_MODE_ACTIVITY, apps.entry_checksum(binary)])
            + raw_block[3:3 + apps.NAME_LEN] + apps.MAGIC + binary)


def gate_managed(entries, dates_by_label):
    """(entries, gated_names): every managed native entry that does not yet read the date gets
    its block rebuilt with the gate for its plan date. Entries whose label is not in
    `dates_by_label` are left alone."""
    out, names = [], []
    for e in entries:
        e = dict(e)
        if (is_managed(e["name"]) and e.get("reserved") == GW.GUIDANCE_ENTRY_TYPE
                and e["name"] in dates_by_label and not is_gated(e["binary"])):
            binary = gated_binary(e["binary"], dates_by_label[e["name"]])
            e["_raw_block"] = native_block(e["_raw_block"], binary)
            e["binary"] = binary
            names.append(e["name"])
        out.append(e)
    return out, names


def entry_label(date_iso, workout_name):
    """"Www dd/mm name" (e.g. "Tue 20/10 Beep5s"), truncated to the 29-byte on-watch name field
    (apps.NAME_LEN). Weekday in fixed English abbreviations (not locale-dependent) so the label
    is byte-identical to the Android port and to what the sync compares against. André
    2026-10-20: weekday + date, easier to read in the WORKOUT menu than the old "dd/mm_"."""
    d = datetime.date.fromisoformat(date_iso)
    prefix = f"{WEEKDAY_ABBR[d.weekday()]} {d.day:02d}/{d.month:02d} "
    budget = (apps.NAME_LEN - 1) - len(prefix)
    return prefix + workout_name[:max(budget, 0)]


def is_managed(name):
    return bool(MANAGED_RE.match(name or ""))


def is_expired(name, today):
    """True if a managed "...dd/mm..." name's (month, day) is before today's — see the module
    docstring's "known v1 simplification" on why this ignores year."""
    m = MANAGED_RE.match(name or "")
    if not m:
        return False
    day, month = int(m.group(1)), int(m.group(2))
    return (month, day) < (today.month, today.day)


def rebuild_apps_region(raw_blocks):
    """Same directory-format assembly as workout_install.build_apps_region's tail, for the
    pure-removal case (no new entry to hand it) — see that function's docstring for the format."""
    num_entries = len(raw_blocks)
    table_len = 4 + 4 * (num_entries + 1)
    offsets = []
    cursor = table_len
    for b in raw_blocks:
        offsets.append(cursor)
        cursor += len(b)
    total_length = cursor
    header = struct.pack("<HH", num_entries, num_entries ^ 0x02) + struct.pack(
        f"<{num_entries + 1}I", *offsets, total_length)
    return header + b"".join(raw_blocks)


# What the sync installed under each label, per watch: a workout edited but left on the same
# date with the same name keeps its label, so plan_diff keeps the old one on the watch (André,
# 2026-10-24: a 33-minute test changed to 10 s steps stayed 33 minutes). The binary is compiled
# at sync, so there is nothing on the watch to compare with; the sync remembers a fingerprint of
# what it installed and reinstalls a kept label whose plan workout no longer matches it.
INSTALLED_DIR = pathlib.Path.home() / "AmbitAppBackups" / "calendar"
# Pre-write copies of the regions a sync or remove rewrites: the folder the sport-mode editors
# use. Never next to this file: in the packaged app that is a temporary or read-only folder.
BACKUP_DIR = pathlib.Path.home() / "AmbitAppBackups" / "custom_modes"


def backup_path(kind, serial=None):
    """BACKUP_DIR/<kind>_<serial>_<time>.bin, the serial telling two watches' copies apart
    (AMBIT_SERIAL, which the backend sets, when the watch's own answer is missing)."""
    serial = serial or os.environ.get("AMBIT_SERIAL") or ""
    tag = "_" + re.sub(r"[^A-Za-z0-9]", "_", serial) if serial else ""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return BACKUP_DIR / f"{kind}{tag}_{time.strftime('%Y%m%d-%H%M%S')}.bin"


def workout_fingerprint(workout, lang):
    """Everything the installed program is built from here: the workout and the watch language
    (the default step words). Rule id, sport byte and date gate are handled by the sync itself."""
    blob = json.dumps({"workout": workout, "lang": lang}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def installed_path(serial):
    return INSTALLED_DIR / f"installed-{re.sub(r'[^A-Za-z0-9]', '_', serial)}.json"


def load_installed(serial):
    """{label: fingerprint} for this watch, {} when unknown or unreadable."""
    if not serial:
        return {}
    try:
        data = json.loads(installed_path(serial).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_installed(serial, fingerprints):
    if not serial:
        return
    path = installed_path(serial)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fingerprints, indent=1, sort_keys=True), encoding="utf-8")


def edited_labels(existing, kept_block_set, by_label, installed, lang):
    """Labels of kept managed entries whose plan workout differs from what was installed.
    A label with no remembered fingerprint (installed before this, or by another computer) is
    taken as matching - the first sync records it."""
    out = []
    for e in existing:
        name = e["name"]
        if not (is_managed(name) and e["_raw_block"] in kept_block_set and name in by_label):
            continue
        known = installed.get(name)
        if known is not None and known != workout_fingerprint(by_label[name]["workout"], lang):
            out.append(name)
    return out


def watch_serial(link):
    try:
        import sbem_schema
        return sbem_schema.watch_identity(link)[0]
    except Exception:
        return None


def plan_diff(current_apps_bytes, plan_entries, today, menu_max=GW.WORKOUT_MENU_MAX):
    """Returns (kept_raw_blocks, to_add, waiting).

    The WORKOUT menu shows only the first `menu_max` native workouts, so the calendar syncs to
    the space available (André, 2026-09-26): the manually installed native workouts keep their
    slots, and the soonest upcoming plan entries fill the rest - earliest first. `waiting` is the
    labels of the later ones; as past ones are erased, each sync moves the next ones in.

    A managed entry is kept only if it's in that window - so an expired one, one removed from
    the calendar (TrainingProgramPage.qml's "Remove workout"), and one pushed out of the window
    are all erased. Unmanaged entries (anything not "dd/mm_"-prefixed — a manually installed
    guided workout, a generic Suunto App) are never touched, matching the module docstring's
    standing rule."""
    existing = WI.apps_entries_with_raw_blocks(current_apps_bytes)
    manual = [e for e in GW.native_names(existing) if not is_managed(e)]
    slots = max(menu_max - len(manual), 0)
    upcoming, seen = [], set()
    for e in sorted(plan_entries, key=lambda e: e["date"]):
        label = entry_label(e["date"], e["workout"]["name"])
        # Already past — never install it, whether or not it was ever installed.
        if datetime.date.fromisoformat(e["date"]) >= today and label not in seen:
            seen.add(label)
            upcoming.append((label, e))
    window = {label for label, _ in upcoming[:slots]}
    waiting = [label for label, _ in upcoming[slots:]]

    kept = [e["_raw_block"] for e in existing if not is_managed(e["name"]) or e["name"] in window]
    names_present = {e["name"] for e in existing}
    to_add = [e for label, e in upcoming[:slots] if label not in names_present]
    return kept, to_add, waiting


def auto_start_rules(decoded, existing, new_apps_bytes, plan_entries, today, auto_start):
    """Edit `decoded` (custom_modes.decode output) in place so that the sport mode each plan
    entry names (plain "Running") runs every installed upcoming workout as an active rule - all
    of them at once, each gated on its own date (iamrule_code.add_date_gate), so the watch's own
    clock picks the day's one and the others do nothing. A RULE in a mode's RULES list is an
    ACTIVE engine slot (hardware 2026-08-19).

    Why the watch's clock and not the sync (2026-10-09/10): a rule pointed at the next workout
    only at sync time ran the 5th's workout on the 7th; André: "try to have a workaround to
    work properly without connecting". Why the plain mode and not a "Running workout" of its
    own (André, 2026-10-17: "training day => card => go Running => opens the day workout. Not
    training day, Running, free run"; no extra sport mode): the gate already makes every other
    day a free run. Hardware 2026-10-18, André's Peak: card -> Start -> Running -> Brisk Walk,
    Jog, Walk pop-ups with the light.

    First every rule in ANY mode that points at a managed ("dd/mm_") entry is dropped (that is
    how the feature is switched off, auto_start=False, and how a "Running workout" left by an
    older sync is emptied); then each named mode gets one rule per workout, soonest first, as
    far as the five slots go.
    Returns (touched_mode_names, [{"sport", "mode", "workouts"}])."""
    old_names = {i: e["name"] for i, e in enumerate(existing)}
    # Rules are compared at the end, so a sync that changes nothing rewrites no sport mode
    # (the plug-in sync runs on every connection - 2026-10-10).
    before_rules = {m["Settings"]["Name"]: list(m.get("Rules") or []) for m in decoded["exercise_modes"]}
    # A kept rule follows its app by name: the rebuilt region drops expired entries, so the
    # others move (André's Peak 2026-10-15: "Workout of the day"'s rule ended on 24/10_W8).
    installed = {e["name"]: i for i, e in enumerate(apps.decode(new_apps_bytes))}
    for mode in decoded["exercise_modes"]:
        kept = []
        for r in mode.get("Rules") or []:
            name = old_names.get(r["RuleIdx"], "")
            if is_managed(name) or name not in installed:
                continue
            kept.append(dict(r, RuleIdx=installed[name]))
        mode["Rules"] = kept

    def touched_modes():
        return [m["Settings"]["Name"] for m in decoded["exercise_modes"]
                if (m.get("Rules") or []) != before_rules[m["Settings"]["Name"]]]
    if not auto_start:
        return touched_modes(), []
    by_mode = {}   # exercise-mode index -> [label, ...] of the installed upcoming workouts, soonest first
    for e in sorted((e for e in plan_entries if e["date"] >= str(today)), key=lambda e: e["date"]):
        try:
            idx = GW.find_mode_index(decoded, e["mode"])
        except SystemExit:
            continue
        label = entry_label(e["date"], e["workout"]["name"])
        if label in installed and label not in by_mode.setdefault(idx, []):
            by_mode[idx].append(label)
    out = []
    for idx, labels in by_mode.items():
        mode = decoded["exercise_modes"][idx]
        room = WI.SPORT_MODE_APP_LIMIT - len(mode.get("Rules") or [])
        chosen = labels[:max(room, 0)]
        mode.setdefault("Rules", []).extend(
            {"RuleIdx": installed[l], "UseRule": True, "LogRule": False} for l in chosen)
        out.append({"sport": mode["Settings"].get("ActivityID"), "mode": mode["Settings"]["Name"],
                    "workouts": chosen})
    return touched_modes(), out


# Callers kill a calendar sync at 300 s (server.py, training_calendar_gui.py) and each compile
# may take up to 45 s on a cold compiler. Past this budget the remaining entries wait for the
# next sync, so the flash write never runs into that kill.
COMPILE_BUDGET_S = 180


def sync(link, plan, today, write, json_out, auto_start=None):
    started = time.monotonic()
    mm = read_memory_map(link)
    cm_base, cm_size = mm["CustomModes"]
    apps_base, apps_size = mm["Apps"]
    current_cm = read_flash(link, cm_base, cm_size, label="CustomModes")
    current_apps = read_flash(link, apps_base, apps_size, label="Apps")

    kept_blocks, to_add, waiting = plan_diff(current_apps, plan["entries"], today)
    existing = WI.apps_entries_with_raw_blocks(current_apps)
    kept_block_set = set(kept_blocks)
    # Everything managed that didn't survive the diff — expired or removed from the plan. One
    # moved back to waiting for a free slot is erased too, but reported under "waiting".
    removed = [e["name"] for e in existing
               if is_managed(e["name"]) and e["_raw_block"] not in kept_block_set
               and e["name"] not in waiting]

    decoded = cm.decode(current_cm)
    lang = GW.read_watch_language(link)
    by_label = {entry_label(e["date"], e["workout"]["name"]): e for e in plan["entries"]}
    serial = watch_serial(link)
    installed = load_installed(serial)
    edited = edited_labels(existing, kept_block_set, by_label, installed, lang)
    edited_blocks = {}   # label -> its block on the watch, kept if the new version fails to compile
    # Their rule ids stay taken until the new version is in: a failed compile puts the old block
    # back with its id, which a workout added meanwhile must not have been given.
    reserved_ids = {}
    for e in existing:
        if e["name"] in edited and e["_raw_block"] in kept_block_set:
            kept_block_set.discard(e["_raw_block"])
            edited_blocks[e["name"]] = e["_raw_block"]
            reserved_ids[e["name"]] = e.get("ruleId")
            to_add.append(by_label[e["name"]])
    for e in existing:
        if not (is_managed(e["name"]) and e["_raw_block"] in kept_block_set
                and e["name"] in by_label):
            continue
        # A date-gated workout does nothing on any other day, which is wrong for a WORKOUT-
        # menu pick; the gate cannot be taken out of a compiled binary, so without auto-start
        # such a kept workout is reinstalled plain. With auto-start one gated on another day
        # than its date (the day-value off-by-one until 2026-10-14) is reinstalled too. And any
        # kept workout that still stops on a missing heart rate is, and any gated before
        # 2026-10-15 (references one slot too low: no step data, no step pop-up).
        if needs_hr_fix(e["binary"]) or needs_regate(e["binary"]) or (is_gated(e["binary"]) and (
                not auto_start
                or gate_day(e["binary"]) != TP.date_to_days2000(by_label[e["name"]]["date"]))):
            kept_block_set.discard(e["_raw_block"])
            to_add.append(by_label[e["name"]])
    # Kept workouts from before 2026-10-05 carry a per-sport byte (a pick could run another
    # sport's workout) and rule id 0. Repair them while we are rewriting the region anyway.
    current_list, restamped = GW.restamp_rule_ids(
        [e for e in existing if e["_raw_block"] in kept_block_set])
    gated = []
    if auto_start:
        # Every managed workout reads the watch's date (iamrule_code.add_date_gate), so all
        # of them can be active rules at once and the day's one runs by itself.
        current_list, gated = gate_managed(
            current_list, {n: e["date"] for n, e in by_label.items()})
    kept_blocks = [e["_raw_block"] for e in current_list]
    added_names = []
    failed = []  # [(label, error)] — one bad workout in the plan must not sink the whole sync
    new_apps_bytes = None
    for e in to_add:
        label = entry_label(e["date"], e["workout"]["name"])
        try:
            GW.mode_activity_id(decoded, e["mode"])  # fail fast on an unknown mode name
            if time.monotonic() - started > COMPILE_BUDGET_S:
                raise SystemExit("the workout compiler is slow right now - this one goes on "
                                 "the watch at the next sync")
            compiled = GW.compile_workout(e["workout"], lang)
        except SystemExit as err:
            failed.append((label, str(err)))
            if label in edited_blocks:   # an edit that won't compile keeps the old version
                new_apps_bytes = rebuild_apps_region(
                    [x["_raw_block"] for x in current_list] + [edited_blocks.pop(label)])
                current_list = WI.apps_entries_with_raw_blocks(new_apps_bytes)
                reserved_ids.pop(label, None)       # now in current_list itself
            continue
        compiled["name"] = label
        compiled["activityId"] = GW.ANY_MODE_ACTIVITY  # row == slot in every mode, see guided_workout
        # A unique rule id per installed workout, or the watch runs the first one with the same
        # id whatever was picked (GW.with_rule_id; André's swim-instead-of-run, 2026-10-05).
        reserved_ids.pop(label, None)               # its own old id may be reused
        rule_id = GW.free_rule_id(current_list + [{"ruleId": r} for r in reserved_ids.values()])
        if rule_id is None:
            failed.append((label, "no free rule id (1..9) left among the installed workouts"))
            continue
        compiled["binary"] = GW.with_rule_id(compiled["binary"], rule_id)
        if auto_start:
            compiled["binary"] = gated_binary(compiled["binary"], e["date"])
            gated.append(label)
        new_apps_bytes = WI.build_apps_region(current_list, compiled, entry_type=GW.GUIDANCE_ENTRY_TYPE)
        current_list = WI.apps_entries_with_raw_blocks(new_apps_bytes)
        added_names.append(compiled["name"])
    if new_apps_bytes is None:
        new_apps_bytes = rebuild_apps_region(kept_blocks)  # also carries any re-stamped ids

    # Ensure every mode named by a kept-or-added entry has the guidance display so its
    # WORKOUT menu is surfaced at all (harmless no-op if it's already there).
    modes_needed = {e["mode"] for e in plan["entries"] if e["date"] >= str(today)} | \
                   {e.get("mode") for e in to_add}
    modes_touched = []
    for mode_name in sorted(m for m in modes_needed if m):
        idx = GW.find_mode_index(decoded, mode_name)
        mode = decoded["exercise_modes"][idx]
        if not any(d.get("Template") == GW.GUIDANCE_TEMPLATE for d in mode.get("Displays", [])):
            mode.setdefault("Displays", []).append(GW.guidance_display())
            modes_touched.append(mode_name)
    rules_touched, auto = auto_start_rules(
        decoded, existing, new_apps_bytes, plan["entries"], today, auto_start)
    modes_touched = sorted(set(modes_touched) | set(rules_touched))
    # Sport modes are written only when a display or rule actually changed, and then only as the
    # used extent. Writing back the raw 12 KB region read above (the old "unchanged" path) leaves a
    # closing hash the firmware rejects on the next restart - "Connect to Moveslink" and
    # factory-default sport modes (André's Peak, 2026-09-26). write_nav.send_plan now refuses it.
    new_cm_bytes = cmw.build_custom_modes_body(decoded, decoded.get("format_type", 2)) \
        if modes_touched else None
    if new_cm_bytes is not None and cm.check_field_type_shortcut_invariant(new_cm_bytes):
        # Finding 53's "connect to Moveslink" root cause - refuse rather than write it.
        result = {"ok": False, "written": False,
                  "error": "the rebuilt sport modes break the field Type/Shortcut invariant - "
                           "nothing written"}
        print(json.dumps(result) if json_out else f"  !! {result['error']}")
        return result

    # The plug-in sync runs on every connection: an Apps region the watch already holds (same
    # bytes, and the hash it reports is theirs) is not written again (desktop audit 2026-10-29).
    apps_unchanged = watch_holds(link, "Apps", apps_base, new_apps_bytes, current=current_apps)
    result = {"ok": True, "today": str(today), "removed": removed, "added": added_names,
              "failed": [{"name": n, "error": err} for n, err in failed],
              "waiting": waiting, "menuMax": GW.WORKOUT_MENU_MAX,
              "displaysAdded": modes_touched, "appsBytes": len(new_apps_bytes),
              "repaired": [n for n, _ in restamped], "autoStart": auto,
              "dateGated": gated, "edited": [n for n in edited if n in added_names],
              "unchanged": apps_unchanged and new_cm_bytes is None}

    if not json_out:
        print(f"sync as of {today}:")
        print(f"  erase: {removed or '(none)'}")
        print(f"  install: {added_names or '(none)'}")
        if waiting:
            print(f"  waiting for a free slot (menu shows {GW.WORKOUT_MENU_MAX}): {len(waiting)}")
        if failed:
            print(f"  FAILED to compile (skipped, rest of sync still applied):")
            for n, err in failed:
                print(f"    {n}: {err}")
        if modes_touched:
            print(f"  sport modes rewritten (guidance display / auto-start rule): {modes_touched}")
        for x in auto:
            print(f"  auto-start: {x['mode']!r} runs the day's workout by date: {x['workouts']}")
        if gated:
            print(f"  date-gated (run only on their day): {gated}")
        if restamped:
            print(f"  repaired (any-mode sport byte / rule id): {[n for n, _ in restamped]}")
        if result["edited"]:
            print(f"  reinstalled after an edit: {result['edited']}")
        if not removed and not added_names and not modes_touched and not failed and not restamped:
            print("  nothing to do — watch already matches the plan")
        if result["unchanged"]:
            print("  already up to date: nothing will be written")

    if not write:
        result["written"] = False
        if json_out:
            print(json.dumps(result))
        else:
            print("dry-run: pass --write to apply")
        return result

    writes = [] if apps_unchanged else [("Apps", apps_base, new_apps_bytes)]
    if new_cm_bytes is not None:
        writes.append(("CustomModes", cm_base, new_cm_bytes))
    backup = None
    if writes:
        # A restore point for the sport modes, in ~/AmbitAppBackups (not next to this file:
        # the backend also runs sync() in its own process for Bluetooth, and a packaged app's
        # program folder is temporary or read-only).
        backup = str(backup_path("CustomModes_pre_calendar", serial))
        pathlib.Path(backup).write_bytes(current_cm)
    for name, base, blob in writes:
        fi = FlashImage(); fi.write(base, blob)
        send_plan(link, fi, [(name, base, blob), ("t", base, None)], commit=False)
    if new_cm_bytes is not None:
        # Read back and roll back on a mismatch - a CustomModes write once landed 54 bytes
        # shifted (2026-08-25, workout_install.py) and scrambled every mode after the target.
        back = bytes(read_flash(link, cm_base, len(new_cm_bytes), label="CustomModes (verify)"))
        if back != bytes(new_cm_bytes):
            old = bytes(current_cm[:cm.used_extent(current_cm)])
            fi = FlashImage(); fi.write(cm_base, old)
            send_plan(link, fi, [("CustomModes (rollback)", cm_base, old), ("t", cm_base, None)],
                      commit=False)
            result.update(ok=False, written=True, autoStart=[],
                          error="sport modes read back wrong after the write; restored the "
                                "previous sport modes (workouts were installed, auto-start was not)")
            print(json.dumps(result) if json_out else f"  !! {result['error']}")
            return result
    result["written"] = bool(writes)
    # Remember what is on the watch now: new installs get their workout's fingerprint, the
    # rest keep theirs (one never recorded gets the plan's - see edited_labels).
    on_watch = {e["name"] for e in apps.decode(new_apps_bytes) if is_managed(e["name"])}
    fresh = {}
    for name in on_watch:
        if name in by_label and (name in added_names or name not in installed):
            fresh[name] = workout_fingerprint(by_label[name]["workout"], lang)
        elif name in installed:
            fresh[name] = installed[name]
    save_installed(serial, fresh)
    result["customModesWritten"] = new_cm_bytes is not None
    result["backup"] = backup
    if json_out:
        print(json.dumps(result))
    elif writes:
        print(f"  written. CustomModes backed up -> {backup}")
    else:
        print("  already up to date: nothing written")
    return result


def remove_native_entry(current_apps_bytes, name):
    """Apps region with the native guided workout `name` taken out - for a manually installed
    one the sync never touches (e.g. the Builder's "Light workout"). Guarded by
    guided_workout.without_native (native entries only, never shifting a Suunto App)."""
    existing = WI.apps_entries_with_raw_blocks(current_apps_bytes)
    return rebuild_apps_region([e["_raw_block"] for e in GW.without_native(existing, name)])


def remove(link, name, write, json_out):
    mm = read_memory_map(link)
    apps_base, apps_size = mm["Apps"]
    current_apps = read_flash(link, apps_base, apps_size, label="Apps")
    try:
        new_apps_bytes = remove_native_entry(current_apps, name)
    except ValueError as err:
        result = {"ok": False, "error": str(err)}
        print(json.dumps(result) if json_out else f"refused: {err}")
        return result
    result = {"ok": True, "removed": [name], "appsBytes": len(new_apps_bytes), "written": False}
    result["remaining"] = [e["name"] for e in apps.decode(new_apps_bytes)]
    if write:
        backup = str(backup_path("Apps_pre_remove", watch_serial(link)))
        pathlib.Path(backup).write_bytes(bytes(current_apps))
        result["backup"] = backup
        fi = FlashImage(); fi.write(apps_base, new_apps_bytes)
        send_plan(link, fi, [("Apps", apps_base, new_apps_bytes), ("t", apps_base, None)],
                  commit=False)
        result["written"] = True
    if json_out:
        print(json.dumps(result))
    else:
        print(f"remove {name!r}: " + ("written" if write else "dry-run: pass --write to apply"))
    return result


def list_calendar(link, json_out):
    mm = read_memory_map(link)
    apps_base, apps_size = mm["Apps"]
    current_apps = read_flash(link, apps_base, apps_size, label="Apps")
    existing = WI.apps_entries_with_raw_blocks(current_apps)
    managed = [e["name"] for e in existing if is_managed(e["name"])]  # watch (menu) order
    if json_out:
        print(json.dumps({"managed": managed}))
    else:
        if not managed:
            print("no dated calendar workouts installed")
        for name in managed:
            print(f"  {name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("plan", nargs="?", help="plan JSON file (see this file's docstring)")
    ap.add_argument("--sync", action="store_true", help="diff the plan against the watch")
    ap.add_argument("--list", action="store_true", help="list installed dated calendar workouts")
    ap.add_argument("--remove", metavar="NAME",
                    help="remove one native workout by its on-watch name (e.g. 'Light workout')")
    ap.add_argument("--today", metavar="YYYY-MM-DD", help="override today's date (testing)")
    ap.add_argument("--auto-start", action="store_true",
                    help="ROUTE 2 (optional, default off): install every upcoming workout as a"
                         " date-gated rule in the plan's own sport mode (plain 'Running'), so on"
                         " its scheduled day entering that sport auto-runs the day's workout by"
                         " the watch's clock. Trade-offs: it removes the free run on a training"
                         " day (ANY entry into the sport fires that day's rule, hardware-confirmed"
                         " 2026-10-20), and the step melody + 'Workout ended' only play with the"
                         " firmware patch (the auto-start path is silent on stock). Without it"
                         " (route 1, default) the workouts are plain WORKOUT-menu picks: free run"
                         " preserved, full cues on stock. Rules from an earlier sync are removed.")
    ap.add_argument("--write", action="store_true", help="actually write (else dry-run)")
    ap.add_argument("--json", action="store_true", help="print one-line JSON (for a GUI)")
    args = ap.parse_args()

    link = Link(dry_run=False)
    link.open()
    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")

    if args.list:
        list_calendar(link, args.json)
        return 0
    if args.remove:
        return 0 if remove(link, args.remove, args.write, args.json).get("ok") else 1

    if not args.sync or not args.plan:
        ap.error("PLAN.json --sync is required (or use --list)")

    plan = json.load(open(args.plan))
    today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()
    result = sync(link, plan, today, args.write, args.json, auto_start=args.auto_start)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
