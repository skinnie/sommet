#!/usr/bin/env python3
"""Desktop Copy page, Ambit1/2 (watch-protocol/10 L14): each category reaches this family's own
readers and writers instead of the SBEM tools, which cannot reach it. No watch.

  - POIs: legacy_link.py waypoints (route waypoints left out), written with pois-add (the whole
    list rewritten, as poi-remove and route-send do), type and stored time carried;
  - routes: legacy_link.py routes as GPX, written as the Routes page sends one to this family
    (route_pins.py, then legacy_link.py route-send);
  - settings: the Watch Settings page's legacy reader and its 0x0b01 writer, same model only;
  - sport modes: the region as read, mirrored whole with legacy_link.py sport-mode-mirror, same
    model only, refused when its displays show a Suunto App;
  - swim styles: refused with a reason, nothing read.
Not seen on a real watch: André no longer has an Ambit1 or Ambit2 (routes/03).

    python3 tools/test_backend_legacy_copy.py
"""
import base64
import json
import pathlib
import struct
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import server  # noqa: E402
import legacy_link  # noqa: E402
import legacy_sport_modes as LSM  # noqa: E402
import test_legacy_sport_mode_manage as M  # noqa: E402

# Made-up places: never a real position.
WAYPOINTS = [
    {"name": "Fonte", "lat": 1.5, "lon": 2.5, "type": 12, "route_name": "",
     "ctime_year": 2026, "ctime_month": 8, "ctime_day": 3, "ctime_hour": 9, "ctime_minute": 1,
     "ctime_second": 2},
    {"name": "Start", "lat": 1.0, "lon": 2.0, "type": 17, "route_name": "Volta", "index": 0},
    {"name": "End", "lat": 1.1, "lon": 2.1, "type": 17, "route_name": "Volta", "index": 1},
]


