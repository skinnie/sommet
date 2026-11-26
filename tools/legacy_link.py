#!/usr/bin/env python3
"""Device info / personal settings / training logs for the legacy Ambit1/2 ("Bluebird")
family - the PMEM 2.0 protocol that predates SBEM (write_nav.py's own protocol).

2026-08-22, real hardware (André's Ambit1, serial 1614984607001600): CMD_DEVICE_INFO/
CMD_STATUS (0x0000/0x0306) turned out common to the whole family - write_nav.py's own
PRODUCT_IDS now covers Ambit1/2, and device_info.py/list_watches.py already work for them
unmodified. But the higher-level SBEM object queries (settings 0x1100, memory map 0x0b21,
POIs 0x0b24) come back EMPTY, not an error, confirming Ambit1/2 predate SBEM and speak a
different, older command set over the same transport. This file is the "one file per format"
tool (see the project's own convention: a new watch feature/format gets its own file
importing from write_nav.py, never a new elif branch there) for that older format - except
the format itself isn't reimplemented here, it's wrapped: see tools/vendor/ambit_legacy_cli/
and tools/vendor/openambit_libambit/README.md for why (a real, hardware-proven implementation
already exists - openambit's libambit - so this shells out to a small compiled CLI built on
top of it, the one piece of C in an otherwise all-Python tools/ directory).

Started read-only; two real writes added same day (2026-08-22): GPS orbit data
(gps-orbit-write, already proven live) and POI/waypoint add+clear (openambit's own
libambit_navigation_write, exercised here for the first time in this project - add, read
back, confirm present, same discipline as every other real write in this project). Personal
SETTINGS write (weight/HR/etc, not waypoints) is real too (SuuntoLink does it) but its wire
format has never been captured in this project (see the sommet-ambit12-settings-write
memory), so there's nothing safe to send for THAT specific piece yet.

    ./tools/legacy_link.py device-info
    ./tools/legacy_link.py settings
    ./tools/legacy_link.py logs OUTDIR
    ./tools/legacy_link.py poi-add NAME LAT LON
    ./tools/legacy_link.py poi-clear
    ./tools/legacy_link.py gps-orbit-status
    ./tools/legacy_link.py gps-orbit-write FILE
    ./tools/legacy_link.py route-send ROUTE.gpx... [--write]
    ./tools/legacy_link.py route-remove NAME [--write]
    ./tools/legacy_link.py sport-mode-edit MODE '{"autolapM": 1000}' [--write]
    ./tools/legacy_link.py sport-mode-displays MODE '[{"op": "add", "type": "1-row"}]' [--write]
    ./tools/legacy_link.py sport-mode-create NAME ACTIVITY --variant Duck [--write]
    ./tools/legacy_link.py sport-mode-delete NAME --variant Duck [--multisport] [--write]
    ./tools/legacy_link.py sport-mode-menu --variant Duck
    ./tools/legacy_link.py sport-mode-mirror SOURCE-REGION.bin [--write]
    ./tools/legacy_link.py pois-add POIS.json [--write]
"""

import json
import os
import pathlib
import struct
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent


