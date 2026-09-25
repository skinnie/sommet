#!/usr/bin/env python3
"""Calendar of dated native guided workouts, named "dd/mm_name" in the WORKOUT menu — the
locked design (André, 2026-08-21) for the "Today"/planned-training experience, taken instead
of the unreachable native `TrainingProgram` flash region (see
`assets/Firmware/re-out/training_program_CONCLUSION.md`).

Flow: pick a date -> pick an activity -> build a workout (tools/workout.py schema) -> this
tool compiles it through the real native guidance path (guided_workout.py's compile_workout,
the JSON->Komposti-binary breakthrough, hardware-confirmed 2026-08-19/20) and installs it into
the browsable WORKOUT menu, named "dd/mm_<workout name>" so the rider can tell entries apart
and pick the right one by hand. There is deliberately NO on-watch date logic involved — the
date is just a label a human reads, sidestepping the firmware wall entirely rather than
working around it with an App-Zone countdown app (training_plan.py's older design, superseded
for this feature by André's explicit call).

Rotation, per André ("on the sync we erase the ones past #today and put another ones"): each
--sync erases every managed (dd/mm_-prefixed) entry whose date has passed and installs
whatever's next from the plan that isn't already installed. Managed entries are recognized by
the "dd/mm_" name prefix this tool writes; anything else already on the watch (a manually
installed guided workout, a generic Suunto App) is left alone.

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
"""
import argparse
import datetime
import json
import re
import struct
import sys
import time

import apps
import custom_modes as cm
import custom_modes_write as cmw
import guided_workout as GW
import workout_install as WI
from ambit_pcap import FlashImage
from write_nav import CMD_DEVICE_INFO, Link, read_flash, read_memory_map, send_plan

MANAGED_RE = re.compile(r"^(\d{2})/(\d{2})_")


def entry_label(date_iso, workout_name):
    """"dd/mm_name", truncated to fit the 29-byte on-watch name field (apps.NAME_LEN)."""
    d = datetime.date.fromisoformat(date_iso)
    prefix = f"{d.day:02d}/{d.month:02d}_"
    budget = (apps.NAME_LEN - 1) - len(prefix)
    return prefix + workout_name[:max(budget, 0)]


def is_managed(name):
    return bool(MANAGED_RE.match(name or ""))


def is_expired(name, today):
    """True if a managed "dd/mm_..." name's (month, day) is before today's — see the module
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


def plan_diff(current_apps_bytes, plan_entries, today):
    """Returns (kept_raw_blocks, to_add) — to_add is the subset of plan_entries (date >= today)
    not already present on the watch under their computed label.

    A managed entry is kept only if it's both unexpired AND still present in plan_entries —
    otherwise it was deliberately removed from the calendar (see TrainingProgramPage.qml's
    "Remove workout") and this sync should erase it, not just leave it stranded on the watch
    until its date happens to pass. Unmanaged entries (anything not "dd/mm_"-prefixed — a
    manually installed guided workout, a generic Suunto App) are never touched, matching the
    module docstring's standing rule."""
    existing = WI.apps_entries_with_raw_blocks(current_apps_bytes)
    future_labels = {entry_label(e["date"], e["workout"]["name"]) for e in plan_entries
                      if datetime.date.fromisoformat(e["date"]) >= today}
    kept = []
    for e in existing:
        if not is_managed(e["name"]):
            kept.append(e["_raw_block"])
        elif not is_expired(e["name"], today) and e["name"] in future_labels:
            kept.append(e["_raw_block"])
        # else: expired, or managed-but-no-longer-in-the-plan — erased by omission below.
    names_present = {e["name"] for e in existing}

    to_add = []
    for e in sorted(plan_entries, key=lambda e: e["date"]):
        d = datetime.date.fromisoformat(e["date"])
        if d < today:
            continue  # already past — never install it, whether or not it was ever installed
        label = entry_label(e["date"], e["workout"]["name"])
        if label not in names_present:
            to_add.append(e)
    return kept, to_add


