#!/usr/bin/env python3
"""The POI library (tools/poi_library.py) and its backend routes. No watch, no network: the
library is Sommet's own list and never touches a watch.

    python3 tools/test_poi_library.py
"""
import json
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import ambit_format as F  # noqa: E402
import poi_library as L  # noqa: E402
import server  # noqa: E402
import no_live_bluetooth  # noqa: E402
no_live_bluetooth.cut_off(server)


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_saved_poi_is_listed_with_what_was_given(self):
        r = L.save_poi(self.d, "  Refuge  ", 45.9237, 6.8694, ele=2702.4, type_id=10)
        self.assertTrue(r["ok"])
        self.assertFalse(r["existed"])
        (p,) = L.list_pois(self.d)
        self.assertEqual((p["name"], p["lat"], p["lon"], p["ele"], p["type"], p["typeList"]),
                         ("Refuge", 45.9237, 6.8694, 2702.4, 10, "ambit"))
        self.assertEqual(p["id"], r["id"])
        self.assertEqual(p["created"], p["updated"])

    def test_the_same_poi_twice_is_kept_once(self):
        a = L.save_poi(self.d, "Col", 46.0, 7.0)
        b = L.save_poi(self.d, "col", 46.00005, 7.0)          # 5.6 m away, other capitals
        self.assertTrue(b["existed"])
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(L.list_pois(self.d)), 1)

    def test_same_name_elsewhere_and_other_name_here_are_both_new(self):
        L.save_poi(self.d, "Water", 46.0, 7.0)
        self.assertFalse(L.save_poi(self.d, "Water", 46.001, 7.0)["existed"])   # 111 m away
        self.assertFalse(L.save_poi(self.d, "Spring", 46.0, 7.0)["existed"])
        self.assertEqual(len(L.list_pois(self.d)), 3)

    def test_listed_by_name(self):
        for n in ("zermatt", "Aosta", "chamonix"):
            L.save_poi(self.d, n, 46.0, 7.0)
        self.assertEqual([p["name"] for p in L.list_pois(self.d)], ["Aosta", "chamonix", "zermatt"])

    def test_what_cannot_be_a_poi_is_refused_in_words(self):
        self.assertIn("name", L.save_poi(self.d, "   ", 46.0, 7.0)["error"])
        self.assertIn("Earth", L.save_poi(self.d, "x", 91.0, 7.0)["error"])
        self.assertIn("number", L.save_poi(self.d, "x", "north", 7.0)["error"])
        self.assertEqual(L.list_pois(self.d), [])

    def test_a_type_the_list_lacks_becomes_the_plain_one(self):
        L.save_poi(self.d, "a", 46.0, 7.0, type_id=99)
        L.save_poi(self.d, "b", 46.0, 8.0, type_id="x", type_list="traverse")
        L.save_poi(self.d, "c", 46.0, 9.0, type_id=43, type_list="traverse")
        types = {p["name"]: (p["type"], p["typeList"]) for p in L.list_pois(self.d)}
        self.assertEqual(types, {"a": (F.WAYPOINT_TYPE_DEFAULT, "ambit"),
                                 "b": (F.TRAVERSE_WAYPOINT_TYPE_DEFAULT, "traverse"),
                                 "c": (43, "traverse")})

    def test_the_type_for_the_other_kind_of_watch_goes_by_icon(self):
        """2 is "Camp" in the Ambit3's list and "Car" in the Traverse's."""
        self.assertEqual(F.WAYPOINT_TYPES[2], "Camp")
        L.save_poi(self.d, "camp", 46.0, 7.0, type_id=2, type_list="ambit")
        (p,) = L.list_pois(self.d, "traverse")
        self.assertEqual(p["typeHere"], F.waypoint_type_between(2, "Emu", "Loon"))
        self.assertEqual(F.TRAVERSE_WAYPOINT_TYPES[p["typeHere"]][0], "Camp")
        (p,) = L.list_pois(self.d, "ambit")
        self.assertEqual(p["typeHere"], 2)
        self.assertNotIn("typeHere", L.list_pois(self.d)[0])

    def test_rename_and_delete(self):
        r = L.save_poi(self.d, "old", 46.0, 7.0)
        self.assertTrue(L.rename_poi(self.d, r["id"], " new ")["ok"])
        self.assertEqual(L.list_pois(self.d)[0]["name"], "new")
        self.assertFalse(L.rename_poi(self.d, r["id"], "  ")["ok"])
        self.assertTrue(L.delete_poi(self.d, r["id"])["ok"])
        self.assertEqual(L.list_pois(self.d), [])
        self.assertFalse(L.delete_poi(self.d, r["id"])["ok"])
        self.assertFalse(L.rename_poi(self.d, r["id"], "x")["ok"])
        data = json.loads((pathlib.Path(self.d) / "index.json").read_text())
        self.assertEqual([x["id"] for x in data["deleted"]], [r["id"]])      # remembered

    def test_a_damaged_file_reads_as_empty_and_is_not_fatal(self):
        (pathlib.Path(self.d) / "index.json").write_text("{not json")
        self.assertEqual(L.list_pois(self.d), [])
        self.assertTrue(L.save_poi(self.d, "x", 46.0, 7.0)["ok"])

    def test_many_at_once_one_bad(self):
        res = L.save_pois(self.d, [{"name": "a", "lat": 46, "lon": 7},
                                   {"name": "", "lat": 46, "lon": 7},
                                   {"name": "a", "lat": 46, "lon": 7}])
        self.assertEqual([r["ok"] for r in res], [True, False, True])
        self.assertTrue(res[2]["existed"])
        self.assertEqual(len(L.list_pois(self.d)), 1)


FIXTURE = HERE.parent / "android" / "src" / "services" / "__tests__" / "poiLibrary.fixture.json"

