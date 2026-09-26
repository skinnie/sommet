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


def plan(*dates):
    return [{"date": d, "mode": "Running", "workout": {"name": f"W{d[-2:]}"}} for d in dates]


class PlanDiffWindow(unittest.TestCase):
    TODAY = TC.datetime.date(2026, 9, 26)

    def test_syncs_only_the_soonest_that_fit(self):
        data = region(("Sunrise", 0), ("Mine", 1))
        entries = plan("2026-09-26", "2026-09-28", "2026-09-30", "2026-10-03", "2026-10-05",
                       "2026-10-07")
        kept, to_add, waiting = TC.plan_diff(data, entries, self.TODAY)
        self.assertEqual([e["date"] for e in to_add],
                         ["2026-09-26", "2026-09-28", "2026-09-30", "2026-10-03"])
        self.assertEqual(waiting, ["05/10_W05", "07/10_W07"])
        self.assertEqual(len(kept), 2)

    def test_erases_installed_ones_outside_the_window(self):
        data = region(("Sunrise", 0), ("24/09_W24", 1), ("26/09_W26", 1), ("28/09_W28", 1),
                      ("30/09_W30", 1), ("03/10_W03", 1), ("05/10_W05", 1), ("07/10_W07", 1))
        entries = plan("2026-09-24", "2026-09-26", "2026-09-28", "2026-09-30", "2026-10-03",
                       "2026-10-05", "2026-10-07")
        kept, to_add, waiting = TC.plan_diff(data, entries, self.TODAY)
        self.assertEqual(names(TC.rebuild_apps_region(kept)),
                         [("Sunrise", 0), ("26/09_W26", 1), ("28/09_W28", 1), ("30/09_W30", 1),
                          ("03/10_W03", 1), ("05/10_W05", 1)])
        self.assertEqual((to_add, waiting), ([], ["07/10_W07"]))

    def test_full_manual_menu_leaves_no_room(self):
        data = region(*[(f"M{i}", 1) for i in range(5)])
        _, to_add, waiting = TC.plan_diff(data, plan("2026-09-28"), self.TODAY)
        self.assertEqual((to_add, waiting), ([], ["28/09_W28"]))


class BuilderMenuFull(unittest.TestCase):
    def test_refuses_a_sixth_and_replaces(self):
        import guided_workout as GW
        data = region(("Sunrise", 0), *[(f"M{i}", 1) for i in range(5)])
        with self.assertRaises(GW.MenuFull) as ctx:
            GW.build_regions(b"", data, {"name": "New"}, "Running", append=True)
        self.assertEqual(ctx.exception.workouts, [f"M{i}" for i in range(5)])
        entries = GW.without_native(WI.apps_entries_with_raw_blocks(data), "M2")
        self.assertEqual(GW.native_names(entries), ["M0", "M1", "M3", "M4"])


if __name__ == "__main__":
    unittest.main()
