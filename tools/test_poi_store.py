#!/usr/bin/env python3
"""The POI store as the Ambit3 firmware runs it (MSP430X Emu 2.4.17, read 2026-10-24):
0x0b25 APPENDS each record it is given, 0x0b02 counts the store, and 0x0b24 answers in
pages of [u16 nextCursor][u16 0][u16 isLast][data] with the SBEM header on the first page
only. Adding a POI used to send "new + everything already there", which doubled the list
on every add; reading took the first page only."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ble_pois  # noqa: E402
import write_nav as W  # noqa: E402

MAGIC = b"SBEM0102"


class FakeWatch:
    """Enough of the watch's POI commands to tell an append from a replace."""

    def __init__(self, page_data=0x3FA):
        self.records = []
        self.page_data = page_data
        self.dry_run = False
        self.sent = []

    def command(self, command, payload=b"", **_):
        self.sent.append(command)
        if command == W.CMD_DEVICE_INFO:
            return b"Emu".ljust(16, b"\0") + b"8A153C5111000900".ljust(16, b"\0") + bytes(12)
        if command == W.CMD_POI_CLEAR:           # 0x0b04 alone: both POI lists emptied
            self.records.clear()
            return b""
        if command == W.CMD_POI_COUNT:
            return len(self.records).to_bytes(4, "little")
        if command == W.CMD_POI_WRITE:
            body = payload[len(W.SBEM_WRITE_PREFIX) + len(MAGIC):]
            assert body[0] == W.POI_ENTRY
            if body[1] == 0xFF:
                size, data = int.from_bytes(body[2:6], "little"), body[6:]
            else:
                size, data = body[1], body[2:]
            assert len(data) == size
            # One 0x55 entry may carry several records back to back (name, route name and
            # stamp NUL-terminated, then 5 + 8 bytes); each is appended, never replacing.
            off = 0
            while off < len(data):
                end = off
                for _ in range(3):
                    end = data.index(b"\0", end) + 1
                end += 13
                self.records.append(bytes(data[off:end]))
                off = end
            return b""
        if command == W.CMD_POI_READ:
            if not self.records:
                return b""                       # an empty store: error, no data (USB: b"")
            # A name over 15 bytes is kept but listed as an empty entry (André's Peak 2026-10-25).
            shown = [b"" if len(r.split(b"\0")[0]) > 15 else r for r in self.records]
            stream = MAGIC + b"".join(bytes([W.POI_ENTRY, len(r)]) + r for r in shown)
            cursor = int.from_bytes(payload[:2], "little")
            chunk = stream[cursor:cursor + self.page_data]
            nxt = cursor + len(chunk)
            last = nxt >= len(stream)
            head = (0 if last else nxt).to_bytes(2, "little") + b"\0\0" + int(last).to_bytes(2, "little")
            return head + chunk
        raise AssertionError(f"unexpected command 0x{command:04x}")


class BleFakeWatch(FakeWatch):
    """The same store answering like the Bluetooth link: a refused read raises."""

    refuse_reads = False

    def command(self, command, payload=b"", **kw):
        if command == W.CMD_POI_READ and (not self.records or self.refuse_reads):
            self.sent.append(command)
            raise RuntimeError("the watch refused 0x0b24 (error flags 0x01)")
        return super().command(command, payload, **kw)


def names(reply):
    return [r.split(b"\0")[0].decode() for e, r in W.F.sbem_entries(reply) if e == W.POI_ENTRY]


