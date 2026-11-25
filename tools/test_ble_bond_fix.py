#!/usr/bin/env python3
"""ble_bond_fix.py without a watch: the pairing record's layout, the checks, the planned write
and the re-read check. The parts that need a real settings read use the SuuntoLink captures and
the watch's descriptor in assets/ (not in the public repository) and are skipped without them.
All addresses and keys here are made up."""

import functools
import json
import os
import pathlib
import platform
import struct
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ble_bond_fix as B  # noqa: E402
import settings_write as S  # noqa: E402

ADDR = "AA:BB:CC:DD:EE:01"
ZERO = lambda n: ":".join(["00"] * n)                                  # noqa: E731
LTK = ":".join(f"{b:02x}" for b in range(0xC1, 0xD1))
RND = ":".join(f"{b:02x}" for b in range(0xE1, 0xE9))
PHONE_ID = 0xFF42F000
CAPTURE = HERE.parent / "assets" / "pcap" / "ambit3full"


def record_bytes(mac=ZERO(6), irk=ZERO(16), key=LTK, rnd=RND, device_id=PHONE_ID, cls=0):
    out = struct.pack("<IB", device_id, cls)
    for text in (mac, irk, key, rnd):
        out += text.encode() + b"\0"
    return out + struct.pack("<HBB", 0x7E57, 1, 1)


def fixed_rand(first):
    return lambda n: bytes([first]) + bytes(range(1, n))


class RecordLayout(unittest.TestCase):
    def test_a_full_record_is_147_bytes_and_round_trips(self):
        data = record_bytes()
        self.assertEqual(len(data), 147)
        rec = B.Record(data)
        self.assertEqual((rec.device_id, rec.addr_class, rec.mac, rec.key), (PHONE_ID, 0, ZERO(6), LTK))
        self.assertEqual(rec.to_bytes(), data)

    def test_an_empty_slot_is_13_bytes(self):
        self.assertEqual(B.Record(struct.pack("<IB", 0, 0) + b"\0" * 4 + bytes(4)).to_bytes(),
                         struct.pack("<IB", 0, 0) + b"\0" * 4 + bytes(4))

    def test_a_damaged_record_is_refused(self):
        with self.assertRaises(ValueError):
            B.Record(record_bytes()[:60])
        with self.assertRaises(ValueError):
            B.Record(record_bytes() + b"x")


class Checks(unittest.TestCase):
    def test_what_needs_the_fix(self):
        self.assertTrue(B.needs_fix(B.Record(record_bytes())))
        # A phone (or a privacy-on pairing) gave its identity: leave it alone.
        self.assertFalse(B.needs_fix(B.Record(record_bytes(mac="11:22:33:44:55:66",
                                                           irk=LTK))))
        # No key: nothing to keep.
        self.assertFalse(B.needs_fix(B.Record(record_bytes(key=""))))
        # The chip's own rule is the first eight bytes of the IRK.
        self.assertTrue(B.irk_is_blank(ZERO(8) + ":01:02:03:04:05:06:07:08"))
        self.assertTrue(B.irk_is_blank(""))
        self.assertFalse(B.irk_is_blank("01:" + ZERO(15)))

    def test_address_goes_in_least_significant_byte_first(self):
        self.assertEqual(B.watch_order(ADDR), "01:ee:dd:cc:bb:aa")
        for bad in ("", "AA:BB:CC:DD:EE", "AA-BB-CC-DD-EE-01", "zz:BB:CC:DD:EE:01"):
            with self.assertRaises(ValueError):
                B.watch_order(bad)

    def test_the_new_irk_never_starts_with_eight_zero_bytes(self):
        draws = iter([bytes(16), bytes(8) + b"\x01" * 8, b"\x00" * 7 + b"\x05" + bytes(8)])
        irk = B.new_irk(lambda n: next(draws))
        self.assertFalse(B.irk_is_blank(irk))
        self.assertEqual(irk.split(":")[7], "05")

    def test_the_fixed_record_keeps_the_watchs_keys(self):
        rec = B.Record(record_bytes(cls=1))
        new = B.fixed_record(rec, ADDR, fixed_rand(0x42))
        self.assertEqual((new.mac, new.addr_class), ("01:ee:dd:cc:bb:aa", 0))
        self.assertFalse(B.irk_is_blank(new.irk))
        self.assertEqual((new.key, new.rnd, new.tail, new.device_id),
                         (rec.key, rec.rnd, rec.tail, rec.device_id))
        self.assertEqual(len(new.to_bytes()), 147)
        self.assertEqual(rec.mac, ZERO(6))          # the original is not changed in place


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


