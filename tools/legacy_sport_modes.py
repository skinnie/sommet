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
format doc). The blob's own length picks the field table: 90 = openambit's struct order,
validated 2026-08-26 against a real Ambit2 region (10 modes incl. a Triathlon multisport;
GPS=0 on pool-swim/gym, bike-pod bits on Cyclisme, 1000 m autolap on trail-run - all
self-consistent with the sport types); 76 = the Ambit1 table of
docs/ambit1_sport_mode_format.md section 4 (the same offsets as ambit1_sport_mode.c). Until
2026-11-26 the 90-byte table was applied to Ambit1 blobs too, which read the pods out of the
GPS interval and the autolap out of the low HR limit.

This module only reads the watch. patch_region() builds the bytes of one sport-mode edit
(read-modify-write: the region as read, with one mode's fields changed); the write itself is
legacy_link.sport_mode_edit(). Android does the same in AmbitLegacySportModes.ts.
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


# The Ambit1's 76-byte blob (docs/ambit1_sport_mode_format.md section 4, every offset seen in
# SuuntoLink's own writes; tools/vendor/ambit_legacy_cli/ambit1_sport_mode.c uses the same).
# It has no sport_mode_id, use_heartrate_limits, auto_scroll, backlight, display mode or
# quick navigation field.
_FIELDS_AMBIT1 = [
    ("activity_name", 0, 16), ("activity_id", 16, 2), ("hrbelt_and_pods", 18, 2),
    ("alti_baro_mode", 20, 2), ("gps_interval", 22, 2), ("recording_interval", 24, 2),
    ("autolap", 26, 2), ("heartrate_max", 28, 2), ("heartrate_min", 30, 2),
    ("auto_pause", 34, 2), ("use_interval_timer", 36, 2), ("interval_repetitions", 38, 2),
    ("interval_timer_max_unit", 40, 2), ("interval_timer_max", 48, 2),
    ("interval_timer_min_unit", 52, 2), ("interval_timer_min", 60, 2),
]
AMBIT1_BLOB_LEN = 76
AMBIT2_BLOB_LEN = 90


def _u16(b, o):
    return b[o] | (b[o + 1] << 8)


# hrbelt_and_pods bit layout, decoded from a real SuuntoLink<->Ambit2 USBPcap 2026-08-26
# (mode toggled one accessory at a time; cross-checked against the factory Cycling mode
# 0x08c3 = HR+Bike+Power+Cadence). See docs / the sommet-ambit2-arrived memory.
POD_BITS = {
    "hr_belt":     0x0003,   # bits 0+1
    "power_pod":   0x0040,   # bit 6
    "cadence_pod": 0x0080,   # bit 7
    "foot_pod":    0x0100,   # bit 8
    "bike_pod":    0x0800,   # bit 11
}


def decode_settings(blob):
    """One 0x0102 settings blob -> dict. A 76-byte blob is read with the Ambit1 table, any
    other length with the Ambit2 one, decoding only the fields inside the blob's length."""
    out = {"_blob_len": len(blob)}
    for name, off, size in (_FIELDS_AMBIT1 if len(blob) == AMBIT1_BLOB_LEN else _FIELDS):
        if off + size > len(blob):
            continue
        if size == 16:
            out[name] = blob[off:off + 16].split(b"\x00")[0].decode("latin1", "replace")
        else:
            out[name] = _u16(blob, off)
    if "hrbelt_and_pods" in out:
        v = out["hrbelt_and_pods"]
        bits = dict(POD_BITS)
        if len(blob) == AMBIT1_BLOB_LEN:
            bits["hr_belt"] = LAYOUTS[AMBIT1_BLOB_LEN]["hr_belt"]
        out["pods"] = {name: bool(v & mask) for name, mask in bits.items()}
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


def to_app_mode(m):
    """One decoded settings dict -> the desktop app's sport-mode schema (the same keys
    server.py's LEGACY_FACTORY_SPORT_MODES uses and legacy_link.sport_modes_to_cli_lines
    consumes), so the Sport Modes page shows the watch's REAL modes, pods included."""
    pods = m.get("pods", {})
    return {
        "name": m.get("activity_name", ""),
        "activityId": m.get("activity_id", 0),
        "modeId": m.get("sport_mode_id", 0),
        "gpsInterval": m.get("gps_interval", 0),
        "recordingInterval": m.get("recording_interval", 0),
        "altiBaroMode": m.get("alti_baro_mode", 0),
        "autolapM": m.get("autolap", 0),
        "hrBelt": pods.get("hr_belt", False),
        "footPod": pods.get("foot_pod", False),
        "bikePod": pods.get("bike_pod", False),
        "cadencePod": pods.get("cadence_pod", False),
        "powerPod": pods.get("power_pod", False),
        # The raw values the Sport Modes page edits: the whole pods field (bits no checkbox
        # names, like 0x0004, must survive a toggle) and the HR limits. The Ambit1 has no
        # use_heartrate_limits field: limits of 0/0 are its "off" (see patch_region).
        "useHw": m.get("hrbelt_and_pods", 0),
        "hrMax": m.get("heartrate_max", 0),
        "hrMin": m.get("heartrate_min", 0),
        "useHrLimits": bool(m["use_heartrate_limits"]) if "use_heartrate_limits" in m
        else bool(m.get("heartrate_max", 0) or m.get("heartrate_min", 0)),
    }


# ─── Editing one sport mode (2026-11-26, watch-protocol/10 L4) ─────────────────────────────────
# What SuuntoLink writes, read from both captures (assets/pcap/ambit2_suuntolink_settings_
# sportmodes.pcap, 159 writes; assets/pcap/2026-08-23-ambit1-suuntolink/zzambit1full.pcap, 79):
# always the region's first 10240 bytes (0x2000-0x47FF; 10 x 1024 on the Ambit2, 20 x 512 on the
# Ambit1), then 0x0b18 [0x2000][0xFFFFFFFF]. One save in SuuntoLink is up to three writes
# (settings, displays, groups), and each also bumps a Unix time in the mode's 0x01FF record
# (settings, displays) or the group's 0x02FF: SuuntoLink's own bookkeeping, left as read here,
# as the Android writer (seen working on an Ambit2, 2026-08-27) always has.
#
# Field by field, each change below is the one SuuntoLink made when that setting alone was
# changed (tools/test_legacy_sport_mode_edit.py replays them):
#   name     blob 0, 16 bytes latin-1, NUL-padded (no NUL when 16 long: "Adventure Racing").
#            On the Ambit2 the mode's single-mode group (0x0210 whose one 0x0214 is the mode's
#            place in 0x0100) carries the name too, in its 24-byte 0x0212: renamed with it.
#            The Ambit1 region has no groups.
#   autolap  u16 metres.
#   HR       on: max/min (+ use_heartrate_limits 1 on the Ambit2). Off: max = min = 0 (+ use 0):
#            SuuntoLink zeroes them (Ambit2 239/30/1 -> 0/0/0), and on the Ambit1, which has no
#            "use" field, 0/0 is the only way to say off.
#   pods     u16 bit field. The HR belt is bit 0 on the Ambit1 (0x0107 -> 0x0106) but bits 0+1
#            on the Ambit2 (0x0003 -> 0x0000 -> 0x0003): the pages toggle bit 0, so on the
#            Ambit2 bit 1 follows it.
REGION_WRITE_BYTES = 10240
COMMIT_EXTRA = 0xFFFFFFFF
TAG_ROOT, TAG_MODES, TAG_MODE = 0x0003, 0x0100, 0x0101
TAG_GROUPS, TAG_GROUP, TAG_GROUP_NAME, TAG_GROUP_MODE = 0x0200, 0x0210, 0x0212, 0x0214
GROUP_NAME_LEN = 24
NAME_LEN = 16

# Offsets of the editable fields, by settings-blob length (= by watch family).
LAYOUTS = {
    AMBIT1_BLOB_LEN: {"family": "ambit1", "pods": 18, "autolap": 26, "hr_max": 28, "hr_min": 30,
                      "use_hr": None, "hr_belt": 0x0001},
    AMBIT2_BLOB_LEN: {"family": "ambit2", "pods": 22, "autolap": 30, "hr_max": 32, "hr_min": 34,
                      "use_hr": 36, "hr_belt": 0x0003},
}


def region_used(data):
    """Bytes in use (root record and its length), or raise if this is not a sport-mode region."""
    if len(data) < 4 or _u16(data, 0) != TAG_ROOT:
        raise ValueError("not a sport-mode region (no 0x0003 root record)")
    used = 4 + _u16(data, 2)
    if used > len(data):
        raise ValueError(f"sport-mode region read short: {len(data)} of {used} bytes")
    return used


def named_blobs(data):
    """(offset, length) of each named mode's 0x0102 settings blob, in the order the readers list
    the modes (Android AmbitLegacySportModes.modeBlobOffsets walks the same way)."""
    out = []

    def walk(start, end):
        o = start
        while o + 4 <= end:
            tag, ln = _u16(data, o), _u16(data, o + 2)
            body, body_end = o + 4, o + 4 + ln
            if body_end > end:
                break
            if tag == TAG_SETTINGS and ln >= 38:
                if data[body:body + 1] not in (b"", b"\x00"):
                    out.append((body, ln))
            elif tag in CONTAINERS and ln >= 4:
                walk(body, body_end)
            o = body_end
            if tag == 0 and ln == 0:
                break

    walk(0, len(data))
    return out


def _children(data, start, end):
    """(tag, body_offset, body_length) of the records directly inside [start, end)."""
    o = start
    while o + 4 <= end:
        tag, ln = _u16(data, o), _u16(data, o + 2)
        if o + 4 + ln > end:
            return
        yield tag, o + 4, ln
        o += 4 + ln


def _mode_place(data, blob_off):
    """The place (0-based) in the 0x0100 list of the 0x0101 mode holding this blob, or None."""
    used = region_used(data)
    for tag, body, ln in _children(data, 4, used):
        if tag != TAG_MODES:
            continue
        for k, (t, b, n) in enumerate(c for c in _children(data, body, body + ln) if c[0] == TAG_MODE):
            if b <= blob_off < b + n:
                return k
    return None


def _groups(data):
    """[(name_offset, name_length, [mode places])] for each 0x0210 group in 0x0200."""
    out = []
    used = region_used(data)
    for tag, body, ln in _children(data, 4, used):
        if tag != TAG_GROUPS:
            continue
        for t, b, n in _children(data, body, body + ln):
            if t != TAG_GROUP:
                continue
            name, members = None, []
            for t2, b2, n2 in _children(data, b, b + n):
                if t2 == TAG_GROUP_NAME:
                    name = (b2, n2)
                elif t2 == TAG_GROUP_MODE and n2 == 2:
                    members.append(_u16(data, b2))
            if name:
                out.append((name[0], name[1], members))
    return out


def _put_u16(buf, off, v):
    v = max(0, min(0xFFFF, int(round(v))))
    buf[off], buf[off + 1] = v & 0xFF, v >> 8


def _name_bytes(name, size):
    raw = str(name).encode("latin-1", "replace")[:NAME_LEN]
    return raw.ljust(size, b"\x00")


def patch_region(data, mode, patch):
    """The sport-mode region `data` (as read off the watch) with one mode's settings changed.
    `mode` is the mode's place in the readers' list (int) or its name (str, first match).
    `patch` keys, as the Android writer names them: name, autolapM, heartrateMax, heartrateMin,
    useHrLimits, useHw (the whole pods field). Every other byte is left as read; the length is
    unchanged. Raises ValueError for an unknown mode or a settings layout other than 76/90."""
    blobs = named_blobs(data)
    if isinstance(mode, str):
        names = [data[o:o + NAME_LEN].split(b"\x00")[0].decode("latin-1") for o, _ in blobs]
        if mode not in names:
            raise ValueError(f"no sport mode named {mode!r} on the watch")
        index = names.index(mode)
    else:
        index = int(mode)
        if not 0 <= index < len(blobs):
            raise ValueError(f"sport mode {index} not found (the watch has {len(blobs)})")
    blob, blen = blobs[index]
    lay = LAYOUTS.get(blen)
    if lay is None:
        raise ValueError(f"settings record of {blen} bytes: only the Ambit1 (76) and Ambit2 (90) "
                         "layouts are known")
    out = bytearray(data)
    if "autolapM" in patch:
        _put_u16(out, blob + lay["autolap"], patch["autolapM"])
    use = None if patch.get("useHrLimits") is None else bool(patch["useHrLimits"])
    if use is False:
        _put_u16(out, blob + lay["hr_max"], 0)
        _put_u16(out, blob + lay["hr_min"], 0)
    else:
        if "heartrateMax" in patch:
            _put_u16(out, blob + lay["hr_max"], patch["heartrateMax"])
        if "heartrateMin" in patch:
            _put_u16(out, blob + lay["hr_min"], patch["heartrateMin"])
    if use is not None and lay["use_hr"] is not None:
        _put_u16(out, blob + lay["use_hr"], 1 if use else 0)
    if "useHw" in patch:
        old = _u16(data, blob + lay["pods"])
        new = int(patch["useHw"]) & 0xFFFF
        if (old ^ new) & 0x0001:                     # the HR belt was switched
            new = (new | lay["hr_belt"]) if new & 0x0001 else (new & ~lay["hr_belt"])
        _put_u16(out, blob + lay["pods"], new)
    if "name" in patch:
        old_name = bytes(data[blob:blob + NAME_LEN]).split(b"\x00")[0]
        out[blob:blob + NAME_LEN] = _name_bytes(patch["name"], NAME_LEN)
        place = _mode_place(data, blob)
        for name_off, name_len, members in _groups(data):
            if (members == [place] and name_len == GROUP_NAME_LEN
                    and bytes(data[name_off:name_off + name_len]).split(b"\x00")[0] == old_name):
                out[name_off:name_off + name_len] = _name_bytes(patch["name"], GROUP_NAME_LEN)
    return bytes(out)


def read_app_modes(from_file=None):
    """Read the watch's sport modes (live, or --from a saved dump) and return them in the
    desktop app's schema. Empty list if nothing decodes (caller falls back to factory)."""
    data = pathlib.Path(from_file).read_bytes() if from_file else read_region_live()
    return [to_app_mode(m) for m in parse_region(data) if m.get("activity_name")]


def _binary_path():
    for name in ("ambit_legacy_cli", "ambit_legacy_cli.exe"):
        c = HERE / "vendor" / "ambit_legacy_cli" / name
        if c.exists():
            return c
    return None


def read_region_live(length=REGION_BYTES):
    """Dump region 0x2000 off the watch via the compiled legacy CLI's read-only
    `region-dump` (0x0b17), same binary tools/legacy_link.py already shells out to. `length`
    bytes at most (the dump stops early where the watch answers no more)."""
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
        # Cap ambit_legacy_cli's per-report read budget and give the call room to finish.
        # Its 20s default is spent in full on every command the watch never answers, so a
        # region-dump ran ~167s and blew the old 120s subprocess timeout - the exception
        # escaped _handle_legacy_sport_modes_read after it had already sent HTTP 200, so the
        # Sport Modes page got an empty body and showed nothing. Same fix legacy_link.py
        # already carries (AMBIT_READ_TIMEOUT_MS=3000 -> ~28s); do it here too and give the
        # subprocess a margin above that.
        env = os.environ.copy()
        env.setdefault("AMBIT_READ_TIMEOUT_MS", "3000")
        proc = subprocess.run(
            [str(binary), *device_args, "region-dump", hex(REGION_ADDR), path,
             str(int(length))],
            capture_output=True, timeout=90, env=env)
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
