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


def run(args):
    """Runs the compiled CLI, returns its parsed JSON stdout object. Raises RuntimeError
    with a clear, actionable message (never a build isn't findable) if it's missing or the
    process failed to produce parseable JSON - the same "report, don't mask" discipline as
    every other tools/*.py CLI in this project."""
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
    proc = subprocess.run([str(binary), *device_args, *args],
                           capture_output=True, timeout=120)
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


def logs(outdir):
    return run(["logs", str(outdir)])


def poi_add(name, lat, lon):
    return run(["poi-add", name, str(lat), str(lon)])


def poi_clear():
    return run(["poi-clear"])


def settings_write(key, value, dry_run=False):
    """Writes ONE personal-settings field on an Ambit1.

    Command 0x0b01, solved from André's own capture: SuuntoLink sends back the same 132-byte
    structure the 0x0b00 read returns, with the changed field patched in place. The CLI does
    the read-modify-write, so every field the caller did not name is preserved exactly."""
    args = ["settings-write", str(key), str(int(value))]
    if dry_run:
        args.append("--dry-run")
    return run(args)


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


_COMMANDS = ("device-info", "settings", "logs", "poi-add", "poi-clear",
             "sport-mode-write-presets")


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