def _binary_path():
    """The compiled ambit_legacy_cli binary - dev tree path, or PyInstaller's frozen
    _MEIPASS/tools/vendor/... (bundled the same way tools/ itself is - see
    desktop/backend/server.py's own FROZEN branch, tools/packaging/sommet_backend.spec)."""
    candidates = [
        HERE / "vendor" / "ambit_legacy_cli" / "ambit_legacy_cli",
        HERE / "vendor" / "ambit_legacy_cli" / "ambit_legacy_cli.exe",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def run(args, timeout=120):
    """Runs the compiled CLI, returns its parsed JSON stdout object. Raises RuntimeError
    with a clear, actionable message (never a build isn't findable) if it's missing or the
    process failed to produce parseable JSON - the same "report, don't mask" discipline as
    every other tools/*.py CLI in this project.

    timeout: seconds allowed for the child. 120 suits the small single-shot commands
    (device-info/settings/poi-*), but `logs` reads every stored activity off the watch and
    scales with their count - a real Ambit2 with 32 activities blew past the old fixed 120s,
    which fired after only ~16 had been read, truncating the sync (the wrapper then raised
    and the app's /api/activities 502'd entirely). Caught 2026-08-26; the 2026-08-22 Ambit1
    test had 0 logs, so this slow path was never exercised. logs() passes a generous value."""
    binary = _binary_path()
    if binary is None:
        raise RuntimeError(
            "ambit_legacy_cli is not built - run tools/vendor/ambit_legacy_cli/build.sh "
            "once (needs cmake + a C compiler + libusb-1.0; see that script's own header "
            "comment). Ambit1/2 settings/logs need it; device identity/battery don't - "
            "those already work via write_nav.py/device_info.py.")
    # Real bug, caught 2026-08-23 with an Ambit1 AND an Ambit3 Sport connected at once: the
    # CLI used to always open whichever Suunto device it saw first, silently ignoring which
    # watch the app had selected - settings for the selected Ambit1 came back as the Ambit3
    # Sport's data instead. AMBIT_PRODUCT_ID is the same env var run_tool() already sets for
    # every other tool (write_nav.py etc) from the app's selected device, so this needs no new
    # plumbing - just honor it here too, via the CLI's own --device.
    device_args = []
    env_pid = os.environ.get("AMBIT_PRODUCT_ID")
    if env_pid:
        device_args = ["--device", env_pid]
    # bytes, not text=True. libambit prints a few debug lines of its own ahead of the JSON,
    # and on this family those can carry ISO-8859 text (watch/waypoint names) - decoding the
    # pipe as UTF-8 raised a real UnicodeDecodeError that surfaced as the Watch settings page
    # hanging forever on "Reading settings off the watch...". The JSON payload itself is pure
    # ASCII by construction (ambit_legacy_cli.c's json_str escapes every byte >= 0x80), so
    # only this surrounding chatter needs the tolerant decode.
    # Cap libambit's per-report read budget. Its 20 s default is spent in full on every
    # command the watch simply never answers (unsupported opcodes probed while identifying
    # the device), which put a single device-info call at 167 s on macOS against an Ambit2 -
    # past the `timeout` below, so the page just failed. The watch replies in milliseconds
    # when it replies at all, so a shorter budget only shortens the waiting-for-silence.
    env = os.environ.copy()
    env.setdefault("AMBIT_READ_TIMEOUT_MS", "3000")
    proc = subprocess.run([str(binary), *device_args, *args],
                           capture_output=True, timeout=timeout, env=env)
    proc_stdout = proc.stdout.decode("utf-8", "replace")
    proc_stderr = proc.stderr.decode("utf-8", "replace")
    try:
        # libambit itself prints a couple of unconditional debug lines to stdout ahead of
        # the real payload (vendored, unmodified - see openambit_libambit/README.md), and
        # the JSON payload can itself contain embedded newlines (the `logs` index is
        # pretty-printed) - splitlines()[-1] would grab a fragment. ambit_legacy_cli.c
        # prints a "@@JSON@@" marker on its own line right before the real payload instead.
        return json.loads(proc_stdout.rsplit("@@JSON@@\n", 1)[-1].strip())
    except Exception as exc:  # noqa: BLE001 - report the real process output, never mask
        raise RuntimeError(
            f"ambit_legacy_cli {' '.join(args)} produced no parseable JSON "
            f"(exit {proc.returncode}): {exc}\nstdout: {proc_stdout!r}\nstderr: {proc_stderr!r}")


def device_info():
    return run(["device-info"])


def settings():
    return run(["settings"])


def waypoints():
    """Fast waypoint/POI read: the CLI's `waypoints` does ONLY libambit_navigation_read
    (0x0b02 count + 0x0b03 per-waypoint, ~55B each - SuuntoLink's own structured sequence,
    confirmed in André's capture), SKIPPING the slow personal_settings_get PMEM region read.
    Same waypoints[] shape as settings(); use this for /api/pois and /api/nav, which only need
    waypoints/routes, so a legacy POI/route read is single-digit seconds instead of ~30s on
    macOS. settings() stays for the Watch Settings page, which needs the personal block too."""
    return run(["waypoints"])


def logs(outdir):
    # A full watch of activities is read one move at a time over slow USB; a real 32-move
    # Ambit2 outran the old 120s, so allow generously (30 min) rather than truncate a sync.
    return run(["logs", str(outdir)], timeout=1800)


def poi_add(name, lat, lon):
    return run(["poi-add", name, str(lat), str(lon)])


def poi_clear():
    return run(["poi-clear"])


def flash_read(address, length):
    """Raw region read over 0x0b17 - READ ONLY. Returns bytes.

    Goes through the C CLI because this family answers libambit's transport and not the
    Ambit3 dialect write_nav.py speaks: every command through that path, device_info
    included, comes back empty on a Bluebird (checked live, 2026-08-27).
    """
    info = run(["flash-read", str(int(address)), str(int(length))], timeout=600)
    if not info.get("ok"):
        raise RuntimeError(info.get("error", "flash-read failed"))
    return bytes.fromhex(info["hex"])


def routes():
    """The watch's REAL routes, off the route region - full point tracks, not the A/B
    waypoint markers /api/nav had to infer them from before.

    Two reads: the 32-byte head first, because it carries route_count and routepoint_count and
    those decide how much there is to fetch - the region is 16 KB and reading all of it when a
    watch holds one short route would be slow for nothing.
    """
    import legacy_route                                      # noqa: PLC0415

    head = flash_read(legacy_route.ROUTE_REGION_ADDR, legacy_route.HEAD_LEN)
    magic = int.from_bytes(head[:2], "little")
    if magic != legacy_route.HEAD_MAGIC:
        return {"ok": True, "routes": [], "note": f"no route region (magic 0x{magic:04X})"}
    point_count = int.from_bytes(head[8:12], "little")
    total = legacy_route.POINTS_OFFSET + legacy_route.POINT_LEN * point_count
    blob = flash_read(legacy_route.ROUTE_REGION_ADDR, total)
    parsed = legacy_route.parse(blob)
    return {"ok": True, "routes": parsed["routes"], "routepoint_count": parsed["routepoint_count"]}


def route_head():
    """Cheap route-region probe: read ONLY the 32-byte head (~1s, one flash read) and return
    route_count/point_count/checksum, so the backend can decide an on-disk route-track cache
    hit/miss WITHOUT the ~13.5KB full point read. The head's checksum is crc16 over the info
    entries + the track points (legacy_route), so it changes on any route or point edit - a
    correct, cheap cache key. head layout <HBBHHIH...>: route_count@4, point_count@8, checksum@12."""
    import legacy_route                                      # noqa: PLC0415
    head = flash_read(legacy_route.ROUTE_REGION_ADDR, legacy_route.HEAD_LEN)
    magic = int.from_bytes(head[:2], "little")
    if magic != legacy_route.HEAD_MAGIC:
        return {"ok": True, "empty": True, "note": f"no route region (magic 0x{magic:04X})"}
    return {"ok": True,
            "route_count": int.from_bytes(head[4:6], "little"),
            "point_count": int.from_bytes(head[8:12], "little"),
            "checksum": int.from_bytes(head[12:14], "little")}


def settings_write(key, value, dry_run=False):
    """Writes ONE personal-settings field on an Ambit1.

    Command 0x0b01, solved from André's own capture: SuuntoLink sends back the same 132-byte
    structure the 0x0b00 read returns, with the changed field patched in place. The CLI does
    the read-modify-write, so every field the caller did not name is preserved exactly."""
    args = ["settings-write", str(key), str(int(value))]
    if dry_run:
        args.append("--dry-run")
    return run(args)


def flash_write(address, data, extra):
    """Raw flash-region WRITE: chunked 0x0b16 + the 0x0b18 commit tail (extra) that makes it
    persist. `data` is bytes, `extra` the region's tail constant (routes 0xFFFFFA1A, sport
    modes 0xFFFFFFFF). The C CLL picks the per-device chunk size (Ambit1 512 / Ambit2 1024).
    Destructive - the caller must have a backup and confirm the device first."""
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tf:
        tf.write(data)
        path = tf.name
    try:
        info = run(["flash-write", str(int(address)), path, str(int(extra))], timeout=600)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if not info.get("ok"):
        raise RuntimeError(info.get("error", "flash-write failed"))
    return info


def _encode_waypoints(waypoints):
    """Pack a waypoint list into the flat 48-byte-per-record blob the CLI's waypoints-restore
    expects (same layout as the Android app's AmbitLegacyNav.encodeWaypoints): name[16],
    route_name[16], lat i32 (deg*1e7), lon i32, type u8, ctime year u16/month/day/hour/min/sec.
    Names are latin1 (this family's encoding), capped at 15 chars + a NUL within the 16 field."""
    out = bytearray()
    for w in waypoints:
        rec = bytearray(48)

        def _put(dst_off, text):
            b = str(text or "").encode("latin-1", "replace")[:15]
            rec[dst_off:dst_off + len(b)] = b

        _put(0, w.get("name"))
        _put(16, w.get("route_name"))
        # `settings` gives lat/lon as float degrees; accept lat_e7/lon_e7 ints too.
        lat_e7 = int(round(float(w["lat"]) * 1e7)) if "lat" in w else int(w.get("lat_e7", 0))
        lon_e7 = int(round(float(w["lon"]) * 1e7)) if "lon" in w else int(w.get("lon_e7", 0))
        struct.pack_into("<ii", rec, 32, lat_e7, lon_e7)
        rec[40] = int(w.get("type", 0)) & 0xFF
        struct.pack_into("<H", rec, 41, int(w.get("ctime_year", 0)) & 0xFFFF)
        rec[43] = int(w.get("ctime_month", 0)) & 0xFF
        rec[44] = int(w.get("ctime_day", 0)) & 0xFF
        rec[45] = int(w.get("ctime_hour", 0)) & 0xFF
        rec[46] = int(w.get("ctime_minute", 0)) & 0xFF
        rec[47] = int(w.get("ctime_second", 0)) & 0xFF
        out += rec
    return bytes(out)


def waypoints_restore(waypoints):
    """Replace the whole on-device waypoint (POI) list with `waypoints` (a list of dicts, as the
    `settings` reply lists them). Command path: nav_memory_delete + one
    waypoint_write per point (no 0x0b1b first since 2026-11-23: it marks moves as synced), type kept RAW so a backup round-trips byte-exact. 0x0b04 clears
    only the waypoint list (not the route flash region), so this never endangers routes."""
    blob = _encode_waypoints(waypoints)
    with tempfile.NamedTemporaryFile(suffix=".wpts", delete=False) as tf:
        tf.write(blob)
        path = tf.name
    try:
        info = run(["waypoints-restore", path], timeout=600)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if not info.get("ok"):
        raise RuntimeError(info.get("error", "waypoints-restore failed"))
    return info


def waypoints_without(waypoints, name, lat, lon, tol=1e-5):
    """The waypoint list with ONE POI taken out: the first standalone waypoint (no route, or a
    route name only it carries - the same rule as the server's _legacy_route_groups) whose name
    and position match. Route turn-points are never removed here. Returns (keep, removed);
    removed is None when nothing matched."""
    counts = {}
    for w in waypoints:
        rn = str(w.get("route_name", "")).strip()
        if rn:
            counts[rn] = counts.get(rn, 0) + 1
    keep, removed = [], None
    for w in waypoints:
        rn = str(w.get("route_name", "")).strip()
        standalone = not rn or counts.get(rn, 0) < 2
        if (removed is None and standalone and str(w.get("name", "")) == name
                and abs(float(w.get("lat", 0.0)) - lat) < tol
                and abs(float(w.get("lon", 0.0)) - lon) < tol):
            removed = w
            continue
        keep.append(w)
    return keep, removed


def poi_remove(name, lat, lon, backup_dir):
    """Remove one POI from an Ambit1/2, keeping every other waypoint (André, 2026-11-21).
    SuuntoLink itself manages this family's POIs by rewriting the whole list -
    0x0b04 empties it, one 0x0b05 per waypoint (assets/pcap/ambit2_suuntolink_settings_
    sportmodes.pcap) - so removing one is that same write without it: a copy of the list goes
    to <backup_dir>/POIs/ambit1-2/ first, the rest is written back (waypoints_restore), the
    list is re-read and checked, and on a failed check the copy is written back once."""
    info = waypoints()
    if not info.get("ok"):
        raise RuntimeError(info.get("error", "could not read the POIs"))
    wps = info.get("waypoints", [])
    keep, removed = waypoints_without(wps, name, lat, lon)
    if removed is None:
        raise RuntimeError(f"no POI named {name!r} at that position on the watch")
    import time                                              # noqa: PLC0415
    folder = pathlib.Path(backup_dir) / "POIs" / "ambit1-2"
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / time.strftime("waypoints-%Y%m%d-%H%M%S.json")
    backup.write_text(json.dumps(wps, indent=1))
    waypoints_restore(keep)
    back = waypoints()
    got = back.get("waypoints", []) if back.get("ok") else None
    if got is None or len(got) != len(keep):
        waypoints_restore(wps)
        raise RuntimeError("the watch did not keep the new list; the copy was written back "
                           f"({backup})")
    return {"ok": True, "removed": removed.get("name", ""), "left": len(keep),
            "backup": str(backup)}


def read_route_region():
    """The raw route-region bytes (used extent only), for a byte-exact backup. Returns None when
    the watch has no routes (no head magic). Mirrors routes() but returns the raw blob, not a
    parse, because restore writes it back verbatim via flash_write."""
    import legacy_route                                      # noqa: PLC0415
    head = flash_read(legacy_route.ROUTE_REGION_ADDR, legacy_route.HEAD_LEN)
    if int.from_bytes(head[:2], "little") != legacy_route.HEAD_MAGIC:
        return None
    point_count = int.from_bytes(head[8:12], "little")
    total = legacy_route.POINTS_OFFSET + legacy_route.POINT_LEN * point_count
    return flash_read(legacy_route.ROUTE_REGION_ADDR, total)


def restore_route_region(data):
    """Write backed-up route-region bytes back to the watch (0x041EB0 + tail 0xFFFFFA1A)."""
    import legacy_route                                      # noqa: PLC0415
    return flash_write(legacy_route.ROUTE_REGION_ADDR, data, legacy_route.ROUTE_COMMIT_EXTRA)


def ambit1_sport_mode_read():
    """The REAL sport modes currently on an Ambit1, decoded off the watch.

    This is the thing openambit/openambit2 cannot do at all (see
    docs/ambit1_sport_mode_format.md): the region is readable via the generic 0x0b17 flash
    read, and the Ambit1's 76-byte settings blob is decoded by ambit1_sport_mode.c. Ambit1
    ONLY - the CLI hard-refuses any other product_id rather than risk applying this layout to
    an Ambit2, which uses the standard 90-byte one."""
    return run(["ambit1-sport-mode-read"])


def sport_mode_write_presets(dry_run=False):
    """Blind-overwrites the watch's sport modes with the first 10 of openambit2's own 19
    factory presets (Running/Trail Running/.../Ski Touring) - capped to 10 in
    ambit_legacy_cli.c because that's this family's real capacity (SuuntoLink's own
    getMaxSportModes(AMBIT/AMBIT2*) == 10), caught live 2026-08-23 before any write reached
    hardware. See ambit_legacy_cli.c's own header comment for why there's no readback/backup:
    this family's driver has no sport-mode read function in openambit OR openambit2, so there
    is nothing to preserve first. dry_run builds the payload and reports its shape without
    touching the watch."""
    args = ["sport-mode-write-presets"]
    if dry_run:
        args.append("--dry-run")
    return run(args)


# The field order ambit_legacy_cli's own sport-mode-write parser expects, after the name.
_SPORT_MODE_FIELDS = ("activityId", "modeId", "gpsInterval", "recordingInterval",
                      "altiBaroMode", "hrBelt", "footPod", "bikePod", "cadencePod",
                      "autolapM")


def sport_modes_to_cli_lines(modes):
    """The host master copy (a list of dicts - see server.py's LEGACY_SPORT_MODES_FILE) ->
    ambit_legacy_cli's pipe-separated one-mode-per-line input. Names are sanitised here (the
    parser splits on '|', and a newline would split the record itself) and truncated to the
    watch's own 16-byte activity_name field, so what's written is what was shown."""
    lines = []
    for m in modes:
        name = str(m.get("name", "")).replace("|", " ").replace("\n", " ").strip()[:15]
        vals = []
        for key in _SPORT_MODE_FIELDS:
            v = m.get(key, 0)
            vals.append(str(int(bool(v)) if isinstance(v, bool) else int(v or 0)))
        lines.append("|".join([name, *vals]))
    return "\n".join(lines) + "\n"


def sport_mode_write(modes, dry_run=False):
    """Writes the host's master copy of the user's sport modes to the watch. Same blind,
    one-way REPLACE as sport_mode_write_presets() (this family has no sport-mode read at all -
    see that function's docstring); the difference is only WHOSE set gets written - the user's
    own saved+edited one instead of the factory presets."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(sport_modes_to_cli_lines(modes))
        path = f.name
    try:
        args = ["sport-mode-write", path]
        if dry_run:
            args.append("--dry-run")
        return run(args)
    finally:
        pathlib.Path(path).unlink(missing_ok=True)


def _sport_mode_region_rmw(mode, patcher, write, backup_dir):
    """Read-modify-write of the sport-mode region's first 10240 bytes, the extent SuuntoLink
    writes: read them (0x0b17), `patcher(region)` -> the new bytes, write them back (0x0b16 + the
    0x0b18 commit tail [0x2000][0xFFFFFFFF]) and read them again to check. Without `write`
    nothing is sent; with it, the region as read goes to <backup_dir> first."""
    import time                                               # noqa: PLC0415
    import legacy_sport_modes as LSM                          # noqa: PLC0415

    region = LSM.read_region_live(LSM.REGION_WRITE_BYTES)
    if len(region) != LSM.REGION_WRITE_BYTES:
        raise RuntimeError(f"read {len(region)} of the sport-mode region's "
                           f"{LSM.REGION_WRITE_BYTES} bytes: nothing written")
    used = LSM.region_used(region)
    new = patcher(region)
    changed = sum(1 for a, b in zip(region, new) if a != b)
    result = {"ok": True, "wrote": False, "mode": mode, "bytes_changed": changed}
    if not write or not changed:
        return result
    from write_nav import DEFAULT_BACKUP_DIR                  # noqa: PLC0415
    folder = pathlib.Path(backup_dir or DEFAULT_BACKUP_DIR / "legacy_sport_modes")
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / time.strftime("sportmodes-%Y%m%d-%H%M%S.bin")
    backup.write_bytes(region)
    flash_write(LSM.REGION_ADDR, new, LSM.COMMIT_EXTRA)
    back = LSM.read_region_live(LSM.REGION_WRITE_BYTES)
    # A display edit moves the used extent; the bytes a shorter list freed were written 0xFF.
    check = max(used, LSM.region_used(new))
    if back[:check] != new[:check]:
        raise RuntimeError(f"the watch did not read back what was written (the copy: {backup})")
    result.update(wrote=True, backup=str(backup))
    return result


def sport_mode_edit(mode, patch, write=False, backup_dir=None):
    """Changes one sport mode's settings on an Ambit1/2: name, autolap, HR limits, pods (the
    `patch` keys of legacy_sport_modes.patch_region, which says where each byte comes from).
    One _sport_mode_region_rmw. The same bytes as the Android app's
    AmbitLegacySportModes.writeLegacySportMode. `mode` is the mode's name."""
    import legacy_sport_modes as LSM                          # noqa: PLC0415
    return _sport_mode_region_rmw(mode, lambda region: LSM.patch_region(region, mode, patch),
                                  write, backup_dir)


