#!/usr/bin/env python3
"""Offline, hardware-free tests for the offline-routing feature (route_plan / track_color /
poi_search / geo_util). Runs anywhere - no watch, no BRouter server, no downloaded data.

    ./tools/test_offline_routing.py            # or: python3 -m unittest test_offline_routing
"""

import math
import tempfile
import unittest

import geo_util
import poi_search
import route_plan
import track_color


class GeoUtil(unittest.TestCase):
    def test_haversine_known(self):
        # ~1 deg of latitude is ~111 km
        d = geo_util.haversine_m(46.0, 6.0, 47.0, 6.0)
        self.assertTrue(110000 < d < 112000, d)

    def test_cumulative_monotonic(self):
        pts = [(46.0, 6.0), (46.0, 6.01), (46.0, 6.02)]
        cd = geo_util.cumulative_distances(pts)
        self.assertEqual(cd[0], 0.0)
        self.assertLess(cd[0], cd[1])
        self.assertLess(cd[1], cd[2])

    def test_gpx_roundtrip(self):
        pts = [{"lat": 46.0, "lon": 6.0, "ele": 400.0},
               {"lat": 46.001, "lon": 6.001, "ele": 410.0}]
        gpx = geo_util.points_to_gpx(pts, name="t & <est>")
        back = geo_util.parse_gpx_points(gpx)
        self.assertEqual(len(back), 2)
        self.assertAlmostEqual(back[1]["ele"], 410.0)
        self.assertAlmostEqual(back[0]["lat"], 46.0)

    def test_gpx_ampersand_escaped(self):
        gpx = geo_util.points_to_gpx([{"lat": 1, "lon": 2}], name="a & b")
        self.assertIn("a &amp; b", gpx)


class TrackColor(unittest.TestCase):
    def test_selftest(self):
        self.assertEqual(track_color._selftest(), 0)

    def test_flat_track_is_flat(self):
        pts = [{"lat": 46.0, "lon": 6.0 + i * 0.001, "ele": 100.0} for i in range(50)]
        r = track_color.colorize(pts, step_m=25, window_m=100)
        self.assertTrue(r["ok"])
        self.assertEqual(r["summary"]["ascent_m"], 0.0)
        self.assertTrue(all(s["bucket"] == "flat" for s in r["segments"]))

    def test_missing_elevation_is_grey_not_faked(self):
        pts = [{"lat": 46.0, "lon": 6.0 + i * 0.001} for i in range(20)]  # no ele
        r = track_color.colorize(pts, step_m=25, window_m=100)
        self.assertTrue(r["ok"])
        self.assertFalse(r["summary"]["has_elevation"])
        self.assertTrue(all(s["color"] == track_color.UNKNOWN_COLOR for s in r["segments"]))

    def test_too_few_points(self):
        self.assertFalse(track_color.colorize([{"lat": 1, "lon": 2, "ele": 3}])["ok"])

    def test_classify_boundaries(self):
        b = track_color.DEFAULT_BUCKETS
        self.assertEqual(track_color.classify(-10, b)["key"], "descent")
        self.assertEqual(track_color.classify(0, b)["key"], "flat")
        self.assertEqual(track_color.classify(7.5, b)["key"], "moderate")
        self.assertEqual(track_color.classify(99, b)["key"], "brutal")


class RoutePlan(unittest.TestCase):
    def test_selftest(self):
        self.assertEqual(route_plan._selftest(), 0)

    def test_geojson_parse_empty(self):
        self.assertFalse(route_plan.parse_geojson({"features": []})["ok"])

    def test_unreachable_server_is_clean_error(self):
        # nothing is listening here; must be a dict error, not an exception
        r = route_plan.plan_route([(6.0, 46.0), (6.1, 46.1)],
                                  url="http://127.0.0.1:1", timeout=2)
        self.assertFalse(r["ok"])
        self.assertIn("hint", r)

    def test_needs_two_points(self):
        self.assertFalse(route_plan.plan_route([(6.0, 46.0)])["ok"])


class PoiSearch(unittest.TestCase):
    def test_selftest(self):
        self.assertEqual(poi_search._selftest(), 0)

    def test_build_and_along(self):
        db = tempfile.mktemp(suffix=".sqlite")
        poi_search.build(db, poi_search._SAMPLE)
        con = poi_search.open_db(db)
        route = [{"lat": 45.8505, "lon": 6.832}, {"lat": 45.851, "lon": 6.831}]
        along = poi_search.search_along(con, route, buffer_m=300)
        self.assertTrue(any("Goûter" in r["name"] for r in along))
        con.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
