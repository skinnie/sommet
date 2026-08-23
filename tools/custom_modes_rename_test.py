#!/usr/bin/env python3
"""Real-hardware test: the first actual CONTENT edit written into CustomModes, not just the
identical-content no-op custom_modes_writeback_test.py already confirmed working
(2026-08-07 - see that file's own docstring and V3_CHANGELOG.md). Everything about the
transport here is unchanged from that confirmed-working test (write_nav.py's own send_plan:
CMD_DATA_WRITE chunks + CMD_DATA_TAIL padded-region SHA256 + CMD_NAV_COMMIT) - the only new
thing is that the bytes being written differ from what was just read, by exactly one
field's worth.

**Scope, real and minimal on purpose**: renames one exercise mode's name everywhere it
really appears in the live, currently-parsed BXml tree - both its own
`EXERCISE_MODES_SETTING_NAME_LEN64` field (a fixed 64-byte, NUL-padded, ISO-8859-15 string)
*and* the matching `SPORT_MODE_SETTING_NAME_LEN64` field of whichever SPORT_MODES
(multisport) slot references that same mode - two separate, real 64-byte fields, found by
walking the actual tag tree the same way custom_modes.py's own decode() does, not by a
blind byte search. **A blind search for the raw bytes of a real name matched three places,
not two** - a real, found-live surprise this file's own history includes: the third match
(this project traced it back to sitting well past the ~10240-byte BXml body, in the region
this project has never confirmed the structure of - see custom_modes_write.py's own
docstring) is stale/orphaned data outside the real parsed structure entirely, not a field
the watch's firmware actually reads. Only the two byte ranges the real walk visits are ever
touched; nothing else in the 12288-byte region changes. Fixed-width fields mean this can be
a pure byte-substitution at known offsets - no BXml tag length changes, no re-encoding of
anything else in the region, and (unlike custom_modes_write.py's own full encoder, whose
own docstring flags the closing structure beyond the BXml body as unconfirmed) no
dependency on reproducing any byte this project doesn't already have real, direct read
access to. Offsets are found live on every run, by walking the just-read region's own real
tag tree - never hardcoded/assumed from a prior dump, the same "always re-derive from what
the watch just said" discipline that mattered for the real DeviceSettings entry-ID bug
found the same day (custom_modes_andre.md).

    ./tools/custom_modes_rename_test.py --from "Walk" --to "Hiking"
    ./tools/custom_modes_rename_test.py --from "Walk" --to "Hiking" --write
"""
import argparse
import datetime
import json
import pathlib
import sys

import ambit_format as F
import custom_modes as CM
from ambit_pcap import FlashImage
from write_nav import Link, check_memory_map, read_flash, read_memory_map, send_plan

BACKUP_DIR = pathlib.Path.home() / "AmbitAppBackups" / "custom_modes"
NAME_FIELD_WIDTH = 64