def sport_mode_displays_edit(mode, edits, write=False, backup_dir=None):
    """Edits one sport mode's displays on an Ambit1/2: add, remove, change a display's layout,
    change a row's values (the Sport Modes page's staged edits, in order; legacy_sport_modes.
    patch_displays says which capture each byte comes from). One _sport_mode_region_rmw, so the
    extent, chunks and commit tail are the settings edit's. Until 2026-11-26 the desktop sent the
    Ambit1's display edits to ambit_legacy_cli ambit1-sport-mode-patch, which wrote 8192 bytes
    and no commit tail (an edit the watch drops after a reconnect) and refused the Ambit2. The
    Android app writes the same bytes for a row change (AmbitLegacySportModes.
    writeLegacyDisplayRow). `mode` is the mode's name."""
    import legacy_sport_modes as LSM                          # noqa: PLC0415
    return _sport_mode_region_rmw(mode, lambda region: LSM.patch_displays(region, mode, edits),
                                  write, backup_dir)


def sport_mode_create(name, activity_id, variant, write=False, backup_dir=None):
    """Creates one sport mode on an Ambit1/2 as SuuntoLink does (legacy_sport_modes.create_mode
    says which capture each byte comes from): its settings from the sport's SuuntoLink defaults,
    SuuntoLink's seven new-mode displays and, on the Ambit2 family, its menu entry. One
    _sport_mode_region_rmw. `variant` is the watch's codename (Bluebird, Duck, Colibri,
    Greentit): it picks the record format and which of SuuntoLink's settings the model has."""
    import legacy_sport_modes as LSM                          # noqa: PLC0415
    return _sport_mode_region_rmw(
        name, lambda region: LSM.create_mode(region, name, activity_id, variant), write, backup_dir)


