#!/usr/bin/env python3
"""Writes GPS/GLONASS extended ephemeris (AGPS orbital data) to the Ambit3's GpsSGEE flash
region. DRY-RUN BY DEFAULT: without --write nothing is emitted, only the exact bytes are
logged. See sgee_andre.md for the full derivation and the live, unauthenticated data source
this was verified against on real hardware, 2026-08-05.

    ./tools/sgee.py gpsorbit.bin                          # simulates, nothing is emitted
    ./tools/sgee.py gpsorbit.bin --compare CAPTURE         # checks against a capture
    ./tools/sgee.py gpsorbit.bin --write                   # ACTUALLY EMITS

Shares the low-level watch transport (`Link`, `send_plan`, the memory-map check) with
`write_nav.py` by importing it, the same way `custom_modes.py`/`apps.py`/`exercise_log.py`
do - not by being folded into `write_nav.py` itself, which is specifically for the
navigation database (routes/waypoints/POIs).
"""

import argparse
import json
import pathlib
import sys

import ambit_format as F
from ambit_pcap import FlashImage, messages
from write_nav import (CMD_DATA_TAIL, CMD_DATA_WRITE, CMD_DEVICE_INFO, Link,
                        check_memory_map, read_flash, read_memory_map, send_plan)

CMD_GPS_ORBIT_HEAD = 0x0B15


def decode_orbit_head(head):
    """[u8 valid][u16 LE year][u8 month][u8 day][u32 LE seconds-since-midnight UTC] - the
    watch's own currently-stored orbit-data generation date, confirmed against real
    hardware read-back (a fresh write followed by re-querying 0x0b15 reported back exactly
    the source file's own Last-Modified date). See sgee_andre.md Part 3 for the full
    derivation. `9x 0x00` (no fields set) is the real "no orbit data yet" reply, not an
    error - returned here as valid=False like any other unparseable reply."""
    if len(head) < 9 or head[0] != 1:
        return {"valid": False}
    year = int.from_bytes(head[1:3], "little")
    month, day = head[3], head[4]
    seconds = int.from_bytes(head[5:9], "little")
    if not (1 <= month <= 12 and 1 <= day <= 31 and seconds < 86400):
        return {"valid": False}
    return {
        "valid": True,
        "date": f"{year:04d}-{month:02d}-{day:02d}",
        "time": f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}",
    }