def sync(link, plan, today, write, json_out):
    mm = read_memory_map(link)
    cm_base, cm_size = mm["CustomModes"]
    apps_base, apps_size = mm["Apps"]
    current_cm = read_flash(link, cm_base, cm_size, label="CustomModes")
    current_apps = read_flash(link, apps_base, apps_size, label="Apps")

    kept_blocks, to_add = plan_diff(current_apps, plan["entries"], today)
    existing = WI.apps_entries_with_raw_blocks(current_apps)
    kept_block_set = set(kept_blocks)
    # Everything managed that didn't survive the diff — expired OR removed from the plan
    # (plan_diff's docstring covers which). Kept as one list, like before the fix; the two
    # cases aren't split out separately since the UI only ever showed one "erase" line.
    removed = [e["name"] for e in existing
               if is_managed(e["name"]) and e["_raw_block"] not in kept_block_set]

    lang = GW.read_watch_language(link)
    current_list = [{"_raw_block": b} for b in kept_blocks]
    added_names = []
    failed = []  # [(label, error)] — one bad workout in the plan must not sink the whole sync
    new_apps_bytes = None
    for e in to_add:
        label = entry_label(e["date"], e["workout"]["name"])
        try:
            compiled = GW.compile_workout(e["workout"], lang)
        except SystemExit as err:
            failed.append((label, str(err)))
            continue
        compiled["name"] = label
        new_apps_bytes = WI.build_apps_region(current_list, compiled, entry_type=GW.GUIDANCE_ENTRY_TYPE)
        current_list = WI.apps_entries_with_raw_blocks(new_apps_bytes)
        added_names.append(compiled["name"])
    if new_apps_bytes is None:
        new_apps_bytes = rebuild_apps_region(kept_blocks)

    # Ensure every mode named by a kept-or-added entry has the guidance display so its
    # WORKOUT menu is surfaced at all (harmless no-op if it's already there).
    decoded = cm.decode(current_cm)
    modes_needed = {e["mode"] for e in plan["entries"] if e["date"] >= str(today)} | \
                   {e.get("mode") for e in to_add}
    modes_touched = []
    for mode_name in sorted(m for m in modes_needed if m):
        idx = GW.find_mode_index(decoded, mode_name)
        mode = decoded["exercise_modes"][idx]
        if not any(d.get("Template") == GW.GUIDANCE_TEMPLATE for d in mode.get("Displays", [])):
            mode.setdefault("Displays", []).append(GW.guidance_display())
            modes_touched.append(mode_name)
    new_cm_bytes = cmw.build_custom_modes_body(decoded, decoded.get("format_type", 2)) \
        if modes_touched else current_cm

    result = {"ok": True, "today": str(today), "removed": removed, "added": added_names,
              "failed": [{"name": n, "error": err} for n, err in failed],
              "displaysAdded": modes_touched, "appsBytes": len(new_apps_bytes)}

    if not json_out:
        print(f"sync as of {today}:")
        print(f"  erase: {removed or '(none)'}")
        print(f"  install: {added_names or '(none)'}")
        if failed:
            print(f"  FAILED to compile (skipped, rest of sync still applied):")
            for n, err in failed:
                print(f"    {n}: {err}")
        if modes_touched:
            print(f"  guidance display added to: {modes_touched}")
        if not removed and not added_names and not modes_touched and not failed:
            print("  nothing to do — watch already matches the plan")

    if not write:
        result["written"] = False
        if json_out:
            print(json.dumps(result))
        else:
            print("dry-run: pass --write to apply")
        return result

    import pathlib
    backup = f"backups/CustomModes_pre_calendar_{int(time.time())}.bin"
    pathlib.Path(backup).parent.mkdir(parents=True, exist_ok=True)
    open(backup, "wb").write(current_cm)
    for name, base, blob in [("Apps", apps_base, new_apps_bytes), ("CustomModes", cm_base, new_cm_bytes)]:
        fi = FlashImage(); fi.write(base, blob)
        send_plan(link, fi, [(name, base, blob), ("t", base, None)], commit=False)
    result["written"] = True
    result["backup"] = backup
    if json_out:
        print(json.dumps(result))
    else:
        print(f"  written. CustomModes backed up -> {backup}")
    return result


def list_calendar(link, json_out):
    mm = read_memory_map(link)
    apps_base, apps_size = mm["Apps"]
    current_apps = read_flash(link, apps_base, apps_size, label="Apps")
    existing = WI.apps_entries_with_raw_blocks(current_apps)
    managed = sorted(e["name"] for e in existing if is_managed(e["name"]))
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
    ap.add_argument("--today", metavar="YYYY-MM-DD", help="override today's date (testing)")
    ap.add_argument("--write", action="store_true", help="actually write (else dry-run)")
    ap.add_argument("--json", action="store_true", help="print one-line JSON (for a GUI)")
    args = ap.parse_args()

    link = Link(dry_run=False)
    link.open()
    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")

    if args.list:
        list_calendar(link, args.json)
        return 0

    if not args.sync or not args.plan:
        ap.error("PLAN.json --sync is required (or use --list)")

    plan = json.load(open(args.plan))
    today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()
    result = sync(link, plan, today, args.write, args.json)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
