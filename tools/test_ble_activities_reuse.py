#!/usr/bin/env python3
"""Over Bluetooth, one read of the watch's activities serves the page visits right after it.

Every page that lists activities asks for them when it opens; over Bluetooth each ask was a whole
sync (clock, sync lock, log header). Six ran in the first minute on the P5Q on 2026-11-22. The
backend now gives the last good answer again for a short while, unless the question changed, the
link is a new one or the watch itself asked to sync. No watch, no network.

    python3 tools/test_ble_activities_reuse.py
"""
import pathlib
import sys
import threading
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import server  # noqa: E402


class Bridge:
    def __init__(self):
        self.asked_at = None
        self.linked = True

    def status(self):
        return {"handshake_done": self.linked, "last_sync_notice_at": self.asked_at}


class Case(unittest.TestCase):
    def setUp(self):
        self.saved = (server.ble_bridge.bridge, server.Handler._read_activities_ble)
        self.bridge = Bridge()
        server.ble_bridge.bridge = self.bridge
        server._BLE_ACTIVITIES_LAST.clear()
        self.reads = []
        self.answer = (200, {"ok": True, "activities": [], "total": 12})
        case = self

        def read(handler, known_count, mark_synced=False):
            case.reads.append((known_count, mark_synced))
            time.sleep(0.05)                     # a read takes a moment; others arrive meanwhile
            handler._send_json(*case.answer)
        server.Handler._read_activities_ble = read

    def tearDown(self):
        server.ble_bridge.bridge, server.Handler._read_activities_ble = self.saved
        server._BLE_ACTIVITIES_LAST.clear()

    def ask(self, known_count=12, mark_synced=False):
        sent = []
        h = server.Handler.__new__(server.Handler)
        h._send_json = lambda code, obj: sent.append((code, obj))
        h._handle_activities_ble(known_count, mark_synced)
        self.assertEqual(len(sent), 1)
        return sent[0]

    def test_the_second_visit_does_not_touch_the_watch(self):
        first = self.ask()
        second = self.ask()
        self.assertEqual(len(self.reads), 1)
        self.assertEqual(first, (200, {"ok": True, "activities": [], "total": 12}))
        self.assertEqual(second, (200, {"ok": True, "activities": [], "total": 12, "reused": True}))

    def test_visits_that_arrive_together_make_one_read(self):
        threads = [threading.Thread(target=self.ask) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(self.reads), 1)

    def test_the_watch_asking_to_sync_gets_a_real_read(self):
        self.ask()
        self.bridge.asked_at = time.time() + 1          # "Sync now" pressed after that read
        self.ask()
        self.assertEqual(len(self.reads), 2)

    def test_another_question_gets_a_real_read(self):
        self.ask(known_count=12)
        self.ask(known_count=13)
        self.ask(known_count=13, mark_synced=True)
        self.assertEqual(len(self.reads), 3)

    def test_everything_asked_three_times_with_anything_new_in_between_is_one_read_of_everything(self):
        """The first sync on the P5Q's Linux side, 2026-11-22: full, quick, full, quick, full."""
        for known_count in (0, 12, 0, 12, 0):
            self.ask(known_count=known_count)
        self.assertEqual(self.reads, [(0, False), (12, False)])

    def test_an_old_answer_is_not_given_again(self):
        self.ask()
        for v in server._BLE_ACTIVITIES_LAST.values():
            v["at"] -= server.BLE_ACTIVITIES_REUSE_S + 1
        self.ask()
        self.assertEqual(len(self.reads), 2)

    def test_a_failed_read_is_never_remembered(self):
        self.answer = (502, {"ok": False, "error": "the watch did not answer"})
        self.assertEqual(self.ask()[0], 502)
        self.answer = (200, {"ok": True, "activities": [], "total": 12})
        self.assertNotIn("reused", self.ask()[1])
        self.assertEqual(len(self.reads), 2)

    def test_no_link_no_reuse(self):
        self.ask()
        self.bridge.linked = False
        self.ask()
        self.assertEqual(len(self.reads), 2)


if __name__ == "__main__":
    unittest.main()
