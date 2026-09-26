#!/usr/bin/env python3
"""Offline tests for write_nav's last-gate CustomModes guard: a padded region (the bug that put a
Peak into "Connect to Moveslink" on 2026-09-26) must never reach send_plan's first USB command.
No watch needed.

    ./tools/test_write_guards.py            # or: python3 -m unittest test_write_guards
"""
import glob
import pathlib
import struct
import unittest

import custom_modes
import write_nav
from ambit_pcap import FlashImage

BASE = 0x2000


class NoUsb:
    """A link that fails the test if anything is actually sent."""
    def command(self, *a, **k):
        raise AssertionError("guard let a write through to the watch")


def synthetic(used, pad):
    body = bytes(used)
    return struct.pack("<HH", custom_modes.DEVICE_CUSTOM, len(body)) + body + b"\xff" * pad


def real_backup():
    hits = sorted(glob.glob(str(pathlib.Path(__file__).parent / "backups" / "CustomModes_*.bin")))
    return open(hits[-1], "rb").read() if hits else None


class CustomModesGuard(unittest.TestCase):
    def _send(self, blob):
        fi = FlashImage()
        fi.write(BASE, blob)
        write_nav.send_plan(NoUsb(), fi, [("CustomModes", BASE, blob), ("t", BASE, None)], commit=False)

    def test_padded_region_is_refused_before_any_usb_command(self):
        with self.assertRaisesRegex(ValueError, "used extent"):
            self._send(synthetic(40, 12288 - 44))

    def test_real_raw_backup_is_refused_and_its_used_extent_passes_the_guard(self):
        raw = real_backup()
        if raw is None:
            self.skipTest("no CustomModes backup on this machine")
        with self.assertRaisesRegex(ValueError, "used extent"):
            write_nav.refuse_bad_custom_modes(raw)
        write_nav.refuse_bad_custom_modes(raw[:custom_modes.used_extent(raw)])  # no exception


if __name__ == "__main__":
    unittest.main(verbosity=2)
