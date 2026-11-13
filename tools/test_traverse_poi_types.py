#!/usr/bin/env python3
"""The Traverse / Traverse Alpha POI type list (ambit_format.TRAVERSE_WAYPOINT_TYPES).

The list was read from the watch program (tracker ticket traverse-alpha-vertical/02). When the
unpacked firmware is on this machine (assets/ is git-ignored) the table is compared with it;
otherwise only its own consistency is checked."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ambit_format as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RE_OUT = os.path.join(ROOT, "assets", "Firmware", "re-out")
PROGRAM = os.path.join(RE_OUT, "jabiru_fw", "part104_addr00fc00_blk0000-0940.bin")
TEXTS = os.path.join(RE_OUT, "jabiru_fw", "part107_addr070c00_blk0950-1360.bin")
SETS = {1: "general", 3: "fishing", 5: "hunting"}
# icon glyph of the watch's icon font -> the Ambit type that draws it
AMBIT_ICON = {"d6a0": 0, "d6b1": 1, "d6a2": 2, "d6a1": 3, "d6a7": 4, "d6aa": 5, "d6ab": 6,
              "d6a3": 7, "d6ad": 8, "d6ac": 9, "d6a4": 10, "d6b0": 11, "d6a6": 12, "d6a8": 13,
              "d6ae": 14, "d6af": 15, "d6a5": 16, "d6a9": 17}


class TraversePoiTypes(unittest.TestCase):
    def test_48_types_numbered_0_to_47(self):
        self.assertEqual(sorted(F.TRAVERSE_WAYPOINT_TYPES), list(range(48)))
        self.assertEqual(F.TRAVERSE_WAYPOINT_TYPES[F.TRAVERSE_WAYPOINT_TYPE_DEFAULT][0], "POI")

    def test_hunting_and_fishing_types(self):
        by_set = {}
        for t, (name, group, _) in F.TRAVERSE_WAYPOINT_TYPES.items():
            by_set.setdefault(group, []).append(name)
        self.assertEqual(sorted(by_set["hunting"]),
                         ["Bedding", "Big game", "Bird", "Prints", "Rub", "Scrape", "Shot",
                          "Small game", "Stand", "Trail cam"])
        self.assertEqual(sorted(by_set["fishing"]),
                         ["Fish", "Fishing spot", "River", "Rock", "Water", "Waterfall"])

    def test_every_ambit_type_has_a_traverse_type_with_the_same_icon(self):
        self.assertEqual(sorted(F.AMBIT_TO_TRAVERSE_WAYPOINT_TYPE), sorted(F.WAYPOINT_TYPES))
        for ambit, traverse in F.AMBIT_TO_TRAVERSE_WAYPOINT_TYPE.items():
            self.assertEqual(F.TRAVERSE_WAYPOINT_TYPES[traverse][2], ambit)

    def test_name_follows_the_watch(self):
        self.assertEqual(F.waypoint_type_name(2), "Camp")                  # Ambit list
        self.assertEqual(F.waypoint_type_name(2, 0x002B), "Car")           # Traverse
        self.assertEqual(F.waypoint_type_name(43, 0x002D), "Big game")     # Traverse Alpha
        self.assertEqual(F.waypoint_type_name(200, 0x002B), "POI")
        self.assertEqual(F.waypoint_type_name(200), "Waypoint")
        self.assertEqual(len(F.waypoint_types_for(0x002D)), 48)
        self.assertEqual(F.waypoint_types_for(0x001B), F.WAYPOINT_TYPES)

    def test_a_poi_copied_to_another_watch_family_keeps_its_icon(self):
        """Copy to watch: the same number means another icon on the other family, so the type
        is carried over by its icon. Watches are named as they name themselves."""
        peak, alpha, traverse, ambit2 = "Emu", "Loon", "Jabiru", "Duck"
        self.assertEqual(F.waypoint_type_between(2, peak, alpha), 4)       # Camp -> Camp
        self.assertEqual(F.waypoint_type_between(4, alpha, peak), 2)
        self.assertEqual(F.waypoint_type_between(5, traverse, peak), 2)    # Camping draws Camp
        self.assertEqual(F.waypoint_type_between(17, peak, traverse), 26)  # Waypoint -> POI
        self.assertEqual(F.waypoint_type_between(43, alpha, peak), 17)     # Big game: no such icon
        self.assertEqual(F.waypoint_type_between(39, alpha, peak), 8)      # Rub draws Forest
        self.assertEqual(F.waypoint_type_between(43, alpha, traverse), 43) # same family: unchanged
        self.assertEqual(F.waypoint_type_between(9, peak, "Ibisbill"), 9)
        self.assertEqual(F.waypoint_type_between(26, ambit2, peak), 17)    # Ambit1/2: the old numbers
        self.assertEqual(F.waypoint_type_between(31, ambit2, alpha), 31)
        self.assertEqual(F.waypoint_type_between(200, peak, alpha), 26)    # unknown -> plain
        self.assertEqual(F.waypoint_type_between(200, alpha, peak), 17)
        self.assertEqual(F.waypoint_type_between(None, peak, alpha), 26)
        for ambit in F.WAYPOINT_TYPES:                                     # there and back
            there = F.waypoint_type_between(ambit, peak, alpha)
            self.assertEqual(F.waypoint_type_between(there, alpha, peak), ambit)

    @unittest.skipUnless(os.path.exists(PROGRAM) and os.path.exists(TEXTS),
                         "unpacked Traverse firmware not on this machine")
    def test_equals_the_table_in_the_watch_program(self):
        sys.path.insert(0, RE_OUT)
        import traverse_poi_types as T
        rows = T.table(open(PROGRAM, "rb").read(), open(TEXTS, "rb").read())
        self.assertEqual(len(rows), 48)
        for r in rows:
            name, group, icon = F.TRAVERSE_WAYPOINT_TYPES[r["type"]]
            self.assertEqual((name, group, icon),
                             (r["name"], SETS[r["set"]], AMBIT_ICON.get(r["glyph"])), r)


if __name__ == "__main__":
    unittest.main()
