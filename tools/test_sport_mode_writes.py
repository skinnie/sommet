#!/usr/bin/env python3
"""Offline tests: every sport-mode editor the desktop calls writes only the used extent of
CustomModes and never sends 0x0b04. Before 2026-10-29 the field/rename/display/interval tools
wrote the whole padded 12 KB region (a hash the watch rejects after a restart) and most of them
followed it with 0x0b04, which empties the watch's POI lists. No watch needed.

    ./tools/test_sport_mode_writes.py        # or: python3 -m unittest test_sport_mode_writes
"""
import contextlib
import hashlib
import io
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

import ambit_format as F
import custom_modes
import write_nav
from ambit_pcap import FlashImage

DEMO = pathlib.Path(__file__).resolve().parent.parent / "desktop/backend/demo_data/custommodes.bin"


class FakeWatch:
    """Just enough of a USB link: applies 0x0b16 writes to a flash image and records commands."""

    def __init__(self, region):
        self.flash = bytearray(b"\xff" * 0x200000)
        self.flash[F.CUSTOM_MODES_BASE:F.CUSTOM_MODES_BASE + len(region)] = region
        self.commands, self.written, self.tails = [], 0, []

    def open(self):
        pass

    def command(self, cmd, payload=b""):
        self.commands.append(cmd)
        if cmd == write_nav.CMD_DATA_WRITE:
            addr, n = struct.unpack_from("<IH", payload)
            self.flash[addr:addr + n] = payload[8:8 + n]
            self.written += n
        elif cmd == write_nav.CMD_DATA_TAIL:
            self.tails.append(payload[8:72].decode())
        return b""

    def read(self, base, size):
        return bytes(self.flash[base:base + size])


def region():
    raw = DEMO.read_bytes()
    return raw + b"\xff" * (F.CUSTOM_MODES_REGION_SIZE - len(raw))


def run_tool(module, argv):
    """Runs a tool's main() against a FakeWatch; returns the watch."""
    watch = FakeWatch(region())
    backups = tempfile.mkdtemp(prefix="sportmode-test-")
    patches = [mock.patch.object(module, "Link", lambda *a, **k: watch),
               mock.patch.object(module, "read_memory_map", lambda link: {}),
               mock.patch.object(module, "check_memory_map", lambda found: True),
               mock.patch.object(module, "read_flash",
                                 lambda link, base, size, label=None: watch.read(base, size)),
               mock.patch.object(module, "BACKUP_DIR", pathlib.Path(backups)),
               mock.patch.object(sys, "argv", [module.__name__] + argv + ["--write", "--json"])]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        code = module.main()
    assert code == 0, f"{module.__name__} exited {code}"
    return watch


class SportModeEditorsWriteSafely(unittest.TestCase):
    def check(self, watch):
        new = watch.read(F.CUSTOM_MODES_BASE, F.CUSTOM_MODES_REGION_SIZE)
        used = custom_modes.used_extent(new)
        self.assertNotIn(write_nav.CMD_NAV_COMMIT, watch.commands, "0x0b04 empties the POI lists")
        self.assertEqual(watch.written, used, "wrote more than the used extent")
        self.assertEqual(watch.tails, [hashlib.sha256(new[:used]).hexdigest().upper()])
        return custom_modes.decode(new)

    def mode(self, decoded, name):
        return next(m["Settings"] for m in decoded["exercise_modes"] if m["Settings"]["Name"] == name)

    def test_autolap_and_hr_limits(self):
        import custom_modes_field_write_test as tool
        watch = run_tool(tool, ["--mode", "Running", "--set", "Autolap=1500", "--set", "HrHigh=171"])
        m = self.mode(self.check(watch), "Running")
        self.assertEqual((m["Autolap"], m["HrHigh"]), (1500, 171))

    def test_value_the_watch_cannot_store_is_refused_before_usb(self):
        # Autolap "2.50" typed on a comma-decimal system arrived as 250000 (2026-10-29): the
        # tool read the watch, then crashed in struct.pack, and the app showed nothing.
        import custom_modes_field_write_test as tool
        for value in ("250000", "2.5"):
            with mock.patch.object(tool, "Link", side_effect=AssertionError("opened the watch")), \
                    mock.patch.object(sys, "argv", ["t", "--mode", "Running", "--set",
                                                    f"Autolap={value}", "--write", "--json"]), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(tool.main(), 1)
            self.assertIn("not a value the watch can store", out.getvalue())

    def test_rename(self):
        import custom_modes_rename_test as tool
        watch = run_tool(tool, ["--from", "Running", "--to", "Run easy"])
        names = [m["Settings"]["Name"] for m in self.check(watch)["exercise_modes"]]
        self.assertIn("Run easy", names)
        self.assertNotIn("Running", names)

    def test_display_field(self):
        import custom_modes_display_field_write_test as tool
        self.check(run_tool(tool, ["--mode", "Running", "--display", "0", "--field", "1",
                                   "--to", "FT_TIME", "--type", "FT_HEART_RATE_CURR"]))

    def test_interval_timer(self):
        import interval_timer as tool
        self.check(run_tool(tool, ["--mode", "Running", "--enable", "--type", "time",
                                   "--high", "60", "--low", "30", "--reps", "5"]))


class SendPlanGuards(unittest.TestCase):
    def blob(self):
        return DEMO.read_bytes()

    def send(self, layout, commit):
        fi = FlashImage()
        for _name, base, blob in layout:
            if blob:
                fi.write(base, blob)
        watch = FakeWatch(b"")
        write_nav.send_plan(watch, fi, layout, commit=commit)
        return watch

    def test_labelled_padded_custom_modes_is_refused(self):
        padded = region()
        with self.assertRaisesRegex(ValueError, "used extent"):
            self.send([("CustomModes (Running field write)", F.CUSTOM_MODES_BASE, padded),
                       ("tail", F.CUSTOM_MODES_BASE, None)], commit=False)

    def test_nav_commit_after_custom_modes_or_apps_is_refused(self):
        for name in ("CustomModes", "CustomModes (rename)", "Apps"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "0x0b04"):
                self.send([(name, F.CUSTOM_MODES_BASE, self.blob()),
                           ("tail", F.CUSTOM_MODES_BASE, None)], commit=True)

    def test_refusal_happens_before_any_usb_command(self):
        watch = FakeWatch(b"")
        fi = FlashImage()
        fi.write(F.CUSTOM_MODES_BASE, self.blob())
        with self.assertRaises(ValueError):
            write_nav.send_plan(watch, fi, [("CustomModes", F.CUSTOM_MODES_BASE, self.blob()),
                                            ("tail", F.CUSTOM_MODES_BASE, None)], commit=True)
        self.assertEqual(watch.commands, [])

    def test_route_writes_still_commit(self):
        flash, layout = write_nav.build_reset()
        watch = FakeWatch(b"")
        write_nav.send_plan(watch, flash, layout)
        self.assertEqual(watch.commands[-1], write_nav.CMD_NAV_COMMIT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
