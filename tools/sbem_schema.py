#!/usr/bin/env python3
"""SBEM schema dictionary of an Ambit3, as SuuntoLink downloads it.

The `descr+SERIAL+FW` file in SuuntoLink's data folder is an SBEM0102 that names and
types every SML field of the firmware. Its entry identifiers are the ones that travel
in the protocol's SBEM payloads: the same integer is both the field number in the
descriptor and the entry identifier on the wire. Verified on the captures: 0x0b21
carries only 0x4a (BinaryDataArea), 0x0b25 only 0x55 (WayPoint), 0x1100 the 66
DeviceSettings groups, 0x1200 entries 0x59/0x5a/0x8a of the DeviceLogBook.

    ./tools/sbem_schema.py                          # dump of the schema
    ./tools/sbem_schema.py --capture CAPTURE        # decodes the SBEM payloads
    ./tools/sbem_schema.py --group 85               # detail of one group

Three entry forms:
    <FRM> + <PTH>   terminal field, sometimes with a <MOD> conversion
    <GRP>a,b,c      record: the ordered list of its fields
    <QRY> + <PTH>   queryable object, whose identifier serves as the request
"""

import argparse
import os
import pathlib
import re
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets"
REFERENCE_FW = "2.4.17"  # the reference watch, see tools/README.md

MAGIC = b"SBEM0102"


def suuntolink_dirs():
    """Where SuuntoLink itself keeps its `descr+SERIAL+FW` files, per platform.

    These are generated per watch AND per firmware, so they can never be shipped with
    the app - they only exist on a machine where SuuntoLink has actually talked to that
    watch. Packaging assets/ alone is therefore not a fix: in the frozen download
    assets/ holds only sportmode_rows.json, which left POIs and Settings undecodable
    (502s) even though the user's own descriptor was sitting in SuuntoLink's folder."""
    home = pathlib.Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "Suuntolink"]
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
        return [pathlib.Path(base) / "Suuntolink"]
    return [home / ".local" / "share" / "Suuntolink",
            home / ".config" / "Suuntolink",
            home / ".Suuntolink"]


def default_descriptor(serial=None, fw=None):
    """The `descr+SERIAL+FW` of SuuntoLink's data folder, found by globbing rather
    than named: the file name carries the watch serial, which is personal data and
    is not written down in this repository.

    Searches SuuntoLink's own data folder first, then the repo's assets/ (which is all
    a source checkout has). Pass the connected watch's serial/firmware - from
    /api/device - to pin the exact file; a data folder may hold descriptors for several
    watches. Deliberately never falls back to a descriptor at a DIFFERENT firmware:
    that is the silent-wrong-schema bug already fixed twice here (Kailash 0x002A,
    Traverse 0x002B/0x002D), where a mismatched descriptor decodes plausible nonsense
    instead of failing loudly."""
    fw = fw or REFERENCE_FW
    for folder in [*suuntolink_dirs(), ASSETS]:
        if not folder.is_dir():
            continue
        if serial:
            exact = folder / f"descr+{serial}+{fw}"
            if exact.is_file():
                return exact
        found = sorted(folder.glob(f"descr+*+{fw}"))
        if found:
            return found[0]
    # Nothing anywhere: name SuuntoLink's folder, not assets/, so the caller's
    # "descriptor is missing from {parent}" message points somewhere actionable.
    return suuntolink_dirs()[0] / f"descr+{serial or '<SERIAL>'}+{fw}"


# --- The watch's own copy --------------------------------------------------------------------
# The `descr+SERIAL+FW` file is not something SuuntoLink invents: on a first connection it asks
# the watch for it (its own log: "NSP::PathDescriptorGet failed ... NOT_SUPPORTED", then
# "setDeviceDescriptors: saving descriptors to library cache" a second later, no network in
# between). On the wire that is command 0x1104, a cursor-paged read: the request is a u32 cursor
# (0 to start), the reply is the next cursor (u32), a "last chunk" byte, one more byte, then the
# data. Concatenated, the chunks ARE the file - byte-identical in every case we hold both sides
# of: Ambit3 Run 2.4.17 and Traverse 1.0.4 + 2.0.22 from SuuntoLink captures, Ambit3 Peak 2.4.17
# read live (26 chunks, 0.8 s, 2026-10-05). So nobody needs SuuntoLink installed, and no table
# has to be hardcoded per model or firmware: every watch brings its own.
CMD_DESCRIPTOR_READ = 0x1104
_CMD_DEVICE_INFO = 0x0000
_MAX_CHUNKS = 400          # 26 on an Ambit3, 21 on a Traverse; a bound, not an expectation
CACHE_DIR = pathlib.Path.home() / "SommetBackups" / "schema"