def sport_mode_delete(name, variant, multisport=False, write=False, backup_dir=None):
    """Deletes one sport mode (and its menu entry) or, with `multisport`, one multisport and the
    modes only it used, with SuuntoLink's refusals (legacy_sport_modes.delete_mode /
    delete_multisport). One _sport_mode_region_rmw."""
    import legacy_sport_modes as LSM                          # noqa: PLC0415
    op = LSM.delete_multisport if multisport else LSM.delete_mode
    return _sport_mode_region_rmw(name, lambda region: op(region, name, variant), write, backup_dir)


def sport_mode_mirror(source, write=False, backup_dir=None):
    """Makes this Ambit1/2's sport-mode region the bytes of another watch of the same model (the
    Copy page, watch-protocol/10 L14): `source` is that watch's region as read. Its used extent is
    written with the rest 0xFF, as SuuntoLink and every writer here leave the 10240 bytes, through
    the same _sport_mode_region_rmw (a copy of this watch's region first, read-back after). The
    caller checks the two watches are the same model (André, 2026-08-30). Refused when the source's
    displays show a Suunto App or its modes link one (0x010C): this family's Apps are not copied
    (no Apps writer), so those rows would point at the target's own apps."""
    import legacy_sport_modes as LSM                          # noqa: PLC0415
    used = LSM.region_used(source)
    if used > LSM.REGION_WRITE_BYTES:
        raise ValueError(f"the source's sport modes use {used} bytes, past the "
                         f"{LSM.REGION_WRITE_BYTES} SuuntoLink writes")
    links = LSM.app_links(source)
    if links:
        raise ValueError("the source's sport modes show Suunto Apps ("
                         + ", ".join(sorted(links)) + "): apps are not copied on an Ambit1/2 yet")
    want = bytes(source[:used]) + b"\xff" * (LSM.REGION_WRITE_BYTES - used)

    def patcher(region):
        lens = {n for _o, n in LSM.named_blobs(region)}
        if lens and lens != {n for _o, n in LSM.named_blobs(want)}:
            raise ValueError("the two watches' sport-mode records differ: not the same model")
        return want
    return _sport_mode_region_rmw("all sport modes", patcher, write, backup_dir)


