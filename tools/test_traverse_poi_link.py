#!/usr/bin/env python3
"""POIs over the link on a Traverse / Traverse Alpha: its own entry number and an altitude.

SuuntoLink's descriptors (the list of fields each watch sends) put a POI ("WayPoint") in entry
0x55 on the Ambit3 (2.4.17, and the Vertical 1.1.22), in entry 0x53 on the Traverse 2.0.22 and
0x52 on the Traverse 1.0.4; on the Traverse the record also ends with an int16 altitude. The tools
knew only 0x55: a Traverse's POIs were not listed, a new one was not kept, and a route write
(which empties the POI store) did not put them back.

The replies read here are built by hand in the watch's form: one entry per POI, the newest
first. The reply the Traverse Alpha 2.0.22 program gave in the PC emulator (2026-11-13, "Rub 01"
and "Shot 01" saved from the watch's own Hunting shortlist) is not part of this repository; the
tests on it run only where that private file exists. No Traverse is on hand: nothing here was seen
on a real watch.

    python3 tools/test_traverse_poi_link.py
"""
import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ambit_format as F  # noqa: E402
import sbem_schema  # noqa: E402
import write_nav as W  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
# Private (assets/ is git-ignored): the emulated watch's own 0x0b24 reply.
EMULATED_REPLY = (HERE.parent / "assets" / "Firmware" / "re-out" / "family_emu" / "testdata"
                  / "traverse_alpha_poi_list_two_watch_made.bin")


def watch_reply(pois, entry=0x53):
    """A 0x0b24 reply as a watch sends it: [u16 next cursor][u16 0][u16 is last], "SBEM0102", then
    one entry per POI. pois = [(name, type, lat, lon, altitude or None)]."""
    out = bytes([0, 0, 0, 0, 1, 0]) + F.SBEM_MAGIC
    for name, type_, lat, lon, altitude in pois:
        record = W.build_poi_record(name, lat, lon, stamp="2026-11-08T07:00:30", type_=type_,
                                    altitude=altitude)
        out += bytes([entry, len(record)]) + record
    return out


# Two POIs on a Traverse Alpha, as its own Hunting shortlist names them.
REPLY = watch_reply([("Shot 01", 46, 46.0207, 7.7491, 1612), ("Rub 01", 39, 46.0207, 7.7491, 1612)])
PEAK, TRAVERSE, ALPHA = 0x001B, 0x002B, 0x002D
TRAVERSE_DESCRIPTOR = W.descriptor_for_product_id(TRAVERSE)


class WatchPoiEntry(unittest.TestCase):
    def test_entry_and_altitude_follow_the_watch(self):
        self.assertEqual(F.poi_entry_for_product(PEAK), (0x55, False))
        self.assertEqual(F.poi_entry_for_product(None), (0x55, False))
        self.assertEqual(F.poi_entry_for_product(TRAVERSE), (0x53, True))
        self.assertEqual(F.poi_entry_for_product(ALPHA), (0x53, True))
        self.assertEqual(F.poi_entry_for_product(TRAVERSE, firmware="1.0.4"), (0x52, True))
        self.assertEqual(F.poi_entry_for_product(TRAVERSE, firmware="2.0.22"), (0x53, True))

    @unittest.skipUnless(TRAVERSE_DESCRIPTOR and sbem_schema.default_descriptor().exists(),
                         "SuuntoLink's descriptors are not on this machine")
    def test_the_table_equals_suuntolinks_descriptors(self):
        self.assertEqual(sbem_schema.waypoint_entry(sbem_schema.load(TRAVERSE_DESCRIPTOR)),
                         (0x53, True))
        self.assertEqual(sbem_schema.waypoint_entry(sbem_schema.load(sbem_schema.default_descriptor())),
                         (0x55, False))
        old = TRAVERSE_DESCRIPTOR.with_name(TRAVERSE_DESCRIPTOR.name.replace("2.0.22", "1.0.4"))
        if old.exists():
            self.assertEqual(sbem_schema.waypoint_entry(sbem_schema.load(old)), (0x52, True))


