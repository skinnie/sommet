#!/usr/bin/env python3
"""Read and edit the data screens (data-field layout) of a Garmin Edge activity profile, over
USB - no BLE, no Garmin Connect. On the Edge the screens live in the SPORT profile files
(`Garmin/Sports/Cycling*.fit`, FIT_TYPE_3), NOT in Settings.fit (confirmed against a real
Edge 1040 Solar). Each screen is a `mesg 14` record:
  * field 254 = screen index
  * field 1   = enabled (1) / unused (255)
  * field 3   = layout (number of data fields shown on the screen)
  * field 7   = u16[10] array of data-field TYPE IDs, one per slot (65535 = empty slot)
  * field 9   = page order
To change a screen you rewrite field 7 (which metrics) and/or field 3 (layout). The edit is a
byte-exact in-place patch of the existing record (field 7 is a fixed 10x u16, field 3 a single
byte, so nothing shifts), then the trailing FIT CRC is recomputed. The result is written to a
NEW file you drop into `Garmin/NewFiles/` on the Edge; the device imports it on next power-up.
The device's own GarminDevice.xml declares FIT_TYPE_3 InputToUnit = Garmin/NewFiles, so this is
Garmin's sanctioned mechanism, not a hack. The original file on disk is never modified.

DATA-FIELD IDs are Garmin's own enum (48, 6, 59, ...). A partial name map is in FIELD_NAMES
below; unknown IDs are shown raw. Copy an ID from a screen you like, or fill in the map.

    ./tools/garmin_edge_screens.py --catalog                       # list every field id + name
    ./tools/garmin_edge_screens.py Sports/CyclingRoad.fit --list
    ./tools/garmin_edge_screens.py Sports/CyclingRoad.fit --set \
        --screen 0 --fields "Speed,Power,Heart Rate,Distance" --out /tmp/CyclingRoad.fit
    # --fields takes NAMES or numeric ids; 'none' blanks a slot; layout auto-sets to the field count.
    # then copy the --out file into <Edge>/Internal Storage/Garmin/NewFiles/ and reboot the Edge

Convention (this repo): dry-run/safe by default - --list only reads; --set writes a COPY to --out
and leaves the device and the source file untouched. Back up the originals before writing to the
Edge (a copy of Garmin/Sports/ is enough)."""

import argparse
import struct
import sys

SCREEN_MESG = 14
# Edge 500 family (2009-2013 mass-storage Edges; read from a real Edge 500 fw 3.30, 2026-11-05):
# mesg 14 has NO field 7. Its slots are field 5 = enum[8] (one byte per data field), field 4 =
# u8[8] slot positions 1..8, field 3 = how many of the 8 slots the page shows, field 1 = enabled.
# Field ids are the Edge 500's own (different numbering from the u16 ids of the newer Edges).
FIELD_SLOTS_U8 = 5
FIELD_LAYOUT = 3       # number of fields on the screen
FIELD_SLOTS = 7        # u16[10] data-field type ids
FIELD_ENABLED = 1
FIELD_INDEX = 254
FIELD_ORDER = 9        # page/carousel order across ALL enabled screens (0..N); 255 = not in the carousel
FIELD_STYPE = 10       # screen-type marker: small ordinal = data screen; >= ~25 = special typed screen
FIELD_ACTIVE = 12      # 0 on a live/shown data screen; empty placeholder slots carry 1 (device skips them)
FIELD_VARIANT = 8      # arrangement variant ("A"/"B"): counts 3-7 accept 0 or 1, others only 0.
DATA_SCREEN_TYPE_MAX = 20  # threshold: stype < this => a data (custom-field) screen (clean gap 3->25)

# Arrangement (mesg14 field 8) for an ORDINARY data screen, reverse-engineered on hardware by the
# fullcarbonbike/Activity-Profile-Editor project (Edge 530, measured not derived). Field counts 3-7
# each offer an A (variant 0) and a B (variant 1) layout; all other counts have a single arrangement
# (variant 0). Named/special screens (Map/Segment/…) use their own per-type variant values and are not
# edited here. NOTE: measured on the Edge 530; a larger Edge (1040) may additionally offer a "C"
# variant or A/B at 8-10 — confirm by saving in Connect and diffing mesg14 f8 before offering more.
COUNTS_WITH_B_VARIANT = {3, 4, 5, 6, 7}

