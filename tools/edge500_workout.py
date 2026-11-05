#!/usr/bin/env python3
"""Turn a workout in this project's schema into a standard FIT workout for a mass-storage Garmin
Edge (Edge 500 & co.) and drop it in Garmin/NewFiles, stdlib only.

    ./tools/edge500_workout.py encode plan.json out.fit
    ./tools/edge500_workout.py send   plan.json /media/<user>/GARMIN

Input schema: the one tools/bryton_from_intervals.py documents (steps with type/duration/target,
targets ABSOLUTE: watts, bpm, m/s, rpm; repeatStart(N) ... repeatEnd).

Output: FIT file type 5 (workout), the format the Edge itself stores in Garmin/Workouts/ and
lists in GarminDevice.xml as FIT_TYPE_5 InputToUnit via Garmin/NewFiles (max 50 workouts).
Unlike the Bryton, Garmin targets are absolute, so no FTP/HR conversion is needed:
  * workout (26): 4 sport = 2 cycling · 6 num_valid_steps · 8 wkt_name
  * workout_step (27): 254 index · 1 duration_type (0 time ms, 1 distance cm, 5 open,
    6 repeat-until-steps-complete) · 2 duration_value · 3 target_type (0 speed, 1 HR,
    2 open, 3 cadence, 4 power) · 4 target_value (0 = custom range; repeat count on a repeat
    step) · 5/6 custom low/high (HR bpm+100, power W+1000, speed mm/s, cadence rpm) ·
    7 intensity (0 active, 1 rest, 2 warmup, 3 cooldown)
Repeats stay repeats (one repeat step pointing back to the block start), as Garmin stores them.
"""

import argparse
import json
import os
import re
import struct
import sys

_CRC = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
        0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]
NAME_LEN = 16
MAX_STEPS = 50
NEWFILES_REL = os.path.join("Garmin", "NewFiles")
U32_INVALID = 0xFFFFFFFF

INTENSITY = {"warmup": 2, "cooldown": 3, "recovery": 1, "rest": 1}
TARGET = {"power": 4, "hr": 1, "speed": 0, "pace": 0, "cadence": 3}