def pois_add(new_pois, write=False, backup_dir=None):
    """Adds POIs to an Ambit1/2, keeping every waypoint already on it, route waypoints included -
    the list writer poi_remove and route_send use (SuuntoLink rewrites this family's list whole:
    0x0b04, then one 0x0b05 each). `new_pois` are {name, lat, lon, type, ctime?}: the type as this
    family numbers it, the ctime fields carried when the POI comes off another Ambit1/2, else 0
    (what poi-add writes). Skips one already there (same name and position to 1e-5 degree).
    Without `write` nothing is sent; with it, the list is saved to <backup_dir> first, read back
    after, and the copy written back once if the count is wrong."""
    import time                                               # noqa: PLC0415
    import legacy_route                                       # noqa: PLC0415

    current = waypoints()
    if not current.get("ok"):
        raise RuntimeError(current.get("error", "could not read the watch's waypoint list"))
    listed = current.get("waypoints", [])
    have = {(str(w.get("name", "")).strip().lower(), round(float(w.get("lat", 0.0)), 5),
             round(float(w.get("lon", 0.0)), 5)) for w in listed}
    added = []
    for p in new_pois:
        key = (str(p.get("name", "")).strip().lower(), round(float(p["lat"]), 5),
               round(float(p["lon"]), 5))
        if key in have:
            continue
        have.add(key)
        rec = {"name": str(p.get("name", "")), "route_name": "", "lat": float(p["lat"]),
               "lon": float(p["lon"]), "type": int(p.get("type", 0)) & 0xFF}
        for k in ("ctime_year", "ctime_month", "ctime_day", "ctime_hour", "ctime_minute",
                  "ctime_second"):
            rec[k] = int((p.get("ctime") or {}).get(k, 0))
        added.append(rec)
    if len(listed) + len(added) > legacy_route.MAX_LIST_WAYPOINTS:
        raise ValueError(f"{len(listed)} POIs and route waypoints are on the watch and {len(added)} "
                         f"more would pass the {legacy_route.MAX_LIST_WAYPOINTS} it holds")
    result = {"ok": True, "wrote": False, "added": [r["name"] for r in added], "kept": len(listed)}
    if not write or not added:
        return result
    from write_nav import DEFAULT_BACKUP_DIR                  # noqa: PLC0415
    folder = pathlib.Path(backup_dir or DEFAULT_BACKUP_DIR / "legacy_nav")
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / time.strftime("legacy-waypoints-%Y%m%d-%H%M%S.json")
    backup.write_text(json.dumps(current))
    waypoints_restore(listed + added)
    back = waypoints()
    if not back.get("ok") or len(back.get("waypoints", [])) != len(listed) + len(added):
        waypoints_restore(listed)
        raise RuntimeError(f"the watch did not keep the new list; the copy was written back ({backup})")
    result.update(wrote=True, backup=str(backup))
    return result


