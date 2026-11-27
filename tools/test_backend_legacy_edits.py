#!/usr/bin/env python3
"""Desktop backend, Ambit1/2: sport-mode edits and route removal reach the legacy writers.

Until 2026-11-26 the Sport Modes page's rename and field edits went to the SBEM tools (which
cannot reach this family) and route removal answered 409. Checked here, no watch: each request
becomes the legacy_link.py command with the patch the Android writer uses, unknown fields and
the interval timer are refused before anything runs, and the Ambit2 read hands the page the raw
pods field and the stored HR limits (what an edit starts from). Display edits (2026-11-26, L5)
go to legacy_link.py sport-mode-displays for all four models (the Ambit1's used to go to the C
patcher, which wrote no commit tail), and the read lists the multisport groups (read only).
Create and delete (L3) go to legacy_link.py sport-mode-create / -delete with the watch's model; a
multisport is created (L2, legacy_link.py multisport-create, legs in order) and deleted, never
edited; the read says which modes may be deleted and SuuntoLink's count, and the activity list
which sports SuuntoLink's create list has (Adventure Racing among them, as captured). A Suunto App
install on an Ambit1 (L1) goes to legacy_link.py app-install with the catalogue's bytes; on the
Ambit2 family, or for an app the catalogue does not list for the Ambit1, it is refused unrun.

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
import no_live_bluetooth  # noqa: E402
no_live_bluetooth.cut_off(server)
import legacy_sport_modes  # noqa: E402
from test_legacy_sport_mode_edit import synthetic  # noqa: E402
import test_legacy_display_edit  # noqa: E402


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
        self.h._customodes_read_legacy()
        code, obj = self.sent[0]
        m = obj["exerciseModes"][0]
        self.assertEqual((code, m["useHw"], m["hrHigh"], m["hrLow"], m["hrLimitsUse"]),
                         (200, 0x0107, 180, 60, 1))

    def test_display_edits(self):
        edits = [{"op": "add", "type": "1-row"},
                 {"op": "setRow", "display": 0, "row": "Top", "values": [11]}]
        self.h._handle_customodes_displays({"mode": "Running", "edits": edits, "confirm": True})
        (script, args), = self.calls
        self.assertEqual((script, args[:2], json.loads(args[2])),
                         ("legacy_link.py", ["sport-mode-displays", "Running"], edits))
        self.assertIn("--write", args)
        self.assertEqual(self.sent[0][0], 200)

    def test_display_edit_in_testing_mode_never_reaches_the_ambit(self):
        # Review before 0.2.68: Testing mode edits the demo image, never a pinned Ambit1/2.
        saved = server.demo_ambit
        server.demo_ambit = lambda: True
        try:
            edits = [{"op": "add", "type": "1-row"}]
            self.h._handle_customodes_displays({"mode": "Running", "edits": edits, "confirm": True})
        finally:
            server.demo_ambit = saved
        self.assertFalse([a for _s, a in self.calls if a and a[0] == "sport-mode-displays"],
                         self.calls)

    def test_display_edit_unknown_op_refused(self):
        self.h._handle_customodes_displays({"mode": "Running", "confirm": True,
                                            "edits": [{"op": "move", "display": 0}]})
        self.assertEqual(self.sent[0][0], 400)
        self.assertEqual(self.calls, [])

    def test_read_lists_multisport_and_displays(self):
        # The display test's synthetic Ambit2 region: Running and Cycling, the Duathlon of both.
        legacy_sport_modes.read_region_live = lambda length=0: test_legacy_display_edit.synthetic(90)
        server.run_tool = lambda script, args, **kw: (0, json.dumps({"ok": False}) + "\n", "")
        self.h._customodes_read_legacy()
        code, obj = self.sent[0]
        self.assertEqual((code, obj["formatType"]), (200, "ambit2"))
        self.assertEqual(obj["sportModes"], [{"name": "Duathlon", "activityId": 2, "legs": [0, 1],
                                              "legNames": ["Running", "Cycling"],
                                              "isMultisport": True, "deletable": False,
                                              "deleteReason": "the watch keeps at least one sport mode"}])
        self.assertEqual([m["usedByMultisport"] for m in obj["exerciseModes"]], [True, True])
        self.assertEqual([m["deletable"] for m in obj["exerciseModes"]], [False, False])

    # ─── create and delete (watch-protocol/10 L3) ─────────────────────────────────────────────
    def test_create_and_delete_reach_legacy_link(self):
        saved = server.selected_legacy_variant
        server.selected_legacy_variant = lambda: "Duck"
        try:
            self.h._handle_customodes_mode({"action": "create", "name": "Trilho", "activityId": 82,
                                            "confirm": True})
            self.h._handle_customodes_mode({"action": "delete", "name": "Trilho", "confirm": True})
            self.h._handle_customodes_multisport({"action": "delete", "name": "Duathlon",
                                                  "confirm": True})
            self.h._handle_customodes_multisport({"action": "create", "name": "Tri", "activityId": 19,
                                                  "legs": ["Running", "Cycling"], "confirm": True})
            self.h._handle_customodes_mode({"action": "create", "name": "Dry", "activityId": 3})
            self.h._handle_customodes_multisport({"action": "edit", "name": "Tri", "activityId": 2,
                                                  "legs": ["Running", "Cycling"], "confirm": True})
        finally:
            server.selected_legacy_variant = saved
        heads = [args[:args.index("--variant") + 2] for _s, args in self.calls]
        self.assertEqual([s for s, _a in self.calls], ["legacy_link.py"] * 5)
        self.assertEqual(heads, [
            ["sport-mode-create", "Trilho", "82", "--variant", "Duck"],
            ["sport-mode-delete", "Trilho", "--variant", "Duck"],
            ["sport-mode-delete", "Duathlon", "--multisport", "--variant", "Duck"],
            ["multisport-create", "Tri", "19", '["Running", "Cycling"]', "--variant", "Duck"],
            ["sport-mode-create", "Dry", "3", "--variant", "Duck"]])
        self.assertEqual(["--write" in a for _s, a in self.calls], [True, True, True, True, False])
        # A multisport edit is refused with a reason, nothing run.
        self.assertEqual([c for c, _o in self.sent], [200] * 6)
        self.assertFalse(self.sent[5][1]["ok"])

    def test_create_needs_the_model(self):
        saved = server.selected_legacy_variant
        server.selected_legacy_variant = lambda: None
        try:
            self.h._handle_customodes_mode({"action": "create", "name": "X", "activityId": 3,
                                            "confirm": True})
        finally:
            server.selected_legacy_variant = saved
        self.assertEqual((self.sent[0][0], self.calls), (409, []))

    def test_read_says_what_may_be_deleted(self):
        import test_legacy_sport_mode_manage as M
        legacy_sport_modes.read_region_live = lambda length=0: M.synthetic(90)
        server.run_tool = lambda script, args, **kw: (0, json.dumps({"ok": False}) + "\n", "")
        self.h._customodes_read_legacy()
        code, obj = self.sent[0]
        self.assertEqual({m["name"]: m["deletable"] for m in obj["exerciseModes"]},
                         {"Running": False, "Transition": False, "Cycling": False})
        self.assertIn("Duathlon", obj["exerciseModes"][0]["deleteReason"])
        self.assertEqual({g["name"]: g["deletable"] for g in obj["sportModes"]},
                         {"Running": False, "Duathlon": True, "Cycling": False})
        self.assertEqual(obj["usage"], {"used": 3, "max": 10, "multisport": 1})
        # A new multisport's legs (the page offers what has a menu entry or a multisport uses):
        # all three here, the Transition only through the Duathlon.
        self.assertEqual([(m["name"], m["inWatchMenu"], m["usedByMultisport"])
                          for m in obj["exerciseModes"]],
                         [("Running", True, True), ("Transition", False, True),
                          ("Cycling", True, True)])

    def test_activities_say_what_suuntolink_creates(self):
        server._STATIC_ANSWERS.pop("activities", None)
        self.h._handle_customodes_activities()
        code, obj = self.sent[0]
        flags = {a["id"]: (a["isMultisport"], a["suuntoLinkCreate"]) for a in obj["activities"]}
        # Adventure racing, Triathlon and Multisport: multisport sports SuuntoLink also creates as
        # ordinary modes; Scuba diving (51) is not in its create list.
        self.assertEqual((code, flags[61], flags[19], flags[2], flags[3], flags[51]),
                         (200, (True, True), (True, True), (True, True), (False, True),
                          (False, False)))

    # ─── Suunto Apps on the Ambit1 (watch-protocol/10 L1) ─────────────────────────────────────
    def _apps_install(self, variant, entry, body):
        saved = (server.selected_legacy_variant, server.catalog_entry_binary)
        server.selected_legacy_variant = lambda: variant
        server.catalog_entry_binary = lambda rule_id: (entry, b"IAMRULE\0" + b"\x01" * 8)
        seen = []

        def fake_run_tool(script, args, **kw):
            seen.append(pathlib.Path(args[4]).read_bytes())
            self.calls.append((script, list(args)))
            return 0, json.dumps({"ok": True, "wrote": True, "apps": 1}) + "\n", ""
        server.run_tool = fake_run_tool
        try:
            self.h._handle_apps_install(body)
        finally:
            server.selected_legacy_variant, server.catalog_entry_binary = saved
        return seen

    def test_ambit1_app_install_reaches_legacy_link(self):
        entry = {"ruleId": 10000003, "name": "Sunrise/Sunset", "compatibleVariants": ["Bluebird"]}
        seen = self._apps_install("Bluebird", entry, {"mode": 9, "display": 0, "field": 2,
                                                       "ruleId": 10000003, "confirm": True})
        (script, args), = self.calls
        self.assertEqual((script, args[:4]), ("legacy_link.py", ["app-install", "@9", "0", "2"]))
        self.assertEqual(seen, [b"IAMRULE\0" + b"\x01" * 8])     # the catalogue's bytes
        self.assertIn("--catalog", args)
        self.assertIn("--write", args)
        self.assertTrue(args[args.index("--backup-dir") + 1].endswith("legacy_apps"))
        code, obj = self.sent[0]
        self.assertEqual((code, obj["ok"], obj["name"], obj["dryRun"]), (200, True, "Sunrise/Sunset", False))

    def test_ambit2_apps_and_incompatible_apps_refused(self):
        entry = {"ruleId": 1, "name": "X", "compatibleVariants": ["Duck", "Emu"]}
        self._apps_install("Duck", entry, {"mode": 0, "display": 0, "field": 2, "ruleId": 1,
                                           "confirm": True})
        self._apps_install("Bluebird", entry, {"mode": 0, "display": 0, "field": 2, "ruleId": 1,
                                               "confirm": True})
        self.assertEqual(self.calls, [])
        self.assertEqual([(c, o["ok"]) for c, o in self.sent], [(200, False), (200, False)])
        self.assertIn("Ambit2", self.sent[0][1]["error"])
        self.assertIn("for the Ambit1", self.sent[1][1]["error"])

    def test_ambit1_apps_list(self):
        saved = server.selected_legacy_variant
        try:
            server.selected_legacy_variant = lambda: "Duck"
            self.h._handle_apps_read()
            server.selected_legacy_variant = lambda: "Bluebird"
            server.run_tool = lambda script, args, **kw: (0, json.dumps({"ok": True, "entries": [
                {"mode": "Cycling", "place": 0, "storePlace": 0, "ruleId": 10000002,
                 "name": "Storm Alarm"}]}) + "\n", "")
            self.h._handle_apps_read()
        finally:
            server.selected_legacy_variant = saved
        self.assertEqual(self.sent, [(200, {"ok": True, "entries": []}),
                                     (200, {"ok": True, "entries": [{"ruleIdx": 0, "name": "Storm Alarm",
                                                                     "mode": "Cycling", "ruleId": 10000002}]})])

    def test_ambit1_read_counts_its_app(self):
        import legacy_apps
        import test_legacy_sport_mode_manage as M
        region, _store = legacy_apps.install(M.synthetic(76), b"\xff" * legacy_apps.STORE_SIZE,
                                             2, 0, 2, b"IAMRULE\0" + b"\x05" * 30)
        legacy_sport_modes.read_region_live = lambda length=0: region
        server.run_tool = lambda script, args, **kw: (0, json.dumps({"ok": False}) + "\n", "")
        self.h._ambit1_app_names = lambda: {}
        self.h._customodes_read_legacy()
        code, obj = self.sent[0]
        self.assertEqual({m["name"]: m["appCount"] for m in obj["exerciseModes"]},
                         {"Running": 0, "Transition": 0, "Cycling": 1})

    def test_ambit1_read_keeps_its_interval_timer(self):
        region = bytearray(test_legacy_display_edit.synthetic(76))
        blob = legacy_sport_modes.named_blobs(region)[0][0]
        for off, value in ((34, 56), (36, 1), (38, 5), (40, 0x0100), (48, 150), (60, 390)):
            region[blob + off:blob + off + 2] = value.to_bytes(2, "little")
        legacy_sport_modes.read_region_live = lambda length=0: bytes(region)
        server.run_tool = lambda script, args, **kw: (0, json.dumps({"ok": False}) + "\n", "")
        self.h._ambit1_app_names = lambda: {}
        self.h._customodes_read_legacy()
        code, obj = self.sent[0]
        m = obj["exerciseModes"][0]
        self.assertEqual((code, obj["formatType"], obj["sportModes"]), (200, "ambit1", []))
        self.assertEqual((m["autoPause"], m["intTimerFlags"], m["intervalTimer"]),
                         (56, 1, {"enabled": True, "type": "time", "high": 150, "low": 390,
                                  "repetitions": 5}))


if __name__ == "__main__":
    unittest.main()
