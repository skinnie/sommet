#!/usr/bin/env python3
"""training_program.plan_move_id: every planned move gets its own non-zero, stable id. With id 0
on every card, one finished card exercise made the watch mark the whole plan done ("Training
program completed!", André's Peak 2026-10-14). No watch, no network."""
import contextlib
import datetime
import hashlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ambit_format as F
import training_program as TP

D = datetime.date


class PlanMoveId(unittest.TestCase):
    def test_never_zero_and_tagged(self):
        mid = TP.plan_move_id(D(2026, 10, 14))
        self.assertNotEqual(mid, 0)
        self.assertEqual(mid & 0xFF000000, TP.SOMMET_MOVE_ID_TAG)

    def test_day_number_is_the_watch_days_since_2000(self):
        self.assertEqual((TP.plan_move_id(D(2026, 10, 14)) >> 4) & 0xFFFFF, 9783)

    def test_stable_across_syncs(self):
        self.assertEqual(TP.plan_move_id(D(2026, 10, 17), 1), TP.plan_move_id(D(2026, 10, 17), 1))

    def test_unique_per_day_and_per_move(self):
        days = [D(2026, 10, 14) + datetime.timedelta(days=i) for i in range(60)]
        ids = {TP.plan_move_id(d, o) for d in days for o in range(3)}
        self.assertEqual(len(ids), 180)

    def test_sixteen_moves_a_day_at_most(self):
        with self.assertRaises(ValueError):
            TP.plan_move_id(D(2026, 10, 14), 16)


class FakeWatch:
    """A TrainingProgram region plus the memory-map hash of what was last written to it, as a
    real Ambit3 reports it (0x0b21: the closing hash of the last write, HASH_WRITTEN)."""
    def __init__(self):
        self.region = b"\xff" * F.TRAINING_PROGRAM_REGION_SIZE
        self.hash = "F" * 64                        # never written: the watch reports no SHA256
        self.writes = 0
        self.dry_run = False
        self.sent = []

    # Link
    def open(self):
        pass

    def command(self, cmd, payload=b""):
        if cmd == TP.CMD_MEMORY_MAP:
            return (b"\x00JYTrainingProgram\x00" + self.hash.encode() + b"\x00"
                    + struct.pack("<II", F.TRAINING_PROGRAM_BASE, F.TRAINING_PROGRAM_REGION_SIZE))
        return b""

    # write_nav functions training_program imported
    def read_flash(self, link, base, size, label=None):
        return self.region[base - F.TRAINING_PROGRAM_BASE:][:size]

    def send_plan(self, link, flash, layout, commit=True):
        name, base, blob = layout[0]
        blob = bytes(blob)
        self.region = blob + self.region[len(blob):]
        self.hash = hashlib.sha256(blob).hexdigest().upper()
        self.writes += 1


class SkipsWhatTheWatchHolds(unittest.TestCase):
    """The plug-in sync rewrote the TrainingProgram region on every connection, same plan or
    not (desktop audit 2026-10-29). A region the watch already holds, under the hash it checks,
    is left alone; anything else is written as before."""

    def setUp(self):
        self.watch = FakeWatch()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        for name, value in (("Link", lambda **kw: self.watch), ("read_flash", self.watch.read_flash),
                            ("send_plan", self.watch.send_plan),
                            ("read_memory_map", lambda link: {}),
                            ("check_memory_map", lambda found: True)):
            self.addCleanup(setattr, TP, name, getattr(TP, name))
            setattr(TP, name, value)

    def run_main(self, *argv):
        out = io.StringIO()
        old = sys.argv
        sys.argv = ["training_program.py", *argv, "--json", "--write"]
        try:
            with contextlib.redirect_stdout(out):
                code = TP.main()
        finally:
            sys.argv = old
        return code, json.loads(out.getvalue().strip().splitlines()[-1])

    def plan(self, minutes):
        path = os.path.join(self.tmp, f"plan{minutes}.json")
        with open(path, "w") as f:
            json.dump({"items": [{"date": "2026-11-02", "activityId": 3, "durationMinutes": minutes,
                                  "intensity": 1, "name": "Easy run"}]}, f)
        return path

    def test_same_plan_twice_writes_once(self):
        code, first = self.run_main("--plan", self.plan(30))
        self.assertEqual((code, first["ok"], first["written"], self.watch.writes), (0, True, True, 1))
        code, again = self.run_main("--plan", self.plan(30))
        self.assertEqual((code, again["ok"], again["written"], again.get("unchanged")),
                         (0, True, False, True))
        self.assertEqual((again["count"], again["dates"]), (1, ["2026-11-02"]))  # the app still shows the card
        self.assertEqual(self.watch.writes, 1)
        code, changed = self.run_main("--plan", self.plan(45))
        self.assertEqual((changed["written"], self.watch.writes), (True, 2))

    def test_clear_of_an_empty_region_is_written_only_once(self):
        # A never-written region reports no SHA256 of its bytes: written, then left alone.
        self.assertEqual(self.run_main("--clear")[1]["written"], True)
        again = self.run_main("--clear")[1]
        self.assertEqual((again["ok"], again["written"], again.get("unchanged")), (True, False, True))
        self.assertEqual(self.watch.writes, 1)

    def test_same_bytes_under_another_hash_are_written(self):
        self.run_main("--plan", self.plan(30))
        self.watch.hash = "0" * 64
        self.assertEqual(self.run_main("--plan", self.plan(30))[1]["written"], True)
        self.assertEqual(self.watch.writes, 2)

    def test_watch_holds_compares_bytes_too(self):
        blob = b"\x01" * 40
        self.watch.hash = hashlib.sha256(blob).hexdigest().upper()
        self.assertFalse(TP.watch_holds(self.watch, "TrainingProgram", F.TRAINING_PROGRAM_BASE, blob,
                                        current=b"\x02" * 40))
        self.assertTrue(TP.watch_holds(self.watch, "TrainingProgram", F.TRAINING_PROGRAM_BASE, blob,
                                       current=blob + b"\xff"))
        self.assertFalse(TP.watch_holds(self.watch, "Apps", 0, blob, current=blob))  # no such hash


if __name__ == "__main__":
    unittest.main()
