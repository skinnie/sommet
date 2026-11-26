#!/usr/bin/env python3
"""Removing a route from an Ambit1/2, without a watch.

No capture shows SuuntoLink removing a route. Ground truth is the region and the waypoint list
SuuntoLink wrote with three routes (assets/pcap/2026-08-23-ambit1-suuntolink/zzambit1full.pcap,
git-ignored - skipped where absent) and the writers route_send already uses. Checked here:
  - removing each of the three routes keeps the other two byte for byte: info entries (only a
    start index moves down) and point blocks, in their stored order; head counts and CRC;
  - remove(append(region, [route]), route) gives the captured region back exactly;
  - the waypoint list without a route's waypoints packs to exactly the rest of the 0x0b05
    packets SuuntoLink sent;
  - the last route, an unknown name and overlapping point blocks are refused;
  - route_remove(): backups first, region then list, read-back check, copies back on failure;
  - the bytes for the Android parity fixture (android/src/services/__tests__/LegacyRoute.test.ts).
Not seen on a real watch.
"""
import json
import pathlib
import struct
import sys
import tempfile

import legacy_link
import legacy_route
from test_legacy_route_send import CAPTURE, _capture, _cli_packet
from test_legacy_poi_remove import _waypoint


def test_remove_each(region):
    old = legacy_route.parse(region)
    count = len(old["routes"])
    infos = [region[32 + 48 * i:32 + 48 * (i + 1)] for i in range(count)]
    po = legacy_route.POINTS_OFFSET
    for k, gone in enumerate(old["routes"]):
        blob, removed = legacy_route.remove(region, gone["name"].strip())
        assert removed == {"name": gone["name"], "point_count": gone["point_count"]}
        new = legacy_route.parse(blob)
        keep = [r for i, r in enumerate(old["routes"]) if i != k]
        assert [r["name"] for r in new["routes"]] == [r["name"] for r in keep]
        assert new["routepoint_count"] == old["routepoint_count"] - gone["point_count"]
        for i, info in zip([i for i in range(count) if i != k], [blob[32 + 48 * j:80 + 48 * j] for j in range(count - 1)]):
            assert info[:16] == infos[i][:16] and info[20:] == infos[i][20:]     # all but start
            s_old = struct.unpack_from("<I", infos[i], 16)[0]
            s_new = struct.unpack_from("<I", info, 16)[0]
            n = struct.unpack_from("<H", info, 20)[0]
            assert blob[po + 8 * s_new:po + 8 * (s_new + n)] == region[po + 8 * s_old:po + 8 * (s_old + n)]
        used = blob[32:32 + 48 * (count - 1)] + blob[po:]
        assert struct.unpack_from("<H", blob, 12)[0] == legacy_route.crc16_ccitt_false(used)
        assert blob[32 + 48 * (count - 1):po] == bytes(po - 32 - 48 * (count - 1))
    # SuuntoLink's own checksum agrees with the one this module computes, so the head rule holds.
    n = struct.unpack_from("<I", region, 8)[0]
    assert struct.unpack_from("<H", region, 12)[0] == legacy_route.crc16_ccitt_false(
        region[32:32 + 48 * count] + region[po:po + 8 * n])


def test_append_then_remove(region):
    pts = [(48.86 + 0.0004 * i, 2.29 + 0.0006 * i) for i in range(30)]
    more = legacy_route.append(region, [{"name": "Sommet test", "points": pts, "distance_m": 2500}])
    back, removed = legacy_route.remove(more, "Sommet test")
    assert back == region and removed["point_count"] == 30


def test_refusals():
    one = legacy_route.append(None, [{"name": "Only", "points": [(1.0, 2.0), (1.001, 2.001)], "distance_m": 150}])
    for name, region in (("Only", one), ("Other", one), ("x", None)):
        try:
            legacy_route.remove(region, name)
            raise AssertionError(f"{name!r} removed")
        except ValueError:
            pass
    two = legacy_route.append(one, [{"name": "Two", "points": [(1.0, 2.0)] * 3, "distance_m": 1}])
    bad = bytearray(two)
    struct.pack_into("<I", bad, 32 + 48 + 16, 0)          # Two's block now overlaps Only's
    try:
        legacy_route.remove(bytes(bad), "Only")
        raise AssertionError("overlapping blocks accepted")
    except ValueError:
        pass


