"""Binary format of the Ambit3 navigation database (Emu, fw 2.4.17).

Every structure is little-endian and unpadded.
Verified against assets/ambit3 pcap/{route12km,route128km,routedelete}.
"""

import hashlib
import math
import struct

# Regions, as the watch declares them in its 0x0b21 response.
WAYPOINT_BASE, WAYPOINT_REGION_SIZE = 0x005000, 16384
ROUTE_BASE, ROUTE_REGION_SIZE = 0x14C080, 130000
SGEE_BASE, SGEE_REGION_SIZE = 0x0704E0, 140000
# Real, 2026-08-10, read off a connected Kailash's own 0x0b21 reply (assets/pcap/
# kailashactivity carries the same map): a second, separate orbital-data region that the
# Ambit3 family does not have at all. Address/size are the Kailash's own; read_memory_map()
# resolves them live per device rather than trusting these, since no other GLONASS-capable
# watch (Traverse, Traverse Alpha, Ambit3 Vertical) has been read yet.
GLONASS_SGEE_BASE, GLONASS_SGEE_REGION_SIZE = 0x1339E0, 100000
TRAINING_PROGRAM_BASE, TRAINING_PROGRAM_REGION_SIZE = 0x001000, 3072
APPS_BASE, APPS_REGION_SIZE = 0x0927C0, 200000
CUSTOM_MODES_BASE, CUSTOM_MODES_REGION_SIZE = 0x002000, 12288

# The closing hash does not cover the same thing depending on the region:
#   PADDED  = the whole region, unwritten bytes at 0xff  (Routes, Waypoints)
#   WRITTEN = only the written bytes                     (GpsSGEE)
# Both modes are verified on the captures; WRITTEN is the one already implemented
# by libambit_pmem20_gps_orbit_write(..., include_sha256_hash=true).
HASH_PADDED, HASH_WRITTEN = "padded", "written"
REGIONS = {
    WAYPOINT_BASE: ("Waypoints", WAYPOINT_REGION_SIZE, HASH_PADDED),
    ROUTE_BASE: ("Routes", ROUTE_REGION_SIZE, HASH_PADDED),
    SGEE_BASE: ("GpsSGEE", SGEE_REGION_SIZE, HASH_WRITTEN),
    # UNVERIFIED hash mode, 2026-08-10: WRITTEN by direct analogy with GpsSGEE, which this
    # region matches in every respect that can be checked without writing it - same
    # length-prefixed blob shape, and the two source files (gpsorbit/binary and
    # glonassorbit/binary) share a byte-identical 12-byte header (magic 62 12 37 09,
    # version 7f 01, big-endian year + month + day). No capture of a real GlonassSGEE write
    # exists anywhere: SuuntoLink's own Devices.xml gates the GLONASS download to exactly
    # three devices (Ambit3 Vertical, Traverse, Traverse Alpha) and Kailash is not one of
    # them, so this region has never been written on André's watch - read back as 4096/4096
    # bytes of 0xff, i.e. erased flash.
    GLONASS_SGEE_BASE: ("GlonassSGEE", GLONASS_SGEE_REGION_SIZE, HASH_WRITTEN),
    # UNVERIFIED, 2026-08-05: no capture in this project touches TrainingProgram, so neither
    # its presence here nor WRITTEN as its hash mode is confirmed against real bytes - it's an
    # inference from the format being self-describing (an item count in its own header), the
    # same shape as GpsSGEE's length-prefixed blob, unlike Routes/Waypoints' fixed-size
    # database. See training_program_andre.md.
    TRAINING_PROGRAM_BASE: ("TrainingProgram", TRAINING_PROGRAM_REGION_SIZE, HASH_WRITTEN),
    # UNVERIFIED, 2026-08-05, both entries: no capture exists of a real write to either
    # region (the only real installs seen were done by SuuntoLink itself, never captured on
    # the wire - training_program_andre.md's Finding 15/"what actually getting this feature
    # back would take"). Apps -> WRITTEN by analogy with GpsSGEE/TrainingProgram: entries are
    # self-describing (their own total_length) and simply appended, the same growing-heap
    # shape, unlike Routes/Waypoints' fixed database. CustomModes -> PADDED by analogy with
    # Routes/Waypoints instead: real before/after captures (custom_modes_andre.md) show the
    # region staying exactly CUSTOM_MODES_REGION_SIZE bytes across a write, content simply
    # growing into what was previously 0xFF padding - the same fixed-region-rewritten-whole
    # shape, not a variable-length append-only heap.
    APPS_BASE: ("Apps", APPS_REGION_SIZE, HASH_WRITTEN),
    # CORRECTED 2026-08-09 (training_program_andre.md Finding 28) from PADDED to WRITTEN, from 4
    # real SuuntoLink app-install captures (assets/ambit3 pcap/v2/) - the earlier PADDED value
    # was an unverified "by analogy with Routes/Waypoints" guess and it was WRONG. Every real
    # capture writes CustomModes as ONLY its used BXML extent (4 + the DEVICE_CUSTOM root tag's
    # length, ~5.9 KB, not the full 12288), and the closing 0x0b18 hash covers exactly that
    # extent. Proven byte-exact: SHA256 of the used extent equals the hash the watch itself
    # reports for CustomModes in its 0x0b21 memory-map reply (EDF772C7... for the trekking
    # capture); SHA256 of the full padded 12288 does NOT. The old PADDED hash is the confirmed
    # cause of "err:62 on all sport modes" after a cold boot: the watch recomputes the used-
    # extent hash on boot and it never matched this project's full-region hash. Writers must
    # therefore write ONLY the used extent (see custom_modes.used_extent()), so the WRITTEN
    # span equals the extent the watch will hash.
    CUSTOM_MODES_BASE: ("CustomModes", CUSTOM_MODES_REGION_SIZE, HASH_WRITTEN),
}

