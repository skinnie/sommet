#!/usr/bin/env python3
"""tools/swim_styles.py: decoding the learned swim styles from a settings reply, and the
restore write it builds. Uses the Ambit3 Peak schema from assets/ (not in the public
repository, so skipped where missing) and a made-up settings reply, never a real watch's."""

import struct
import unittest
from pathlib import Path

import sbem_schema
import swim_styles as S
import write_nav

HERE = Path(__file__).resolve().parent
LARS = HERE.parent / "assets" / "pcap" / "lars_ambit3peak_13032017.pcap"

PREFIX = bytes.fromhex("1b0044000000")
RECORDS = [(0, [0, 0, 0, 0]), (1, [2445, 2870, 442, 0]), (2, [510, 1715, 1748, 0]),
           (3, [2193, 2166, 207, 0]), (4, [1094, 2873, 876, 1]), (5, [0, 0, 0, 0])]


def entry(eid, data):
    return bytes([eid, len(data)]) + data


def reply(records=RECORDS):
    body = entry(0x01, b"\x00") + entry(0x02, b"\x01")
    for sid, vec in records:
        body += entry(0x40, struct.pack("<B4h", sid, *vec))
    body += entry(0x2C, b"\x00")
    return PREFIX + sbem_schema.MAGIC + body


def entries_of(payload):
    head = payload.find(sbem_schema.MAGIC)
    return payload[:head], list(sbem_schema.entries(payload[head:]))


class SwimStyles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = write_nav.descriptor_for_product_id(0x001B) or sbem_schema.default_descriptor()
        if not path or not Path(path).exists():
            raise unittest.SkipTest("no Ambit3 Peak descriptor on this machine (assets/ is not in git)")
        cls.schema = sbem_schema.load(path)

    def test_layout_and_names(self):
        gid, style, items = S.layout(self.schema)
        self.assertEqual(gid, 0x40)
        self.assertEqual(len(items), 4)
        self.assertEqual(S.style_names(style)[1], "Butterfly")
        self.assertEqual(S.style_names(style)[4], "Freestyle")

    def test_decode(self):
        recs = S.decode(self.schema, reply())
        self.assertEqual([r["style"] for r in recs],
                         ["Other", "Butterfly", "Backstroke", "Breaststroke", "Freestyle", "Drill"])
        self.assertEqual(recs[3]["vectors"], [2193, 2166, 207, 0])

    def test_restore_writes_only_the_swim_records(self):
        wanted = [{"id": 1, "style": "Butterfly", "vectors": [100, 200, 300, 0]}]
        patches, changes, problem = S.plan_restore(self.schema, reply(), wanted)
        self.assertIsNone(problem)
        self.assertEqual(changes, [{"id": 1, "style": "Butterfly",
                                    "from": [2445, 2870, 442, 0], "to": [100, 200, 300, 0]}])
        payload = S.build_payload(self.schema, reply(), patches)
        prefix, ents = entries_of(payload)
        self.assertEqual(prefix, PREFIX[:-1] + b"\x01")        # the write flag
        self.assertEqual([e for e, _ in ents], [0x40] * 6)       # nothing else sent
        self.assertEqual(ents[1][1], struct.pack("<B4h", 1, 100, 200, 300, 0))
        self.assertEqual(ents[3][1], struct.pack("<B4h", 3, 2193, 2166, 207, 0))

    def test_same_values_change_nothing(self):
        wanted = [{"id": sid, "vectors": vec} for sid, vec in RECORDS]
        patches, changes, problem = S.plan_restore(self.schema, reply(), wanted)
        self.assertEqual((patches, changes, problem), ([], [], None))

    def test_refusals(self):
        bad = [
            ([{"id": 9, "vectors": [0, 0, 0, 0]}], "not on this watch"),
            ([{"id": 1, "vectors": [1, 2, 3]}], "this watch stores 4"),
            ([{"id": 1, "vectors": [40000, 0, 0, 0]}], "not a value the watch can store"),
        ]
        for wanted, why in bad:
            patches, changes, problem = S.plan_restore(self.schema, reply(), wanted)
            self.assertEqual(patches, [])
            self.assertIn(why, problem)
        _p, _c, problem = S.plan_restore(self.schema, PREFIX + sbem_schema.MAGIC
                                         + entry(0x01, b"\x00"), [{"id": 1, "vectors": [0] * 4}])
        self.assertIn("no swim style records", problem)

    def test_file_round_trip(self):
        info = {"watch": {"serial": "X"}, "read": "2026-10-30T06:00:00",
                "styles": S.decode(self.schema, reply())}
        saved = S.saved_file(info)
        self.assertEqual(saved["kind"], S.FILE_KIND)
        patches, changes, problem = S.plan_restore(self.schema, reply(), saved["styles"])
        self.assertEqual((patches, changes, problem), ([], [], None))

    @unittest.skipUnless(LARS.exists(), "no Lars capture on this machine")
    def test_real_moveslink_reply(self):
        from ambit_pcap import messages
        replies = [m for m in messages(LARS) if m.incoming and m.command == 0x1100
                   and sbem_schema.MAGIC in m.payload]
        recs = S.decode(self.schema, replies[0].payload)
        self.assertEqual(len(recs), 6)
        self.assertEqual(recs[3], {"id": 3, "style": "Breaststroke", "vectors": [364, 4098, 621, 0]})


if __name__ == "__main__":
    unittest.main()