def sport_mode_menu(variant):
    """The watch's sport modes and multisports with what may be deleted and the 10-mode count
    (legacy_sport_modes.menu). Read only."""
    import legacy_sport_modes as LSM                          # noqa: PLC0415
    region = LSM.read_region_live(LSM.REGION_WRITE_BYTES)
    return dict(LSM.menu(region, variant), ok=True)


def waypoints_without_route(waypoints, route_name, still_used=()):
    """The waypoint list without a removed route's own waypoints: those whose route_name is that
    route's (as route_send tags them, and SuuntoLink before it). Kept when another route left on
    the watch has the same name. Returns (keep, dropped)."""
    target = str(route_name).strip()
    if target in {str(n).strip() for n in still_used}:
        return list(waypoints), 0
    keep = [w for w in waypoints if str(w.get("route_name", "")).strip() != target]
    return keep, len(waypoints) - len(keep)


def route_remove(name, write=False, backup_dir=None):
    """Removes one route from an Ambit1/2, keeping every other route and every POI.

    The reverse of route_send, through the same two writes: the route region rebuilt without it
    (legacy_route.remove: the other routes byte for byte, flash_write with the 0x0b18 tail
    0xFFFFFA1A), then the waypoint list rewritten without that route's waypoints (0x0b04 + one
    0x0b05 each, as poi_remove). No capture shows SuuntoLink removing a route; the bytes are the
    captured region format, as route_send and the backup restore write it. The last route is not
    removed (an empty region is in no capture). Without `write` nothing is sent; with it, the
    region and the list are saved to <backup_dir> first, both are read back, and on a wrong
    read-back both copies are written back once."""
    import time                                               # noqa: PLC0415
    import legacy_route                                       # noqa: PLC0415

    region = read_route_region()
    if region is None:
        raise RuntimeError("the watch has no routes")
    blob, removed = legacy_route.remove(region, name)
    current = waypoints()
    if not current.get("ok"):
        raise RuntimeError(current.get("error", "could not read the watch's waypoint list"))
    listed = current.get("waypoints", [])
    left = legacy_route.parse(blob)["routes"]
    keep, dropped = waypoints_without_route(listed, removed["name"], [r["name"] for r in left])
    result = {"ok": True, "wrote": False, "removed": removed["name"].strip(),
              "routes_left": len(left), "waypoints_removed": dropped}
    if not write:
        return result
    from write_nav import DEFAULT_BACKUP_DIR                  # noqa: PLC0415
    backup_dir = pathlib.Path(backup_dir or DEFAULT_BACKUP_DIR / "legacy_nav")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (backup_dir / f"legacy-routes-{stamp}.bin").write_bytes(region)
    (backup_dir / f"legacy-waypoints-{stamp}.json").write_text(json.dumps(current))
    restore_route_region(blob)
    if dropped:
        waypoints_restore(keep)
    head = route_head()
    back = waypoints() if dropped else {"ok": True, "waypoints": keep}
    if (head.get("route_count") != len(left) or not back.get("ok")
            or len(back.get("waypoints", [])) != len(keep)):
        restore_route_region(region)
        if dropped:
            waypoints_restore(listed)
        raise RuntimeError("the watch did not keep the new routes; the copies were written back "
                           f"({backup_dir / f'legacy-routes-{stamp}.bin'})")
    result.update(wrote=True, backup=str(backup_dir / f"legacy-routes-{stamp}.bin"))
    return result


