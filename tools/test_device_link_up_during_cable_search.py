#!/usr/bin/env python3
"""/api/device, when a Bluetooth link comes up WHILE the backend is still looking on the cable.

Looking on the cable is three tool runs and takes seconds on a slow PC. The answer used to be
"no watch" even though the watch had linked meanwhile, and the app took the watch away for a
second: POIs left the side bar and came back (P5Q, 2026-11-22). No watch, no network.

    python3 tools/test_device_link_up_during_cable_search.py
"""
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import server  # noqa: E402
import no_live_bluetooth  # noqa: E402
no_live_bluetooth.cut_off(server)


class Bridge:
    """ble_bridge.bridge, as far as /api/device uses it."""

    def __init__(self):
        self.linked = False
        self.commands = []

    def status(self):
        return {"handshake_done": self.linked,
                "device_info": {"model": "Emu", "serial": "TEST"} if self.linked else None}

    def set_dry_run(self, _on):
        pass

    def command(self, command, _payload=b"", **_):
        self.commands.append(command)
        return bytes([0, 77, 0, 0])            # 0x0306: battery 77 %


class Case(unittest.TestCase):
    def setUp(self):
        self.saved = (server.ble_bridge.bridge, server.run_tool, server.demo_ambit,
                      server.keepalive_sync, server.USB_WATCH_PRESENT)
        self.bridge = Bridge()
        server.ble_bridge.bridge = self.bridge
        server.demo_ambit = lambda: False
        server.keepalive_sync = lambda: None
        server.USB_WATCH_PRESENT = False
        self.tools = []
        self.sent = []
        self.handler = server.Handler.__new__(server.Handler)
        self.handler._send_json = lambda code, obj: self.sent.append((code, obj))

    def tearDown(self):
        (server.ble_bridge.bridge, server.run_tool, server.demo_ambit,
         server.keepalive_sync, server.USB_WATCH_PRESENT) = self.saved

    def cable_search(self, link_up_after):
        """run_tool finding nothing on the cable; the link comes up during run number N."""
        def run_tool(script, _args, **_):
            self.tools.append(script)
            if len(self.tools) == link_up_after:
                self.bridge.linked = True
            if script == "list_watches.py":
                return 0, '{"watches": []}', ""
            return 1, "", "no supported Suunto watch on the USB bus"
        server.run_tool = run_tool

    def test_the_link_coming_up_during_the_search_is_the_answer(self):
        self.cable_search(link_up_after=2)
        self.handler._handle_device()
        self.assertEqual(len(self.sent), 1)
        code, body = self.sent[0]
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["transport"], "ble")
        self.assertEqual(body["model"], "Emu")
        self.assertEqual(body["battery_percent"], 77)
        self.assertEqual(self.bridge.commands, [0x0306])

    def test_no_link_and_no_cable_is_still_no_watch(self):
        self.cable_search(link_up_after=99)
        self.handler._handle_device()
        self.assertEqual(len(self.sent), 1)
        code, body = self.sent[0]
        self.assertEqual(code, 502)
        self.assertFalse(body["ok"])
        self.assertEqual(self.bridge.commands, [])


if __name__ == "__main__":
    unittest.main()