def find_name_offsets(data, name):
    """Every real content-byte offset where `name`'s own 64-byte NUL-padded field lives in
    the CURRENTLY PARSED BXml tree - walked exactly the way custom_modes.py's own decode()
    does (DEVICE_CUSTOM -> EXERCISE_MODES -> EXERCISE_MODES_MODE, and
    DEVICE_CUSTOM -> SPORT_MODES -> SPORT_MODE -> SPORT_MODE_SETTING_NAME_LEN64), not a
    blind byte search. A blind search over this exact data found a third match for a real
    name - real, stale bytes sitting outside this walk entirely (past the confirmed BXml
    body - see custom_modes_write.py's own docstring) - so only offsets this real walk
    actually visits are ever returned; nothing outside the tree this function walks is
    touched by design, not just by construction."""
    root = CM.read_tag(data, 0)
    if root is None or root[0] != CM.DEVICE_CUSTOM:
        raise ValueError(f"expected DEVICE_CUSTOM at offset 0, got {root}")
    _, root_len = root
    cursor = 4
    end = 4 + root_len
    offsets = []
    target = name.strip()

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
                    # The mode's own name is the first, EXERCISE_MODES_SETTING_NAME_LEN64
                    # tag inside this content - real offset = sub_content + 4 (its own
                    # tag header), matching decode_settings()'s own data[offset:offset+64].
                    name_tag = CM.read_tag(data, sub_content)
                    if name_tag and name_tag[0] == CM.EXERCISE_MODES_SETTING_NAME_LEN64:
                        name_off = sub_content + 4
                        current = data[name_off:name_off + NAME_FIELD_WIDTH].rstrip(b"\0").decode(
                            "utf-8", "replace")
                        if current == target:
                            offsets.append(name_off)
                sub_cursor = sub_content + sub_len

        elif tag_id == CM.SPORT_MODES:
            sub_end, sub_cursor = content + length, content
            while sub_cursor < sub_end:
                sub_tag = CM.read_tag(data, sub_cursor)
                if sub_tag is None:
                    break
                sub_id, sub_len = sub_tag
                sub_content = sub_cursor + 4
                if sub_id == CM.SPORT_MODE:
                    slot_end, slot_cursor = sub_content + sub_len, sub_content
                    while slot_cursor < slot_end:
                        inner_tag = CM.read_tag(data, slot_cursor)
                        if inner_tag is None:
                            break
                        inner_id, inner_len = inner_tag
                        inner_content = slot_cursor + 4
                        if inner_id == CM.SPORT_MODE_SETTING_NAME_LEN64:
                            current = data[inner_content:inner_content + inner_len].rstrip(b"\0").decode(
                                "utf-8", "replace")
                            if current == target:
                                offsets.append(inner_content)
                        slot_cursor = inner_content + inner_len
                sub_cursor = sub_content + sub_len

        cursor = content + length

    return offsets


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="from_name", required=True, metavar="NAME",
                     help="the exercise mode's current real name, exactly as shown by "
                          "custom_modes.py")
    ap.add_argument("--to", dest="to_name", required=True, metavar="NAME",
                     help="the new name - must fit in 64 bytes (ISO-8859-15) including "
                          "the terminating NUL")
    ap.add_argument("--write", action="store_true",
                     help="actually write; without this, only reads, locates the field, "
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

    new_bytes = args.to_name.encode("utf-8") + b"\0"
    if len(new_bytes) > NAME_FIELD_WIDTH:
        msg = f"{args.to_name!r} is {len(new_bytes)} bytes encoded, doesn't fit in the {NAME_FIELD_WIDTH}-byte field."
        out(f"ABORT: {msg}")
        return finish({"ok": False, "error": msg}, 1)

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

    offsets = find_name_offsets(fresh, args.from_name)
    if not offsets:
        msg = f"{args.from_name!r} not found anywhere in the real, currently-parsed BXml tree."
        out(f"ABORT: {msg}")
        return finish({"ok": False, "error": msg}, 1)
    out(f"Found {args.from_name!r} in the real parsed tree at {len(offsets)} "
        f"place(s): {[f'0x{o:x}' for o in offsets]}")

    modified = bytearray(fresh)
    for offset in offsets:
        modified[offset:offset + NAME_FIELD_WIDTH] = new_bytes.ljust(NAME_FIELD_WIDTH, b"\0")
    changed = sum(1 for a, b in zip(fresh, modified) if a != b)
    out(f"Would change {changed} byte(s) across {len(offsets)} name field(s) "
        f"({NAME_FIELD_WIDTH} bytes each)")

    if not args.write:
        out("Read-only (pass --write to actually send this).")
        return finish({"ok": True, "dryRun": True, "from": args.from_name, "to": args.to_name,
                        "offsets": offsets, "bytesChanged": changed}, 0)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = BACKUP_DIR / f"before_rename_{stamp}.bin"
    backup_path.write_bytes(fresh)
    out(f"Backup written to {backup_path}")

    flash = FlashImage()
    flash.write(F.CUSTOM_MODES_BASE, bytes(modified))
    layout = [(f"CustomModes (rename {args.from_name!r} -> {args.to_name!r})",
               F.CUSTOM_MODES_BASE, bytes(modified)),
              ("tail", F.CUSTOM_MODES_BASE, None)]

    out("Writing modified content + CMD_DATA_TAIL (padded-region SHA256) + CMD_NAV_COMMIT...")
    send_plan(link, flash, layout, commit=True)
    out("  send_plan returned without raising - no protocol-level rejection seen")

    out("Reading CustomModes back to verify...")
    after = read_flash(link, F.CUSTOM_MODES_BASE, F.CUSTOM_MODES_REGION_SIZE, label="CustomModes")

    if bytes(after) == bytes(modified):
        out(f"\nSUCCESS: region read back byte-for-byte matching the intended edit.")
        out(f"PLEASE CHECK THE WATCH NOW: {args.from_name!r} should now show as {args.to_name!r}")
        return finish({"ok": True, "dryRun": False, "from": args.from_name, "to": args.to_name,
                        "offsets": offsets, "bytesChanged": changed}, 0)
    else:
        diffs = sum(1 for a, b in zip(after, modified) if a != b)
        out(f"\nMISMATCH: {diffs} bytes differ from what was intended.")
        mismatch_path = BACKUP_DIR / f"after_rename_{stamp}.bin"
        mismatch_path.write_bytes(after)
        out(f"Post-write state saved to {mismatch_path} for inspection. "
            f"Restore from {backup_path} if needed.")
        return finish({"ok": False, "error": f"{diffs} bytes differ from what was intended after write",
                        "mismatchPath": str(mismatch_path), "backupPath": str(backup_path)}, 1)


if __name__ == "__main__":
    sys.exit(main())
