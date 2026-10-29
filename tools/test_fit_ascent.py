#!/usr/bin/env python3
"""An exported FIT carries the move's climb in session total_ascent / total_descent.

Desktop audit 2026-10-29: a ride the app showed with +7 m exported as a FIT with 0 m of climbing.
The writers put ascent and descent in session fields 25 and 26, which the FIT profile defines as
first_lap_index and num_laps; total_ascent is 22 and total_descent 23 (as android/src/services/
FitExport.ts already had). The desktop writer and both native twins (jni_bridge.cpp, Android;
AmbitUsbModule.mm, iOS) had the same field numbers, so this also checks their session layout.

    python3 tools/test_fit_ascent.py
"""
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import exercise_log as E
import fit_decode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NATIVE = {
    "android": os.path.join(ROOT, "android/android/app/src/main/cpp/jni_bridge.cpp"),
    "ios": os.path.join(ROOT, "android/ios/AmbitBle/AmbitUsbModule.mm"),
}


def header(ascent, descent):
    return {"year": 2026, "month": 10, "day": 4, "hour": 9, "minute": 41, "msec": 0,
            "duration_ms": 495_000, "distance": 2317, "ascent": ascent, "descent": descent,
            "activity_type": 4, "activity_name": "Cycling",
            "heartrate_avg": 130, "heartrate_max": 160}


def periodic(ms, hr):
    return {"type": "periodic", "time": ms,
            "values": [{"type": 6, "name": "time", "value": ms},
                       {"type": 1, "name": "hr", "value": hr}]}


def decode(blob):
    with tempfile.NamedTemporaryFile(suffix=".fit", delete=False) as f:
        f.write(blob)
        path = f.name
    try:
        return fit_decode.decode(path)
    finally:
        os.unlink(path)


def session_fields(src):
    """The field numbers of the session (global 18) definition in a native twin."""
    body = src[src.index("// session (local 2, global 18)"):]
    body = body[:body.index("b.def(2, 18")]
    return [int(n) for n in re.findall(r"\{(\d+),\s*\d+,\s*F", body)]


class FitAscent(unittest.TestCase):
    def test_gps_move_keeps_its_climb(self):
        start = datetime(2026, 10, 4, 9, 41, tzinfo=timezone.utc)
        points = [{"time": start + timedelta(seconds=10 * i), "lat": 41.0 + i * 1e-4,
                   "lon": -8.0, "ele": 100.0 + i} for i in range(20)]
        real = E.extract_track_points
        E.extract_track_points = lambda h, s: points
        try:
            blob = E.to_fit(header(7, 5), [])
        finally:
            E.extract_track_points = real
        self.assertEqual(decode(blob)["ascentMeters"], 7.0)

    def test_indoor_move_keeps_its_climb(self):
        samples = [periodic(ms, 120 + ms // 60_000) for ms in range(0, 600_000, 10_000)]
        self.assertEqual(decode(E.to_fit(header(12, 9), samples))["ascentMeters"], 12.0)

    def test_native_twins_use_total_ascent_and_descent(self):
        for name, path in NATIVE.items():
            with self.subTest(twin=name):
                with open(path, encoding="utf-8") as fh:
                    fields = session_fields(fh.read())
                self.assertIn(22, fields, "total_ascent")
                self.assertIn(23, fields, "total_descent")
                self.assertNotIn(25, fields, "first_lap_index is not the climb")
                self.assertNotIn(26, fields, "num_laps is not the descent")


if __name__ == "__main__":
    unittest.main()
