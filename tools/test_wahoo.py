#!/usr/bin/env python3
"""The Wahoo ELEMNT tools' device-free parts: the .plan encoder (wahoo_workout), the zoom-8 tile
maths (wahoo_maps) and the Bluetooth folder listing parser (wahoo_ble_files)."""
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import wahoo_ble_files as B
import wahoo_maps as M
import wahoo_workout as W


def step(kind, secs=None, metres=None, target=None, lo=None, hi=None):
    st = {"type": {"typeName": kind},
          "duration": {"durationName": "distance", "value": metres} if metres
          else {"durationName": "time", "value": secs}}
    if target:
        st["target"] = {"targetName": target, "valueRange": {"min": lo, "max": hi}}
    return st


class PlanEncoding(unittest.TestCase):
    def test_repeat_becomes_interval_with_subintervals(self):
        text = W.encode({"name": "VO2", "steps": [
            step("warmup", 600, target="power", lo=130, hi=160),
            {"type": {"typeName": "repeatStart", "value": 3}},
            step("interval", 300, target="power", lo=270, hi=250),
            step("recovery", 180, target="cadence", lo=90, hi=90),
            {"type": {"typeName": "repeatEnd"}},
            step("cooldown", 600, target="hr", lo=110, hi=130)]})
        lines = text.splitlines()
        self.assertEqual(lines[:3], ["=HEADER=", "NAME=VO2", "DURATION=%d" % (600 + 3 * 480 + 600)])
        self.assertIn("=STREAM=", lines)
        self.assertEqual(lines.count("=INTERVAL="), 3)
        self.assertEqual(lines.count("=SUBINTERVAL="), 2)
        self.assertIn("REPEAT=3", lines)
        self.assertIn("PWR_LO=250", lines)               # a reversed range is put in order
        self.assertIn("PWR_HI=270", lines)
        self.assertIn("CAD_LO=90", lines)
        self.assertIn("HR_HI=130", lines)
        self.assertIn("MESG_DURATION_SEC>=300?EXIT", lines)

    def test_distance_step_and_no_duration_header(self):
        text = W.encode({"name": "x", "steps": [step("interval", metres=5000)]})
        self.assertIn("MESG_DISTANCE_M>=5000?EXIT", text)
        self.assertNotIn("DURATION=", text)

    def test_speed_target_is_dropped_not_guessed(self):
        text = W.encode({"name": "x", "steps": [step("interval", 60, target="speed", lo=8, hi=9)]})
        self.assertNotIn("SPD_", text)

    def test_nested_repeats_refused(self):
        with self.assertRaises(ValueError):
            W.encode({"name": "x", "steps": [{"type": {"typeName": "repeatStart", "value": 2}},
                                             {"type": {"typeName": "repeatStart", "value": 2}}]})

    def test_safe_name(self):
        self.assertEqual(W.safe_name(' 5x5 "VO2" / hard '), "5x5 VO2  hard")
        with self.assertRaises(ValueError):
            W.safe_name("///")


class Tiles(unittest.TestCase):
    def test_tile_of_known_point(self):
        # Lille (50.63 N, 3.06 E) is in the ELEMNT's tile 130/86.
        self.assertEqual(M.tile_of(50.63, 3.06), (130, 86))

    def test_bounds_contain_their_points(self):
        s, w, n, e = M.tile_bounds(130, 86)
        self.assertTrue(s < 50.63 < n and w < 3.06 < e)

    def test_margin_reaches_neighbour_tile(self):
        s, w, n, e = M.tile_bounds(130, 86)
        near_east = (50.3, e - 0.05)
        self.assertEqual(M.tiles_for_points([near_east], margin_km=0), [(130, 86)])
        self.assertIn((131, 86), M.tiles_for_points([near_east], margin_km=10))

    def test_parse_tiles(self):
        self.assertEqual(M.parse_tiles(["130/86,131/86", " 129/85 "]), [(130, 86), (131, 86), (129, 85)])
        for bad in ("130", "300/1", "a/b"):
            with self.assertRaises(ValueError):
                M.parse_tiles([bad])


class BleListing(unittest.TestCase):
    def test_parse_listing(self):
        path = "/sdcard/exports/"
        body = b"\x00\x00\x00" + b"\x00\x03\x00" + path.encode() + b"\x00" + struct.pack("<H", 2)
        body += b"\x01" + b"ride.fit\x00" + struct.pack("<II", 1790000000, 7900)
        body += b"\x00" + b"sub\x00" + struct.pack("<II", 1790000001, 0)
        files = B._parse_listing(body, path)
        self.assertEqual(files[0], {"name": "ride.fit", "dir": False, "size": 7900, "time": 1790000000})
        self.assertTrue(files[1]["dir"])


if __name__ == "__main__":
    unittest.main()
