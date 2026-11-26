#!/usr/bin/env python3
"""Checks for kailash_pois.py. No watch needed; every coordinate here is made up.

Replies are built in the shape the Kailash 2.0.5 program gives (sommet-builds/kailash-poi-re/
FINDINGS.md): 0x0b02 a u32 count; 0x0b24 [u32 next cursor][u16 last] "SBEM0102" then one 0x46
entry per record, newest first, later pages without the magic.
"""
import struct
import sys

import ambit_format as F
import kailash_pois as K
import sbem_schema


def entry(eid, data):
    return bytes([eid, len(data)]) + data


BOOKMARK = F.build_poi_record("Bookmark", 45.1, 6.2, "2026-11-26T09:01:02", type_=6,
                              sub_type=0, type_index=1, flags=1)
HOME = F.build_poi_record("Home", 45.0, 6.0, "2026-11-20T18:00:00", type_=0, sub_type=1,
                          type_index=1, flags=1)


class FakeWatch:
    def __init__(self, count, pages):
        self.count, self.pages, self.sent = count, list(pages), []

    def command(self, cmd, payload=b"", *a, **k):
        self.sent.append((cmd, payload))
        if cmd == K.CMD_POI_COUNT:
            return struct.pack("<I", self.count)
        if cmd == K.CMD_POI_READ:
            return self.pages.pop(0)
        raise AssertionError(f"unexpected command 0x{cmd:04x}")


def main():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    # 1. one page, bookmark newest then home
    page = struct.pack("<IH", 0, 1) + sbem_schema.MAGIC + entry(0x46, BOOKMARK) + entry(0x46, HOME)
    w = FakeWatch(2, [page])
    out = K.read(w)
    check("count", out["count"] == 2)
    check("saved", out["saved"] == {"name": "Bookmark", "type": 6, "subtype": 0, "type_index": 1,
                                    "flags": 1, "time": "2026-11-26T09:01:02"})
    check("home", out["home"]["name"] == "Home" and out["home"]["subtype"] == 1)
    check("no coordinates by default", "lat" not in out["saved"])
    check("asked count then one page", [c for c, _p in w.sent] == [0x0B02, 0x0B24]
          and w.sent[1][1] == b"\0\0\0\0")
    with_coords = K.read(FakeWatch(2, [page]), show_coordinates=True)
    check("coordinates on request", abs(with_coords["saved"]["lat"] - 45.1) < 1e-6
          and abs(with_coords["home"]["lon"] - 6.0) < 1e-6)

    # 2. an empty list: count 0 and no 0x0b24 at all (it would answer with an error)
    w = FakeWatch(0, [])
    out = K.read(w)
    check("empty", out["records"] == [] and out["saved"] is None and out["home"] is None)
    check("no list read when empty", [c for c, _p in w.sent] == [0x0B02])

    # 3. two pages, the second without the magic; the cursor travels in the request
    p1 = struct.pack("<IH", 0x30, 0) + sbem_schema.MAGIC + entry(0x46, BOOKMARK)
    p2 = struct.pack("<IH", 0, 1) + entry(0x46, HOME)
    w = FakeWatch(2, [p1, p2])
    out = K.read(w)
    check("paged", [r["name"] for r in out["records"]] == ["Bookmark", "Home"])
    check("cursor asked", w.sent[2][1] == struct.pack("<HH", 0x30, 0))

    # 4. an entry with another id is refused, not read as a POI
    bad = struct.pack("<IH", 0, 1) + sbem_schema.MAGIC + entry(0x55, HOME)
    try:
        K.read(FakeWatch(1, [bad]))
        check("foreign entry refused", False)
    except ValueError:
        pass

    # 4b. a 2-byte count is read too
    class Short(FakeWatch):
        def command(self, cmd, payload=b"", *a, **k):
            r = super().command(cmd, payload)
            return r[:2] if cmd == K.CMD_POI_COUNT else r
    check("2-byte count", K.read(Short(2, [page]))["count"] == 2)

    # 5. the guard lets only the count and the read through
    g = K._Guard(FakeWatch(0, []))
    for cmd in (0x0B04, 0x0B25, 0x0B16, 0x0B18, 0x0B17, 0x1200):
        try:
            g.command(cmd, b"")
            check(f"0x{cmd:04x} refused", False)
        except RuntimeError:
            pass

    # 6. the WayPoint group comes from the Kailash's own descriptor (assets/, skipped without)
    import glob
    import pathlib
    found = sorted(glob.glob(str(pathlib.Path(__file__).resolve().parent.parent / "assets" / "**"
                                 / "descr+*+2.0.5"), recursive=True))
    if found:
        check("group from descriptor", K.waypoint_group(sbem_schema.load(found[0])) == 0x46)

    if fails:
        print("FAILED:", ", ".join(fails))
        return 1
    print("kailash_pois: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