# One list of saves, in order, for both apps: what each answers and what the library then holds.
SHARED_SAVES = [
    {"name": "  Refuge  ", "lat": 45.9237, "lon": 6.8694, "ele": 2702.4, "type": 10},
    {"name": "refuge", "lat": 45.92375, "lon": 6.8694},                      # 5.6 m: the same
    {"name": "Refuge", "lat": 45.9247, "lon": 6.8694},                       # 111 m: another
    {"name": "Camp", "lat": 46.0, "lon": 7.0, "type": 2, "typeList": "ambit"},
    {"name": "Car park", "lat": 46.1, "lon": 7.1, "type": 2, "typeList": "traverse"},
    {"name": "Hide", "lat": 46.2, "lon": 7.2, "type": 41, "typeList": "traverse", "ele": 0},
    {"name": "Odd type", "lat": 46.3, "lon": 7.3, "type": 99},
    {"name": "Odd list", "lat": 46.4, "lon": 7.4, "type": 3, "typeList": "garmin"},
    {"name": "", "lat": 46.0, "lon": 7.0},
    {"name": "Nowhere", "lat": 91, "lon": 7.0},
    {"name": "No number", "lat": None, "lon": 7.0},
    {"name": "Äiguille", "lat": 45.8, "lon": 6.9, "ele": None},
    {"name": "aiguille", "lat": 45.8, "lon": 6.9},
    {"name": "Seven decimals", "lat": 45.123456789, "lon": -6.987654321},
]


def shared_cases():
    """What poi_library.py answers for SHARED_SAVES, without the ids and the times."""
    with tempfile.TemporaryDirectory() as d:
        results = L.save_pois(d, SHARED_SAVES)
        ids = {}
        for r in results:
            if r.get("ok"):
                ids.setdefault(r["id"], len(ids))
        answers = [{"ok": True, "entry": ids[r["id"]], "existed": r["existed"]} if r.get("ok")
                   else {"ok": False, "error": r["error"]} for r in results]
        keep = ("name", "lat", "lon", "ele", "type", "typeList")
        listed = {key: [{**{k: p[k] for k in keep}, "typeHere": p["typeHere"]}
                        for p in L.list_pois(d, key)] for key in L.LISTS}
    between = {f"{a}>{b}": [L.type_in(t, a, b) for t in range(-1, 51)]
               for a in L.LISTS for b in L.LISTS}
    return {"about": "Made by tools/test_poi_library.py --write-fixture. The desktop "
                     "(tools/poi_library.py) and Android (PoiLibrary.ts) must both give this.",
            "saves": SHARED_SAVES, "answers": answers, "listed": listed,
            "typeBetweenFromMinus1To50": between}


class SharedWithAndroid(unittest.TestCase):
    def test_the_fixture_android_is_tested_against_is_what_the_desktop_gives(self):
        self.assertEqual(json.loads(FIXTURE.read_text(encoding="utf-8")), shared_cases(),
                         "run: python3 tools/test_poi_library.py --write-fixture")


class Routes(unittest.TestCase):
    """The backend routes, with the library in a scratch folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (server.Handler.POI_LIBRARY_DIR, server.run_tool)
        server.Handler.POI_LIBRARY_DIR = self.tmp.name
        self.tools = []
        server.run_tool = lambda script, *_a, **_k: self.tools.append(script) or (1, "", "")
        self.sent = []
        self.h = server.Handler.__new__(server.Handler)
        self.h._send_json = lambda code, obj: self.sent.append((code, obj))
        self.h.path = "/api/poilibrary"

    def tearDown(self):
        server.Handler.POI_LIBRARY_DIR, server.run_tool = self.saved
        self.tmp.cleanup()

    def call(self, action, body=None, path="/api/poilibrary"):
        self.h.path = path
        self.h._handle_poi_library(action, body or {})
        return self.sent[-1]

    def test_save_list_rename_delete(self):
        code, r = self.call("save", {"name": "Hut", "lat": 46.5, "lon": 7.5, "type": 10})
        self.assertEqual((code, r["saved"], r["existed"]), (200, 1, 0))
        poi_id = r["results"][0]["id"]
        code, r = self.call("save", {"pois": [{"name": "Hut", "lat": 46.5, "lon": 7.5},
                                              {"name": "Lake", "lat": 46.6, "lon": 7.5}]})
        self.assertEqual((code, r["saved"], r["existed"]), (200, 1, 1))
        code, r = self.call("list", path="/api/poilibrary?for=traverse")
        self.assertEqual([p["name"] for p in r["pois"]], ["Hut", "Lake"])
        self.assertTrue(all("typeHere" in p for p in r["pois"]))
        self.assertEqual(self.call("rename", {"id": poi_id, "name": "Cabane"})[0], 200)
        self.assertEqual(self.call("delete", {"id": poi_id})[0], 200)
        code, r = self.call("list")
        self.assertEqual([p["name"] for p in r["pois"]], ["Lake"])

    def test_errors_come_back_as_400_with_words(self):
        code, r = self.call("save", {"name": "", "lat": 46.5, "lon": 7.5})
        self.assertEqual(code, 400)
        self.assertIn("name", r["error"])
        self.assertEqual(self.call("save", {"pois": []})[0], 400)
        self.assertEqual(self.call("delete", {"id": "nope"})[0], 400)

    def test_the_library_never_runs_a_watch_tool(self):
        self.call("save", {"name": "Hut", "lat": 46.5, "lon": 7.5})
        self.call("list")
        self.assertEqual(self.tools, [])


if __name__ == "__main__":
    if "--write-fixture" in sys.argv:
        FIXTURE.write_text(json.dumps(shared_cases(), ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
        print("wrote", FIXTURE)
        sys.exit(0)
    unittest.main()