class Copy(unittest.TestCase):
    def setUp(self):
        self.calls, self.sent = [], []
        self.saved = (server.run_tool, legacy_sport_modes_read(), server.Handler._sync_connected_device)
        self.answers = {}

        def fake_run_tool(script, args, **kw):
            self.calls.append((script, list(args)))
            key = (script, args[0] if args else "")
            out = self.answers.get(key, {"ok": True, "wrote": True})
            if key == ("route_pins.py", args[0]):
                out = {"ok": True, "gpx": "<gpx><rte><rtept lat='1' lon='2'/></rte></gpx>",
                       "stats": {"pins": 2}}
            if script == "legacy_link.py" and args[0] in ("pois-add", "sport-mode-mirror"):
                self.files = getattr(self, "files", []) + [pathlib.Path(args[1]).read_bytes()]
            return 0, json.dumps(out) + "\n", ""
        server.run_tool = fake_run_tool
        self.h = server.Handler.__new__(server.Handler)
        self.h._send_json = lambda code, obj: self.sent.append((code, obj))

    def tearDown(self):
        server.run_tool, LSM.read_region_live, server.Handler._sync_connected_device = self.saved
        with server._SYNC_LOCK:
            server._SYNC_SNAPSHOTS.clear()

    def test_readers(self):
        self.answers[("legacy_link.py", "waypoints")] = {"ok": True, "waypoints": WAYPOINTS}
        pois, err = self.h._sync_read_pois("Duck")
        self.assertIsNone(err)
        self.assertEqual([(p["name"], p["type"], p["ctime"]["ctime_minute"]) for p in pois],
                         [("Fonte", 12, 1)])
        self.answers[("legacy_link.py", "routes")] = {"ok": True, "routes": [
            {"name": "Volta", "points": [[1.0, 2.0], [1.05, 2.05], [1.1, 2.1]]}]}
        routes, err = self.h._sync_read_routes("Bluebird")
        self.assertEqual((err, routes[0]["name"], routes[0]["pointCount"]), (None, "Volta", 3))
        self.assertEqual(routes[0]["gpx"].count("<rtept"), 3)
        self.assertEqual([s for s, _a in self.calls], ["legacy_link.py", "legacy_link.py"])
        styles, err = self.h._sync_read_swimstyles("Colibri")
        self.assertIsNone(styles)
        self.assertIn("Ambit1 or Ambit2", err)
        self.assertEqual(len(self.calls), 2)                    # nothing read for swim styles

    def test_settings(self):
        saved = legacy_link.settings, legacy_link.settings_write
        written = []
        legacy_link.settings = lambda: {"ok": True, "language": 2, "max_hr": 180,
                                        "alti_baro_mode": 1, "units": {"distance": 0}}
        legacy_link.settings_write = lambda k, v, dry_run=False: (
            written.append((k, v, dry_run)) or {"ok": True})
        try:
            items, err = self.h._sync_read_settings("Greentit")
            self.assertIsNone(err)
            self.assertTrue(items["max_hr"]["writable"] and items["unit_distance"]["writable"])
            self.assertNotIn("alti_baro_mode", items)                 # the 2R has no barometer
            self.assertFalse(self.h._sync_write_setting("Greentit", "alti_baro_mode", 0)["ok"])
            self.assertEqual(self.h._sync_write_setting("Greentit", "max_hr", 175)["ok"], True)
            self.h._sync_write_setting("Greentit", "unit_distance", 1)
        finally:
            legacy_link.settings, legacy_link.settings_write = saved
        self.assertEqual(written, [("max_hr", 175, False), ("units.distance", 1, False)])

    def test_sport_modes_read(self):
        region = M.synthetic(90)
        LSM.read_region_live = lambda length=0: region[:length]
        data, err = self.h._sync_read_sportmodes("Duck")
        self.assertIsNone(err)
        self.assertEqual(base64.b64decode(data["items"]["legacyRegionB64"]),
                         region[:LSM.region_used(region)])
        self.assertEqual(data["count"], 3)
        # A display showing a Suunto App: the region is not offered (its apps would not follow).
        app = bytearray(region)
        at = app.find(struct.pack("<HHHH", 0x0109, 4, 1, 11))
        app[at + 6:at + 8] = struct.pack("<H", 0x0033)
        LSM.read_region_live = lambda length=0: bytes(app[:length])
        data, err = self.h._sync_read_sportmodes("Duck")
        self.assertIsNone(data)
        self.assertIn("Suunto Apps", err)

    def test_apply_onto_an_ambit2(self):
        src = {"slot": "A", "serial": "SRC", "model": "Duck", "displayName": "Ambit2",
               "categories": {
                   "pois": {"supported": True, "count": 1, "items": [
                       {"name": "Fonte", "type": 12, "lat": 1.5, "lon": 2.5,
                        "ctime": {"ctime_year": 2026}}]},
                   "routes": {"supported": True, "count": 1, "items": [
                       {"name": "Volta", "pointCount": 2, "gpx": "<gpx><rte><rtept/></rte></gpx>"}]},
                   "sportModes": {"supported": True, "count": 3, "signature": "a", "items": {
                       "legacyRegionB64": base64.b64encode(b"\x03\x00\x00\x00").decode()}}}}
        tgt = {"slot": "B", "serial": "TGT", "model": "Duck", "displayName": "Ambit2",
               "categories": {"pois": {"supported": True, "count": 0, "items": []},
                              "routes": {"supported": True, "count": 0, "items": []},
                              "sportModes": {"supported": True, "count": 2, "signature": "b",
                                             "items": {}}}}
        with server._SYNC_LOCK:
            server._SYNC_SNAPSHOTS.update({"A": src, "B": tgt})
        server.Handler._sync_connected_device = lambda self: {
            "model": "Duck", "serial": "TGT", "displayName": "Ambit2"}
        self.h._handle_sync_apply({"mode": "mirror", "direction": "AtoB", "confirm": True,
                                   "categories": ["pois", "routes", "sportModes"]})
        code, obj = self.sent[-1]
        self.assertEqual((code, obj["applied"]), (200, 3))
        cmds = [(s, a[0]) for s, a in self.calls]
        self.assertEqual(cmds, [("legacy_link.py", "pois-add"), ("route_pins.py", cmds[1][1]),
                                ("legacy_link.py", "route-send"),
                                ("legacy_link.py", "sport-mode-mirror")])
        self.assertTrue(all("--write" in a for s, a in self.calls if s == "legacy_link.py"))
        pois = json.loads(self.files[0])
        self.assertEqual((pois[0]["name"], pois[0]["type"], pois[0]["ctime"]), ("Fonte", 12, {"ctime_year": 2026}))
        self.assertEqual(self.files[1], b"\x03\x00\x00\x00")

    def test_sport_modes_stay_same_model(self):
        cat = {"supported": True, "count": 1, "signature": "x", "items": {"legacyRegionB64": ""}}
        with server._SYNC_LOCK:
            server._SYNC_SNAPSHOTS.update({
                "A": {"slot": "A", "serial": "1", "model": "Duck", "displayName": "Ambit2",
                      "categories": {"sportModes": cat, "settings": {"supported": True, "items": {}}}},
                "B": {"slot": "B", "serial": "2", "model": "Colibri", "displayName": "Ambit2 S",
                      "categories": {"sportModes": dict(cat, signature="y"),
                                     "settings": {"supported": True, "items": {}}}}})
        plan, err = self.h._sync_build_plan({"categories": ["sportModes", "settings"]})
        self.assertIsNone(err)
        self.assertEqual([c["changes"] for c in plan["categories"]], [[], []])
        self.assertTrue(all("different models" in c["skipped"][0]["reason"] for c in plan["categories"]))


