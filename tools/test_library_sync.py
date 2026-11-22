#!/usr/bin/env python3
"""The route library and the POI library shared through Sommet Sync (tools/library_sync.py).

Two "devices" (two pairs of scratch folders) against the Python twin of sync.php on a free
port: what is saved, renamed or deleted on one reaches the other; the same thing saved on both
before they met ends up once; an older server is skipped quietly; a GPX that did not come is
asked again. No watch.

    python3 tools/test_library_sync.py
"""
import os
import pathlib
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import library_sync as S  # noqa: E402
import poi_library as P  # noqa: E402
import route_library as R  # noqa: E402


def gpx(n):
    pts = "".join(f'<trkpt lat="{46 + i * 0.001 * n}" lon="{7 + i * 0.001}"><ele>{500 + i}</ele></trkpt>'
                  for i in range(6))
    return f'<gpx version="1.1"><trk><trkseg>{pts}</trkseg></trk></gpx>'


class Device:
    def __init__(self, root, name, http):
        self.routes = os.path.join(root, name, "Routes")
        self.pois = os.path.join(root, name, "Pois")
        self.http = http

    def sync(self):
        return (S.sync_library(S.Routes(self.routes), self.http),
                S.sync_library(S.Pois(self.pois), self.http))

    def route_names(self):
        return sorted(r["name"] for r in R.list_routes(self.routes))

    def poi_names(self):
        return [p["name"] for p in P.list_pois(self.pois)]


