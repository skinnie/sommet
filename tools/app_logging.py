#!/usr/bin/env python3
"""Turns a Suunto App's *logging* on or off - reviving the Movescount feature where an app
assigned to a sport mode not only shows a value on the watch but also *records* that value
into the Move, so it can appear as its own graph in later analysis.

The mechanism (traced end to end in this repo):

  * A sport mode carries up to five EXERCISE_MODES_RULE entries `{RuleIdx, UseRule, LogRule}`
    (`custom_modes.py:decode_rule`). RuleIdx = the app's 0-based position in the Apps flash
    region; UseRule=1 means the app runs/displays; **LogRule=1 means its per-sample output is
    written into the recorded Move**. See custom_modes.py's own comment on the tag.

  * At record time the firmware emits each logged app's value into the periodic sample as
    `ruleoutput1..5` (periodic types 0x64-0x68, `libambit.h`), which `exercise_log.py` already
    decodes. ruleoutput slot N corresponds to the Nth logged rule.

SuuntoLink dropped this: **every** real SuuntoLink app-install capture in
`assets/ambit3 pcap/` writes LogRule=0 (verified - it never logs), so there is no SuuntoLink
capture that turns it on. What makes the write safe anyway is that it is the single most
contained CustomModes edit possible: flipping LogRule on one already-installed app changes
**exactly one byte** (0x00 -> 0x01) at that rule's LogRule offset, region length and every
other byte unchanged (proven offline against real captures - the CustomModes encoder rebuilds
those captures byte-exact, and re-encoding with only LogRule flipped differs in that one byte).
It cannot shift an offset or resize a mode, so it cannot brick a mode the way a structural
CustomModes bug could.

What it CANNOT prove offline is that the firmware then actually emits ruleoutputN - only a real
recorded Move can. So the intended flow is: enable logging here -> record a real activity on the
watch with that app's mode -> read it back with `exercise_log.py` and check for ruleoutput
samples (which also calibrates the raw int32 -> real-units scaling, unknown until we see one).

    ./tools/app_logging.py --from-custom-modes DUMP.bin           # list apps + LogRule state
    ./tools/app_logging.py --from-custom-modes DUMP.bin --mode 0 --slot 0 --log on   # dry-run
    ./tools/app_logging.py --mode 0 --slot 0 --log on --write     # real write to the watch

Dry-run by default: without --write not a byte is emitted. --backup-to saves the current
CustomModes region before a real write, exactly like workout_install.py.
"""

import argparse
import copy
import sys

import apps
import ambit_format as F
import custom_modes as cm
import custom_modes_write as cmw
from ambit_pcap import FlashImage
from write_nav import (CMD_DEVICE_INFO, Link, check_memory_map, read_flash,
                       read_memory_map, send_plan)


def app_name_for_rule_idx(rule_idx, apps_entries):
    """RuleIdx is the app's 0-based physical position in the Apps region (workout_install.py's
    next_rule_idx()). Return its name, or None if we have no Apps dump / it's out of range."""
    if apps_entries is None or not (0 <= rule_idx < len(apps_entries)):
        return None
    return apps_entries[rule_idx].get("name")


def collect_rules(decoded, apps_entries):
    """Flat list of every rule across every mode: dicts with mode index/name, slot (rule
    position within the mode), RuleIdx, app name, UseRule, LogRule."""
    rows = []
    for mi, mode in enumerate(decoded["exercise_modes"]):
        mode_name = mode["Settings"].get("Name")
        for slot, rule in enumerate(mode["Rules"]):
            rows.append({
                "mode": mi, "mode_name": mode_name, "slot": slot,
                "rule_idx": rule["RuleIdx"], "use_rule": rule["UseRule"],
                "log_rule": rule["LogRule"],
                "app": app_name_for_rule_idx(rule["RuleIdx"], apps_entries),
            })
    return rows


