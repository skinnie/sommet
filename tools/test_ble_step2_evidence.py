#!/usr/bin/env python3
"""Fixes that came from Suunto's own material (tracker watch-protocol/05 step 2, 2026-11-22;
the evidence is in sommet-builds/re-ble-step2/FINDINGS.md). No watch, no network.

- A route write looks at the two areas it writes to, nothing else: a Traverse (whose logs are
  elsewhere than a Peak's) is not refused, a watch whose Routes area is missing or elsewhere is.
- GPS data over Bluetooth goes where the watch's own memory map says, as on the cable.
- Marking a move as synced over Bluetooth sends the bytes SuuntoLink sends.

    python3 tools/test_ble_step2_evidence.py
"""
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ambit_format as F  # noqa: E402
import ble_sgee  # noqa: E402
import mark_synced  # noqa: E402
import write_nav as W  # noqa: E402

# What each family declares (0x0b21), from captures and each firmware's own table.
COMMON = {"Waypoints": (0x005000, 16384), "Routes": (0x14C080, 130000),
          "GpsSGEE": (0x0704E0, 140000), "EventLog": (0x0C3500, 400000)}
PEAK = {**COMMON, "TrainingProgram": (0x001000, 3072), "CustomModes": (0x002000, 12288),
        "Apps": (0x0927C0, 200000), "ExerciseLog": (0x27AC40, 5526464)}
TRAVERSE = {**COMMON, "CustomModes": (0x002000, 12288), "Apps": (0x0927C0, 200000),
            "GlonassSGEE": (0x1339E0, 100000), "TrackLog": (0x2477F0, 100000),
            "ExerciseLog": (0x2B7CD0, 5276464)}
KAILASH = {**COMMON, "GlonassSGEE": (0x1339E0, 100000), "TrackLog": (0x48A1C0, 1310713)}


class RouteWriteAndTheMemoryMap(unittest.TestCase):
    def test_our_table_has_the_two_areas_where_every_watch_declares_them(self):
        ours = {name: (base, size) for base, (name, size, _) in F.REGIONS.items()}
        self.assertEqual(ours["Waypoints"], COMMON["Waypoints"])
        self.assertEqual(ours["Routes"], COMMON["Routes"])

    def test_every_family_may_be_written(self):
        for name, found in (("Peak", PEAK), ("Traverse", TRAVERSE), ("Kailash", KAILASH)):
            self.assertIsNone(W.nav_map_error(found), name)

    def test_the_older_check_let_a_watch_without_a_routes_area_through(self):
        """check_memory_map() skips what the watch does not declare; a route write must not."""
        import contextlib
        import io
        no_routes = {k: v for k, v in PEAK.items() if k != "Routes"}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(W.check_memory_map(no_routes))
        self.assertIsNotNone(W.nav_map_error(no_routes))

    def test_a_missing_or_moved_area_is_refused_in_words(self):
        no_routes = {k: v for k, v in PEAK.items() if k != "Routes"}
        self.assertIn("does not declare a Routes area", W.nav_map_error(no_routes))
        moved = {**PEAK, "Routes": (0x150000, 130000)}
        self.assertIn("keeps Routes at 0x150000", W.nav_map_error(moved))
        smaller = {**PEAK, "Waypoints": (0x005000, 8192)}
        self.assertIn("Waypoints", W.nav_map_error(smaller))


class Link:
    """Enough of a link for ble_sgee.write_orbit: answers the memory map, records the writes."""
    dry_run = False

    def __init__(self, regions):
        self.regions = regions
        self.sent = []

    def command(self, command, payload=b"", **_):
        self.sent.append((command, bytes(payload)))
        return b""


class GpsDataOverBluetooth(unittest.TestCase):
    def setUp(self):
        self.saved = ble_sgee.read_memory_map

    def tearDown(self):
        ble_sgee.read_memory_map = self.saved

    def write(self, regions, glonass=False):
        link = Link(regions)
        asked = []
        ble_sgee.read_memory_map = lambda l: asked.append(l) or dict(regions)
        ble_sgee.write_orbit(link, b"\x01" * 3000, glonass=glonass)
        return link, asked

    def test_the_address_is_the_one_the_watch_declares(self):
        link, asked = self.write(PEAK)
        self.assertEqual(len(asked), 1)                       # the map is read, as SuuntoLink does
        writes = [p for c, p in link.sent if c == W.CMD_DATA_WRITE]
        self.assertEqual(int.from_bytes(writes[0][:4], "little"), 0x0704E0)
        self.assertEqual([c for c, _ in link.sent if c == W.CMD_NAV_COMMIT], [])   # no commit

    def test_an_address_no_firmware_declares_is_refused_before_a_byte(self):
        elsewhere = {**PEAK, "GpsSGEE": (0x080000, 140000)}
        link = Link(elsewhere)
        ble_sgee.read_memory_map = lambda _l: dict(elsewhere)
        with self.assertRaises(RuntimeError) as e:
            ble_sgee.write_orbit(link, b"\x01" * 3000)
        self.assertIn("0x080000", str(e.exception))
        self.assertEqual(link.sent, [])

    def test_glonass_goes_to_its_own_area(self):
        link, _ = self.write(TRAVERSE, glonass=True)
        writes = [p for c, p in link.sent if c == W.CMD_DATA_WRITE]
        self.assertEqual(int.from_bytes(writes[0][:4], "little"), 0x1339E0)

    def test_a_watch_without_the_area_gets_nothing(self):
        for regions, glonass in (({k: v for k, v in PEAK.items() if k != "GpsSGEE"}, False), (PEAK, True)):
            link = Link(regions)
            ble_sgee.read_memory_map = lambda _l, r=regions: dict(r)
            with self.assertRaises(RuntimeError):
                ble_sgee.write_orbit(link, b"\x01" * 3000, glonass=glonass)
            self.assertEqual(link.sent, [])


class MarkAsSynced(unittest.TestCase):
    def test_the_bytes_suuntolink_sends(self):
        """51 messages in the cable captures: 00 00 00 00 01 00, SBEM0102, 8b 15, 20 bytes of
        local time text, 01. 37 bytes."""
        payload = mark_synced.build_marker("2026-11-22T09:25:00", 0x8b)
        self.assertEqual(len(payload), 37)
        self.assertEqual(payload[:6], bytes.fromhex("000000000100"))
        self.assertEqual(payload[6:14], b"SBEM0102")
        self.assertEqual(payload[14:16], bytes([0x8b, 0x15]))
        self.assertEqual(payload[-1], 1)

    def test_over_bluetooth_the_same_prefix(self):
        import inspect
        src = inspect.getsource(mark_synced.mark_ble)
        self.assertIn("prefix=USB_SBEM_PREFIX", src)
        self.assertNotIn("prefix=SBEM_WRITE_PREFIX", src)


if __name__ == "__main__":
    unittest.main()