def test_waypoints_match_suuntolink(packets):
    # The second list SuuntoLink wrote (after its last 0x0b04) is the watch's final list: 9 POIs,
    # 9 waypoints of Grand Tour HDF, 2 of Gare du Nord to, 1 of 88 Av. Raymond.
    final = packets[-21:]
    wps = [_waypoint(p) for p in final]
    keep, dropped = legacy_link.waypoints_without_route(wps, "Gare du Nord to", ["Grand Tour HDF "])
    assert dropped == 2 and len(keep) == len(wps) - 2
    want = [p for p in final if p[20:36].split(b"\0")[0] != b"Gare du Nord to"]
    blob = legacy_link._encode_waypoints(keep)
    for i, p in enumerate(want):
        got = _cli_packet(blob[48 * i:48 * (i + 1)])
        assert got[:53] == p[:53] and got[54:] == p[54:], i       # byte 53: see the route test
    # Another route of the same name is still on the watch: its waypoints stay.
    assert legacy_link.waypoints_without_route(wps, "Gare du Nord to", ["Gare du Nord to"]) == (wps, 0)


def test_route_remove(region, packets):
    wps = [_waypoint(p) for p in packets[-21:]]
    state = {"region": region, "list": list(wps), "writes": []}
    legacy_link.read_route_region = lambda: state["region"]
    legacy_link.waypoints = lambda: {"ok": True, "waypoints": list(state["list"])}
    legacy_link.route_head = lambda: {"ok": True, "route_count": struct.unpack_from("<H", state["region"], 4)[0]}

    def put_region(data):
        state["writes"].append("region")
        state["region"] = data

    def put_list(lst):
        state["writes"].append("list")
        state["list"] = list(lst)
    legacy_link.restore_route_region = put_region
    legacy_link.waypoints_restore = put_list
    with tempfile.TemporaryDirectory() as tmp:
        dry = legacy_link.route_remove("Gare du Nord to", backup_dir=tmp)
        assert dry == {"ok": True, "wrote": False, "removed": "Gare du Nord to", "routes_left": 2,
                       "waypoints_removed": 2} and not state["writes"]
        out = legacy_link.route_remove("Gare du Nord to", write=True, backup_dir=tmp)
        assert out["wrote"] and state["writes"] == ["region", "list"]
        assert pathlib.Path(out["backup"]).read_bytes() == region
        saved = json.loads(next(pathlib.Path(tmp).glob("legacy-waypoints-*.json")).read_text())
        assert saved["waypoints"] == wps
        assert [r["name"].strip() for r in legacy_route.parse(state["region"])["routes"]] == \
            ["Grand Tour HDF", "88 Av. Raymond"]
        assert len(state["list"]) == 19
        # The watch keeps the old list: the check fails and both copies go back.
        state.update(region=region, list=list(wps), writes=[])
        legacy_link.waypoints_restore = lambda lst: state["writes"].append("list")
        try:
            legacy_link.route_remove("88 Av. Raymond", write=True, backup_dir=tmp)
            raise AssertionError("a failed check was not reported")
        except RuntimeError:
            pass
        assert state["writes"] == ["region", "list", "region", "list"] and state["region"] == region


def test_android_parity():
    # The fixture of android/src/services/__tests__/LegacyRoute.test.ts, in Python.
    import math
    a = {"name": "Cap Roux loop", "distance_m": 5210,
         "points": [(43.4521 + 0.0011 * math.sin(i / 3), 6.8803 + 0.0013 * i) for i in range(40)]}
    b = {"name": "Dramont", "distance_m": 2400,
         "points": [(43.41 + 0.0007 * i, 6.85 - 0.0009 * math.cos(i / 2)) for i in range(25)]}

    def fnv(data):
        h = 0x811C9DC5
        for x in data:
            h = ((h ^ x) * 0x01000193) & 0xFFFFFFFF
        return h
    both = legacy_route.append(legacy_route.append(None, [a]), [b])
    assert len(both) == 2952 and fnv(both) == 0x2061018F
    gone, _ = legacy_route.remove(both, "Cap Roux loop")
    assert len(gone) == 2632 and fnv(gone) == 0x09DF91C2, hex(fnv(gone))


if __name__ == "__main__":
    test_refusals()
    test_android_parity()
    if not CAPTURE.exists():
        print("legacy route remove: refusals and parity OK; capture absent, rest skipped")
        sys.exit(0)
    region, packets = _capture()
    test_remove_each(region)
    test_append_then_remove(region)
    test_waypoints_match_suuntolink(packets)
    test_route_remove(region, packets)
    print("legacy route remove: each captured route removed with the others byte for byte, "
          "SuuntoLink's waypoints kept exactly, writer and refusals OK")
