#!/usr/bin/env python3
"""Write a source watch's CustomModes (+ Apps) regions onto the CONNECTED watch, for the
two-watch sport-mode sync (backend/server.py /api/sync). This is the whole-region MIRROR: the
target's sport modes become identical to the source's. Built on the exact write discipline
restore_apps_custommodes.py proved on real hardware (used extent only, HASH_WRITTEN, no
0x0b04 nav-commit), with three differences it needs to be a sync writer rather than a recovery
tool:

  1. Handles a BLANK Apps region (a watch with no App-Zone apps installed): num_entries reads
     as 0xFFFF, so there is nothing to write - the Apps step is skipped and only CustomModes is
     mirrored. Refuses if the source's modes actually reference apps but its Apps region is
     blank (an inconsistent source).
  2. Backs the target's current CustomModes/Apps up to --backup-dir before writing (the restore
     tool's own backup path was hardcoded), so a mirror is always reversible.
  3. DRY-RUN BY DEFAULT; --write emits. --json prints one machine-readable result line.

    ./tools/sync_write_sportmodes.py --apps A.bin --custom-modes C.bin \
        --backup-dir ~/AmbitAppBackups --write --json
"""
import argparse
import json
import struct
import sys
import time
from pathlib import Path

import ambit_format as F
import apps
import custom_modes as cm
from ambit_pcap import FlashImage
from restore_apps_custommodes import apps_used_extent, check_consistency
from write_nav import (CMD_DEVICE_INFO, Link, check_memory_map, read_flash,
                       read_memory_map, send_plan)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apps", required=True, help="source watch's Apps region .bin")
    ap.add_argument("--custom-modes", required=True, help="source watch's CustomModes .bin")
    ap.add_argument("--backup-dir", default=str(Path.home() / "AmbitAppBackups"))
    ap.add_argument("--write", action="store_true", help="actually emit; else dry-run")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    def out(payload, code=0):
        if args.json:
            print(json.dumps(payload))
        else:
            print(payload)
        return code

    apps_bytes = Path(args.apps).read_bytes()
    cm_bytes = Path(args.custom_modes).read_bytes()
    if len(apps_bytes) != F.APPS_REGION_SIZE:
        return out({"ok": False, "error": "apps region wrong size %d" % len(apps_bytes)}, 1)
    if len(cm_bytes) != F.CUSTOM_MODES_REGION_SIZE:
        return out({"ok": False, "error": "custom-modes region wrong size %d" % len(cm_bytes)}, 1)

    # A blank Apps directory reads num_entries == 0xFFFF: nothing installed, nothing to write.
    num_entries = struct.unpack_from("<H", apps_bytes, 0)[0]
    apps_blank = num_entries in (0x0000, 0xFFFF)
    decoded = cm.decode(cm_bytes)
    referenced = sorted({r["RuleIdx"] for m in decoded.get("exercise_modes", [])
                         for r in m.get("Rules", [])})
    if apps_blank:
        if referenced:
            return out({"ok": False, "error": "source modes reference apps %s but the source "
                        "Apps region is blank - inconsistent source" % referenced}, 1)
        apps_extent = 0
    else:
        check_consistency(apps_bytes, cm_bytes)   # rules <-> apps entries must line up
        apps_extent = apps_used_extent(apps_bytes)
    cm_extent = cm.used_extent(cm_bytes)

    link = Link(dry_run=not args.write, verbose=not args.json)
    if args.write:
        link.open()
    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    regions = read_memory_map(link)
    if regions.get("CustomModes", (0xFFFFFFFF,))[0] != F.CUSTOM_MODES_BASE:
        return out({"ok": False, "error": "target has no matching CustomModes region"}, 1)

    backup = None
    if args.write:
        # Back the target up first - a mirror overwrites its sport modes, so keep a restore point.
        cur_a = read_flash(link, F.APPS_BASE, F.APPS_REGION_SIZE, label="Apps(backup)")
        cur_c = read_flash(link, F.CUSTOM_MODES_BASE, F.CUSTOM_MODES_REGION_SIZE, label="CM(backup)")
        bdir = Path(args.backup_dir).expanduser()
        bdir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (bdir / f"sportmodes-prewrite-{stamp}-apps.bin").write_bytes(cur_a)
        (bdir / f"sportmodes-prewrite-{stamp}-custommodes.bin").write_bytes(cur_c)
        backup = str(bdir / f"sportmodes-prewrite-{stamp}-*.bin")

    # Apps first (if any) so a mid-way failure never points a rule at a missing app.
    if apps_extent > 0:
        payload = apps_bytes[:apps_extent]
        fa = FlashImage(); fa.write(F.APPS_BASE, payload)
        send_plan(link, fa, [("Apps", F.APPS_BASE, payload), ("tail", F.APPS_BASE, None)],
                  commit=False)
    cm_payload = cm_bytes[:cm_extent]
    fc = FlashImage(); fc.write(F.CUSTOM_MODES_BASE, cm_payload)
    send_plan(link, fc, [("CustomModes", F.CUSTOM_MODES_BASE, cm_payload),
                         ("tail", F.CUSTOM_MODES_BASE, None)], commit=False)

    if not args.write:
        return out({"ok": True, "wrote": False, "dryRun": True, "cmBytes": cm_extent,
                    "appsBytes": apps_extent, "modeCount": len(decoded.get("exercise_modes", []))})

    back_c = read_flash(link, F.CUSTOM_MODES_BASE, cm_extent, label="CM(verify)")
    cm_ok = back_c == cm_payload
    apps_ok = True
    if apps_extent > 0:
        back_a = read_flash(link, F.APPS_BASE, apps_extent, label="Apps(verify)")
        apps_ok = back_a == apps_bytes[:apps_extent]
    if not (cm_ok and apps_ok):
        return out({"ok": False, "error": "READBACK MISMATCH - do not restart the watch",
                    "cmMatch": cm_ok, "appsMatch": apps_ok, "backup": backup}, 1)
    return out({"ok": True, "wrote": True, "cmBytes": cm_extent, "appsBytes": apps_extent,
                "modeCount": len(decoded.get("exercise_modes", [])), "backup": backup})


if __name__ == "__main__":
    sys.exit(main())
