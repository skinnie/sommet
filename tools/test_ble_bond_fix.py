#!/usr/bin/env python3
"""ble_bond_fix.py without a watch: the pairing record's layout, the checks, the planned write
and the re-read check. The parts that need a real settings read use the SuuntoLink captures and
the watch's descriptor in assets/ (not in the public repository) and are skipped without them.
All addresses and keys here are made up."""

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


@unittest.skipUnless(platform.system() == "Linux", "Linux only")
class PrivacyStepSwitch(unittest.TestCase):
    def test_the_test_switch_skips_the_privacy_step(self):
        import bt_privacy
        os.environ["SOMMET_BT_PAIR_WITHOUT_PRIVACY"] = "1"
        try:
            needed, reason = bt_privacy.check()
        finally:
            del os.environ["SOMMET_BT_PAIR_WITHOUT_PRIVACY"]
        self.assertFalse(needed)
        self.assertIn("without the privacy step", reason)


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
