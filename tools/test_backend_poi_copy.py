#!/usr/bin/env python3
"""Copy to watch, POIs between watch families: the type is carried over by its icon.

An Ambit3 numbers its 18 POI types differently from a Traverse's 48 (2 is "Camp" on one and "Car"
on the other, tools/ambit_format.py), so the number read off the source is not the number to
write to the target. No watch, no network.

    python3 tools/test_backend_poi_copy.py
"""
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import server  # noqa: E402


def snap(model, pois):
    return {"model": model, "serial": model, "displayName": model,
            "categories": {"pois": {"supported": True, "count": len(pois), "items": pois}}}


def poi(name, type_, lat=46.5, lon=7.5):
    return {"name": name, "type": type_, "lat": lat, "lon": lon}


def planned(src, tgt):
    changes, _ = server._sync_diff_pois(src, tgt)
    return {c["poi"]["name"]: c["poi"]["type"] for c in changes}


class PoiCopy(unittest.TestCase):
    def test_peak_to_traverse_alpha(self):
        src = snap("Emu", [poi("Hut", 10), poi("Camp", 2, 46.6), poi("Top", 17, 46.7)])
        self.assertEqual(planned(src, snap("Loon", [])), {"Hut": 9, "Camp": 4, "Top": 26})

    def test_traverse_alpha_to_peak(self):
        src = snap("Loon", [poi("Deer", 43), poi("Tent", 5, 46.6), poi("Rub", 39, 46.7)])
        self.assertEqual(planned(src, snap("Emu", [])), {"Deer": 17, "Tent": 2, "Rub": 8})

    def test_same_family_keeps_the_number(self):
        self.assertEqual(planned(snap("Emu", [poi("Camp", 2)]), snap("Ibisbill", [])), {"Camp": 2})
        self.assertEqual(planned(snap("Jabiru", [poi("Deer", 43)]), snap("Loon", [])), {"Deer": 43})

    def test_a_traverse_pois_altitude_is_read_and_carried(self):
        raw = ("  0x53 sml.WayPoint  [44]\n"
               "        Name='Shot 01'  RouteName=''  Timestamp='2026-11-08T07:00:30'  RouteIndex=0  "
               "Type=46  SubType=0  TypeIndex=1  Flags=1  Location.Latitude=460207000  "
               "Location.Longitude=77491000  Altitude=1234\n"
               "  0x55 sml.WayPoint  [40]\n"
               "        Name='Hut'  RouteName=''  Timestamp='x'  RouteIndex=0  Type=10  SubType=0  "
               "TypeIndex=0  Flags=1  Location.Latitude=465000000  Location.Longitude=75000000\n")
        pois = server._sync_parse_pois(raw)
        self.assertEqual([(p["name"], p.get("altitude")) for p in pois], [("Shot 01", 1234), ("Hut", None)])
        changes, _ = server._sync_diff_pois(snap("Loon", pois[:1]), snap("Jabiru", []))
        self.assertEqual(changes[0]["poi"]["altitude"], 1234)

    def test_a_poi_the_target_has_is_not_planned(self):
        src = snap("Emu", [poi("Hut", 10)])
        self.assertEqual(planned(src, snap("Loon", [poi("Hut", 9)])), {})


if __name__ == "__main__":
    unittest.main()