class ReadingATraverse(unittest.TestCase):
    def test_both_watch_made_pois_are_read_with_their_traverse_type(self):
        pois = W.pois_of_reply(REPLY)
        self.assertEqual([(p["name"], p["type"]) for p in pois], [("Shot 01", 46), ("Rub 01", 39)])
        self.assertEqual((pois[0]["lat"], pois[0]["lon"], pois[0]["altitude"]), (460207000, 77491000, 1612))
        self.assertEqual(F.waypoint_type_name(pois[0]["type"], ALPHA), "Shot")
        self.assertEqual(F.waypoint_type_name(pois[1]["type"], ALPHA), "Rub")

    def test_an_ambit3_list_reads_as_before(self):
        record = W.build_poi_record("Hut", 46.5, 7.5, stamp="2026-08-06T10:00:00", type_=10)
        pois = W.pois_of_reply(W.poi_write_payload_add(b"", record))
        self.assertEqual([(p["name"], p["type"], p["lat"], p["lon"]) for p in pois],
                         [("Hut", 10, 465000000, 75000000)])

    def test_a_route_write_puts_a_traverses_pois_back(self):
        payloads = W.poi_restore_payloads(REPLY)
        self.assertEqual(W.poi_restore_count(REPLY), 2)
        self.assertEqual(len(payloads), 1)
        entries = F.sbem_entries(payloads[0])
        self.assertEqual([e for e, _ in entries], [0x53])          # the entry they came in
        body = entries[0][1]
        self.assertTrue(body.startswith(b"Rub 01\0"))             # reverse of the order read
        self.assertIn(b"Shot 01\0", body)
        self.assertEqual(len(body), len(REPLY) - 14 - 2 * 2)        # both records, whole


@unittest.skipUnless(EMULATED_REPLY.exists(), "the emulated watch's reply is private and not on this machine")
class TheEmulatedWatchsOwnReply(unittest.TestCase):
    def test_it_reads_as_the_two_pois_saved_on_the_watch(self):
        reply = EMULATED_REPLY.read_bytes()
        pois = W.pois_of_reply(reply)
        self.assertEqual([(p["name"], p["type"]) for p in pois], [("Shot 01", 46), ("Rub 01", 39)])
        self.assertEqual((pois[0]["lat"], pois[0]["lon"]), (602716000, 249719000))
        self.assertEqual(W.poi_entry_of(reply), 0x53)
        self.assertEqual(W.poi_restore_count(reply), 2)


class WritingToATraverse(unittest.TestCase):
    def test_a_new_poi_carries_the_traverse_entry_type_and_altitude(self):
        record = W.build_poi_record("Big game hide", 60.28, 24.98, stamp="2016-06-15T13:10:00",
                                    type_=43, altitude=F.ALTITUDE_NONE)
        self.assertTrue(record.endswith(
            (602800000).to_bytes(4, "little") + (249800000).to_bytes(4, "little")
            + (30000).to_bytes(2, "little")))
        payload = W.poi_write_payload_add(b"", record, entry=0x53)
        self.assertEqual(F.sbem_entries(payload), [(0x53, record)])
        self.assertEqual(W.pois_of_reply(payload)[0]["type"], 43)

    def test_an_ambit3_record_is_unchanged(self):
        record = W.build_poi_record("Hut", 46.5, 7.5, stamp="2026-08-06T10:00:00")
        self.assertEqual(record, b"Hut\0\0" + b"2026-08-06T10:00:00\0" + bytes([0, 17, 0, 0, 1])
                         + (465000000).to_bytes(4, "little") + (75000000).to_bytes(4, "little"))
        self.assertEqual(W.poi_write_payload_add(b"", record)[14], 0x55)

    def test_a_type_name_is_resolved_in_the_watchs_own_list(self):
        self.assertEqual(F.waypoint_type_id("Camp"), 2)
        self.assertEqual(F.waypoint_type_id("Camp", ALPHA), 4)
        self.assertEqual(F.waypoint_type_id("Big game", ALPHA), 43)
        self.assertEqual(F.waypoint_type_id("46", ALPHA), 46)
        self.assertEqual(F.waypoint_type_id(None, ALPHA), 26)        # the Traverse's plain "POI"
        self.assertEqual(F.waypoint_type_id(None), 17)
        with self.assertRaises(ValueError):
            F.waypoint_type_id("Big game")                          # not an Ambit3 type
        with self.assertRaises(ValueError):
            F.waypoint_type_id(48, ALPHA)


