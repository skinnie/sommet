#!/usr/bin/env python3
"""Send a structured workout to a Magene C406 (Pro) over BLE - the Magene twin of
bryton_from_intervals.py / guided_workout.py, taking the project's workout schema.

    ./tools/magene_workout.py encode plan.json [--ftp 240]            # dry run, no device
    ./tools/magene_workout.py send plan.json --address <addr> [--ftp 240] [--name "VO2 5x4"]
    ./tools/intervals_workout.py W1.json | ./tools/magene_workout.py send - --address <addr>

Decoded from the OneLap app's native BLE library (lib_ble WorkoutFilePacket /
WriteWorkoutInfoCommand / blslib CreateWorkoutPb), see assets/onelap-re/NOTES.md. The C406 Pro
class sets setSupportWorkout(true).

Transfer is the same as a route (magene_route.transfer_file): an info command on CC02, the file in
A5 5A 5A A5 packets on CC03 paced by 40 8c credit grants, then 40 52. Only the payload differs:
  * info `40 88`: <workoutId u32LE> <ceil(TSS*10) u16LE> <totalSeconds u32LE> <crc16 u16LE>
    <nameLen u8> <name bytes>
  * file = protobuf Workout.WorkOut{ infor=1, intervals=2 (repeated) } (proto2 - the app sets
    every field explicitly, zeros included, so we emit them too):
      Infor{ number=1, type=2 (=1), duration_min=3, duration_max=4, target_power_min=5,
             target_power_max=6, target_cad_min=7, target_cad_max=8, target_heart_min=9,
             target_heart_max=10 }      - fields 5..10 are SINT32, the rest uint32
      Interval{ duration=1, target=2 (repeated), intensity=3 }
      Duration{ type=1 (=1 time), value=2 (seconds), unit=3 (1 if >= 60 s else 0) }
      Target{ type=1 (4 power / 3 cadence), unit=2 (power: 0 %FTP, 1 W), value=3 SINT32 }
    Field types were read from the protobuf-lite info strings (the check that would have caught
    the route's sint32 coordinates up front).
  * intensity: 0 ride/work, 1 recovery, 2 warm-up, 3 cool-down (same order as the Bryton's).
Power goes over as watts (unit 1), so no FTP is needed to encode; FTP only feeds the TSS shown
on the device (--ftp, else the device's own FTP read from its profile on send). The schema's power range is sent as its midpoint (the C406 takes one value).
Time-based steps only; HR targets have no C406 equivalent here and are sent as no target.
"""

import argparse
import asyncio
import json
import math
import struct
import sys
import time
from decimal import Decimal, ROUND_HALF_UP

from bryton_from_intervals import PHASE_TO_INTENSITY, _flatten
from magene_device import read_profile
from magene_import import _connect, _fit_crc16
from magene_route import (_pb_int32, _pb_sint32, _pb_msg, _packets, negotiate_mtu,
                          transfer_file)

INTENSITY_CODE = {"work": 0, "recovery": 1, "warmup": 2, "cooldown": 3}
POWER_FTP_PCT, POWER_W = 0, 1    # Target.unit for power (type 4)


def _warn(msg):
    print(f"magene_workout: {msg}", file=sys.stderr)


def _mid(target, name):
    if target.get("targetName") != name:
        return 0
    rng = target.get("valueRange") or {}
    lo, hi = rng.get("min"), rng.get("max")
    if lo is None and hi is None:
        v = target.get("value")
        return int(round(float(v))) if v is not None else 0
    lo = float(lo if lo is not None else hi)
    hi = float(hi if hi is not None else lo)
    return int(round((lo + hi) / 2.0))


def to_intervals(workout):
    """Project schema -> [{seconds, intensity, watts, cadence}] (repeats flattened)."""
    steps = _flatten(workout.get("steps") or [])
    if not steps:
        raise ValueError("workout has no steps")
    out = []
    for st in steps:
        dur = st.get("duration") or {}
        if dur.get("durationName") == "distance":
            raise ValueError("the C406 takes time-based steps only; this workout has a "
                             "distance step")
        secs = int(round(float(dur.get("value") or 0)))
        if secs <= 0:
            raise ValueError(f"step has no duration: {st!r}")
        target = st.get("target") or {}
        tname = target.get("targetName")
        if tname not in (None, "power", "cadence", "none"):
            _warn(f"{tname} target has no C406 equivalent; step sent without a target")
        phase = PHASE_TO_INTENSITY.get(st.get("type", {}).get("typeName"), "work")
        out.append({"seconds": secs, "intensity": INTENSITY_CODE.get(phase, 0),
                    "power": _mid(target, "power"), "powerUnit": POWER_W,
                    "cadence": _mid(target, "cadence")})
    return out


def from_native(workout):
    """The workout builder's own payload (the object it posts to /api/bryton/workout/native):
    {name, unit, interval_mode, steps:[{intensity, duration, low, high}]}. The C406 takes %FTP
    power natively (the device multiplies by its own FTP, like the Bryton) or cadence; it has no
    HR/speed target and no distance steps here, so those are refused rather than mis-sent."""
    if workout.get("interval_mode", "time") != "time":
        raise ValueError("The Magene C406 takes time-based steps only.")
    unit = workout.get("unit", "ftp")
    if unit not in ("ftp", "cadence"):
        raise ValueError("The Magene C406 takes FTP% or cadence targets only.")
    out = []
    for st in workout.get("steps") or []:
        mid = int(round((float(st.get("low", 0)) + float(st.get("high", st.get("low", 0)))) / 2))
        out.append({"seconds": int(st["duration"]),
                    "intensity": INTENSITY_CODE.get(st.get("intensity", "work"), 0),
                    "power": mid if unit == "ftp" else 0,
                    "powerUnit": POWER_FTP_PCT if unit == "ftp" else POWER_W,
                    "cadence": mid if unit == "cadence" else 0})
    if not out:
        raise ValueError("workout has no steps")
    return out


