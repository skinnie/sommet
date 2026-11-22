#!/usr/bin/env python3
"""Routes over BLE, phone-driven, post-handshake.

Own file, this project's own "one file per format" convention. Reuses `write_nav.py`'s
own route-building/write pipeline unchanged (`build_routes`, `send_plan`, `read_pois`,
`read_memory_map`, `check_memory_map`, `put_pois_back`) against any Link-compatible
object (`desktop/backend/ble_bridge.BleBridge`) - none of that logic is re-derived here,
only assembled. The one thing skipped is `write_nav.py main()`'s own USB-specific 0x0000
warm-up ping before the real work starts: no real BLE capture this project has (see
HANDOFF.md Milestone 7 items 15-17) shows the phone re-requesting device info after the
handshake - it already has model/serial/fw/hw from the hello - so sending it would be
guessing at a step with no evidence it's needed or even answered post-handshake.

STATUS: the read-only pieces (`read_pois` -> 0x0b24, `read_memory_map` -> 0x0b21,
`existing_routes_as_gpx()` -> 0x0b17) share the exact `command()` shape already proven
working over BLE tonight (0x0306, 0x0b1e, 0x1200 - same driver-path flags/connId/pktNum).
The WRITE itself (`send_plan` -> 0x0b16/0x0b18, then 0x0b04 to commit) is proven too, as of
tonight - confirmed with a real, visible watch change (a settings write flipped the
display light/dark live) and a real route write that landed and read back correctly.

**Real incident, 2026-08-11, fixed at the source**: a first real route write over BLE
wiped two of André's existing routes - `build_routes()` rebuilds the ENTIRE Routes region
from exactly the GPX paths it's given, which is `write_nav.py route`'s own documented
contract (list everything you want kept), but neither this file nor the USB path
(`server.py`/`RouteService::uploadPendingRoute()`) had ever implemented "read what's
already there and keep it" - both only ever sent the one new route. Not a BLE-specific
bug: the exact same code path existed over USB, just never tested against a watch that
already had other real routes on it. Fixed here by having `write_route()` read the
watch's existing routes first (`write_nav.existing_routes_as_gpx()`) and include them
alongside the new one by default - `preserve_existing=False` is the old, dangerous
behavior, kept only as an explicit opt-out for a genuine "replace everything" case.
"""

import pathlib
import sys
import tempfile

from write_nav import (
    backup_pois_before_route_write, build_routes, check_memory_map, existing_routes_as_gpx,
    nav_summary_json, put_pois_back, put_pois_back_safely,
    read_memory_map, read_nav_flash, read_pois, remove_route, send_plan, waypoint_size,
)


def read_nav_summary(link):
    """The BLE path for GET /api/nav - real gap, found live 2026-08-11: routes could be
    written over BLE (write_route(), same night) but the Routes PAGE itself still showed
    nothing/failed, because listing what's already on the watch (RouteService.cpp's own
    `onWatchRoutes`, populated from this exact endpoint's "routes" array) was never wired
    up - only the write side was. Reuses `nav_summary_json()` unchanged against the same
    `read_nav_flash()` `existing_routes_as_gpx()` already uses."""
    return nav_summary_json(read_nav_flash(link))


def write_route(link, gpx_paths, meta_capture=None, preserve_existing=True, serial=None,
                backup_dir=None, progress=None):
    """Real route write flow, transport-agnostic - see this file's own docstring for what
    is and isn't proven over BLE specifically, and for why `preserve_existing` exists at
    all. `gpx_paths`: real GPX file paths, same as `write_nav.py route FILE...` - the
    route(s) being ADDED, not the full set to end up with (this function makes that true
    by reading what's already there first). Returns a summary dict; raises on a real
    protocol failure (a timeout, a short reply) the same way the USB tools already do -
    callers (server.py) catch that the same way they catch every other BLE command()
    failure."""
    # `progress(stage, done, total)` tells the page where the write is: "reading" (what the
    # watch holds now), "sending" (bytes done and in all), "pois" (the POIs going back).
    def tell(stage, done=0, total=0):
        if progress:
            progress(stage, done, total)

    tell("reading")
    pois = read_pois(link, meta_capture)
    check_memory_map(read_memory_map(link))

    temp_paths = []
    try:
        all_gpx_paths = list(gpx_paths)
        # Real bug caught fixing this same evening: a dry-run link.command() returns b""
        # without touching the watch (that IS what dry-run means), so existing_routes_as_gpx()
        # -> read_flash() would see a 0-byte reply and raise "short reply" - turning every
        # SAFE rehearsal into a crash. Nothing is written in a rehearsal regardless of what
        # "already there" would have been, so there is nothing to preserve for one.
        if preserve_existing and not link.dry_run:
            for gpx_text in existing_routes_as_gpx(link):
                with tempfile.NamedTemporaryFile(
                        "w", suffix=".gpx", delete=False) as f:
                    f.write(gpx_text)
                    temp_paths.append(f.name)
            all_gpx_paths = temp_paths + all_gpx_paths

        flash, layout = build_routes([pathlib.Path(p) for p in all_gpx_paths], meta_capture,
                                     waypoint_size(link))      # 54-byte records on a Traverse
        # The commit empties the POI store: a copy goes to disk first, and the put-back
        # retries once from an empty store (write_nav.put_pois_back_safely).
        backup = backup_pois_before_route_write(link, pois, serial, backup_dir)
        try:
            send_plan(link, flash, layout,
                      progress=lambda done, total: tell("sending", done, total))
        except (RuntimeError, TimeoutError, OSError):
            if not link.dry_run:
                put_pois_back_safely(link, pois, backup)
            raise
        # Several 0x0b25 of at most 254 bytes each, never the extended header that corrupted
        # the POI store (write_nav.poi_entry_payloads), then the count is checked.
        tell("pois")
        _back, err = put_pois_back_safely(link, pois, backup)
        if err:
            raise RuntimeError(f"The route was written, but {err}")
    finally:
        for p in temp_paths:
            pathlib.Path(p).unlink(missing_ok=True)

    return {"ok": True, "messages": len(link.sent), "dry_run": link.dry_run,
            "routes_kept": len(temp_paths), "routes_added": len(gpx_paths)}


def delete_route(link, name, serial=None, backup_dir=None):
    """The BLE path for POST /api/routes/remove: write_nav.remove_route() against the bridge,
    the same steps the USB path runs through `write_nav.py delroute`."""
    return remove_route(link, name, serial=serial, backup_dir=backup_dir)


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("gpx", nargs="+")
    ap.add_argument("--write", action="store_true",
                     help="actually emits over the already-connected ble_server.py daemon; "
                          "without this, a rehearsal only (nothing sent)")
    args = ap.parse_args()

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent
                           / "desktop" / "backend"))
    import ble_bridge                                        # noqa: PLC0415

    bridge = ble_bridge.BleBridge()
    status = bridge.status()
    if not status.get("handshake_done"):
        print("no BLE connection with a completed handshake - connect first")
        return 1
    bridge.set_dry_run(not args.write)
    result = write_route(bridge, args.gpx)
    print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