# Special (named) screen types, f10 -> display name. 25..104 from the Activity-Profile-Editor survey
# (Edge 530); 125/127/128/162 are Edge-1040-only types identified on-device via Garmin Connect
# (2026-09-30) by matching the Connect Data Screens list against the FIT page order (f9): the newer
# 1040 features Power Guide / Stamina / Solar / GroupRide.
SPECIAL_SCREEN_NAMES = {
    25: "Map", 26: "Virtual Partner", 32: "Reserved", 35: "Compass", 38: "Workout",
    44: "Elevation", 56: "Segment", 57: "GroupTrack List", 58: "eBike Metrics",
    63: "Cycling Dynamics", 74: "Lap Summary", 95: "STEPS Metrics", 104: "ClimbPro",
    125: "Power Guide", 127: "Stamina", 128: "Solar", 162: "GroupRide",
}
# Special types that hold NO editable data fields at all (device generates their whole content).
SPECIAL_NO_FIELDS = {26, 38, 57}
# Per-type editable named-screen layouts, from the Activity-Profile-Editor on-device survey (Edge 530):
# the ordered (field_count, f8_variant) states each type offers. Locked types have a single state; the
# variable ones (Map 0-2, Segment 0/2/4/6, Lap Summary 1-4) offer several. NOTE: measured on the 530 —
# the 1040 can differ (as it did by adding the C data-screen variant), so treat Segment's non-positional
# f8 (4-A=2, 4-B=1) and Map's count-0 A/B as needing an on-device confirm before relying on them.
SPECIAL_LAYOUTS = {
    25:  [(0, 0), (0, 1), (1, 0), (2, 0)],           # Map (0 = full-screen map, no fields)
    35:  [(2, 0)],                                    # Compass
    44:  [(2, 0)],                                    # Elevation
    56:  [(0, 0), (2, 0), (4, 2), (4, 1), (6, 0)],    # Segment (even counts only; 4 has two variants)
    63:  [(2, 0)],                                    # Cycling Dynamics
    74:  [(1, 0), (2, 0), (3, 0), (4, 0)],            # Lap Summary
    104: [(2, 0)],                                    # ClimbPro
    127: [(2, 0)],                                    # Stamina (1040) — 2 data fields + an effort toggle
}


def special_name(stype):
    return SPECIAL_SCREEN_NAMES.get(stype, "Screen type %d" % stype)


# ---------------------------------------------------------------------------------------------------
# Type-specific screen TOGGLES. Reverse-engineered on hardware (flip in Garmin Connect, sync, diff the
# FIT): they live in mesg 13 (the profile SETTINGS message), one field per toggle — NOT in the mesg 14
# screen record. SCREEN_TOGGLES maps a screen type (f10) -> its toggles; each toggle is a mesg-13 field
# with either on/off (bool) or a small set of choices. Add a row here as each toggle is captured.
SETTINGS_MESG = 13
SCREEN_TOGGLES = {
    127: [  # Stamina
        {"field": 99, "name": "Effort display", "kind": "choice",
         "options": [["Show Distance", 0], ["Show Time", 1]]},
    ],
    # e.g. later: 25 (Map) "Show Elevation Profile" bool, 104 (ClimbPro) "Pop-up on Climb Approach" bool …
}


def screen_toggles(stype):
    return SCREEN_TOGGLES.get(stype)


def parse_settings(b):
    """Return {field_num: (value, offset, size)} for the profile's mesg-13 settings record (1-byte
    fields decoded as ints). b is the raw FIT bytes."""
    hs = b[0]; dsz = struct.unpack_from('<I', b, 4)[0]
    p = hs; end = hs + dsz; defs = {}
    while p < end:
        rh = b[p]; p += 1
        if rh & 0x40:
            local = rh & 0xF; arch = b[p + 1]; e = '<' if arch == 0 else '>'
            gnum = struct.unpack_from(e + 'H', b, p + 2)[0]; n = b[p + 4]; p += 5
            flds = [(b[p + 3 * i], b[p + 3 * i + 1], b[p + 3 * i + 2]) for i in range(n)]; p += 3 * n
            if rh & 0x20:
                nd = b[p]; p += 1; p += 3 * nd
            defs[local] = (gnum, e, flds); continue
        local = rh & 0xF; gnum, e, flds = defs[local]; q = p
        out = {}
        for fn, sz, bt in flds:
            out[fn] = (b[q] if sz == 1 else None, q, sz)
            q += sz
        if gnum == SETTINGS_MESG:
            return out
        p = q
    return {}


