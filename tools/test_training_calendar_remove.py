#!/usr/bin/env python3
"""training_calendar.remove_native_entry: only native workouts go, and never one that would shift
a generic Suunto App's position (sport-mode App displays point at apps by position)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apps
import training_calendar as TC
import workout_install as WI

BIN = bytes(range(16))


def region(*entries):
    """[(name, entry_type)] -> Apps region bytes, built the way the installer builds it."""
    current = []
    out = None
    for name, entry_type in entries:
        out = WI.build_apps_region(current, {"name": name, "binary": BIN}, entry_type=entry_type)
        current = WI.apps_entries_with_raw_blocks(out)
    return out


def names(data):
    return [(e["name"], e["reserved"]) for e in apps.decode(data)]


class RemoveNativeEntry(unittest.TestCase):
    def test_removes_native_between_apps_and_dated(self):
        data = region(("Sunrise", 0), ("Light workout", 1), ("26/09_W4", 1), ("28/09_W4", 1))
        out = TC.remove_native_entry(data, "Light workout")
        self.assertEqual(names(out), [("Sunrise", 0), ("26/09_W4", 1), ("28/09_W4", 1)])

    def test_refuses_generic_app(self):
        data = region(("Sunrise", 0), ("Light workout", 1))
        with self.assertRaises(ValueError):
            TC.remove_native_entry(data, "Sunrise")

    def test_refuses_when_an_app_would_shift(self):
        data = region(("Light workout", 1), ("Sunrise", 0))
        with self.assertRaises(ValueError):
            TC.remove_native_entry(data, "Light workout")

    def test_refuses_unknown_name(self):
        with self.assertRaises(ValueError):
            TC.remove_native_entry(region(("Sunrise", 0)), "Nope")


if __name__ == "__main__":
    unittest.main()