class BluetoothLink:
    """A watch over Bluetooth as the tools see it: no USB product id, only what it answers.
    Keeps what 0x0b25 was given; refuses to hand out its field list (the tools then go by the
    firmware version)."""

    def __init__(self, model, firmware):
        self.dry_run = False
        self.model, self.firmware = model, firmware
        self.written = []

    def command(self, command, payload=b"", **_):
        if command == W.CMD_DEVICE_INFO:
            return (self.model.encode().ljust(16, b"\0") + b"TESTSERIAL000000"
                    + bytes(self.firmware) + bytes(8))
        if command == W.CMD_POI_COUNT:
            return len(self.written).to_bytes(4, "little")
        if command == W.CMD_POI_WRITE:
            self.written.append(payload)
            return b""
        return b""


class AddingOverBluetooth(unittest.TestCase):
    def setUp(self):
        import ble_pois
        self.ble_pois = ble_pois

    def test_a_traverse_alpha_gets_its_own_form_and_the_picked_type(self):
        watch = BluetoothLink("Loon", (2, 0, 22, 0))
        self.assertTrue(self.ble_pois.add_poi(watch, "Hide", 60.28, 24.98, "Big game")["ok"])
        (entry, record), = F.sbem_entries(watch.written[0])
        self.assertEqual(entry, 0x53)
        self.assertEqual(W.pois_of_reply(watch.written[0]),
                         [{"name": "Hide", "route_name": "", "timestamp": mock_stamp(record),
                           "type": 43, "lat": 602800000, "lon": 249800000, "altitude": 30000}])

    def test_a_traverse_on_its_first_firmware_uses_its_older_entry(self):
        watch = BluetoothLink("Jabiru", (1, 0, 4, 0))
        self.assertTrue(self.ble_pois.add_poi(watch, "Hide", 60.28, 24.98)["ok"])
        (entry, _), = F.sbem_entries(watch.written[0])
        self.assertEqual(entry, 0x52)
        self.assertEqual(W.pois_of_reply(watch.written[0])[0]["type"], 26)      # plain "POI"

    def test_an_elevation_goes_to_a_traverse_and_not_to_an_ambit3(self):
        alpha = BluetoothLink("Loon", (2, 0, 22, 0))
        self.assertTrue(self.ble_pois.add_poi(alpha, "Col", 45.9, 6.9, None, 1461.4)["ok"])
        self.assertEqual(W.pois_of_reply(alpha.written[0])[0]["altitude"], 1461)
        peak = BluetoothLink("Emu", (2, 4, 17, 0))
        self.assertTrue(self.ble_pois.add_poi(peak, "Col", 45.9, 6.9, None, 1461.4)["ok"])
        self.assertIsNone(W.pois_of_reply(peak.written[0])[0]["altitude"])

    def test_an_elevation_that_is_no_height_on_earth_is_sent_as_none(self):
        self.assertEqual(F.poi_altitude(None), 30000)
        self.assertEqual(F.poi_altitude("x"), 30000)
        self.assertEqual(F.poi_altitude(30000), 30000)
        self.assertEqual(F.poi_altitude(-32), -32)
        self.assertEqual(F.poi_altitude(8848.6), 8849)

    def test_an_ambit3_gets_the_picked_type_in_its_own_form(self):
        watch = BluetoothLink("Emu", (2, 4, 17, 0))
        self.assertTrue(self.ble_pois.add_poi(watch, "Hut", 46.5, 7.5, 10)["ok"])
        (entry, _), = F.sbem_entries(watch.written[0])
        self.assertEqual(entry, 0x55)
        poi = W.pois_of_reply(watch.written[0])[0]
        self.assertEqual((poi["type"], poi["altitude"]), (10, None))

    def test_a_type_the_watch_does_not_have_is_refused_before_anything_is_sent(self):
        watch = BluetoothLink("Emu", (2, 4, 17, 0))
        result = self.ble_pois.add_poi(watch, "Hide", 60.28, 24.98, "Big game")
        self.assertFalse(result["ok"])
        self.assertEqual(watch.written, [])


def mock_stamp(record):
    """The timestamp text of a record just built (it is the time of the run)."""
    return record.split(b"\0")[2].decode()


if __name__ == "__main__":
    unittest.main()
