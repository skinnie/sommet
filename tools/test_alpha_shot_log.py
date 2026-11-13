#!/usr/bin/env python3
"""A Traverse Alpha's counted shots are read from its Hunting log.

The layout is described here by records built by hand: a shot is an episodic sample (type 3) of
kind 0x20 that carries latitude and longitude (int32, 1e-7 degrees, 0x7FFFFFFF for "no
position"), and a log header of 1509 bytes or more (Traverse firmware 2.0.22) carries the number
of counted shots as a u16 at offset 0xD9.

No Alpha is on hand. The layout was read from the Traverse Alpha 2.0.22 program and seen in the
PC emulator (2026-11-13). The two logs that run saved are not part of this repository: they sit in
a private folder, and the tests that read them (and the mobile app's parser built for this
computer) run only where that folder exists.

    python3 tools/test_alpha_shot_log.py
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import exercise_log as EL

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Private (assets/ is git-ignored): the start of the emulated watch's ExerciseLog region as saved,
# once without GPS (both shots carry "no position") and once at 60.2716 N, 24.9719 E.
DATA = os.path.join(ROOT, "assets", "Firmware", "re-out", "family_emu", "testdata")
LOG = os.path.join(DATA, "alpha_hunting_two_shots_log.bin")
GPS_LOG = os.path.join(DATA, "alpha_hunting_two_shots_gps_log.bin")
HAVE_LOGS = os.path.exists(LOG) and os.path.exists(GPS_LOG)
NO_LOGS = "the emulated logs are private and not on this machine"
TRAVERSE_BASE, TRAVERSE_SIZE = 0x2b7cd0, 5276464   # from the Traverse's 0x0b21 memory map


def hunting_move(path=LOG):
    with open(path, "rb") as f:
        data = f.read()
    entries = list(EL.walk_entries(data, mem_start=TRAVERSE_BASE, mem_size=TRAVERSE_SIZE))
    assert len(entries) == 1
    return entries[0]


def fit_messages(blob, wanted_global):
    """The data messages of one global number in a FIT file, each as {field number: bytes}."""
    size = blob[0]
    end = size + struct.unpack_from("<I", blob, 4)[0]
    pos, defs, out = size, {}, []
    while pos < end:
        head = blob[pos]; pos += 1
        local = head & 0x0F
        if head & 0x40:
            global_no = struct.unpack_from("<H", blob, pos + 2)[0]
            count = blob[pos + 4]; pos += 5
            fields = [(blob[pos + 3 * i], blob[pos + 3 * i + 1]) for i in range(count)]
            pos += 3 * count
            if head & 0x20:
                dev = blob[pos]; pos += 1
                fields += [(None, blob[pos + 3 * i + 1]) for i in range(dev)]
                pos += 3 * dev
            defs[local] = (global_no, fields)
            continue
        global_no, fields = defs[local]
        msg = {}
        for number, length in fields:
            msg[number] = blob[pos:pos + length]; pos += length
        if global_no == wanted_global:
            out.append(msg)
    return out


def shot_sample(time_ms, lat=None, lon=None):
    """One shot sample as bytes: [u16 length][u8 3][u32 time][u8 0x20][i32 lat][i32 lon]."""
    none = EL.NO_POSITION
    body = struct.pack("<BIBii", 3, time_ms, 0x20, none if lat is None else lat, none if lon is None else lon)
    return struct.pack("<H", len(body)) + body


def parsed(sample_bytes):
    samples = []
    EL.parse_sample(sample_bytes, 0, [0], samples, [])
    return samples[0]


def hunting(shots, with_track):
    """A decoded Hunting move (header and samples as the reader gives them) with these shots:
    [(time_ms, lat or None, lon or None)]."""
    raw = bytearray(EL.SHOT_HEADER_LEN)                       # an empty 2.0.22 header ...
    struct.pack_into("<H", raw, EL.SHOT_COUNT_OFFSET, len(shots))
    header = EL.parse_log_header(bytes(raw), 0, len(raw))     # ... read by the reader itself
    header.update({"year": 2026, "month": 11, "day": 8, "hour": 6, "minute": 30, "msec": 0,
                   "duration_ms": 60_000, "distance": 120, "ascent": 3, "descent": 2,
                   "activity_type": 98, "activity_name": "Hunting",
                   "heartrate_avg": 96, "heartrate_max": 131})
    samples = []
    for ms in range(0, 60_001, 10_000):
        values = [{"type": 6, "name": "time", "value": ms}, {"type": 1, "name": "hr", "value": 96}]
        if with_track:
            values += [{"type": 0x0b, "name": "latitude", "value": 460207000 + ms},
                       {"type": 0x0c, "name": "longitude", "value": 77491000}]
        samples.append({"type": "periodic", "time": ms, "values": values})
    for ms, lat, lon in shots:
        samples.append(parsed(shot_sample(ms, lat, lon)))
    return header, sorted(samples, key=lambda s: s["time"])


class ShotRecords(unittest.TestCase):
    """The layout, on records built by hand."""

    def test_a_shot_sample_carries_where_the_watch_was(self):
        shot = parsed(shot_sample(5547, 460207000, 77491000))
        self.assertEqual((shot["type"], shot["time"], shot["latitude"], shot["longitude"]),
                         ("shot", 5547, 460207000, 77491000))

    def test_a_shot_counted_without_a_fix_has_no_position(self):
        shot = parsed(shot_sample(3165))
        self.assertEqual((shot["type"], shot["latitude"], shot["longitude"]), ("shot", None, None))

    def test_another_length_is_not_taken_for_a_shot(self):
        body = struct.pack("<BIBi", 3, 100, 0x20, 1)
        self.assertNotEqual(parsed(struct.pack("<H", len(body)) + body).get("type"), "shot")

    def test_only_the_longer_header_carries_a_shot_count(self):
        long_header = bytearray(EL.SHOT_HEADER_LEN)
        struct.pack_into("<H", long_header, EL.SHOT_COUNT_OFFSET, 2)
        self.assertEqual(EL.parse_log_header(bytes(long_header), 0, len(long_header))["shots"], 2)
        self.assertNotIn("shots", EL.parse_log_header(bytes(1497), 0, 1497))    # Ambit3, Traverse 1.0.4

    def test_the_gpx_carries_the_count_and_a_waypoint_per_positioned_shot(self):
        gpx = EL.to_gpx(*hunting([(20_000, 460207000, 77491000), (21_500, None, None)], with_track=True))
        self.assertIn("<shots>2</shots>", gpx)
        self.assertEqual(gpx.count('<wpt lat="46.0207000" lon="7.7491000">'), 1)
        self.assertEqual(gpx.count("<type>shot</type>"), 1)       # the one without a position: count only
        self.assertLess(gpx.index("<wpt "), gpx.index("<trk>"))   # GPX order: wpt before trk
        self.assertIn("<trk><name>Hunting</name>", gpx)

    def test_the_fit_marks_each_shot_with_a_user_marker_event(self):
        for with_track in (True, False):
            events = fit_messages(EL.to_fit(*hunting([(20_000, None, None), (21_500, None, None)], with_track)), 21)
            # FIT profile: event 32 = user_marker, event_type 3 = marker; data = which shot
            marks = [e for e in events if e[0] == b"\x20" and e[1] == b"\x03"]
            self.assertEqual([struct.unpack("<I", e[3])[0] for e in marks], [1, 2], with_track)


@unittest.skipUnless(HAVE_LOGS, NO_LOGS)
class AlphaShots(unittest.TestCase):
    def test_both_counted_shots_are_read_as_shots(self):
        header, samples = hunting_move()
        self.assertEqual(header["activity_name"], "Hunting")
        shots = [s for s in samples if s["type"] == "shot"]
        self.assertEqual(len(shots), 2)
        # the spikes were 1.5 s apart in the emulator; the log's clock steps are coarser
        self.assertAlmostEqual((shots[1]["time"] - shots[0]["time"]) / 1000, 1.5, delta=0.2)

    def test_a_shot_without_gps_has_no_position(self):
        _, samples = hunting_move()
        for shot in (s for s in samples if s["type"] == "shot"):
            self.assertIsNone(shot["latitude"])
            self.assertIsNone(shot["longitude"])

    def test_a_shot_with_gps_carries_where_the_watch_was(self):
        header, samples = hunting_move(GPS_LOG)
        shots = [s for s in samples if s["type"] == "shot"]
        self.assertEqual(header["shots"], 2)
        self.assertEqual([(s["latitude"], s["longitude"]) for s in shots],
                         [(602716000, 249719000)] * 2)      # 1e-7 degrees, as in gps_base
        self.assertAlmostEqual((shots[1]["time"] - shots[0]["time"]) / 1000, 1.5, delta=0.2)

    def test_the_header_carries_the_shot_count_the_watch_showed(self):
        header, _ = hunting_move()
        self.assertEqual(header["shots"], 2)

    def test_the_gpx_carries_the_shot_count_and_each_shot_as_a_waypoint(self):
        gpx = EL.to_gpx(*hunting_move(GPS_LOG))
        self.assertIn("<shots>2</shots>", gpx)
        self.assertEqual(gpx.count('<wpt lat="60.2716000" lon="24.9719000">'), 2)
        self.assertEqual(gpx.count("<type>shot</type>"), 2)
        self.assertLess(gpx.index("<wpt "), gpx.index("<trk>"))     # GPX order: wpt before trk
        self.assertIn("<trk><name>Hunting</name>", gpx)

    def test_the_gpx_of_shots_without_gps_has_the_count_and_no_waypoint(self):
        gpx = EL.to_gpx(*hunting_move())
        self.assertIn("<shots>2</shots>", gpx)
        self.assertNotIn("<wpt", gpx)

    def test_the_fit_marks_each_shot_with_a_user_marker_event(self):
        header, samples = hunting_move(GPS_LOG)
        events = fit_messages(EL.to_fit(header, samples), 21)
        # FIT profile: event 32 = user_marker, event_type 3 = marker; data = which shot
        marks = [e for e in events if e[0] == b"\x20" and e[1] == b"\x03"]
        self.assertEqual([struct.unpack("<I", e[3])[0] for e in marks], [1, 2])
        shots = [s for s in samples if s["type"] == "shot"]
        garmin_epoch = 631065600
        self.assertEqual([struct.unpack("<I", e[253])[0] for e in marks],
                         [int(s["utc_time"].timestamp()) - garmin_epoch for s in shots])

    def test_the_fit_of_a_move_without_gps_marks_the_shots_too(self):
        events = fit_messages(EL.to_fit(*hunting_move()), 21)
        self.assertEqual(len([e for e in events if e[0] == b"\x20"]), 2)


LIBAMBIT = os.path.join(ROOT, "android", "android", "app", "src", "main", "cpp", "libambit")
HARNESS = os.path.join(ROOT, "csrc", "test", "shot_log_harness.c")


@unittest.skipUnless(HAVE_LOGS, NO_LOGS)
@unittest.skipUnless(shutil.which("gcc") and os.path.isdir(LIBAMBIT), "no gcc or no libambit here")
class MobileAppParser(unittest.TestCase):
    """The mobile app's own log parser (libambit pmem20.c), built for this computer with the
    transport replaced by the file, reads the same shots as the Python reader above."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="shot_harness_")
        cls.exe = os.path.join(cls.dir, "shot_log_harness")
        sources = [HARNESS] + [os.path.join(LIBAMBIT, f) for f in ("pmem20.c", "utils.c", "sha256.c", "debug.c")]
        subprocess.run(["gcc", "-O1", "-w", "-I" + LIBAMBIT, "-o", cls.exe] + sources, check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def parsed(self, path):
        out = subprocess.run([self.exe, path, "%x" % TRAVERSE_BASE, str(TRAVERSE_SIZE)],
                             check=True, capture_output=True, text=True).stdout
        return [line for line in out.splitlines() if line.startswith(("move ", "shot "))]

    def expected(self, path):
        header, samples = hunting_move(path)
        lines = ["move %s has_shots=1 shots=%d" % (header["activity_name"], header["shots"])]
        for s in samples:
            if s["type"] != "shot":
                continue
            lines.append("shot time_ms=%d no position" % s["time"] if s["latitude"] is None else
                         "shot time_ms=%d lat=%d lon=%d" % (s["time"], s["latitude"], s["longitude"]))
        return lines

    def test_shots_without_gps(self):
        self.assertEqual(self.parsed(LOG), self.expected(LOG))
        self.assertEqual(len(self.parsed(LOG)), 3)

    def test_shots_with_gps(self):
        self.assertEqual(self.parsed(GPS_LOG), self.expected(GPS_LOG))
        self.assertIn("lat=602716000 lon=249719000", self.parsed(GPS_LOG)[1])


if __name__ == "__main__":
    unittest.main()
