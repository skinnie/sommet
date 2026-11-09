#!/usr/bin/env python3
"""Dumps the Suunto Kailash's `EventLog` flash region and tries decoding it as a flat array
of Ambit3-shaped `WaypointDescriptor` records (the same 52-byte lat/lon/name/route_name/tail
struct this project's own POI and Waypoint tooling already uses) - a real hypothesis, not
confirmed against real bytes yet.

**Why this exists**: `kailash`'s own real `0x0b21` memory-map reply (`assets/ambit3 pcap/v2/
kailash`, decoded 2026-08-08, see `custom_modes_andre.md`'s "Kailash" section) reports a real,
present `EventLog` region (`0x0c3500`, 400,000 bytes) with no Ambit3 equivalent at all - not
Waypoints, not Routes, not ExerciseLog. André's own real-world context: Kailash/Hoopoe belongs
to the same Traverse-generation watch line that started out purely as a places-you've-visited
logger (no export mechanism of its own, no sync) before later watches in that line added real
activity recording - "event" here plausibly means exactly that kind of place/moment marker,
not a structured sport-mode setting the way `EXERCISE_MODES_APP_META`'s events are. Nothing
here has confirmed the record shape yet, though: `WaypointDescriptor` is this project's own
closest real analogue (a real, hardware-verified lat/lon/name struct already used for the
watch's Waypoints/POI region - and notably, Kailash's own memory map places its *Waypoints*
region at the exact same address as Ambit3's, `0x005000`, real evidence the two devices share
at least some struct layouts), not a confirmed EventLog format of its own.

**What this script actually does**: reads the real region byte-for-byte (`0x0b17`, same
generic flash-read mechanism every other region in this project already uses) and walks it as
a flat array of 52-byte records from offset 0 (no assumed header - EventLog's own header shape,
if it has one, is unconfirmed; this is the simplest hypothesis, not the only possible one),
keeping only records whose `lat`/`lon` fall in a real valid range (-900000000..900000000 /
-1800000000..1800000000, i.e. degrees*1e7) and whose decoded name is mostly printable - both
checks needed because `decode_name()` never raises (UTF-8 errors just become the U+FFFD
replacement character), so a genuine format mismatch would otherwise silently produce
plausible-looking-but-fake entries instead of an honest failure. Writes one real GPX `<wpt>`
per record that passes both checks, the same `lat/1e7, lon/1e7` convention every other real
POI/waypoint tool in this project already uses (`decode_route.py`, `build_route.py`).

**Not yet run against a real Kailash** - no such device was connected while this was written.
If the plausibility rate is low or zero, that's real information the format guess above is
wrong, not proof this device has no such data.

    ./tools/kailash_eventlog.py --gpx-out /tmp/kailash_events.gpx
    ./tools/kailash_eventlog.py --from /tmp/eventlog_dump.bin --gpx-out /tmp/kailash_events.gpx
"""

import argparse
import struct
import sys

from ambit_format import WAYPOINT_DESCRIPTOR, decode_name

# From the real kailash capture's own 0x0b21 memory-map reply, parsed directly (see
# custom_modes_andre.md's "Kailash" section for the full region table) - not guessed.
EVENTLOG_BASE = 0x0C3500
EVENTLOG_SIZE = 400000

RECORD_SIZE = WAYPOINT_DESCRIPTOR.size  # 52


def plausible_name(name):
    if not name:
        return False
    printable = sum(1 for c in name if c.isprintable() and c != "�")
    return printable / len(name) >= 0.8


def walk_candidates(data):
    """Every 52-byte slot in `data` whose lat/lon/name look like a real record - not every
    slot will be real even among these (this is a plausibility filter, not a validator), but
    a slot that fails these checks is definitely not one, which is the useful half."""
    for offset in range(0, len(data) - RECORD_SIZE + 1, RECORD_SIZE):
        blob = data[offset:offset + RECORD_SIZE]
        try:
            lat, lon, name_raw, route_name_raw, tail = WAYPOINT_DESCRIPTOR.unpack(blob)
        except struct.error:
            continue
        if not (-900000000 <= lat <= 900000000 and -1800000000 <= lon <= 1800000000):
            continue
        if lat == 0 and lon == 0:
            continue  # a real (0,0) coordinate is vanishingly unlikely here; almost
                      # certainly an unused/empty slot, not the real Gulf-of-Guinea
        name = decode_name(name_raw)
        if not plausible_name(name):
            continue
        yield offset, lat, lon, name, decode_name(route_name_raw)


def to_gpx(entries):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="Sommet kailash_eventlog.py (unconfirmed format)">']
    for offset, lat, lon, name, extra in entries:
        esc_name = (name or f"event@0x{offset:x}").replace("&", "&amp;").replace("<", "&lt;")
        lines.append(f'  <wpt lat="{lat/1e7:.7f}" lon="{lon/1e7:.7f}"><name>{esc_name}</name>'
                     f'</wpt>')
    lines.append("</gpx>")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="from_file", metavar="FILE",
                     help="decode a raw EventLog dump instead of the watch")
    ap.add_argument("--save", metavar="FILE",
                     help="also save the raw region bytes here (live read only)")
    ap.add_argument("--gpx-out", metavar="FILE",
                     help="write every plausible record as one GPX file")
    args = ap.parse_args()

    if args.from_file:
        with open(args.from_file, "rb") as f:
            data = f.read()
    else:
        from write_nav import Link, read_flash
        link = Link(dry_run=False, verbose=False)
        print("read-only: 0x0b17 reads flash, nothing is written")
        link.open()
        data = read_flash(link, EVENTLOG_BASE, EVENTLOG_SIZE, label="EventLog")
        if args.save:
            with open(args.save, "wb") as f:
                f.write(data)
            print(f"\nsaved raw dump to {args.save}")

    total_slots = len(data) // RECORD_SIZE
    entries = list(walk_candidates(data))
    rate = len(entries) / total_slots if total_slots else 0
    print(f"{len(entries)}/{total_slots} slot(s) look like plausible records "
          f"({rate:.1%})")
    if rate < 0.01:
        print("  WARNING: that's a very low hit rate - the flat-WaypointDescriptor-array "
              "hypothesis is probably wrong for this region, not just mostly-empty. "
              "Real information either way, not a tool bug: EventLog needs its own format "
              "decode.")

    for offset, lat, lon, name, route_name in entries[:20]:
        print(f"  0x{offset:06x}  {lat/1e7:.7f}, {lon/1e7:.7f}  {name!r}"
              + (f"  route_name={route_name!r}" if route_name else ""))
    if len(entries) > 20:
        print(f"  ... and {len(entries) - 20} more")

    if args.gpx_out and entries:
        with open(args.gpx_out, "w") as f:
            f.write(to_gpx(entries))
        print(f"\nwrote {args.gpx_out} ({len(entries)} waypoint(s))")
    elif args.gpx_out:
        print("\nno plausible entries - not writing an empty GPX file")

    return 0


if __name__ == "__main__":
    sys.exit(main())
