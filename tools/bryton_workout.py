#!/usr/bin/env python3
"""Decode and encode Bryton Aero 60 planned workouts (`System/Plan/Cycling/*.fit`), stdlib only.

    ./tools/bryton_workout.py decode "mhr.fit"                 # -> workout JSON on stdout
    ./tools/bryton_workout.py encode plan.json out.fit         # workout JSON -> Bryton .fit
    ./tools/bryton_workout.py selftest /media/.../Plan/Cycling # round-trip every real file, byte-exact

The Aero 60 (2018, black-and-white GPS) stores each planned workout the app builds as one tiny
standard-FIT file: a `file_id` (type 5 = workout), one `workout` message (global 26) and one
`workout_step` (global 27) per step. It is plain FIT - the same family we already read in
`fit_decode.py` and write in `legacy_export.py` - with just a few Bryton conventions, all proven
against Andre's hardware capture (a labelled test file per unit x range/target x time/distance,
2026-09-24):

**Workout-level choices** (set once for the whole workout, per the app's "Plan Workout" screen -
UNIT / BASED ON / INTERVAL - and confirmed by every step in a file sharing them):

  * unit          -> workout_step target_type (field 3):
                        speed = 0, cadence = 3, ftp = 245, lthr = 248, mhr = 249
                     (0 and 3 are stock FIT; 245/248/249 = 0xF5/F8/F9 are Bryton's own codes.)
  * interval mode -> workout_step duration_type (field 1): time = 0, distance = 1
  * based on      -> Range vs Target is just low != high vs low == high; no separate flag.

**Per-step value encoding** (custom_target_value_low/high, fields 5/6):

  * ftp / mhr / lthr : percent + 100000   (100090 == 90 %)
  * cadence          : rpm, as-is         (85 == 85 rpm)
  * speed            : mm/s, TRUNCATED     (10.0 km/h -> 2777, not 2778 - the app floors)
  * duration (field 2): time in ms (600000 == 10:00), distance in cm (500000 == 5 km)

**Intensity / step type** (field 7): work = 0, recovery = 1, warmup = 2, cooldown = 3. The type
label you see on a card ("Work", "Warm Up") comes from THIS, not from a name - `wkt_step_name`
(field 0) is always the literal default "Step Name". The app's 5th builder icon, "Interval", is a
"Repeats N times" container; the file stores it FLATTENED into plain work/recovery steps, so there
is no nested FIT repeat message to handle here (Andre's Interval Workout = 18 flat time steps).

Copy the produced .fit into the mounted device's `System/Plan/Cycling/` folder (the Aero 60 is USB
mass storage) and it appears in the watch's workout list. This module never touches the device on
its own.
"""

import argparse
import datetime
import json
import struct
import sys

# ── FIT constants (shared shape with legacy_export.py) ───────────────────────────────────────
FIT_EPOCH = 631065600                 # 1989-12-31T00:00:00Z as a Unix timestamp
PROFILE_VERSION = 2167                # exactly what the Aero 60 app writes (0x0877)
MANUFACTURER = 255                    # what the app stamps into file_id.manufacturer
FILE_TYPE_WORKOUT = 5                 # file_id.type for a workout file

_E, _U8, _U16, _U32, _STR = 0x00, 0x02, 0x84, 0x86, 0x07   # FIT base-type codes

_CRC_TABLE = [
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
]


def _fit_crc(data) -> int:
    crc = 0
    for byte in data:
        tmp = _CRC_TABLE[crc & 0x0F]
        crc = ((crc >> 4) ^ tmp ^ _CRC_TABLE[byte & 0x0F]) & 0xFFFF
        tmp = _CRC_TABLE[crc & 0x0F]
        crc = ((crc >> 4) ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0x0F]) & 0xFFFF
    return crc & 0xFFFF


# ── unit <-> target_type, and human <-> raw target value ─────────────────────────────────────
UNIT_TO_TARGET = {"speed": 0, "cadence": 3, "ftp": 245, "lthr": 248, "mhr": 249}
TARGET_TO_UNIT = {v: k for k, v in UNIT_TO_TARGET.items()}

INTENSITY_TO_CODE = {"work": 0, "recovery": 1, "warmup": 2, "cooldown": 3}
CODE_TO_INTENSITY = {v: k for k, v in INTENSITY_TO_CODE.items()}

_PCT_UNITS = {"ftp", "mhr", "lthr"}    # encoded as percent + 100000


def target_to_raw(unit: str, value: float) -> int:
    """Human target (%, rpm, or km/h) -> the uint32 the file stores."""
    if unit in _PCT_UNITS:
        return 100000 + int(round(value))
    if unit == "cadence":
        return int(round(value))
    if unit == "speed":
        return int(value / 3.6 * 1000)     # km/h -> mm/s, TRUNCATED (the app floors)
    raise ValueError(f"unknown unit {unit!r}")


