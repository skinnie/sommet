#!/usr/bin/env python3
"""training_calendar.remove_native_entry: only native workouts go, and never one that would shift
a generic Suunto App's position (sport-mode App displays point at apps by position)."""
import contextlib
import datetime
import hashlib
import io
import json
import os
import pathlib
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import apps
import training_calendar as TC
import workout_install as WI

BIN = bytes(range(16))


def lbl(date_iso):
    """The on-watch label the tool writes for plan()'s workout (name "W<dd>") on `date_iso` —
    tracks TC.entry_label so these tests don't hardcode the naming format."""
    return TC.entry_label(date_iso, "W" + date_iso[-2:])


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
        self.assertEqual(waiting, [lbl("2026-10-05"), lbl("2026-10-07")])
        self.assertEqual(len(kept), 2)

    def test_erases_installed_ones_outside_the_window(self):
        dates = ["2026-09-24", "2026-09-26", "2026-09-28", "2026-09-30", "2026-10-03",
                 "2026-10-05", "2026-10-07"]
        data = region(("Sunrise", 0), *[(lbl(d), 1) for d in dates])
        entries = plan(*dates)
        kept, to_add, waiting = TC.plan_diff(data, entries, self.TODAY)
        self.assertEqual(names(TC.rebuild_apps_region(kept)),
                         [("Sunrise", 0)] + [(lbl(d), 1) for d in
                          ["2026-09-26", "2026-09-28", "2026-09-30", "2026-10-03", "2026-10-05"]])
        self.assertEqual((to_add, waiting), ([], [lbl("2026-10-07")]))

    def test_full_manual_menu_leaves_no_room(self):
        data = region(*[(f"M{i}", 1) for i in range(5)])
        _, to_add, waiting = TC.plan_diff(data, plan("2026-09-28"), self.TODAY)
        self.assertEqual((to_add, waiting), ([], [lbl("2026-09-28")]))


class BuilderMenuFull(unittest.TestCase):
    def test_refuses_a_sixth_and_replaces(self):
        import guided_workout as GW
        data = region(("Sunrise", 0), *[(f"M{i}", 1) for i in range(5)])
        with self.assertRaises(GW.MenuFull) as ctx:
            GW.build_regions(b"", data, {"name": "New"}, "Running", append=True)
        self.assertEqual(ctx.exception.workouts, [f"M{i}" for i in range(5)])
        entries = GW.without_native(WI.apps_entries_with_raw_blocks(data), "M2")
        self.assertEqual(GW.native_names(entries), ["M0", "M1", "M3", "M4"])


class ModeActivityId(unittest.TestCase):
    """Each sport's WORKOUT menu lists only workouts carrying its ActivityID (HW 2026-09-26)."""

    def test_real_sport_modes(self):
        import glob
        import pathlib
        import custom_modes as cm
        import guided_workout as GW
        hits = sorted(glob.glob(str(pathlib.Path(__file__).parent / "backups" / "CustomModes_*.bin")))
        if not hits:
            self.skipTest("no local CustomModes backup (gitignored)")
        decoded = cm.decode(open(hits[-1], "rb").read())
        names = [m["Settings"]["Name"] for m in decoded["exercise_modes"]]
        for mode, activity in (("Running", 3), ("Cycling", 4), ("Pool swimming", 6)):
            if mode in names:
                self.assertEqual(GW.mode_activity_id(decoded, mode), activity)


def modes(*spec):
    """[(name, activity_id)] -> a custom_modes.decode-shaped dict with empty rules."""
    return {"exercise_modes": [{"Settings": {"Name": n, "ActivityID": a}, "Displays": [], "Rules": []}
                               for n, a in spec]}


