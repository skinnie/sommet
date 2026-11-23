#!/usr/bin/env python3
"""Ambit1/2 POI removal, without a watch (André, 2026-11-21: "we have all kinds of captures").

Ground truth: André's Ambit2 SuuntoLink capture (assets/pcap/ambit2_suuntolink_settings_
sportmodes.pcap, git-ignored - skipped where it is absent). SuuntoLink writes this family's POIs
as one whole list: 0x0b04 empties it, one 0x0b05 per waypoint (the 0x0b1b earlier in that
capture belongs to the move download before it, not to the waypoint write). Checked here:
  - the list read back from those 0x0b05 packets, minus one POI, packs to exactly the remaining
    packets SuuntoLink sent (same bytes, same order) - so a removal writes what SuuntoLink would;
  - poi_remove() saves a copy first, keeps route turn-points, and writes the copy back when the
    re-read count is wrong.
The write itself (waypoints-restore) is the path proven on an Ambit1 and an Ambit2 on 2026-08-27
(backup/restore); a removal through it has not been seen on a watch.
"""
import json
import pathlib
import struct
import sys
import tempfile

import legacy_link
from test_legacy_route_send import _cli_packet

ROOT = pathlib.Path(__file__).resolve().parent.parent
CAPTURE = ROOT / "assets" / "pcap" / "ambit2_suuntolink_settings_sportmodes.pcap"


def _waypoint(p):
    """One SuuntoLink 0x0b05 packet -> the waypoint dict the CLI's `waypoints` gives."""
    s = lambda b: b.split(b"\0")[0].decode("latin-1")             # noqa: E731
    return {"name": s(p[4:20]), "route_name": s(p[20:36]),
            "lat": struct.unpack_from("<i", p, 43)[0] / 1e7, "lon": struct.unpack_from("<i", p, 47)[0] / 1e7,
            "type": p[51], "ctime_second": p[36], "ctime_minute": p[37], "ctime_hour": p[38],
            "ctime_day": p[39], "ctime_month": p[40], "ctime_year": struct.unpack_from("<H", p, 41)[0]}


def test_matches_suuntolink(packets):
    wps = [_waypoint(p) for p in packets]
    loose = [w for w in wps if not w["route_name"]]
    assert loose, "capture has no standalone POI"
    target = loose[len(loose) // 2]
    keep, removed = legacy_link.waypoints_without(wps, target["name"], target["lat"], target["lon"])
    assert removed is target and len(keep) == len(wps) - 1
    blob = legacy_link._encode_waypoints(keep)
    want = [p for p in packets if _waypoint(p) is not None and _waypoint(p) != target]
    assert len(want) == len(keep)
    for i, p in enumerate(want):
        got = _cli_packet(blob[48 * i:48 * (i + 1)])
        # byte 53: the CLI writes the name length there, SuuntoLink 0 (see the route test).
        assert got[:53] == p[:53] and got[54:] == p[54:], (i, got.hex(" "), p.hex(" "))


def test_keeps_routes_backs_up_and_restores():
    wps = [{"name": "Home", "lat": 37.0194, "lon": -7.9322, "route_name": "", "type": 0},
           {"name": "Start", "lat": 37.1, "lon": -7.9, "route_name": "Loop", "type": 17},
           {"name": "Home", "lat": 37.1, "lon": -7.9, "route_name": "Loop", "type": 17},
           {"name": "Spring", "lat": 37.2, "lon": -8.0, "route_name": "", "type": 3}]
    # a route turn-point with the same name and place is never taken
    keep, removed = legacy_link.waypoints_without(wps, "Home", 37.1, -7.9)
    assert removed is None and keep == wps
    state = {"list": list(wps), "writes": []}
    legacy_link.waypoints = lambda: {"ok": True, "waypoints": list(state["list"])}

    def restore(lst):
        state["writes"].append(list(lst))
        state["list"] = list(lst)
    legacy_link.waypoints_restore = restore
    with tempfile.TemporaryDirectory() as tmp:
        out = legacy_link.poi_remove("Spring", 37.2, -8.0, tmp)
        assert out["ok"] and out["left"] == 3 and [w["name"] for w in state["list"]] == ["Home", "Start", "Home"]
        assert json.loads(pathlib.Path(out["backup"]).read_text()) == wps
        # the watch keeps the old list: the check fails and the copy goes back
        state["list"] = list(wps)
        legacy_link.waypoints_restore = lambda lst: state["writes"].append(list(lst))
        try:
            legacy_link.poi_remove("Home", 37.0194, -7.9322, tmp)
            raise AssertionError("a failed check was not reported")
        except RuntimeError:
            pass
        assert state["writes"][-1] == wps


if __name__ == "__main__":
    test_keeps_routes_backs_up_and_restores()
    if not CAPTURE.exists():
        print("legacy POI remove: logic OK; capture absent, rest skipped")
        sys.exit(0)
    import ambit_pcap
    pk = [m.payload for m in ambit_pcap.messages(CAPTURE) if m.command == 0x0B05 and not m.incoming and len(m.payload) == 55]
    test_matches_suuntolink(pk)
    print(f"legacy POI remove: {len(pk)} SuuntoLink waypoints, one removed, the rest byte-exact; copy and restore OK")
