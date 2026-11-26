#!/usr/bin/env python3
"""Daily steps of a Suunto Kailash, read from the watch's own step timeline. Read-only.

The Kailash keeps its steps as `sml.TimelinePart` samples, not in DeviceHistory as the Ambit3
does (tools/ambit_steps.py). They come back from the same `0x1200` object query:

    request  [u32 cursor 0][u16 1][u16 10] "SBEM0102" [u16 query id]
    reply    [u32 cursor][u16 last] "SBEM0102" entries        (first page)
             [u32 cursor][u16 last] entries                   (later pages, no magic)

Each entry is the sample group (0x8e on firmware 2.0.5), holding samples of
[NUL-terminated ISO time text][u32 StepCount]; 0xFFFFFFFF = no count.

What the samples are, from the Kailash 2.0.5 watch program (static reading and the firmware
run on the PC's MSP430X core, 2026-11-26; sommet-builds/kailash-steps-re/FINDINGS.md). Not yet
seen on a real Kailash:
  - 31 samples: 30 daily totals from a ring the watch keeps, then today's count so far. A day's
    total is added at midnight; days without data are 0xFFFFFFFF.
  - The last sample is today's count with the watch's current local time: always right.
  - The 30 history samples come in the ring's storage order, and the watch dates them as if the
    ring started at slot 0. That is right on 1 day in 30 only, so their dates are NOT used here:
    they are returned raw (`history_raw`), for a comparison of two reads on different days to
    place them (the slots that changed between them end at yesterday).
  - Reading changes nothing on the watch (emulated: no storage write, a second read identical).
    This tool never writes: no 0x1101, no 0x1201 (NextTime 0x8f does nothing on the Kailash;
    the sample writer would change the ring).

    ./tools/kailash_steps.py            # USB
    ./tools/kailash_steps.py --ble      # Bluetooth
    ./tools/kailash_steps.py --json     # one JSON object
"""

import argparse
import json
import struct
import sys

import sbem_schema

CMD_SML_QUERY = 0x1200
NO_COUNT = 0xFFFFFFFF
HISTORY_DAYS = 30

# Kailash 2.0.5 (its own descriptor), used only when no descriptor is at hand.
REFERENCE_IDS = {"query": 0x91, "samples": 0x8E}
NAMES = {"time": "TimelinePart.Samples+Sample.TimeISO8601",
         "steps": "TimelinePart.Samples.Sample.Events.StepCount.StepCount"}


def ids_from_schema(schema):
    """{"query", "samples"} of this watch's TimelinePart step samples, by name, or None when the
    watch has no StepCount samples (the Ambit3's TimelinePart holds other events)."""
    query = next((fid for fid, p in schema.queries.items() if p == "sml.TimelinePart"), None)
    time_fid = next((f.fid for f in schema.fields.values() if f.path.endswith(NAMES["time"])), None)
    steps_fid = next((f.fid for f in schema.fields.values() if f.path.endswith(NAMES["steps"])), None)
    if None in (query, time_fid, steps_fid):
        return None
    group = next((g for g, members in schema.groups.items() if time_fid in members), None)
    if group is None or steps_fid not in schema.groups[group]:
        return None
    return {"query": query, "samples": group}


def request(query_id, cursor=0):
    return struct.pack("<IHH", cursor, 1, 10) + sbem_schema.MAGIC + struct.pack("<H", query_id)


def page_entries(reply, first):
    """(entry id, data) of one reply page. Only the first page carries "SBEM0102"."""
    if len(reply) < 6:
        raise ValueError(f"short reply to the step query ({len(reply)} bytes)")
    body = reply[6:]
    if first or body[:8] == sbem_schema.MAGIC:
        head = reply.find(sbem_schema.MAGIC)
        if head < 0:
            raise ValueError("no SBEM0102 in the first page of the step query")
        body = reply[head:]
    else:
        body = sbem_schema.MAGIC + body
    return list(sbem_schema.entries(body))


def read_raw(link, query_id):
    """Every page of the query: a list of (entry id, data). The last page has cursor 0."""
    out, cursor = [], 0
    for n in range(64):
        reply = link.command(CMD_SML_QUERY, request(query_id, cursor), quiet=True)
        out += page_entries(reply, n == 0)
        cursor, = struct.unpack_from("<I", reply, 0)
        if cursor == 0:
            return out
    raise ValueError("the step query did not finish within 64 pages")


def samples(entries, group_id):
    """[(time text, steps or None)] from the sample group entries, in reply order."""
    out = []
    for eid, data in entries:
        if eid != group_id:
            continue
        off = 0
        while off < len(data):
            end = data.find(b"\0", off)
            if end < 0 or end + 5 > len(data):
                raise ValueError("a step sample is cut short")
            text = data[off:end].decode("ascii", "replace")
            n, = struct.unpack_from("<I", data, end + 1)
            out.append((text, None if n == NO_COUNT else n))
            off = end + 5
    return out


def decode(entries, group_id):
    """{"today": {"time", "date", "steps"}, "history_raw": [30 counts or None]}. Refuses a
    reply that isn't 30 history samples and one for today."""
    found = samples(entries, group_id)
    if len(found) != HISTORY_DAYS + 1:
        raise ValueError(f"{len(found)} step samples, {HISTORY_DAYS + 1} expected "
                         "(30 days and today) - not the layout this tool knows")
    when, steps = found[-1]
    if len(when) < 10 or when[4] != "-" or when[7] != "-":
        raise ValueError(f"unexpected time in today's sample {when!r}")
    return {"today": {"time": when, "date": when[:10], "steps": steps},
            "history_raw": [n for _t, n in found[:-1]],
            "history_labels": [t for t, _n in found[:-1]]}


def _own_schema(link):
    if getattr(link, "dry_run", False):
        return None
    try:
        return sbem_schema.for_link(link)
    except Exception:  # noqa: BLE001 - hid backends and short replies raise their own types
        return None


def read_steps(link):
    ids, source = dict(REFERENCE_IDS), "fw 2.0.5 table"
    path = _own_schema(link)
    if path is not None and path.exists():
        found = ids_from_schema(sbem_schema.load(path))
        if found is None:
            raise ValueError("this watch keeps no step samples in its timeline")
        ids, source = found, "its own descriptor"
    result = decode(read_raw(link, ids["query"]), ids["samples"])
    return {"ok": True, "ids_from": source, "query": ids["query"], **result}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ble", action="store_true", help="read over Bluetooth instead of USB")
    ap.add_argument("--address", help="Bluetooth address (with --ble); scans when omitted")
    ap.add_argument("--json", action="store_true", help="print one JSON object")
    args = ap.parse_args()
    if args.ble:
        from ble_link import BleLink
        link = BleLink(dry_run=False, address=args.address)
    else:
        from write_nav import Link
        link = Link(dry_run=False)
    try:
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            link.open()
            result = read_steps(link)
    except (ValueError, RuntimeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}) if args.json else f"error: {exc}")
        return 1
    finally:
        if hasattr(link, "close"):
            try:
                link.close()
            except Exception:  # noqa: BLE001 - closing is best effort
                pass
    if args.json:
        print(json.dumps(result))
        return 0
    t = result["today"]
    print(f"today {t['date']} (watch time {t['time']}): "
          f"{t['steps'] if t['steps'] is not None else 'no count'} steps")
    print("30 stored days, in the watch's storage order (dates not known from one read):")
    print("  " + " ".join("-" if n is None else str(n) for n in result["history_raw"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