class AutoStartRules(unittest.TestCase):
    """2026-10-17: every installed upcoming workout is an active rule of the sport mode the plan
    names (plain "Running", date-gated on the watch), soonest first; other modes carry none."""
    TODAY = TC.datetime.date(2026, 10, 10)

    def test_all_upcoming_into_the_named_mode_only(self):
        d = modes(("Cycling", 4), ("Running", 3), ("Running workout", 3))
        data = region(("Sunrise", 0), (lbl("2026-10-10"), 1), (lbl("2026-10-12"), 1),
                      (lbl("2026-10-14"), 1))
        existing = WI.apps_entries_with_raw_blocks(data)
        touched, out = TC.auto_start_rules(
            d, existing, data, plan("2026-10-14", "2026-10-12", "2026-10-10"), self.TODAY, True)
        self.assertEqual(touched, ["Running"])
        self.assertEqual(out, [{"sport": 3, "mode": "Running",
                                "workouts": [lbl("2026-10-10"), lbl("2026-10-12"), lbl("2026-10-14")]}])
        by_name = {m["Settings"]["Name"]: m["Rules"] for m in d["exercise_modes"]}
        self.assertEqual([r["RuleIdx"] for r in by_name["Running"]], [1, 2, 3])
        self.assertEqual(by_name["Running workout"], [])
        self.assertEqual(by_name["Cycling"], [])

    def test_rules_left_in_an_old_workout_mode_move_to_the_named_mode(self):
        d = modes(("Running", 3), ("Running workout", 3))
        data = region(("Sunrise", 0), (lbl("2026-10-10"), 1))
        d["exercise_modes"][1]["Rules"] = [{"RuleIdx": 1, "UseRule": True, "LogRule": False}]
        existing = WI.apps_entries_with_raw_blocks(data)
        touched, out = TC.auto_start_rules(d, existing, data, plan("2026-10-10"), self.TODAY, True)
        self.assertEqual(sorted(touched), ["Running", "Running workout"])
        self.assertEqual([r["RuleIdx"] for r in d["exercise_modes"][0]["Rules"]], [1])
        self.assertEqual(d["exercise_modes"][1]["Rules"], [])

    def test_unknown_mode_gets_no_rules(self):
        d = modes(("Cycling", 4))
        data = region(("10/10_W10", 1))
        existing = WI.apps_entries_with_raw_blocks(data)
        _, out = TC.auto_start_rules(d, existing, data, plan("2026-10-10"), self.TODAY, True)
        self.assertEqual(out, [])
        self.assertEqual(d["exercise_modes"][0]["Rules"], [])

    def test_off_drops_managed_rules_everywhere(self):
        d = modes(("Running", 3), ("Running workout", 3))
        data = region(("Sunrise", 0), (lbl("2026-10-10"), 1))
        for m in d["exercise_modes"]:
            m["Rules"] = [{"RuleIdx": 0, "UseRule": True, "LogRule": False},
                          {"RuleIdx": 1, "UseRule": True, "LogRule": False}]
        existing = WI.apps_entries_with_raw_blocks(data)
        touched, out = TC.auto_start_rules(d, existing, data, plan("2026-10-10"), self.TODAY, False)
        self.assertEqual(sorted(touched), ["Running", "Running workout"])
        self.assertEqual(out, [])
        for m in d["exercise_modes"]:
            self.assertEqual([r["RuleIdx"] for r in m["Rules"]], [0])   # the Sunrise app stays

    def test_five_slot_limit(self):
        d = modes(("Running", 3), ("Running workout", 3))
        d["exercise_modes"][0]["Rules"] = [{"RuleIdx": 0, "UseRule": True, "LogRule": False}] * 3
        dates = ["2026-10-1%d" % i for i in range(0, 5)]
        data = region(("Sunrise", 0), *[(TC.entry_label(x, "W" + x[-2:]), 1) for x in dates])
        existing = WI.apps_entries_with_raw_blocks(data)
        _, out = TC.auto_start_rules(d, existing, data, plan(*dates), self.TODAY, True)
        self.assertEqual(len(d["exercise_modes"][0]["Rules"]), WI.SPORT_MODE_APP_LIMIT)
        self.assertEqual(out[0]["workouts"], [lbl("2026-10-10"), lbl("2026-10-11")])


class DateGate(unittest.TestCase):
    def test_gate_managed_gates_once_and_only_known_labels(self):
        import json
        import pathlib
        fx = json.loads((pathlib.Path(__file__).parent / "testdata" / "iamrule_oracle.json").read_text())
        plain = bytes.fromhex(fx["date_gate_plain"])[len(apps.MAGIC):]
        data = WI.build_apps_region([], {"name": "10/10_W10", "binary": plain}, entry_type=1)
        data = WI.build_apps_region(WI.apps_entries_with_raw_blocks(data),
                                    {"name": "12/10_W12", "binary": plain}, entry_type=1)
        entries = WI.apps_entries_with_raw_blocks(data)
        out, names = TC.gate_managed(entries, {"10/10_W10": "2026-10-10"})
        self.assertEqual(names, ["10/10_W10"])
        self.assertTrue(TC.is_gated(out[0]["binary"]))
        self.assertFalse(TC.is_gated(out[1]["binary"]))
        again = TC.rebuild_apps_region([e["_raw_block"] for e in out])
        decoded = apps.decode(again)
        self.assertEqual([e["name"] for e in decoded], ["10/10_W10", "12/10_W12"])
        self.assertTrue(TC.is_gated(decoded[0]["binary"]))
        self.assertEqual(TC.gate_managed(out, {"10/10_W10": "2026-10-10"})[1], [])