def do_toggle(path, field, value, out):
    """Set one mesg-13 settings field (a screen toggle) to a value, byte-patch in place + fix CRC."""
    b = bytearray(open(path, 'rb').read())
    settings = parse_settings(b)
    if field not in settings:
        sys.exit("settings field %d not present in this profile" % field)
    _v, off, sz = settings[field]
    if sz != 1:
        sys.exit("settings field %d is %d bytes; only 1-byte toggles are supported" % (field, sz))
    b[off] = value & 0xFF
    crc = fit_crc(b[:len(b) - 2])
    struct.pack_into('<H', b, len(b) - 2, crc)
    with open(out, 'wb') as f:
        f.write(b)
    print("set settings field %d = %d -> %s. Copy into <Edge>/Garmin/NewFiles/ and reboot." % (field, value, out))


def special_count_states(stype):
    """[(count, default_f8)] — one entry per distinct field count the type offers, in device order, each
    with the FIRST f8 variant listed for that count (its "A"). None if the type isn't editable."""
    states = SPECIAL_LAYOUTS.get(stype)
    if not states:
        return None
    out, seen = [], set()
    for c, v in states:
        if c not in seen:
            out.append((c, v))
            seen.add(c)
    return out


def special_render_count(stype, f3):
    """The field count an editable special actually renders, given its stored f3 (which the device may
    leave over-range and clamp). = f3 if it's a valid count, else the largest valid count <= f3, else the
    smallest valid count."""
    states = special_count_states(stype)
    valid = [c for c, _ in states]
    if f3 in valid:
        return f3
    le = [c for c in valid if c <= f3]
    return max(le) if le else min(valid)