DEV = "dev_AA_BB_CC_DD_EE_02"           # made up, like every address here


def linked(dev=DEV, serial="TESTSERIAL01", done=True):
    return {"handshake_done": done, "linked_device": dev, "device_info": {"serial": serial}}


class AfterPairingRules(unittest.TestCase):
    """When the backend completes a Linux pairing on the watch by itself (watch-protocol/09
    step 2, André 2026-11-25 "b")."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "fix.json"
        self.calls, self.answers, self.logs = [], [], []
        self.clock = Clock()

    def tearDown(self):
        self.tmp.cleanup()

    def make(self):
        def run(address, serial, over_ble):
            self.calls.append((address, serial, over_ble))
            return self.answers.pop(0) if self.answers else {"ok": True, "state": "fix"}
        return B.AfterPairing(self.path, run, log=self.logs.append, clock=self.clock)

    def test_nothing_runs_without_a_pairing_made_here(self):
        fix = self.make()
        self.clock.t += 100
        self.assertFalse(fix.due_over_ble(linked(), False))
        self.assertIsNone(fix.due_over_cable([{"serial": "TESTSERIAL01"}]))
        self.assertIsNone(fix.try_now(True))
        self.assertEqual(self.calls, [])
        self.assertFalse(fix.failed())

    def test_after_a_pairing_it_runs_once_in_that_watchs_first_session(self):
        fix = self.make()
        fix.paired("/org/bluez/hci0/" + DEV, ADDR, "public")
        self.assertFalse(fix.due_over_ble(linked(done=False), False))     # still pairing
        self.assertFalse(fix.due_over_ble(linked(), False))               # session starts now
        self.clock.t += B.AfterPairing.SETTLE_S - 1
        self.assertFalse(fix.due_over_ble(linked(), False))               # settling
        self.clock.t += 1
        self.assertTrue(fix.due_over_ble(linked(), False))
        self.assertEqual(fix.try_now(True), {"ok": True, "state": "fix"})
        self.assertEqual(self.calls, [(ADDR, None, True)])
        self.assertIsNone(fix.pending)
        self.clock.t += 1000
        self.assertFalse(fix.due_over_ble(linked(), False))               # done: never again
        self.assertIsNone(self.make().pending)                            # also after a restart
        self.assertFalse(any(ADDR.lower() in m.lower() or "TESTSERIAL" in m for m in self.logs))

    def test_not_for_another_watch_nor_with_a_watch_on_the_cable(self):
        fix = self.make()
        fix.paired(DEV, ADDR, "public")
        fix.due_over_ble(linked(dev="dev_11_22_33_44_55_66"), False)
        self.clock.t += 60
        self.assertFalse(fix.due_over_ble(linked(dev="dev_11_22_33_44_55_66"), False))
        fix.due_over_ble(linked(), True)
        self.clock.t += 60
        self.assertFalse(fix.due_over_ble(linked(), True))     # a watch on the cable: never BLE
        self.assertTrue(fix.due_over_ble(linked(), False))

    def test_a_random_adapter_address_is_not_written(self):
        fix = self.make()
        fix.paired(DEV, ADDR, "random")
        self.assertIsNone(fix.pending)
        fix.paired(DEV, "not an address", None)
        self.assertIsNone(fix.pending)
        self.assertTrue(self.logs)

    def test_fine_and_no_phone_end_it_without_a_write(self):
        for state in ("fine", "no_phone"):
            fix = self.make()
            fix.paired(DEV, ADDR, "public")
            self.answers.append({"ok": True, "state": state, "written": False})
            self.assertIsNotNone(fix.try_now(True))
            self.assertIsNone(fix.pending, state)
            self.assertFalse(fix.failed())

    def test_a_failure_shows_and_is_tried_again_then_over_the_cable(self):
        fix = self.make()
        fix.paired(DEV, ADDR, "public")
        fix.due_over_ble(linked(), False)                         # learns the serial
        self.clock.t += B.AfterPairing.SETTLE_S
        self.answers += [{"ok": False, "error": "the watch did not answer"}] * 3
        self.assertTrue(fix.due_over_ble(linked(), False))
        fix.try_now(True)
        self.assertTrue(fix.failed())                             # Home's line
        self.assertEqual(B.AfterPairing(self.path, None).pending["error"],
                         "the watch did not answer")              # kept across a restart
        self.clock.t += 10
        self.assertFalse(fix.due_over_ble(linked(), False))       # not at once
        self.clock.t += B.AfterPairing.RETRY_S
        self.assertTrue(fix.due_over_ble(linked(), False))
        fix.try_now(True)
        self.clock.t += 1000
        self.assertFalse(fix.due_over_ble(linked(), False))       # two tries per session
        fix.due_over_ble(linked(done=False), False)               # the link drops...
        fix.due_over_ble(linked(), False)                         # ...a new session
        self.clock.t += B.AfterPairing.SETTLE_S
        self.assertTrue(fix.due_over_ble(linked(), False))
        # The cable: only the watch with that serial, and not again at once.
        self.assertIsNone(fix.due_over_cable([{"serial": "OTHER"}]))
        self.assertEqual(fix.due_over_cable([{"serial": "TESTSERIAL01"}]), "TESTSERIAL01")
        fix.try_now(False, "TESTSERIAL01")
        self.assertIsNone(fix.due_over_cable([{"serial": "TESTSERIAL01"}]))
        self.clock.t += B.AfterPairing.CABLE_RETRY_S
        self.assertEqual(fix.due_over_cable([{"serial": "TESTSERIAL01"}]), "TESTSERIAL01")
        self.answers.append({"ok": True, "state": "fix", "written": True})
        fix.try_now(False, "TESTSERIAL01")
        self.assertEqual(self.calls[-1], (ADDR, "TESTSERIAL01", False))
        self.assertFalse(fix.failed())
        self.assertIsNone(fix.pending)

    def test_forget_ends_it_and_a_crash_counts_as_a_failure(self):
        fix = self.make()
        fix.paired(DEV, ADDR, "public")
        fix.forgotten()
        self.assertIsNone(fix.pending)
        self.assertIsNone(B.AfterPairing(self.path, None).pending)

        def boom(*_a):
            raise TimeoutError(f"Command ['x', '--address', '{ADDR}'] timed out")
        fix = B.AfterPairing(self.path, boom, log=self.logs.append, clock=self.clock)
        fix.paired(DEV, ADDR, "public")
        fix.try_now(True)
        self.assertTrue(fix.failed())
        self.assertNotIn(ADDR, fix.pending["error"])            # never the address in a message
        self.assertFalse(any(ADDR in m for m in self.logs))

    def test_unsupported_and_no_key_end_it_with_a_notice_until_the_next_pairing(self):
        for state in ("unsupported", "no_key"):
            self.path.unlink(missing_ok=True)
            fix = self.make()
            fix.paired(DEV, ADDR, "public")
            self.assertEqual(fix.notice(), "")
            self.answers.append({"ok": True, "state": state, "written": False})
            fix.try_now(True)
            self.assertIsNone(fix.pending, state)
            self.assertFalse(fix.failed())
            self.assertEqual(fix.notice(), state)                         # Home's line
            again = self.make()                                           # after a restart
            self.assertEqual((again.pending, again.notice()), (None, state))
            self.clock.t += 1000
            self.assertFalse(again.due_over_ble(linked(), False))         # nothing more to try
            again.paired(DEV, ADDR, "public")                             # a new pairing
            self.assertEqual(again.notice(), "")
            again.forgotten()
            again.final = state
            again.forgotten()                                             # Forget clears it
            self.assertEqual(self.make().notice(), "")

    def test_a_failure_is_the_failed_notice(self):
        fix = self.make()
        fix.paired(DEV, ADDR, "public")
        self.answers.append({"ok": False, "error": "the watch did not answer"})
        fix.try_now(True)
        self.assertEqual(fix.notice(), "failed")

    def test_with_privacy_kept_on_there_is_nothing_to_complete(self):
        fix = self.make()
        fix.paired(DEV, ADDR, "public", privacy_on=True)
        self.assertIsNone(fix.pending)
        self.assertEqual(fix.notice(), "")
        self.clock.t += 100
        self.assertFalse(fix.due_over_ble(linked(), False))

    def test_a_damaged_state_file_means_nothing_pending(self):
        self.path.write_text("{not json")
        self.assertIsNone(self.make().pending)
        self.path.write_text('{"pending": {"device": "dev_x", "address": "bad"}}')
        self.assertIsNone(self.make().pending)


@unittest.skipUnless(platform.system() == "Linux", "Linux only")
class BackendWiring(unittest.TestCase):
    """server.py: the code box marks the pairing, the status poll starts the try, Forget ends
    it, and the status says when it failed."""

    def setUp(self):
        import tempfile
        sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
        import server
        self.server = server
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (server.PAIRING_FIX_FILE, server._PAIRING_FIX[0], server.ble_bridge.bridge,
                      server._pairing_fix_run, server.USB_WATCH_PRESENT)
        server.PAIRING_FIX_FILE = pathlib.Path(self.tmp.name) / "fix.json"
        server._PAIRING_FIX[0] = None
        server.USB_WATCH_PRESENT = False

    def tearDown(self):
        s = self.server
        (s.PAIRING_FIX_FILE, s._PAIRING_FIX[0], s.ble_bridge.bridge, s._pairing_fix_run,
         s.USB_WATCH_PRESENT) = self.saved
        self.tmp.cleanup()

    def test_code_box_then_session_then_status(self):
        s = self.server
        state = {"pending_passkey_device": "/org/bluez/hci0/" + DEV, "adapter_address": ADDR,
                 "adapter_address_type": "public", "running": True}

        log = pathlib.Path(self.tmp.name) / "ble_daemon.log"

        class Bridge:
            _log_path = log

            def status(self):
                return dict(state)

            def submit_passkey(self, _code):
                return True
        s.ble_bridge.bridge = Bridge()
        import bt_privacy
        saved_always = bt_privacy.always_on
        self.addCleanup(setattr, bt_privacy, "always_on", saved_always)
        bt_privacy.always_on = lambda conf=None: False      # not this computer's main.conf
        h = s.Handler.__new__(s.Handler)
        sent = []
        h._send_json = lambda code, obj: sent.append((code, obj))
        h._handle_ble_passkey({"passkey": 123456})
        self.assertEqual(sent[-1][0], 200)
        fix = s._pairing_fix()
        self.assertEqual(fix.pending["device"], DEV)

        ran = []
        fix.run = lambda address, serial, over_ble: ran.append(over_ble) or \
            {"ok": False, "error": "the watch did not answer"}
        state.update(pending_passkey_device=None, handshake_done=True, linked_device=DEV,
                     device_info={"serial": "TESTSERIAL01"})
        fix.clock = Clock()
        h._handle_ble_status()                                   # the session is seen
        fix.clock.t += B.AfterPairing.SETTLE_S
        h._handle_ble_status()                                   # starts the try on a thread
        for _ in range(200):
            if not fix.busy and ran:
                break
            import time
            time.sleep(0.01)
        self.assertEqual(ran, [True])
        h._handle_ble_status()
        self.assertEqual(sent[-1][1]["pairing_fix"],
                         {"pending": True, "failed": True, "notice": "failed"})
        self.assertNotIn("bt_privacy", sent[-1][1])
        state["running"] = False                                 # Forget ends it
        h._handle_ble_forget()
        h._handle_ble_status()
        self.assertEqual(sent[-1][1]["pairing_fix"],
                         {"pending": False, "failed": False, "notice": ""})
        # With the system's privacy kept on, a pairing has nothing to complete.
        bt_privacy.always_on = lambda conf=None: True
        state.update(pending_passkey_device="/org/bluez/hci0/" + DEV, running=True)
        h._handle_ble_passkey({"passkey": 123456})
        self.assertIsNone(s._pairing_fix().pending)
        text = log.read_text()
        self.assertIn("pairing fix:", text)
        self.assertNotIn(ADDR, text.upper())
        self.assertNotIn("TESTSERIAL", text)


def _kailash_descriptor():
    """The Kailash's own descriptor (firmware 2.0.5) from SuuntoLink's data in assets/, found by
    its firmware so its file name (the watch's serial) is not written here."""
    import glob                                          # follows a linked assets/ too
    found = sorted(glob.glob(str(HERE.parent / "assets" / "**" / "descr+*+2.0.5"),
                             recursive=True))
    return pathlib.Path(found[0]) if found else None


KAILASH_CAPTURE = HERE.parent / "assets" / "APK" / "kailash" / "kailash7rsettingschange.pklg"


@functools.lru_cache(maxsize=1)
def _kailash_read():
    """The Kailash's settings as Suunto's 7R app read them (0x1100 reply in
    kailash7rsettingschange.pklg): 41 entries, one pairing record, a phone paired with its
    identity. None without assets/ or tshark."""
    import shutil
    if not KAILASH_CAPTURE.is_file() or not shutil.which("tshark"):
        return None
    import ble_pklg
    for m in ble_pklg.messages(str(KAILASH_CAPTURE)):
        # The reply, not the 4-byte request: the message that carries the settings themselves
        # (the watch writes to the phone here, so ble_pklg's IN/OUT names don't tell).
        if m.command == S.CMD_SETTINGS_READ and S.sbem_schema.MAGIC in m.payload and m.crc_ok:
            return bytes(m.payload)
    return None


@unittest.skipUnless(_kailash_descriptor() is not None and _kailash_read() is not None,
                     "needs assets/ (the Kailash's descriptor and the 7R app capture) and tshark")
class AgainstARealKailashRead(unittest.TestCase):
    """The Kailash (firmware 2.0.5) has the pairing list but no Pods list. Until 2026-11-25 the
    fix crashed on it, then skipped it; now it finds the phone record by its DeviceId and
    writes that one entry, the shape a Kailash merges (settings_write._write_single_entry)."""

    @classmethod
    def setUpClass(cls):
        import sbem_schema
        cls.schema = sbem_schema.load(_kailash_descriptor())
        cls.real = _kailash_read()
        cls.wl = B._group_id(cls.schema, B.WHITELIST_FIELD)
        cls.phone_id = B.phone_slot(cls.schema, cls.real)[1].device_id

    def with_record(self, payload, data):
        prefix, entries = S._split_entries(payload)
        out = bytearray(prefix) + S.sbem_schema.MAGIC
        for eid, d in entries:
            out += S._encode_entry(eid, data if eid == self.wl else d)
        return bytes(out)

    def broken(self):
        return self.with_record(self.real, record_bytes(device_id=self.phone_id))

    def test_the_phone_record_is_found_by_its_device_id(self):
        self.assertTrue(B.supported(self.schema))
        self.assertIsNone(B._pods_id(self.schema))
        slot, rec = B.phone_slot(self.schema, self.real)
        self.assertEqual(slot, 0)
        self.assertEqual(rec.device_id & 0xFFFF, B.PHONE_ID_LOW)
        self.assertEqual(B.Record(rec.to_bytes()).to_bytes(), rec.to_bytes())
        self.assertEqual(B.plan(self.schema, self.real, ADDR), {"state": "fine", "slot": 0})

    def test_a_privacy_off_pairing_gets_a_one_entry_write(self):
        broken = self.broken()
        step = B.plan(self.schema, broken, ADDR, fixed_rand(0x42))
        self.assertEqual((step["state"], step["slot"]), ("fix", 0))
        prefix, entries = S._split_entries(step["payload"])
        self.assertEqual(prefix, S._split_entries(broken)[0])     # the read's header, unchanged
        self.assertEqual([e for e, _d in entries], [self.wl])
        new = B.Record(entries[0][1])
        self.assertEqual(new.mac, B.watch_order(ADDR))
        self.assertFalse(B.irk_is_blank(new.irk))
        self.assertEqual((new.key, new.rnd, new.device_id), (LTK, RND, self.phone_id))

    def test_the_re_read_check(self):
        broken = self.broken()
        step = B.plan(self.schema, broken, ADDR, fixed_rand(0x42))
        new = step["record"]
        good = self.with_record(broken, new.to_bytes())
        self.assertIsNone(B.check_after(self.schema, broken, good, 0, new))
        self.assertIn("did not take", B.check_after(self.schema, broken, broken, 0, new))
        prefix, entries = S._split_entries(good)
        eid, data = next((e, d) for e, d in entries if e != self.wl)
        other = bytearray(prefix) + S.sbem_schema.MAGIC
        for e, d in entries:
            other += S._encode_entry(e, (bytes([d[0] ^ 1]) + d[1:]) if e == eid else d)
        self.assertIn("another setting changed",
                      B.check_after(self.schema, broken, bytes(other), 0, new))

    def test_several_pairing_records_are_left_alone(self):
        prefix, entries = S._split_entries(self.broken())
        out = bytearray(prefix) + S.sbem_schema.MAGIC
        for eid, d in entries:
            out += S._encode_entry(eid, d)
            if eid == self.wl:
                out += S._encode_entry(eid, record_bytes(device_id=0x1234))
        self.assertEqual(B.plan(self.schema, bytes(out), ADDR), {"state": "unsupported"})

    def test_run_writes_the_one_entry_and_confirms_it(self):
        test = self

        class FakeKailash:
            def __init__(self):
                self.state, self.writes = test.broken(), []

            def command(self, cmd, payload):
                if cmd == S.CMD_SETTINGS_READ:
                    return self.state
                self.writes.append(payload)
                _p, entries = S._split_entries(payload)
                self.state = test.with_record(self.state, entries[0][1])
                return b""

        watch = FakeKailash()
        result = B.run(watch, self.schema, ADDR, write=True, wait=lambda s: None)
        self.assertEqual(result, {"ok": True, "state": "fix", "slot": 0, "written": True,
                                  "error": None})
        self.assertEqual(len(watch.writes), 1)
        self.assertEqual(B.plan(self.schema, watch.state, ADDR)["state"], "fine")


class TheToolNeverPrintsATraceback(unittest.TestCase):
    def test_a_missing_field_is_one_json_line(self):
        import contextlib
        import io
        import write_nav

        class Link:
            def __init__(self, **_kw):
                pass

            def open(self):
                pass

            def command(self, cmd, payload):
                return b""
        saved = (write_nav.Link, B.sbem_schema.for_link, B.run, B.sbem_schema.load)
        try:
            write_nav.Link = Link
            B.sbem_schema.for_link = lambda link: pathlib.Path(__file__)   # exists
            B.sbem_schema.load = lambda path: object()

            def boom(*_a, **_k):
                raise KeyError("no field ending in '.Something' in this watch's own schema")
            B.run = boom
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = B.main(["--address", ADDR, "--write", "--json"])
        finally:
            write_nav.Link, B.sbem_schema.for_link, B.run, B.sbem_schema.load = saved
        result = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(code, 1)
        self.assertFalse(result["ok"])
        self.assertIn("not laid out", result["error"])

    def test_no_stored_layout_stands_in_for_the_watchs_own(self):
        """Pre-release review, 2026-11-25: if the watch's own layout can't be had, nothing is
        sent, rather than decoding its pairing list with another model's stored layout."""
        import contextlib
        import io
        import write_nav

        class Link:
            def __init__(self, *a, **k):
                pass

            def open(self):
                pass

            def command(self, cmd, payload):
                return b""

        def refused(link):
            raise RuntimeError("short reply")

        def never(*_a, **_k):
            raise AssertionError("nothing may run without the watch's own layout")
        saved = (write_nav.Link, B.sbem_schema.for_link, B.run)
        try:
            write_nav.Link, B.sbem_schema.for_link, B.run = Link, refused, never
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = B.main(["--address", ADDR, "--write", "--json"])
        finally:
            write_nav.Link, B.sbem_schema.for_link, B.run = saved
        result = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(code, 1)
        self.assertIn("settings layout", result["error"])


def _descriptor():
    try:
        import sbem_schema
        return sbem_schema.default_descriptor()          # the Ambit3 Peak's
    except Exception:                                    # noqa: BLE001
        return None


@unittest.skipUnless(CAPTURE.is_file() and _descriptor() is not None and _descriptor().exists(),
                     "needs assets/ (captures and the watch's descriptor)")
class AgainstARealSettingsRead(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import sbem_schema
        from write_nav import settings_from_capture
        cls.schema = sbem_schema.load(_descriptor())
        cls.real = settings_from_capture(str(CAPTURE))     # holds an iPhone pairing in slot 7
        cls.wl = B._group_id(cls.schema, B.WHITELIST_FIELD)
        cls.pods = B._group_id(cls.schema, B.PODS_FIELD)
        # A Linux pairing replaces the phone record; the Pods list keeps pointing at its id.
        cls.phone_id = B.phone_slot(cls.schema, cls.real)[1].device_id

    def broken(self):
        return self.with_slot(self.real, 7, record_bytes(device_id=self.phone_id))

    def with_slot(self, payload, slot, data):
        prefix, entries = S._split_entries(payload)
        out, n = bytearray(prefix) + S.sbem_schema.MAGIC, 0
        for eid, d in entries:
            if eid == self.wl:
                d = data if n == slot else d
                n += 1
            out += S._encode_entry(eid, d)
        return bytes(out)

    def test_every_real_record_round_trips_and_the_phone_is_found(self):
        _p, entries = S._split_entries(self.real)
        for eid, d in entries:
            if eid == self.wl:
                self.assertEqual(B.Record(d).to_bytes(), bytes(d))
        slot, rec = B.phone_slot(self.schema, self.real)
        self.assertEqual(slot, 7)
        self.assertFalse(B.needs_fix(rec))
        self.assertEqual(B.plan(self.schema, self.real, ADDR), {"state": "fine", "slot": 7})

    def test_a_phone_record_without_a_key_is_never_fine(self):
        keyless = self.with_slot(self.real, 7, record_bytes(mac="11:22:33:44:55:66", irk=LTK,
                                                            key="", device_id=self.phone_id))
        self.assertEqual(B.plan(self.schema, keyless, ADDR), {"state": "no_key", "slot": 7})

    def test_the_planned_write_changes_only_the_phone_record(self):
        broken = self.broken()
        step = B.plan(self.schema, broken, ADDR, fixed_rand(0x42))
        self.assertEqual((step["state"], step["slot"]), ("fix", 7))
        plain, _ = S.build_write_payload(self.schema, broken, B.TEMPLATE)
        _p, got = S._split_entries(step["payload"])
        _p, want = S._split_entries(plain)
        self.assertEqual(len(got), len(want))
        n = 0
        for (eg, dg), (ew, dw) in zip(got, want):
            self.assertEqual(eg, ew)
            if eg == self.wl:
                if n == 7:
                    new = B.Record(dg)
                    self.assertEqual(new.mac, "01:ee:dd:cc:bb:aa")
                    self.assertEqual((new.key, new.rnd), (LTK, RND))
                else:
                    self.assertEqual(dg, dw)
                n += 1
            else:
                self.assertEqual(dg, dw)
        self.assertEqual(n, 8)

    def test_the_re_read_check(self):
        broken = self.broken()
        step = B.plan(self.schema, broken, ADDR, fixed_rand(0x42))
        new = step["record"]
        good = self.with_slot(broken, 7, new.to_bytes())
        self.assertIsNone(B.check_after(self.schema, broken, good, 7, new))
        self.assertIn("did not take", B.check_after(self.schema, broken, broken, 7, new))
        lost_key = B.Record(new.to_bytes())
        lost_key.key = ZERO(16)
        self.assertIn("key changed", B.check_after(
            self.schema, broken, self.with_slot(broken, 7, lost_key.to_bytes()), 7, new))
        other = self.with_slot(good, 0, record_bytes(device_id=0x1234))
        self.assertIn("record 0 changed", B.check_after(self.schema, broken, other, 7, new))

    def test_run_reads_only_unless_asked_and_retries_an_unanswered_write(self):
        broken = self.broken()
        schema = self.schema

        class FakeWatch:
            def __init__(self, answer_writes_after=0):
                self.state, self.sent, self.skip = broken, [], answer_writes_after

            def command(self, cmd, payload):
                self.sent.append(cmd)
                if cmd == S.CMD_SETTINGS_READ:
                    return self.state
                if self.skip:                       # busy: no answer, nothing stored
                    self.skip -= 1
                    raise RuntimeError("no reply")
                _p, entries = S._split_entries(payload)
                rec = [d for e, d in entries if e == B._group_id(schema, B.WHITELIST_FIELD)][7]
                self.state = AgainstARealSettingsRead.with_slot(self_test, self.state, 7, rec)
                return b""

        self_test = self
        watch = FakeWatch()
        self.assertEqual(B.run(watch, schema, ADDR, write=False),
                         {"ok": True, "state": "fix", "slot": 7, "written": False})
        self.assertNotIn(S.CMD_SETTINGS_WRITE, watch.sent)

        watch = FakeWatch(answer_writes_after=1)
        waits = []
        result = B.run(watch, schema, ADDR, write=True, wait=waits.append)
        self.assertTrue(result["ok"] and result["written"], result)
        self.assertEqual(watch.sent.count(S.CMD_SETTINGS_WRITE), 2)
        self.assertTrue(waits)
        self.assertEqual(B.plan(schema, watch.state, ADDR)["state"], "fine")

        watch = FakeWatch(answer_writes_after=5)
        result = B.run(watch, schema, ADDR, write=True, wait=lambda s: None)
        self.assertFalse(result["ok"])
        self.assertEqual(watch.sent.count(S.CMD_SETTINGS_WRITE), 3)


if __name__ == "__main__":
    unittest.main(verbosity=1)