def is_complete(blob):
    """True when `blob` is a whole SBEM0102 dictionary: the magic, then entries that end
    exactly on the last byte. entries() itself slices past the end without complaining."""
    if blob[:8] != MAGIC:
        return False
    off = 8
    while off + 2 <= len(blob):
        length = blob[off + 1]
        off += 2
        if length == 0xFF:
            if off + 4 > len(blob):
                return False
            length, = struct.unpack_from("<I", blob, off)
            off += 4
        off += length
    return off == len(blob) and len(blob) > 8


def read_from_watch(link):
    """The schema, straight off the connected watch (read-only, 0x1104). Raises RuntimeError
    when the reply is not a complete SBEM0102 dictionary - a half-read schema must never be
    cached, it would mis-name fields on every later run."""
    cursor, blob = 0, b""
    for _ in range(_MAX_CHUNKS):
        reply = link.command(CMD_DESCRIPTOR_READ, struct.pack("<I", cursor), quiet=True)
        if len(reply) < 6:
            raise RuntimeError(f"schema read: short reply ({len(reply)} B) at cursor {cursor}")
        cursor, = struct.unpack("<I", reply[:4])
        blob += reply[6:]
        if reply[4]:
            break
    else:
        raise RuntimeError("schema read: the watch never marked a last chunk")
    if not is_complete(blob):
        raise RuntimeError(f"schema read: {len(blob)} B that are not a whole SBEM0102 dictionary")
    return blob


def watch_identity(link):
    """(serial, firmware) of the open watch, e.g. ("8A15...", "2.4.17") - the two halves of
    the descriptor's file name. Same 0x0000 request and layout as device_info.py."""
    reply = link.command(_CMD_DEVICE_INFO, b"\x02\x48\x03\x00", quiet=True)
    if len(reply) < 36:
        raise RuntimeError(f"device_info reply too short: {len(reply)} bytes")
    serial = reply[16:32].split(b"\0")[0].decode("ascii", "replace")
    fw = reply[32:36]
    return serial, f"{fw[0]}.{fw[1]}.{fw[2] | (fw[3] << 8)}"


def for_link(link):
    """The schema descriptor of the watch `link` is open on. Looks for this exact serial +
    firmware in our own cache, then SuuntoLink's folder, then assets/; failing all three it
    reads the schema from the watch itself and caches it. Never a different firmware's file,
    never another model's - which also retires the per-model lookups that only worked on a
    machine holding a private assets/ folder."""
    serial, fw = watch_identity(link)
    name = f"descr+{serial}+{fw}"
    for folder in [CACHE_DIR, *suuntolink_dirs(), ASSETS]:
        path = folder / name
        if path.is_file() and is_complete(path.read_bytes()):
            return path
    blob = read_from_watch(link)
    path = CACHE_DIR / name
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(blob)
        os.replace(tmp, path)
    except OSError:
        # Read-only home, full disk: still answer this run, from a temp file.
        import tempfile
        handle, tmp_name = tempfile.mkstemp(prefix="descr+", suffix=f"+{fw}")
        with os.fdopen(handle, "wb") as out:
            out.write(blob)
        return pathlib.Path(tmp_name)
    return path


# Types of the <FRM> tag. utf8 is NUL-terminated, the rest is little-endian.
STRUCT_FMT = {"uint8": "<B", "int8": "<b", "bool": "<B",
              "uint16": "<H", "int16": "<h",
              "uint32": "<I", "int32": "<i",
              "float32": "<f", "float64": "<d"}


class Field:
    __slots__ = ("fid", "path", "frm", "mod")

    def __init__(self, fid, path, frm, mod):
        self.fid, self.path, self.frm, self.mod = fid, path, frm, mod

    @property
    def base(self):
        """Bare type, without the `,nillable=...` suffix nor the enum values."""
        return self.frm.split(",")[0].split(":")[0]

    @property
    def name(self):
        return self.path.rsplit(".", 1)[-1]

    @property
    def size(self):
        return None if self.base == "utf8" else struct.calcsize(self.fmt)

    @property
    def fmt(self):
        return "<B" if self.base == "enum" else STRUCT_FMT[self.base]