WAYPOINT_HEADER_MAGIC = 0x0334
# Empty nav database: both headers, everything at zero after the magic and marker.
ROUTE_HEADER_RESET = bytes.fromhex("0c340001") + b"\0" * 28
WAYPOINT_HEADER_RESET = bytes.fromhex("34030000ffff")

WAYPOINT_DESC = 0x005020
ROUTE_DESC = 0x14C0A0
ROUTE_POINTS = 0x14CAC8
ROUTE_INDEX = 0x169F88
WAYPOINT_INDEX = 0x16A370

MAX_ROUTES = 50
MAX_ROUTE_POINTS = 1000
MAX_TOTAL_ROUTE_POINTS = 10000
MAX_WAYPOINTS = 100
MAX_NAME_BYTES = 15

ROUTE_HEADER_MAGIC = 0x340C  # Ambit2: 0x3008
ALTITUDE_NONE = 30000

# Radius of the relative coordinates: the one implied by the definition of the
# nautical mile (1852 m per arc minute). Determined by fitting on 1188 points, exact.
# This is NOT the 6367 km of openambit's distance_calc(), which reproduces only
# 124/336 then 508/852 points.
EARTH_RADIUS_ROUTE_M = 10800 * 1852 / math.pi  # 6366707.0195
EARTH_RADIUS_RELDIST_M = 6378100.0  # calculateRelativeDistance() in route.js

ROUTE_HEADER = struct.Struct("<HBBHHIHH")          # 16 useful bytes out of 32
ROUTE_DESCRIPTOR = struct.Struct("<16sIHIiiiiHHHHH")  # 52 B
ROUTE_POINT = struct.Struct("<iiHH")                # 12 B
WAYPOINT_DESCRIPTOR = struct.Struct("<ii16s16s12s")  # 52 B
WAYPOINT_TAIL = struct.Struct("<HBBBBBBB3s")        # 12 B, descriptor tail
ROUTE_INDEX_ENTRY = struct.Struct("<IIIBBBBI")      # 20 B

WAYPOINT_TAIL_MAGIC = 0x0771  # constant across the 42 observed tails
WAYPOINT_TYPE_DEFAULT = 17    # "Waypoint"; enum on the Ambit side, cf. openambit

# The Ambit POI/waypoint type byte, 0x00-0x11 - the 18 icon groups the watch itself renders.
# From Lars's "Types of Poi.md" (assets/Lars, 2017 Ambit RE), cross-checked against openambit.
# Movescount had ~31 finer names (Restaurant/Cafe both map to Food=7, Hostel/Hotel to
# Lodging=10, Hill/Valley/Cliff to Mountain=12, River/Lake/Coast to Water=16, etc.) but the
# *watch* only distinguishes these 18 by the type byte, so this is what a POI picker chooses.
WAYPOINT_TYPES = {
    0: "Building", 1: "Cave", 2: "Camp", 3: "Car", 4: "Crossroads", 5: "Beginning",
    6: "End", 7: "Food", 8: "Forest", 9: "Geocache", 10: "Lodging", 11: "Meadow",
    12: "Mountain", 13: "Sight", 14: "Road", 15: "Rock", 16: "Water", 17: "Waypoint",
}


