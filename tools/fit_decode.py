#!/usr/bin/env python3
"""Decode a Garmin/ANT FIT activity file to a ride summary + GPX track, standard library only.

    ./tools/fit_decode.py ride.fit                 # summary JSON on stdout
    ./tools/fit_decode.py ride.fit --gpx out.gpx   # also write a GPX track

Written for the rough Edge/Karoo direct-import (2026-09-04): those devices record .fit, but the
app had no FIT *decoder* (it only encodes FIT for export, and the desktop backend is stdlib-only,
so no python-fitparse). This is a deliberately small decoder - just enough of the FIT binary
format to pull what a library card + map need: the session summary and the GPS track. It is not
a general FIT library.

FIT layout (see the public FIT SDK): a 12/14-byte header, then a stream of records. Each record
begins with a header byte: definition messages describe the fields of a "local message type";
data messages carry those fields. We keep the definitions we see and read the two messages we
care about - session (global num 18) and record (global num 20). Compressed-timestamp data
headers (bit7 set) are handled against a running timestamp.
"""

import argparse
import datetime
import json
import struct
import sys

FIT_EPOCH = 631065600            # FIT timestamps are seconds since 1989-12-31 00:00:00 UTC
SEMI = 180.0 / (2 ** 31)         # semicircles -> degrees

# Base type number (low nibble of the base-type byte) -> (struct code, size, is-invalid check).
# Only the widths we actually read are here; unknown types fall back to raw bytes by size.
_BASE = {
    0x00: ("B", 1), 0x01: ("b", 1), 0x02: ("B", 1), 0x83: ("h", 2), 0x84: ("H", 2),
    0x85: ("i", 4), 0x86: ("I", 4), 0x07: ("s", 1), 0x88: ("f", 4), 0x89: ("d", 8),
    0x0a: ("B", 1), 0x8b: ("H", 2), 0x8c: ("I", 4), 0x0d: ("B", 1), 0x8e: ("q", 8),
    0x8f: ("Q", 8), 0x90: ("Q", 8),
}
# The FIT "invalid" sentinel per base type - signed types use 0x7F.. (not 0xFF..), which is why
# an unset sint32 lat/long is 0x7FFFFFFF and must be dropped rather than shown as ~179.99999 deg.
_INVALID = {
    0x00: 0xFF, 0x01: 0x7F, 0x02: 0xFF, 0x83: 0x7FFF, 0x84: 0xFFFF,
    0x85: 0x7FFFFFFF, 0x86: 0xFFFFFFFF, 0x0a: 0x00, 0x8b: 0x0000, 0x8c: 0x00000000,
    0x0d: 0xFF, 0x8e: 0x7FFFFFFFFFFFFFFF, 0x8f: 0xFFFFFFFFFFFFFFFF, 0x90: 0x0000000000000000,
}

SPORT = {0: "Activity", 1: "Running", 2: "Cycling", 5: "Swimming", 11: "Walking",
         12: "Cross Country Skiing", 13: "Alpine Skiing", 15: "Rowing", 17: "Hiking",
         18: "Multisport", 25: "Indoor Cycling"}


class _Reader:
    def __init__(self, data):
        self.d = data
        self.i = 0

    def u8(self):
        v = self.d[self.i]
        self.i += 1
        return v

    def take(self, n):
        b = self.d[self.i:self.i + n]
        self.i += n
        return b


def _field_value(raw, base_type, arch):
    """One field's raw bytes -> a Python number (or None if the FIT 'invalid' sentinel), for the
    scalar fields we read. Multi-value/array fields are reduced to their first element."""
    code, size = _BASE.get(base_type, (None, len(raw) or 1))
    if code in (None, "s"):
        return None
    order = "<" if arch == 0 else ">"
    # Field may be an array (size = k * elem); read the first element only.
    if len(raw) < size:
        return None
    val = struct.unpack(order + code, raw[:size])[0]
    if base_type in _INVALID and val == _INVALID[base_type]:
        return None
    return val


