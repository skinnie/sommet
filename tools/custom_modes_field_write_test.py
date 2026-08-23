#!/usr/bin/env python3
"""Real-hardware test: writes one or more of an exercise mode's own fixed uint16 settings
fields (Autolap, HrHigh, HrLow, HrLimitsUse, ...) - the flat `SETTING_FIELDS` list
`custom_modes.py`'s own `decode_settings()` already reads right after the 64-byte `Name`.
Same real transport as `custom_modes_rename_test.py`/`custom_modes_writeback_test.py`
(write_nav.py's send_plan: CMD_DATA_WRITE chunks + CMD_DATA_TAIL padded-region SHA256 +
CMD_NAV_COMMIT) and the same discipline: the mode's settings-block offset is found live by
walking the real BXml tag tree (DEVICE_CUSTOM -> EXERCISE_MODES -> EXERCISE_MODES_MODE ->
EXERCISE_MODES_SETTING_NAME_LEN64), not assumed from a prior dump - each field's own byte
offset within that block is then computed from SETTING_FIELDS' own real, declared order
(cumulative struct.calcsize), not a hardcoded magic number, so a future change to that list
in custom_modes.py can't silently desync this file's own offsets from the real decoder's.

Verified against a known real value before ever writing anything: for the offset math to
be trusted, this file's own docstring update (see custom_modes_andre.md) records the exact
check performed - the real, already-known Autolap=1000 on "Run a route" was independently
recomputed from this same offset logic and matched exactly before any write was attempted.

    ./tools/custom_modes_field_write_test.py --mode Walk --set Autolap=10 --set HrLow=100 --set HrHigh=110 --set HrLimitsUse=1
    ./tools/custom_modes_field_write_test.py --mode Walk --set Autolap=10 --write
"""
import argparse
import datetime
import json
import pathlib
import struct
import sys

import ambit_format as F
import custom_modes as CM
from ambit_pcap import FlashImage
from write_nav import Link, check_memory_map, read_flash, read_memory_map, send_plan

BACKUP_DIR = pathlib.Path.home() / "AmbitAppBackups" / "custom_modes"


def field_offsets():
    """{field_name: byte_offset_within_the_settings_block} - offset 0 is the start of the
    64-byte Name field itself (matching decode_settings()'s own `offset` parameter),
    computed by walking SETTING_FIELDS in its own real declared order, not hardcoded."""
    offsets = {}
    cursor = 64  # past the fixed 64-byte Name field
    for name, fmt in CM.SETTING_FIELDS:
        offsets[name] = cursor
        cursor += struct.calcsize("<" + fmt)
    return offsets


FIELD_OFFSETS = field_offsets()
FIELD_FORMATS = {name: fmt for name, fmt in CM.SETTING_FIELDS}