def encode_workout(intervals):
    secs = [iv["seconds"] for iv in intervals]
    infor = (_pb_int32(1, len(intervals)) + _pb_int32(2, 1)
             + _pb_int32(3, min(secs)) + _pb_int32(4, max(secs))
             + _pb_sint32(5, 0) + _pb_sint32(6, max(iv["power"] for iv in intervals))
             + _pb_sint32(7, 0) + _pb_sint32(8, max(iv["cadence"] for iv in intervals))
             + _pb_sint32(9, 0) + _pb_sint32(10, 0))
    body = bytearray(_pb_msg(1, infor))
    for iv in intervals:
        duration = (_pb_int32(1, 1) + _pb_int32(2, iv["seconds"])
                    + _pb_int32(3, 1 if iv["seconds"] >= 60 else 0))
        power = _pb_int32(1, 4) + _pb_int32(2, iv["powerUnit"]) + _pb_sint32(3, iv["power"])
        cadence = _pb_int32(1, 3) + _pb_int32(2, 0) + _pb_sint32(3, iv["cadence"])
        interval = (_pb_msg(1, duration) + _pb_msg(2, power) + _pb_msg(2, cadence)
                    + _pb_int32(3, iv["intensity"]))
        body += _pb_msg(2, interval)
    return bytes(body)


def _normalized_power(series):
    """Exact copy of the app's TrainFormulaUtil.getNP_PowerList_Double: the 4th-power mean of
    30-sample rolling averages (len-30 windows, taken from the end), 4th root, rounded half-up to
    4 decimals. 0 when the series is 30 s or shorter."""
    n = len(series) - 30
    if n <= 0:
        return 0.0
    pre = [0.0]
    for v in series:
        pre.append(pre[-1] + v)
    total_len = len(series)
    acc = 0.0
    for i in range(n):
        acc += ((pre[total_len - i] - pre[total_len - 30 - i]) / 30.0) ** 4
    np_ = (acc / n) ** 0.25
    return float(Decimal(np_).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def tss(intervals, ftp):
    """The app's TSS (CourseDetailViewModel): a per-second series of each step's power in watts
    (FTP% steps -> ftp*pct/100 with integer division, as the app does), then
    seconds * NP * IF / FTP / 36 with IF = NP / FTP."""
    ftp = int(ftp or 0)
    if ftp <= 0:
        return 0.0
    series = []
    for iv in intervals:
        watts = (ftp * iv["power"]) // 100 if iv["powerUnit"] == POWER_FTP_PCT else iv["power"]
        series.extend([float(watts)] * iv["seconds"])
    np_ = _normalized_power(series)
    return len(series) * np_ * (np_ / ftp) / ftp / 36.0


def workout_info(workout_id, tss_value, total_seconds, crc16, name):
    # The app sends the full title; the only limit is the protocol's u8 length byte (the app's
    # id is its server workout id - any unique number, so a timestamp does the same job).
    nb = name.encode("utf-8")
    while len(nb) > 255:
        name = name[:-1]
        nb = name.encode("utf-8")
    return (b"\x40\x88" + struct.pack("<IHIHB", workout_id & 0xFFFFFFFF,
                                      int(math.ceil(tss_value * 10)) & 0xFFFF,
                                      total_seconds & 0xFFFFFFFF, crc16 & 0xFFFF, len(nb)) + nb)


def _load(path):
    return json.load(sys.stdin if path == "-" else open(path, encoding="utf-8"))


async def send(address, workout, ftp, name, native=False):
    intervals = from_native(workout) if native else to_intervals(workout)
    file_bytes = encode_workout(intervals)
    crc16 = _fit_crc16(file_bytes)
    total = sum(iv["seconds"] for iv in intervals)
    client = await _connect(address)
    try:
        if not ftp:
            # Like the app (and the Bryton path): the TSS shown on the device uses the DEVICE'S OWN FTP.
            prof = await read_profile(client)
            ftp = (prof or {}).get("ftp") or 0
        info = workout_info(int(time.time()) & 0x7FFFFFFF, tss(intervals, ftp), total, crc16,
                            name or workout.get("name") or "Workout")
        mtu = await negotiate_mtu(client)
        packets = _packets(file_bytes, mtu)
        res = await transfer_file(client, info, packets)
        return {"ok": res["error"] is None, "error": res["error"], "intervals": len(intervals),
                "seconds": total, "fileBytes": len(file_bytes), "packetsSent": res["packetsSent"],
                "packets": len(packets), "deviceReplies": res["replies"]}
    finally:
        await client.disconnect()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["encode", "send"])
    ap.add_argument("workout", help="workout JSON in the project schema, or - for stdin")
    ap.add_argument("--address")
    ap.add_argument("--ftp", type=float, default=0.0)
    ap.add_argument("--name")
    ap.add_argument("--native", action="store_true",
                    help="input is the workout builder's payload (unit/interval_mode/steps)")
    args = ap.parse_args()
    workout = _load(args.workout)
    if args.action == "encode":
        try:
            iv = from_native(workout) if args.native else to_intervals(workout)
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
        fb = encode_workout(iv)
        print(json.dumps({"ok": True, "intervals": iv, "fileBytes": len(fb),
                          "crc16": hex(_fit_crc16(fb)), "tss": round(tss(iv, args.ftp), 1),
                          "hex": fb.hex(" ")}))
        return 0
    if not args.address:
        ap.error("send needs --address")
    try:
        result = asyncio.run(send(args.address, workout, args.ftp, args.name, args.native))
    except ValueError as e:
        result = {"ok": False, "error": str(e)}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