class Schema:
    def __init__(self, fields, groups, queries):
        self.fields, self.groups, self.queries = fields, groups, queries

    def label(self, eid):
        if eid in self.groups:
            first = self.fields[self.groups[eid][0]].path
            # The `+` in the descriptor marks the start of a repeated record.
            return first.replace("+", ".").rsplit(".", 1)[0]
        if eid in self.queries:
            return self.queries[eid]
        if eid in self.fields:
            return self.fields[eid].path
        return None

    def field_name(self, eid, fid):
        """Field name relative to the record root: the groups repeat Min/Max/Avg
        under several sub-objects."""
        path = self.fields[fid].path.replace("+", ".")
        root = self.label(eid) + "."
        return path[len(root):] if path.startswith(root) else path

    def decode_record(self, body, off, eid):
        """Decodes one record of group `eid` starting at `off`."""
        out = []
        for fid in self.groups[eid]:
            f = self.fields[fid]
            if f.base == "utf8":
                end = body.index(b"\0", off)
                value, off = body[off:end].decode("utf-8", "replace"), end + 1
            else:
                value, = struct.unpack_from(f.fmt, body, off)
                off += f.size
            out.append((f, value))
        return out, off

    def decode_entry(self, eid, data):
        """Decodes a complete SBEM entry, that is one or several records."""
        if eid in self.groups:
            records, off = [], 0
            while off < len(data):
                record, off = self.decode_record(data, off, eid)
                records.append(record)
            return records
        if eid in self.fields:
            f = self.fields[eid]
            if f.base == "utf8":
                return [[(f, data.split(b"\0")[0].decode("utf-8", "replace"))]]
            return [[(f, struct.unpack_from(f.fmt, data, 0)[0])]]
        return None


def waypoint_entry(schema):
    """(entry number, has altitude) of the POI records ("WayPoint") in a watch's descriptor,
    None when it has none. The entry is the group that starts with the field sml+WayPoint.Name;
    the Traverse's also ends with sml.WayPoint.Altitude."""
    for gid, fids in schema.groups.items():
        paths = [schema.fields[f].path for f in fids if f in schema.fields]
        if paths and paths[0] == "sml+WayPoint.Name":
            return gid, any(p == "sml.WayPoint.Altitude" for p in paths)
    return None


def entries(buf):
    """Walks the `[u8 id][u8 len][data]` entries of an SBEM0102 payload, a `len` of
    0xff introducing a u32 length. An id byte of 0xff introduces a u16 id, for the fields
    numbered 0xff and above: `[ff][u16 id][u8 len][data]`. Seen on a real Ambit3 Peak
    (fw 2.4.17, 2026-10-18): its DeviceHistory reply carries 0x12e (the daily steps) and
    0x130 (the timestamp) that way."""
    if buf[:8] != MAGIC:
        raise ValueError(f"SBEM0102 magic expected, saw {buf[:8]!r}")
    off = 8
    while off + 2 <= len(buf):
        eid = buf[off]
        off += 1
        if eid == 0xFF:
            if off + 3 > len(buf):
                break
            eid, = struct.unpack_from("<H", buf, off)
            off += 2
        length = buf[off]
        off += 1
        if length == 0xFF:
            length, = struct.unpack_from("<I", buf, off)
            off += 4
        yield eid, buf[off:off + length]
        off += length


def load(path=None):
    fields, groups, queries = {}, {}, {}
    for _, data in entries(pathlib.Path(path or default_descriptor()).read_bytes()):
        fid, = struct.unpack_from("<H", data, 0)
        text = data[2:].split(b"\0")[0].decode("latin-1")
        tags = dict(re.findall(r"<([A-Z]+)>([^<]*)", text))
        path_ = tags.get("PTH", "").strip("\n")
        if "GRP" in tags:
            groups[fid] = [int(x) for x in tags["GRP"].split(",")]
        elif "QRY" in tags:
            queries[fid] = path_
        else:
            fields[fid] = Field(fid, path_, tags["FRM"].strip("\n"),
                                tags.get("MOD", "").strip())
    return Schema(fields, groups, queries)


