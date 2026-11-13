#!/usr/bin/env python3
"""The byte after the satellite count in a gps_base record is a signal mean, not an HDOP.

openambit named it `hdop` with a question mark. The Traverse 2.0.22 program fills it with the mean
signal (dB-Hz) of the five strongest satellites (FUN_00011936, read 2026-11-13), and the logs
agree record for record: 2,543 of 2,543 in a real Traverse's 37 moves (firmware 1.0.4) and 627 of
627 in Ambit3 Peak logs (2.4.17). The reader calls it `snr_top5_mean`.

The logs with satellites are private backups; when they are not on this machine only the name is
checked, on a record built by hand.

    python3 tools/test_gps_base_signal.py
"""
import glob
import json
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exercise_log as EL  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRAVERSE = os.path.join(ROOT, "backups", "traverse-fw1.0.4-pristine")
PEAK_LOGS = sorted(glob.glob(os.path.join(ROOT, "backups", "watch-*", "regions", "ExerciseLog.bin")))


def fixes(data, **where):
    """Every gps_base record of a saved ExerciseLog region."""
    for _header, samples in EL.walk_entries(data, **where):
        for s in samples:
            if s["type"] == "gps_base":
                yield s


def mean_of_best_five(fix):
    return sum(sorted((q["snr"] for q in fix["satellites"]), reverse=True)[:5]) // 5


class GpsBaseSignal(unittest.TestCase):
    def test_the_reader_names_the_byte_for_what_it_is(self):
        # A gps_base sample built by hand: type 3, time, kind 0x0f, nav valid and type, UTC date and
        # time, latitude, longitude, altitude, speed, heading, EHPE, satellites used, THE BYTE,
        # then 4 bytes per satellite (number, status, a spare byte, signal).
        signals = [44, 42, 41, 39, 37, 35]
        body = struct.pack("<BIBHHHBBBBHiiiHHIBB", 3, 1000, 0x0F, 0, 4, 2026, 11, 8, 6, 30, 0,
                           460207000, 77491000, 161000, 0, 0, 300, len(signals), sum(signals[:5]) // 5)
        body += b"".join(bytes([n + 1, 0xBF, 0, snr]) for n, snr in enumerate(signals))
        samples = []
        EL.parse_sample(struct.pack("<H", len(body)) + body, 0, [0], samples, [])
        fix = samples[0]
        self.assertEqual((fix["type"], fix["noofsatellites"], len(fix["satellites"])), ("gps_base", 6, 6))
        self.assertEqual(fix["snr_top5_mean"], mean_of_best_five(fix))
        self.assertNotIn("hdop", fix)

    @unittest.skipUnless(os.path.exists(os.path.join(TRAVERSE, "ExerciseLog.bin")),
                         "the real Traverse backup is not on this machine")
    def test_a_real_traverse_log_stores_the_mean_of_the_five_strongest(self):
        region = json.load(open(os.path.join(TRAVERSE, "manifest.json")))["regions"]["ExerciseLog"]
        with open(os.path.join(TRAVERSE, "ExerciseLog.bin"), "rb") as f:
            got = [x for x in fixes(f.read(), mem_start=int(region["base"], 16), mem_size=region["size"])
                   if x["satellites"]]
        self.assertGreater(len(got), 2000)
        self.assertEqual([x["snr_top5_mean"] for x in got], [mean_of_best_five(x) for x in got])

    @unittest.skipUnless(PEAK_LOGS, "no Ambit3 log backup on this machine")
    def test_an_ambit3_log_stores_the_same(self):
        checked = 0
        for path in PEAK_LOGS:              # many backups hold an empty log: stop at the first with fixes
            with open(path, "rb") as f:
                for x in fixes(f.read()):
                    if any(q["snr"] for q in x["satellites"]):
                        self.assertEqual(x["snr_top5_mean"], mean_of_best_five(x))
                        checked += 1
            if checked > 100:
                break
        if not checked:
            self.skipTest("the Ambit3 log backups here hold no move with satellites")


if __name__ == "__main__":
    unittest.main()
