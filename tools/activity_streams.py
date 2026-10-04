#!/usr/bin/env python3
"""Decode an activity FIT into what the activity screen draws, standard library only.

    ./tools/activity_streams.py ride.fit                 # JSON on stdout
    ./tools/activity_streams.py ride.fit --points 1500   # cap the chart streams at 1500 points
    ./tools/activity_streams.py --self-test              # unit checks on a synthetic FIT

One decoder for every activity the app shows: a watch move (its FIT is built by
tools/exercise_log.py), an intervals.icu import (its FIT is fetched on demand), a Garmin/Karoo/
Magene file. Output:

  sport        {"sport": int, "sub_sport": int}              FIT enums, as recorded
  summary      session totals the file states (never recomputed here), plus `np_w`
  streams      {"t","dist","hr","pw","cad","v","alt","lat","lon","temp"} - equal-length lists,
               downsampled to at most --points by averaging; a channel is left out when the file
               never recorded it, and a gap is null. t = seconds from start, dist = km,
               v = m/s, alt = m, temp = C, cad = rpm for bikes / steps per minute on foot.
  laps         [{start_s, timer_s, elapsed_s, dist_m, avg_hr, max_hr, avg_speed, avg_cad, avg_pw,
                 trigger}]  trigger is the FIT lap_trigger name ("manual", "distance", ...)
  lengths      pool swims: [{n, start_s, swim_s, rest_s, strokes, stroke, active, avg_hr}]
  sets         lengths grouped between rests at the wall (rest >= 10 s): [{n, lengths, dist_m,
                 swim_s, rest_after_s, stroke, strokes_per_length, avg_hr}]
  longest_nonstop_m   the longest set, in metres (pool swims)
  workout_steps  the guided-workout steps the device entered: [{workout, start_s, intensity
                 ("active"/"rest"/None), ends_on ("time"/"distance"/"lap"/None), value (seconds
                 or metres)}]; `workout` counts the workouts run in the one activity, from 1
  hist         {"hr": [[bpm, seconds], ...], "pw": [[watts, seconds], ...]} at full resolution,
               so zone times are exact whatever the zone bounds (the streams are averaged)

Unit conversions live HERE and nowhere else (the screens only format): FIT speed mm/s -> m/s,
distance cm -> km, altitude (x/5 - 500) m, semicircles -> degrees, and foot cadence, which FIT
stores per leg (strides/min + fractional_cadence/128), doubled to steps per minute.

The FIT layout is the public FIT SDK profile (Garmin, FIT Protocol + Profile.xlsx): 12/14-byte
header, definition + data messages, compressed-timestamp headers. Messages read: file_id(0),
session(18), lap(19), record(20), event(21), workout_step(27), length(101).
"""

import argparse
import base64
import json
import math
import struct
import sys

FIT_EPOCH = 631065600
SEMI = 180.0 / 2 ** 31
REST_SPLIT_S = 10.0          # a rest this long at the wall starts a new set (as Apple/Garmin group)

# base type (low 5 bits) -> (struct code, size, invalid raw value)
_BT = {0: ("B", 1, 0xFF), 1: ("b", 1, 0x7F), 2: ("B", 1, 0xFF), 3: ("h", 2, 0x7FFF),
       4: ("H", 2, 0xFFFF), 5: ("i", 4, 0x7FFFFFFF), 6: ("I", 4, 0xFFFFFFFF),
       8: ("f", 4, None), 9: ("d", 8, None), 10: ("B", 1, 0x00), 11: ("H", 2, 0x0000),
       12: ("I", 4, 0x00000000), 14: ("q", 8, 0x7FFFFFFFFFFFFFFF),
       15: ("Q", 8, 0xFFFFFFFFFFFFFFFF), 16: ("Q", 8, 0x0000000000000000)}
WANTED = {0, 18, 19, 20, 21, 27, 101}

LAP_TRIGGERS = {0: "manual", 1: "time", 2: "distance", 3: "position_start", 4: "position_lap",
                5: "position_waypoint", 6: "position_marked", 7: "session_end",
                8: "fitness_equipment"}