def waypoint_type_name(type_id):
    """POI type byte -> human name, falling back to 'Waypoint' for anything unexpected."""
    return WAYPOINT_TYPES.get(type_id, WAYPOINT_TYPES[WAYPOINT_TYPE_DEFAULT])


def waypoint_type_id(value):
    """Resolve a POI type given either its numeric id or its (case-insensitive) name.
    Raises ValueError on an unknown name so a CLI/API surfaces it rather than writing 17."""
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        tid = int(value)
        if tid not in WAYPOINT_TYPES:
            raise ValueError(f"POI type id {tid} out of range 0-17")
        return tid
    for tid, name in WAYPOINT_TYPES.items():
        if name.lower() == str(value).strip().lower():
            return tid
    raise ValueError(f"unknown POI type {value!r}; choose from {list(WAYPOINT_TYPES.values())}")
ROUTE_INDEX_CONST = 415       # field @8, constant across the 7 observed entries

# The route timestamp (index @4, in seconds) shares its clock with the date in the
# waypoint tail: verified to the second on 7 entries, including two independent gaps
# of 19 h and 39 days. The epoch is not a known round date; it is defined
# empirically, relative to the naive local date.
ROUTE_TIME_EPOCH = "1953-11-25T17:31:44"


def crc16_ccitt_false(data, crc=0xFFFF):
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def region_hash(flash, base):
    """Closing hash of the 0x0b18, in uppercase hex. See REGIONS for the mode."""
    name, size, mode = REGIONS[base]
    if mode == HASH_PADDED:
        buf = flash.read(base, size, fill=0xFF)
    else:
        written = [(a, s) for a, s in flash.ranges() if a == base]
        buf = flash.read(base, written[0][1]) if written else b""
    return hashlib.sha256(buf).hexdigest().upper()


def encode_name(name, encoding="utf-8"):
    """15 useful bytes + NUL, truncated on bytes and not on characters.

    CORRECTED 2026-08-22: was "iso-8859-15" here (mismatched with decode_name's own "utf-8"
    right below - an encode/decode round-trip was never actually identity for non-ASCII).
    Confirmed wrong against real hardware: André's real Ambit3 Sport, set to French, has
    real sport-mode names ("Entraîn. salle", "Course itinér.") - reading them through the
    OLD iso-8859-15 decode (custom_modes.py, same bug, fixed alongside this) produced visible
    mojibake ("EntraÃ®n. salle") - the unmistakable signature of UTF-8 bytes misread as
    Latin-1/-15. The watch's firmware sends/expects UTF-8; every sibling encoder/decoder
    across tools/ that used iso-8859-15 for a watch NAME field was fixed the same session.

    Truncation is now also UTF-8-safe: cutting a multi-byte character in half (e.g. a name
    with an accented character sitting right at the 15-byte boundary) used to leave a
    dangling lead byte, decoded back as U+FFFD on read - real for any long enough French/
    accented name, not hypothetical."""
    raw = name.encode(encoding, "replace")[:MAX_NAME_BYTES]
    if encoding.lower() in ("utf-8", "utf8"):
        raw = raw.decode("utf-8", "ignore").encode("utf-8")
    return raw + b"\0" * (16 - len(raw))


def decode_name(raw):
    return raw.split(b"\0")[0].decode("utf-8", "replace")


class RouteHeader:
    def __init__(self, route_count, point_count, checksum, magic=ROUTE_HEADER_MAGIC):
        self.route_count = route_count
        self.point_count = point_count
        self.checksum = checksum
        self.magic = magic

    @classmethod
    def parse(cls, blob):
        magic, b2, b3, count, pad, points, crc, trailer = ROUTE_HEADER.unpack(blob[:16])
        h = cls(count, points, crc, magic)
        h.raw_b2, h.raw_b3, h.raw_pad, h.raw_trailer = b2, b3, pad, trailer
        h.raw_tail = blob[16:32]
        return h

    def build(self):
        # b3 and the word @14 are 1 on the Ambit3; openambit puts 0 at @14 for Ambit2.
        return ROUTE_HEADER.pack(self.magic, 0, 1, self.route_count, 0,
                                 self.point_count, self.checksum, 1) + b"\0" * 16