def legacy_sport_modes_read():
    return LSM.read_region_live


class Writers(unittest.TestCase):
    """legacy_link's two Copy writers, the watch replaced by a list and a region."""

    def test_pois_add(self):
        state = {"list": [dict(w) for w in WAYPOINTS], "restores": []}
        saved = legacy_link.waypoints, legacy_link.waypoints_restore
        legacy_link.waypoints = lambda: {"ok": True, "waypoints": [dict(w) for w in state["list"]]}

        def restore(wps):
            state["restores"].append(len(wps))
            state["list"] = list(wps)
        legacy_link.waypoints_restore = restore
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = legacy_link.pois_add([{"name": "Fonte", "lat": 1.5, "lon": 2.5, "type": 12},
                                            {"name": "Moinho", "lat": 1.7, "lon": 2.7, "type": 3}],
                                           write=True, backup_dir=tmp)
                self.assertEqual((out["added"], out["kept"], state["restores"]), (["Moinho"], 3, [4]))
                self.assertEqual(state["list"][:3], WAYPOINTS)       # every waypoint kept
                new = state["list"][3]
                self.assertEqual((new["route_name"], new["type"], new["ctime_year"]), ("", 3, 0))
                self.assertTrue(pathlib.Path(out["backup"]).exists())
                with self.assertRaises(ValueError):                  # past the 100 it holds
                    legacy_link.pois_add([{"name": f"P{i}", "lat": 0.01 * i, "lon": 0.5}
                                          for i in range(97)], write=True, backup_dir=tmp)
        finally:
            legacy_link.waypoints, legacy_link.waypoints_restore = saved

    def test_sport_mode_mirror(self):
        target = M.synthetic(90)
        source = LSM.create_mode(target, "Trilho", 82, "Duck", now=5)
        state = {"flash": target, "writes": []}
        saved = LSM.read_region_live, legacy_link.flash_write
        LSM.read_region_live = lambda length=0: state["flash"][:length]

        def flash_write(addr, data, extra):
            state["writes"].append((addr, len(data), extra))
            state["flash"] = data
        legacy_link.flash_write = flash_write
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = legacy_link.sport_mode_mirror(source[:LSM.region_used(source)], write=True,
                                                    backup_dir=tmp)
                self.assertTrue(out["wrote"])
                self.assertEqual(state["flash"], source)
                self.assertEqual(state["writes"], [(0x2000, 10240, 0xFFFFFFFF)])
                self.assertEqual(pathlib.Path(out["backup"]).read_bytes(), target)
                with self.assertRaises(ValueError):                  # an Ambit1 region onto it
                    legacy_link.sport_mode_mirror(M.synthetic(76), write=True, backup_dir=tmp)
        finally:
            LSM.read_region_live, legacy_link.flash_write = saved


if __name__ == "__main__":
    unittest.main()