class PoiStore(unittest.TestCase):
    def test_add_sends_only_the_new_poi(self):
        watch = FakeWatch()
        for name in ("A", "B", "C"):
            self.assertTrue(ble_pois.add_poi(watch, name, 50.6, 3.0)["ok"])
        self.assertEqual(len(watch.records), 3)
        self.assertEqual(names(W.read_pois(watch)), ["A", "B", "C"])

    def test_an_add_the_watch_did_not_keep_is_an_error(self):
        watch = FakeWatch()
        watch.command = lambda c, p=b"", **k: (b"\0\0\0\0" if c == W.CMD_POI_COUNT else b"")
        result = ble_pois.add_poi(watch, "A", 50.6, 3.0)
        self.assertFalse(result["ok"])
        self.assertIn("0 -> 0", result["error"])

    def test_a_long_accented_name_is_cut_to_what_the_watch_keeps(self):
        watch = FakeWatch()
        self.assertTrue(ble_pois.add_poi(watch, "114 Av. André Malraux", 48.84, 2.23)["ok"])
        self.assertEqual(names(W.read_pois(watch)), ["114 Av. André"])

    def test_empty_store_reads_as_nothing(self):
        self.assertEqual(W.read_pois(FakeWatch()), b"")

    def test_empty_store_over_bluetooth_reads_as_nothing(self):
        # Over Bluetooth the refusal of an empty store arrives as an error, not b"".
        watch = BleFakeWatch()
        self.assertEqual(W.read_pois(watch), b"")
        self.assertTrue(ble_pois.add_poi(watch, "A", 50.6, 3.0)["ok"])
        self.assertEqual(names(W.read_pois(watch)), ["A"])

    def test_a_refusal_with_pois_held_is_still_an_error(self):
        watch = BleFakeWatch()
        ble_pois.add_poi(watch, "A", 50.6, 3.0)
        watch.refuse_reads = True
        with self.assertRaises(RuntimeError):
            W.read_pois(watch)

    def test_route_write_pois_survive_a_put_back_that_breaks_halfway(self):
        # The routes commit empties the store; the first put-back dies after one 0x0b25.
        import tempfile
        watch = FakeWatch()
        for i in range(12):
            ble_pois.add_poi(watch, f"Point number {i}", 50.0 + i / 100, 3.0)
        before = W.read_pois(watch)
        with tempfile.TemporaryDirectory() as d:
            backup = W.backup_pois_before_route_write(watch, before, "SERIAL1", d)
            self.assertEqual(pathlib.Path(backup).read_bytes(), before)
            watch.records.clear()                       # what the 0x0b04 commit does
            real, writes = watch.command, [0]

            def flaky(command, payload=b"", **kw):
                if command == W.CMD_POI_WRITE:
                    writes[0] += 1
                    if writes[0] == 2:
                        raise TimeoutError("no reply to 0x0b25 within 20s")
                return real(command, payload, **kw)
            watch.command = flaky
            back, err = W.put_pois_back_safely(watch, before, backup)
        self.assertIsNone(err)
        self.assertEqual(back, 12)
        self.assertEqual(sorted(names(W.read_pois(watch))), sorted(names(before)))

    def test_route_write_put_back_failing_twice_names_the_backup(self):
        watch = FakeWatch()
        ble_pois.add_poi(watch, "A", 50.6, 3.0)
        before = W.read_pois(watch)
        watch.records.clear()
        real = watch.command
        watch.command = lambda c, p=b"", **k: ((_ for _ in ()).throw(TimeoutError("gone"))
                                               if c == W.CMD_POI_WRITE else real(c, p, **k))
        back, err = W.put_pois_back_safely(watch, before, "/x/POIs_pre_1.bin")
        self.assertIsNone(back)
        self.assertIn("/x/POIs_pre_1.bin", err)

    def test_no_backup_without_pois_or_in_a_dry_run(self):
        self.assertIsNone(W.backup_pois_before_route_write(FakeWatch(), b"", "S", "/nonexistent"))
        dry = FakeWatch()
        dry.dry_run = True
        self.assertIsNone(W.backup_pois_before_route_write(dry, b"x", "S", "/nonexistent"))

    def test_a_long_list_is_read_page_by_page(self):
        watch = FakeWatch(page_data=60)          # a few POIs per page
        for i in range(12):
            ble_pois.add_poi(watch, f"P{i:02d}", 50.6, 3.0)
        reply = W.read_pois(watch)
        self.assertEqual(names(reply), [f"P{i:02d}" for i in range(12)])
        self.assertGreater(watch.sent.count(W.CMD_POI_READ), 1)


    # A route write erases the POIs (its 0x0b04 commit) and puts back what it read. Past 254
    # bytes that used to go back as one entry with the extended header (0x55 0xFF + u32), the
    # form that corrupted the POI store (2026-08-30). André's 8 POIs are about 400 bytes.
    def test_a_long_list_goes_back_in_entries_of_at_most_254_bytes(self):
        watch = FakeWatch()
        for i in range(9):
            ble_pois.add_poi(watch, f"Point number {i}", 50.6 + i / 100, 3.0)
        reply = W.read_pois(watch)
        payloads = W.poi_restore_payloads(reply)
        self.assertGreater(len(payloads), 1)
        for payload in payloads:
            body = payload[len(W.SBEM_WRITE_PREFIX) + len(MAGIC):]
            self.assertEqual(body[0], W.POI_ENTRY)
            self.assertLessEqual(body[1], W.POI_ENTRY_MAX)
            self.assertEqual(len(body), 2 + body[1])     # one entry, single-byte length

        cleared = FakeWatch()                            # the store after the 0x0b04 commit
        for payload in payloads:
            cleared.command(W.CMD_POI_WRITE, payload)
        # Most recently modified first, as SuuntoLink puts them back on `routedelete`.
        self.assertEqual(names(W.read_pois(cleared)), [f"Point number {i}" for i in reversed(range(9))])
        self.assertEqual(W.poi_restore_count(reply), 9)
        self.assertTrue(W.check_poi_count(cleared, 9))

    def test_an_empty_entry_has_nothing_to_put_back(self):
        watch = FakeWatch()
        ble_pois.add_poi(watch, "A", 50.6, 3.0)
        watch.records.append(b"N" * 16 + b"\0\0" + b"2026-10-25T09:00:00\0" + bytes(13))   # name too long
        reply = W.read_pois(watch)
        self.assertEqual(W.poi_restore_count(reply), 1)
        self.assertEqual(len(W.poi_restore_payloads(reply)), 1)

    def test_no_poi_means_no_write(self):
        self.assertEqual(W.poi_restore_payloads(b""), [])
        self.assertEqual(W.build_set_pois_payloads([]), [])

    def test_a_set_list_never_uses_the_extended_header(self):
        pois = [{"name": f"Point number {i}", "lat": 50.6, "lon": 3.0} for i in range(12)]
        payloads = W.build_set_pois_payloads(pois)
        watch = FakeWatch()
        for payload in payloads:
            body = payload[len(W.SBEM_WRITE_PREFIX) + len(MAGIC):]
            self.assertNotEqual(body[1], 0xFF)
            watch.command(W.CMD_POI_WRITE, payload)
        self.assertEqual(len(watch.records), 12)

    def test_a_count_that_does_not_match_is_reported(self):
        watch = FakeWatch()
        ble_pois.add_poi(watch, "A", 50.6, 3.0)
        self.assertFalse(W.check_poi_count(watch, 2))



