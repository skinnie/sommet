#!/usr/bin/env python3
"""Send a route (GPX) to a Magene C406 (Pro) over BLE so it can navigate it - decoded from the
OneLap app's native BLE library (com.onelap.lib_ble + com.kai.app_bike_computer.RouteToBuffer),
see assets/onelap-re/NOTES.md. No packet captures used.

    ./tools/magene_route.py send --address <addr> --gpx route.gpx [--name "My route"]

HARDWARE-VERIFIED 2026-09-25 on a C406 Pro (route shows and navigates). The byte format was
matched against a real OneLap transfer captured from a rooted tablet's HCI snoop log.

The route file is a protobuf the device parses, sent as a chunked file transfer:
  * content: `stepCount` length-prefixed `RoadPlan` blocks (<len u32LE><RoadPlan>), then a
    FF FF FF FF marker and `previewPointSize` length-prefixed `StepPreviewLocation` "dots" the
    device uses to draw the overview map (without them the route loads but nothing is drawn).
    RoadPlan = {stepOrigin=1 (StepSourceLocation), pathSize=2, path=3 (repeated PathType)};
    StepSourceLocation = {distance=1, duration=2, origin=3, dest=4, directioninfor=5, name=6};
    PathType = {latitude=1, longitude=2} as protobuf **sint32 (zigzag)** of int(deg*2^31/180) -
    plain int32 puts every coordinate 2x off and the device errors. A GPX has no turn-by-turn,
    so it's split into ~50-point "go straight" segments; the app's Mapbox routes carry real road
    names/turns, which the device doesn't require.
  * framing (RouteFilePacket): each packet = A5 5A 5A A5 <totalLen u32LE> <numPackets u16LE>
    <pktIndex u16LE, 1-based> <chunkLen u8> <chunk>, chunk = mtu-17 bytes; FIT CRC-16 of the file.
  * transfer: 54-byte route-info `40 8d` on CC02 (routeId, totalDistance m, zoom, crc, stepCount,
    byteSize, center/NE/SW bbox, previewOffset, previewPointSize), packets on CC03 paced by the
    device's `40 8c 00 <n>` credit grants, then TransFormEnd `40 52` on CC02.
  * The C406 holds ONE route; sending replaces it. If the unit ends up bonded to a host that has
    forgotten it, it goes silent (directed reconnects only) - factory-reset it to recover.
"""

import argparse
import asyncio
import json
import math
import struct
import sys
import time
import xml.etree.ElementTree as ET

from magene_import import _connect, CC02, CC03, _fit_crc16

PRECISION = 2 ** 31
PKT_MAGIC = bytes([0xA5, 0x5A, 0x5A, 0xA5])


# ---- minimal protobuf wire encoding (proto3, no deps) ----
def _varint(n):
    # int32 fields: negative values sign-extend to 64 bits (10-byte varint), per protobuf.
    if n < 0:
        n += 1 << 64
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _tag(field, wire):
    return _varint((field << 3) | wire)


def _pb_int32(field, value):
    return _tag(field, 0) + _varint(int(value))


def _pb_sint32(field, value):
    # protobuf sint32: zigzag-encoded varint. PathType lat/lon are sint32 (verified against a
    # real OneLap capture - plain int32 gave coordinates 2x off and the device errored).
    n = int(value)
    zz = (n << 1) ^ (n >> 31)
    return _tag(field, 0) + _varint(zz)


def _pb_msg(field, body):
    return _tag(field, 2) + _varint(len(body)) + body


def _pathtype(lat_deg, lon_deg):
    lat = int(lat_deg * PRECISION / 180.0)
    lon = int(lon_deg * PRECISION / 180.0)
    return _pb_sint32(1, lat) + _pb_sint32(2, lon)


def _roadplan(points):
    # points: [(lat, lon)]. RoadPlan{ stepOrigin(1), pathSize(2), path(3 repeated PathType) }.
    # stepOrigin: StepSourceLocation{ distance(1), duration(2), origin(3), dest(4), dir(5), name(6) }.
    # A GPX has no turn-by-turn, so a segment is a plain "go straight" step: real distance +
    # endpoints, generic direction (destType=1, destDirect=0), no road name.
    first, last = points[0], points[-1]
    dist = int(round(sum(_haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1))))
    step = bytearray()
    step += _pb_int32(1, dist)                    # distance (m)
    step += _pb_int32(2, 0)                        # duration
    step += _pb_msg(3, _pathtype(*first))         # originLocation
    step += _pb_msg(4, _pathtype(*last))          # destinationLocation
    step += _pb_msg(5, _pb_int32(1, 1) + _pb_int32(2, 0))  # directioninfor {destType=1, destDirect=0}
    body = bytearray()
    body += _pb_msg(1, bytes(step))               # stepOrigin
    body += _pb_int32(2, len(points))             # pathSize
    for lat, lon in points:
        body += _pb_msg(3, _pathtype(lat, lon))   # path
    return bytes(body)


