#!/usr/bin/env python3
"""Restore from a `nav --save` backup puts back exactly the backup's routes and POIs.

Desktop audit 2026-10-29: Restore rebuilt the routes from whatever was left in the saved
region, so a route removed before the backup came back, and it kept the POIs the watch held
at restore time. These tests build the regions offline with the real serializer (no watch).
"""
import json
import math
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ambit_format as F                                   # noqa: E402
import write_nav as W                                      # noqa: E402
from ambit_pcap import FlashImage                          # noqa: E402


def gpx(name, lat0, lon0, n=30, r=0.004):
    pts = "".join(f'<rtept lat="{lat0 + r * math.sin(2 * math.pi * i / n):.6f}" '
                  f'lon="{lon0 + r * math.cos(2 * math.pi * i / n):.6f}">'
                  f'<ele>{100 + 10 * math.sin(i):.1f}</ele></rtept>' for i in range(n))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" creator="t" xmlns="http://www.topografix.com/GPX/1/1">'
            f'<metadata><name>{name}</name></metadata>'
            f'<wpt lat="{lat0:.6f}" lon="{lon0 + r:.6f}"><name>Start</name>'
            '<type>Waypoint</type></wpt>'
            f'<rte><name>{name}</name>{pts}</rte></gpx>\n')


def apply(watch, layout):
    for _name, address, blob in layout:
        if blob:
            watch.write(address, blob)


class BackupRestoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.prefix = str(self.dir / "b1")
        # The watch: two routes sent, then "Gone" removed (only "Keep" rewritten), which leaves
        # Gone's bytes behind the new headers - what a real removal does.
        watch = FlashImage()
        apply(watch, W.build_routes_from_gpx_texts(
            [gpx("Keep", 50.63, 3.06), gpx("Gone", 50.70, 3.10)])[1])
        apply(watch, W.build_routes_from_gpx_texts([gpx("Keep", 50.63, 3.06)])[1])
        self.routes = watch.read(F.ROUTE_BASE, F.ROUTE_REGION_SIZE, fill=0xFF)
        self.waypoints = watch.read(F.WAYPOINT_BASE, F.WAYPOINT_REGION_SIZE, fill=0xFF)
        self.save(self.prefix, self.routes, self.waypoints)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def save(prefix, routes, waypoints):
        pathlib.Path(f"{prefix}-routes.bin").write_bytes(routes)
        pathlib.Path(f"{prefix}-waypoints.bin").write_bytes(waypoints)

    def test_restore_puts_back_only_the_listed_routes(self):
        self.assertEqual(W.backup_route_names(self.prefix), ["Keep"])
        routes = W.backup_routes_as_gpx(self.prefix)
        self.assertEqual([r["name"] for r in routes], ["Keep"])
        flash, _layout = W.build_routes_from_gpx_texts([r["gpx"] for r in routes])
        header = F.RouteHeader.parse(flash.read(F.ROUTE_BASE, 32))
        self.assertEqual(header.route_count, 1)

    def test_leftover_recovery_still_finds_the_removed_route(self):
        # The old method, kept for undoing a reset, counts what is in the region.
        flash, _layout = W.build_restore(self.prefix)
        self.assertEqual(F.RouteHeader.parse(flash.read(F.ROUTE_BASE, 32)).route_count, 2)

    def test_backup_with_no_routes_restores_none(self):
        empty = FlashImage()
        apply(empty, W.build_reset()[1])
        prefix = str(self.dir / "empty")
        self.save(prefix, empty.read(F.ROUTE_BASE, F.ROUTE_REGION_SIZE, fill=0xFF),
                  empty.read(F.WAYPOINT_BASE, F.WAYPOINT_REGION_SIZE, fill=0xFF))
        self.assertEqual(W.backup_routes_as_gpx(prefix), [])
        flash, layout = W.build_routes_from_gpx_texts([])
        self.assertEqual([n for n, _a, _b in layout], [n for n, _a, _b in W.build_reset()[1]])

    def test_cut_short_backup_is_refused(self):
        prefix = str(self.dir / "short")
        self.save(prefix, self.routes[:100], self.waypoints)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            W.backup_routes_as_gpx(prefix)

    def test_corrupt_backup_is_refused(self):
        bad = bytearray(self.routes)
        bad[F.ROUTE_POINTS - F.ROUTE_BASE + 5] ^= 0x5A
        prefix = str(self.dir / "bad")
        self.save(prefix, bytes(bad), self.waypoints)
        with self.assertRaisesRegex(ValueError, "do not check out"):
            W.backup_routes_as_gpx(prefix)

    def test_pois_from_the_raw_list(self):
        records = [W.build_poi_record("Col", 45.2, 6.3), W.build_poi_record("Cafe", 50.1, 3.1)]
        # A 0x0b24 read as read_pois() returns it: [u32 0][u8][u8], the magic, then one 0x55
        # entry per POI (the layout of the POIs_pre_*.bin files).
        reply = (b"\0\0\0\0\x01\0" + F.SBEM_MAGIC
                 + b"".join(bytes([W.POI_ENTRY, len(r)]) + r for r in records))
        pathlib.Path(f"{self.prefix}-pois.bin").write_bytes(reply)
        kind, data = W.backup_poi_list(self.prefix)
        self.assertEqual(kind, "reply")
        self.assertEqual(W.poi_records_of(data), records)

    def test_pois_from_an_older_sync_snapshot(self):
        items = [{"name": "Col", "type": 17, "lat": 45.2, "lon": 6.3}]
        pathlib.Path(f"{self.prefix}-sync.json").write_text(json.dumps(
            {"categories": {"pois": {"supported": True, "count": 1, "items": items}}}))
        self.assertEqual(W.backup_poi_list(self.prefix), ("list", items))

    def test_backup_without_pois_keeps_the_watch_s(self):
        self.assertEqual(W.backup_poi_list(self.prefix), (None, None))


if __name__ == "__main__":
    unittest.main()
