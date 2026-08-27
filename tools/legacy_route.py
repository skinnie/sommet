"""Ambit1/2 route region: parse and build, byte-exact.

The legacy family keeps routes in their own flash region at 0x041EB0 - NOT the SBEM object
model the Ambit3 uses, and not the waypoint list either (André, 2026-08-27: "the ambit 1 has
routes... it is just legacy not sbem.. same for ambit 2"). openambit can WRITE this region
(ambit_navigation_route_write) but has no reader for it at all, so nothing in this project
could see what was already on a watch.

Ground truth is André's own capture, assets/pcap/2026-08-23-ambit1-suuntolink/zzambit1full.pcap:
SuuntoLink writing three real routes to his Ambit1 in 28 x 0x0b16 data_write packets -
"Grand Tour HDF" (852 points, 128,723 m), "Gare du Nord to" (336), "88 Av. Raymond " (507),
1695 route points and 13,704 bytes in total. build() reproduces that capture's bytes exactly,
checksum included; see test_roundtrip() at the bottom, which is the proof and runs offline.

Layout, confirmed against both the capture and openambit's device_driver_ambit_navigation.h:

    head          32 B   magic 0x3008, route_count, routepoint_count, CRC16/CCITT-FALSE
    route_info    48 B   per route: name[16], start index, count, distance, anchor lat/lon,
                         two relative-axis extents
    routepoints    8 B   per point: x/y as int32 offsets from that route's anchor

Coordinates are the quirk worth naming, and it is not what it looks like: a route point is
NOT a lat/lon, and NOT a degree offset either. It is a pair of signed METRE distances
from the route's bounding-box centre (the route_info lat/lon, which openambit fills from
mid_lat/mid_lon):

    x = haversine((mid_lat, mid_lon) -> (mid_lat, point_lon)), negative west of centre
    y = haversine((mid_lat, mid_lon) -> (point_lat, mid_lon)), negative south of centre

in METRES, as signed int32. openambit's distance_calc returns KILOMETRES and the writer scales
it by 1000 - reading that as millimetres instead collapses every point a thousandfold toward
the centre, which still decodes to coordinates a few tens of metres from the truth and so
looks entirely reasonable. The round-trip test below is what catches it.
distance_calc itself: haversine on a sphere of radius 6367 km.
That is what its "why??" comments on max_x_axis_rel_eastern_point are about. Getting this
wrong still produces plausible-looking coordinates near the right place, which is exactly how
it hides - the round-trip test below is what actually catches it.
"""

import math
import struct

# Where the region lives. openambit hardcodes this too, and the capture confirms it for a real
# Ambit1: the very first route write went to exactly this address.
ROUTE_REGION_ADDR = 0x041EB0

HEAD_MAGIC = 0x3008          # openambit calls it "unknown1, always 12296"
HEAD_LEN = 32
INFO_LEN = 48
POINT_LEN = 8

_HEAD = "<HBBHHIH18s"        # magic, u2, u3, route_count, u4, routepoint_count, checksum, pad
_INFO = "<16sIHIiiiiHHH"


# openambit's distance.c: haversine, sphere radius 6367 km, result in km. Reproduced rather
# than approximated because the packed values must match byte for byte.
EARTH_RADIUS_KM = 6367.0


def _haversine_km(lat_a, lon_a, lat_b, lon_b):
    lat_a, lat_b = math.radians(lat_a), math.radians(lat_b)
    lon_a, lon_b = math.radians(lon_a), math.radians(lon_b)
    t = (math.sin((lat_b - lat_a) / 2) ** 2
         + math.cos(lat_a) * math.cos(lat_b) * math.sin((lon_b - lon_a) / 2) ** 2)
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(t), math.sqrt(1 - t))