# FIT workout_step: duration_type, intensity, and the scale of duration_value per kind.
STEP_ENDS = {0: "time", 1: "distance", 5: "lap"}
STEP_INTENSITY = {0: "active", 1: "rest", 2: "warmup", 3: "cooldown"}
STEP_VALUE_SCALE = {"time": 1000.0, "distance": 100.0}
STROKES = {0: "freestyle", 1: "backstroke", 2: "breaststroke", 3: "butterfly", 4: "drill",
           5: "mixed", 6: "im"}
FOOT_SPORTS = {1, 11, 17}     # running, walking, hiking: cadence is per leg in FIT


def _decode(data: bytes):
    """Yield (global_msg_num, {field_num: value}) for the messages in WANTED."""
    if len(data) < 12:
        raise ValueError("not a FIT file (too short)")
    hs = data[0]
    if data[8:12] != b".FIT":
        raise ValueError("not a FIT file (no .FIT signature)")
    end = min(len(data), hs + struct.unpack_from("<I", data, 4)[0])
    i = hs
    defs = {}
    last_ts = 0
    while i < end:
        h = data[i]
        i += 1
        if h & 0x80:                                  # compressed timestamp data message
            lt = (h >> 5) & 3
            off = h & 0x1F
            comp = True
        else:
            lt = h & 0x0F
            comp = False
            if h & 0x40:                              # definition message
                arch = data[i + 1]
                gn = struct.unpack_from("<H" if arch == 0 else ">H", data, i + 2)[0]
                n = data[i + 4]
                i += 5
                fields = []
                for _ in range(n):
                    fields.append((data[i], data[i + 1], data[i + 2] & 0x1F))
                    i += 3
                dev = 0
                if h & 0x20:
                    nd = data[i]
                    i += 1
                    for _ in range(nd):
                        dev += data[i + 1]
                        i += 3
                defs[lt] = (arch, gn, fields, dev)
                continue
        if lt not in defs:
            raise ValueError(f"data message for undefined local type {lt} at byte {i - 1}")
        arch, gn, fields, dev = defs[lt]
        e = "<" if arch == 0 else ">"
        vals = {}
        for fn, size, bt in fields:
            raw = data[i:i + size]
            i += size
            if gn not in WANTED:
                continue
            spec = _BT.get(bt)
            if spec is None or size != spec[1]:
                if bt == 7:                           # string
                    vals[fn] = raw.split(b"\0")[0].decode("utf-8", "replace")
                continue
            v = struct.unpack(e + spec[0], raw)[0]
            if spec[2] is not None and v == spec[2]:
                continue
            vals[fn] = v
        i += dev
        if 253 in vals:
            last_ts = vals[253]
        elif comp:
            t = (last_ts & ~0x1F) + off
            if t < last_ts:
                t += 0x20
            last_ts = t
            vals[253] = t
        if gn in WANTED:
            yield gn, vals


def _round(x, digits=0):
    """Round half UP, like JavaScript's Math.round - Python's round() goes half-to-even, which
    made the Android twin (shared/activity_streams.js) differ on ties (108.5 -> 108 vs 109)."""
    if x is None:
        return None
    m = 10 ** digits
    r = math.floor(x * m + 0.5) / m
    return int(r) if digits == 0 else r


def _sint8(v):
    return v - 256 if v is not None and v > 127 else v


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def normalized_power(times, watts):
    """Coggan normalized power: 30 s rolling mean of a 1 Hz power series, 4th-power mean, 4th
    root. Gaps shorter than 10 s hold the last value; longer gaps count as zero (coasting)."""
    pts = [(t, w) for t, w in zip(times, watts) if w is not None]
    if len(pts) < 30:
        return None
    t0 = pts[0][0]
    span = int(pts[-1][0] - t0)
    grid = [0.0] * (span + 1)
    j = 0
    for s in range(span + 1):
        while j + 1 < len(pts) and pts[j + 1][0] - t0 <= s:
            j += 1
        grid[s] = pts[j][1] if (s - (pts[j][0] - t0)) <= 10 else 0.0
    if len(grid) < 30:
        return None
    acc = sum(grid[:30])
    fourth = [(acc / 30) ** 4]
    for s in range(30, len(grid)):
        acc += grid[s] - grid[s - 30]
        fourth.append((acc / 30) ** 4)
    return (sum(fourth) / len(fourth)) ** 0.25


