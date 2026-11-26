#!/usr/bin/env python3
"""The saved location and the Find-back home of a Suunto Kailash. Read-only.

The Kailash keeps one POI list (up to 14 records) in storage no flash read can reach. Its own
"Save location" (7R button) leaves one "Bookmark" (type 6, subtype 0) there, replacing the last
one; setting home leaves "Home" (type 0, subtype 1). "Find back" offers those two. From the
Kailash 2.0.5 program (static reading and the code run on the PC's MSP430X core, 2026-11-26,
sommet-builds/kailash-poi-re/FINDINGS.md); not yet seen answered by a real Kailash.

How it is read, the same way as an Ambit3's POIs:
    0x0b02  ->  u32 count
    0x0b24  [u16 cursor][u16 0]  ->  [u32 next cursor][u16 last] "SBEM0102" entries
            (later pages without "SBEM0102"); one entry per record, newest first, with the
            WayPoint group id of the watch's own descriptor (0x46 on 2.0.5), in the Ambit3 POI
            layout: name, route name, "YYYY-MM-DDThh:mm:ss" (the watch's local time), five u8
            (route index, type, subtype, type index, flags), i32 latitude and longitude x 1e7.
An empty list answers 0x0b24 with an error, so 0x0b24 is only sent when the count isn't 0.

Never sent, by design (the firmware shows why): 0x0b04 (empties this list, Find back loses home
and bookmark), 0x0b25 / 0x0b16 / 0x0b18 (writes), the 0x1200 query 0xa5 (broken on this
firmware: repeats a scratch record and never ends), and 0x0b17 at 0x027100.

Coordinates are printed only with --show-coordinates (for checking on this computer).

    ./tools/kailash_pois.py [--json] [--show-coordinates]
"""

import argparse
import json
import struct
import sys

import ambit_format as F
import sbem_schema

CMD_POI_COUNT = 0x0B02
CMD_POI_READ = 0x0B24
WAYPOINT_GROUP_2_0_5 = 0x46
TYPE_HOME, TYPE_BOOKMARK = 0, 6
SAFE_COMMANDS = frozenset({CMD_POI_COUNT, CMD_POI_READ})


def waypoint_group(schema):
    """The id of the watch's own sml.WayPoint record group, or None."""
    fid = next((f.fid for f in schema.fields.values() if f.path.endswith("WayPoint.Name")), None)
    if fid is None:
        return None
    return next((g for g, members in schema.groups.items() if fid in members), None)


def read_raw(link):
    """The POI count, and every page of the list as one payload in the first page's shape
    ([u32 0][u16 1] "SBEM0102" entries), as ambit_format.parse_sbem_poi_list takes it."""
    # The count: 4 bytes as on the Ambit3 (write_nav.read_poi_count); the emulated Kailash handler
    # (0x146da) was printed as a u16, so 2 bytes are taken too.
    raw = link.command(CMD_POI_COUNT, b"")
    if len(raw) >= 4:
        count = int.from_bytes(raw[:4], "little")
    elif len(raw) >= 2:
        count = int.from_bytes(raw[:2], "little")
    else:
        raise ValueError(f"short reply to the POI count ({len(raw)} bytes)")
    if count == 0:
        return 0, struct.pack("<IH", 0, 1) + sbem_schema.MAGIC
    body, cursor = b"", 0
    for n in range(16):
        reply = link.command(CMD_POI_READ, struct.pack("<HH", cursor, 0))
        if len(reply) < 6:
            raise ValueError(f"short reply to the POI read ({len(reply)} bytes)")
        cursor, last = struct.unpack_from("<IH", reply, 0)
        page = reply[6:]
        if page[:8] == sbem_schema.MAGIC:
            page = page[8:]
        elif n == 0:
            raise ValueError("no SBEM0102 in the first page of the POI read")
        body += page
        if cursor == 0 or last:
            return count, struct.pack("<IH", 0, 1) + sbem_schema.MAGIC + body
    raise ValueError("the POI read did not finish within 16 pages")


def decode(payload, group_id, show_coordinates=False):
    """{"records", "saved", "home"} - newest first, as the watch keeps them. Entries of another
    id are refused rather than parsed as POIs."""
    entries = list(sbem_schema.entries(payload[6:]))
    foreign = sorted({eid for eid, _d in entries if eid != group_id})
    if foreign:
        raise ValueError("unexpected entries in the POI list: "
                         + ", ".join(f"0x{e:x}" for e in foreign))
    records = []
    for p in F.parse_sbem_poi_list(payload):
        rec = {"name": p["name"], "type": p["type"], "subtype": p["sub_type"],
               "type_index": p["type_index"], "flags": p["flags"], "time": p["stamp"]}
        if show_coordinates:
            rec["lat"], rec["lon"] = p["lat"] / 1e7, p["lon"] / 1e7
        records.append(rec)
    saved = next((r for r in records if r["type"] == TYPE_BOOKMARK), None)
    home = next((r for r in records if r["type"] == TYPE_HOME), None)
    return {"records": records, "saved": saved, "home": home}


def read(link, schema=None, show_coordinates=False):
    group = waypoint_group(schema) if schema is not None else None
    count, payload = read_raw(link)
    out = decode(payload, group or WAYPOINT_GROUP_2_0_5, show_coordinates)
    return {"ok": True, "count": count, **out}


class _Guard:
    """Refuses, before it reaches the watch, any command this tool must never send."""

    def __init__(self, link):
        self.link = link

    def command(self, cmd, payload=b"", *a, **k):
        if cmd not in SAFE_COMMANDS:
            raise RuntimeError(f"kailash_pois never sends 0x{cmd:04x}")
        return self.link.command(cmd, payload, *a, **k)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show-coordinates", action="store_true",
                    help="print latitude/longitude (for checking on this computer only)")
    args = ap.parse_args()
    from write_nav import Link
    link = Link(dry_run=False)
    try:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            link.open()
            try:
                schema = sbem_schema.load(sbem_schema.for_link(link))
            except Exception:  # noqa: BLE001 - the 2.0.5 id then
                schema = None
        result = read(_Guard(link), schema, args.show_coordinates)
    except (ValueError, RuntimeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}) if args.json else f"error: {exc}")
        return 1
    finally:
        try:
            link.close()
        except Exception:  # noqa: BLE001
            pass
    if args.json:
        print(json.dumps(result))
        return 0
    print(f"{result['count']} record(s) on the watch")
    for label, rec in (("Saved location", result["saved"]), ("Home (Find back)", result["home"])):
        if rec is None:
            print(f"  {label}: none")
        else:
            where = f"  {rec['lat']:.5f}, {rec['lon']:.5f}" if "lat" in rec else ""
            print(f"  {label}: {rec['name']!r} saved {rec['time']}{where}")
    others = [r for r in result["records"] if r["type"] not in (TYPE_HOME, TYPE_BOOKMARK)]
    for r in others:
        print(f"  other (not shown by the watch): {r['name']!r} type {r['type']} {r['time']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