class TwoDevices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = tempfile.TemporaryDirectory()
        cls.token = secrets.token_hex(8)                 # made up for this run
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        cls.proc = subprocess.Popen(
            [sys.executable, str(HERE / "sommet_sync_devserver.py"), "--token", cls.token,
             "--port", str(cls.port), "--db", os.path.join(cls.srv.name, "t.db"),
             "--blobs", os.path.join(cls.srv.name, "blobs")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=5)
        cls.srv.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # Each test gets its own collections on the one server: a prefix on the names would need
        # server support, so each test uses its own uids and starts from "now" instead.
        http = S.Http(f"http://127.0.0.1:{self.port}/sync.php", self.token)
        self.a = Device(self.tmp.name, "a", http)
        self.b = Device(self.tmp.name, "b", http)
        # Start both after everything earlier tests left on the server.
        now = http.pull("pois", 0)["now"]
        for dev in (self.a, self.b):
            for d in (dev.routes, dev.pois):
                S._store_state(d, {"since": now, "pushed": {}})

    def tearDown(self):
        self.tmp.cleanup()

    def test_what_one_saves_the_other_gets(self):
        R.save(self.a.routes, "Tour du Lac", gpx(1))
        P.save_poi(self.a.pois, "Refuge", 45.85, 6.83, ele=3835, type_id=10)
        r, p = self.a.sync()
        self.assertEqual((r["sent"], p["sent"]), (1, 1))
        r, p = self.b.sync()
        self.assertEqual((r["changed"], p["changed"]), (1, 1))
        self.assertEqual(self.b.route_names(), ["Tour du Lac"])
        (route,) = R.list_routes(self.b.routes)
        self.assertEqual(R.get(self.b.routes, route["id"])["gpx"], gpx(1))
        self.assertGreater(route["distanceMeters"], 0)               # figures worked out here
        (poi,) = P.list_pois(self.b.pois)
        self.assertEqual((poi["name"], poi["lat"], poi["lon"], poi["ele"], poi["type"], poi["typeList"]),
                         ("Refuge", 45.85, 6.83, 3835.0, 10, "ambit"))
        # Nothing bounces: a second round changes and sends nothing on either side.
        for dev in (self.b, self.a, self.b):
            r, p = dev.sync()
            self.assertEqual((r["changed"], r["sent"], p["changed"], p["sent"]), (0, 0, 0, 0))

    def test_a_rename_and_a_delete_travel(self):
        rid = R.save(self.a.routes, "Old name", gpx(2))["id"]
        pid = P.save_poi(self.a.pois, "Col", 46.0, 7.0)["id"]
        self.a.sync(); self.b.sync()
        time.sleep(1.1)                                   # `updated` is in whole seconds
        R.rename(self.b.routes, rid, "New name")
        P.rename_poi(self.b.pois, pid, "Col du Midi")
        self.b.sync(); self.a.sync()
        self.assertEqual(self.a.route_names(), ["New name"])
        self.assertEqual(self.a.poi_names(), ["Col du Midi"])
        R.delete(self.a.routes, rid)
        P.delete_poi(self.a.pois, pid)
        r, p = self.a.sync()
        self.assertEqual((r["deleted_sent"], p["deleted_sent"]), (1, 1))
        self.b.sync()
        self.assertEqual((self.b.route_names(), self.b.poi_names()), ([], []))
        self.assertFalse(os.path.exists(os.path.join(self.b.routes, rid + ".gpx")))
        # It does not come back, and the delete marks are cleared once the server knows.
        self.a.sync(); self.b.sync(); self.a.sync()
        self.assertEqual((self.a.route_names(), self.a.poi_names()), ([], []))
        self.assertEqual(R.deleted(self.a.routes), [])
        self.assertEqual(S.Pois(self.a.pois).tombstones(), [])

    def test_the_same_thing_saved_on_both_ends_up_once(self):
        ra = R.save(self.a.routes, "Loop", gpx(3))["id"]
        pa = P.save_poi(self.a.pois, "Spring", 46.5, 7.5)["id"]
        time.sleep(1.1)                                   # b's copies are the younger ones
        R.save(self.b.routes, "Loop (b)", gpx(3))
        P.save_poi(self.b.pois, "spring", 46.50004, 7.5)
        self.a.sync(); self.b.sync(); self.a.sync(); self.b.sync()
        for dev in (self.a, self.b):
            self.assertEqual([r["id"] for r in R.list_routes(dev.routes)], [ra])
            self.assertEqual([p["id"] for p in P.list_pois(dev.pois)], [pa])
            self.assertEqual(dev.route_names(), ["Loop"])
            self.assertEqual(dev.poi_names(), ["Spring"])

    def test_a_route_whose_gpx_did_not_come_is_asked_again(self):
        rid = R.save(self.a.routes, "Late", gpx(4))["id"]
        self.a.sync()

        class NoBlob(S.Http):
            def get_blob(self, uid):
                raise FileNotFoundError(uid)
        real = self.b.http
        self.b.http = NoBlob(real.url, real.token)
        r, _ = self.b.sync()
        self.assertEqual((r["changed"], self.b.route_names()), (0, []))
        self.b.http = real
        r, _ = self.b.sync()
        self.assertEqual((r["changed"], self.b.route_names()), (1, ["Late"]))
        self.assertEqual([x["id"] for x in R.list_routes(self.b.routes)], [rid])

    def test_an_older_server_is_skipped_quietly(self):
        class Old(S.Http):
            def pull(self, collection, since):
                raise S.OlderServer(collection)
        P.save_poi(self.a.pois, "Kept here", 46.0, 7.0)
        self.a.http = Old("http://127.0.0.1:1/sync.php", "x")
        r, p = self.a.sync()
        self.assertTrue(r["older_server"] and p["older_server"])
        self.assertEqual(self.a.poi_names(), ["Kept here"])

    def test_no_server_is_an_error_in_words_and_nothing_is_lost(self):
        P.save_poi(self.a.pois, "Kept here", 46.0, 7.0)
        out = S.sync_all(self.a.routes, self.a.pois, "http://127.0.0.1:1/sync.php", "x")
        self.assertFalse(out["ok"])
        self.assertIn("could not be reached", out["error"])
        self.assertEqual(self.a.poi_names(), ["Kept here"])
        self.assertFalse(S.sync_all(self.a.routes, self.a.pois, "", "")["ok"])


class Rules(unittest.TestCase):
    """plan_pull(), with no files and no server."""

    @staticmethod
    def same(a, b):
        return a.get("key") is not None and a.get("key") == b.get("key")

    def plan(self, local, tombs, records=(), deleted=()):
        return S.plan_pull(local, tombs, {"records": list(records), "deleted": list(deleted)}, self.same)

    def test_new_and_newer_are_taken_older_is_not(self):
        local = [{"uid": "a", "updated_at": 2000, "created": 1}]
        p = self.plan(local, [], [{"uid": "a", "updated_at": 1000}, {"uid": "b", "updated_at": 5}])
        self.assertEqual(sorted(p["put"]), ["b"])
        p = self.plan(local, [], [{"uid": "a", "updated_at": 3000, "name": "n"}])
        self.assertEqual(sorted(p["put"]), ["a"])

    def test_deleted_here_stays_deleted_unless_changed_elsewhere_after(self):
        tomb = [{"id": "a", "at": 10}]                                     # 10 s = 10000 ms
        p = self.plan([], tomb, [{"uid": "a", "updated_at": 9000}])
        self.assertEqual((p["put"], p["forget_tombstones"]), ({}, []))
        p = self.plan([], tomb, [{"uid": "a", "updated_at": 11000}])
        self.assertEqual((sorted(p["put"]), p["forget_tombstones"]), (["a"], ["a"]))

    def test_a_delete_from_elsewhere_removes_it_here(self):
        p = self.plan([{"uid": "a", "updated_at": 1, "created": 1}], [{"id": "a", "at": 1}], deleted=["a", "zz"])
        self.assertEqual((p["remove"], p["forget_tombstones"]), (["a"], ["a"]))

    def test_two_copies_of_the_same_thing_the_older_stays(self):
        mine = [{"uid": "m", "updated_at": 1, "created": 100, "key": "k"}]
        p = self.plan(mine, [], [{"uid": "t", "updated_at": 1, "created": 50, "key": "k"}])
        self.assertEqual((sorted(p["put"]), p["remove"], p["kill"]), (["t"], ["m"], ["m"]))
        p = self.plan(mine, [], [{"uid": "t", "updated_at": 1, "created": 200, "key": "k"}])
        self.assertEqual((p["put"], p["remove"], p["kill"]), ({}, [], ["t"]))
        p = self.plan(mine, [], [{"uid": "a", "updated_at": 1, "created": 100, "key": "k"}])   # a tie: the smaller uid
        self.assertEqual(p["kill"], ["m"])


FIXTURE = HERE.parent / "android" / "src" / "services" / "__tests__" / "librarySync.fixture.json"

# Cases for plan_pull(), given to both apps. Two entries are "the same thing" when their `key`
# is equal (a route's SHA-1, or a POI's name and place, in the real libraries).
SHARED_PULLS = [
    {"about": "nothing here, two new records",
     "local": [], "tombstones": [],
     "records": [{"uid": "a", "updated_at": 5000, "created": 5, "name": "A"},
                 {"uid": "b", "updated_at": 6000, "created": 6, "name": "B"}], "deleted": []},
    {"about": "newer wins, older and equal do not",
     "local": [{"uid": "a", "updated_at": 2000, "created": 1}, {"uid": "b", "updated_at": 2000, "created": 1},
               {"uid": "c", "updated_at": 2000, "created": 1}], "tombstones": [],
     "records": [{"uid": "a", "updated_at": 3000, "name": "new"}, {"uid": "b", "updated_at": 1000},
                 {"uid": "c", "updated_at": 2000}], "deleted": []},
    {"about": "deleted here: stays deleted, unless changed elsewhere after",
     "local": [], "tombstones": [{"id": "a", "at": 10}, {"id": "b", "at": 10}, {"id": "c", "at": 10}],
     "records": [{"uid": "a", "updated_at": 9000}, {"uid": "b", "updated_at": 10000},
                 {"uid": "c", "updated_at": 10001}], "deleted": []},
    {"about": "deleted elsewhere",
     "local": [{"uid": "a", "updated_at": 1, "created": 1}, {"uid": "b", "updated_at": 1, "created": 1}],
     "tombstones": [{"id": "a", "at": 1}], "records": [], "deleted": ["a", "zz"]},
    {"about": "the same thing twice: the older stays",
     "local": [{"uid": "m", "updated_at": 1, "created": 100, "key": "k"}], "tombstones": [],
     "records": [{"uid": "t", "updated_at": 1, "created": 50, "key": "k"}], "deleted": []},
    {"about": "the same thing twice: ours is older",
     "local": [{"uid": "m", "updated_at": 1, "created": 100, "key": "k"}], "tombstones": [],
     "records": [{"uid": "t", "updated_at": 1, "created": 200, "key": "k"}], "deleted": []},
    {"about": "the same thing twice, same second: the smaller uid stays",
     "local": [{"uid": "m", "updated_at": 1, "created": 100, "key": "k"}], "tombstones": [],
     "records": [{"uid": "a", "updated_at": 1, "created": 100, "key": "k"},
                 {"uid": "z", "updated_at": 1, "created": 100, "key": "k"}], "deleted": []},
    {"about": "a record with no uid is skipped; a delete and a record of the same uid in one pull",
     "local": [{"uid": "a", "updated_at": 1, "created": 1}], "tombstones": [],
     "records": [{"updated_at": 9}, {"uid": "a", "updated_at": 9, "created": 1}], "deleted": ["a"]},
]
SHA1_TEXTS = ["", "abc", "Refuge du Goûter", "a" * 55, "a" * 56, "a" * 64, "a" * 1000,
              '<gpx version="1.1"><trk><trkseg><trkpt lat="46" lon="7"/></trkseg></trk></gpx>', "日本 🏔"]


def shared_cases():
    import hashlib
    same = Rules.same
    return {"about": "Made by tools/test_library_sync.py --write-fixture. The desktop "
                     "(tools/library_sync.py) and Android (LibrarySync.ts) must both give this.",
            "pulls": [{**c, "plan": S.plan_pull(c["local"], c["tombstones"],
                                                {"records": c["records"], "deleted": c["deleted"]}, same)}
                      for c in SHARED_PULLS],
            "sha1": [{"text": t, "hex": hashlib.sha1(t.encode("utf-8")).hexdigest()} for t in SHA1_TEXTS]}


class SharedWithAndroid(unittest.TestCase):
    def test_the_fixture_android_is_tested_against_is_what_the_desktop_gives(self):
        import json
        self.assertEqual(json.loads(FIXTURE.read_text(encoding="utf-8")), shared_cases(),
                         "run: python3 tools/test_library_sync.py --write-fixture")


if __name__ == "__main__":
    if "--write-fixture" in sys.argv:
        import json
        FIXTURE.write_text(json.dumps(shared_cases(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("wrote", FIXTURE)
        sys.exit(0)
    unittest.main()