def fit_crc(data, crc=0):
    for b in data:
        t = _CRC[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ t ^ _CRC[b & 0xF]
        t = _CRC[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ t ^ _CRC[(b >> 4) & 0xF]
    return crc & 0xFFFF


def _step(st):
    """A leaf step -> (duration_type, duration_value, target_type, target_value, lo, hi, intensity)."""
    dur = st.get("duration") or {}
    dn, dv = dur.get("durationName"), dur.get("value")
    if dn == "time" and dv:
        dtype, dval = 0, int(round(float(dv) * 1000))
    elif dn == "distance" and dv:
        dtype, dval = 1, int(round(float(dv) * 100))
    else:
        dtype, dval = 5, U32_INVALID                       # open: ends on the lap button
    tg = st.get("target") or {}
    vr = tg.get("valueRange") or {}
    ttype, lo, hi = 2, U32_INVALID, U32_INVALID
    if tg.get("targetName") in TARGET and vr.get("min") is not None and vr.get("max") is not None:
        a, b = sorted((float(vr["min"]), float(vr["max"])))
        name = tg["targetName"]
        ttype = TARGET[name]
        if name == "power":
            lo, hi = int(round(a)) + 1000, int(round(b)) + 1000
        elif name == "hr":
            lo, hi = int(round(a)) + 100, int(round(b)) + 100
        elif name in ("speed", "pace"):
            lo, hi = int(round(a * 1000)), int(round(b * 1000))
        else:
            lo, hi = int(round(a)), int(round(b))
    inten = INTENSITY.get((st.get("type") or {}).get("typeName"), 0)
    return dtype, dval, ttype, 0 if ttype != 2 else U32_INVALID, lo, hi, inten


def steps_of(workout):
    """Schema steps -> FIT step tuples, repeats as Garmin repeat steps."""
    out, starts = [], []
    for st in workout.get("steps") or []:
        tn = (st.get("type") or {}).get("typeName")
        if tn == "repeatStart":
            starts.append((len(out), int(st["type"].get("value", 1))))
        elif tn == "repeatEnd":
            if not starts:
                raise ValueError("repeatEnd without repeatStart")
            first, count = starts.pop()
            out.append((6, first, 0xFF, max(1, count), U32_INVALID, U32_INVALID, 0xFF))
        else:
            out.append(_step(st))
    if starts:
        raise ValueError("repeatStart without repeatEnd")
    if not out:
        raise ValueError("workout has no steps")
    if len(out) > MAX_STEPS:
        raise ValueError(f"{len(out)} steps; the Edge takes up to {MAX_STEPS}")
    return out


def _name(workout, name=None):
    n = re.sub(r"[^\w \-]", "", name or workout.get("name") or "", flags=re.UNICODE).strip() or "Workout"
    return n[:NAME_LEN - 1]


def encode(workout, name=None):
    steps = steps_of(workout)
    nm = _name(workout, name).encode("utf-8")[:NAME_LEN - 1]
    body = b""
    # file_id: type 5, manufacturer 1 (Garmin), product 65534 (Garmin Connect, as its workouts)
    body += b"\x40\x00\x00" + struct.pack("<HB", 0, 4) + bytes([0, 1, 0x00, 1, 2, 0x84, 2, 2, 0x84, 3, 4, 0x8C])
    body += b"\x00" + struct.pack("<BHHI", 5, 1, 65534, 0)
    body += b"\x41\x00\x00" + struct.pack("<HB", 26, 3) + bytes([4, 1, 0x00, 6, 2, 0x84, 8, NAME_LEN, 0x07])
    body += b"\x01" + struct.pack("<BH", 2, len(steps)) + nm.ljust(NAME_LEN, b"\0")
    body += b"\x42\x00\x00" + struct.pack("<HB", 27, 8) + bytes(
        [254, 2, 0x84, 1, 1, 0x00, 2, 4, 0x86, 3, 1, 0x00, 4, 4, 0x86, 5, 4, 0x86, 6, 4, 0x86, 7, 1, 0x00])
    for i, (dt, dv, tt, tv, lo, hi, it) in enumerate(steps):
        body += b"\x02" + struct.pack("<HBIBIIIB", i, dt, dv, tt, tv, lo, hi, it)
    hdr = bytearray(struct.pack("<BBHI4s", 14, 16, 2093, len(body), b".FIT") + b"\0\0")
    struct.pack_into("<H", hdr, 12, fit_crc(hdr[:12]))
    data = bytes(hdr) + body
    return data + struct.pack("<H", fit_crc(data))


def send(workout, mount, name=None):
    nf = os.path.join(mount, NEWFILES_REL)
    if not os.path.isdir(nf):
        raise FileNotFoundError(f"{nf} missing - is this an Edge 500 drive?")
    data = encode(workout, name)
    dst = os.path.join(nf, re.sub(r"[^\w\-]", "_", _name(workout, name)) + ".FIT")
    with open(dst, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    if open(dst, "rb").read() != data:
        raise RuntimeError("verify after write failed")
    return {"ok": True, "written": dst, "name": _name(workout, name),
            "note": "Unplug the Edge: the workout appears under Training > Workouts."}


def main(argv=None):
    ap = argparse.ArgumentParser(description="workout JSON -> Edge 500 FIT workout")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("encode"); e.add_argument("json"); e.add_argument("out"); e.add_argument("--name")
    s = sub.add_parser("send"); s.add_argument("json"); s.add_argument("mount"); s.add_argument("--name")
    a = ap.parse_args(argv)
    try:
        wk = json.load(sys.stdin if a.json == "-" else open(a.json, encoding="utf-8"))
        if a.cmd == "encode":
            open(a.out, "wb").write(encode(wk, a.name))
            print(json.dumps({"ok": True, "out": a.out}))
        else:
            print(json.dumps(send(wk, a.mount, a.name)))
        return 0
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