def route_send(gpx_paths, write=False, backup_dir=None):
    """Adds routes to an Ambit1/2, keeping every route and POI already on it - the order
    SuuntoLink uses (Ambit1 capture zzambit1full): the route region (0x041EB0, commit tail
    0xFFFFFA1A), then the waypoint list rewritten with each new route's own waypoints
    (direction pins, or Start/End) tagged with the route name and their rank.

    `gpx_paths` are route GPX files (<rtept>, waypoints typed "Waypoint" - what route_pins.py
    --target ambit / --plain writes). Without `write` nothing is sent: the result says what
    would be. Before a write the current route region and waypoint list are saved to
    `backup_dir` (~/SommetBackups/legacy_nav by default) so they can be put back."""
    import time                                               # noqa: PLC0415
    import legacy_route                                       # noqa: PLC0415
    from build_route import route_from_gpx                    # noqa: PLC0415

    new_routes, new_wpts = [], []
    for path in gpx_paths:
        r = route_from_gpx(pathlib.Path(path), 0, 0, 0, (1, 1, 0, 0, 0, 0),
                           max_points=legacy_route.MAX_ROUTE_POINTS)
        name = r.name.encode("latin-1", "replace")[:15].decode("latin-1")
        pts = [(lat, lon) for lat, lon, _ in r.points]
        dist = sum(legacy_route._haversine_km(*a, *b) for a, b in zip(pts, pts[1:])) * 1000
        new_routes.append({"name": name, "points": pts, "distance_m": int(dist)})
        new_wpts += legacy_route.route_waypoints(name, [(lat, lon, wn) for lat, lon, wn, _ in r.waypoints])

    region = read_route_region()
    current = waypoints()
    if not current.get("ok"):
        raise RuntimeError(current.get("error", "could not read the watch's waypoint list"))
    listed = current.get("waypoints", [])
    blob = legacy_route.append(region, new_routes)           # raises past the route room
    if len(listed) + len(new_wpts) > legacy_route.MAX_LIST_WAYPOINTS:
        raise RuntimeError(f"{len(listed)} POIs and route waypoints are on the watch and these "
                           f"routes add {len(new_wpts)}: it holds {legacy_route.MAX_LIST_WAYPOINTS}. "
                           "Remove some first")
    old = legacy_route.parse(region)["routes"] if region else []
    taken = {r["name"].strip() for r in old}
    clash = [r["name"] for r in new_routes if r["name"].strip() in taken]
    if clash:
        raise RuntimeError(f"the watch already has a route named {clash[0]!r}: rename the new one")
    result = {"ok": True, "wrote": False, "routes_kept": len(old),
              "routes_added": [r["name"] for r in new_routes], "points": sum(len(r["points"]) for r in new_routes),
              "waypoints_added": len(new_wpts), "region_bytes": len(blob)}
    if not write:
        return result
    # Never the program's own folder: temporary or read-only in the packaged app.
    from write_nav import DEFAULT_BACKUP_DIR                  # noqa: PLC0415
    backup_dir = pathlib.Path(backup_dir or DEFAULT_BACKUP_DIR / "legacy_nav")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if region:
        (backup_dir / f"legacy-routes-{stamp}.bin").write_bytes(region)
    (backup_dir / f"legacy-waypoints-{stamp}.json").write_text(json.dumps(current))
    restore_route_region(blob)
    waypoints_restore(listed + new_wpts)
    result.update(wrote=True, backup=str(backup_dir / f"legacy-waypoints-{stamp}.json"))
    return result


_COMMANDS = ("device-info", "settings", "waypoints", "logs", "poi-add", "poi-clear",
             "gps-orbit-status", "gps-orbit-write",
             "sport-mode-write-presets", "routes", "route-head",
             "route-region-save", "route-region-restore", "nav-restore-json", "route-send",
             "route-remove", "sport-mode-edit", "sport-mode-displays", "sport-mode-create",
             "sport-mode-delete", "sport-mode-menu", "sport-mode-mirror", "pois-add")


