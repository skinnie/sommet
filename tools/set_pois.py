#!/usr/bin/env python3
"""Replace the watch's whole standalone-POI list in ONE 0x0b25 write, from a JSON list on
stdin: [{"name","lat","lon","type"?}, ...]. This is the bulk counterpart to write_nav.py's
single `addpoi` - the two-watch sync must set many POIs at once, and calling `addpoi` in a
loop is wrong: each call is a full read-modify-write of the region, and doing that repeatedly
duplicated entries on real hardware (macOS, 2026-08-30). One write of the final list is both
correct and fast. DRY-RUN by default; --write emits. --json prints one result line.

The 0x0b25 payload REPLACES the region (build_poi_record + a single 0x55 SBEM entry, exactly
what addpoi builds for new+existing), so the list given here IS the resulting POI list - no
merge with what is already on the watch. Callers that want to preserve existing POIs must
include them in the list.

    echo '[{"name":"Home","lat":50.6,"lon":3.0}]' | ./tools/set_pois.py --write --json
"""
import argparse
import json
import sys

import ambit_format as F
from write_nav import (CMD_DEVICE_INFO, CMD_POI_WRITE, POI_ENTRY, SBEM_WRITE_PREFIX,
                       Link, build_poi_record)


MAX_ENTRY = 0xFE   # keep every 0x55 entry's length a single byte (< 0xFF, the extension flag)


def build_payload(pois):
    """POIs as combined 0x55 SBEM entries, each holding as many POI records as fit in a
    single-byte length (<= 254 bytes), matching SuuntoLink's own `poiimport` capture exactly:
    it packs multiple POIs into ONE 0x55 entry with a single-byte length (its 5-POI write is a
    249-byte entry) and NEVER uses the extended-length header (0x55 0xFF + u32). That extended
    form is the one the SBEM parser notes openambit never WRITES - it is emitted only by the
    watch on read - and it corrupted the region on real hardware (2026-08-30). So a list past
    254 bytes is split into several single-byte-length 0x55 entries, never the extended form."""
    payload = SBEM_WRITE_PREFIX + F.SBEM_MAGIC
    chunk = b""

    def flush():
        nonlocal chunk, payload
        if chunk:
            payload += bytes([POI_ENTRY, len(chunk)]) + chunk
            chunk = b""

    for p in pois:
        type_ = p.get("type")
        type_ = F.WAYPOINT_TYPE_DEFAULT if type_ in (None, "") else int(type_)
        rec = build_poi_record(p["name"], float(p["lat"]), float(p["lon"]), type_=type_)
        if chunk and len(chunk) + len(rec) > MAX_ENTRY:
            flush()
        chunk += rec
    flush()
    return payload


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--write", action="store_true", help="actually emit; else dry-run")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pois = json.load(sys.stdin)
    for p in pois:
        if not str(p.get("name", "")).strip():
            print(json.dumps({"ok": False, "error": "every POI needs a name"}))
            return 1
        lat, lon = float(p["lat"]), float(p["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            print(json.dumps({"ok": False, "error": "%s,%s not on Earth" % (lat, lon)}))
            return 1

    payload = build_payload(pois)
    link = Link(dry_run=not args.write, verbose=not args.json)
    if args.write:
        link.open()
    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    if args.write:
        link.command(CMD_POI_WRITE, payload)
    print(json.dumps({"ok": True, "wrote": args.write, "count": len(pois),
                      "payloadBytes": len(payload)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
