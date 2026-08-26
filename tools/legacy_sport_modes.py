#!/usr/bin/env python3
"""Ambit1/2 ("Bluebird") sport-mode READ - the one thing openambit/openambit2 cannot do
for this family at all (their driver has a sport-mode *write* but no read; see
tools/legacy_link.py's sport_mode_write_presets docstring). It's readable because the
region is a plain 0x0b17 flash read and the on-flash layout is a documented nested TLV.

Two paths, mirroring the rest of tools/:
  - offline:  ./tools/legacy_sport_modes.py --from DUMP.bin        # decode a saved region
  - live:     ./tools/legacy_sport_modes.py                        # dump 0x2000 off the
              watch via tools/vendor/ambit_legacy_cli's `region-dump`, then decode

Region 0x00002000 (PMEM20_SPORT_MODE_START), nested `[u16 tag][u16 len][body]`:
  0x0003 root / 0x0100 modes / 0x0101 one mode / 0x0102 its settings blob /
  0x0105-0x010a display config / 0x0200,0x0210 multisport groups
  (see docs/ambit1_sport_mode_format.md and openambit sport_mode_serialize.h).

The settings blob (tag 0x0102) is **90 bytes on the Ambit2** (openambit's standard
`ambit_sport_mode_settings_t`) and 76 on the Ambit1 (five capabilities dropped - see the
format doc). This decoder reads whatever length is present and only decodes the fields
that fall inside it, so it handles both. Field layout below is openambit's struct order,
validated 2026-08-26 against a real Ambit2 region (10 modes incl. a Triathlon multisport;
GPS=0 on pool-swim/gym, bike-pod bits on Cyclisme, 1000 m autolap on trail-run - all
self-consistent with the sport types).

READ-ONLY: only ever issues 0x0b17 (flash read) in live mode; never writes.
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REGION_ADDR = 0x00002000
REGION_BYTES = 16384          # generous; the watch returns however much the region holds

TAG_SETTINGS = 0x0102
CONTAINERS = {0x0003, 0x0100, 0x0101, 0x0105, 0x0106, 0x0107, 0x0108, 0x0109,
              0x0200, 0x0210}

# (name, offset, size) into the settings blob. u16 little-endian unless size 16 (name).
# openambit ambit_sport_mode_settings_t order; offsets are cumulative over that struct.
_FIELDS = [
    ("activity_name", 0, 16), ("activity_id", 16, 2), ("sport_mode_id", 18, 2),
    ("hrbelt_and_pods", 22, 2), ("alti_baro_mode", 24, 2), ("gps_interval", 26, 2),
    ("recording_interval", 28, 2), ("autolap", 30, 2), ("heartrate_max", 32, 2),
    ("heartrate_min", 34, 2), ("use_heartrate_limits", 36, 2), ("auto_pause", 40, 2),
    ("auto_scroll", 42, 2), ("use_interval_timer", 44, 2), ("interval_repetitions", 46, 2),
    ("interval_timer_max_unit", 48, 2), ("interval_timer_max", 56, 2),
    ("interval_timer_min_unit", 60, 2), ("interval_timer_min", 68, 2),
    ("backlight_mode", 84, 2), ("display_mode", 86, 2), ("quick_navigation", 88, 2),
]


def _u16(b, o):
    return b[o] | (b[o + 1] << 8)


# hrbelt_and_pods bit layout, decoded from a real SuuntoLink<->Ambit2 USBPcap 2026-08-26
# (mode toggled one accessory at a time; cross-checked against the factory Cycling mode
# 0x08c3 = HR+Bike+Power+Cadence). See docs / the ambit-app-ambit2-arrived memory.
POD_BITS = {
    "hr_belt":     0x0003,   # bits 0+1
    "power_pod":   0x0040,   # bit 6
    "cadence_pod": 0x0080,   # bit 7
    "foot_pod":    0x0100,   # bit 8
    "bike_pod":    0x0800,   # bit 11
}


def decode_settings(blob):
    """One 0x0102 settings blob -> dict. Decodes only fields inside the blob's length,
    so a 76-byte Ambit1 blob and a 90-byte Ambit2 blob both come back cleanly."""
    out = {"_blob_len": len(blob)}
    for name, off, size in _FIELDS:
        if off + size > len(blob):
            continue
        if size == 16:
            out[name] = blob[off:off + 16].split(b"\x00")[0].decode("latin1", "replace")
        else:
            out[name] = _u16(blob, off)
    if "hrbelt_and_pods" in out:
        v = out["hrbelt_and_pods"]
        out["pods"] = {name: bool(v & mask) for name, mask in POD_BITS.items()}
    return out


def parse_region(data):
    """Walk the nested TLV and return every sport mode's decoded settings, in file order."""
    modes = []

    def walk(b):
        o = 0
        while o + 4 <= len(b):
            tag = _u16(b, o)
            ln = _u16(b, o + 2)
            body = b[o + 4:o + 4 + ln]
            if len(body) != ln:                       # truncated tail - stop cleanly
                break
            if tag == TAG_SETTINGS:
                modes.append(decode_settings(body))
            elif tag in CONTAINERS and ln >= 4:
                walk(body)
            o += 4 + ln
            if tag == 0 and ln == 0:                  # padding / end of region
                break

    walk(data)
    return modes


