#!/usr/bin/env python3
"""Route waypoints in both layouts, without a watch.

The Ambit3 Peak/Sport/Run/Vertical keep a route waypoint in 52 bytes (header magic 0x0334); the
Traverse / Traverse Alpha in 54 (magic 0x0336, an int16 altitude after the longitude). Source:
SuuntoLink's own WaypointsAreaConverter and the Traverse 2.0.22 firmware; see ambit_format.py.
No Traverse capture with route waypoints exists, so this checks that what Sommet writes reads
back - names, pins, CRC - through the same reader the "keep existing routes" export uses.
The 52-byte path is held to the real captures by c_reference.py (make -C csrc test).
"""
import io
import contextlib
import os
import sys
import tempfile
import pathlib

import ambit_format as F
import route_pins
import write_nav
from build_route import route_from_gpx, serialize


def _route_with_pins():
    walk = route_pins._synthetic(route_pins.TEST_WALK)
    res = route_pins.build(walk, target="ambit", name="Pins test")
    assert res["ok"], res
    with tempfile.NamedTemporaryFile("w", suffix=".gpx", delete=False) as f:
        f.write(res["gpx"])
    try:
        route = route_from_gpx(pathlib.Path(f.name), 0, 0, 0, (1, 1, 0, 0, 0, 0))
    finally:
        os.unlink(f.name)
    return route


def test_both_layouts_read_back():
    route = _route_with_pins()
    names = [F.decode_name(F.encode_name(w[2], "utf-8")) for w in route.waypoints]
    assert len(names) >= 4 and any(n.startswith("Turn back") for n in names), names
    for size, magic in ((52, 0x0334), (54, 0x0336)):
        flash, _layout = serialize([route], size)
        header = F.WaypointHeader.parse(flash.read(F.WAYPOINT_BASE, 6))
        assert header.magic == magic and header.size == size and header.count == len(names)
        blob = flash.read(F.WAYPOINT_DESC, size * header.count)
        assert F.crc16_ccitt_false(blob) == header.checksum
        with contextlib.redirect_stdout(io.StringIO()):
            route_ok, wpt_ok = write_nav.show_navigation(flash)
        assert route_ok and wpt_ok, size
        gpx = write_nav.route_to_gpx(flash, 0)
        for n in names:   # every pin survives the "keep existing routes" export
            assert f"<name>{n}</name>" in gpx, (size, n)
        assert gpx.count("<type>Waypoint</type>") == len(names)


def test_traverse_altitude():
    route = _route_with_pins()
    route.points = [(lat, lon, 123) for lat, lon, _ in route.points]
    flash, _ = serialize([route], 54)
    w = F.WaypointDescriptor.parse(flash.read(F.WAYPOINT_DESC, 54))
    assert w.altitude == 123 and w.route_name == "Pins test", (w.altitude, w.route_name)
    route.points = [(lat, lon, None) for lat, lon, _ in route.points]
    flash, _ = serialize([route], 54)
    raw = flash.read(F.WAYPOINT_DESC + 8, 2)
    assert int.from_bytes(raw, "little") == F.ALTITUDE_NONE   # what SuuntoLink writes for none


def test_waypoint_type_is_the_plain_one_of_each_watch():
    """A route waypoint's type byte is read against the same list as a POI's: on a Traverse the
    one routine that draws a point (FUN_000190f6 of the 2.0.22 program, record byte 0x32) serves
    the route-waypoint list and the POI list alike, through its 48-type table. There 17 is
    "Hill" (a mountain icon); the plain flag is 26 ("POI"). On an Ambit3 it is 17 ("Waypoint")."""
    route = _route_with_pins()
    for size, plain in ((52, F.WAYPOINT_TYPE_DEFAULT), (54, F.TRAVERSE_WAYPOINT_TYPE_DEFAULT)):
        flash, _ = serialize([route], size)
        count = F.WaypointHeader.parse(flash.read(F.WAYPOINT_BASE, 6)).count
        assert count >= 2
        types = {F.WaypointTail.parse(F.WaypointDescriptor.parse(
            flash.read(F.WAYPOINT_DESC + size * i, size)).tail).type for i in range(count)}
        assert types == {plain}, (size, types)
    assert (F.WAYPOINT_TYPE_DEFAULT, F.TRAVERSE_WAYPOINT_TYPE_DEFAULT) == (17, 26)


def test_reset_and_size_choice():
    assert write_nav.build_reset(54)[1][0][2] == bytes.fromhex("36030000ffff")
    assert write_nav.build_reset()[1][0][2] == F.WAYPOINT_HEADER_RESET

    class Rehearsal:
        dry_run = True

        def __init__(self, pid):
            self.product_id = pid

    assert write_nav.waypoint_size(Rehearsal(0x002B)) == 54      # Traverse
    assert write_nav.waypoint_size(Rehearsal(0x002D)) == 54      # Traverse Alpha
    for pid in (0x001B, 0x001C, 0x001E, 0x002C, None):
        assert write_nav.waypoint_size(Rehearsal(pid)) == 52


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("route waypoints: both layouts OK")
    sys.exit(0)