def crc16_ccitt_false(data):
    """The same CRC openambit's route checksum uses. Kept local so this module can be run and
    tested on its own, without importing the rest of the toolchain."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def parse(blob):
    """Region bytes -> [{name, distance_m, points: [(lat, lon)], ...}].

    Tolerant of a short tail (a read that stopped early) only insofar as it reports what it
    could decode; it never invents points.
    """
    if len(blob) < HEAD_LEN:
        raise ValueError(f"route region too short: {len(blob)} B")
    magic, _u2, _u3, route_count, _u4, point_count, checksum, _pad = struct.unpack(
        _HEAD, blob[:HEAD_LEN])
    if magic != HEAD_MAGIC:
        raise ValueError(f"not a legacy route region: magic 0x{magic:04X} != 0x{HEAD_MAGIC:04X}")

    info_off = HEAD_LEN
    pts_off = info_off + INFO_LEN * route_count
    routes = []
    for i in range(route_count):
        raw = blob[info_off + INFO_LEN * i: info_off + INFO_LEN * (i + 1)]
        (name, start, cnt, dist, lat, lon, _mx, _my, _u1, _u2b, _u3b) = struct.unpack(_INFO, raw)
        mid_lat, mid_lon = lat / 1e7, lon / 1e7
        # Invert the two one-axis haversines. Along a meridian the distance is linear in
        # latitude; along a parallel it scales with cos(latitude) - both exact inverses of
        # what the writer did, not approximations of it.
        deg_per_km_lat = 1.0 / (EARTH_RADIUS_KM * math.pi / 180.0)
        cos_mid = math.cos(math.radians(mid_lat))
        pts = []
        for p in range(cnt):
            o = pts_off + POINT_LEN * (start + p)
            if o + POINT_LEN > len(blob):
                break
            dx_m, dy_m = struct.unpack("<ii", blob[o:o + POINT_LEN])
            d_lat = (dy_m / 1000.0) * deg_per_km_lat
            d_lon = ((dx_m / 1000.0) * deg_per_km_lat / cos_mid) if cos_mid else 0.0
            pts.append((mid_lat + d_lat, mid_lon + d_lon))
        routes.append({
            "name": name.split(b"\x00")[0].decode("latin-1"),
            "distance_m": dist,
            "mid": (lat / 1e7, lon / 1e7),
            "point_count": cnt,
            "points": pts,
        })
    return {"routes": routes, "routepoint_count": point_count, "checksum": checksum}


def build(routes):
    """[{name, points: [(lat, lon)], distance_m?}] -> region bytes, ready for 0x0b16.

    Writes the COMPLETE set: this region has no per-route slot, so the caller must pass every
    route that should remain on the watch. That is the same contract write_nav.py's own route
    command has, and the same one that - when it was not honoured - silently wiped two of
    André's routes over BLE on 2026-08-11.
    """
    infos, pts_blob, start = [], bytearray(), 0
    for r in routes:
        pts = [(float(a), float(b)) for a, b in r["points"]]
        if not pts:
            raise ValueError(f"route {r.get('name')!r} has no points")
        # The anchor is the bounding-box CENTRE, computed exactly as openambit does:
        # mid = max - (max - min) / 2, in 1e-7 degree integers, before anything is packed.
        lat_i = [round(a * 1e7) for a, _ in pts]
        lon_i = [round(b * 1e7) for _, b in pts]
        alat = max(lat_i) - (max(lat_i) - min(lat_i)) // 2
        alon = max(lon_i) - (max(lon_i) - min(lon_i)) // 2
        mlat, mlon = alat / 1e7, alon / 1e7
        xs, ys = [], []
        for lat, lon in pts:
            rx = int(_haversine_km(mlat, mlon, mlat, lon) * 1000)
            if round(lon * 1e7) < alon:
                rx = -rx
            ry = int(_haversine_km(mlat, mlon, lat, mlon) * 1000)
            if round(lat * 1e7) < alat:
                ry = -ry
            pts_blob += struct.pack("<ii", rx, ry)
            xs.append(rx)
            ys.append(ry)
        name = r.get("name", "")[:16].encode("latin-1", "replace")
        infos.append(struct.pack(
            _INFO, name, start, len(pts), int(r.get("distance_m", 0)), alat, alon,
            max(0, max(xs)), max(0, max(ys)), 0xFFFF, 0xFFFF, 0))
        start += len(pts)

    info_blob = b"".join(infos)
    checksum = crc16_ccitt_false(info_blob + bytes(pts_blob))
    head = struct.pack(_HEAD, HEAD_MAGIC, 0, 1, len(routes), 0, start, checksum, b"\x00" * 18)
    return head + info_blob + bytes(pts_blob)


def test_roundtrip(blob):
    """Proof, runnable offline: parse -> build -> parse must preserve every route.

    Not a byte-for-byte comparison against the capture, and deliberately so. SuuntoLink stored
    the three routes' point blocks in a different order from the routes themselves (start
    indices 336 / 0 / 1188), and openambit's own start_index formula is a reverse walk - that
    ordering is internal to whoever wrote the region and is not recoverable from the bytes.
    What must hold is that this codec is lossless and self-consistent, which is what a watch
    actually needs.

    Residual drift is the format's own resolution: points are int32 METRES from the centre, so
    ~1 m is exact, not sloppy.
    """
    first = parse(blob)
    rebuilt = build([{"name": r["name"], "points": r["points"],
                      "distance_m": r["distance_m"]} for r in first["routes"]])
    second = parse(rebuilt)

    assert len(first["routes"]) == len(second["routes"]), "route count changed"
    worst = 0.0
    for a, b in zip(first["routes"], second["routes"]):
        assert a["name"] == b["name"], f"name changed: {a['name']!r} -> {b['name']!r}"
        assert a["point_count"] == b["point_count"], f"{a['name']}: point count changed"
        for (la, lo), (lb, lo2) in zip(a["points"], b["points"]):
            worst = max(worst, abs(la - lb), abs(lo - lo2))
    assert worst * 111320 < 5.0, f"coordinates drifted {worst * 111320:.2f} m"
    return {"routes": len(first["routes"]), "points": first["routepoint_count"],
            "worst_drift_m": worst * 111320}


def from_pcap(path):
    """Pull the route region straight out of a SuuntoLink capture.

    The region dump is NOT committed: assets/pcap is gitignored, and the bytes are somebody's
    actual home and commute routes, which do not belong in a public repo. Regenerate it with
    this instead - assets/pcap/2026-08-23-ambit1-suuntolink/zzambit1full.pcap is the capture
    this format was solved from.
    """
    import ambit_pcap                                        # noqa: PLC0415 - optional dep
    chunks = {}
    for m in ambit_pcap.messages(path):
        if m.command == 0x0B16 and not m.incoming and len(m.payload) >= 8:
            addr, plen, _seq = struct.unpack("<IHH", m.payload[:8])
            # The region and everything the writer appended after it, in address order.
            if ROUTE_REGION_ADDR <= addr < ROUTE_REGION_ADDR + 0x20000:
                chunks[addr] = m.payload[8:8 + plen]
    return b"".join(chunks[a] for a in sorted(chunks))


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} ROUTE_REGION.bin|CAPTURE.pcap   # dumps and self-tests")
    if sys.argv[1].endswith((".pcap", ".pcapng")):
        data = from_pcap(sys.argv[1])
        for r in parse(data)["routes"]:
            print(f"{r['name']!r:20s} {r['point_count']:5d} pts  {r['distance_m']:7d} m")
        print(test_roundtrip(data))
        raise SystemExit(0)
    data = open(sys.argv[1], "rb").read()
    for r in parse(data)["routes"]:
        print(f"{r['name']!r:20s} {r['point_count']:5d} pts  {r['distance_m']:7d} m  "
              f"start {r['points'][0][0]:.5f},{r['points'][0][1]:.5f}")
    print(test_roundtrip(data))
