#!/usr/bin/env python3
"""tools/bryton_ee.py: reading u-blox AlmanacPlus (.alp) GPS prediction files and replacing the
Aero 60's System/AssitOff.alp + System/date.txt over USB - only when the device already holds the
same kind of file, with a backup first. Made-up files in a temporary folder; no device, no network."""
import datetime
import pathlib
import struct
import tempfile
import unittest

import bryton_ee as E

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 30, 9, 0, tzinfo=UTC)


def alp(start, days=14, size=1024):
    """A file shaped like u-blox's: magic, length/4 at 0x44, TOW/week/span at 0x4C."""
    gps = start - E.GPS_EPOCH + datetime.timedelta(seconds=E.LEAP_SECONDS)
    week, tow = divmod(int(gps.total_seconds()), 7 * 86400)
    b = bytearray(size)
    struct.pack_into("<I", b, 0, E.ALP_MAGIC)
    struct.pack_into("<H", b, 0x44, size // 4)
    struct.pack_into("<IHH", b, 0x4C, tow, week, days * 144)
    return bytes(b)


def date_txt(when):
    return struct.pack(">I", int(when.timestamp()))


class Decode(unittest.TestCase):
    def test_valid_file(self):
        info = E.decode(alp(NOW))
        self.assertTrue(info["ok"])
        self.assertEqual(info["days"], 14.0)
        self.assertEqual(info["start"], NOW.isoformat())
        self.assertEqual(info["end"], (NOW + datetime.timedelta(days=14)).isoformat())

    def test_broken(self):
        self.assertIn("not u-blox", E.decode(b"\xb5\x62\x13\x00" + bytes(100))["error"])
        self.assertIn("header says", E.decode(alp(NOW)[:-4])["error"])
        self.assertIn("too short", E.decode(b"\xb5\x62")["error"])
        bad = bytearray(alp(NOW))
        struct.pack_into("<H", bad, 0x52, 0)
        self.assertIn("make no sense", E.decode(bytes(bad))["error"])

    def test_date_file_wins(self):
        info = E.good_until(E.decode(alp(NOW)), NOW + datetime.timedelta(days=2), NOW)
        self.assertEqual(info["validUntil"], (NOW + datetime.timedelta(days=2)).isoformat())
        self.assertFalse(info["expired"])
        old = E.good_until(E.decode(alp(NOW - datetime.timedelta(days=37))), None, NOW)
        self.assertTrue(old["expired"])
        self.assertEqual(old["daysLeft"], 0)

    def test_new_date_is_bryton_midnight_capped_by_the_file(self):
        until = E.new_date(E.decode(alp(NOW)), NOW)
        local = until.astimezone()
        self.assertEqual((local.hour, local.minute, local.second), (0, 0, 0))
        self.assertEqual(local.date(), NOW.astimezone().date() + datetime.timedelta(days=14))
        short = E.decode(alp(NOW, days=7))
        self.assertEqual(E.new_date(short, NOW).isoformat(), short["end"])


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mount = pathlib.Path(self.tmp.name) / "BRYTON"
        (self.mount / "System").mkdir(parents=True)
        (self.mount / "System" / "device.txt").write_bytes(b"[MODEL]\nModel=Aero60\n\0\0")
        self.saved_backup = E.BACKUP_DIR
        E.BACKUP_DIR = pathlib.Path(self.tmp.name) / "backups"
        self.dev = self.mount / "System" / "AssitOff.alp"
        self.date = self.mount / "System" / "date.txt"
        self.old = alp(NOW - datetime.timedelta(days=37), size=2048)
        self.old_date = date_txt(NOW - datetime.timedelta(days=24))

    def tearDown(self):
        E.BACKUP_DIR = self.saved_backup
        self.tmp.cleanup()

    def put_old(self):
        self.dev.write_bytes(self.old)
        self.date.write_bytes(self.old_date)

    def test_status(self):
        self.put_old()
        s = E.device_status(self.mount, NOW)
        self.assertTrue(s["readable"] and s["expired"])
        self.assertEqual(s["model"], "Aero60")
        self.assertEqual(s["validUntil"], (NOW - datetime.timedelta(days=24)).isoformat())

    def test_dry_run_writes_nothing(self):
        self.put_old()
        r = E.install(self.mount, alp(NOW), write=False, now=NOW)
        self.assertTrue(r["ok"] and r["dryRun"])
        self.assertEqual(self.dev.read_bytes(), self.old)
        self.assertEqual(self.date.read_bytes(), self.old_date)

    def test_write_backs_up_both_then_replaces(self):
        self.put_old()
        new = alp(NOW)
        r = E.install(self.mount, new, write=True, now=NOW)
        self.assertTrue(r["ok"] and r["wrote"] and r["verified"])
        self.assertEqual(self.dev.read_bytes(), new)
        until = E.read_date(self.date.read_bytes())
        self.assertEqual(until, E.new_date(E.decode(new), NOW))
        backup = pathlib.Path(r["backup"])
        self.assertEqual((backup / "AssitOff.alp").read_bytes(), self.old)
        self.assertEqual((backup / "date.txt").read_bytes(), self.old_date)
        self.assertEqual(sorted(p.name for p in (self.mount / "System").iterdir()),
                         ["AssitOff.alp", "date.txt", "device.txt"])
        again = E.install(self.mount, new, write=True, now=NOW)
        self.assertFalse(again["wrote"])
        self.assertIn("already", again["note"])

    def test_unknown_format_is_left_alone(self):
        self.dev.write_bytes(b"\xb5\x62\x13\x00" + bytes(100))
        r = E.install(self.mount, alp(NOW), write=True, now=NOW)
        self.assertFalse(r["ok"])
        self.assertIn("left alone", r["error"])
        self.assertEqual(self.dev.read_bytes(), b"\xb5\x62\x13\x00" + bytes(100))
        self.assertFalse(self.date.exists())

    def test_no_file_on_device_adds_nothing(self):
        r = E.install(self.mount, alp(NOW), write=True, now=NOW)
        self.assertFalse(r["ok"])
        self.assertFalse(self.dev.exists() or self.date.exists())

    def test_bad_download_refused(self):
        self.put_old()
        self.assertFalse(E.install(self.mount, b"<html>error</html>", write=True, now=NOW)["ok"])
        r = E.install(self.mount, alp(NOW - datetime.timedelta(days=30)), write=True, now=NOW)
        self.assertIn("out of date", r["error"])
        self.assertEqual(self.dev.read_bytes(), self.old)

    def test_not_a_bryton(self):
        r = E.install(pathlib.Path(self.tmp.name) / "nothing", alp(NOW), write=True, now=NOW)
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