def decode(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 14 or data[8:12] != b".FIT":
        raise ValueError("not a FIT file (missing .FIT signature)")
    header_size = data[0]
    data_size = struct.unpack("<I", data[4:8])[0]
    body = data[header_size:header_size + data_size]
    r = _Reader(body)

    defs = {}                    # local msg type -> {"global","arch","fields":[(num,size,base)]}
    records = []                 # [{"t","lat","lon","ele"}]
    session = {}
    last_ts = None

    while r.i < len(body):
        try:
            hdr = r.u8()
        except IndexError:
            break
        if hdr & 0x80:           # compressed-timestamp data header
            local = (hdr >> 5) & 0x03
            offset = hdr & 0x1F
            if last_ts is not None:
                ts = (last_ts & ~0x1F) | offset
                if offset < (last_ts & 0x1F):
                    ts += 0x20
                last_ts = ts
            definition = defs.get(local)
            _read_data(r, definition, arch_for(definition), records, session, last_ts)
        elif hdr & 0x40:         # definition message
            local = hdr & 0x0F
            r.u8()               # reserved
            arch = r.u8()
            order = "<" if arch == 0 else ">"
            gnum = struct.unpack(order + "H", r.take(2))[0]
            nfields = r.u8()
            fields = []
            for _ in range(nfields):
                fnum = r.u8(); size = r.u8(); base = r.u8()
                fields.append((fnum, size, base))
            dev_bytes = 0
            if hdr & 0x20:       # developer fields: each is (field_num, size, dev_data_index).
                ndev = r.u8()    # we skip their VALUES in data messages, so sum their sizes.
                for _ in range(ndev):
                    _fn = r.u8(); dsize = r.u8(); _di = r.u8()
                    dev_bytes += dsize
            defs[local] = {"global": gnum, "arch": arch, "fields": fields,
                           "dev_bytes": dev_bytes}
        else:                    # normal data message
            local = hdr & 0x0F
            definition = defs.get(local)
            ts = _read_data(r, definition, arch_for(definition), records, session, last_ts)
            if ts is not None:
                last_ts = ts

    return _summarize(session, records, path)


def arch_for(definition):
    return definition["arch"] if definition else 0


def _read_data(r, definition, arch, records, session, comp_ts):
    """Consume one data message's bytes. Fills records/session for the two globals we track.
    Returns this message's timestamp (field 253) if present, for compressed-timestamp tracking."""
    if not definition:
        return None
    vals = {}
    for fnum, size, base in definition["fields"]:
        raw = r.take(size)
        vals[fnum] = _field_value(raw, base, arch)
    # A message defined with developer fields carries their VALUES here too; we don't use them,
    # but we MUST consume their bytes or every later message desyncs (this was reading garbage
    # session totals - 42949 km, negative durations - on Karoo/Edge files that use dev fields).
    dev_bytes = definition.get("dev_bytes", 0)
    if dev_bytes:
        r.take(dev_bytes)
    g = definition["global"]
    ts = vals.get(253)
    if g == 20:                  # record
        lat, lon = vals.get(0), vals.get(1)
        pt = {"t": comp_ts if ts is None else ts}
        if lat is not None and lon is not None:
            pt["lat"] = lat * SEMI
            pt["lon"] = lon * SEMI
        ele = vals.get(78)       # enhanced_altitude (uint32, scale5 offset500)
        if ele is not None:
            pt["ele"] = ele / 5.0 - 500.0
        elif vals.get(2) is not None:  # altitude (uint16, scale5 offset500)
            pt["ele"] = vals[2] / 5.0 - 500.0
        if "lat" in pt:
            records.append(pt)
    elif g == 18:                # session (summary)
        if vals.get(5) is not None:
            session["sport"] = vals[5]
        if vals.get(2) is not None:
            session["start_time"] = vals[2]
        if vals.get(7) is not None:
            session["elapsed_s"] = vals[7] / 1000.0        # scale 1000
        if vals.get(9) is not None:
            session["distance_m"] = vals[9] / 100.0        # scale 100
        if vals.get(11) is not None:
            session["calories"] = vals[11]
        if vals.get(22) is not None:
            session["ascent_m"] = vals[22]
    return ts


def _iso(fit_seconds):
    if fit_seconds is None:
        return None
    dt = datetime.datetime.fromtimestamp(fit_seconds + FIT_EPOCH, datetime.timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _summarize(session, records, path):
    start = session.get("start_time")
    if start is None and records:
        start = records[0]["t"]
    sport = session.get("sport")
    return {
        "ok": True,
        "sport": SPORT.get(sport, "Activity"),
        "sportCode": sport,
        "startTime": _iso(start),
        "durationSeconds": int(session.get("elapsed_s") or 0),
        "distanceMeters": float(session.get("distance_m") or 0.0),
        "ascentMeters": float(session.get("ascent_m") or 0.0),
        "energyKcal": int(session.get("calories") or 0),
        "trackPoints": len([p for p in records if "lat" in p]),
    }


def to_gpx(path, name=None):
    summary = decode(path)
    with open(path, "rb") as fh:
        pass
    # decode() re-read for the summary; re-run to get the point list without changing its return
    # shape (a second parse of a ride file is cheap and keeps decode()'s contract simple).
    pts = _points(path)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="ambit-app fit_decode.py" '
             'xmlns="http://www.topografix.com/GPX/1/1">', '<trk>',
             f'<name>{name or summary["sport"]}</name>', '<trkseg>']
    for p in pts:
        if "lat" not in p:
            continue
        t = _iso(p["t"]) if p.get("t") is not None else None
        ele = f'<ele>{p["ele"]:.1f}</ele>' if "ele" in p else ""
        tt = f'<time>{t}</time>' if t else ""
        lines.append(f'<trkpt lat="{p["lat"]:.7f}" lon="{p["lon"]:.7f}">{ele}{tt}</trkpt>')
    lines += ['</trkseg>', '</trk>', '</gpx>']
    return "\n".join(lines), summary


def _points(path):
    # Lightweight re-parse that returns the record points (decode() returns only a summary).
    with open(path, "rb") as fh:
        data = fh.read()
    header_size = data[0]
    data_size = struct.unpack("<I", data[4:8])[0]
    r = _Reader(data[header_size:header_size + data_size])
    defs, records, session, last_ts = {}, [], {}, None
    body_len = len(r.d)
    while r.i < body_len:
        try:
            hdr = r.u8()
        except IndexError:
            break
        if hdr & 0x80:
            local = (hdr >> 5) & 0x03; offset = hdr & 0x1F
            if last_ts is not None:
                ts = (last_ts & ~0x1F) | offset
                if offset < (last_ts & 0x1F):
                    ts += 0x20
                last_ts = ts
            _read_data(r, defs.get(local), arch_for(defs.get(local)), records, session, last_ts)
        elif hdr & 0x40:
            local = hdr & 0x0F; r.u8(); arch = r.u8()
            order = "<" if arch == 0 else ">"
            gnum = struct.unpack(order + "H", r.take(2))[0]
            nfields = r.u8()
            fields = [(r.u8(), r.u8(), r.u8()) for _ in range(nfields)]
            dev_bytes = 0
            if hdr & 0x20:
                for _ in range(r.u8()):
                    _fn = r.u8(); dsize = r.u8(); _di = r.u8()
                    dev_bytes += dsize
            defs[local] = {"global": gnum, "arch": arch, "fields": fields,
                           "dev_bytes": dev_bytes}
        else:
            local = hdr & 0x0F
            ts = _read_data(r, defs.get(local), arch_for(defs.get(local)), records, session, last_ts)
            if ts is not None:
                last_ts = ts
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("fit", help="path to a .fit file")
    ap.add_argument("--gpx", metavar="OUT", help="also write a GPX track to OUT")
    ap.add_argument("--name", help="track name for the GPX")
    args = ap.parse_args()
    if args.gpx:
        gpx, summary = to_gpx(args.fit, name=args.name)
        with open(args.gpx, "w") as fh:
            fh.write(gpx)
        print(json.dumps(summary))
    else:
        print(json.dumps(decode(args.fit)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