class SyncReinstallsEdits(unittest.TestCase):
    """A workout edited under the same date and name reaches the watch (2026-10-24: a 33-minute
    test changed to 10 s steps stayed 33 minutes - plan_diff keeps a label it already sees)."""
    TODAY = datetime.date(2026, 10, 24)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.apps = bytes([0, 0, 2, 0, 8, 0, 0, 0]) + b"\xff" * 64   # an empty Apps directory
        self.apps_hash = hashlib.sha256(self.apps[:8]).hexdigest().upper()  # its written extent
        self.writes = []                             # region names written, in order
        self.compiled = []
        self.fail_compile = False
        self.fail_names = set()
        test = self

        class Link:
            """Answers the memory map (0x0b21) with the Apps hash of what was last written."""
            def command(self, cmd, payload=b""):
                assert cmd == TC.watch_holds.__globals__["CMD_MEMORY_MAP"]
                return (b"\x00JNApps\x00" + test.apps_hash.encode() + b"\x00"
                        + struct.pack("<II", 0x2000, 200000))
        self.link = Link()

        def send(link, fi, parts, commit=False):
            for name, base, blob in parts:
                if blob is not None:
                    self.writes.append(name)
                if name == "Apps" and blob is not None:
                    self.apps = bytes(blob) + b"\xff" * 64
                    self.apps_hash = hashlib.sha256(bytes(blob)).hexdigest().upper()

        def compile_workout(workout, lang=None):
            if self.fail_compile or workout["name"] in self.fail_names:
                raise SystemExit("compiler unreachable")
            self.compiled.append(workout["steps"])
            body = json.dumps(workout["steps"]).encode()
            return {"name": workout["name"], "activityId": 1,
                    "binary": struct.pack("<IIII", 8, 1, 1, 0) + body + b"rule0\0"}

        decoded = {"format_type": 2, "exercise_modes": [{
            "Settings": {"Name": "Running", "ActivityID": 3}, "Rules": [],
            "Displays": [{"Template": TC.GW.GUIDANCE_TEMPLATE}]}]}
        patches = [
            (TC, "read_memory_map", lambda link: {"CustomModes": (0x1000, 12288), "Apps": (0x2000, 200000)}),
            (TC, "read_flash", lambda link, base, size, label=None: self.apps if base == 0x2000 else b"\0" * 64),
            (TC, "send_plan", send),
            (TC, "watch_serial", lambda link: "8A15TEST"),
            (TC, "INSTALLED_DIR", pathlib.Path(self.tmp)),
            (TC, "needs_hr_fix", lambda b: False), (TC, "needs_regate", lambda b: False),
            (TC, "is_gated", lambda b: False),
            (TC, "BACKUP_DIR", pathlib.Path(self.tmp) / "custom_modes"),     # backups go to tmp
            (TC.GW, "read_watch_language", lambda link: "en"),
            (TC.GW, "compile_workout", compile_workout),
            (TC.cm, "decode", lambda blob: json.loads(json.dumps(decoded))),
        ]
        for mod, name, value in patches:
            self.addCleanup(setattr, mod, name, getattr(mod, name))
            setattr(mod, name, value)

    def sync(self, steps):
        plan = {"entries": [{"date": "2026-10-24", "mode": "Running",
                             "workout": {"name": "Test", "steps": steps}}]}
        with contextlib.redirect_stdout(io.StringIO()):
            return TC.sync(self.link, plan, self.TODAY, write=True, json_out=True)

    def on_watch(self):
        return [(e["name"], e["binary"]) for e in apps.decode(self.apps)]

    def test_edit_is_reinstalled_and_unchanged_is_not(self):
        label = TC.entry_label("2026-10-24", "Test")
        self.assertEqual(self.sync([{"s": 600}])["added"], [label])
        again = self.sync([{"s": 600}])
        self.assertEqual((again["added"], again["edited"], len(self.compiled)), ([], [], 1))
        edited = self.sync([{"s": 10}])
        self.assertEqual((edited["added"], edited["edited"]), ([label], [label]))
        [(name, binary)] = self.on_watch()
        self.assertEqual(name, label)
        self.assertIn(b'"s": 10', binary)

    def test_an_edit_that_fails_to_compile_keeps_the_old_version(self):
        label = TC.entry_label("2026-10-24", "Test")
        self.sync([{"s": 600}])
        self.fail_compile = True
        r = self.sync([{"s": 10}])
        self.assertEqual([f["name"] for f in r["failed"]], [label])
        [(name, binary)] = self.on_watch()
        self.assertIn(b'"s": 600', binary)          # still there, not erased
        self.fail_compile = False
        self.assertEqual(self.sync([{"s": 10}])["edited"], [label])   # retried next time

    def test_a_failed_edit_and_a_new_workout_never_share_a_rule_id(self):
        # Two installed workouts with one rule id: the watch runs the first, whichever is picked.
        def sync(entries):
            plan = {"entries": [{"date": d, "mode": "Running",
                                 "workout": {"name": n, "steps": st}} for d, n, st in entries]}
            with contextlib.redirect_stdout(io.StringIO()):
                return TC.sync(self.link, plan, self.TODAY, write=True, json_out=True)
        sync([("2026-10-24", "A", [{"s": 600}])])
        self.fail_names = {"A"}                     # the edit of A won't compile (offline)
        r = sync([("2026-10-24", "A", [{"s": 10}]), ("2026-10-25", "B", [{"s": 300}])])
        self.assertEqual([f["name"] for f in r["failed"]], [TC.entry_label("2026-10-24", "A")])
        ids = [e["ruleId"] for e in apps.decode(self.apps)]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2, f"rule ids {ids}")

    def test_past_the_compile_budget_entries_wait_for_the_next_sync(self):
        label = TC.entry_label("2026-10-24", "Test")
        self.sync([{"s": 600}])
        self.addCleanup(setattr, TC, "COMPILE_BUDGET_S", TC.COMPILE_BUDGET_S)
        TC.COMPILE_BUDGET_S = -1                    # every compile is already too late
        r = self.sync([{"s": 10}])
        self.assertEqual([f["name"] for f in r["failed"]], [label])
        self.assertIn("next sync", r["failed"][0]["error"])
        [(_name, binary)] = self.on_watch()
        self.assertIn(b'"s": 600', binary)          # the old version stays on the watch

    def test_an_identical_sync_writes_nothing(self):
        # The plug-in sync runs on every connection; it rewrote Apps each time (audit 2026-10-29).
        first = self.sync([{"s": 600}])
        self.assertEqual((first["written"], first["unchanged"], self.writes), (True, False, ["Apps"]))
        backups = sorted((pathlib.Path(self.tmp) / "custom_modes").glob("CustomModes_pre_calendar_8A15TEST_*.bin"))
        self.assertEqual(len(backups), 1)
        again = self.sync([{"s": 600}])
        self.assertEqual((again["ok"], again["written"], again["unchanged"]), (True, False, True))
        self.assertEqual(self.writes, ["Apps"])                       # nothing sent the second time
        self.assertEqual(len(list((pathlib.Path(self.tmp) / "custom_modes").glob("*.bin"))), 1)
        changed = self.sync([{"s": 10}])                              # an edit is written again
        self.assertEqual((changed["written"], changed["unchanged"]), (True, False))
        self.assertEqual(self.writes, ["Apps", "Apps"])

    def test_same_bytes_but_another_hash_is_written(self):
        # The watch checks the hash it was given: a stale one is healed, never trusted.
        self.sync([{"s": 600}])
        self.apps_hash = "0" * 64
        self.assertEqual(self.sync([{"s": 600}])["written"], True)
        self.assertEqual(self.writes, ["Apps", "Apps"])

    def test_first_sync_without_a_record_trusts_the_watch(self):
        self.sync([{"s": 600}])
        TC.installed_path("8A15TEST").unlink()
        r = self.sync([{"s": 10}])                  # installed by an older version: no record
        self.assertEqual((r["added"], r["edited"]), ([], []))
        self.assertEqual(self.sync([{"s": 20}])["edited"], [TC.entry_label("2026-10-24", "Test")])

if __name__ == "__main__":
    unittest.main()
