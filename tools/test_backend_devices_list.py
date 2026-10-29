#!/usr/bin/env python3
"""/api/devices says whether the backend can read activities without a USB watch (a Bluetooth
link or Testing mode), so the app can skip the read entirely when there is no watch at all.

Desktop audit 2026-10-29: with no watch, Activities spent seconds "reading off the watch" and
ended on an error. No watch, no backend process, no network: list_watches.py is stubbed."""
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import server  # noqa: E402


def devices_reply(watches, ble=False, demo=False):
    """Runs the real /api/devices handler with list_watches.py, Bluetooth and Testing mode
    stubbed; returns (status, body)."""
    sent = []
    saved = (server.run_tool, server.use_ble, dict(server.DEMO),
             server.SELECTED_PRODUCT_ID, server.SELECTED_SERIAL, server.USB_WATCH_PRESENT)
    server.run_tool = lambda script, args, **kw: (
        0, json.dumps({"ok": True, "watches": watches}) + "\n", "")
    server.use_ble = lambda: ble
    server.DEMO.update(enabled=demo, variant="Emu")
    try:
        handler = server.Handler.__new__(server.Handler)
        handler._send_json = lambda code, body: sent.append((code, body))
        handler._handle_devices_list()
    finally:
        (server.run_tool, server.use_ble, demo_saved, server.SELECTED_PRODUCT_ID,
         server.SELECTED_SERIAL, server.USB_WATCH_PRESENT) = saved
        server.DEMO.clear()
        server.DEMO.update(demo_saved)
    return sent[-1]


PEAK = {"productId": 27, "name": "Ambit3 Peak (Emu)", "codename": "Emu",
        "serial": "FA4E0000000000A1"}


class DevicesList(unittest.TestCase):
    def test_no_watch_at_all(self):
        code, body = devices_reply([])
        self.assertEqual(code, 200)
        self.assertEqual(body["watches"], [])
        self.assertIs(body["readsWithoutUsb"], False)

    def test_bluetooth_link_still_reads(self):
        code, body = devices_reply([], ble=True)
        self.assertIs(body["readsWithoutUsb"], True)

    def test_testing_mode_still_reads(self):
        code, body = devices_reply([], demo=True)
        self.assertIs(body["readsWithoutUsb"], True)

    def test_usb_watch_listed(self):
        code, body = devices_reply([PEAK])
        self.assertEqual([w["serial"] for w in body["watches"]], [PEAK["serial"]])
        self.assertIs(body["readsWithoutUsb"], False)


if __name__ == "__main__":
    unittest.main()
