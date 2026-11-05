#!/usr/bin/env python3
"""Checks tools/edge500_workout.py: schema workout -> FIT workout for the Edge 500, decoded back
with the project's own FIT reader conventions. Run: python3 tools/test_edge500_workout.py"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edge500_workout as ew  # noqa: E402

WK = {"name": "4x4 VO2max", "steps": [
    {"type": {"typeName": "warmup"}, "duration": {"durationName": "time", "value": 600},
     "target": {"targetName": "hr", "valueRange": {"min": 120, "max": 140}}},
    {"type": {"typeName": "repeatStart", "value": 4}},
    {"type": {"typeName": "work"}, "duration": {"durationName": "time", "value": 240},
     "target": {"targetName": "power", "valueRange": {"min": 300, "max": 280}}},
    {"type": {"typeName": "recovery"}, "duration": {"durationName": "distance", "value": 1500},
     "target": {"targetName": "cadence", "valueRange": {"min": 85, "max": 95}}},
    {"type": {"typeName": "repeatEnd"}},
    {"type": {"typeName": "cooldown"}, "duration": {"durationName": "lap"}},
]}


def decode(b):
    """{global: [ {field: value} ]} for the small fixed layout encode() writes."""
    assert ew.fit_crc(b) == 0 and ew.fit_crc(b[:14]) == 0
    p, end, defs, out = 14, 14 + struct.unpack_from("<I", b, 4)[0], {}, {}
    codes = {0x00: "B", 0x84: "H", 0x86: "I", 0x8C: "I", 0x07: None}
    while p < end:
        h = b[p]; p += 1
        if h & 0x40:
            g, n = struct.unpack_from("<HB", b, p + 2); p += 5
            defs[h & 15] = (g, [tuple(b[p + 3 * i:p + 3 * i + 3]) for i in range(n)]); p += 3 * n
            continue
        g, fl = defs[h & 15]; rec = {}
        for f, sz, bt in fl:
            c = codes[bt]
            rec[f] = b[p:p + sz].rstrip(b"\0").decode() if c is None else struct.unpack_from("<" + c, b, p)[0]
            p += sz
        out.setdefault(g, []).append(rec)
    return out


def main():
    m = decode(ew.encode(WK))
    assert m[0][0][0] == 5, "file type workout"
    w = m[26][0]
    assert w[4] == 2 and w[8] == "4x4 VO2max" and w[6] == 5, w
    s = m[27]
    assert [x[254] for x in s] == [0, 1, 2, 3, 4]
    assert (s[0][1], s[0][2], s[0][3], s[0][5], s[0][6], s[0][7]) == (0, 600000, 1, 220, 240, 2)
    assert (s[1][3], s[1][5], s[1][6], s[1][7]) == (4, 1280, 1300, 0), s[1]      # swapped range fixed
    assert (s[2][1], s[2][2], s[2][3], s[2][5], s[2][7]) == (1, 150000, 3, 85, 1), s[2]
    assert (s[3][1], s[3][2], s[3][4]) == (6, 1, 4), s[3]                         # repeat steps 1..2 x4
    assert (s[4][1], s[4][3], s[4][7]) == (5, 2, 3), s[4]                         # open cooldown
    for bad in ({"steps": []}, {"steps": [{"type": {"typeName": "repeatStart", "value": 2}}]}):
        try:
            ew.encode(bad)
            raise AssertionError("must refuse %r" % bad)
        except ValueError:
            pass
    print("edge500_workout: all checks passed")


if __name__ == "__main__":
    main()
