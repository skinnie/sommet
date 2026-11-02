#!/usr/bin/env python3
"""The Training Program's Sync to watch in the backend: an empty plan syncs too (2026-11-02), so
the computer's last workout can be erased. No watch: the tools it runs are stubbed."""
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import server  # noqa: E402


class EmptyPlan(unittest.TestCase):
    def setUp(self):
        self.calls = []      # (script, args, plan sent or None)
        self.sent = []       # (status, body) answered to the app

        def fake_run_tool(script, args, **kw):
            plan = None
            if script == "training_calendar.py":
                plan = json.loads(pathlib.Path(args[0]).read_text())
            self.calls.append((script, args, plan))
            if script == "custom_modes.py":
                return 1, "", "no watch"           # mode lookup is best effort
            if script == "training_calendar.py":
                return 0, json.dumps({"ok": True, "removed": ["Mon 02/11 PC test"],
                                      "added": [], "othersKept": ["Tue 03/11 T10s"]}) + "\n", ""
            return 0, json.dumps({"ok": True, "written": True, "count": 0, "dates": [],
                                  "cleared": True}) + "\n", ""

        for name, value in (("run_tool", fake_run_tool), ("use_ble", lambda: False)):
            self.addCleanup(setattr, server, name, getattr(server, name))
            setattr(server, name, value)
        self.handler = server.Handler.__new__(server.Handler)
        self.handler._send_json = lambda status, body: self.sent.append((status, body))

    def test_an_empty_plan_is_synced(self):
        self.handler._handle_trainingprogram_sync_calendar({"entries": [], "write": True})
        (script, args, plan), = self.calls
        self.assertEqual(script, "training_calendar.py")
        self.assertEqual(plan["entries"], [])
        self.assertEqual(args[1:], ["--sync", "--json", "--write"])
        (status, body), = self.sent
        self.assertEqual(status, 200)
        self.assertEqual(body["othersKept"], ["Tue 03/11 T10s"])

    def test_no_list_is_still_refused(self):
        for body in ({}, {"entries": None}, {"entries": "x"}):
            self.handler._handle_trainingprogram_sync_calendar(body)
        self.assertEqual(self.calls, [])
        self.assertEqual([s for s, _ in self.sent], [400, 400, 400])

    def test_an_empty_plan_clears_the_cards(self):
        # Same as a plan with only guided workouts, which get no card.
        self.handler._handle_trainingprogram_planned_moves({"entries": [], "write": True})
        self.assertEqual([(s, a) for s, a, _ in self.calls],
                         [("custom_modes.py", ["--json"]),
                          ("training_program.py", ["--clear", "--json", "--write"])])
        (status, body), = self.sent
        self.assertEqual((status, body["items"]), (200, []))

    def test_cards_without_a_list_are_still_refused(self):
        self.handler._handle_trainingprogram_planned_moves({"write": True})
        self.assertEqual((self.calls, [s for s, _ in self.sent]), ([], [400]))


if __name__ == "__main__":
    unittest.main()