def _interp(ts, vs, max_gap):
    """Fill None runs in `vs` by linear interpolation over time, when the gap between the two
    known neighbours is at most `max_gap` seconds (None = any gap). Leading/trailing Nones stay."""
    known = [k for k, v in enumerate(vs) if v is not None]
    for a, b in zip(known, known[1:]):
        if b - a < 2:
            continue
        dt = ts[b] - ts[a]
        if dt <= 0 or (max_gap is not None and dt > max_gap):
            continue
        for k in range(a + 1, b):
            f = (ts[k] - ts[a]) / dt
            vs[k] = vs[a] + (vs[b] - vs[a]) * f


def _fill_gaps(full):
    """Samples between GPS fixes carry no distance/position (an Ambit3 logs a fix every few
    seconds; Suunto FITs start with distance-less samples). Distance is cumulative, so its gaps
    are interpolated over time whatever their length, and the leading gap before the first
    reading is 0 km. Positions are interpolated only across short gaps (<= 30 s) - a longer one
    is a real signal loss and stays a gap on the map."""
    ts, dist = full["t"], full["dist"]
    if any(v is not None for v in dist):
        _interp(ts, dist, None)
        first = next(k for k, v in enumerate(dist) if v is not None)
        for k in range(first):
            dist[k] = 0.0 if dist[first] < 0.05 else None
        last = max(k for k, v in enumerate(dist) if v is not None)
        for k in range(last + 1, len(dist)):
            dist[k] = dist[last]
    for ch in ("lat", "lon"):
        if any(v is not None for v in full[ch]):
            _interp(ts, full[ch], 30)