def find_settings_base(data, mode_name):
    """The real content offset of `mode_name`'s own settings block (same base
    decode_settings() itself is called with) - found by walking the real tag tree exactly
    the way custom_modes.py's own decode() does. Returns None if not found."""
    root = CM.read_tag(data, 0)
    if root is None or root[0] != CM.DEVICE_CUSTOM:
        raise ValueError(f"expected DEVICE_CUSTOM at offset 0, got {root}")
    _, root_len = root
    cursor, end = 4, 4 + root_len

    while cursor < end:
        tag = CM.read_tag(data, cursor)
        if tag is None:
            break
        tag_id, length = tag
        content = cursor + 4
        if tag_id == CM.EXERCISE_MODES:
            sub_end, sub_cursor = content + length, content
            while sub_cursor < sub_end:
                sub_tag = CM.read_tag(data, sub_cursor)
                if sub_tag is None:
                    break
                sub_id, sub_len = sub_tag
                sub_content = sub_cursor + 4
                if sub_id == CM.EXERCISE_MODES_MODE:
                    name_tag = CM.read_tag(data, sub_content)
                    if name_tag and name_tag[0] == CM.EXERCISE_MODES_SETTING_NAME_LEN64:
                        base = sub_content + 4
                        current = data[base:base + 64].rstrip(b"\0").decode("utf-8", "replace")
                        if current == mode_name:
                            return base
                sub_cursor = sub_content + sub_len
        cursor = content + length
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", required=True, metavar="NAME",
                     help="the exercise mode's current real name, exactly as shown by "
                          "custom_modes.py")
    ap.add_argument("--set", action="append", default=[], metavar="FIELD=VALUE",
                     help="a field from SETTING_FIELDS (e.g. Autolap=10) - repeatable")
    ap.add_argument("--write", action="store_true",
                     help="actually write; without this, only reads, locates the fields, "
                          "and reports what would change")
    ap.add_argument("--json", action="store_true",
                     help="print one JSON line instead of human-readable output - for "
                          "ambitapp-v2/backend/server.py, not meant for a person to read")
    args = ap.parse_args()
    quiet = args.json

    def out(msg):
        if not quiet:
            print(msg)

    def finish(payload, code):
        if quiet:
            print(json.dumps(payload))
        return code

    if not args.set:
        out("ABORT: no --set given, nothing to do.")
        return finish({"ok": False, "error": "no --set given"}, 1)

    changes = {}
    for item in args.set:
        field, _, raw = item.partition("=")
        if field not in FIELD_OFFSETS:
            msg = f"unknown field {field!r} - known: {sorted(FIELD_OFFSETS)}"
            out(f"ABORT: {msg}")
            return finish({"ok": False, "error": msg}, 1)
        changes[field] = int(raw)

    link = Link(dry_run=False, verbose=not quiet)
    link.open()

    out("Checking memory map before touching anything...")
    found = read_memory_map(link)
    if not check_memory_map(found):
        msg = "memory map does not match expectations, refusing to write."
        out(f"ABORT: {msg}")
        return finish({"ok": False, "error": msg}, 1)

    out("Reading CustomModes...")
    fresh = read_flash(link, F.CUSTOM_MODES_BASE, F.CUSTOM_MODES_REGION_SIZE, label="CustomModes")

    base = find_settings_base(fresh, args.mode)
    if base is None:
        msg = f"{args.mode!r} not found as a real exercise mode in this reply."
        out(f"ABORT: {msg}")
        return finish({"ok": False, "error": msg}, 1)
    out(f"Found {args.mode!r} settings block at region offset {base} (0x{base:x})")

    modified = bytearray(fresh)
    changed_fields = {}
    for field, value in changes.items():
        off = base + FIELD_OFFSETS[field]
        fmt = "<" + FIELD_FORMATS[field]
        before_value = struct.unpack_from(fmt, fresh, off)[0]
        struct.pack_into(fmt, modified, off, value)
        changed_fields[field] = {"previous": before_value, "requested": value, "offset": off}
        out(f"  {field}: {before_value} -> {value}  (region offset {off}, 0x{off:x})")

    changed = sum(1 for a, b in zip(fresh, modified) if a != b)
    out(f"Would change {changed} byte(s) total")

    if not args.write:
        out("Read-only (pass --write to actually send this).")
        return finish({"ok": True, "dryRun": True, "mode": args.mode,
                        "fields": changed_fields, "bytesChanged": changed}, 0)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = BACKUP_DIR / f"before_fieldwrite_{stamp}.bin"
    backup_path.write_bytes(fresh)
    out(f"Backup written to {backup_path}")

    flash = FlashImage()
    flash.write(F.CUSTOM_MODES_BASE, bytes(modified))
    layout = [(f"CustomModes ({args.mode} field write)", F.CUSTOM_MODES_BASE, bytes(modified)),
              ("tail", F.CUSTOM_MODES_BASE, None)]

    out("Writing modified content + CMD_DATA_TAIL (padded-region SHA256) + CMD_NAV_COMMIT...")
    send_plan(link, flash, layout, commit=True)
    out("  send_plan returned without raising - no protocol-level rejection seen")

    out("Reading CustomModes back to verify...")
    after = read_flash(link, F.CUSTOM_MODES_BASE, F.CUSTOM_MODES_REGION_SIZE, label="CustomModes")

    if bytes(after) == bytes(modified):
        out(f"\nSUCCESS: region read back byte-for-byte matching the intended edit.")
        for field, value in changes.items():
            out(f"  {args.mode}.{field} is now {value}")
        return finish({"ok": True, "dryRun": False, "mode": args.mode,
                        "fields": changed_fields, "bytesChanged": changed}, 0)
    else:
        diffs = sum(1 for a, b in zip(after, modified) if a != b)
        out(f"\nMISMATCH: {diffs} bytes differ from what was intended.")
        mismatch_path = BACKUP_DIR / f"after_fieldwrite_{stamp}.bin"
        mismatch_path.write_bytes(after)
        out(f"Post-write state saved to {mismatch_path} for inspection. "
            f"Restore from {backup_path} if needed.")
        return finish({"ok": False, "error": f"{diffs} bytes differ from what was intended after write",
                        "mismatchPath": str(mismatch_path), "backupPath": str(backup_path)}, 1)


if __name__ == "__main__":
    sys.exit(main())
