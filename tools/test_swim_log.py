#!/usr/bin/env python3
"""Pool-swim log handling, on made-up samples shaped like André's swim of 2026-10-04 (Ambit3
Sport): lengths logged out of order, heart rate rebuilt from the belt's stored beats, and the
guided-workout steps carried into the FIT.

    python3 tools/test_swim_log.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import activity_streams
import exercise_log as E

HEADER = {"year": 2026, "month": 10, "day": 4, "hour": 8, "minute": 31, "msec": 0,
          "duration_ms": 200_000, "distance": 100, "ascent": 0, "descent": 0,
          "activity_type": 6, "activity_name": "", "swimming_pool_length": 25,
          "swimming_pool_lengths": 4, "heartrate_avg": 120, "heartrate_max": 150}


def turn(ms, lengths, swim_s, strokes=18, style=4):
    return {"type": "swimming_turn", "time": ms, "lengths": lengths,
            "duration_ds": round(swim_s * 10), "strokes": strokes, "style": style}


def periodic(ms):
    return {"type": "periodic", "time": ms, "values": [{"type": 6, "name": "time", "value": ms}]}


class SwimLog(unittest.TestCase):
    def test_lengths_logged_out_of_order_are_all_kept(self):
        # the watch wrote length 3's record ahead of length 2's
        samples = [turn(30_000, 1, 30), turn(40_000, 3, 20), turn(75_000, 2, 40), turn(130_000, 4, 30)]
        lengths = E.extract_pool_lengths(HEADER, samples)
        self.assertEqual([ln["swim_s"] for ln in lengths], [30, 40, 20, 30])
        starts = [(ln["start"] - lengths[0]["start"]).total_seconds() for ln in lengths]
        self.assertEqual(starts, [0, 35, 75, 100])     # 3 starts where 2 ended; 4 after a rest

    def test_heart_rate_from_belt_beats(self):
        # 60 s at 120 bpm, a 30 s pause the belt wrote as one gap record, then 60 s at 150 bpm
        ibis = [500] * 120 + [30_000] + [400] * 150
        samples = [periodic(ms) for ms in range(0, 150_001, 10_000)]
        samples.append({"type": "ibi", "time": 150_000, "ibi": ibis})
        hr = {round(r["time"].timestamp()) % 3600: r["hr"] for r in E.extract_indoor_records(HEADER, samples)}
        base = 31 * 60
        self.assertEqual(hr[base + 30], 120)
        self.assertIsNone(hr[base + 80])               # in the pause: no beats in the last 5 s
        self.assertEqual(hr[base + 120], 150)

    def test_live_heart_rate_is_left_alone(self):
        samples = [{"type": "periodic", "time": 1000, "values": [{"type": 0, "name": "hr", "value": 99}]},
                   {"type": "ibi", "time": 2000, "ibi": [500] * 20}]
        self.assertEqual([r["hr"] for r in E.extract_indoor_records(HEADER, samples)], [99])

    def test_workout_steps_and_summary_heart_rate_reach_the_file(self):
        def step(ms, kind, ends_on, value):
            return {"type": "workout_step", "time": ms, "target_min": -1.0, "target_max": -1.0,
                    "end_value": value, "step": kind, "ends_on": ends_on}
        samples = [periodic(ms) for ms in range(0, 200_001, 10_000)]
        samples += [turn(30_000, 1, 30), turn(60_000, 2, 30),
                    step(5_000, 1, 0x0a, 50.0), step(62_000, 3, 0x0b, 45.0), step(107_000, 1, 0x19, 0.0),
                    step(150_000, 7, 0x0b, 0.0), step(160_000, 1, 0x0a, 100.0)]
        st = activity_streams.streams_from_fit(E.to_fit(HEADER, samples))
        self.assertEqual((st["summary"]["avg_hr"], st["summary"]["max_hr"]), (120, 150))
        self.assertEqual(
            [(w["workout"], w["start_s"], w["intensity"], w["ends_on"], w["value"]) for w in st["workout_steps"]],
            [(1, 5, "active", "distance", 50.0), (1, 62, "rest", "time", 45.0),
             (1, 107, "active", "lap", None), (2, 160, "active", "distance", 100.0)])


if __name__ == "__main__":
    unittest.main()
