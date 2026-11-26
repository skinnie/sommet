#!/usr/bin/env python3
"""Desktop backend, Ambit1/2: sport-mode edits and route removal reach the legacy writers.

Until 2026-11-26 the Sport Modes page's rename and field edits went to the SBEM tools (which
cannot reach this family) and route removal answered 409. Checked here, no watch: each request
becomes the legacy_link.py command with the patch the Android writer uses, unknown fields and
the interval timer are refused before anything runs, and the Ambit2 read hands the page the raw
pods field and the stored HR limits (what an edit starts from).

    python3 tools/test_backend_legacy_edits.py
"""
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import server  # noqa: E402
import legacy_sport_modes  # noqa: E402
from test_legacy_sport_mode_edit import synthetic  # noqa: E402


class Legacy(unittest.TestCase):
    def setUp(self):
        self.calls, self.sent = [], []
        self.saved = (server.run_tool, server.selected_is_legacy, legacy_sport_modes.read_region_live)

        def fake_run_tool(script, args, **kw):
            self.calls.append((script, list(args)))
            return 0, json.dumps({"ok": True, "wrote": True}) + "\n", ""
        server.run_tool = fake_run_tool
        server.selected_is_legacy = lambda: True
        self.h = server.Handler.__new__(server.Handler)
        self.h._send_json = lambda code, obj: self.sent.append((code, obj))

    def tearDown(self):
        server.run_tool, server.selected_is_legacy, legacy_sport_modes.read_region_live = self.saved

    def patch_sent(self):
        (script, args), = self.calls
        self.assertEqual((script, args[0]), ("legacy_link.py", "sport-mode-edit"))
        self.assertIn("--write", args)
        return args[1], json.loads(args[2])

    def test_autolap(self):
        self.h._handle_customodes_field({"mode": "Running", "fields": {"Autolap": 500}, "confirm": True})
        self.assertEqual(self.patch_sent(), ("Running", {"autolapM": 500}))
        self.assertEqual(self.sent[0][0], 200)

    def test_hr_limits_and_pods(self):
        self.h._handle_customodes_field({"mode": "Cycling", "confirm": True, "fields": {
            "HrLow": 60, "HrHigh": 180, "HrLimitsUse": 0, "UseHw": 0x0803}})
        self.assertEqual(self.patch_sent(), ("Cycling", {
            "heartrateMin": 60, "heartrateMax": 180, "useHrLimits": False, "useHw": 0x0803}))

    def test_rename(self):
        self.h._handle_customodes_rename({"from": "Indoor training", "to": "Treino Forção ", "confirm": True})
        self.assertEqual(self.patch_sent(), ("Indoor training", {"name": "Treino Forção"}))

    def test_rehearsal_writes_nothing(self):
        self.h._handle_customodes_field({"mode": "Running", "fields": {"Autolap": 500}})
        (_script, args), = self.calls
        self.assertNotIn("--write", args)

    def test_refused_before_anything_runs(self):
        self.h._handle_customodes_field({"mode": "Running", "fields": {"GpsInterval": 5}, "confirm": True})
        self.h._handle_customodes_interval_timer({"mode": "Running", "enabled": True, "confirm": True})
        self.assertEqual([c for c, _ in self.sent], [400, 409])
        self.assertEqual(self.calls, [])

    def test_route_remove(self):
        self.h._handle_route_remove({"name": "Gare du Nord to"})
        self.assertEqual(self.calls, [("legacy_link.py", ["route-remove", "Gare du Nord to", "--write"])])
        self.assertEqual(self.sent[0][0], 200)

    def test_ambit2_read_keeps_the_raw_values(self):
        region = bytearray(synthetic(90))
        blob = legacy_sport_modes.named_blobs(region)[0][0]
        region[blob + 22:blob + 24] = (0x0107).to_bytes(2, "little")          # 0x0004 too
        region[blob + 32:blob + 38] = bytes([180, 0, 60, 0, 1, 0])           # 180/60, on
        legacy_sport_modes.read_region_live = lambda length=0: bytes(region)
        server.run_tool = lambda script, args, **kw: (0, json.dumps({"ok": False}) + "\n", "")
        self.h._customodes_read_ambit2()
        code, obj = self.sent[0]
        m = obj["exerciseModes"][0]
        self.assertEqual((code, m["useHw"], m["hrHigh"], m["hrLow"], m["hrLimitsUse"]),
                         (200, 0x0107, 180, 60, 1))


if __name__ == "__main__":
    unittest.main()