def streams_from_fit(data: bytes, points: int = 2000) -> dict:
    file_id, session, laps_raw, recs, lens_raw = {}, {}, [], [], []
    step_defs, step_events = {}, []
    for gn, v in _decode(data):
        if gn == 27:
            step_defs[v.get(254)] = v
        elif gn == 21 and v.get(0) in (3, 4):
            step_events.append(v)
        if gn == 0 and not file_id:
            file_id = v
        elif gn == 18 and not session:
            session = v
        elif gn == 19:
            laps_raw.append(v)
        elif gn == 20:
            recs.append(v)
        elif gn == 101:
            lens_raw.append(v)

    sport = session.get(5)
    foot = sport in FOOT_SPORTS
    t0 = session.get(2) or (recs[0].get(253) if recs else None) or 0

    # --- full-resolution record channels, converted once ---------------------------------
    full = {k: [] for k in ("t", "dist", "hr", "pw", "cad", "v", "alt", "lat", "lon", "temp")}
    for r in recs:
        if 253 not in r:
            continue
        full["t"].append(r[253] - t0)
        full["dist"].append(r[5] / 100000.0 if 5 in r else None)
        full["hr"].append(r.get(3) if r.get(3) else None)
        full["pw"].append(r.get(7))
        cad = r.get(4)
        if cad is not None and foot:
            cad = (cad + r.get(53, 0) / 128.0) * 2
        full["cad"].append(cad)
        spd = r.get(73, r.get(6))
        full["v"].append(spd / 1000.0 if spd is not None else None)
        alt = r.get(78, r.get(2))
        full["alt"].append(alt / 5.0 - 500 if alt is not None else None)
        full["lat"].append(r[0] * SEMI if 0 in r else None)
        full["lon"].append(r[1] * SEMI if 1 in r else None)
        full["temp"].append(_sint8(r.get(13)))

    n = len(full["t"])
    _fill_gaps(full)
    present = [k for k in full if k != "t" and any(x is not None for x in full[k])]

    # --- downsample for the chart: average within equal-count buckets --------------------
    step = max(1, -(-n // max(1, points)))
    streams = {"t": []}
    for k in present:
        streams[k] = []
    for s in range(0, n, step):
        e = min(n, s + step)
        streams["t"].append(full["t"][s])
        for k in present:
            seg = full[k][s:e]
            if k in ("lat", "lon"):
                val = next((x for x in seg if x is not None), None)
                streams[k].append(_round(val, 6) if val is not None else None)
            else:
                m = _mean(seg)
                streams[k].append(_round(m, 3) if m is not None else None)

    # --- full-resolution time per value, so zone times are exact for ANY zone bounds (the chart
    # streams above are averaged and would smear short efforts). A gap over 10 s is a pause.
    def hist(ch, bucket):
        acc = {}
        ts, vs = full["t"], full[ch]
        for k in range(len(ts) - 1):
            v = vs[k]
            dt = ts[k + 1] - ts[k]
            if v is None or dt <= 0 or dt > 10:
                continue
            key = int(v // bucket * bucket)
            acc[key] = acc.get(key, 0) + dt
        return sorted([k, _round(s, 1)] for k, s in acc.items())
    histograms = {}
    if "hr" in present:
        histograms["hr"] = hist("hr", 1)
    if "pw" in present:
        histograms["pw"] = hist("pw", 1)

    # --- session summary: what the file itself states -------------------------------------
    def sv(f, scale=1.0, offset=0.0):
        return session[f] / scale - offset if f in session else None
    summary = {
        "elapsed_s": sv(7, 1000), "timer_s": sv(8, 1000), "dist_m": sv(9, 100),
        "kcal": session.get(11), "avg_hr": session.get(16), "max_hr": session.get(17),
        "avg_cad": session.get(18), "max_cad": session.get(19),
        "avg_pw": session.get(20), "max_pw": session.get(21),
        "ascent_m": session.get(22), "descent_m": session.get(23),
        "avg_speed": (session.get(124, session.get(14)) or 0) / 1000.0 if (124 in session or 14 in session) else None,
        "max_speed": (session.get(125, session.get(15)) or 0) / 1000.0 if (125 in session or 15 in session) else None,
        "pool_length_m": sv(44, 100), "avg_temp": _sint8(session.get(57)), "max_temp": _sint8(session.get(58)),
    }
    if foot:
        for f in ("avg_cad", "max_cad"):
            if summary[f] is not None:
                summary[f] = summary[f] * 2
    if "pw" in present:
        np_w = normalized_power(full["t"], full["pw"])
        summary["np_w"] = _round(np_w) if np_w else None
    temps = [x for x in full["temp"] if x is not None]
    if temps:
        summary["min_temp"] = min(temps)
        summary["max_temp"] = summary["max_temp"] if summary["max_temp"] is not None else max(temps)

    # --- laps -------------------------------------------------------------------------------
    laps = []
    for lp in laps_raw:
        st = lp.get(2)
        spd = lp.get(110, lp.get(13))
        cad = lp.get(17)
        if cad is not None and foot:
            cad = cad * 2
        laps.append({
            "start_s": (st - t0) if st is not None else None,
            "timer_s": lp[8] / 1000.0 if 8 in lp else None,
            "elapsed_s": lp[7] / 1000.0 if 7 in lp else None,
            "dist_m": lp[9] / 100.0 if 9 in lp else None,
            "avg_hr": lp.get(15), "max_hr": lp.get(16),
            "avg_speed": spd / 1000.0 if spd is not None else None,
            "avg_cad": cad, "avg_pw": lp.get(19),
            "trigger": LAP_TRIGGERS.get(lp.get(24), None),
        })

    # --- pool lengths and sets ----------------------------------------------------------------
    lengths = []
    rec_hr = [(r[253], r[3]) for r in recs if 253 in r and r.get(3)]
    for k, ln in enumerate(lens_raw, 1):
        st = ln.get(2)
        swim = ln[4] / 1000.0 if 4 in ln else None
        el = ln[3] / 1000.0 if 3 in ln else None
        hr = None
        if st is not None and el:
            hs = [h for ts, h in rec_hr if st <= ts < st + el]
            hr = _round(sum(hs) / len(hs)) if hs else None
        lengths.append({
            "n": k, "start_s": (st - t0) if st is not None else None,
            "swim_s": swim, "rest_s": (el - swim) if (el is not None and swim is not None) else 0.0,
            "strokes": ln.get(5), "stroke": STROKES.get(ln.get(7)),
            "active": ln.get(12, 1) == 1, "avg_hr": hr,
        })
    # Garmin writes a rest at the wall as its own IDLE length (length_type 0) instead of extra
    # elapsed time on the swum one: fold each idle length into the rest after the length before it.
    last_active = None
    for ln, raw in zip(lengths, lens_raw):
        if ln["active"]:
            last_active = ln
        elif last_active is not None:
            idle = raw[3] / 1000.0 if 3 in raw else (raw[4] / 1000.0 if 4 in raw else 0.0)
            last_active["rest_s"] = (last_active["rest_s"] or 0.0) + idle
    pool = summary["pool_length_m"] or 25.0
    sets = []
    cur = []
    active = [ln for ln in lengths if ln["active"] and ln["swim_s"]]
    for ln in active:
        cur.append(ln)
        if (ln["rest_s"] or 0) >= REST_SPLIT_S:
            sets.append(cur)
            cur = []
    if cur:
        sets.append(cur)
    set_rows = []
    for k, s in enumerate(sets, 1):
        strokes = [x["strokes"] for x in s if x["strokes"] is not None]
        kinds = [x["stroke"] for x in s if x["stroke"]]
        hrs = [x["avg_hr"] for x in s if x["avg_hr"]]
        set_rows.append({
            "n": k, "lengths": len(s), "dist_m": _round(len(s) * pool),
            "swim_s": _round(sum(x["swim_s"] for x in s), 1),
            "rest_after_s": _round(s[-1]["rest_s"] or 0, 1),
            "stroke": _most_common(kinds),
            "strokes_per_length": _round(sum(strokes) / len(strokes)) if strokes else None,
            "avg_hr": _round(sum(hrs) / len(hrs)) if hrs else None,
        })

    # --- guided-workout steps: each workout_step start event, described by the step it names --
    workout_steps, workout_n = [], 1
    for ev in step_events:
        if ev.get(0) == 3:                    # workout stopped: the next step opens a new one
            if ev.get(1) == 1 and workout_steps:
                workout_n = workout_steps[-1]["workout"] + 1
            continue
        if ev.get(1) != 0 or 253 not in ev:
            continue
        d = step_defs.get(ev.get(3)) or {}
        ends_on = STEP_ENDS.get(d.get(1))
        value = d.get(2)
        workout_steps.append({
            "workout": workout_n, "start_s": ev[253] - t0,
            "intensity": STEP_INTENSITY.get(d.get(7)), "ends_on": ends_on,
            "value": value / STEP_VALUE_SCALE[ends_on] if value is not None and ends_on in STEP_VALUE_SCALE else None,
        })

    return {
        "ok": True,
        "workout_steps": workout_steps,
        "laps_kind": laps_kind(laps, bool(lengths)),
        "sport": {"sport": sport, "sub_sport": session.get(6)},
        "start_utc": (t0 + FIT_EPOCH) if t0 else None,
        "manufacturer": file_id.get(1), "product": file_id.get(2),
        "records": n, "channels": present,
        "summary": summary,
        "streams": streams,
        "hist": histograms,
        "laps": laps,
        "lengths": lengths,
        "sets": set_rows,
        "longest_nonstop_m": max((r["dist_m"] for r in set_rows), default=None),
    }


def _most_common(items):
    """Most frequent item; a tie goes to the one that reaches the top count first (same rule as
    the JavaScript twin, so both give identical sets)."""
    best, best_n, counts = None, -1, {}
    for x in items:
        counts[x] = counts.get(x, 0) + 1
        if counts[x] > best_n:
            best, best_n = x, counts[x]
    return best


def laps_kind(laps, has_lengths=False):
    """"pressed", "auto" or None - whether the laps are worth a Laps tab.

    Most files don't say what triggered a lap (FIT lap_trigger is absent in Garmin re-exports),
    so this reads the pattern: fewer than 2 laps -> None; a pool swim's laps (its rest intervals,
    shown as sets instead) -> "auto"; every lap but the last the same round distance (1 km, 1 mile, 5 km...) -> "auto";
    anything else -> "pressed". A stated trigger wins when present.
    """
    if len(laps) < 2:
        return None
    if has_lengths:                 # a pool swim's laps are its rest intervals, shown as sets
        return "auto"
    trig = [l.get("trigger") for l in laps if l.get("trigger")]
    if trig:
        return "pressed" if any(t in ("manual", "position_lap") for t in trig) else "auto"
    dists = [l.get("dist_m") for l in laps[:-1]]
    if all(d for d in dists):
        ref = dists[0]
        for split in (1000.0, 1609.344, 5000.0, 500.0, 400.0):
            if all(abs(d - split) <= split * 0.02 for d in dists):
                return "auto"
        if max(dists) - min(dists) <= ref * 0.01:
            return "auto"
    return "pressed"


# ------------------------------------------------------------------------------------------
# self-test: a tiny synthetic FIT built with the same field layout the watch export writes
def _fit_file(messages):
    """messages: [(local, global, [(field, base_type, value)])] -> FIT bytes (CRC not checked)."""
    body = bytearray()
    defined = {}
    fmt = {0x84: ("H", 2), 0x86: ("I", 4), 0x85: ("i", 4), 0x02: ("B", 1), 0x01: ("b", 1), 0x00: ("B", 1)}
    for local, glob, fields in messages:
        sig = (glob, tuple((f, bt) for f, bt, _ in fields))
        if defined.get(local) != sig:
            body += bytes([0x40 | local, 0, 0]) + struct.pack("<H", glob) + bytes([len(fields)])
            for f, bt, _ in fields:
                body += bytes([f, fmt[bt][1], bt])
            defined[local] = sig
        body.append(local)
        for f, bt, v in fields:
            body += struct.pack("<" + fmt[bt][0], v)
    hdr = bytearray(struct.pack("<BBHI4s", 12, 0x10, 2100, len(body), b".FIT"))
    return bytes(hdr + body + b"\0\0")


def _self_test():
    t0 = 1_000_000_000
    msgs = [(0, 0, [(1, 0x84, 1), (2, 0x84, 4315)])]
    for s in range(0, 120):
        msgs.append((1, 20, [(253, 0x86, t0 + s), (5, 0x86, s * 250), (3, 0x02, 120 + s % 10),
                             (4, 0x02, 80), (6, 0x84, 2500), (2, 0x84, (100 + 500) * 5), (7, 0x84, 200)]))
    msgs.append((2, 19, [(253, 0x86, t0 + 120), (2, 0x86, t0), (7, 0x86, 120000), (8, 0x86, 120000),
                         (9, 0x86, 30000), (24, 0x00, 0)]))
    msgs.append((3, 18, [(253, 0x86, t0 + 120), (2, 0x86, t0), (5, 0x00, 2), (6, 0x00, 7),
                         (7, 0x86, 120000), (8, 0x86, 120000), (9, 0x86, 30000), (16, 0x02, 125)]))
    out = streams_from_fit(_fit_file(msgs), points=40)
    ok = True
    def check(name, got, want):
        nonlocal ok
        good = got == want if not isinstance(want, float) else abs(got - want) < 1e-6
        print(("PASS " if good else "FAIL ") + name, got, "" if good else f"(want {want})")
        ok &= good
    check("records", out["records"], 120)
    check("points <= 40", len(out["streams"]["t"]) <= 40, True)
    check("speed m/s", out["streams"]["v"][0], 2.5)
    check("altitude m", out["streams"]["alt"][0], 100.0)
    check("distance km, last bucket mean of samples 117-119", _round(out["streams"]["dist"][-1], 4), 0.295)
    check("np of constant 200 W", out["summary"]["np_w"], 200)
    check("lap trigger", out["laps"][0]["trigger"], "manual")
    check("sport ride/road", (out["sport"]["sport"], out["sport"]["sub_sport"]), (2, 7))
    check("product", out["product"], 4315)
    check("laps kind: 1 km auto-laps", laps_kind([{"dist_m": 1000}, {"dist_m": 1001}, {"dist_m": 229}]), "auto")
    check("laps kind: uneven laps", laps_kind([{"dist_m": 486}, {"dist_m": 417}, {"dist_m": 793}]), "pressed")
    check("laps kind: one lap", laps_kind([{"dist_m": 5000}]), None)
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("fit", nargs="?")
    ap.add_argument("--points", type=int, default=2000)
    ap.add_argument("--base64", action="store_true", help="the file holds base64 text, not raw FIT")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(0 if _self_test() else 1)
    if not a.fit:
        ap.error("give a FIT file (or --self-test)")
    raw = open(a.fit, "rb").read()
    if a.base64:
        raw = base64.b64decode(raw)
    json.dump(streams_from_fit(raw, a.points), sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main()
