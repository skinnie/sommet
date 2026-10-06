#!/usr/bin/env python3
"""Builds the Ambit3 navigation database from GPX files, and compares it to a capture.

    ./tools/build_route.py --compare "assets/ambit3 pcap/route12km" \\
        "assets/ambit3 pcap/Gare-du-Nord-to-114-Av.-André-Morizet.gpx"

Four values are not derivable from a GPX: `distance`, `ascent`, `descent` and the
route timestamp. They come from the application. In --compare mode they are taken
from the capture, which makes it possible to demand byte-for-byte identity on
everything else.
"""

import argparse
import struct
import sys

import ambit_format as F
from ambit_pcap import FlashImage, messages, tails, write_packs
from ambit_simplify import simplify_route
from regen_route import read_gpx

CHUNK = 1024


class Route:
    def __init__(self, name, points, waypoints, distance, ascent, descent, stamp):
        self.name = name
        self.points = points          # [(lat, lon, ele|None)] after simplification
        self.waypoints = waypoints    # [(lat, lon, name, point_index)]
        self.distance = distance
        self.ascent, self.descent = ascent, descent
        self.stamp = stamp            # (month, day, hour, minute, second, seconds)


def route_from_gpx(path, distance, ascent, descent, stamp, max_points=None):
    name, gpx, gpx_waypoints = read_gpx(path)
    if not gpx:
        # Reached by passing a capture where a GPX was meant, which is easy to do since
        # both live in the same folder. Without this it fails later on an empty bbox.
        raise ValueError(f"{path}: no <rtept>, so this is not a route GPX")
    max_points = max_points or F.MAX_ROUTE_POINTS

    # The waypoints are the ones of type "Waypoint"; each matches a point of the
    # track, which must survive the simplification.
    forced, tagged = [], []
    cursor = 0
    for lat, lon, wname, wtype in gpx_waypoints:
        if wtype != "Waypoint":
            continue
        # Waypoints come in route order, so look forward from the previous match first: on a
        # loop the last waypoint shares its coordinates with the first point, and a search from
        # the top would pin it to the start. Falls back to the whole line for an unordered file.
        hit = lambda i: abs(gpx[i][0] - lat) < 1e-7 and abs(gpx[i][1] - lon) < 1e-7
        match = next((i for i in range(cursor, len(gpx)) if hit(i)), None)
        if match is None:
            match = next((i for i in range(len(gpx)) if hit(i)), None)
        if match is not None:
            cursor = match
        if match is None:
            raise ValueError(f"waypoint {wname!r} missing from the track")
        forced.append(match)
        tagged.append((match, lat, lon, wname))

    if not tagged:
        # A route with zero attached waypoints writes fine but never appears in the
        # watch's own Navigation menu - confirmed on hardware 2026-08-04, see
        # HANDOFF_ANDRE.md. Most real-world GPX (a plain track/route with no named
        # Suunto/Komoot waypoints) supply none, so without this every such route
        # would silently fail to show up despite writing without error. Synthesize
        # the two the watch actually needs: start and end.
        for i, wname in ((0, "Start"), (len(gpx) - 1, "End")):
            lat, lon = gpx[i][0], gpx[i][1]
            forced.append(i)
            tagged.append((i, lat, lon, wname))

    kept = simplify_route(gpx, max_points, forced)
    if kept is None:
        raise ValueError(f"route {name!r} cannot be simplified below {max_points} points")
    position = {src: rank for rank, src in enumerate(kept)}
    waypoints = [(lat, lon, wname, position[src]) for src, lat, lon, wname in tagged]
    return Route(name, [gpx[i] for i in kept], waypoints,
                 distance, ascent, descent, stamp)


