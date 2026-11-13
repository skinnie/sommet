#!/usr/bin/env python3
"""The POI type lists of the two apps equal the one read from the watches.

One source: tools/ambit_format.py (WAYPOINT_TYPES, the Ambit3's 18; TRAVERSE_WAYPOINT_TYPES, the
48 of a Traverse / Traverse Alpha, checked against the firmware by test_traverse_poi_types.py).
The desktop keeps a copy in desktop/qml/DeviceCapabilities.qml and Android in
android/src/services/PoiTypes.ts. A number that drifts sends a POI to the watch with another
icon, so the three are compared here: same numbers, names and sets, and every icon is in the
bundled font.

    python3 tools/test_poi_types_parity.py
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ambit_format as F  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QML = os.path.join(ROOT, "desktop", "qml", "DeviceCapabilities.qml")
TS = os.path.join(ROOT, "android", "src", "services", "PoiTypes.ts")
FONTS = [os.path.join(ROOT, "desktop", "assets", "fonts", "MaterialSymbolsRounded.ttf"),
         os.path.join(ROOT, "android", "android", "app", "src", "main", "assets", "fonts",
                      "MaterialSymbolsRounded.ttf")]
ROW = re.compile(r"""\{\s*id:\s*(\d+),\s*name:\s*(?:qsTr\()?["']([^"']+)["']\)?,\s*"""
                 r"""set:\s*["'](\w+)["'],\s*glyph:\s*["']\\u([0-9a-fA-F]{4})["']\s*\}""")


def lists(path, first, second):
    """(ambit rows, traverse rows) of a source file: [(id, name, set, glyph codepoint)]."""
    src = open(path, encoding="utf-8").read()
    a, b = src.index(first), src.index(second)
    rows = lambda text: [(int(i), n, s, int(g, 16)) for i, n, s, g in ROW.findall(text)]
    start = src.index("[\n", b)                       # the list itself, past a "PoiType[]" type
    return rows(src[a:b]), rows(src[start:src.index("]", start)])


class PoiTypeLists(unittest.TestCase):
    def setUp(self):
        self.qml = lists(QML, "readonly property var ambitPoiTypes", "readonly property var traversePoiTypeList")
        self.ts = lists(TS, "export const AMBIT_POI_TYPES", "export const TRAVERSE_POI_TYPES")

    def test_the_ambit3_list_is_the_18_types_in_both_apps(self):
        want = [(t, F.WAYPOINT_TYPES[t], "general") for t in sorted(F.WAYPOINT_TYPES)]
        for name, (ambit, _) in (("desktop", self.qml), ("android", self.ts)):
            self.assertEqual([r[:3] for r in ambit], want, name)

    def test_the_traverse_list_is_the_48_types_in_both_apps(self):
        want = {t: (v[0], v[1]) for t, v in F.TRAVERSE_WAYPOINT_TYPES.items()}
        for name, (_, traverse) in (("desktop", self.qml), ("android", self.ts)):
            self.assertEqual({r[0]: (r[1], r[2]) for r in traverse}, want, name)
            self.assertEqual(len(traverse), 48, name)
            self.assertEqual(traverse[0][0], F.TRAVERSE_WAYPOINT_TYPE_DEFAULT, name)   # plain type first

    def test_both_apps_draw_the_same_icon_for_each_type(self):
        self.assertEqual(self.qml, self.ts)

    def test_a_traverse_type_draws_the_ambit_icon_of_its_kind_or_its_own(self):
        ambit, traverse = self.qml
        ambit_glyph = {r[0]: r[3] for r in ambit}
        own = set()
        for tid, _name, _set, glyph in traverse:
            group = F.TRAVERSE_WAYPOINT_TYPES[tid][2]
            if group is None:
                self.assertNotIn(glyph, ambit_glyph.values(), tid)
                own.add(glyph)
            else:
                self.assertEqual(glyph, ambit_glyph[group], tid)
        self.assertEqual(len(own), 10)                    # ten icons the Ambit3 does not have

    def test_the_mobile_apps_ambit_to_traverse_map_is_the_tools_map(self):
        src = open(TS, encoding="utf-8").read()
        body = src[src.index("AMBIT_TO_TRAVERSE_POI_TYPE"):]
        body = body[body.index("{"):body.index("}")]
        got = {int(a): int(b) for a, b in re.findall(r"(\d+):\s*(\d+)", body)}
        self.assertEqual(got, F.AMBIT_TO_TRAVERSE_WAYPOINT_TYPE)

    @unittest.skipUnless(all(os.path.exists(f) for f in FONTS), "the icon fonts are not built here")
    def test_every_icon_is_in_both_bundled_fonts(self):
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            self.skipTest("fontTools is not installed")
        glyphs = {r[3] for part in self.qml for r in part}
        for path in FONTS:
            have = set(TTFont(path).getBestCmap())
            self.assertEqual(glyphs - have, set(), path)


if __name__ == "__main__":
    unittest.main()
