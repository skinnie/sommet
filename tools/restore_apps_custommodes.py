#!/usr/bin/env python3
"""Recover the Ambit3 Apps + CustomModes regions from a known-good backup pair, using the
REAL SuuntoLink write shape (training_program_andre.md Finding 28): each region written as
ONLY its used extent, closing 0x0b18 hash over exactly that extent (HASH_WRITTEN), and NO
0x0b04 nav-commit. Writing the full padded region / a nav-commit is what left the watch in
"connect to Moveslink" then "err:62 on all sport modes" after a cold boot.

DRY-RUN BY DEFAULT. Backs up the current on-watch state before writing (unless --no-backup).
Reads both regions back afterward and verifies the used extents are byte-identical to the
backup, and that CustomModes still decodes and every rule's RuleIdx points at a real Apps
entry (the cross-region consistency the firmware itself checks on a cold boot).

    ./tools/restore_apps_custommodes.py \\
        --apps  backups/Apps_before_walk_install.bin \\
        --custom-modes backups/CustomModes_before_walk_install.bin --write
"""

import argparse
import struct
import sys

import apps
import ambit_format as F
import custom_modes as cm
from ambit_pcap import FlashImage
from write_nav import (CMD_DEVICE_INFO, Link, check_memory_map, pre_write_backup_path,
                        read_flash, read_memory_map, send_plan)


def apps_used_extent(data):
    """Apps region's used length = the directory's own total_length (its last table entry)."""
    num_entries, _ = struct.unpack_from("<HH", data, 0)
    table = struct.unpack_from(f"<{num_entries + 1}I", data, 4)
    return table[-1]


def check_consistency(apps_bytes, cm_bytes):
    """Every CustomModes rule must reference a real Apps entry (RuleIdx = that entry's index
    in the Apps directory - Finding 23). A mismatch is exactly what makes the firmware reject
    the whole sport-mode region."""
    entries = apps.decode(apps_bytes)
    decoded = cm.decode(cm_bytes)
    rules = sorted({r["RuleIdx"] for m in decoded["exercise_modes"] for r in m["Rules"]})
    bad = [r for r in rules if r >= len(entries)]
    if bad:
        raise SystemExit(f"refusing: CustomModes references RuleIdx {bad} but Apps has only "
                         f"{len(entries)} entries - inconsistent backup pair")
    return len(entries), rules


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apps", required=True, help="known-good Apps region .bin")
    ap.add_argument("--custom-modes", required=True, help="known-good CustomModes region .bin")
    ap.add_argument("--write", action="store_true",
                     help="actually emits; without this nothing is sent")
    ap.add_argument("--no-backup", action="store_true",
                     help="skip backing up the current on-watch state first")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    apps_bytes = open(args.apps, "rb").read()
    cm_bytes = open(args.custom_modes, "rb").read()
    if len(apps_bytes) != F.APPS_REGION_SIZE:
        sys.exit(f"{args.apps}: {len(apps_bytes)} bytes, expected {F.APPS_REGION_SIZE}")
    if len(cm_bytes) != F.CUSTOM_MODES_REGION_SIZE:
        sys.exit(f"{args.custom_modes}: {len(cm_bytes)} bytes, expected "
                 f"{F.CUSTOM_MODES_REGION_SIZE}")

    n_entries, rules = check_consistency(apps_bytes, cm_bytes)
    apps_extent = apps_used_extent(apps_bytes)
    cm_extent = cm.used_extent(cm_bytes)
    print(f"backup pair OK: {n_entries} apps, rules {rules}")
    print(f"  Apps        used extent {apps_extent} of {len(apps_bytes)}")
    print(f"  CustomModes used extent {cm_extent} of {len(cm_bytes)}")

    link = Link(dry_run=not args.write, verbose=args.verbose)
    if args.write:
        print("!! REAL WRITE requested")
        link.open()
    else:
        print("dry-run mode: not a byte will be emitted")

    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    check_memory_map(read_memory_map(link))

    if args.write and not args.no_backup:
        cur_a = read_flash(link, F.APPS_BASE, F.APPS_REGION_SIZE, label="Apps(backup)")
        cur_c = read_flash(link, F.CUSTOM_MODES_BASE, F.CUSTOM_MODES_REGION_SIZE,
                            label="CM(backup)")
        apps_path = pre_write_backup_path("Apps_prerestore", link)
        cm_path = pre_write_backup_path("CustomModes_prerestore", link)
        apps_path.write_bytes(cur_a)
        cm_path.write_bytes(cur_c)
        print(f"  backed up current state to {apps_path.parent}")

    # Apps first, then CustomModes - so a mid-way failure never points a rule at a missing app.
    apps_payload = apps_bytes[:apps_extent]
    fa = FlashImage(); fa.write(F.APPS_BASE, apps_payload)
    send_plan(link, fa, [("Apps", F.APPS_BASE, apps_payload),
                         ("tail", F.APPS_BASE, None)], commit=False)

    cm_payload = cm_bytes[:cm_extent]
    fc = FlashImage(); fc.write(F.CUSTOM_MODES_BASE, cm_payload)
    send_plan(link, fc, [("CustomModes", F.CUSTOM_MODES_BASE, cm_payload),
                         ("tail", F.CUSTOM_MODES_BASE, None)], commit=False)

    if args.write:
        back_a = read_flash(link, F.APPS_BASE, apps_extent, label="Apps(verify)")
        back_c = read_flash(link, F.CUSTOM_MODES_BASE, cm_extent, label="CM(verify)")
        ok = back_a == apps_payload and back_c == cm_payload
        print(f"  readback Apps match={back_a == apps_payload}  "
              f"CustomModes match={back_c == cm_payload}")
        # decode readback to confirm it's a coherent state
        check_consistency(back_a + b"\xff" * (F.APPS_REGION_SIZE - len(back_a)),
                          back_c + b"\xff" * (F.CUSTOM_MODES_REGION_SIZE - len(back_c)))
        print("  readback decodes coherently (rules <-> apps consistent)")
        if not ok:
            sys.exit("READBACK MISMATCH - do NOT restart the watch, investigate")
        print("\nRestore written and verified. Restart the watch to confirm err:62 is gone.")
    else:
        total = sum(len(payload) for _, payload, _ in link.sent)
        print(f"\n{len(link.sent)} messages, {total} payload bytes — nothing was emitted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
