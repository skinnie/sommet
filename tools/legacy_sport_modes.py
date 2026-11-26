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
(read-modify-write: the region as read, with one mode's fields changed) and patch_displays() those
of a display edit; the writes themselves are legacy_link.sport_mode_edit() and
sport_mode_displays_edit(). sport_mode_groups() lists the 0x0200 groups (the watch's sport-mode
menu, multisports included). create_mode() / delete_mode() / delete_multisport() /
create_multisport() build the bytes of a create or delete as SuuntoLink makes them
(legacy_link.sport_mode_create / _delete / multisport_create write them). Android does the same in
AmbitLegacySportModes.ts.
"""

import argparse
import json
import os
import pathlib
import struct
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
#   pods     u16 bit field: belt bit 0, power 6, cadence 7, foot 8, bike 11. Bit 1 (0x0002) is
#            set whenever the belt or any pod is used, in every pods value of both captures
#            (Ambit1 0x0107 -> 0x0106 keeps it with the foot pod on; Ambit2 0x0003 -> 0x0000 ->
#            0x0003, belt only). When the belt or a pod is switched, bit 1 follows that rule on
#            every model (André 2026-11-26, Q14 a); a value nothing switched in is kept as read.
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


def named_mode_places(data):
    """The place in the 0x0100 list (what a group's 0x0214 legs count) of each named mode, in the
    order named_blobs and read_app_modes list them."""
    return [_mode_place(data, off) for off, _ln in named_blobs(data)]


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
    if use and lay["use_hr"] is None and not (_u16(out, blob + lay["hr_max"])
                                              or _u16(out, blob + lay["hr_min"])):
        raise ValueError("on the Ambit1 HR limits are on only with a max or a min above 0 "
                         "(it has no on/off field): nothing changed")
    if "useHw" in patch:
        old = _u16(data, blob + lay["pods"])
        new = int(patch["useHw"]) & 0xFFFF
        if (old ^ new) & SENSOR_BITS:                # the belt or a pod was switched
            new = (new | SENSOR_SEARCH) if new & SENSOR_BITS else (new & ~SENSOR_SEARCH)
        _put_u16(out, blob + lay["pods"], new)
    if "name" in patch:
        old_name = bytes(data[blob:blob + NAME_LEN]).split(b"\x00")[0]
        new_name = _name_bytes(patch["name"], NAME_LEN).split(b"\x00")[0]
        place = _mode_place(data, blob)
        if new_name != old_name:
            # Every later edit finds a mode by name: refuse one another mode or multisport has,
            # as create_mode and create_multisport do.
            taken = {bytes(data[o:o + NAME_LEN]).split(b"\x00")[0] for o, _ in blobs if o != blob}
            taken |= {bytes(data[no:no + nl]).split(b"\x00")[0]
                      for no, nl, members in _groups(data) if members != [place]}
            if new_name in taken:
                raise ValueError("the watch already has a sport mode or multisport named "
                                 f"{new_name.decode('latin-1')!r}")
        out[blob:blob + NAME_LEN] = _name_bytes(patch["name"], NAME_LEN)
        for name_off, name_len, members in _groups(data):
            if (members == [place] and name_len == GROUP_NAME_LEN
                    and bytes(data[name_off:name_off + name_len]).split(b"\x00")[0] == old_name):
                out[name_off:name_off + name_len] = _name_bytes(patch["name"], GROUP_NAME_LEN)
    return bytes(out)


# ─── Editing a mode's displays (2026-11-26, watch-protocol/10 L5) ─────────────────────────────
# One display, as every display in both captures holds it (and openambit's serialize_display
# writes it):
#   0x0106 { 0x0107 [u16 layout][u16 type],
#            one 0x0108 per row: { 0x0109 [u16 row][u16 item], 0x010A [u16 value]... } }
# type 0x000A is a screen the wearer set up (custom_modes.USER_DISPLAY_TYPE, the same word on the
# Ambit3); 4, 5, 6, 7, 50, 65 and 70 are the watch's own views (compass, navigation, map...). The
# user screens are always a prefix of the mode's 0x0105 list. A row whose item is 0 and which
# holds 0x010A values is the multi-value row (the values it steps through); any other row shows
# its item.
#
# What SuuntoLink wrote, each change alone between two consecutive region writes
# (tools/test_legacy_display_edit.py replays every one byte for byte, the display time in the
# mode's 0x01FF aside, left as read like the settings time):
#   add      a new display after the last user one, its rows as NEW_DISPLAY_ROWS. Ambit2 (Treino
#            Forção): 1-row, 2-row, 3-row, graph. Ambit1 (Running): graph, 3-row, and 2-row +
#            1-row + graph in one save.
#   remove   the display cut out. Ambit2: Treino Forção's graph.
#   setType  the display replaced, in place, by a new one of the other layout. Ambit1: Running's
#            3-row -> 1-row and 3-row -> 2-row.
#   setRow   the Top or Center item (Ambit2 70 -> 11, 21 -> 33; Ambit1 5 -> 68, 28 -> 10), and the
#            multi-value row's list replaced by another of a different length (Ambit2 3 -> 5
#            values, Ambit1 4 -> 5).
# Every enclosing length (0x0105, the mode's 0x0101, 0x0100, the root) moves by the size change,
# what follows shifts, and the rest of the region is 0xFF, as in every captured write. The
# display records are byte-identical on the two watches, so each change is written on all four
# models; the remove is seen only on the Ambit2 and setType only on the Ambit1.
TAG_DISPLAYS, TAG_DISPLAY, TAG_LAYOUT = 0x0105, 0x0106, 0x0107
TAG_ROW_BOX, TAG_ROW, TAG_VIEW = 0x0108, 0x0109, 0x010A
USER_DISPLAY = 0x000A
MAX_DISPLAYS = 8               # SuuntoLink getMaxDisplays() for Bluebird/Duck/Colibri/Greentit
MAX_ROW_VALUES = 5             # sportmode_rows.json maxValuesPerMultiRow; the captures' longest
# The Sport Modes page's layout names -> the id the 0x0107 record stores.
LAYOUT_IDS = {"3-row": 260, "2-row": 261, "1-row": 262, "graph": 257}
# A new display's rows, (row, item, multi-value list), as SuuntoLink wrote every display it
# created in both captures, for a new mode as for an added or retyped display.
NEW_DISPLAY_ROWS = {
    260: ((0, 10, ()), (1, 11, ()), (2, 0, (5,))),
    261: ((0, 10, ()), (1, 0, (5,))),
    262: ((0, 10, ()),),
    257: ((0, 6, ()), (1, 32, ()), (2, 5, ())),
}
ROW_NUMBERS = {"top": 0, "center": 1, "bottom": 2}


def _tlv(tag, body):
    return struct.pack("<HH", tag, len(body)) + bytes(body)


def display_record(layout, rows, kind=USER_DISPLAY):
    """One 0x0106 display: `rows` is [(row, item, [values...])]."""
    body = _tlv(TAG_LAYOUT, struct.pack("<HH", layout, kind))
    for row, item, values in rows:
        box = _tlv(TAG_ROW, struct.pack("<HH", row, item))
        box += b"".join(_tlv(TAG_VIEW, struct.pack("<H", v)) for v in values)
        body += _tlv(TAG_ROW_BOX, box)
    return _tlv(TAG_DISPLAY, body)


def parse_display(record):
    """A 0x0106 record -> {"layout", "type", "rows": [[row, item, [values]]]}, or None when it
    holds anything but the captured shape (then it is never rewritten)."""
    if len(record) < 4 or _u16(record, 0) != TAG_DISPLAY or 4 + _u16(record, 2) != len(record):
        return None
    kids = list(_children(record, 4, len(record)))
    if not kids or kids[0][0] != TAG_LAYOUT or kids[0][2] != 4:
        return None
    out = {"layout": _u16(record, kids[0][1]), "type": _u16(record, kids[0][1] + 2), "rows": []}
    for tag, body, ln in kids[1:]:
        cells = list(_children(record, body, body + ln))
        if tag != TAG_ROW_BOX or not cells or cells[0][0] != TAG_ROW or cells[0][2] != 4:
            return None
        if any(t != TAG_VIEW or n != 2 for t, _b, n in cells[1:]):
            return None
        out["rows"].append([_u16(record, cells[0][1]), _u16(record, cells[0][1] + 2),
                            [_u16(record, b) for _t, b, _n in cells[1:]]])
    if display_record(out["layout"], out["rows"], out["type"]) != bytes(record):
        return None
    return out


def _mode_records(data, mode):
    """Where a mode sits: {"modes": 0x0100 header offset, "mode": its 0x0101 header offset,
    "displays": (body offset, body length) of its 0x0105 or None}. `mode` as patch_region."""
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
    blob = blobs[index][0]
    for tag, body, ln in _children(data, 4, region_used(data)):
        if tag != TAG_MODES:
            continue
        for t, b, n in _children(data, body, body + ln):
            if t == TAG_MODE and b <= blob < b + n:
                disp = [(b2, n2) for t2, b2, n2 in _children(data, b, b + n) if t2 == TAG_DISPLAYS]
                return {"modes": body - 4, "mode": b - 4, "displays": disp[0] if disp else None}
    raise ValueError("the sport mode is not inside the region's mode list")


def _display_index(edit, user):
    try:
        i = int(edit.get("display"))
    except (TypeError, ValueError):
        raise ValueError(f"{edit.get('op')}: which display? got {edit.get('display')!r}") from None
    if not 0 <= i < len(user):
        raise ValueError(f"{edit.get('op')}: display {i} not found (the mode has {len(user)})")
    return i


def _layout_of(edit):
    layout = LAYOUT_IDS.get(str(edit.get("type", "")).strip().lower())
    if layout is None:
        raise ValueError(f"{edit.get('op')}: unknown layout {edit.get('type')!r} "
                         f"(one of {', '.join(LAYOUT_IDS)})")
    return layout


def _set_row(record, edit):
    """`record` with one row's values changed, the way SuuntoLink changed them."""
    disp = parse_display(record)
    if disp is None:
        raise ValueError("setRow: this display is not in the layout the captures show; not changed")
    row_nbr = ROW_NUMBERS.get(str(edit.get("row", "")).strip().lower())
    rows = [r for r in disp["rows"] if r[0] == row_nbr]
    if row_nbr is None or len(rows) != 1:
        raise ValueError(f"setRow: this display has no {edit.get('row')!r} row")
    try:
        values = [int(v) for v in edit.get("values") or []]
    except (TypeError, ValueError):
        raise ValueError("setRow: the values must be field numbers") from None
    if any(not 0 < v < 0xFFFE for v in values):
        raise ValueError("setRow: a value of 0 (empty) or above 0xFFFD is in no capture")
    row = rows[0]
    if row[1] == 0 and row[2]:                     # the multi-value row
        if not 1 <= len(values) <= MAX_ROW_VALUES:
            raise ValueError(f"setRow: this row holds 1 to {MAX_ROW_VALUES} values")
        row[2] = values
    elif row[1] != 0 and not row[2]:               # a one-value row
        if len(values) != 1:
            raise ValueError("setRow: this row shows one value")
        row[1] = values[0]
    else:
        raise ValueError("setRow: an empty row is in no capture; not changed")
    return display_record(disp["layout"], disp["rows"], disp["type"])


def patch_displays(data, mode, edits):
    """The sport-mode region `data` with one mode's displays edited. `mode` as patch_region;
    `edits` are the Sport Modes page's, applied in order (each display index counts the mode's
    user screens as they are after the edits before it):
      {"op": "add", "type": "3-row"|"2-row"|"1-row"|"graph"}   after the last user screen
      {"op": "remove", "display": N}                            (never the last one)
      {"op": "setType", "display": N, "type": ...}              a new display of that layout
      {"op": "setRow", "display": N, "row": "Top"|"Center"|"Bottom", "values": [field ids]}
    Every byte outside that mode's 0x0105 list is left as read, apart from the four lengths that
    enclose it and the shift. Raises ValueError for anything the captures do not show."""
    where = _mode_records(data, mode)
    if where["displays"] is None:
        raise ValueError("this sport mode has no display list")
    dbody, dlen = where["displays"]
    records = []
    for tag, body, ln in _children(data, dbody, dbody + dlen):
        if tag != TAG_DISPLAY:
            raise ValueError(f"record 0x{tag:04x} in the display list is in no capture; "
                             "nothing changed")
        records.append(bytes(data[body - 4:body + ln]))
    if sum(len(r) for r in records) != dlen:
        raise ValueError("the display list does not parse to its end; nothing changed")
    kinds = [(parse_display(r) or {}).get("type") for r in records]
    n_user = 0
    while n_user < len(kinds) and kinds[n_user] == USER_DISPLAY:
        n_user += 1
    if USER_DISPLAY in kinds[n_user:]:
        raise ValueError("this mode's own screens are not all before the built-in ones; "
                         "nothing changed")
    user, builtin = records[:n_user], records[n_user:]
    for edit in edits:
        op = edit.get("op") if isinstance(edit, dict) else None
        if op == "add":
            layout = _layout_of(edit)
            if len(user) >= MAX_DISPLAYS:
                raise ValueError(f"add: this watch holds at most {MAX_DISPLAYS} displays per mode")
            user.append(display_record(layout, NEW_DISPLAY_ROWS[layout]))
        elif op == "remove":
            i = _display_index(edit, user)
            if len(user) <= 1:
                raise ValueError("remove: a mode keeps at least one display")
            del user[i]
        elif op == "setType":
            i = _display_index(edit, user)
            layout = _layout_of(edit)
            user[i] = display_record(layout, NEW_DISPLAY_ROWS[layout])
        elif op == "setRow":
            i = _display_index(edit, user)
            user[i] = _set_row(user[i], edit)
        else:
            raise ValueError(f"unknown display edit {op!r}")
    new_body = b"".join(user + builtin)
    delta = len(new_body) - dlen
    used = region_used(data)
    out = bytearray(data[:dbody]) + new_body + bytes(data[dbody + dlen:used])
    if len(out) > len(data):
        raise ValueError(f"the sport modes would need {len(out)} of the region's {len(data)} bytes")
    for header in (dbody - 4, where["mode"], where["modes"], 0):
        _put_u16(out, header + 2, _u16(out, header + 2) + delta)
    return bytes(out) + b"\xff" * (len(data) - len(out))


def sport_mode_groups(data):
    """The region's 0x0200 groups, as tools/custom_modes.py lists an Ambit3's SPORT_MODES: one per
    entry in the watch's sport-mode menu, [{"name", "activityId", "legs", "legNames",
    "isMultisport"}]. A group with several legs is a multisport (the Ambit2 capture's Triathlon:
    Cycling, Running, Openwater swim); a leg is a place in the 0x0100 mode list. create_mode
    adds a sport mode's own entry, create_multisport a multisport's, and delete_mode /
    delete_multisport remove entries, as SuuntoLink does; a multisport is not edited here (no
    capture). The Ambit1 region has none."""
    used = region_used(data)
    places = []
    for tag, body, ln in _children(data, 4, used):
        if tag == TAG_MODES:
            for t, b, n in _children(data, body, body + ln):
                if t == TAG_MODE:
                    name = ""
                    for t2, b2, n2 in _children(data, b, b + n):
                        if t2 == TAG_SETTINGS and n2 >= NAME_LEN:
                            name = bytes(data[b2:b2 + NAME_LEN]).split(b"\x00")[0].decode("latin-1")
                    places.append(name)
    out = []
    for tag, body, ln in _children(data, 4, used):
        if tag != TAG_GROUPS:
            continue
        for t, b, n in _children(data, body, body + ln):
            if t != TAG_GROUP:
                continue
            group = {"name": "", "activityId": 0, "legs": []}
            for t2, b2, n2 in _children(data, b, b + n):
                if t2 == TAG_GROUP_NAME:
                    group["name"] = bytes(data[b2:b2 + n2]).split(b"\x00")[0].decode("latin-1")
                elif t2 == 0x0213 and n2 == 2:
                    group["activityId"] = _u16(data, b2)
                elif t2 == TAG_GROUP_MODE and n2 == 2:
                    group["legs"].append(_u16(data, b2))
            if not group["name"]:
                continue
            group["legNames"] = [places[i] if 0 <= i < len(places) else None for i in group["legs"]]
            group["isMultisport"] = len(group["legs"]) > 1
            out.append(group)
    return out


# ─── Creating and deleting a sport mode (2026-11-26, watch-protocol/10 L3) ─────────────────────
# What SuuntoLink 4.1.15 does, from its own code (ui/sport_mode_editor.js, ambit/sport_mode.js,
# ui/input.js) and from its writes in both captures:
#
#   create  SuuntoLink's showCreateSportMode: defaultSettings(new id) with the chosen sport's
#           getActivityDefaults row put in the form, then three region writes on an Ambit2 (the
#           mode's settings, its displays, its menu entry = addSportModeGroup) and two on an
#           Ambit1 (no groups: `Variant === AMBIT` skips the menu entry). Captured four times:
#           Ambit1 Yoga (10), Adventure Racing (61), Alpine Skiing (20) in zzambit1full.pcap
#           (writes 0-6), Ambit2 Adventure Racing in ambit2_suuntolink_settings_sportmodes.pcap
#           (writes 2-5). Each new mode is appended to 0x0100 as
#             0x0101 { 0x0102 settings, 0x01FF [u32 settings time][u32 display time],
#                      0x0105 the same seven displays every time (NEW_MODE_DISPLAYS) }
#           and, on the Ambit2, its menu entry appended to 0x0200 as
#             0x0210 { 0x0212 name (24), 0x0213 sport, 0x0214 its place in 0x0100,
#                      0x02FE [u32 entry id], 0x02FF [u32 time] }.
#           Sommet writes the end state in one write (as the rename in patch_region does).
#           The settings record is zero apart from: the name, the sport, the pods, alti-baro,
#           GPS and recording interval from the sport's defaults, the interval timer's 99
#           repetitions (defaultSettings) and, Ambit2 only, the mode id (getNextModeId: the
#           lowest free one, 1 in the capture) at 18, backlight and display 0x00FF (null) at
#           84/86. The entry id is getNextGroupId (lowest free: 9 in the capture).
#   delete  SuuntoLink deletes a menu entry (deleteSportModeGroup). It refuses the last one, a
#           mode with no entry of its own (the factory Transition) and a mode a multisport uses
#           ("TXT_SPORT_MODE_NOT_DELETABLE"). Deleting a multisport also drops the modes only it
#           used: Ambit2 write 0->1 (Triathlon and its Transition legs; the remaining entries are
#           untouched, ids included). The Ambit3 capture of the same program
#           (removeandaddsportsmodeandmultisport, saves 3->4, tools/sport_mode_manage.py) shows
#           the same delete with the dropped mode in the MIDDLE of the list: the mode order is
#           kept and every leg above it moves down by one. A deleted single mode takes its menu
#           entry with it; removing a captured create gives back the write before it, byte for
#           byte, on both watches.
# The limit is SuuntoLink's countSportModes: max(single entries + modes only a multisport uses,
# all entries), 10 on all four models (getMaxSportModes). Not captured on the 2S or 2R: those use
# the Ambit2's record format, with SuuntoLink's per-model form (no alti-baro choice on either, no
# bike/power/cadence pod on the 2R).
TAG_STAMP = 0x01FF
TAG_GROUP_ACTIVITY, TAG_GROUP_ID, TAG_GROUP_STAMP = 0x0213, 0x02FE, 0x02FF
MAX_SPORT_MODES = 10                       # sport_mode.js getMaxSportModes, all four models
# The four models and their settings record (the Ambit1's 76 bytes, the Ambit2 family's 90).
LEGACY_VARIANTS = {"Bluebird": AMBIT1_BLOB_LEN, "Duck": AMBIT2_BLOB_LEN,
                   "Colibri": AMBIT2_BLOB_LEN, "Greentit": AMBIT2_BLOB_LEN}
# ui/input.js movescountActivity: the sports SuuntoLink's "create sport mode" list offers.
SELECTABLE_ACTIVITIES = frozenset((
    1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26,
    27, 28, 29, 30, 31, 33, 34, 35, 36, 37, 38, 39, 40, 58, 61, 62, 63, 64, 65, 66, 67, 68, 69,
    70, 71, 72, 73, 74, 75, 76, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93,
    94, 95, 96, 97, 98, 99))
# sport_mode.js getFusedSpeedActivities (the same eight on all four): useAccelerometer() is true
# for these, and SuuntoLink puts it in the pods field as 0x0004 (its own reader, USEHW bit 2; set
# in every SuuntoLink save of the Ambit1's Running in zzambit1full.pcap, and on every factory
# Running mode).
FUSED_SPEED_ACTIVITIES = frozenset((3, 25, 28, 40, 75, 81, 82, 93))
SWIM_ACTIVITIES = frozenset((6, 83))       # activity.js PoolSwimming, OpenwaterSwimming
# Which of the form's inputs SuuntoLink shows per model (sport_mode.js supports*).
_SUPPORTS = {
    "Bluebird": {"alti": True, "foot": True, "bike": True, "power": True, "cadence": True},
    "Duck": {"alti": True, "foot": True, "bike": True, "power": True, "cadence": True},
    "Colibri": {"alti": False, "foot": True, "bike": True, "power": True, "cadence": True},
    "Greentit": {"alti": False, "foot": True, "bike": False, "power": False, "cadence": False},
}
SENSOR_SEARCH = 0x0002   # set with any pod or the belt: every pods value in both captures
SENSOR_BITS = 0x0001 | 0x0040 | 0x0080 | 0x0100 | 0x0800   # belt, power, cadence, foot, bike
NEW_MODE_DISPLAYS = b"".join(display_record(layout, rows, kind) for layout, kind, rows in (
    (260, 10, ((0, 10, ()), (1, 11, ()), (2, 0, (5,)))),
    (273, 4, ((0, 8, ()), (1, 8, ()), (2, 0, (16, 1, 65534)))),
    (291, 5, ((0, 8, ()), (1, 40, ()), (2, 0, (16, 8, 1, 65534)))),
    (290, 6, ((0, 24, ()), (1, 25, ()), (2, 0, (50, 26, 16)))),
    (257, 65, ((1, 195, ()), (2, 196, ()), (3, 197, ()))),
    (336, 7, ()),
    (260, 50, ((0, 62, ()), (1, 61, ()), (2, 0, (5, 10, 21, 11, 28)))),
))
ROWS_JSON = HERE.parent / "assets" / "sportmode_rows.json"


def activity_defaults(variant, activity_id, rows=None):
    """SuuntoLink's getActivityDefaults row for this sport on this model (assets/
    sportmode_rows.json, generated from SuuntoLink)."""
    if rows is None:
        rows = json.loads(ROWS_JSON.read_text())
    d = rows.get("activityDefaults", {}).get(variant, {}).get(str(int(activity_id)))
    if d is None:
        raise ValueError(f"SuuntoLink has no defaults for sport {activity_id} on the {variant}")
    return d


def new_mode_fields(variant, activity_id, rows=None):
    """The settings SuuntoLink's create form saves for a new mode of this sport on this model:
    the sport's defaults as activityChanged() puts them in the form, then what updateSettings()
    reads back. On the Ambit2 family a swim locks the belt and pods off and hides alti-baro
    (an unset radio reads as 0); a model without the alti-baro choice reads 0 too."""
    sup = _SUPPORTS[variant]
    d = activity_defaults(variant, activity_id, rows)
    locked = variant != "Bluebird" and int(activity_id) in SWIM_ACTIVITIES
    hr = bool(d["hrBelt"]) and not locked
    pods = 0x0001 if hr else 0
    for key, bit, sup_key in (("powerPod", 0x0040, "power"), ("cadencePod", 0x0080, "cadence"),
                              ("footPod", 0x0100, "foot"), ("bikePod", 0x0800, "bike")):
        if d[key] and sup[sup_key] and not locked:
            pods |= bit
    if pods:
        pods |= SENSOR_SEARCH
    if int(activity_id) in FUSED_SPEED_ACTIVITIES:
        pods |= 0x0004
    return {"pods": pods, "alti": d["altiBaroProfile"] if sup["alti"] and not locked else 0,
            "gps": d["gpsInterval"], "rec": d["recordingInterval"]}


def new_mode_settings(variant, name, activity_id, mode_id=1, rows=None):
    """The 0x0102 settings record of a new mode (76 bytes on the Ambit1, 90 on the Ambit2)."""
    f = new_mode_fields(variant, activity_id, rows)
    blen = LEGACY_VARIANTS[variant]
    lay = {AMBIT1_BLOB_LEN: (18, 20, 22, 24, 38), AMBIT2_BLOB_LEN: (22, 24, 26, 28, 46)}[blen]
    out = bytearray(blen)
    out[0:NAME_LEN] = _name_bytes(name, NAME_LEN)
    _put_u16(out, 16, activity_id)
    for off, value in zip(lay, (f["pods"], f["alti"], f["gps"], f["rec"], 99)):
        _put_u16(out, off, value)
    if blen == AMBIT2_BLOB_LEN:
        struct.pack_into("<I", out, 18, mode_id)          # CMIDLOW / CMIDHIGH
        _put_u16(out, 84, 0x00FF)                         # BacklightMode null
        _put_u16(out, 86, 0x00FF)                         # DisplayInvert null
    return bytes(out)


def _mode_list(data):
    """[(header offset, record length, name, settings length)] of every 0x0101 in 0x0100, in
    place order, and the 0x0100 header offset."""
    used = region_used(data)
    for tag, body, ln in _children(data, 4, used):
        if tag != TAG_MODES:
            continue
        out = []
        for t, b, n in _children(data, body, body + ln):
            if t != TAG_MODE:
                continue
            name, blen = "", 0
            for t2, b2, n2 in _children(data, b, b + n):
                if t2 == TAG_SETTINGS:
                    blen = n2
                    if n2 >= NAME_LEN:
                        name = bytes(data[b2:b2 + NAME_LEN]).split(b"\x00")[0].decode("latin-1")
            out.append((b - 4, n + 4, name, blen))
        return body - 4, out
    raise ValueError("the sport-mode region has no mode list")


def _group_list(data):
    """(0x0200 header offset or None, [{"at", "len", "name", "legs", "id"}]) for each 0x0210."""
    used = region_used(data)
    for tag, body, ln in _children(data, 4, used):
        if tag != TAG_GROUPS:
            continue
        out = []
        for t, b, n in _children(data, body, body + ln):
            if t != TAG_GROUP:
                continue
            g = {"at": b - 4, "len": n + 4, "name": "", "legs": [], "legs_at": [], "id": None}
            for t2, b2, n2 in _children(data, b, b + n):
                if t2 == TAG_GROUP_NAME:
                    g["name"] = bytes(data[b2:b2 + n2]).split(b"\x00")[0].decode("latin-1")
                elif t2 == TAG_GROUP_MODE and n2 == 2:
                    g["legs"].append(_u16(data, b2))
                    g["legs_at"].append(b2)
                elif t2 == TAG_GROUP_ID and n2 == 4:
                    g["id"] = struct.unpack_from("<I", data, b2)[0]
            out.append(g)
        return body - 4, out
    return None, []


def usage(data):
    """SuuntoLink's countSportModes for this region: {"used", "max", "multisport"}. Ambit1 (no
    menu entries in its region): every mode counts."""
    _modes_at, modes = _mode_list(data)
    groups_at, groups = _group_list(data)
    if groups_at is None:
        return {"used": sum(1 for m in modes if m[2]), "max": MAX_SPORT_MODES, "multisport": 0}
    singles = [g for g in groups if len(g["legs"]) == 1]
    own = {g["legs"][0] for g in singles}
    hidden = {leg for g in groups if len(g["legs"]) > 1 for leg in g["legs"]} - own
    return {"used": max(len(singles) + len(hidden), len(groups)), "max": MAX_SPORT_MODES,
            "multisport": len(groups) - len(singles)}


def _lowest_free(used):
    n = 1
    while n in used:
        n += 1
    return n


def _rewrite(data, cuts, inserts):
    """`data` with byte ranges cut and blobs inserted, every enclosing length fixed and the rest
    of the region 0xFF. `cuts` [(offset, length)], `inserts` [(offset, bytes, [header offsets
    of the records it goes inside])]; a cut's enclosing records are found from the tree."""
    used = region_used(data)
    edits = []                                      # (offset, removed length, added bytes, headers)
    for off, ln in cuts:
        edits.append((off, ln, b"", _enclosing(data, off)))
    for off, blob, headers in inserts:
        edits.append((off, 0, bytes(blob), headers))
    out = bytearray(data[:used])
    lengths = {}
    for off, removed, added, headers in edits:
        for h in headers:
            lengths[h] = lengths.get(h, 0) + len(added) - removed
    for h, delta in lengths.items():
        _put_u16(out, h + 2, _u16(out, h + 2) + delta)
    for off, removed, added, _h in sorted(edits, key=lambda e: e[0], reverse=True):
        out[off:off + removed] = added
    if len(out) > len(data):
        raise ValueError(f"the sport modes would need {len(out)} of the region's {len(data)} bytes")
    return bytes(out) + b"\xff" * (len(data) - len(out))


def _enclosing(data, off):
    """Header offsets of the records that strictly contain the record starting at `off`."""
    out, start, end = [], 0, region_used(data)
    while True:
        hit = None
        for tag, body, ln in _children(data, start, end):
            if body - 4 < off < body + ln and tag in CONTAINERS:
                hit = (body - 4, body, ln)
        if hit is None:
            return out
        out.append(hit[0])
        start, end = hit[1], hit[1] + hit[2]


def _check_family(data, variant):
    if variant not in LEGACY_VARIANTS:
        raise ValueError(f"{variant!r} is not an Ambit1 or Ambit2 (Bluebird, Duck, Colibri, Greentit)")
    lens = {blen for _h, _n, name, blen in _mode_list(data)[1] if name}
    if lens and lens != {LEGACY_VARIANTS[variant]}:
        raise ValueError(f"the region's settings records ({sorted(lens)} bytes) are not the "
                         f"{variant}'s {LEGACY_VARIANTS[variant]}: nothing changed")


def create_mode(data, name, activity_id, variant, now=None, rows=None):
    """The region `data` with one new sport mode, as SuuntoLink creates one (see above).
    `now` is a Unix time, or (settings time, display time, menu-entry time) as SuuntoLink's
    three writes stamp them. Raises ValueError for anything SuuntoLink would refuse."""
    import time                                             # noqa: PLC0415
    _check_family(data, variant)
    raw = str(name or "").strip()
    if not raw:
        raise ValueError("a sport mode needs a name")
    if len(raw.encode("latin-1", "replace")) > NAME_LEN:
        raise ValueError(f"a sport mode name holds {NAME_LEN} characters on this watch")
    modes_at, modes = _mode_list(data)
    if raw in {m[2] for m in modes}:
        raise ValueError(f"the watch already has a sport mode named {raw!r}")
    activity_id = int(activity_id)
    if activity_id not in SELECTABLE_ACTIVITIES:
        raise ValueError(f"sport {activity_id} is not one SuuntoLink offers for a sport mode")
    use = usage(data)
    if use["used"] >= use["max"]:
        raise ValueError(f"{use['used']}/{use['max']} sport modes used: delete one first")
    if now is None:
        now = int(time.time())
    t_set, t_disp, t_menu = (now, now, now) if isinstance(now, int) else tuple(int(x) for x in now)
    ids = set()
    for at, ln, _name, blen in modes:
        for t2, b2, n2 in _children(data, at + 4, at + ln):
            if t2 == TAG_SETTINGS and n2 == AMBIT2_BLOB_LEN:
                ids.add(struct.unpack_from("<I", data, b2 + 18)[0])
    settings = new_mode_settings(variant, raw, activity_id, _lowest_free(ids), rows)
    record = _tlv(TAG_MODE, _tlv(TAG_SETTINGS, settings)
                  + _tlv(TAG_STAMP, struct.pack("<II", t_set, t_disp))
                  + _tlv(TAG_DISPLAYS, NEW_MODE_DISPLAYS))
    modes_end = modes_at + 4 + _u16(data, modes_at + 2)
    inserts = [(modes_end, record, [modes_at, 0])]
    groups_at, groups = _group_list(data)
    if variant != "Bluebird":
        if groups_at is None:
            raise ValueError("this Ambit2 region has no sport-mode menu (0x0200): nothing changed")
        entry = _tlv(TAG_GROUP, _tlv(TAG_GROUP_NAME, _name_bytes(raw, GROUP_NAME_LEN))
                     + _tlv(TAG_GROUP_ACTIVITY, struct.pack("<H", activity_id))
                     + _tlv(TAG_GROUP_MODE, struct.pack("<H", len(modes)))
                     + _tlv(TAG_GROUP_ID, struct.pack("<I", _lowest_free({g["id"] for g in groups})))
                     + _tlv(TAG_GROUP_STAMP, struct.pack("<I", t_menu)))
        inserts.append((groups_at + 4 + _u16(data, groups_at + 2), entry, [groups_at, 0]))
    return _rewrite(data, [], inserts)


def _delete(data, groups_gone, places_gone):
    """The region without those menu entries and those modes; every leg above a removed mode
    moves down, as SuuntoLink renumbers them (see above)."""
    _modes_at, modes = _mode_list(data)
    _groups_at, groups = _group_list(data)
    cuts = [(modes[p][0], modes[p][1]) for p in places_gone]
    cuts += [(g["at"], g["len"]) for g in groups_gone]
    out = bytearray(data)
    for g in groups:
        if any(g is x for x in groups_gone):
            continue
        for leg, at in zip(g["legs"], g["legs_at"]):
            _put_u16(out, at, leg - sum(1 for p in places_gone if p < leg))
    return _rewrite(bytes(out), cuts, [])


def delete_mode(data, name, variant):
    """The region without one sport mode (and its menu entry on the Ambit2), with SuuntoLink's
    refusals: the last entry, a mode a multisport uses, a mode with no entry of its own."""
    _check_family(data, variant)
    _modes_at, modes = _mode_list(data)
    places = [i for i, m in enumerate(modes) if m[2] == name]
    if not places:
        raise ValueError(f"no sport mode named {name!r} on the watch")
    place = places[0]
    groups_at, groups = _group_list(data)
    if groups_at is None:
        if sum(1 for m in modes if m[2]) <= 1:
            raise ValueError("the watch keeps at least one sport mode")
        return _delete(data, [], [place])
    users = [g["name"] for g in groups if len(g["legs"]) > 1 and place in g["legs"]]
    if users:
        raise ValueError(f"{name!r} is used by the multisport {', '.join(users)}: "
                         "delete that first")
    own = [g for g in groups if g["legs"] == [place]]
    if not own:
        raise ValueError(f"{name!r} has no entry of its own in the watch menu: SuuntoLink does "
                         "not delete it")
    if len(groups) - len(own) < 1:
        raise ValueError("the watch keeps at least one sport mode")
    return _delete(data, own, [place])


def delete_multisport(data, name, variant):
    """The region without one multisport entry and the modes only it used (Ambit2 capture:
    Triathlon and its Transition)."""
    _check_family(data, variant)
    groups_at, groups = _group_list(data)
    multi = [g for g in groups if g["name"] == name and len(g["legs"]) > 1]
    if not multi:
        raise ValueError(f"no multisport named {name!r} on the watch")
    gone = multi[0]
    if len(groups) <= 1:
        raise ValueError("the watch keeps at least one sport mode")
    still = {leg for g in groups if g is not gone for leg in g["legs"]}
    return _delete(data, [gone], sorted(set(gone["legs"]) - still))


# ─── Creating a multisport (2026-11-26, watch-protocol/10 L2) ───────────────────────────────────
# SuuntoLink's showCreateMultisportMode (ui/sport_mode_editor.js): a name (getMaxNameLength, 16 on
# this family), one of getMultisportPhrases' three sports (Multisport 2, Triathlon 19, Adventure
# racing 61) and 2 to 6 legs (the form starts with two, MAX_MULTISPORT_SPORT_MODES = 6), each picked
# from showSelectSportMode's list: the modes with a menu entry of their own and the modes only a
# multisport uses (hiddenModes, the factory Transition). Repeats are allowed. Saved with one
# addSportModeGroup(getNextGroupId, {ActivityID, Name, CustomModeIDs}). The editor offers it while
# fewer than MAX_MULTISPORT_MODES = 2 multisports exist and countSportModes is under 10, and only on
# the models whose capability record has supportsMultisportModes (Duck, Colibri).
#
# Captured once: ambit2_suuntolink_settings_sportmodes.pcap write 1 -> 2, the Triathlon made right
# after the old one was deleted. Nothing else changes: one entry appended to 0x0200,
#     0x0210 { 0x0212 name (24), 0x0213 sport, one 0x0214 per leg (its place in 0x0100), in the
#              order picked, 0x02FE the lowest free entry id (1 there), 0x02FF the save time }
# and the 0x0200 and root lengths grow by its size. Deleting it again (delete_multisport, its legs
# all having entries of their own) gives write 1 back. Not captured on the 2S (same record format).
# Editing an existing multisport is in no capture (the Triathlon is never changed after its create
# in the 159 writes), so it is not written here.
MAX_MULTISPORT_MODES = 2                   # sport_mode_editor.js MAX_MULTISPORT_MODES
MIN_MULTISPORT_LEGS, MAX_MULTISPORT_LEGS = 2, 6
MULTISPORT_ACTIVITIES = (2, 19, 61)        # activity.js getMultisportPhrases
MULTISPORT_VARIANTS = frozenset(("Duck", "Colibri"))   # sport_mode.js supportsMultisportModes


def multisport_leg_choices(data):
    """The modes SuuntoLink's leg picker offers, by name in list order: those with a menu entry of
    their own, then those only a multisport uses."""
    _modes_at, modes = _mode_list(data)
    _groups_at, groups = _group_list(data)
    own = {g["legs"][0] for g in groups if len(g["legs"]) == 1}
    used = {leg for g in groups if len(g["legs"]) > 1 for leg in g["legs"]}
    places = [p for p in range(len(modes)) if p in own] + \
             [p for p in range(len(modes)) if p in used - own]
    return [modes[p][2] for p in places if modes[p][2]]


def create_multisport(data, name, activity_id, legs, variant, now=None):
    """The region `data` with one new multisport, as SuuntoLink's editor makes one (see above).
    `legs` are mode names in the order the watch steps through them. Raises ValueError for anything
    SuuntoLink would refuse or no capture shows."""
    import time                                             # noqa: PLC0415
    _check_family(data, variant)
    if variant not in MULTISPORT_VARIANTS:
        raise ValueError(f"the {variant} has no multisport modes (SuuntoLink's capability record)")
    raw = str(name or "").strip()
    if not raw:
        raise ValueError("a multisport needs a name")
    if len(raw.encode("latin-1", "replace")) > NAME_LEN:
        raise ValueError(f"a multisport name holds {NAME_LEN} characters on this watch")
    activity_id = int(activity_id)
    if activity_id not in MULTISPORT_ACTIVITIES:
        raise ValueError(f"sport {activity_id} cannot hold several sports; pick Multisport (2), "
                         "Triathlon (19) or Adventure racing (61)")
    groups_at, groups = _group_list(data)
    if groups_at is None:
        raise ValueError("this Ambit2 region has no sport-mode menu (0x0200): nothing changed")
    _modes_at, modes = _mode_list(data)
    if raw in {g["name"] for g in groups} | {m[2] for m in modes}:
        raise ValueError(f"the watch already has a sport mode or multisport named {raw!r}")
    use = usage(data)
    if use["multisport"] >= MAX_MULTISPORT_MODES:
        raise ValueError(f"{use['multisport']}/{MAX_MULTISPORT_MODES} multisport modes used: "
                         "delete one first")
    if use["used"] >= use["max"]:
        raise ValueError(f"{use['used']}/{use['max']} sport modes used: delete one first")
    legs = [str(x) for x in (legs or [])]
    if not MIN_MULTISPORT_LEGS <= len(legs) <= MAX_MULTISPORT_LEGS:
        raise ValueError(f"a multisport takes {MIN_MULTISPORT_LEGS} to {MAX_MULTISPORT_LEGS} sport "
                         f"modes, not {len(legs)}")
    choices = multisport_leg_choices(data)
    names = [m[2] for m in modes]
    places = []
    for leg in legs:
        if leg not in choices:
            raise ValueError(f"{leg!r} is not a sport mode SuuntoLink offers as a leg")
        places.append(names.index(leg))
    stamp = int(time.time()) if now is None else int(now)
    body = (_tlv(TAG_GROUP_NAME, _name_bytes(raw, GROUP_NAME_LEN))
            + _tlv(TAG_GROUP_ACTIVITY, struct.pack("<H", activity_id))
            + b"".join(_tlv(TAG_GROUP_MODE, struct.pack("<H", p)) for p in places)
            + _tlv(TAG_GROUP_ID, struct.pack("<I", _lowest_free({g["id"] for g in groups})))
            + _tlv(TAG_GROUP_STAMP, struct.pack("<I", stamp)))
    end = groups_at + 4 + _u16(data, groups_at + 2)
    return _rewrite(data, [], [(end, _tlv(TAG_GROUP, body), [groups_at, 0])])


def menu(data, variant):
    """What the apps show and may delete: {"used", "max", "multisport", "modes": [{"name",
    "activityId", "deletable", "reason"}], "multisports": [{"name", "activityId", "legNames",
    "deletable", "reason"}], "legChoices": [names]}. `reason` says why one may not be deleted;
    `legChoices` are the modes a new multisport may use (multisport_leg_choices)."""
    use = usage(data)
    out = dict(use, modes=[], multisports=[], legChoices=multisport_leg_choices(data))
    for name, place in zip([data[o:o + NAME_LEN].split(b"\x00")[0].decode("latin-1")
                            for o, _ in named_blobs(data)], named_mode_places(data)):
        try:
            delete_mode(data, name, variant)
            reason = ""
        except ValueError as exc:
            reason = str(exc)
        out["modes"].append({"name": name, "place": place, "deletable": not reason,
                             "reason": reason})
    for g in sport_mode_groups(data):
        if not g["isMultisport"]:
            continue
        try:
            delete_multisport(data, g["name"], variant)
            reason = ""
        except ValueError as exc:
            reason = str(exc)
        out["multisports"].append({"name": g["name"], "activityId": g["activityId"],
                                   "legNames": g["legNames"], "deletable": not reason,
                                   "reason": reason})
    return out


# The display rows that show a Suunto App (custom_modes.FIELD_TYPES FT_RULE_ENGINE_*), and the
# record an Ambit1 mode links its app with (0x010C, seen on the capture's Cycling and Alpine
# Skiing, which show app 1).
APP_FIELDS = frozenset((0x0033, 0x0034, 0x0035, 0x006B, 0x006C, 0x006E, 0x0070))
TAG_APP_LINK = 0x010C


def app_links(data):
    """Names of the modes whose displays show a Suunto App or that link one: their region cannot
    be copied to another watch without its apps."""
    out = set()
    for at, ln, name, _blen in _mode_list(data)[1]:
        for tag, body, n in _children(data, at + 4, at + ln):
            if tag == TAG_APP_LINK:
                out.add(name)
            elif tag == TAG_DISPLAYS:
                for t2, b2, n2 in _children(data, body, body + n):
                    disp = parse_display(data[b2 - 4:b2 + n2]) if t2 == TAG_DISPLAY else None
                    for row in (disp or {}).get("rows", []):
                        if row[1] in APP_FIELDS or APP_FIELDS & set(row[2]):
                            out.add(name)
    return out


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