def print_rules(rows):
    if not rows:
        print("  no sport mode has any Suunto App installed (no rules found)")
        return
    print(f"  {'mode':>4}  {'slot':>4}  {'ruleIdx':>7}  {'use':>3}  {'LOG':>3}  app")
    last_mode = None
    for r in rows:
        if r["mode"] != last_mode:
            print(f"  mode[{r['mode']}] = {r['mode_name']!r}")
            last_mode = r["mode"]
        app = r["app"] if r["app"] is not None else "(name needs an Apps dump)"
        print(f"  {r['mode']:>4}  {r['slot']:>4}  {r['rule_idx']:>7}  "
              f"{'yes' if r['use_rule'] else 'no':>3}  "
              f"{'ON' if r['log_rule'] else 'off':>3}  {app}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", type=int, help="EXERCISE_MODES_MODE index (see the listing)")
    ap.add_argument("--slot", type=int,
                    help="which rule within that mode (0-based, as printed); the app in that"
                         " slot is the one toggled")
    ap.add_argument("--log", choices=("on", "off"),
                    help="turn that app's logging on or off (omit to just list)")
    ap.add_argument("--write", action="store_true",
                    help="actually emit the write; without it nothing is sent")
    ap.add_argument("--backup-to", metavar="FILE",
                    help="save the current CustomModes region here before writing")
    ap.add_argument("--from-custom-modes", metavar="FILE",
                    help="use this raw CustomModes dump instead of reading the watch")
    ap.add_argument("--from-apps", metavar="FILE",
                    help="use this raw Apps dump to resolve app names (offline)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    toggling = args.log is not None
    if toggling and (args.mode is None or args.slot is None):
        ap.error("--log needs --mode and --slot to say which app to toggle")

    # Offline (dumps given) needs no connection at all; otherwise open the watch. A real write
    # always needs a live watch. Mirrors workout_install.py's dry-run discipline.
    offline = args.from_custom_modes is not None
    if args.write and offline:
        ap.error("--write writes to the watch; drop --from-custom-modes to write for real")
    link = None
    if not offline:
        if not args.write:
            # Listing/dry-run without a dump still needs to read the watch, which needs a
            # connection. Match workout_install: reads require --write's live link.
            ap.error("reading the watch needs --write; pass --from-custom-modes DUMP.bin to"
                     " work offline")
        link = Link(dry_run=not args.write, verbose=args.verbose)
        print("!! REAL WRITE session" if args.write else "dry-run: nothing will be emitted")
        link.open()
        link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
        check_memory_map(read_memory_map(link))

    # --- load current CustomModes ---
    if args.from_custom_modes:
        with open(args.from_custom_modes, "rb") as f:
            current = f.read()
    else:
        current = read_flash(link, F.CUSTOM_MODES_BASE, F.CUSTOM_MODES_REGION_SIZE,
                             label="CustomModes")

    # --- load Apps region for names (best-effort) ---
    apps_entries = None
    if args.from_apps:
        with open(args.from_apps, "rb") as f:
            apps_entries = apps.decode(f.read())
    elif link is not None:
        try:
            apps_entries = apps.decode(apps.read_apps_region(link))
        except Exception as exc:                          # names are a nicety, never fatal
            print(f"  (couldn't read Apps region for names: {exc})")

    decoded = cm.decode(current)
    rows = collect_rules(decoded, apps_entries)

    print("\ncurrent Suunto App logging state:")
    print_rules(rows)

    if not toggling:
        return 0

    # --- locate and flip the target rule ---
    want = args.log == "on"
    try:
        mode = decoded["exercise_modes"][args.mode]
    except IndexError:
        sys.exit(f"no mode[{args.mode}] - this watch has {len(decoded['exercise_modes'])} modes")
    if not (0 <= args.slot < len(mode["Rules"])):
        sys.exit(f"mode[{args.mode}] has {len(mode['Rules'])} rule(s); no slot {args.slot}")

    edited = copy.deepcopy(decoded)
    rule = edited["exercise_modes"][args.mode]["Rules"][args.slot]
    app = app_name_for_rule_idx(rule["RuleIdx"], apps_entries) or "(unknown app)"
    if bool(rule["LogRule"]) == want:
        print(f"\nmode[{args.mode}] slot {args.slot} ({app}) LogRule is already "
              f"{'ON' if want else 'off'} - nothing to do")
        return 0
    rule["LogRule"] = want

    ft = decoded.get("format_type", 2)
    baseline = cmw.build_custom_modes_body(decoded, format_type=ft)
    payload = cmw.build_custom_modes_body(edited, format_type=ft)

    # Safety gate: this edit MUST be a single-byte value flip. If the encoder produced any
    # other change (a resized region, a shifted offset), refuse - that would mean the toggle
    # touched more than the LogRule field and must not reach flash. Proven behaviour offline;
    # this asserts it on the actual bytes about to be written.
    if len(payload) != len(baseline):
        sys.exit(f"refusing to write: enabling logging resized CustomModes "
                 f"({len(baseline)} -> {len(payload)} bytes) - unexpected, aborting")
    diffs = [i for i in range(len(payload)) if payload[i] != baseline[i]]
    if len(diffs) != 1 or abs(payload[diffs[0]] - baseline[diffs[0]]) != 1:
        sys.exit(f"refusing to write: expected exactly one LogRule byte to change, got "
                 f"{len(diffs)} differing byte(s) - aborting")
    off = diffs[0]
    print(f"\nmode[{args.mode}] slot {args.slot} ({app}): LogRule "
          f"{'off -> ON' if want else 'ON -> off'}  "
          f"(1 byte @ region+{off}: {baseline[off]:#04x} -> {payload[off]:#04x})")

    if args.backup_to and not offline:
        with open(args.backup_to, "wb") as f:
            f.write(current)
        print(f"backed up current CustomModes to {args.backup_to}")

    if offline:
        print("offline (--from-custom-modes): computed the write, emitted nothing")
        return 0

    # Write only the used BXML extent, no 0x0b04 nav-commit - the same finalization every
    # CustomModes write in this repo uses (workout_install.py's Finding 27/28 notes).
    flash = FlashImage()
    flash.write(F.CUSTOM_MODES_BASE, payload)
    layout = [("CustomModes", F.CUSTOM_MODES_BASE, payload),
              ("tail", F.CUSTOM_MODES_BASE, None)]
    send_plan(link, flash, layout, commit=False)

    total = sum(len(p) for _, p, _ in link.sent)
    print(f"\n{len(link.sent)} messages, {total} payload bytes"
          + ("" if args.write else " — nothing was emitted"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