def _binary_path():
    for name in ("ambit_legacy_cli", "ambit_legacy_cli.exe"):
        c = HERE / "vendor" / "ambit_legacy_cli" / name
        if c.exists():
            return c
    return None


def read_region_live():
    """Dump region 0x2000 off the watch via the compiled legacy CLI's read-only
    `region-dump` (0x0b17), same binary tools/legacy_link.py already shells out to."""
    binary = _binary_path()
    if binary is None:
        raise RuntimeError(
            "ambit_legacy_cli is not built - run tools/vendor/ambit_legacy_cli/build.sh "
            "(see tools/legacy_link.py for the same dependency).")
    device_args = []
    env_pid = os.environ.get("AMBIT_PRODUCT_ID")
    if env_pid:
        device_args = ["--device", env_pid]
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        path = f.name
    try:
        proc = subprocess.run(
            [str(binary), *device_args, "region-dump", hex(REGION_ADDR), path,
             str(REGION_BYTES)],
            capture_output=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(
                "region-dump failed (exit %d): %s"
                % (proc.returncode, proc.stderr.decode("utf-8", "replace")))
        return pathlib.Path(path).read_bytes()
    finally:
        pathlib.Path(path).unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="from_file", metavar="FILE",
                    help="decode a saved region dump instead of reading the watch")
    ap.add_argument("--json", action="store_true", help="emit one JSON object")
    args = ap.parse_args()

    if args.from_file:
        data = pathlib.Path(args.from_file).read_bytes()
    else:
        data = read_region_live()

    modes = parse_region(data)

    if args.json:
        print(json.dumps({"ok": True, "count": len(modes), "modes": modes}))
        return 0

    print("read-only: region 0x%04x decoded, %d sport mode(s)" % (REGION_ADDR, len(modes)))
    for i, m in enumerate(modes):
        print("  [%d] %-18s act_id=%-3d mode_id=%d  gps=%ss rec=%ss autolap=%d  "
              "hr[%d-%d]%s pods=0x%04x alti_baro=%d"
              % (i, repr(m.get("activity_name", "")), m.get("activity_id", -1),
                 m.get("sport_mode_id", -1), m.get("gps_interval", "?"),
                 m.get("recording_interval", "?"), m.get("autolap", -1),
                 m.get("heartrate_min", -1), m.get("heartrate_max", -1),
                 "" if m.get("use_heartrate_limits") else "(off)",
                 m.get("hrbelt_and_pods", 0), m.get("alti_baro_mode", -1)))
        pods = m.get("pods", {})
        on = [n for n in ("hr_belt", "foot_pod", "bike_pod", "power_pod", "cadence_pod")
              if pods.get(n)]
        print("        pods: %s" % (", ".join(on) if on else "none"))
        if m.get("_blob_len") != 90:
            print("        (settings blob %d bytes - Ambit1 76-byte layout, some fields "
                  "absent)" % m.get("_blob_len"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