# Garmin Edge built-in data-field id -> name. The id->name map is drawn from the MIT-licensed
# fullcarbonbike/Activity-Profile-Editor project and cross-verified on a real Edge 1040 Solar
# (ids 0-9,13,77 confirmed via Garmin Connect / on-device). A newer/higher-end 1040 may expose a
# few extra ids not listed here. id 216 = a Connect IQ data field slot; specific CIQ fields are
# user-installed (device Garmin/Apps), not in this table.
FIELD_NAMES = {
    0: "Calories", 2: "Course Pt Dist.", 3: "Cadence", 4: "Avg Cadence",
    5: "Lap Cadence", 6: "Distance", 7: "Lap Dist.", 9: "Elevation",
    11: "Percent Grade", 12: "Heading", 13: "Heart Rate", 14: "Avg Heart Rate",
    15: "Lap HR", 16: "%Max Heart Rate", 17: "Avg %Max Heart Rate", 18: "Lap %Max HR",
    19: "%Heart Rate Reserve", 20: "Avg %HRR", 22: "Heart Rate Zone", 23: "HR Zone Graph",
    24: "Laps", 27: "Distance to Destination", 28: "Time to Destination", 29: "Distance to Next",
    30: "Time to Next", 31: "Dest. Location", 32: "Next Pt Location", 36: "Power",
    37: "Avg Power", 38: "Kilojoules", 39: "Lap Power", 40: "Max Power",
    41: "Max Lap Power", 42: "%FTP", 43: "Power Zone", 45: "Workout Step",
    48: "Speed", 49: "Avg Speed", 50: "Lap Speed", 53: "Sunrise",
    54: "Sunset", 55: "Elapsed Time", 56: "Timer", 57: "Avg Lap Time",
    58: "Lap Time", 59: "Time of Day", 60: "Total Ascent", 61: "Total Descent",
    62: "Dest. Ahead", 63: "Time Ahead", 64: "Calories to Go", 65: "Distance to Go",
    66: "Heart Rate to Go", 67: "Reps to Go", 68: "Time to Go", 77: "VAM",
    78: "Temperature", 79: "3s Power", 80: "30s Power", 81: "Normalized Power",
    82: "TSS", 83: "Intensity Factor (IF)", 84: "Last Lap Dist", 86: "Last Lap Speed",
    87: "Last Lap Time", 88: "30s VAM", 91: "Max Speed", 93: "ETA at Destination",
    94: "ETA to Next", 95: "Odometer", 96: "Battery Level", 97: "GPS Signal Strength",
    98: "Watts/kg", 99: "Aerobic Training Effect", 100: "Last Lap Power", 146: "10s Power",
    147: "Lap NP", 148: "Last Lap NP", 149: "Balance", 150: "Avg Balance",
    151: "Lap Balance", 159: "3s Balance", 160: "10s Balance", 161: "30s Balance",
    165: "Last Lap HR", 176: "Pedal Smoothness", 177: "Torque Effect", 178: "Gears",
    179: "Front Gear", 180: "Rear Gear", 181: "Gear Battery", 182: "Gear Ratio",
    199: "HR Zone 1 (time)", 200: "HR Zone 2 (time)", 201: "HR Zone 3 (time)", 202: "HR Zone 4 (time)",
    203: "HR Zone 5 (time)", 207: "Power Z1", 208: "Power Z2", 209: "Power Z3",
    210: "Power Z4", 211: "Power Z5", 212: "Power Z6", 213: "Power Z7",
    216: "CIQ Data Field", 257: "Time Standing", 258: "Lap Time Standing", 259: "Time Seated",
    260: "Lap Time Seated", 263: "Platform Center Offset", 264: "Avg PCO", 265: "Lap PCO",
    266: "Power Phase Right", 267: "Avg Right PP", 268: "Lap Right PP", 269: "Right PPP",
    270: "Avg R. Peak Pwr Phase", 271: "Lap Right PPP", 272: "Power Phase Left", 273: "Avg Left PP",
    274: "Lap Left PP", 275: "Left PPP", 276: "Avg L. Peak Pwr Phase", 277: "Lap Left PPP",
    295: "Target Power", 316: "Lights Connected", 317: "Light Battery", 318: "Beam Angle Status",
    319: "Light Mode", 320: "Perf. Conditioning", 343: "Heart Rate Graph", 344: "Speed Graph",
    345: "Cadence Graph", 346: "Power Graph", 347: "HR Bars", 348: "Speed Bars",
    349: "Cadence Bars", 350: "Power Bars", 368: "Elevation Graph", 408: "Di2 Battery",
    409: "Gear Combo", 411: "Di2 Shift Mode", 418: "Power Z8", 419: "Power Z9",
    433: "Anaerobic TE", 437: "Avg W/kg", 439: "3s W/kg", 440: "10s W/kg",
    441: "30s W/kg", 442: "Lap VAM", 443: "Avg VAM", 444: "Ascent Remaining",
    445: "Asc to Next Crs Pt", 452: "Respiration", 478: "EPOC", 486: "Grit",
    487: "Lap Grit", 488: "Flow", 489: "Lap Flow", 491: "Assist Mode",
    492: "Shifting Advice", 493: "eBike Battery", 494: "Travel Range", 495: "60s Grit",
    497: "60s Flow", 511: "Workout Comparison", 521: "Target", 522: "Duration",
    523: "Step Time",
}

# Edge 500 data-field ids (u8, mesg 14 field 5) -> on-screen name. Filled from the Edge 500 fw 3.30
# field table; an id missing here shows as "id:N" and is refused on write.
EDGE500_FIELD_NAMES = {
}

FIT_CRC = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
           0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]


