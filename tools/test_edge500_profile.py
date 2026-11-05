#!/usr/bin/env python3
"""Checks tools/edge500_profile.py on FIT files laid out like a real Edge 500's Settings.fit and
Sports/Cycling.fit (same messages, field numbers and base types as fw 3.30, built here so no
device serial is committed). Run: python3 tools/test_edge500_profile.py"""

import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edge500_profile as ep  # noqa: E402


def fit(messages):
    """messages: [(global, [(field, base_type, value)])] -> a FIT file (local type 0 reused)."""
    body = b""
    for g, fields in messages:
        sizes = {0x02: 1, 0x00: 1, 0x84: 2, 0x86: 4}
        codes = {0x02: "B", 0x00: "B", 0x84: "H", 0x86: "I"}
        body += bytes([0x40, 0, 0]) + struct.pack("<HB", g, len(fields))
        body += b"".join(bytes([f, sizes[bt], bt]) for f, bt, _ in fields)
        body += bytes([0x00]) + b"".join(struct.pack("<" + codes[bt], v) for _f, bt, v in fields)
    hdr = bytearray(struct.pack("<BBHI4s", 14, 16, 152, len(body), b".FIT") + b"\0\0")
    struct.pack_into("<H", hdr, 12, ep.fit_crc(hdr[:12]))
    data = bytes(hdr) + body
    return data + struct.pack("<H", ep.fit_crc(data))


FILE_ID = (0, [(0, 0x00, 2), (1, 0x84, 1), (2, 0x84, 1036), (3, 0x86, 0)])
SETTINGS = fit([FILE_ID, (3, [(1, 0x00, 1), (2, 0x02, 60), (3, 0x02, 174), (4, 0x84, 772),
                              (8, 0x02, 0)])])
CYCLING = fit([(0, [(0, 0x00, 3), (1, 0x84, 1), (2, 0x84, 1036), (3, 0x86, 0)]),
               (7, [(1, 0x02, 204), (2, 0x02, 184), (3, 0x84, 243), (5, 0x00, 1), (7, 0x00, 1)])]
              + [(8, [(1, 0x02, v)]) for v in (102, 122, 143, 163, 184, 204)]
              + [(9, [(1, 0x84, v)]) for v in (0, 135, 181, 224, 262, 295, 376, 2000)])


def crc_ok(b):
    return ep.fit_crc(b) == 0


def mount():
    d = tempfile.mkdtemp()
    for rel, data in ((ep.SETTINGS_REL, SETTINGS), (ep.CYCLING_REL, CYCLING)):
        os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
        open(os.path.join(d, rel), "wb").write(data)
    os.makedirs(os.path.join(d, ep.NEWFILES_REL))
    return d


def main():
    assert crc_ok(SETTINGS) and crc_ok(CYCLING)
    m = mount()
    p = ep.read(m)
    assert p["weight"] == 77.2 and p["height"] == 174 and p["age"] == 60 and p["gender"] == 1, p
    assert (p["max_hr"], p["lthr"], p["ftp"]) == (204, 184, 243), p

    s, c, res = ep.build(SETTINGS, CYCLING, {"weight": 72.5, "ftp": 260, "max_hr": 190})
    assert set(res) == {"weight", "ftp", "max_hr"}
    assert len(s) == len(SETTINGS) and len(c) == len(CYCLING), "a patch must not change the size"
    assert crc_ok(s) and crc_ok(c), "file CRC must be recomputed"
    diff = [i for i in range(len(c)) if c[i] != CYCLING[i]]
    assert len(diff) <= 2 + 2 + 2 * 6 + 2 * 7, diff       # values + zones + crc only

    os.environ["SOMMET_BACKUP_DIR"] = tempfile.mkdtemp()
    out = ep.write(m, {"weight": 72.5, "ftp": 260, "max_hr": 190, "age": 60})
    assert "age" not in out["changed"], "unchanged values are not rewritten"
    nf = os.path.join(m, ep.NEWFILES_REL)
    assert sorted(os.listdir(nf)) == ["CYCLING.FIT", "SETTINGS.FIT"], os.listdir(nf)
    assert open(os.path.join(m, ep.SETTINGS_REL), "rb").read() == SETTINGS, "live file untouched"
    os.makedirs(os.path.join(m, "x", "Garmin", "Settings"))
    os.makedirs(os.path.join(m, "x", "Garmin", "Sports"))
    os.replace(os.path.join(nf, "SETTINGS.FIT"), os.path.join(m, "x", ep.SETTINGS_REL))
    os.replace(os.path.join(nf, "CYCLING.FIT"), os.path.join(m, "x", ep.CYCLING_REL))
    q = ep.read(os.path.join(m, "x"))
    assert (q["weight"], q["ftp"], q["max_hr"], q["lthr"]) == (72.5, 260, 190, 184), q
    assert q["hr_zones"] == [95, 114, 133, 152, 171, 190], q["hr_zones"]
    assert q["power_zones"][0] == 0 and q["power_zones"][-1] == 2000, q["power_zones"]
    assert q["power_zones"][1] == round(135 * 260 / 243), q["power_zones"]

    try:
        ep.build(SETTINGS, CYCLING, {"ftp": 5000})
        raise AssertionError("out-of-range FTP must be refused")
    except ValueError:
        pass
    print("edge500_profile: all checks passed")


if __name__ == "__main__":
    main()
