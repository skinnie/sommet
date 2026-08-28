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
format has never been captured in this project (see the ambit-app-ambit12-settings-write
memory), so there's nothing safe to send for THAT specific piece yet.

    ./tools/legacy_link.py device-info
    ./tools/legacy_link.py settings
    ./tools/legacy_link.py logs OUTDIR
    ./tools/legacy_link.py poi-add NAME LAT LON
    ./tools/legacy_link.py poi-clear
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
    desktop/backend/server.py's own FROZEN branch, tools/packaging/ambit_backend.spec)."""
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
    `settings` reply lists them). Command path: write_start + nav_memory_delete + one
    waypoint_write per point, type kept RAW so a backup round-trips byte-exact. 0x0b04 clears
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


_COMMANDS = ("device-info", "settings", "waypoints", "logs", "poi-add", "poi-clear",
             "sport-mode-write-presets", "routes",
             "route-region-save", "route-region-restore", "nav-restore-json")


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
        elif cmd == "sport-mode-write-presets":
            result = sport_mode_write_presets(dry_run="--dry-run" in sys.argv[2:])
        else:
            result = run([cmd])
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
