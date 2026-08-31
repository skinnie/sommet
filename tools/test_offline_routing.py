#!/usr/bin/env python3
"""Offline, hardware-free tests for the route-weather feature (track_color / weather_route /
astro / geo_util). Runs anywhere - no watch, no network, no downloaded data. The offline
BRouter route-planner (route_plan) and POI-DB search (poi_search) were removed 2026-08-31; see
docs/offline-routing.md.

    ./tools/test_offline_routing.py            # or: python3 -m unittest test_offline_routing
"""

import unittest

import astro
import geo_util
import track_color
import weather_route
from datetime import date


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


class Astro(unittest.TestCase):
    def test_selftest(self):
        self.assertEqual(astro._selftest(), 0)

    def test_events_ordered(self):
        r = astro.events(date(2026, 8, 29), 40.32, -7.61, 1.0)
        sm = r["sun_min"]
        order = [sm["civil_dawn"], sm["sunrise"], sm["solar_noon"], sm["sunset"], sm["civil_dusk"]]
        self.assertEqual(order, sorted(order))
        self.assertTrue(0.0 <= r["moon_illumination"] <= 1.0)

    def test_polar_day_no_sunset(self):
        r = astro.events(date(2026, 6, 21), 78.0, 15.0, 1.0)
        self.assertIsNone(r["sun_min"]["sunset"])


class WeatherRoute(unittest.TestCase):
    def test_selftest(self):
        self.assertEqual(weather_route._selftest(), 0)

    def test_wind_relation(self):
        # heading east (90): wind FROM east is a headwind, FROM west a tailwind
        self.assertEqual(weather_route._wind_relation(90, 90), "headwind")
        self.assertEqual(weather_route._wind_relation(270, 90), "tailwind")
        self.assertEqual(weather_route._wind_relation(0, 90), "crosswind")

    def test_eta_increases_and_weather_present(self):
        r = weather_route.plan(weather_route._synthetic_route(), start="09:00",
                               date=date(2026, 8, 29), pace_kmh=4.5, tz_offset_h=1.0,
                               fetch=weather_route._synthetic_fetch)
        self.assertTrue(r["ok"])
        etas = [p["eta_min"] for p in r["profile"]]
        self.assertEqual(etas, sorted(etas))
        self.assertTrue(all("temp_c" in p and "rain_mm" in p for p in r["profile"]))

    def test_too_few_points(self):
        self.assertFalse(weather_route.plan([{"lat": 1, "lon": 2}])["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
