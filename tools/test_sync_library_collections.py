#!/usr/bin/env python3
"""Sommet Sync holds the route library and the POI library (collections `routes` and `pois`).

Runs the Python twin of sync.php (tools/sommet_sync_devserver.py) on a free port with a scratch
database and does what a device will do: a route record with its GPX as a blob, a POI record,
a rename (newer wins), a delete (remembered). Also: both servers list the same collections,
and an unknown collection is answered 400, which is how an app tells an older server.

    python3 tools/test_sync_library_collections.py
"""
import json
import os
import pathlib
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
GPX = '<gpx version="1.1"><trk><trkseg><trkpt lat="46" lon="7"/><trkpt lat="46.1" lon="7.1"/></trkseg></trk></gpx>'


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.token = secrets.token_hex(8)                 # a made-up token for this run only
        cls.port = free_port()
        cls.proc = subprocess.Popen(
            [sys.executable, str(HERE / "sommet_sync_devserver.py"), "--token", cls.token,
             "--port", str(cls.port), "--db", os.path.join(cls.tmp.name, "t.db"),
             "--blobs", os.path.join(cls.tmp.name, "blobs")],
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
        cls.tmp.cleanup()

    def http(self, method, params, body=None, raw=None):
        url = f"http://127.0.0.1:{self.port}/sync.php?" + urllib.parse.urlencode(params)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"X-Sommet-Token": self.token})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def pull(self, c, since=0):
        st, b = self.http("GET", {"c": c, "since": since})
        self.assertEqual(st, 200)
        return json.loads(b)

    def test_a_route_with_its_gpx_then_renamed_then_deleted(self):
        rec = {"uid": "1795340000-ab12cd34", "name": "Tour du Lac", "created": 1795340000,
               "sha1": "0" * 40, "updated_at": 1000}
        st, _ = self.http("POST", {"blob": 1, "uid": "route:" + rec["uid"], "fmt": "gpx"}, raw=GPX.encode())
        self.assertEqual(st, 200)
        st, _ = self.http("POST", {"c": "routes"}, {"records": [rec], "deleted": []})
        self.assertEqual(st, 200)
        got = self.pull("routes")
        self.assertEqual([r["name"] for r in got["records"]], ["Tour du Lac"])
        st, b = self.http("GET", {"blob": 1, "uid": "route:" + rec["uid"], "fmt": "gpx"})
        self.assertEqual((st, b.decode()), (200, GPX))

        self.http("POST", {"c": "routes"}, {"records": [{**rec, "name": "older", "updated_at": 500}], "deleted": []})
        self.http("POST", {"c": "routes"}, {"records": [{**rec, "name": "Lake loop", "updated_at": 2000}], "deleted": []})
        self.assertEqual([r["name"] for r in self.pull("routes")["records"]], ["Lake loop"])

        self.http("POST", {"c": "routes"}, {"records": [], "deleted": [rec["uid"]]})
        got = self.pull("routes")
        self.assertEqual(got["records"], [])
        self.assertIn(rec["uid"], got["deleted"])

    def test_a_poi(self):
        rec = {"uid": "ee4717aba40d4470ba4fd7ce6fe23dff", "name": "Refuge", "lat": 45.8512,
               "lon": 6.8306, "ele": 3835.0, "type": 10, "typeList": "ambit", "created": 1795340000,
               "updated_at": 1795340000000}
        st, _ = self.http("POST", {"c": "pois"}, {"records": [rec], "deleted": []})
        self.assertEqual(st, 200)
        (back,) = self.pull("pois")["records"]
        self.assertEqual({k: back[k] for k in rec}, rec)

    def test_an_unknown_collection_is_a_400(self):
        """What an app sees from a server that predates routes and pois."""
        st, b = self.http("GET", {"c": "bookmarks", "since": 0})
        self.assertEqual(st, 400)
        self.assertIn("unknown collection", b.decode())


class Twins(unittest.TestCase):
    def test_sync_php_and_the_python_twin_list_the_same_collections(self):
        php = (ROOT / "sync-server" / "sync.php").read_text(encoding="utf-8")
        m = re.search(r"\$COLLECTIONS\s*=\s*\[(.*?)\];", php, re.S)
        in_php = set(re.findall(r"'([a-z_]+)'", m.group(1)))
        sys.path.insert(0, str(HERE))
        import sommet_sync_devserver                      # noqa: PLC0415
        self.assertEqual(in_php, set(sommet_sync_devserver.COLLECTIONS))
        self.assertTrue({"routes", "pois"} <= in_php)


if __name__ == "__main__":
    unittest.main()
