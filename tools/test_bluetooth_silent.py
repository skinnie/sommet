#!/usr/bin/env python3
"""Sommet asks whether the system's Bluetooth answers before it uses it (Linux).

On the X230, 2026-11-23, the Bluetooth service was up and answered nothing. Sommet asked for
the password, switched the adapter off and on, ran a 90 s Magene read every 90 s for an hour,
and said the watch could not be found. No Bluetooth, no root: the asking is faked.

    python3 tools/test_bluetooth_silent.py
"""
import json
import pathlib
import subprocess
import sys
import types
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import ble_bridge as B  # noqa: E402
import server  # noqa: E402


def answer(code, err=""):
    return types.SimpleNamespace(returncode=code, stdout="", stderr=err)


class Ping(unittest.TestCase):
    def setUp(self):
        self.saved = (B.subprocess.run, B.shutil.which, B.sys.platform, dict(B._BLUEZ_PING))
        self.calls = []
        B.sys = types.SimpleNamespace(platform="linux")
        B.shutil.which = lambda name, path=None: "/usr/bin/" + name
        B._BLUEZ_PING.update(at=None, silent=False, wine=False)

    def tearDown(self):
        B.subprocess.run, B.shutil.which, _platform, ping = self.saved
        B.sys = sys
        B._BLUEZ_PING.clear()
        B._BLUEZ_PING.update(ping)

    def fake(self, result):
        def run(argv, **_kw):
            self.calls.append(argv)
            if isinstance(result, Exception):
                raise result
            return result
        B.subprocess.run = run

    def test_an_answer_is_not_silent(self):
        self.fake(answer(0))
        self.assertFalse(B.bluetooth_silent())
        self.assertEqual(self.calls[0][0], "busctl")
        self.assertIn("--auto-start=no", self.calls[0])     # asking must not start Bluetooth

    def test_no_reply_is_silent(self):
        self.fake(answer(1, "Call failed: Connection timed out\n"))
        self.assertTrue(B.bluetooth_silent())

    def test_dbus_send_words_for_no_reply(self):
        B.shutil.which = lambda name, path=None: None if name == "busctl" else "/usr/bin/" + name
        self.fake(answer(1, "Error org.freedesktop.DBus.Error.NoReply: Did not receive a reply."))
        self.assertTrue(B.bluetooth_silent())
        self.assertEqual(self.calls[0][0], "dbus-send")

    def test_bluetooth_not_there_is_another_matter(self):
        self.fake(answer(1, 'Call failed: Name "org.bluez" does not exist\n'))
        self.assertFalse(B.bluetooth_silent())

    def test_nothing_to_ask_with_is_not_a_finding(self):
        B.shutil.which = lambda name, path=None: None
        self.fake(answer(0))
        self.assertFalse(B.bluetooth_silent())
        self.assertEqual(self.calls, [])

    def test_the_asking_failing_is_not_a_finding(self):
        self.fake(subprocess.TimeoutExpired("busctl", 7))
        self.assertFalse(B.bluetooth_silent())

    def test_the_answer_is_reused(self):
        self.fake(answer(1, "Call failed: Connection timed out"))
        pings = lambda: sum("Ping" in c for c in self.calls)  # noqa: E731
        self.assertTrue(B.bluetooth_silent())
        self.assertTrue(B.bluetooth_silent())
        self.assertEqual(pings(), 1)
        self.assertTrue(B.bluetooth_silent(max_age=0))       # a pairing asks fresh
        self.assertEqual(pings(), 2)

    def test_wine_on_the_bus_is_named(self):
        """The X230's cause: a leftover winedevice.exe that never read BlueZ's signals."""
        listing = (":1.16     1646 bluetoothd      root    :1.16   bluetooth.service - -\n"
                   ":1.6529 251223 winedevice.exe  andre   :1.6529 user@1000.service - -\n")

        def run(argv, **_kw):
            self.calls.append(argv)
            if "list" in argv:
                return types.SimpleNamespace(returncode=0, stdout=listing, stderr="")
            return answer(1, "Call failed: Connection timed out")
        B.subprocess.run = run
        self.assertTrue(B.bluetooth_silent())
        self.assertIn("Wine", B.silent_hint())
        self.assertIn("wineserver -k", B.silent_hint())
        self.assertLess(B.silent_hint().index("wineserver"), B.silent_hint().index("systemctl"))

    def test_without_wine_the_words_do_not_mention_it(self):
        def run(argv, **_kw):
            if "list" in argv:
                return types.SimpleNamespace(
                    returncode=0, stderr="",
                    stdout=":1.16 1646 bluetoothd root :1.16 bluetooth.service - -\n")
            return answer(1, "Call failed: Connection timed out")
        B.subprocess.run = run
        self.assertTrue(B.bluetooth_silent())
        self.assertNotIn("Wine", B.silent_hint())
        self.assertNotIn("{", B.silent_hint())

    def test_an_answering_bluetooth_never_looks_for_wine(self):
        self.fake(answer(0))
        self.assertFalse(B.bluetooth_silent())
        self.assertEqual(len(self.calls), 1)

    def test_other_systems_are_never_asked(self):
        B.sys = types.SimpleNamespace(platform="win32")
        self.fake(answer(1, "Call failed: Connection timed out"))
        self.assertFalse(B.bluetooth_silent())
        self.assertEqual(self.calls, [])


class Uses(unittest.TestCase):
    def setUp(self):
        self.saved = (B.bluetooth_silent, server.subprocess.run)
        self.ran = []
        server.subprocess.run = lambda *a, **k: self.ran.append(a) or answer(0)

    def tearDown(self):
        B.bluetooth_silent, server.subprocess.run = self.saved

    def test_a_magene_tool_is_not_started_on_a_silent_bluetooth(self):
        B.bluetooth_silent = lambda **_kw: True
        code, out, _err = server.run_tool("magene_device.py", ["hello"], timeout=90)
        self.assertEqual(code, 1)
        self.assertEqual(self.ran, [])
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["bluetooth_silent"])
        self.assertIn("not answering", payload["error"])

    def test_the_daemon_is_not_started_on_a_silent_bluetooth(self):
        B.bluetooth_silent = lambda **_kw: True
        bridge = B.BleBridge()
        bridge._request = lambda *_a, **_k: (_ for _ in ()).throw(B.BleBridgeError("none"))
        with self.assertRaises(B.BleBridgeError) as caught:
            bridge.start()
        self.assertIn("not answering", str(caught.exception))
        self.assertIsNone(bridge._proc)

    def test_a_pairing_stops_before_the_password_prompt(self):
        class Bridge:
            started = False

            def status(self):
                return {"running": False}

            def start(self, **_):
                Bridge.started = True
        saved = (B.daemon_command, server._open_bt_privacy_window, B.bridge)
        opened, asked = [], []
        B.bluetooth_silent = lambda **kw: asked.append(kw) or True
        B.daemon_command = lambda _script: ([], {})
        server._open_bt_privacy_window = lambda: opened.append(1)
        B.bridge = Bridge()
        try:
            sent = []
            h = server.Handler.__new__(server.Handler)
            h._send_json = lambda code, obj: sent.append((code, obj))
            h._handle_ble_connect({})
        finally:
            B.daemon_command, server._open_bt_privacy_window, B.bridge = saved
        self.assertEqual(asked, [{"max_age": 0}])            # asked fresh, not from memory
        self.assertEqual(opened, [], "the password prompt was shown on a silent Bluetooth")
        self.assertFalse(Bridge.started)
        self.assertEqual(sent[0][0], 502)
        self.assertIn("not answering", sent[0][1]["error"])


if __name__ == "__main__":
    unittest.main()
