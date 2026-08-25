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
import json
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
    ap.add_argument("--all", action="store_true",
                    help="with --log, apply to EVERY activated app (UseRule=1) - across all"
                         " modes, or within --mode if given - instead of a single --slot. This"
                         " is the standing 'log whatever is activated in a sport mode' rule.")
    ap.add_argument("--write", action="store_true",
                    help="actually emit the write; without it nothing is sent")
    ap.add_argument("--backup-to", metavar="FILE",
                    help="save the current CustomModes region here before writing")
    ap.add_argument("--from-custom-modes", metavar="FILE",
                    help="use this raw CustomModes dump instead of reading the watch")
    ap.add_argument("--from-apps", metavar="FILE",
                    help="use this raw Apps dump to resolve app names (offline)")
    ap.add_argument("--json", action="store_true",
                    help="emit the current logging state as one JSON array (for the desktop UI)"
                         " instead of the human table; suppresses all other chatter")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    toggling = args.log is not None
    if toggling and not args.all and (args.mode is None or args.slot is None):
        ap.error("--log needs --mode and --slot (or --all) to say which app(s) to toggle")
    if args.json and toggling:
        # --json is the desktop UI's read-only listing; a toggle is a separate write call whose
        # success the backend reads from the exit code, not from JSON. Keep them from mixing.
        ap.error("--json lists the current state only; drop --log (run the toggle separately)")

    # Offline (dumps given) needs no connection at all; otherwise open the watch. A real write
    # always needs a live watch. Mirrors workout_install.py's dry-run discipline.
    offline = args.from_custom_modes is not None
    if args.write and offline:
        ap.error("--write writes to the watch; drop --from-custom-modes to write for real")
    link = None
    if not offline:
        # A live READ is safe (dry-run Link emits nothing), so the desktop UI's --json listing
        # is allowed to open the watch without --write. A human dry-run listing still requires
        # --write to keep workout_install's discipline (a --write session that then chooses not
        # to toggle). Toggling for real always needs --write, enforced below.
        if not args.write and not args.json:
            ap.error("reading the watch needs --write; pass --from-custom-modes DUMP.bin to"
                     " work offline, or --json for the desktop UI's read-only listing")
        # A live link must NOT be dry-run for a READ: in dry-run, Link.command() returns b""
        # without ever reading the watch's reply (write_nav.py line ~289), so read_flash gets
        # nothing ("short reply"). Both live paths here read for real - --json lists, --write
        # reads-then-writes - so the link is real in both (matches custom_modes.py's own reader,
        # Link(dry_run=False)). The write itself is still gated by --write below.
        link = Link(dry_run=not (args.write or args.json), verbose=args.verbose)
        if not args.json:
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
            if not args.json:
                print(f"  (couldn't read Apps region for names: {exc})")

    decoded = cm.decode(current)
    rows = collect_rules(decoded, apps_entries)

    # Desktop UI path: one JSON array on stdout, nothing else. Only activated apps (UseRule=1)
    # are things the user actually logs, but we return every rule so the UI can show state and
    # the mode/slot addressing stays stable with the human table.
    if args.json:
        print(json.dumps(rows))
        return 0

    print("\ncurrent Suunto App logging state:")
    print_rules(rows)

    if not toggling:
        return 0

    # --- collect the target rule(s) and flip LogRule ---
    want = args.log == "on"
    edited = copy.deepcopy(decoded)
    targets = []                     # (mode_index, slot, rule_dict_in_edited)

    if args.all:
        # "log whatever is activated": every rule with UseRule=1, across all modes (or within
        # --mode if given), whose LogRule isn't already what we want.
        if args.mode is not None:
            if not (0 <= args.mode < len(edited["exercise_modes"])):
                sys.exit(f"no mode[{args.mode}] - this watch has "
                         f"{len(edited['exercise_modes'])} modes")
            mode_indices = [args.mode]
        else:
            mode_indices = range(len(edited["exercise_modes"]))
        for mi in mode_indices:
            for si, rule in enumerate(edited["exercise_modes"][mi]["Rules"]):
                if rule["UseRule"] and bool(rule["LogRule"]) != want:
                    targets.append((mi, si, rule))
    else:
        try:
            mode = edited["exercise_modes"][args.mode]
        except IndexError:
            sys.exit(f"no mode[{args.mode}] - this watch has "
                     f"{len(edited['exercise_modes'])} modes")
        if not (0 <= args.slot < len(mode["Rules"])):
            sys.exit(f"mode[{args.mode}] has {len(mode['Rules'])} rule(s); no slot {args.slot}")
        rule = mode["Rules"][args.slot]
        if bool(rule["LogRule"]) != want:
            targets.append((args.mode, args.slot, rule))

    if not targets:
        print(f"\nnothing to do - the selected app(s) already have logging "
              f"{'ON' if want else 'off'}")
        return 0

    for _, _, rule in targets:
        rule["LogRule"] = want

    ft = decoded.get("format_type", 2)
    baseline = cmw.build_custom_modes_body(decoded, format_type=ft)
    payload = cmw.build_custom_modes_body(edited, format_type=ft)

    # Safety gate: each flip MUST be a single LogRule byte value change (0<->1) and nothing
    # else may move. If the encoder resized the region or changed any other byte, refuse - the
    # edit touched more than LogRule and must not reach flash. Proven offline; asserted here on
    # the actual bytes about to be written.
    if len(payload) != len(baseline):
        sys.exit(f"refusing to write: the edit resized CustomModes "
                 f"({len(baseline)} -> {len(payload)} bytes) - unexpected, aborting")
    diffs = [i for i in range(len(payload)) if payload[i] != baseline[i]]
    if len(diffs) != len(targets) or any(abs(payload[i] - baseline[i]) != 1 for i in diffs):
        sys.exit(f"refusing to write: expected {len(targets)} single-byte LogRule flip(s), got "
                 f"{len(diffs)} differing byte(s) - aborting")
    print()
    for mi, si, rule in targets:
        app = app_name_for_rule_idx(rule["RuleIdx"], apps_entries) or "(unknown app)"
        print(f"  mode[{mi}] slot {si} ({app}): LogRule -> {'ON' if want else 'off'}")
    print(f"({len(targets)} rule(s), {len(diffs)} byte(s) changed)")

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