def _step_preview(lat_deg, lon_deg, dest_type=0):
    # RoutePreview.StepPreviewLocation{ previewlocation(1)=PathType, dest_type(2) }.
    body = _pb_msg(1, _pathtype(lat_deg, lon_deg)) + _pb_int32(2, dest_type)
    return body


def build_route_file(points, seg_size=50, preview_max=180):
    # The device parses the route as `stepCount` length-prefixed RoadPlan blocks, then (after a
    # FF FF FF FF marker) a set of StepPreviewLocation "dots" it uses to draw the overview map -
    # without them the route loads but nothing is shown. Mimic the app: split the track into short
    # RoadPlan segments, then append ~preview_max evenly-spaced preview dots.
    segments = []
    i = 0
    n = len(points)
    while i < n - 1:
        seg = points[i:i + seg_size + 1]     # +1 so this seg ends where the next begins
        if len(seg) >= 2:
            segments.append(seg)
        i += seg_size
    out = bytearray()
    for seg in segments:
        plan = _roadplan(seg)
        out += struct.pack("<I", len(plan)) + plan

    preview_offset = len(out) + 4            # position right after the FF FF FF FF marker
    out += b"\xff\xff\xff\xff"
    step = max(1, len(points) // preview_max)
    dots = points[::step]
    for lat, lon in dots:
        blk = _step_preview(lat, lon)
        out += struct.pack("<I", len(blk)) + blk
    return bytes(out), len(segments), preview_offset, len(dots)


# ---- GPX ----
def parse_gpx(path):
    tree = ET.parse(path)
    pts = []
    for el in tree.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("trkpt", "rtept"):
            lat, lon = el.get("lat"), el.get("lon")
            if lat and lon:
                pts.append((float(lat), float(lon)))
    return pts


# ---- BLE transfer ----
def _packets(file_bytes, mtu):
    chunk = max(20, mtu - 17)
    total = len(file_bytes)
    n = (total + chunk - 1) // chunk
    out = []
    for i in range(n):
        part = file_bytes[i * chunk:(i + 1) * chunk]
        hdr = PKT_MAGIC + struct.pack("<IHHB", total, n, i + 1, len(part))
        out.append(hdr + part)
    return out


def _sint32(deg):
    return struct.pack("<i", int(deg * PRECISION / 180.0))


def _haversine_m(a, b):
    r = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _route_info(route_id, total_distance, zoom, crc16, num_steps, size, center, ne, sw,
                preview_offset, preview_count):
    # WriteRouteInfoWithPreviewCommand (40 8d), 54 bytes - matched byte-for-byte against a real
    # OneLap capture (the C406 Pro supports preview, so the app uses this variant):
    #  40 8d <routeId u32LE> <totalDistance u32LE> <zoom u8> <crc u16LE> <stepCount u8>
    #        <byteSize u32LE> <centerLon s32> <centerLat s32> <NElon s32> <NElat s32>
    #        <SWlon s32> <SWlat s32> <dotsAngle u32LE> <previewOffset u32LE> <previewPointSize u32LE>
    # (coords = int(deg * 2^31 / 180); zoom = int(log2(86400/(lonSpan*256)) - 3);
    #  stepCount = number of RoadPlan blocks). No preview data for a GPX -> the last 12 bytes are 0.
    b = bytearray(54)
    b[0] = 0x40
    b[1] = 0x8d
    struct.pack_into("<I", b, 2, route_id & 0xFFFFFFFF)
    struct.pack_into("<I", b, 6, int(total_distance) & 0xFFFFFFFF)
    b[10] = zoom & 0xFF
    struct.pack_into("<H", b, 11, crc16 & 0xFFFF)
    b[13] = num_steps & 0xFF
    struct.pack_into("<I", b, 14, size & 0xFFFFFFFF)
    b[18:22] = _sint32(center[1])
    b[22:26] = _sint32(center[0])
    b[26:30] = _sint32(ne[1])
    b[30:34] = _sint32(ne[0])
    b[34:38] = _sint32(sw[1])
    b[38:42] = _sint32(sw[0])
    struct.pack_into("<I", b, 42, preview_offset & 0xFFFFFFFF)   # previewOffset
    struct.pack_into("<I", b, 46, preview_count & 0xFFFFFFFF)    # previewPointSize
    # @50 (rotate angle) left 0
    return bytes(b)


async def send_route(address, gpx_path, name):
    points = parse_gpx(gpx_path)
    if len(points) < 2:
        return {"ok": False, "error": "GPX has fewer than 2 points"}
    file_bytes, num_steps, preview_offset, preview_count = build_route_file(points)
    crc16 = _fit_crc16(file_bytes)

    client = await _connect(address)
    try:
        try:
            await client._backend._acquire_mtu()
        except Exception:
            pass
        mtu = getattr(client, "mtu_size", 23) or 23
        packets = _packets(file_bytes, mtu)
        packet_total = sum(len(p) for p in packets)
        lats = [p[0] for p in points]
        lons = [p[1] for p in points]
        ne = (max(lats), max(lons))          # north-east corner
        sw = (min(lats), min(lons))          # south-west corner
        center = ((ne[0] + sw[0]) / 2.0, (ne[1] + sw[1]) / 2.0)
        lon_span = max(1e-6, ne[1] - sw[1])
        zoom = int(math.log(86400.0 / (lon_span * 256.0)) / math.log(2.0) - 3.0)
        total_distance = sum(_haversine_m(points[i], points[i + 1]) for i in range(len(points) - 1))
        route_id = int(time.time()) & 0x7FFFFFFF

        # Flow control: after the 40 8d handshake, the device grants a credit window via
        # 40 8c 00 <n> (setRequestNum += n); we may send packets while position < credit, then
        # wait for the device to grant more. Errors surface as other 40 xx status commands.
        state = {"credit": 0, "error": None, "replies": []}

        def _on02(_, d):
            b = bytes(d)
            state["replies"].append(b.hex(" "))
            if len(b) >= 2 and b[1] == 0x8c:            # next-package credit grant
                status = b[2] if len(b) > 2 else 0
                if status != 0:
                    state["error"] = f"device nack (0x8c status {status})"
                    return
                state["credit"] += (b[3] if len(b) > 3 else 1)

        await client.start_notify(CC02, _on02)

        # 1) route-info handshake
        info = _route_info(route_id, total_distance, zoom, crc16, num_steps,
                           packet_total, center, ne, sw, preview_offset, preview_count)
        await client.write_gatt_char(CC02, info, response=True)

        # 2) packet stream on CC03. Respect the device's credit window when it grants one
        # (40 8c); if it never grants credit (non-highSpeed devices just want a stream), fall
        # back to ungated streaming after a short grace period.
        await asyncio.sleep(1.5)                      # give the first credit grant a chance
        ungated = state["credit"] == 0
        pos = 0
        stalled = 0.0
        while pos < len(packets) and state["error"] is None:
            if ungated or pos < state["credit"]:
                await client.write_gatt_char(CC03, packets[pos], response=False)
                pos += 1
                stalled = 0.0
                await asyncio.sleep(0.015)
            else:
                await asyncio.sleep(0.05)
                stalled += 0.05
                if stalled > 8.0:
                    ungated = True                   # stop waiting; just stream the rest

        # 3) end
        await asyncio.sleep(0.3)
        await client.write_gatt_char(CC02, b"\x40\x52", response=True)
        await asyncio.sleep(0.6)
        await client.stop_notify(CC02)
        return {"ok": state["error"] is None, "error": state["error"],
                "points": len(points), "fileBytes": len(file_bytes),
                "packetsSent": pos, "packets": len(packets), "mtu": mtu,
                "routeId": route_id, "deviceReplies": state["replies"][-8:]}
    finally:
        await client.disconnect()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["send"])
    ap.add_argument("--address", required=True)
    ap.add_argument("--gpx", required=True)
    ap.add_argument("--name", default="route")
    args = ap.parse_args()
    print(json.dumps(asyncio.run(send_route(args.address, args.gpx, args.name))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
