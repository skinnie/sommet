#!/usr/bin/env python3
"""Swim styles in the backend: what Copy plans between two watches, and the one write it makes.
No watch, no network: swim_styles.py and the device read are stubbed."""
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import server  # noqa: E402

TAUGHT = [{"id": 0, "style": "Other", "vectors": [0, 0, 0, 0]},
          {"id": 1, "style": "Butterfly", "vectors": [2445, 2870, 442, 0]},
          {"id": 3, "style": "Breaststroke", "vectors": [2193, 2166, 207, 0]}]
NEW_WATCH = [{"id": 0, "style": "Other", "vectors": [0, 0, 0, 0]},
             {"id": 1, "style": "Butterfly", "vectors": [0, 0, 0, 0]},
             {"id": 3, "style": "Breaststroke", "vectors": [5, 5, 5, 0]}]


def snap(styles, model="Emu", serial="S1"):
    return {"model": model, "serial": serial, "displayName": model,
            "categories": {"swimStyles": {"supported": True, "count": 0,
                                          "items": {"styles": styles}}}}


class Diff(unittest.TestCase):
    def test_changes_per_taught_stroke(self):
        changes, skipped = server._sync_diff_swimstyles(snap(TAUGHT), snap(NEW_WATCH))
        self.assertEqual([c["key"] for c in changes], ["swim:1", "swim:3"])
        self.assertEqual(changes[0]["to"], [2445, 2870, 442, 0])
        self.assertEqual(changes[0]["fromText"], "not taught")
        self.assertEqual(skipped, [])

    def test_untaught_never_erases_the_target(self):
        changes, _ = server._sync_diff_swimstyles(snap(NEW_WATCH), snap(TAUGHT))
        self.assertEqual([c["key"] for c in changes], ["swim:3"])

    def test_same_styles_no_change(self):
        self.assertEqual(server._sync_diff_swimstyles(snap(TAUGHT), snap(TAUGHT)), ([], []))

    def test_stroke_missing_on_target_is_skipped(self):
        changes, skipped = server._sync_diff_swimstyles(snap(TAUGHT), snap(TAUGHT[:2]))
        self.assertEqual(changes, [])
        self.assertEqual(skipped[0]["key"], "swim:3")

    def test_unsupported_side(self):
        no = {"categories": {"swimStyles": {"supported": False}}}
        self.assertEqual(server._sync_diff_swimstyles(snap(TAUGHT), no), ([], []))

    def test_cross_model_allowed(self):
        changes, _ = server._sync_diff_swimstyles(snap(TAUGHT, "Emu"), snap(NEW_WATCH, "Finch"))
        self.assertEqual(len(changes), 2)


class Write(unittest.TestCase):
    def test_one_write_pinned_to_the_serial(self):
        calls = []

        def fake_run_tool(script, args, **kw):
            path = args[args.index("--restore") + 1]
            calls.append((script, args, json.loads(pathlib.Path(path).read_text())))
            return 0, json.dumps({"ok": True, "changes": [{"id": 1}], "confirmed": True}) + "\n", ""

        saved = server.run_tool
        server.run_tool = fake_run_tool
        try:
            handler = server.Handler.__new__(server.Handler)
            r = handler._sync_write_swimstyles(TAUGHT[1:2], "8A15")
        finally:
            server.run_tool = saved
        self.assertTrue(r["ok"])
        (script, args, sent), = calls
        self.assertEqual(script, "swim_styles.py")
        self.assertIn("--write", args)
        self.assertEqual(args[args.index("--expect-serial") + 1], "8A15")
        self.assertEqual(sent["styles"], TAUGHT[1:2])


if __name__ == "__main__":
    unittest.main()