def _opt(flag):
    """The value after `flag` on the command line, or None."""
    args = sys.argv[2:]
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else None


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in _COMMANDS:
        sys.exit(f"usage: {sys.argv[0]} {'|'.join(_COMMANDS)} [ARGS]")
    cmd = sys.argv[1]
    try:
        if cmd == "logs":
            if len(sys.argv) < 3:
                sys.exit(f"usage: {sys.argv[0]} logs OUTDIR")
            result = logs(sys.argv[2])
        elif cmd == "poi-add":
            if len(sys.argv) < 5:
                sys.exit(f"usage: {sys.argv[0]} poi-add NAME LAT LON")
            result = poi_add(sys.argv[2], sys.argv[3], sys.argv[4])
        elif cmd == "routes":
            result = routes()
        elif cmd == "route-head":
            result = route_head()
        elif cmd == "route-region-save":
            # route-region-save OUTFILE : write the raw route-region bytes to OUTFILE for a
            # byte-exact backup. {ok, empty:true} when the watch has no routes.
            if len(sys.argv) < 3:
                sys.exit(f"usage: {sys.argv[0]} route-region-save OUTFILE")
            data = read_route_region()
            if data is None:
                result = {"ok": True, "empty": True, "bytes": 0}
            else:
                pathlib.Path(sys.argv[2]).write_bytes(data)
                result = {"ok": True, "empty": False, "bytes": len(data)}
        elif cmd == "route-region-restore":
            # route-region-restore INFILE : write backed-up route-region bytes back to the watch.
            if len(sys.argv) < 3:
                sys.exit(f"usage: {sys.argv[0]} route-region-restore INFILE")
            result = restore_route_region(pathlib.Path(sys.argv[2]).read_bytes())
        elif cmd == "nav-restore-json":
            # nav-restore-json INFILE : INFILE is the `settings` reply (or any {waypoints:[...]});
            # replace the on-device POI list with its waypoints.
            if len(sys.argv) < 3:
                sys.exit(f"usage: {sys.argv[0]} nav-restore-json INFILE")
            payload = json.loads(pathlib.Path(sys.argv[2]).read_text())
            result = waypoints_restore(payload.get("waypoints", payload) if isinstance(payload, dict) else payload)
        elif cmd == "route-send":
            # route-send GPX... [--write] : add routes (keeps the watch's routes and POIs).
            files = [a for a in sys.argv[2:] if not a.startswith("--")]
            if not files:
                sys.exit(f"usage: {sys.argv[0]} route-send GPX... [--write]")
            try:
                result = route_send(files, write="--write" in sys.argv[2:])
            except ValueError as exc:
                result = {"ok": False, "error": str(exc)}
        elif cmd == "route-remove":
            # route-remove NAME [--write] [--backup-dir DIR] : remove one route (keeps the rest).
            if len(sys.argv) < 3 or sys.argv[2].startswith("--"):
                sys.exit(f"usage: {sys.argv[0]} route-remove NAME [--write] [--backup-dir DIR]")
            try:
                result = route_remove(sys.argv[2], write="--write" in sys.argv[3:],
                                      backup_dir=_opt("--backup-dir"))
            except ValueError as exc:
                result = {"ok": False, "error": str(exc)}
        elif cmd == "sport-mode-edit":
            # sport-mode-edit MODE PATCH_JSON [--write] [--backup-dir DIR] : one mode's settings.
            if len(sys.argv) < 4:
                sys.exit(f"usage: {sys.argv[0]} sport-mode-edit MODE PATCH_JSON [--write] "
                         "[--backup-dir DIR]")
            try:
                result = sport_mode_edit(sys.argv[2], json.loads(sys.argv[3]),
                                         write="--write" in sys.argv[4:],
                                         backup_dir=_opt("--backup-dir"))
            except ValueError as exc:
                result = {"ok": False, "error": str(exc)}
        elif cmd == "sport-mode-displays":
            # sport-mode-displays MODE EDITS_JSON [--write] [--backup-dir DIR] : one mode's
            # displays, the Sport Modes page's edit list.
            if len(sys.argv) < 4:
                sys.exit(f"usage: {sys.argv[0]} sport-mode-displays MODE EDITS_JSON [--write] "
                         "[--backup-dir DIR]")
            try:
                edits = json.loads(sys.argv[3])
                if not isinstance(edits, list):
                    raise ValueError("EDITS_JSON must be a list of edits")
                result = sport_mode_displays_edit(sys.argv[2], edits,
                                                  write="--write" in sys.argv[4:],
                                                  backup_dir=_opt("--backup-dir"))
            except ValueError as exc:
                result = {"ok": False, "error": str(exc)}
        elif cmd in ("sport-mode-create", "sport-mode-delete", "sport-mode-menu"):
            # sport-mode-create NAME ACTIVITY --variant V [--write] [--backup-dir DIR]
            # sport-mode-delete NAME --variant V [--multisport] [--write] [--backup-dir DIR]
            # sport-mode-menu --variant V
            variant = _opt("--variant")
            pos = []
            for i, a in enumerate(sys.argv[2:]):
                if not a.startswith("--") and sys.argv[1 + i] not in ("--variant", "--backup-dir"):
                    pos.append(a)
            need = {"sport-mode-create": 2, "sport-mode-delete": 1, "sport-mode-menu": 0}[cmd]
            if not variant or len(pos) < need:
                sys.exit(f"usage: {sys.argv[0]} sport-mode-create NAME ACTIVITY | sport-mode-delete "
                         "NAME [--multisport] | sport-mode-menu, with --variant CODENAME "
                         "[--write] [--backup-dir DIR]")
            write = "--write" in sys.argv[2:]
            try:
                if cmd == "sport-mode-create":
                    result = sport_mode_create(pos[0], int(pos[1]), variant, write=write,
                                               backup_dir=_opt("--backup-dir"))
                elif cmd == "sport-mode-delete":
                    result = sport_mode_delete(pos[0], variant,
                                               multisport="--multisport" in sys.argv[2:],
                                               write=write, backup_dir=_opt("--backup-dir"))
                else:
                    result = sport_mode_menu(variant)
            except ValueError as exc:
                result = {"ok": False, "error": str(exc)}
        elif cmd in ("sport-mode-mirror", "pois-add"):
            # sport-mode-mirror REGION.bin [--write] [--backup-dir DIR] : the Copy page's
            #     whole-region copy between two watches of one model.
            # pois-add POIS.json [--write] [--backup-dir DIR] : add POIs, keep every waypoint.
            if len(sys.argv) < 3 or sys.argv[2].startswith("--"):
                sys.exit(f"usage: {sys.argv[0]} {cmd} FILE [--write] [--backup-dir DIR]")
            write = "--write" in sys.argv[3:]
            try:
                if cmd == "sport-mode-mirror":
                    result = sport_mode_mirror(pathlib.Path(sys.argv[2]).read_bytes(), write=write,
                                               backup_dir=_opt("--backup-dir"))
                else:
                    result = pois_add(json.loads(pathlib.Path(sys.argv[2]).read_text()),
                                      write=write, backup_dir=_opt("--backup-dir"))
            except ValueError as exc:
                result = {"ok": False, "error": str(exc)}
        elif cmd == "sport-mode-write-presets":
            result = sport_mode_write_presets(dry_run="--dry-run" in sys.argv[2:])
        elif cmd == "gps-orbit-write":
            if len(sys.argv) < 3:
                sys.exit(f"usage: {sys.argv[0]} gps-orbit-write FILE")
            result = run(["gps-orbit-write", sys.argv[2]])
        else:
            result = run([cmd])
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