class WaypointHeader:
    """[u16 magic][u16 count][u16 crc16 of the descriptors]. Exact on 5 captures."""

    STRUCT = struct.Struct("<HHH")

    def __init__(self, count, checksum, magic=WAYPOINT_HEADER_MAGIC):
        self.count, self.checksum, self.magic = count, checksum, magic

    @classmethod
    def parse(cls, blob):
        magic, count, checksum = cls.STRUCT.unpack(blob[:6])
        return cls(count, checksum, magic)

    @classmethod
    def build_for(cls, descriptor_blob, count):
        return cls.STRUCT.pack(WAYPOINT_HEADER_MAGIC, count,
                               crc16_ccitt_false(descriptor_blob))


class RouteDescriptor:
    FIELDS = ("name", "start_index", "point_count", "distance", "mid_lat", "mid_lon",
              "max_x", "max_y", "unknown1", "unknown2", "unknown3", "ascent", "descent")

    def __init__(self, **kw):
        kw.setdefault("unknown1", 0xFFFF)
        kw.setdefault("unknown2", 0xFFFF)
        kw.setdefault("unknown3", 0)
        for f in self.FIELDS:
            setattr(self, f, kw[f])

    @classmethod
    def parse(cls, blob):
        v = ROUTE_DESCRIPTOR.unpack(blob)
        return cls(**dict(zip(cls.FIELDS, (decode_name(v[0]),) + v[1:])))

    def build(self, encoding="utf-8"):
        return ROUTE_DESCRIPTOR.pack(
            encode_name(self.name, encoding), self.start_index, self.point_count,
            self.distance, self.mid_lat, self.mid_lon, self.max_x, self.max_y,
            self.unknown1, self.unknown2, self.unknown3, self.ascent, self.descent)


class RoutePoint:
    __slots__ = ("x", "y", "altitude", "rel_distance")

    def __init__(self, x, y, altitude, rel_distance):
        self.x, self.y = x, y
        self.altitude, self.rel_distance = altitude, rel_distance

    @classmethod
    def parse(cls, blob):
        return cls(*ROUTE_POINT.unpack(blob))

    def build(self):
        return ROUTE_POINT.pack(self.x, self.y, self.altitude, self.rel_distance)


class WaypointTail:
    """The last 12 bytes of the waypoint descriptor. Conforms on 42 tails.

    The date is the last modification date of the ROUTE, not of the waypoint: it is
    identical for every waypoint of the same route, stable from one capture to the
    next, and agrees to the second with the @4 timestamp of the route index. The
    year is not stored.
    """

    def __init__(self, month, day, hour, minute, second, rank,
                 wtype=WAYPOINT_TYPE_DEFAULT, magic=WAYPOINT_TAIL_MAGIC):
        self.month, self.day = month, day
        self.hour, self.minute, self.second = hour, minute, second
        self.rank = rank          # rank of the waypoint in its route, from 0
        self.type = wtype
        self.magic = magic

    @classmethod
    def parse(cls, blob):
        magic, mo, da, ho, mi, se, rank, wtype, pad = WAYPOINT_TAIL.unpack(blob)
        tail = cls(mo, da, ho, mi, se, rank, wtype, magic)
        tail.pad = pad
        return tail

    def build(self):
        return WAYPOINT_TAIL.pack(self.magic, self.month, self.day, self.hour,
                                  self.minute, self.second, self.rank, self.type,
                                  b"\0\0\0")

    def plausible(self):
        return (self.magic == WAYPOINT_TAIL_MAGIC and 1 <= self.month <= 12
                and 1 <= self.day <= 31 and self.hour <= 23
                and self.minute <= 59 and self.second <= 59
                and getattr(self, "pad", b"\0\0\0") == b"\0\0\0")

    def __str__(self):
        return (f"{self.month:02}-{self.day:02} {self.hour:02}:{self.minute:02}:"
                f"{self.second:02} rank {self.rank} type {self.type}")