def serialize(routes):
    """Returns (flash image, layout), the layout being the ordered list of
    (address, bytes) as SuuntoLink writes it. Do not derive it from
    FlashImage.ranges(), which merges adjacent writes: the route header and the
    descriptor table are contiguous but go out as two 0x0b16."""
    flash = FlashImage()

    all_points = [p for r in routes for p in r.points]
    descriptors, points_blob = [], b""
    cursor = 0
    waypoint_rows, index_rows = [], []
    for number, route in enumerate(routes, start=1):
        mid_lat, mid_lon = F.bbox_mid(route.points)
        xy = [F.relative_xy(mid_lat, mid_lon, p[0], p[1]) for p in route.points]
        reld = F.relative_distances([(p[0], p[1]) for p in route.points])
        records = b""
        for (x, y), point, rel in zip(xy, route.points, reld):
            altitude = F.ALTITUDE_NONE if point[2] is None else int(point[2])
            records += F.RoutePoint(x, y, altitude, rel).build()

        descriptors.append(F.RouteDescriptor(
            name=route.name, start_index=cursor, point_count=len(route.points),
            distance=route.distance, mid_lat=mid_lat, mid_lon=mid_lon,
            max_x=max(p[0] for p in xy), max_y=max(p[1] for p in xy),
            ascent=route.ascent, descent=route.descent).build())
        points_blob += records

        month, day, hour, minute, second, seconds = route.stamp
        index_rows.append((number, seconds, len(route.waypoints)))
        for rank, (lat, lon, wname, point_index) in enumerate(route.waypoints):
            tail = F.WaypointTail(month, day, hour, minute, second, rank).build()
            waypoint_rows.append((
                F.WaypointDescriptor(round(lat * 1e7), round(lon * 1e7), wname,
                                     route.name, tail).build(),
                point_index, number))
        cursor += len(route.points)

    desc_blob = b"".join(descriptors)
    # SuuntoLink lays out the waypoint descriptor TABLE in the reverse order of the
    # routes, but the INDEX table in their direct order: the two are therefore not in
    # correspondence when there is more than one route. Reproduced as is, to stay
    # identical to the reference; it is most likely a SuuntoLink defect, with no
    # effect on the route itself.
    by_route = {}
    for row in waypoint_rows:
        by_route.setdefault(row[2], []).append(row)
    order = list(dict.fromkeys(row[2] for row in waypoint_rows))
    desc_rows = [row for key in reversed(order) for row in by_route[key]]
    wpt_blob = b"".join(row[0] for row in desc_rows)
    wpt_index = b"".join(struct.pack("<I", row[1]) for row in waypoint_rows)

    # @12 of the route index designates the rank in the DESCRIPTOR table, and @15
    # the number of waypoint descriptors placed AFTER this route's block.
    starts, cursor_wpt = {}, 0
    for key in reversed(order):
        starts[key] = cursor_wpt
        cursor_wpt += len(by_route[key])
    total_wpt = len(waypoint_rows)
    index_blob = b"".join(
        F.RouteIndexEntry(
            number=n, timestamp=ts, waypoint_start=starts.get(n, 0),
            waypoint_count=count,
            extra=(0, total_wpt - starts.get(n, 0) - count)).build()
        for n, ts, count in index_rows)

    layout = [
        # waypoint group, closed by its 0x0b18
        ("waypoint header", F.WAYPOINT_BASE,
         F.WaypointHeader.build_for(wpt_blob, len(waypoint_rows))),
        ("waypoint descriptors", F.WAYPOINT_DESC, wpt_blob),
        ("tail", F.WAYPOINT_BASE, None),
        # route group
        ("route header", F.ROUTE_BASE, F.RouteHeader(
            len(routes), len(all_points),
            F.crc16_ccitt_false(desc_blob + points_blob)).build()),
        ("route descriptors", F.ROUTE_DESC, desc_blob),
        ("point body", F.ROUTE_POINTS, points_blob),
        ("route index", F.ROUTE_INDEX, index_blob),
        ("waypoint index", F.WAYPOINT_INDEX, wpt_index),
        ("tail", F.ROUTE_BASE, None),
    ]
    for _, addr, blob in layout:
        if blob:
            flash.write(addr, blob)
    return flash, layout


def emit_packs(flash, layout):
    """Sequence (command, address, body) to emit, in SuuntoLink's order."""
    out = []
    for label, addr, blob in layout:
        if blob is None:
            out.append((0x0B18, addr, F.region_hash(flash, addr).encode("ascii")))
            continue
        for off in range(0, len(blob), CHUNK):
            out.append((0x0B16, addr + off, blob[off:off + CHUNK]))
    return out


