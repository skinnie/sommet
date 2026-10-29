#!/usr/bin/env python3
"""One route, one climb figure: the planner, the route library and the watch agree.

Desktop audit 2026-10-29: Boucle 5km showed 46 m up / 47 m down in the route library and
40 / 40 m in the planner. The library summed every rise of the GPX (the rule the watch uses for a
sent route: ambit_format.route_elevation_m, SuuntoLink's calculateElevation); the planner summed
its 30 m resample, which flattens small rises. Both now use the watch's rule, and the planner's
per-day climb adds up to its total.

    python3 tools/test_route_climb_agree.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ambit_format as F
import geo_util
import route_library
import track_color

BOUCLE = "/media/skinnie/05a64eef-3fdf-450f-aaf7-09748f80297c/ambit-app/assets/Boucle+5km.gpx"


def bumpy_gpx(n=200):
    """~6 km straight line whose elevation rises 1-2 m every few metres: small bumps a 30 m
    resample smooths away, so the old planner total came out lower than the full-track one."""
    pts = []
    for i in range(n):
        ele = 50 + (i % 7) * 1.5 + i * 0.1
        pts.append(f'<trkpt lat="{50.6 + i * 0.00027:.6f}" lon="3.05"><ele>{ele:.1f}</ele></trkpt>')
    return ('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
            '<metadata><name>Bumps</name></metadata><trk><name>Bumps</name><trkseg>'
            + "".join(pts) + "</trkseg></trk></gpx>")


class ClimbAgrees(unittest.TestCase):
    def check(self, gpx):
        pts = geo_util.parse_gpx_points(gpx)
        want = F.route_elevation_m([(p["lat"], p["lon"], p["ele"]) for p in pts])
        planner = track_color.colorize(pts)
        _d, asc, desc, _n, _t = route_library._stats(gpx)
        self.assertEqual((planner["summary"]["ascent_m"], planner["summary"]["descent_m"]), want)
        self.assertEqual((asc, desc), want)
        return planner, want

    def test_bumpy_route_planner_equals_library_equals_watch(self):
        planner, want = self.check(bumpy_gpx())
        # the old planner rule (sum of the resample) really did differ on this track
        eles = [r["ele_m"] for r in planner["profile"] if r["ele_m"] is not None]
        old = sum(b - a for a, b in zip(eles, eles[1:]) if b > a)
        self.assertGreater(want[0] - old, 5)

    def test_days_add_up_to_the_total(self):
        planner, want = self.check(bumpy_gpx())
        asc = [r["asc_m"] for r in planner["profile"]]
        self.assertEqual(asc[0], 0)
        self.assertTrue(all(b >= a for a, b in zip(asc, asc[1:])), "climb only ever grows")
        self.assertLessEqual(abs(asc[-1] - want[0]), 1.0)

    @unittest.skipUnless(os.path.exists(BOUCLE), "Boucle+5km.gpx not on this machine")
    def test_boucle_5km_is_46_47_like_the_watch(self):
        # The Peak read 5269 m, 46 m up and 47 m down back for this route on 2026-10-27.
        with open(BOUCLE, encoding="utf-8") as f:
            gpx = f.read()
        _planner, want = self.check(gpx)
        self.assertEqual(want, (46, 47))

    def test_old_library_entries_get_the_new_figures_names_kept(self):
        gpx = bumpy_gpx()
        with tempfile.TemporaryDirectory() as d:
            saved = route_library.save(d, "My bumps", gpx)
            path = os.path.join(d, "index.json")
            with open(path, encoding="utf-8") as f:
                index = json.load(f)
            index[0].update(ascentMeters=1, descentMeters=2)   # saved by the old rule
            index[0].pop("climbRule")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(index, f)
            listed = route_library.list_routes(d)
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["id"], saved["id"])
            self.assertEqual(listed[0]["name"], "My bumps")
            pts = geo_util.parse_gpx_points(gpx)
            want = F.route_elevation_m([(p["lat"], p["lon"], p["ele"]) for p in pts])
            self.assertEqual((listed[0]["ascentMeters"], listed[0]["descentMeters"]), want)
            self.assertEqual(listed[0]["climbRule"], route_library.CLIMB_RULE)


if __name__ == "__main__":
    unittest.main()
