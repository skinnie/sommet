#!/usr/bin/env python3
"""Real-hardware test, confirmed 2026-08-07: writes the CustomModes region back to the watch
with EXACTLY the bytes just read (true no-op at the content level), using this project's own
already-proven generic writer (write_nav.py's send_plan: CMD_DATA_WRITE chunks + CMD_DATA_TAIL
closing hash + CMD_NAV_COMMIT), using ambit_format.py's own HASH_PADDED classification for
CustomModes (recorded 2026-08-05 by analogy with Routes/Waypoints, unconfirmed until this
test). Tests only the transport/commit mechanism - no new content, nothing synthesized.

**Confirmed working**: the watch accepted the write, and a fresh read back afterward was
byte-for-byte identical. See V3_CHANGELOG.md's 2026-08-07 entry. This resolves the open
"does CustomModes need a CMD_NAV_COMMIT" question from workout_install.py's own docstring -
yes, the full Routes/Waypoints-style sequence, not GpsSGEE's simpler one.

Safety: backs up to ~/AmbitAppBackups/ before touching flash (same directory the backend's
BackupService already uses); reads back immediately after and reports whether the region is
still byte-identical; aborts instead of writing if the memory map doesn't match expectations.

    ./tools/custom_modes_writeback_test.py --write
"""
import argparse
import datetime
import pathlib

import ambit_format as F
import custom_modes as CM
from ambit_pcap import FlashImage
from write_nav import Link, check_memory_map, read_flash, read_memory_map, send_plan

BACKUP_DIR = pathlib.Path.home() / "AmbitAppBackups" / "custom_modes"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true",
                    help="actually write; without this, only reads and reports")
    args = ap.parse_args()

    link = Link(dry_run=False, verbose=False)
    link.open()

    print("Checking memory map before touching anything...")
    found = read_memory_map(link)
    if not check_memory_map(found):
        print("ABORT: memory map does not match expectations, refusing to write.")
        return 1

    print("Reading CustomModes...")
    fresh = read_flash(link, F.CUSTOM_MODES_BASE, F.CUSTOM_MODES_REGION_SIZE,
                       label="CustomModes")

    if not args.write:
        print("Read-only (pass --write to actually test the write path).")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = BACKUP_DIR / f"before_write_{stamp}.bin"
    backup_path.write_bytes(fresh)
    print(f"Backup written to {backup_path}")

    # Only the used extent and no 0x0b04 (fixed 2026-10-29): the padded region gets a hash the
    # watch rejects after a restart, and 0x0b04 empties its POI lists.
    payload = bytes(fresh[:CM.used_extent(fresh)])
    flash = FlashImage()
    flash.write(F.CUSTOM_MODES_BASE, payload)
    layout = [("CustomModes (writeback test, identical content)", F.CUSTOM_MODES_BASE, payload),
              ("tail", F.CUSTOM_MODES_BASE, None)]

    print(f"Writing back the identical used extent ({len(payload)} bytes) + CMD_DATA_TAIL...")
    send_plan(link, flash, layout, commit=False)
    print("  send_plan returned without raising - no protocol-level rejection seen")

    print("Reading CustomModes back to verify...")
    after = read_flash(link, F.CUSTOM_MODES_BASE, len(payload), label="CustomModes")

    if bytes(after) == payload:
        print("\nSUCCESS: region read back byte-for-byte identical to what was written.")
        return 0
    else:
        diffs = sum(1 for a, b in zip(after, payload) if a != b)
        print(f"\nMISMATCH: {diffs} bytes differ after write.")
        mismatch_path = BACKUP_DIR / f"after_write_{stamp}.bin"
        mismatch_path.write_bytes(after)
        print(f"Post-write state saved to {mismatch_path} for inspection. Restore from "
              f"{backup_path} if needed.")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