def raw_to_target(unit: str, raw: int) -> float:
    """The stored uint32 -> human target (%, rpm, or km/h rounded to 0.1)."""
    if unit in _PCT_UNITS:
        return raw - 100000
    if unit == "cadence":
        return raw
    if unit == "speed":
        return round(raw * 3.6 / 1000, 1)  # mm/s -> km/h
    raise ValueError(f"unknown unit {unit!r}")


def duration_to_raw(mode: str, value: float) -> int:
    """Human duration (seconds or metres) -> the uint32 the file stores."""
    if mode == "time":
        return int(round(value * 1000))    # s  -> ms
    if mode == "distance":
        return int(round(value * 100))     # m  -> cm
    raise ValueError(f"unknown interval mode {mode!r}")


def raw_to_duration(mode: str, raw: int) -> float:
    if mode == "time":
        return raw / 1000.0                # ms -> s
    if mode == "distance":
        return raw / 100.0                 # cm -> m
    raise ValueError(f"unknown interval mode {mode!r}")


# ── decode ───────────────────────────────────────────────────────────────────────────────────
def decode(data: bytes) -> dict:
    """Parse a Bryton workout .fit into a plain dict (human units)."""
    if data[8:12] != b".FIT":
        raise ValueError("not a FIT file (missing .FIT magic)")
    hdr_sz = data[0]
    dsize = struct.unpack("<I", data[4:8])[0]
    pos, end = hdr_sz, hdr_sz + dsize
    defs = {}
    name = ""
    sport = 2
    time_created = None
    raw_steps = []          # (index, dur_type, dur_val, tgt_type, low, high, intensity)
    while pos < end:
        rh = data[pos]; pos += 1
        if rh & 0x40:                                   # definition
            local = rh & 0x0F
            arch = data[pos + 1]
            gnum = struct.unpack("<H", data[pos + 2:pos + 4])[0]
            nf = data[pos + 4]; pos += 5
            fields = []
            for _ in range(nf):
                fields.append((data[pos], data[pos + 1], data[pos + 2])); pos += 3
            defs[local] = (gnum, fields)
        else:                                           # data
            local = rh & 0x0F
            gnum, fields = defs[local]
            rec = {}
            for fnum, sz, bt in fields:
                raw = data[pos:pos + sz]; pos += sz
                base = bt & 0x1F
                if base == 0x07:                        # string
                    rec[fnum] = raw.split(b"\x00")[0].decode("utf-8", "replace")
                elif sz == 1:
                    rec[fnum] = raw[0]
                elif sz == 2:
                    rec[fnum] = struct.unpack("<H", raw)[0]
                elif sz == 4:
                    rec[fnum] = struct.unpack("<I", raw)[0]
                else:
                    rec[fnum] = raw
            if gnum == 0:                               # file_id
                time_created = rec.get(4)
            elif gnum == 26:                            # workout
                name = rec.get(8, "")
                sport = rec.get(4, 2)
            elif gnum == 27:                            # workout_step
                raw_steps.append((rec.get(254, 0), rec.get(1, 0), rec.get(2, 0),
                                  rec.get(3, 0), rec.get(5, 0), rec.get(6, 0), rec.get(7, 0)))
    raw_steps.sort(key=lambda s: s[0])
    if not raw_steps:
        raise ValueError("no workout steps found")

    unit = TARGET_TO_UNIT.get(raw_steps[0][3])
    if unit is None:
        raise ValueError(f"unknown Bryton target_type {raw_steps[0][3]}")
    mode = "time" if raw_steps[0][1] == 0 else "distance"

    steps = []
    based_on = "target"
    for _idx, dtype, dval, _ttype, low, high, inten in raw_steps:
        if low != high:
            based_on = "range"
        steps.append({
            "intensity": CODE_TO_INTENSITY.get(inten, f"code{inten}"),
            "duration": raw_to_duration("time" if dtype == 0 else "distance", dval),
            "low": raw_to_target(unit, low),
            "high": raw_to_target(unit, high),
        })
    return {
        "name": name,
        "sport": sport,
        "unit": unit,
        "based_on": based_on,
        "interval_mode": mode,
        "time_created": time_created,
        "steps": steps,
    }


# ── encode ───────────────────────────────────────────────────────────────────────────────────
def _def(b, local, gnum, fields):
    b.append(0x40 | local); b.append(0); b.append(0)
    b.extend(struct.pack("<H", gnum)); b.append(len(fields))
    for num, size, bt in fields:
        b.extend((num, size, bt))


def _fixed_str(s: str, size: int) -> bytes:
    raw = s.encode("utf-8")[:size - 1]
    return raw + b"\x00" * (size - len(raw))