def glonass_status(link):
    """The GLONASS region's own state, read from flash.

    Real, 2026-08-10: there is no GLONASS equivalent of the 0x0b15 orbit-status query -
    that command reports the GPS region only. But the generation date is carried inside the
    ephemeris file's own header, and the region stores `[u32 LE length][raw file]`, so the
    date sits at region offset 10..13 as big-endian year, month, day (confirmed against
    both source files and against real region content on a Kailash). That makes the same
    freshness question answerable without any new command.

    Returns {"supported": False} for a watch that declares no GlonassSGEE region at all
    (the whole Ambit3 family) - not an error, just a device without the feature."""
    found = read_memory_map(link)
    if "GlonassSGEE" not in found:
        return {"supported": False}
    base, size = found["GlonassSGEE"]
    head = read_flash(link, base, 16, "GlonassSGEE")
    if all(b == 0xFF for b in head):
        # Erased flash - never written. The normal state on any watch Suunto ships, since
        # its own Devices.xml never lists this model for the GLONASS download.
        return {"supported": True, "valid": False, "address": base, "region_size": size}
    length = int.from_bytes(head[:4], "little")
    year = int.from_bytes(head[10:12], "big")
    month, day = head[12], head[13]
    if not (2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
        return {"supported": True, "valid": False, "address": base, "region_size": size,
                "note": "region is not erased but its header does not decode as a date"}
    return {"supported": True, "valid": True, "address": base, "region_size": size,
            "bytes": length, "date": f"{year:04d}-{month:02d}-{day:02d}"}


def run_status(verbose, product_id=None):
    """Real, read-only device access (like exercise_log.py's own "read-only" mode) -
    dry_run=False so 0x0b15 actually reaches the watch, but nothing is ever written in this
    path; build_sgee()/send_plan() are never called here."""
    link = Link(dry_run=False, verbose=verbose, product_id=product_id)
    link.open()
    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    head = link.command(CMD_GPS_ORBIT_HEAD, b"")
    status = decode_orbit_head(head)
    # GLONASS rides along in the same status call so a caller (the desktop backend) learns
    # in one round trip both whether this watch supports it and whether it is stale.
    status["glonass"] = glonass_status(link)
    return status


def build_sgee(path):
    """GPS/GLONASS extended ephemeris (AGPS), for a faster fix outdoors. Confirmed
    byte-exact against a real capture, 2026-08-05 (assets/ambit3 pcap/orbitsync): the wire
    format is a 4-byte little-endian length prefix followed by the raw ephemeris bytes,
    written at F.SGEE_BASE in the usual 1024-byte CMD_DATA_WRITE chunks, closed by one
    CMD_DATA_TAIL whose hash covers only the written bytes (F.region_hash's HASH_WRITTEN
    mode, already implemented and already used by this exact function via emit_packs()) -
    no CMD_NAV_COMMIT afterward, unlike Routes/Waypoints (see send_plan).

    `path` is a raw ephemeris file with no wrapper of its own - what SuuntoLink itself
    caches on disk (confirmed identical, byte for byte, to
    "assets/WIndows apps/Suuntolink/sgee.7d" against the capture above), not something
    this project can generate: it comes from Suunto's/a u-blox AGPS provider's live service,
    and goes stale after roughly one to a few weeks (the reason SuuntoLink bothers with the
    separate 0x0b15 freshness check at all - see the head bytes this action prints before
    writing). Point this at whatever fresh file you have; this function does not fetch one.
    """
    return build_sgee_for_region(path, F.SGEE_BASE, F.SGEE_REGION_SIZE, "GpsSGEE")


# Real, 2026-08-10: the same length-prefixed blob, into whichever orbital region is being
# filled. GLONASS lives in its own separate region (`GlonassSGEE`) that only some watches
# declare - Kailash does, the Ambit3 family does not - so the caller resolves the base and
# size from the WATCH's own 0x0b21 reply and passes them in, rather than this function
# assuming either.
#
# Both source files come from the same service and share a byte-identical 12-byte header
# (magic `62 12 37 09`, version `7f 01`, big-endian year, month, day), so the framing is
# identical: `[u32 LE length][raw file]`. Confirmed against real bytes on both sides - the
# watch's own GpsSGEE region begins `28 1b 01 00` = 72488 followed verbatim by
# gpsorbit.bin, and the `kailashactivity` capture wrote 72020 bytes whose leading u32 is
# 72016 (= length + 4).
def build_sgee_for_region(path, base, region_size, label):
    data = pathlib.Path(path).read_bytes()
    blob = len(data).to_bytes(4, "little") + data
    # Hard bounds check BEFORE anything is planned, never a trusting write: this project
    # has already had one real out-of-bounds flash write from a computed offset that was
    # never checked against the declared region size.
    if len(blob) > region_size:
        raise SystemExit(
            f"{label}: {path} is {len(data)} bytes, which with its 4-byte length prefix "
            f"needs {len(blob)} - the watch declares only {region_size} bytes for this "
            "region. Refusing to write past the end of it.")
    flash = FlashImage()
    flash.write(base, blob)
    layout = [(f"{label} data", base, blob), ("tail", base, None)]
    return flash, layout


def compare_sgee_with_capture(link, capture):
    """Address-scoped rather than whole-capture: `orbitsync` is a full sync session
    (routes, POIs, logs and the SGEE write all mixed together), not an SGEE-only recording,
    so a message-count comparison over the whole capture does not apply - only messages
    whose own address falls inside the GpsSGEE region are pulled out and compared."""
    lo, hi = F.SGEE_BASE, F.SGEE_BASE + F.SGEE_REGION_SIZE

    def in_range(command, payload):
        if command not in (CMD_DATA_WRITE, CMD_DATA_TAIL) or len(payload) < 4:
            return False
        addr = int.from_bytes(payload[:4], "little")
        return lo <= addr < hi

    expected = [(m.command, m.payload) for m in messages(capture)
                if not m.incoming and in_range(m.command, m.payload)]
    produced = [(command, payload) for command, payload, _ in link.sent
                if in_range(command, payload)]
    if len(produced) != len(expected):
        print(f"\n  FAIL  {len(produced)} messages produced against "
              f"{len(expected)} in the capture's GpsSGEE range")
        return False
    ok = True
    for i, (got, want) in enumerate(zip(produced, expected)):
        if got[0] != want[0]:
            print(f"  FAIL  message {i}: 0x{got[0]:04x} against 0x{want[0]:04x}")
            ok = False
        elif got[1] != want[1]:
            ok = False
            differing = [k for k in range(min(len(got[1]), len(want[1])))
                         if got[1][k] != want[1][k]]
            only_extra = got[0] == CMD_DATA_TAIL and all(4 <= k < 8 for k in differing)
            print(f"  {'INFO ' if only_extra else 'FAIL '} message {i} "
                  f"0x{got[0]:04x}: bytes {differing[:8]}"
                  + ("  (word supplied by the application)" if only_extra else ""))
            if only_extra:
                ok = True
    print(f"\n  {'OK   ' if ok else 'FAIL '} {len(produced)} 0x0b16/0x0b18 payloads "
          f"compared to {capture}'s GpsSGEE range")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", nargs="?",
                     help="raw GPS/GLONASS ephemeris data, no wrapper of its own - not "
                          "needed with --status")
    ap.add_argument("--status", action="store_true",
                     help="just reads back the watch's current orbit-data date (0x0b15) "
                          "and exits - no file needed, nothing written")
    ap.add_argument("--json", action="store_true", help="machine-readable output for --status")
    ap.add_argument("--write", action="store_true",
                     help="actually emits; without this option nothing is sent")
    ap.add_argument("--compare", metavar="CAPTURE",
                     help="checks the simulated payloads against a capture")
    ap.add_argument("--verbose", action="store_true", help="logs every 64-byte report")
    ap.add_argument("--device", metavar="NAME",
                     help="same as write_nav.py's own --device - which watch to open when "
                          "more than one is connected (e.g. 'kailash'). Required in "
                          "practice for --glonass, since a Peak, Sport or Run has no such "
                          "region and both watches may be plugged in at once.")
    ap.add_argument("--glonass", action="store_true",
                     help="write the file to the watch's GlonassSGEE region instead of "
                          "GpsSGEE - only for a watch that declares one (Kailash, Traverse, "
                          "Traverse Alpha and Ambit3 Vertical do; Peak, Sport and Run do "
                          "not). Pair with glonassorbit/binary, not gpsorbit/binary.")
    args = ap.parse_args()

    from write_nav import resolve_product_id as _rpid
    product_id = _rpid(args.device) if args.device else None
    if args.status:
        result = run_status(args.verbose, product_id)
        if args.json:
            print(json.dumps(result))
        elif result["valid"]:
            print(f"watch's current orbit data: {result['date']} {result['time']} UTC")
        else:
            print("watch has no valid orbit data (0x0b15 came back empty/unparseable)")
        return 0 if result["valid"] else 1

    if not args.file:
        ap.error("file is required unless --status is given")

    link = Link(dry_run=not args.write, verbose=args.verbose, product_id=product_id)
    if args.write:
        print("!! REAL WRITE requested")
        link.open()
    else:
        print("dry-run mode: not a byte will be emitted")

    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    if not link.dry_run:
        head = link.command(CMD_GPS_ORBIT_HEAD, b"")
        print(f"  current orbit-data status (0x0b15, {len(head)} B): {head.hex()}"
              "  (structure decoded in sgee_andre.md)")
    found = read_memory_map(link)
    check_memory_map(found)

    # Which region, and - importantly - at the address THIS watch declares rather than a
    # constant. A watch that does not declare the region at all is refused outright: that
    # is the whole Ambit3-vs-Kailash difference, and guessing an address for a region the
    # firmware never mentioned is exactly how a write lands somewhere it should not.
    label = "GlonassSGEE" if args.glonass else "GpsSGEE"
    if label not in found:
        print(f"\n  this watch does not declare a {label} region in its own memory map "
              f"(it lists: {', '.join(sorted(found)) or 'nothing'}).")
        if args.glonass:
            print("  GLONASS orbital data has nowhere to go on this device - refusing.")
        return 1
    base, region_size = found[label]
    # Be honest about where these numbers came from: in dry-run read_memory_map() hands
    # back the REFERENCE table (F.REGIONS), not the watch - the giveaway is that it lists
    # TrainingProgram/Apps/CustomModes, which a Kailash reports as 0xffffffff. Only a real
    # run has actually asked the device.
    source = "as declared by this watch" if not link.dry_run \
        else "REFERENCE values - dry-run never queried the watch"
    print(f"  target region {label} at 0x{base:06x}, {region_size} bytes ({source})")

    flash, layout = build_sgee_for_region(args.file, base, region_size, label)
    send_plan(link, flash, layout, commit=False)

    total = sum(len(payload) for _, payload, _ in link.sent)
    reports = sum(len(r) for _, _, r in link.sent)
    print(f"\n{len(link.sent)} messages, {total} payload bytes, "
          f"{reports} reports of 64 bytes"
          + ("" if args.write else " — nothing was emitted"))
    if args.compare:
        return 0 if compare_sgee_with_capture(link, args.compare) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