class RemovePoi(unittest.TestCase):
    """Removing a POI on the desktop (write_nav.remove_pois), the Android flow's twin."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.watch = FakeWatch()
        for name, lat in (("SommetA", 50.6), ("Maceira Leiria", 39.69), ("SommetB", 50.61)):
            ble_pois.add_poi(self.watch, name, lat, 3.06)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_others_stay_byte_for_byte_and_a_copy_is_saved_first(self):
        before = W.read_pois(self.watch)
        kept = [r for r in self.watch.records if not r.startswith(b"SommetA\0")]
        result = W.remove_pois(self.watch, [("SommetA", 50.6, 3.06)], self.tmp.name)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["left"], 2)
        self.assertEqual(sorted(self.watch.records), sorted(kept))
        self.assertTrue(result["backup"].endswith(".bin"))
        self.assertIn("/POIs/8A153C5111000900/POIs_pre_", result["backup"])
        self.assertEqual(pathlib.Path(result["backup"]).read_bytes(), before)
        self.assertLess(self.watch.sent.index(W.CMD_POI_READ), self.watch.sent.index(W.CMD_POI_CLEAR))

    def test_of_two_identical_pois_one_stays(self):
        ble_pois.add_poi(self.watch, "SommetA", 50.6, 3.06)
        self.assertTrue(W.remove_pois(self.watch, [("SommetA", 50.6, 3.06)], self.tmp.name)["ok"])
        self.assertEqual(sorted(names(W.read_pois(self.watch))),
                         ["Maceira Leiria", "SommetA", "SommetB"])

    def test_a_poi_not_there_writes_nothing(self):
        result = W.remove_pois(self.watch, [("SommetA", 50.7, 3.06)], self.tmp.name)
        self.assertFalse(result["ok"])
        self.assertIn("SommetA", result["error"])
        self.assertNotIn(W.CMD_POI_CLEAR, self.watch.sent)
        self.assertEqual(len(self.watch.records), 3)

    def test_empty_entries_are_cleared_too(self):
        self.watch.records.append(b"N" * 16 + b"\0\0" + b"2026-10-25T09:00:00\0" + bytes(13))
        result = W.remove_pois(self.watch, [("SommetB", 50.61, 3.06)], self.tmp.name)
        self.assertEqual((result["ok"], result["left"], result["emptyCleared"]), (True, 2, 1))
        self.assertEqual(sorted(names(W.read_pois(self.watch))), ["Maceira Leiria", "SommetA"])

    def test_the_last_poi_leaves_an_empty_list(self):
        for name, lat in (("SommetA", 50.6), ("Maceira Leiria", 39.69), ("SommetB", 50.61)):
            self.assertTrue(W.remove_pois(self.watch, [(name, lat, 3.06)], self.tmp.name)["ok"])
        self.assertEqual(self.watch.records, [])

    def test_a_write_the_watch_did_not_keep_puts_the_list_back(self):
        real = self.watch.command
        state = {"writes": 0}

        def flaky(command, payload=b"", **k):
            if command == W.CMD_POI_WRITE:
                state["writes"] += 1
                if state["writes"] == 1:
                    return b""                   # acked, not stored (0x0b25 always acks)
            return real(command, payload, **k)
        self.watch.command = flaky
        before = sorted(self.watch.records)
        result = W.remove_pois(self.watch, [("SommetA", 50.6, 3.06)], self.tmp.name)
        self.assertFalse(result["ok"])
        self.assertTrue(result["restored"])
        self.assertEqual(sorted(self.watch.records), before)

    def test_the_routes_command_is_never_sent(self):
        W.remove_pois(self.watch, [("SommetA", 50.6, 3.06)], self.tmp.name)
        self.assertFalse({0x0B16, 0x0B18} & set(self.watch.sent))

    def test_the_list_is_read_twice_and_must_not_have_changed(self):
        """As Android's remove: a POI saved on the watch itself between the first read and the
        emptying would be lost. Nothing is written when the two reads differ."""
        real = self.watch.command
        reads = {"n": 0}

        def a_poi_saved_on_the_watch_meanwhile(command, payload=b"", **k):
            if command == W.CMD_POI_READ and payload[:2] == b"\0\0":
                reads["n"] += 1
                if reads["n"] == 2:
                    self.watch.records.append(W.build_poi_record("On the watch", 41.0, -8.0))
            return real(command, payload, **k)
        self.watch.command = a_poi_saved_on_the_watch_meanwhile
        result = W.remove_pois(self.watch, [("SommetA", 50.6, 3.06)], self.tmp.name)
        self.assertFalse(result["ok"])
        self.assertTrue(result["changed"])
        self.assertIn("changed", result["error"])
        self.assertNotIn(W.CMD_POI_ERASE if hasattr(W, "CMD_POI_ERASE") else 0x0B04, self.watch.sent)
        self.assertEqual(len(self.watch.records), 4)                 # all three, and the new one

    def test_an_unchanged_list_costs_one_more_read(self):
        self.watch.sent.clear()
        self.assertTrue(W.remove_pois(self.watch, [("SommetA", 50.6, 3.06)], self.tmp.name)["ok"])
        first_write = self.watch.sent.index(0x0B04)
        self.assertEqual(self.watch.sent[:first_write].count(W.CMD_POI_READ), 2)


if __name__ == "__main__":
    unittest.main()
