#!/usr/bin/env python3
"""Dump the Ambit3/Traverse CustomModes + Apps flash regions to .bin files, for the two-watch
sport-mode sync (backend/server.py /api/sync). READ-ONLY. The two regions are cross-linked
(CustomModes rules reference Apps entries by index), so they are always dumped - and later
written - as a pair; see restore_apps_custommodes.py, which this feeds. Prints one JSON line:
{ok, apps, customModes, appsSize, customModesSize, modeCount, modeNames}.

Refuses on a watch whose declared Apps/CustomModes bases don't match the addresses
restore_apps_custommodes.py writes to (F.APPS_BASE / F.CUSTOM_MODES_BASE), so a snapshot is
never taken from a layout the restore path can't safely write back.

    ./tools/dump_sportmode_regions.py --apps-out A.bin --custom-modes-out C.bin --json
"""
import argparse
import hashlib
import json
import struct
import sys

import ambit_format as F
import custom_modes as cm
from restore_apps_custommodes import apps_used_extent
from write_nav import CMD_DEVICE_INFO, Link, read_flash, read_memory_map


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apps-out", required=True)
    ap.add_argument("--custom-modes-out", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    def fail(msg):
        print(json.dumps({"ok": False, "error": msg}))
        return 1

    link = Link(dry_run=False, verbose=not args.json)
    link.open()
    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    regions = read_memory_map(link)
    apps_r = regions.get("Apps")
    cm_r = regions.get("CustomModes")
    if not apps_r or not cm_r or apps_r[0] == 0xFFFFFFFF or cm_r[0] == 0xFFFFFFFF:
        return fail("this watch has no CustomModes/Apps region (not an Ambit3/Traverse)")
    if apps_r[0] != F.APPS_BASE or cm_r[0] != F.CUSTOM_MODES_BASE:
        return fail("this watch's sport-mode region layout differs from the reference "
                    "(Apps 0x%06x / CM 0x%06x vs 0x%06x / 0x%06x) - not safe to mirror"
                    % (apps_r[0], cm_r[0], F.APPS_BASE, F.CUSTOM_MODES_BASE))

    # The Apps region is 200 KB and slow to read over macOS HID, but is blank on any watch with
    # no App-Zone apps installed (the common case). Read just its directory header first: a blank
    # directory (num_entries 0 or 0xFFFF) means nothing is installed, so synthesize an all-0xFF
    # region instead of reading 200 KB of padding. Only a watch that really has apps pays the
    # full read.
    apps_head = read_flash(link, F.APPS_BASE, 4, label="Apps(header)")
    num_entries = struct.unpack_from("<H", apps_head, 0)[0]
    if num_entries in (0x0000, 0xFFFF):
        apps_bytes = b"\xff" * F.APPS_REGION_SIZE
    else:
        apps_bytes = read_flash(link, F.APPS_BASE, F.APPS_REGION_SIZE, label="Apps")
    cm_bytes = read_flash(link, F.CUSTOM_MODES_BASE, F.CUSTOM_MODES_REGION_SIZE,
                          label="CustomModes")
    with open(args.apps_out, "wb") as f:
        f.write(apps_bytes)
    with open(args.custom_modes_out, "wb") as f:
        f.write(cm_bytes)

    names = []
    try:
        decoded = cm.decode(cm_bytes)
        for m in decoded.get("exercise_modes", []):
            names.append(m.get("name") or m.get("Name") or "?")
    except Exception:  # noqa: BLE001 - a names summary is cosmetic; the bytes are the payload
        pass

    # A content signature over only the USED extents of each region - the padding past the
    # used data is undefined and differs between watches even when the sport modes are
    # identical, so comparing full regions gives false "differs" results. This is what the
    # sync diff compares.
    cm_ext = cm.used_extent(cm_bytes)
    apps_ext = 0 if num_entries in (0x0000, 0xFFFF) else apps_used_extent(apps_bytes)
    sig = hashlib.sha256(cm_bytes[:cm_ext] + b"|" + apps_bytes[:apps_ext]).hexdigest()

    print(json.dumps({"ok": True, "apps": args.apps_out, "customModes": args.custom_modes_out,
                      "appsSize": len(apps_bytes), "customModesSize": len(cm_bytes),
                      "modeCount": len(names), "modeNames": names, "signature": sig}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
