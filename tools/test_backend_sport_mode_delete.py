#!/usr/bin/env python3
"""Desktop backend: deleting an Ambit3-family sport mode or multisport (watch-protocol/10, batch D).

Android had Delete on these watches and the backend had the endpoint (/api/customodes/mode and
/multisport -> tools/sport_mode_manage.py --delete / --delete-multisport, replayed byte for byte
against SuuntoLink's capture removeandaddsportsmodeandmultisport by its --selftest); the desktop
page offered no Delete. Checked here, no watch:
  - the read marks what SuuntoLink lets you delete (ui/sport_mode_editor.js addSportMode): not the
    last menu entry, not a mode without an entry of its own, not a mode a multisport uses;
  - a confirmed delete runs the tool with --write and a backup folder (the dialog says a copy is
    saved first), and the tool's answer reaches the page: it ended with an indented JSON block
    whose last parseable line was a bare leg number, so every desktop create and delete read as
    "not confirmed" even when written. Now one JSON line, {ok: false, error} on a refusal;
  - with the capture present (git-ignored), the real tool on a real region image.
Not seen on a real watch with the desktop's new Delete link.

    python3 tools/test_backend_sport_mode_delete.py
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import server  # noqa: E402

CAPTURE = HERE.parent / "assets" / "pcap" / "removeandaddsportsmodeandmultisport"


def read(modes, menu):
    """A custom_modes.py --json read: `modes` [(name, inWatchMenu, usedByMultisport)], `menu`
    [(name, legs)]."""
    return {"ok": True,
            "exerciseModes": [{"name": n, "inWatchMenu": w, "usedByMultisport": u}
                              for n, w, u in modes],
            "sportModes": [{"name": n, "legs": legs, "isMultisport": len(legs) > 1}
                           for n, legs in menu]}


class Ambit3Delete(unittest.TestCase):
    def setUp(self):
        self.calls, self.sent = [], []
        self.saved = (server.run_tool, server.selected_is_legacy, server.demo_ambit)
        server.selected_is_legacy = lambda: False
        server.demo_ambit = lambda: False
        self.h = server.Handler.__new__(server.Handler)
        self.h._send_json = lambda code, obj: self.sent.append((code, obj))

    def tearDown(self):
        server.run_tool, server.selected_is_legacy, server.demo_ambit = self.saved

    def test_flags_follow_suuntolink(self):
        info = server.ambit3_delete_flags(read(
            [("Openwater swim", True, True), ("Transition", False, True), ("Trekking", True, False),
             ("Old", False, False)],
            [("Openwater swim", [0]), ("Trekking", [2]), ("Triathlon", [0, 1, 0])]))
        self.assertEqual([(m["name"], m["deletable"]) for m in info["exerciseModes"]],
                         [("Openwater swim", False), ("Transition", False), ("Trekking", True),
                          ("Old", False)])
        self.assertIn("multisport", info["exerciseModes"][0]["deleteReason"])
        self.assertIn("no entry", info["exerciseModes"][3]["deleteReason"])
        self.assertTrue(info["sportModes"][2]["deletable"])
        # The last entry stays, mode or multisport.
        last = server.ambit3_delete_flags(read([("Running", True, False)], [("Running", [0])]))
        self.assertFalse(last["exerciseModes"][0]["deletable"])

    def test_read_carries_the_flags(self):
        out = json.dumps(read([("Running", True, False), ("Cycling", True, False)],
                              [("Running", [0]), ("Cycling", [1])]))
        server.run_tool = lambda script, args, **kw: (0, out + "\n", "")
        self.h._handle_customodes_read()
        code, obj = self.sent[0]
        self.assertEqual((code, [m["deletable"] for m in obj["exerciseModes"]]), (200, [True, True]))

    def test_delete_writes_with_a_backup_and_reports(self):
        answer = {"ok": True, "wrote": True, "writes": 1, "sport_modes": []}

        def fake(script, args, **kw):
            self.calls.append((script, list(args)))
            # The tool's human lines first, then its one JSON line.
            return 0, "  wrote 1/1 (3200 bytes)\nDone.\n" + json.dumps(answer) + "\n", ""
        server.run_tool = fake
        self.h._handle_customodes_mode({"action": "delete", "name": "Trekking", "confirm": True})
        self.h._handle_customodes_multisport({"action": "delete", "name": "Triathlon",
                                              "confirm": True})
        (s1, a1), (s2, a2) = self.calls
        self.assertEqual((s1, a1[:2], s2, a2[:2]), ("sport_mode_manage.py", ["--delete", "Trekking"],
                                                    "sport_mode_manage.py",
                                                    ["--delete-multisport", "Triathlon"]))
        for args in (a1, a2):
            self.assertIn("--write", args)
            self.assertIn("--json", args)
            self.assertTrue(args[args.index("--backup-dir") + 1].endswith("sport_modes"))
        self.assertEqual(self.sent, [(200, answer), (200, answer)])

    def test_create_passes_the_watch_model(self):
        server.run_tool = lambda script, args, **kw: (self.calls.append(list(args)) or 0,
                                                      '{"ok": true}\n', "")
        self.h._handle_customodes_mode({"action": "create", "name": "Trail", "activityId": 82,
                                        "variant": "Jabiru", "confirm": True})
        args, = self.calls
        self.assertEqual(args[args.index("--variant") + 1], "Jabiru")

    def test_refusal_reaches_the_page(self):
        server.run_tool = lambda script, args, **kw: (
            4, json.dumps({"ok": False, "error": "'Cycling' is a leg of Triathlon"}) + "\n",
            "REFUSED: 'Cycling' is a leg of Triathlon\n")
        self.h._handle_customodes_mode({"action": "delete", "name": "Cycling", "confirm": True})
        code, obj = self.sent[0]
        self.assertEqual((code, obj["ok"]), (200, False))
        self.assertIn("leg of Triathlon", obj["error"])


@unittest.skipUnless(CAPTURE.exists(), "SuuntoLink capture absent (git-ignored)")
class RealTool(unittest.TestCase):
    """sport_mode_manage.py itself, --from a region image of SuuntoLink's capture."""

    def run_tool(self, *args):
        from custom_modes_roundtrip import region_images
        with tempfile.TemporaryDirectory() as tmp:
            image = pathlib.Path(tmp) / "region.bin"
            image.write_bytes(region_images(str(CAPTURE))[10])
            proc = subprocess.run([sys.executable, str(HERE / "sport_mode_manage.py"), "--from",
                                   str(image), "--json", *args], capture_output=True, text=True,
                                  timeout=120)
        return proc.returncode, server.Handler._parse_last_json_line(None, proc.stdout)

    def test_dry_run_delete(self):
        code, obj = self.run_tool("--delete", "Pool swimming")
        self.assertEqual((code, obj["ok"], obj["wrote"], obj["writes"]), (0, True, False, 1))
        self.assertNotIn("Pool swimming", [m["Name"] for m in obj["sport_modes"]])

    def test_refused_delete(self):
        code, obj = self.run_tool("--delete", "Trekking")      # a leg of the Adventure Race
        self.assertEqual((code, obj["ok"]), (4, False))
        self.assertIn("Adventure Race", obj["error"])


if __name__ == "__main__":
    unittest.main()