def stamp_from_capture(flash, msgs, index):
    """Recovers from the capture the four values supplied by the application."""
    d = F.RouteDescriptor.parse(flash.read(F.ROUTE_DESC + 52 * index, 52))
    entry = F.RouteIndexEntry.parse(flash.read(F.ROUTE_INDEX + 20 * index, 20))
    header = F.WaypointHeader.parse(flash.read(F.WAYPOINT_BASE, 6))
    tail = None
    for i in range(header.count):
        w = F.WaypointDescriptor.parse(flash.read(F.WAYPOINT_DESC + 52 * i, 52))
        if w.route_name == F.decode_name(F.encode_name(d.name, "utf-8")):
            tail = F.WaypointTail.parse(w.tail)
            break
    if tail is None:
        raise ValueError("no waypoint attached to this route")
    return (d.distance, d.ascent, d.descent,
            (tail.month, tail.day, tail.hour, tail.minute, tail.second,
             entry.timestamp))


def compare(capture, gpx_paths):
    msgs = messages(capture)
    reference = FlashImage(write_packs(msgs))
    header = F.RouteHeader.parse(reference.read(F.ROUTE_BASE, 32))
    if header.route_count != len(gpx_paths):
        print(f"  FAIL  the capture carries {header.route_count} route(s), "
              f"{len(gpx_paths)} GPX supplied")
        return False

    routes = []
    for i, path in enumerate(gpx_paths):
        distance, ascent, descent, stamp = stamp_from_capture(reference, msgs, i)
        routes.append(route_from_gpx(path, distance, ascent, descent, stamp))
    built, layout = serialize(routes)

    ok = True
    for label, addr, blob in layout:
        if blob is None:
            continue
        want = reference.read(addr, len(blob))
        same = blob == want
        ok &= same
        print(f"  {'OK   ' if same else 'FAIL '} {label} ({len(blob)} B)")
        if not same:
            first = next(i for i in range(len(blob)) if blob[i] != want[i])
            print(f"        first difference at offset {first}: "
                  f"{blob[first:first + 12].hex(' ')} against {want[first:first + 12].hex(' ')}")

    for addr, _, want in tails(msgs):
        if addr not in (F.ROUTE_BASE, F.WAYPOINT_BASE):
            continue
        got = F.region_hash(built, addr)
        ok &= got == want
        print(f"  {'OK   ' if got == want else 'FAIL '} "
              f"closing hash {F.REGIONS[addr][0]}")

    # The packet sequence must match command by command, in the chronological order
    # of the capture.
    generated = [(cmd, addr, len(body)) for cmd, addr, body in emit_packs(built, layout)]
    captured = []
    for m in msgs:
        if m.incoming or m.command not in (0x0B16, 0x0B18) or len(m.payload) < 8:
            continue
        addr, = struct.unpack("<I", m.payload[:4])
        captured.append((m.command, addr,
                         struct.unpack("<H", m.payload[4:6])[0] if m.command == 0x0B16
                         else len(m.payload) - 8))
    same_seq = generated == captured
    ok &= same_seq
    print(f"  {'OK   ' if same_seq else 'FAIL '} sequence of {len(generated)} packets "
          f"(command, address, length)")
    if not same_seq:
        for i, (g, c) in enumerate(zip(generated, captured)):
            if g != c:
                print(f"        divergence at packet {i}: generated "
                      f"0x{g[0]:04x}@0x{g[1]:06x}/{g[2]} against captured "
                      f"0x{c[0]:04x}@0x{c[1]:06x}/{c[2]}")
                break
        if len(generated) != len(captured):
            print(f"        {len(generated)} packets generated against {len(captured)}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gpx", nargs="+")
    ap.add_argument("--compare", metavar="CAPTURE", required=True)
    args = ap.parse_args()
    print(f"### {args.compare}")
    return 0 if compare(args.compare, args.gpx) else 1


if __name__ == "__main__":
    sys.exit(main())