class RouteIndexEntry:
    """20 bytes per route, at 0x169f88.

    `waypoint_count` is the route's waypoint count: exact on the 7 entries.
    `waypoint_start` is indeed the index of the route's first waypoint in the table,
    except in the `sync` capture: to be confirmed. The two bytes @14-15 are zero
    everywhere except for the secondary route of a two-route database, where the
    second one is 9.
    """

    def __init__(self, number, timestamp, waypoint_start, waypoint_count,
                 extra=(0, 0), const=ROUTE_INDEX_CONST, trailer=0):
        self.number = number          # rank of the route, from 1
        self.timestamp = timestamp    # seconds, same clock as WaypointTail
        self.waypoint_start = waypoint_start
        self.waypoint_count = waypoint_count
        self.extra, self.const, self.trailer = extra, const, trailer

    @classmethod
    def parse(cls, blob):
        num, ts, const, start, count, e0, e1, trailer = ROUTE_INDEX_ENTRY.unpack(blob)
        return cls(num, ts, start, count, (e0, e1), const, trailer)

    def build(self):
        return ROUTE_INDEX_ENTRY.pack(self.number, self.timestamp, self.const,
                                      self.waypoint_start, self.waypoint_count,
                                      self.extra[0], self.extra[1], self.trailer)


class WaypointDescriptor:
    def __init__(self, lat, lon, name, route_name, tail=b"\0" * 12):
        self.lat, self.lon = lat, lon
        self.name, self.route_name, self.tail = name, route_name, tail

    @classmethod
    def parse(cls, blob):
        lat, lon, name, route_name, tail = WAYPOINT_DESCRIPTOR.unpack(blob)
        return cls(lat, lon, decode_name(name), decode_name(route_name), tail)

    def build(self):
        # The observed names are UTF-8 ("Andre" with its accent encoded c3 a9),
        # unlike the route names, which route.js announces as ISO-8859-15.
        return WAYPOINT_DESCRIPTOR.pack(self.lat, self.lon,
                                        encode_name(self.name, "utf-8"),
                                        encode_name(self.route_name, "utf-8"),
                                        self.tail)


# --- SBEM payload of the 0x0b25 --------------------------------------------

SBEM_MAGIC = b"SBEM0102"


def sbem_entries(payload):
    """Entries of an SBEM0102 payload: [u32 0][u8][u8]["SBEM0102"] then a run of
    [u8 id][u8 len][len bytes].

    Length extension: when the length byte is 0xff, a u32 carrying the real length
    follows. openambit only writes single-byte lengths
    (`libambit_sbem0102_data_add`), but the watch does emit the extended form.
    """
    if len(payload) < 14 or payload[6:14] != SBEM_MAGIC:
        raise ValueError("unexpected SBEM0102 payload")
    out = []
    off = 14
    while off + 2 <= len(payload):
        entry_id, length = payload[off], payload[off + 1]
        off += 2
        if length == 0xFF:
            length, = struct.unpack("<I", payload[off:off + 4])
            off += 4
        out.append((entry_id, payload[off:off + length]))
        off += length
    return out


#   name, route name and timestamp are NUL-terminated; the five u8 in between are what
#   an earlier version of this parser mistook for padding.
POI_FIELDS = ("name", "route_name", "stamp", "route_index", "type", "sub_type",
              "type_index", "flags", "lat", "lon")


def parse_sbem_poi_list(payload):
    """Decodes the POI list of the 0x0b24 and 0x0b25: three NUL-terminated strings, five
    u8, then latitude and longitude as i32 x 1e7. Ten fields, and no altitude.

    These POIs are DISJOINT from the binary descriptors of the Waypoints region, which
    only carry route waypoints - but they do not survive a navigation write. Confirmed on
    hardware 2026-08-04: the write erases them and the 0x0b25 is what puts them back.

    This used to skip runs of zero bytes to find the coordinates, which worked only
    because SuuntoLink writes zero in all five u8. A POI created on the watch does not:
    2026-08-04 produced Type=17, TypeIndex=1, Flags=1, and the old parser crashed on it.
    The layout is hardcoded here rather than read from the schema, so that
    `sbem_schema.py --verify` stays an independent check of the two against each other.
    """
    body = b"".join(data for _, data in sbem_entries(payload))
    out = []
    off = 0
    while off < len(body):
        values = []
        for _ in range(3):
            end = body.index(b"\0", off)
            values.append(body[off:end].decode("utf-8", "replace"))
            off = end + 1
        values.extend(body[off:off + 5])
        off += 5
        values.extend(struct.unpack("<ii", body[off:off + 8]))
        off += 8
        out.append(dict(zip(POI_FIELDS, values)))
    return out


