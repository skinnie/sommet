#!/usr/bin/env python3
"""GPS/GLONASS orbit (AGPS/SGEE) status over BLE, phone-driven, post-handshake.

Own file, this project's own "one file per format" convention. Reuses `sgee.py`'s own
`decode_orbit_head()`/`glonass_status()` unchanged - pure decode functions, transport-
agnostic already. The only thing not reused is `sgee.py`'s `run_status()` itself: it opens
with the same USB-specific `link.command(CMD_DEVICE_INFO, ...)` warm-up ping every other
BLE caller in this project has had to skip (see `ble_routes.py`'s own docstring for why -
no real BLE capture shows the phone re-requesting device info after the handshake, and it
has no proven reply over this transport).
"""

import sys

import ambit_format as F
from sgee import CMD_GPS_ORBIT_HEAD, build_sgee_for_region, decode_orbit_head, glonass_status
from write_nav import read_memory_map, send_plan


def read_status(link):
    """Real 0x0b15 read, no network fetch, nothing written - the same query
    /api/agps/status and /api/agps/update's own offline fallback already make over USB."""
    head = link.command(CMD_GPS_ORBIT_HEAD, b"")
    status = decode_orbit_head(head)
    status["glonass"] = glonass_status(link)
    return status


def write_orbit(link, data_bytes, glonass=False):
    """Real write: `data_bytes` is a raw ephemeris file (no wrapper), same as `sgee.py`'s
    own CLI takes as a path - reuses `build_sgee_for_region()`'s bounds-checked plan
    construction and `send_plan()` unchanged (`commit=False`, matching `sgee.py`'s own
    convention: this region gets no `CMD_NAV_COMMIT`, unlike Routes/Waypoints). The region's
    base and size come from the watch's own memory map (`read_memory_map()`, 0x0b21), for
    GpsSGEE as for GlonassSGEE, as the cable path (`sgee.py`) and SuuntoLink do: its orbit
    write is 0x0b15, 0x0b21, the 0x0b16 chunks, 0x0b18. A watch that does not declare the
    region is refused. Until 2026-11-22 GpsSGEE used the fixed 0x0704E0 here; every model
    declares exactly that (sommet-builds/re-ble-step2/FINDINGS.md, Q1 and Q2), so what goes to
    a watch is the same and only the read of the map is new."""
    import tempfile
    with tempfile.NamedTemporaryFile("wb", suffix=".bin", delete=False) as f:
        f.write(data_bytes)
        path = f.name
    try:
        label = "GlonassSGEE" if glonass else "GpsSGEE"
        declared = read_memory_map(link)
        if label not in declared:
            raise RuntimeError(f"this watch declares no {label} region")
        base, size = declared[label]
        # Every model declares GpsSGEE at 0x0704E0 and GlonassSGEE at 0x1339E0, and those are the
        # addresses the closing hash is worked out for (ambit_format.REGIONS). A watch that
        # says otherwise is one this program has never seen: refused in words, before a byte.
        if base not in F.REGIONS or F.REGIONS[base][0] != label:
            raise RuntimeError(f"this watch keeps {label} at 0x{base:06x}; Sommet only knows "
                               "the place every Suunto firmware seen so far declares")
        flash, layout = build_sgee_for_region(path, base, size, label)
        send_plan(link, flash, layout, commit=False)
    finally:
        import pathlib
        pathlib.Path(path).unlink(missing_ok=True)
    return {"ok": True, "wrote": not link.dry_run, "fetched_bytes": len(data_bytes)}


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    args = ap.parse_args()  # noqa: F841 - no options yet, kept for --help consistency

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent
                           / "desktop" / "backend"))
    import ble_bridge                                        # noqa: PLC0415

    bridge = ble_bridge.BleBridge()
    status = bridge.status()
    if not status.get("handshake_done"):
        print("no BLE connection with a completed handshake - connect first")
        return 1
    bridge.set_dry_run(False)
    print(read_status(bridge))
    return 0


if __name__ == "__main__":
    sys.exit(main())
