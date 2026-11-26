#!/usr/bin/env python3
"""Desktop backend: a Kailash gets no training plan.

On 2026-11-26 the desktop's automatic plan sync ran on André's Kailash the moment it linked by
Bluetooth: an App Zone read and a "Today" card write (0x0b16), both refused by the watch. Its
memory map declares no TrainingProgram region (DeviceCapabilities.qml, 2026-08-27). Checked
here, no watch: both plan endpoints answer "unsupported" for a Kailash, over Bluetooth and by
cable, and start no tool and no Bluetooth job; an Ambit3 still goes through.

    python3 tools/test_backend_kailash_plans.py
"""
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import server  # noqa: E402

PLAN = {"entries": [{"date": "2026-11-27", "mode": "Running", "workout": "T10s"}],
        "write": True, "autoStart": False}


class KailashPlans(unittest.TestCase):
    def setUp(self):
        self.calls, self.sent = [], []
        self.saved = (server.run_tool, server.use_ble, server.selected_is_kailash,
                      server.ble_bridge.bridge.status)
        server.run_tool = lambda *a, **k: self.calls.append(a) or (0, '{"ok": true}', "")
        self.h = server.Handler.__new__(server.Handler)
        self.h._send_json = lambda code, obj: self.sent.append((code, obj))
        self.h._sync_calendar_ble = lambda *a: self.calls.append(("ble sync", a))
        self.h._write_planned_moves_ble = lambda *a: self.calls.append(("ble cards", a)) or {}

    def tearDown(self):
        (server.run_tool, server.use_ble, server.selected_is_kailash,
         server.ble_bridge.bridge.status) = self.saved

    def linked(self, model, over_ble):
        server.use_ble = lambda: over_ble
        server.ble_bridge.bridge.status = lambda: {"device_info": {"model": model}}
        server.selected_is_kailash = lambda: model == "Hoopoe"

    def refused(self):
        ((code, obj),) = self.sent
        self.assertEqual(code, 200)
        self.assertEqual((obj["ok"], obj["unsupported"]), (False, True))
        self.assertEqual(obj["error"], server.KAILASH_NO_PLANS)
        self.assertEqual(self.calls, [])

    def test_kailash_over_bluetooth(self):
        self.linked("Hoopoe", over_ble=True)
        self.h._handle_trainingprogram_sync_calendar(dict(PLAN))
        self.refused()
        self.sent.clear()
        self.h._handle_trainingprogram_planned_moves(dict(PLAN))
        self.refused()

    def test_kailash_by_cable(self):
        self.linked("Hoopoe", over_ble=False)
        self.h._handle_trainingprogram_sync_calendar(dict(PLAN))
        self.refused()
        self.sent.clear()
        self.h._handle_trainingprogram_planned_moves(dict(PLAN))
        self.refused()

    def test_ambit3_still_syncs(self):
        self.linked("Emu", over_ble=True)
        self.h._handle_trainingprogram_sync_calendar(dict(PLAN))
        self.assertEqual([c[0] for c in self.calls], ["ble sync"])


if __name__ == "__main__":
    unittest.main()
