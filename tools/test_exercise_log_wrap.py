#!/usr/bin/env python3
"""A wrapped ExerciseLog decodes end to end (GitHub #19): a sample that straddles the end of the
circular region -> WRAP_START_OFFSET used to raise struct.error. Uses the pristine Traverse dump
(its log has wrapped - the pre-fix code fails on it), skipped when that local backup is absent."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import exercise_log as EL

DUMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backups",
                    "traverse-fw1.0.4-pristine", "ExerciseLog.bin")
TRAVERSE_BASE, TRAVERSE_SIZE = 0x2b7cd0, 5276464   # from the Traverse's 0x0b21 memory map


class WrappedLog(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(DUMP), "local Traverse ExerciseLog dump not present")
    def test_every_entry_decodes_with_its_declared_sample_count(self):
        data = open(DUMP, "rb").read()
        self.assertEqual(len(data), TRAVERSE_SIZE)
        entries = list(EL.walk_entries(data, mem_start=TRAVERSE_BASE, mem_size=TRAVERSE_SIZE))
        self.assertEqual(len(entries), 37)
        for header, samples in entries:
            self.assertEqual(len(samples), header["samples_count"])


if __name__ == "__main__":
    unittest.main()