def dump(schema):
    print(f"{len(schema.fields)} fields, {len(schema.groups)} groups, "
          f"{len(schema.queries)} queryable objects\n")
    for fid in sorted(schema.fields | schema.groups | schema.queries):
        if fid in schema.groups:
            print(f"{fid:4} 0x{fid:02x}  GROUP  {schema.label(fid)}")
            for sub in schema.groups[fid]:
                f = schema.fields[sub]
                print(f"            {f.name:34} {f.frm}")
        elif fid in schema.queries:
            print(f"{fid:4} 0x{fid:02x}  QUERY  {schema.queries[fid]}")
        else:
            f = schema.fields[fid]
            mod = f"  <MOD>{f.mod}" if f.mod else ""
            print(f"{fid:4} 0x{fid:02x}  {f.path:66} {f.frm}{mod}")


def show_capture(schema, path):
    sys.path.insert(0, str(HERE))
    from ambit_pcap import messages

    for m in messages(path):
        head = m.payload.find(MAGIC)
        if head < 0:
            continue
        print(f"{'<-' if m.incoming else '->'} 0x{m.command:04x}  "
              f"prefix {m.payload[:head].hex()}")
        for eid, data in entries(m.payload[head:]):
            label = schema.label(eid) or "?"
            print(f"   0x{eid:02x} {label}  [{len(data)}]")
            records = schema.decode_entry(eid, data)
            if records is None:
                print(f"        {data[:32].hex()}")
                continue
            if not data:
                continue
            for record in records:
                print("        " + "  ".join(
                    f"{schema.field_name(eid, f.fid)}={v!r}" for f, v in record))


def verify(schema):
    """Confronts the schema with the captures: the entry identifiers observed on the
    wire must be the descriptor's, and the schema-driven POI decoding must match the
    one in `ambit_format`."""
    sys.path.insert(0, str(HERE))
    import ambit_format as F
    from ambit_pcap import messages

    captures = HERE.parent / "assets" / "ambit3 pcap"
    expected = {0x0B21: 74, 0x0B24: 85, 0x0B25: 85}
    failures, checks = [], 0

    for cap in sorted(p for p in captures.iterdir()
                      if p.is_file() and p.suffix.lower() != ".gpx"):
        for m in messages(str(cap)):
            head = m.payload.find(MAGIC)
            if head < 0:
                continue
            ids = [eid for eid, _ in entries(m.payload[head:])]
            checks += 1
            want = expected.get(m.command)
            if want is not None and set(ids) != {want}:
                failures.append(f"{cap.name} 0x{m.command:04x}: "
                                f"expected 0x{want:02x}, saw {set(map(hex, ids))}")
            unknown = [i for i in ids if schema.label(i) is None]
            if unknown:
                failures.append(f"{cap.name} 0x{m.command:04x}: "
                                f"ids missing from the descriptor {set(map(hex, unknown))}")
            if m.command != 0x0B25 or m.incoming:
                continue
            schema_pois = [(r[0][1], r[8][1], r[9][1])
                           for eid, data in entries(m.payload[head:])
                           for r in schema.decode_entry(eid, data)]
            heuristic = [(p["name"], p["lat"], p["lon"])
                         for p in F.parse_sbem_poi_list(m.payload)]
            checks += 1
            if schema_pois != heuristic:
                failures.append(f"{cap.name} 0x0b25: schema POI {schema_pois} "
                                f"!= heuristic {heuristic}")

    print(f"{checks - len(failures)}/{checks} SBEM payloads conform to the descriptor")
    for line in failures:
        print(f"  FAIL  {line}")
    return 1 if failures else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("descriptor", nargs="?", default=str(default_descriptor()))
    ap.add_argument("--capture", help="names and decodes the SBEM payloads of a pcap")
    ap.add_argument("--group", type=int, help="detail of one identifier")
    ap.add_argument("--verify", action="store_true",
                    help="confronts the schema with every capture")
    args = ap.parse_args()

    if not pathlib.Path(args.descriptor).exists():
        print(f"descriptor not found: {args.descriptor}\n"
              "It comes from SuuntoLink's data folder (descr+SERIAL+FW).")
        return 2

    schema = load(args.descriptor)
    if args.verify:
        return verify(schema)
    if args.capture:
        show_capture(schema, args.capture)
    elif args.group is not None:
        eid = args.group
        print(f"0x{eid:02x} = {eid}  {schema.label(eid)}")
        for fid in schema.groups.get(eid, [eid]):
            f = schema.fields[fid]
            print(f"  {fid:4} {f.path:66} {f.frm}"
                  f"{'  <MOD>' + f.mod if f.mod else ''}")
    else:
        dump(schema)
    return 0


if __name__ == "__main__":
    sys.exit(main())
