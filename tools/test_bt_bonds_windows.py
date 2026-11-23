#!/usr/bin/env python3
"""Windows: the Forget list, and a pairing left to Windows' own pop-up (André, 2026-11-23: "it
stops after putting the pin", "it doesn't remove them from windows"). No Bluetooth, no Windows:
WinRT is faked.

    python3 tools/test_bt_bonds_windows.py
"""
import asyncio
import pathlib
import sys
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ble_server_win as W  # noqa: E402
import bt_bonds as B  # noqa: E402

SUUNTO = "0C:8C:DC:00:00:01"
MOUSE = "AA:BB:CC:00:00:02"


class FakePairing:
    def __init__(self, status="UNPAIRED"):
        self.status, self.calls = status, 0

    async def unpair_async(self):
        self.calls += 1
        return types.SimpleNamespace(status=types.SimpleNamespace(name=self.status))


class Forget(unittest.TestCase):
    def setUp(self):
        self.saved = B._windows_paired
        self.watch = types.SimpleNamespace(name="Ambit3 0000", pairing=FakePairing())
        self.mouse = types.SimpleNamespace(name="Mouse", pairing=FakePairing())

        async def paired():
            return [(SUUNTO, self.watch)]        # what the real one keeps: Suunto only
        B._windows_paired = paired

    def tearDown(self):
        B._windows_paired = self.saved

    def test_an_address_as_windows_gives_it(self):
        self.assertEqual(B.address_text(0x0C8CDC000001), SUUNTO)

    def test_only_suunto_counts(self):
        self.assertTrue(B.is_suunto(SUUNTO, ""))
        self.assertTrue(B.is_suunto("11:22:33:44:55:66", "Ambit3 1234"))
        self.assertFalse(B.is_suunto(MOUSE, "Mouse"))

    def test_the_list(self):
        self.assertEqual(B.windows_list(),
                         [{"address": SUUNTO, "name": "Ambit3 0000", "kind": "suunto"}])

    def test_forget_removes_the_watch(self):
        self.assertTrue(B.windows_forget(SUUNTO.lower())["ok"])
        self.assertEqual(self.watch.pairing.calls, 1)

    def test_anything_else_is_refused_and_untouched(self):
        with self.assertRaises(ValueError):
            B.windows_forget(MOUSE)
        self.assertEqual(self.mouse.pairing.calls, 0)
        self.assertEqual(self.watch.pairing.calls, 0)

    def test_windows_saying_no_is_an_error(self):
        self.watch.pairing.status = "ACCESS_DENIED"
        with self.assertRaises(ValueError) as caught:
            B.windows_forget(SUUNTO)
        self.assertIn("ACCESS_DENIED", str(caught.exception))


class PairingLeftToWindows(unittest.TestCase):
    def run_it(self, states, wait=0.02, grace=0.0):
        """`states`: (is paired, connection) per poll; the last one repeats."""
        host = W.WinHost.__new__(W.WinHost)
        link = {"up": types.SimpleNamespace(name="CONNECTED"),
                "down": types.SimpleNamespace(name="DISCONNECTED")}
        host.ConnStatus = types.SimpleNamespace(CONNECTED=link["up"], DISCONNECTED=link["down"])
        host.state = types.SimpleNamespace(pairing_hint=None)
        left = list(states)
        dev = types.SimpleNamespace(device_id="id", connection_status=link["up"])

        async def create_from_id_async(_id):
            paired, state = left.pop(0) if len(left) > 1 else left[0]
            dev.connection_status = link[state]
            return types.SimpleNamespace(pairing=types.SimpleNamespace(is_paired=paired))
        host.enum = types.SimpleNamespace(DeviceInformation=types.SimpleNamespace(
            create_from_id_async=create_from_id_async))
        saved = W.PAIR_WAIT_S, W.PAIR_LINK_GRACE_S, W.asyncio.sleep
        real_sleep = asyncio.sleep
        W.PAIR_WAIT_S, W.PAIR_LINK_GRACE_S = wait, grace
        W.asyncio.sleep = lambda _s: real_sleep(0.001)
        try:
            return asyncio.run(host.wait_for_windows_pairing(dev)), host.state.pairing_hint
        finally:
            W.PAIR_WAIT_S, W.PAIR_LINK_GRACE_S, W.asyncio.sleep = saved

    def test_sommet_starts_no_pairing_of_its_own(self):
        self.assertFalse(hasattr(W.WinHost, "pair"))

    def test_done_when_windows_is_done(self):
        self.assertEqual(self.run_it([(False, "up"), (False, "up"), (True, "up")]), (True, None))

    def test_a_dropped_link_says_what_to_do(self):
        done, hint = self.run_it([(False, "up"), (False, "down")], wait=1.0)
        self.assertFalse(done)
        self.assertIn("Windows pop-up", hint)

    def test_a_short_flicker_is_not_the_end(self):
        done, _hint = self.run_it([(False, "down"), (False, "up"), (True, "up")], wait=1.0, grace=0.5)
        self.assertTrue(done)

    def test_nobody_typing_ends_with_the_hint(self):
        done, hint = self.run_it([(False, "up")])
        self.assertFalse(done)
        self.assertIn("notifications", hint)


if __name__ == "__main__":
    unittest.main()