def fit_crc(data):
    crc = 0
    for b in data:
        t = FIT_CRC[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ t ^ FIT_CRC[b & 0xF]
        t = FIT_CRC[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ t ^ FIT_CRC[(b >> 4) & 0xF]
    return crc & 0xFFFF


BASE = {0x00: ('B', 1), 0x01: ('b', 1), 0x02: ('B', 1), 0x83: ('h', 2), 0x84: ('H', 2),
        0x85: ('i', 4), 0x86: ('I', 4), 0x07: (None, 1), 0x88: ('f', 4), 0x89: ('d', 8),
        0x0A: ('B', 1), 0x8B: ('H', 2), 0x8C: ('I', 4), 0x0D: (None, 1), 0x8E: ('q', 8),
        0x8F: ('Q', 8), 0x90: ('Q', 8)}


def parse(path):
    """Return (raw bytes, list of screen dicts). Each screen: index, enabled, layout, layout_off,
    slots (list of ids), slots_off, slots_endian."""
    b = bytearray(open(path, 'rb').read())
    hs = b[0]
    dsz = struct.unpack_from('<I', b, 4)[0]
    p = hs
    end = hs + dsz
    defs = {}
    screens = []
    while p < end:
        rh = b[p]; p += 1
        if rh & 0x40:  # definition
            local = rh & 0xF
            arch = b[p + 1]
            e = '<' if arch == 0 else '>'
            gnum = struct.unpack_from(e + 'H', b, p + 2)[0]
            n = b[p + 4]; p += 5
            fields = [(b[p + 3 * i], b[p + 3 * i + 1], b[p + 3 * i + 2]) for i in range(n)]
            p += 3 * n
            if rh & 0x20:
                nd = b[p]; p += 1; p += 3 * nd
            defs[local] = (gnum, e, fields)
            continue
        local = rh & 0xF
        gnum, e, fields = defs[local]
        rec = {'index': None, 'enabled': None, 'enabled_off': None, 'layout': None, 'layout_off': None,
               'slots': None, 'slots_off': None, 'e': e, 'stype': None, 'stype_off': None,
               'variant': None, 'variant_off': None,
               'order': None, 'order_off': None, 'active': None, 'active_off': None,
               'slot_w': 2, 'family': 'edge'}
        for (fnum, sz, bt) in fields:
            off = p
            t = BASE.get(bt, (None, 1))[0]
            if gnum == SCREEN_MESG:
                if fnum == FIELD_INDEX and t:
                    rec['index'] = struct.unpack_from(e + t, b, off)[0]
                elif fnum == FIELD_ENABLED and t:
                    rec['enabled'] = struct.unpack_from(e + t, b, off)[0]
                    rec['enabled_off'] = off
                elif fnum == FIELD_LAYOUT and t:
                    rec['layout'] = struct.unpack_from(e + t, b, off)[0]
                    rec['layout_off'] = off
                elif fnum == FIELD_SLOTS and t == 'H':
                    k = sz // 2
                    rec['slots'] = list(struct.unpack_from(e + 'H' * k, b, off))
                    rec['slots_off'] = off
                elif fnum == FIELD_SLOTS_U8 and bt in (0x00, 0x02) and sz > 1 and rec['slots'] is None:
                    rec['slots'] = list(b[off:off + sz])
                    rec['slots_off'] = off
                    rec['slot_w'] = 1
                    rec['family'] = 'edge500'
                elif fnum == FIELD_STYPE and t:
                    # field 10 = screen-type marker. Data (custom-field) screens use small ordinals
                    # (0,1,2,3… = Screen 1,2,3,4); the special typed screens (Map/Elevation/ClimbPro/
                    # …) use codes >= ~25. Verified across all tested Edge 1040 profiles.
                    rec['stype'] = struct.unpack_from(e + t, b, off)[0]
                    rec['stype_off'] = off
                elif fnum == FIELD_VARIANT and t:
                    rec['variant'] = struct.unpack_from(e + t, b, off)[0]
                    rec['variant_off'] = off
                elif fnum == FIELD_ORDER and t:
                    rec['order'] = struct.unpack_from(e + t, b, off)[0]
                    rec['order_off'] = off
                elif fnum == FIELD_ACTIVE and t:
                    rec['active'] = struct.unpack_from(e + t, b, off)[0]
                    rec['active_off'] = off
            p += sz
        if gnum == SCREEN_MESG:
            screens.append(rec)
    return b, screens


def names_for(family):
    return EDGE500_FIELD_NAMES if family == 'edge500' else FIELD_NAMES


def name(fid, family='edge'):
    if fid in (65535, 255) and family != 'edge500':
        return "-"
    return names_for(family).get(fid, "id:%d" % fid)


def shown_slots(s):
    """The field ids a screen actually shows. Newer Edges blank unused slots (65535); the Edge 500
    keeps all 8 bytes filled and shows the first `layout` of them."""
    slots = s.get('slots') or []
    if s.get('family') == 'edge500':
        n = s.get('layout') if s.get('layout') not in (None, 255) else len(slots)
        return slots[:max(0, min(n, len(slots)))]
    return [f for f in slots if f not in (65535, 255)]


SPORT_MESG = 12
SPORT_NAME_FIELD = 3


def sport_name(path):
    """The profile's display name = SPORT (mesg 12) field 3 (a string), e.g. 'ROAD'.
    Returns '' if absent. Cheap standalone parse (used to label profiles in the UI)."""
    b = open(path, 'rb').read()
    hs = b[0]; dsz = struct.unpack_from('<I', b, 4)[0]
    p = hs; end = hs + dsz; defs = {}
    while p < end:
        rh = b[p]; p += 1
        if rh & 0x40:
            local = rh & 0xF; arch = b[p + 1]; e = '<' if arch == 0 else '>'
            gnum = struct.unpack_from(e + 'H', b, p + 2)[0]; n = b[p + 4]; p += 5
            fields = [(b[p + 3 * i], b[p + 3 * i + 1], b[p + 3 * i + 2]) for i in range(n)]
            p += 3 * n
            if rh & 0x20:
                nd = b[p]; p += 1; p += 3 * nd
            defs[local] = (gnum, e, fields); continue
        local = rh & 0xF; gnum, e, fields = defs[local]
        for (fnum, sz, bt) in fields:
            if gnum == SPORT_MESG and fnum == SPORT_NAME_FIELD and bt == 0x07:
                raw = b[p:p + sz].split(b'\x00')[0]
                return raw.decode('utf-8', 'replace').strip()
            p += sz
    return ""


def _norm(s):
    """Loose key for name matching: lowercase, drop non-alphanumerics."""
    return ''.join(c for c in s.lower() if c.isalnum())


NAME_TO_ID = {}
for _fid, _nm in FIELD_NAMES.items():
    NAME_TO_ID.setdefault(_norm(_nm), _fid)
NAME_TO_ID_500 = {}
for _fid, _nm in EDGE500_FIELD_NAMES.items():
    NAME_TO_ID_500.setdefault(_norm(_nm), _fid)


def resolve_field(token, family='edge'):
    """A --fields token -> field id. Accepts a numeric id, or a field name (case/space/punctuation
    insensitive). 'none'/'-'/'empty' -> 65535 (blank slot)."""
    t = token.strip()
    if t.lower() in ('none', 'empty', '-', ''):
        return 65535
    if t.isdigit():
        return int(t)
    fid = (NAME_TO_ID_500 if family == 'edge500' else NAME_TO_ID).get(_norm(t))
    if fid is None:
        raise SystemExit("unknown field %r - run with --catalog to see valid names/ids" % token)
    return fid


def do_catalog(as_json=False):
    if as_json:
        import json
        print(json.dumps([{"id": fid, "name": FIELD_NAMES[fid]} for fid in sorted(FIELD_NAMES)]))
        return
    print("Edge built-in data-field ids (%d). Use the name or the number in --fields." % len(FIELD_NAMES))
    for fid in sorted(FIELD_NAMES):
        print("  %4d  %s" % (fid, FIELD_NAMES[fid]))


def do_list(path, as_json=False):
    _, screens = parse(path)
    if as_json:
        import json
        out = []
        for s in screens:
            if s.get('enabled') != 1:
                continue
            fields = [{"id": f, "name": name(f, s['family'])} for f in shown_slots(s)]
            out.append({"index": s['index'], "layout": s['layout'], "fields": fields})
        print(json.dumps({"screens": out}))
        return
    shown = [s for s in screens if s.get('enabled') == 1]
    print("%s: %d screen slots, %d enabled" % (path, len(screens), len(shown)))
    for s in screens:
        if s.get('enabled') != 1:
            continue
        fields = shown_slots(s)
        print("  screen %2d  layout=%s  fields(%d): %s"
              % (s['index'], s['layout'], len(fields),
                 ", ".join("%s(%d)" % (name(f, s['family']), f) for f in fields)))


def do_set(path, screen_idx, new_fields, layout, out, variant=None):
    b, screens = parse(path)
    target = next((s for s in screens if s['index'] == screen_idx), None)
    if not target:
        sys.exit("screen %d not found" % screen_idx)
    if target['slots_off'] is None:
        sys.exit("screen %d has no editable field-7 slots" % screen_idx)
    e = target['e']
    slots = list(target['slots'])
    n = len(slots)
    if target.get('family') == 'edge500':
        _set_u8(b, target, new_fields, layout)
        crc = fit_crc(b[:len(b) - 2])
        struct.pack_into('<H', b, len(b) - 2, crc)
        with open(out, 'wb') as f:
            f.write(b)
        print("wrote %s (%d bytes, CRC fixed)" % (out, len(b)))
        return
    if new_fields is not None:
        ids = [resolve_field(x) for x in new_fields.split(',')]
        ids = [i for i in ids if i != 65535] or [65535]  # drop trailing blanks; allow all-empty
        real = [i for i in ids if i != 65535]
        if len(real) > n:
            sys.exit("too many fields (%d), screen holds %d slots" % (len(real), n))
        newslots = real + [65535] * (n - len(real))
        struct.pack_into(e + 'H' * n, b, target['slots_off'], *newslots)
        print("screen %d fields: %s -> %s"
              % (screen_idx, [name(x) for x in slots if x != 65535],
                 [name(x) for x in newslots if x != 65535]))
        # field 3 = field COUNT (verified on the Edge 1040). Default it to the number of fields set,
        # unless the caller passes --layout explicitly.
        if layout is None and target['layout_off'] is not None:
            b[target['layout_off']] = len(real) & 0xFF
            print("screen %d layout: %s -> %d (auto = field count)" % (screen_idx, target['layout'], len(real)))
    if layout is not None and target['layout_off'] is not None:
        b[target['layout_off']] = layout & 0xFF
        print("screen %d layout: %s -> %d" % (screen_idx, target['layout'], layout))
    if variant is not None and target['variant_off'] is not None:
        b[target['variant_off']] = variant & 0xFF
        print("screen %d arrangement (f8): %s -> %d" % (screen_idx, target['variant'], variant))
    # recompute trailing FIT CRC over everything except the last 2 bytes
    crc = fit_crc(b[:len(b) - 2])
    struct.pack_into('<H', b, len(b) - 2, crc)
    with open(out, 'wb') as f:
        f.write(b)
    print("wrote %s (%d bytes, CRC fixed). Copy into <Edge>/Garmin/NewFiles/ and reboot the Edge."
          % (out, len(b)))


def _set_u8(b, target, new_fields, layout):
    """Edge 500 page edit in place: the first k slots get the new ids, field 3 = k. Slots past k
    keep their old bytes (the device never stores a blank slot; it just shows `layout` of them)."""
    slots = list(target['slots'])
    n = len(slots)
    if new_fields is not None:
        ids = [resolve_field(x, 'edge500') for x in new_fields.split(',')]
        real = [i for i in ids if i != 65535]
        if not 1 <= len(real) <= n:
            sys.exit("an Edge 500 page shows 1..%d fields (got %d)" % (n, len(real)))
        if any(i > 254 for i in real):
            sys.exit("Edge 500 field ids are 0..254")
        if EDGE500_FIELD_NAMES and any(i not in EDGE500_FIELD_NAMES for i in real):
            sys.exit("unknown Edge 500 field id in %r" % real)
        slots[:len(real)] = real
        b[target['slots_off']:target['slots_off'] + n] = bytes(slots)
        if layout is None:
            layout = len(real)
    if layout is not None:
        if not 1 <= layout <= n:
            sys.exit("layout must be 1..%d" % n)
        b[target['layout_off']] = layout & 0xFF


def do_add(path, new_fields, layout, out, variant=None):
    """Add a data screen (Garmin Connect's 'Add Data Screen', generic custom-field type). The profile's
    screen table is a fixed set of records; a new data screen is created by ENABLING a slot in place
    (no record is appended, so the file size is unchanged). We give it stype = one past the current
    highest enabled data screen (its display order), the requested metrics, and a matching layout count.
    Preference order for the host slot: a spare record already TYPED as this data-screen ordinal (the
    device ships these pre-made), else any empty (enabled=255) record repurposed to this ordinal."""
    b, screens = parse(path)
    data_en = [s for s in screens if s['stype'] is not None
               and s['stype'] < DATA_SCREEN_TYPE_MAX and s['enabled'] == 1]
    new_stype = (max(s['stype'] for s in data_en) + 1) if data_en else 0
    # Prefer a pre-typed slot for this ordinal that isn't currently on.
    host = next((s for s in screens if s['stype'] == new_stype and s['enabled'] != 1
                 and s['slots_off'] is not None and s['enabled_off'] is not None), None)
    if host is None:  # else repurpose an empty slot (enabled == 255, untyped)
        host = next((s for s in screens if s['enabled'] == 255
                     and s['slots_off'] is not None and s['enabled_off'] is not None
                     and s['stype_off'] is not None), None)
    if host is None:
        sys.exit("no free screen slot available to add a data screen")
    ids = [resolve_field(x) for x in new_fields.split(',')] if new_fields else []
    real = [i for i in ids if i != 65535] or [resolve_field('Distance')]  # never create an all-blank screen
    cap = len(host['slots'])
    if len(real) > cap:
        sys.exit("too many fields (%d), a screen holds %d slots" % (len(real), cap))
    e = host['e']
    newslots = real + [65535] * (cap - len(real))
    struct.pack_into(e + 'H' * cap, b, host['slots_off'], *newslots)
    b[host['enabled_off']] = 1
    if host['stype_off'] is not None:
        b[host['stype_off']] = new_stype & 0xFF
    cnt = layout if layout is not None else len(real)
    if host['layout_off'] is not None:
        b[host['layout_off']] = cnt & 0xFF
    # arrangement: caller's variant, else "A" (0); a repurposed empty slot has f8=255 and must be reset.
    if host['variant_off'] is not None:
        b[host['variant_off']] = (variant if variant is not None else 0) & 0xFF
    # A stored-but-hidden screen (idx enabled, yet absent from the carousel) was the failure mode of a
    # first attempt: an empty slot keeps f12=1 (placeholder) and an f9 that isn't at the end of the page
    # order, so the device imports the record but never shows it. Real added data screens have f12=0 and
    # sit AFTER every other screen in the carousel. Match that: f12=0 and f9 = one past the last live page.
    if host['active_off'] is not None:
        b[host['active_off']] = 0
    if host['order_off'] is not None:
        live = [s['order'] for s in screens if s['enabled'] == 1
                and s['order'] not in (None, 255) and s is not host]
        b[host['order_off']] = ((max(live) + 1) if live else 0) & 0xFF
    crc = fit_crc(b[:len(b) - 2])
    struct.pack_into('<H', b, len(b) - 2, crc)
    with open(out, 'wb') as f:
        f.write(b)
    print("added data screen (order %d, record idx %s, %d fields, layout %d) -> %s. "
          "Copy into <Edge>/Garmin/NewFiles/ and reboot." % (new_stype, host['index'], len(real), cnt, out))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('fit', nargs='?', help="a SPORT profile .fit (omit only with --catalog)")
    ap.add_argument('--list', action='store_true', help="list the profile's screens and fields")
    ap.add_argument('--catalog', action='store_true', help="print every known field id + name")
    ap.add_argument('--set', action='store_true')
    ap.add_argument('--add', action='store_true', help="add a new data screen (enable a slot)")
    ap.add_argument('--screen', type=int)
    ap.add_argument('--fields', help="comma-separated field NAMES or ids, left-to-right "
                                     "(e.g. 'Speed,Power,Heart Rate,Distance'); 'none' = blank slot")
    ap.add_argument('--layout', type=int, help="fields-per-screen (default: count of --fields)")
    ap.add_argument('--variant', type=int, help="arrangement (mesg14 f8): 0=A, 1=B (counts 3-7 only)")
    ap.add_argument('--toggle', type=int, metavar='FIELD', help="set a mesg-13 settings toggle field")
    ap.add_argument('--value', type=int, help="value for --toggle")
    ap.add_argument('--out', help="output FIT path (required with --set)")
    ap.add_argument('--json', action='store_true', help="machine-readable output (for --list/--catalog)")
    a = ap.parse_args()
    if a.catalog:
        do_catalog(a.json)
        return
    if not a.fit:
        ap.error("a .fit path is required (or use --catalog)")
    if a.toggle is not None:
        if a.value is None or not a.out:
            ap.error("--toggle needs --value and --out")
        do_toggle(a.fit, a.toggle, a.value, a.out)
    elif a.add:
        if not a.out:
            ap.error("--add needs --out")
        do_add(a.fit, a.fields, a.layout, a.out, a.variant)
    elif a.set:
        if a.screen is None or not a.out:
            ap.error("--set needs --screen and --out")
        do_set(a.fit, a.screen, a.fields, a.layout, a.out, a.variant)
    else:
        do_list(a.fit, a.json)


if __name__ == '__main__':
    main()