def encode(workout: dict) -> bytes:
    """Build a Bryton workout .fit (bytes) from a workout dict (human units)."""
    name = workout.get("name", "Workout")
    sport = workout.get("sport", 2)
    unit = workout["unit"]
    mode = workout.get("interval_mode", "time")
    steps = workout["steps"]
    target_type = UNIT_TO_TARGET[unit]
    dur_type = 0 if mode == "time" else 1
    tc = workout.get("time_created")
    if tc is None:
        tc = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) - FIT_EPOCH

    data = bytearray()

    # file_id (local 0, global 0)
    _def(data, 0, 0, [(0, 1, _E), (1, 2, _U16), (4, 4, _U32)])
    data.append(0)                                   # data header, local 0
    data.append(FILE_TYPE_WORKOUT)
    data.extend(struct.pack("<H", MANUFACTURER))
    data.extend(struct.pack("<I", tc & 0xFFFFFFFF))

    # The app emits BOTH remaining definitions before the workout data message: workout def
    # (redefines local 0), then workout_step def (local 2), then the workout data, then steps.
    _def(data, 0, 26, [(8, 16, _STR), (6, 2, _U16), (4, 1, _E)])   # workout, global 26
    _def(data, 2, 27, [                                            # workout_step, global 27
        (0, 10, _STR), (2, 4, _U32), (4, 4, _U32), (5, 4, _U32), (6, 4, _U32),
        (254, 2, _U16), (1, 1, _E), (3, 1, _E), (7, 1, _E),
    ])

    data.append(0)                                   # workout data, local 0
    data.extend(_fixed_str(name, 16))
    data.extend(struct.pack("<H", len(steps)))
    data.append(sport)

    for i, st in enumerate(steps):
        low = target_to_raw(unit, st["low"])
        high = target_to_raw(unit, st.get("high", st["low"]))
        dval = duration_to_raw(mode, st["duration"])
        inten = INTENSITY_TO_CODE.get(st.get("intensity", "work"), 0)
        data.append(2)                               # data header, local 2
        data.extend(_fixed_str("Step Name", 10))
        data.extend(struct.pack("<I", dval))
        data.extend(struct.pack("<I", 0))            # target_value: 0, range lives in low/high
        data.extend(struct.pack("<I", low))
        data.extend(struct.pack("<I", high))
        data.extend(struct.pack("<H", i))
        data.append(dur_type)
        data.append(target_type)
        data.append(inten)

    hdr = bytearray()
    hdr.append(14); hdr.append(0x10)
    hdr.extend(struct.pack("<H", PROFILE_VERSION))
    hdr.extend(struct.pack("<I", len(data)))
    hdr.extend(b".FIT")
    hdr.extend(struct.pack("<H", _fit_crc(hdr)))
    return bytes(hdr) + bytes(data) + struct.pack("<H", _fit_crc(data))


# ── CLI ──────────────────────────────────────────────────────────────────────────────────────
def _cmd_decode(args):
    data = open(args.fit, "rb").read()
    print(json.dumps(decode(data), indent=2))
    return 0


def _cmd_encode(args):
    workout = json.load(open(args.json))
    blob = encode(workout)
    with open(args.out, "wb") as fh:
        fh.write(blob)
    print(f"wrote {args.out} ({len(blob)} bytes)")
    return 0


def _cmd_selftest(args):
    import glob, os
    files = sorted(glob.glob(os.path.join(args.dir, "*.fit")))
    if not files:
        print(f"no .fit files under {args.dir}", file=sys.stderr); return 1
    ok = bad = 0
    for path in files:
        original = open(path, "rb").read()
        try:
            wk = decode(original)
            rebuilt = encode(wk)
        except Exception as exc:                     # noqa: BLE001 - report, keep going
            print(f"  ERROR {os.path.basename(path)}: {exc}"); bad += 1; continue
        if rebuilt == original:
            print(f"  OK    {os.path.basename(path):24s} {len(wk['steps'])} steps, "
                  f"{wk['unit']}/{wk['based_on']}/{wk['interval_mode']}")
            ok += 1
        else:
            n = min(len(rebuilt), len(original))
            diff = next((i for i in range(n) if rebuilt[i] != original[i]), n)
            print(f"  DIFF  {os.path.basename(path):24s} len {len(original)}->{len(rebuilt)}, "
                  f"first byte differs at {diff}")
            bad += 1
    print(f"\n{ok} byte-exact, {bad} failed, of {len(files)} files")
    return 0 if bad == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bryton Aero 60 workout FIT decode/encode")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decode"); d.add_argument("fit"); d.set_defaults(fn=_cmd_decode)
    e = sub.add_parser("encode"); e.add_argument("json"); e.add_argument("out"); e.set_defaults(fn=_cmd_encode)
    s = sub.add_parser("selftest"); s.add_argument("dir"); s.set_defaults(fn=_cmd_selftest)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
