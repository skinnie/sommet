#!/usr/bin/env python3
"""Testing mode's sample data: the same in both apps, and a Hunting move for a sample Traverse Alpha.

The desktop backend answers from desktop/backend/demo_data; the mobile app carries a generated
copy (android/src/config/demoData.json, tools/gen_activity_view.py). No watch, no network.

    python3 tools/test_demo_data.py
"""
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import gen_activity_view  # noqa: E402
import server  # noqa: E402

MOBILE = HERE.parent / "android" / "src" / "config" / "demoData.json"


class Sent(Exception):
    pass


def demo_reply(variant, method):
    """What a handler method sends in Testing mode as `variant`."""
    got = {}
    handler = server.Handler.__new__(server.Handler)
    handler.path = "/api/activities"
    handler._send_json = lambda code, body: got.update(code=code, body=body)
    before = dict(server.DEMO)
    server.DEMO.update(enabled=True, variant=variant)
    try:
        getattr(handler, method)()
    finally:
        server.DEMO.clear()
        server.DEMO.update(before)
    return got["body"]


class SampleData(unittest.TestCase):
    def test_the_mobile_copy_is_the_desktops_sample_data(self):
        self.assertEqual(MOBILE.read_bytes(), gen_activity_view.demo_data_for_mobile(),
                         "run tools/gen_activity_view.py")
        mobile = json.loads(MOBILE.read_text())
        self.assertEqual(len(mobile["activities"]), 3)
        self.assertEqual([p["name"] for p in mobile["pois"]], ["Summit Cairn", "Water Spring", "Trailhead"])

    def test_a_sample_traverse_alpha_has_a_hunting_move_with_shots(self):
        alpha = demo_reply("Loon", "_handle_activities")
        self.assertEqual(alpha["total_entries"], 4)
        hunt = alpha["activities"][-1]["gpx"]
        self.assertIn("<name>Hunting</name>", hunt)
        self.assertIn("<shots>3</shots>", hunt)
        self.assertEqual(hunt.count("<type>shot</type>"), 3)
        self.assertEqual(hunt, json.loads(MOBILE.read_text())["huntingTraverseAlpha"])
        self.assertEqual(alpha["device"], "demo")

    def test_other_sample_watches_keep_the_three_moves(self):
        for variant in ("Emu", "Jabiru"):
            self.assertEqual(len(demo_reply(variant, "_handle_activities")["activities"]), 3, variant)

    def test_a_sample_traverse_shows_its_pois_with_traverse_numbers(self):
        peak = demo_reply("Emu", "_handle_pois_read")["raw_output"]
        alpha = demo_reply("Loon", "_handle_pois_read")["raw_output"]
        types = lambda raw: [int(t) for t in __import__("re").findall(r"Type=(\d+)", raw)]
        self.assertEqual(types(peak), [12, 16, 5])       # Mountain, Water, Beginning (Ambit3 numbers)
        self.assertEqual(types(alpha), [16, 12, 23])     # the same three in a Traverse's numbers


if __name__ == "__main__":
    unittest.main()
