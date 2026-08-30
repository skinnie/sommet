#!/usr/bin/env python3
"""Rebuild the watch's whole navigation store from scratch: clear it, write the given routes,
and write EXACTLY the given POIs - deliberately NOT restoring the POIs already on the watch
(unlike write_nav.py's `reset`/`route`, which read the POI list first and put it back). This is
the recovery path for a POI region left with stale/duplicate records (2026-08-30): a nav write
erases the POI store as a side effect, so writing to an already-cleared store leaves no tail.

Order: build_reset (clears routes+waypoints and, as a side effect, the POI store) -> write the
routes -> write the fresh POI list. DRY-RUN by default; --write emits. --json prints a result.

    ./tools/rebuild_nav.py --route a.gpx --route b.gpx --pois-json p.json --write --json
"""
import argparse
import json
import pathlib
import sys

import ambit_format as F
from write_nav import (CMD_DEVICE_INFO, CMD_POI_WRITE, POI_ENTRY, SBEM_WRITE_PREFIX, Link,
                       build_poi_record, build_reset, build_routes, check_memory_map,
                       read_memory_map, read_pois, send_plan)


def poi_payload(pois):
    records = []
    for p in pois:
        t = p.get("type")
        t = F.WAYPOINT_TYPE_DEFAULT if t in (None, "") else int(t)
        records.append(build_poi_record(p["name"], float(p["lat"]), float(p["lon"]), type_=t))
    body = b"".join(records)
    if not body:
        return None
    header = (bytes([POI_ENTRY, len(body)]) if len(body) < 0xFF
              else bytes([POI_ENTRY, 0xFF]) + len(body).to_bytes(4, "little"))
    return SBEM_WRITE_PREFIX + F.SBEM_MAGIC + header + body


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--route", action="append", default=[], help="a route GPX (repeatable)")
    ap.add_argument("--pois-json", help="JSON file: [{name,lat,lon,type?}, ...]")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pois = json.load(open(args.pois_json)) if args.pois_json else []
    payload = poi_payload(pois)

    link = Link(dry_run=not args.write, verbose=not args.json)
    if args.write:
        link.open()
    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    before = read_pois(link) if args.write else b""
    before_n = (before.count(b"\x55") if before else 0)   # rough, for the log only
    check_memory_map(read_memory_map(link))

    # 1. Clear everything (this erases the POI store too - we intentionally never restore it).
    flash, layout = build_reset()
    send_plan(link, flash, layout)

    # 2. Write the routes, if any (build_routes rebuilds the whole Routes region from these).
    if args.route:
        flash, layout = build_routes([pathlib.Path(p) for p in args.route], None)
        send_plan(link, flash, layout)

    # 3. Write exactly the fresh POI list onto the now-empty POI store.
    if args.write and payload:
        link.command(CMD_POI_WRITE, payload)

    result = {"ok": True, "wrote": args.write, "routes": len(args.route), "pois": len(pois)}
    print(json.dumps(result) if args.json else result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