def build_poi_record(name, lat, lon, stamp, route_name="", route_index=0,
                      type_=WAYPOINT_TYPE_DEFAULT, sub_type=0, type_index=0, flags=1):
    """Encodes one POI record: the exact inverse of `parse_sbem_poi_list` - three
    NUL-terminated strings, five u8, then latitude/longitude as i32 x 1e7.

    Defaults (`type_=17`, `flags=1`) match what the watch itself writes for a POI it
    creates - confirmed on hardware 2026-08-04, see `parse_sbem_poi_list`. SuuntoLink
    instead leaves type/sub_type/type_index/flags at 0 for imported POIs.
    """
    return (name.encode("utf-8") + b"\0" +
            route_name.encode("utf-8") + b"\0" +
            stamp.encode("utf-8") + b"\0" +
            bytes([route_index, type_, sub_type, type_index, flags]) +
            struct.pack("<ii", round(lat * 1e7), round(lon * 1e7)))


# --- geometry --------------------------------------------------------------

def haversine_m(lat_a, lon_a, lat_b, lon_b, radius=EARTH_RADIUS_RELDIST_M):
    lat_a, lon_a, lat_b, lon_b = map(math.radians, (lat_a, lon_a, lat_b, lon_b))
    t = (math.sin((lat_b - lat_a) / 2) ** 2
         + math.cos(lat_a) * math.cos(lat_b) * math.sin((lon_b - lon_a) / 2) ** 2)
    return 2 * radius * math.atan2(math.sqrt(t), math.sqrt(1 - t))


def bbox_mid(points):
    """Centre of the bbox in degrees x 1e7. max - (max-min)/2, as openambit does:
    this is not the barycentre.

    The computation is done on the x 1e7 integers, not on the degrees, so that the C
    version of the serializer is identical by construction. Both forms coincide on
    the fixtures, whose coordinates are exactly representable at 1e-7."""
    lats = [round(p[0] * 1e7) for p in points]
    lons = [round(p[1] * 1e7) for p in points]
    mid_lat = max(lats) - (max(lats) - min(lats)) / 2
    mid_lon = max(lons) - (max(lons) - min(lons)) / 2
    return round(mid_lat), round(mid_lon)


def _round_half_up(value):
    """Rounding of the magnitude, as the encoder does it: -2.5 gives -3, not -2."""
    return int(math.floor(abs(value) + 0.5))


def relative_xy(mid_lat_e7, mid_lon_e7, lat, lon):
    """Signed metres from the bbox centre, equirectangular projection at the
    centre's parallel. Exact on the 1188 points of both fixtures."""
    mid_lat, mid_lon = mid_lat_e7 / 1e7, mid_lon_e7 / 1e7
    r = EARTH_RADIUS_ROUTE_M
    x = _round_half_up(r * math.cos(math.radians(mid_lat)) * math.radians(lon - mid_lon))
    y = _round_half_up(r * math.radians(lat - mid_lat))
    return (-x if lon < mid_lon else x), (-y if lat < mid_lat else y)


def inverse_xy(mid_lat_e7, mid_lon_e7, x, y):
    """Inverse of relative_xy, up to the integer rounding (less than a metre)."""
    mid_lat, mid_lon = mid_lat_e7 / 1e7, mid_lon_e7 / 1e7
    r = EARTH_RADIUS_ROUTE_M
    lat = mid_lat + math.degrees(y / r)
    lon = mid_lon + math.degrees(x / (r * math.cos(math.radians(mid_lat))))
    return lat, lon


def cumulative_distance_m(points, radius=EARTH_RADIUS_RELDIST_M):
    total = 0.0
    steps = [0.0]
    for a, b in zip(points, points[1:]):
        total += haversine_m(a[0], a[1], b[0], b[1], radius)
        steps.append(total)
    return total, steps


def relative_distances(points):
    """The records' rel_distance field: the travelled fraction, rounded to 4
    significant digits the way route.js does (toPrecision(4)), then scaled to 16
    bits and truncated. Exact on the 852 points of route128km."""
    total, steps = cumulative_distance_m(points)
    if total <= 0:
        return [0] * len(points)
    return [int(float(f"{step / total:.4g}") * 0xFFFF) for step in steps]
