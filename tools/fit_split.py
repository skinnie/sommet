#!/usr/bin/env python3
"""Cut one activity FIT into two at a long stop - sync/06, "Split here".

    ./tools/fit_split.py ride.fit OUT_PREFIX [--at SECONDS]   # writes OUT_PREFIX-1.fit, -2.fit

A device that lost power mid-ride can carry on in the same file the next time it starts (André's
Edge 500, 2026-11-06: 9 min of riding, 16 h 17 min with no records, 5 min the next morning). This
makes two ordinary FIT files out of it, cut between the last record before the stop and the
first record after it:

  - definition messages, and messages before the first timestamp (file_id, device settings,
    developer fields), go into both files, in their original order;
  - timestamped messages (records, events, device info) go to the side their time falls on, and a
    message without a timestamp follows the one before it;
  - a session or lap that spans the cut goes into both, its totals worked out again from that
    side's records (start, end, total and moving time, distance, heart rate, speed, power,
    cadence, ascent; calories in proportion to moving time). Fields that can't be worked out
    again are written as "invalid", so no reader shows a number that belongs to the whole file;
  - the second file's record distances start again from 0; the activity message goes into both.

Moving time is the time between records 60 s apart or less, the same rule as
tools/activity_streams.py long_gaps(), so the note's figures and the split agree. Files with
compressed-timestamp headers are refused (a dropped message would shift the next one's time).
The original file is never changed; callers keep it. Android twin: shared/fit_split.js (checked
by tools/test_fit_split.py). FIT layout: the public FIT SDK profile.
"""
import argparse
import math
import struct
import sys

LONG_GAP_S = 6 * 3600
RIDING_DT_S = 60

_CRC = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
        0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]

# base type (low 5 bits) -> (struct code, invalid value)
_BT = {0: ("B", 0xFF), 1: ("b", 0x7F), 2: ("B", 0xFF), 3: ("h", 0x7FFF), 4: ("H", 0xFFFF),
       5: ("i", 0x7FFFFFFF), 6: ("I", 0xFFFFFFFF), 7: (None, 0), 8: ("I", 0xFFFFFFFF),
       10: ("B", 0x00), 11: ("H", 0x0000), 12: ("I", 0x00000000), 13: ("B", 0xFF)}
# 64-bit types are left out on purpose: they are only ever written as invalid (all 0xFF bytes),
# exactly as the JavaScript twin does (no 64-bit integers there).
_SIZE = {"B": 1, "b": 1, "h": 2, "H": 2, "i": 4, "I": 4}

SESSION, LAP, RECORD, ACTIVITY = 18, 19, 20, 34
# session/lap fields kept as they are: event, event_type, sport, sub_sport, trigger, lap indexes,
# pool length, message_index. Everything else is worked out again or written as invalid.
KEEP = {0, 1, 5, 6, 24, 25, 26, 28, 44, 46, 254}


def fit_crc(data, crc=0):
    for b in data:
        t = _CRC[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ t ^ _CRC[b & 0xF]
        t = _CRC[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ t ^ _CRC[(b >> 4) & 0xF]
    return crc & 0xFFFF


def _parse(data):
    """-> (header bytes, [item]); item = {kind: 'def'|'data', raw: bytearray, gn, arch, layout,
    vals}; layout = [(field, offset in raw, size, base type)], vals = {field: value} (valid only)."""
    if len(data) < 12 or data[8:12] != b".FIT":
        raise ValueError("not a FIT file")
    hs = data[0]
    end = min(len(data), hs + struct.unpack_from("<I", data, 4)[0])
    items, defs, i = [], {}, hs
    while i < end:
        start = i
        h = data[i]
        i += 1
        if h & 0x80:
            raise ValueError("this file uses compressed timestamps; it can't be split here")
        lt = h & 0x0F
        if h & 0x40:
            arch = data[i + 1]
            gn = struct.unpack_from("<H" if arch == 0 else ">H", data, i + 2)[0]
            n = data[i + 4]
            i += 5
            layout, off = [], 1
            for _ in range(n):
                fn, size, bt = data[i], data[i + 1], data[i + 2] & 0x1F
                layout.append((fn, off, size, bt))
                off += size
                i += 3
            dev = 0
            if h & 0x20:
                nd = data[i]
                i += 1
                for _ in range(nd):
                    dev += data[i + 1]
                    i += 3
            defs[lt] = (arch, gn, layout, dev)
            items.append({"kind": "def", "raw": bytearray(data[start:i])})
            continue
        if lt not in defs:
            raise ValueError(f"data message for undefined local type {lt} at byte {start}")
        arch, gn, layout, dev = defs[lt]
        size = sum(s for _, _, s, _ in layout) + dev
        raw = bytearray(data[start:i + size])
        i += size
        e = "<" if arch == 0 else ">"
        vals = {}
        for fn, off, sz, bt in layout:
            code, inv = _BT.get(bt, (None, 0))
            if code is None or _SIZE[code] != sz:
                continue
            v = struct.unpack_from(e + code, raw, off)[0]
            if v != inv:
                vals[fn] = v
        items.append({"kind": "data", "raw": raw, "gn": gn, "arch": arch, "layout": layout, "vals": vals})
    return bytes(data[:hs]), items


def _set(item, fn, value):
    """Write a field of a data message in place; value None = invalid. Absent field: skipped."""
    e = "<" if item["arch"] == 0 else ">"
    for f, off, sz, bt in item["layout"]:
        if f != fn:
            continue
        code, inv = _BT.get(bt, (None, 0))
        if code is None or _SIZE[code] != sz:
            if value is None:
                item["raw"][off:off + sz] = b"\xff" * sz if bt != 7 else b"\0" * sz
            return
        if value is None:
            v = inv
        else:
            v = math.floor(value + 0.5)        # half up, like JavaScript's Math.round
            lo, hi = (0, (1 << (8 * sz)) - 1) if code.isupper() else (-(1 << (8 * sz - 1)), (1 << (8 * sz - 1)) - 1)
            v = min(max(v, lo), hi)
            if v == inv:                       # a real value never reads as "invalid"
                v = v - 1 if v > 0 else v + 1
        struct.pack_into(e + code, item["raw"], off, v)
        return


def _ascent(alts):
    """Climb and descent (m) with 3 m of hysteresis, so GPS/baro noise doesn't add up."""
    up = down = 0.0
    ref = None
    for a in alts:
        if ref is None:
            ref = a
        elif a - ref >= 3:
            up += a - ref
            ref = a
        elif ref - a >= 3:
            down += ref - a
            ref = a
    return up, down


def _stats(recs):
    """Totals of one side, from its record messages (vals dicts)."""
    ts = [r[253] for r in recs]
    ride = 0
    for k in range(1, len(ts)):
        dt = ts[k] - ts[k - 1]
        if 0 < dt <= RIDING_DT_S:
            ride += dt
    dist = [r[5] for r in recs if 5 in r]
    hr = [r[3] for r in recs if r.get(3)]
    spd = [r.get(73, r.get(6)) for r in recs if (73 in r or 6 in r)]
    pw = [r[7] for r in recs if 7 in r]
    cad = [r[4] for r in recs if 4 in r]
    alts = [(r[78] / 5.0 - 500) if 78 in r else (r[2] / 5.0 - 500) for r in recs if (78 in r or 2 in r)]
    up, down = _ascent(alts)
    d0 = dist[0] if dist else 0
    dcm = (dist[-1] - d0) if dist else 0
    return {"start": ts[0], "end": ts[-1], "ride_s": ride, "d0_cm": d0, "dist_cm": dcm,
            "avg_hr": sum(hr) / len(hr) if hr else None, "max_hr": max(hr) if hr else None,
            "max_mms": max(spd) if spd else None,
            "avg_pw": sum(pw) / len(pw) if pw else None, "max_pw": max(pw) if pw else None,
            "avg_cad": sum(cad) / len(cad) if cad else None, "max_cad": max(cad) if cad else None,
            "ascent": up if alts else None, "descent": down if alts else None}


def _patch_summary(item, st, kcal_share):
    for fn, _, _, _ in item["layout"]:
        if fn not in KEEP:
            _set(item, fn, None)
    if item["gn"] == ACTIVITY:
        _set(item, 253, st["end"])
        _set(item, 0, st["ride_s"] * 1000)
        for fn in (1, 2, 3, 4, 5, 6):          # num_sessions, type, event, event_type, local ts, event_group
            v = item["vals"].get(fn)
            if v is not None:
                _set(item, fn, v if fn != 5 else v + (st["end"] - item["vals"].get(253, st["end"])))
        return
    avg_mms = st["dist_cm"] * 10.0 / st["ride_s"] if st["ride_s"] else None
    for fn, v in ((253, st["end"]), (2, st["start"]), (7, (st["end"] - st["start"]) * 1000),
                  (8, st["ride_s"] * 1000), (9, st["dist_cm"]), (16, st["avg_hr"]), (17, st["max_hr"]),
                  (14, avg_mms), (124, avg_mms), (15, st["max_mms"]), (125, st["max_mms"]),
                  (20, st["avg_pw"]), (21, st["max_pw"]), (18, st["avg_cad"]), (19, st["max_cad"]),
                  (22, st["ascent"]), (23, st["descent"])):
        if v is not None:
            _set(item, fn, v)
    kcal = item["vals"].get(11)
    if kcal is not None:
        _set(item, 11, kcal * kcal_share)


def long_stops(data):
    """[(at_s, len_s)] for the record gaps of LONG_GAP_S or more; at_s counts from the session
    start (else the first record), as tools/activity_streams.py does."""
    _, items = _parse(data)
    recs = [it["vals"] for it in items if it["kind"] == "data" and it["gn"] == RECORD and 253 in it["vals"]]
    sess = next((it["vals"] for it in items if it["kind"] == "data" and it["gn"] == SESSION), {})
    t0 = sess.get(2) or (recs[0][253] if recs else 0)
    out = []
    for k in range(1, len(recs)):
        dt = recs[k][253] - recs[k - 1][253]
        if dt >= LONG_GAP_S:
            out.append((recs[k - 1][253] - t0, dt))
    return out


def split_fit(data, at_s=None):
    """-> (first FIT bytes, second FIT bytes), cut at the long stop that starts at_s seconds in
    (the first long stop when at_s is None). ValueError when there is no such stop."""
    header, items = _parse(data)
    recs = [it for it in items if it["kind"] == "data" and it["gn"] == RECORD and 253 in it["vals"]]
    sess = next((it["vals"] for it in items if it["kind"] == "data" and it["gn"] == SESSION), {})
    t0 = sess.get(2) or (recs[0]["vals"][253] if recs else 0)
    cut = None
    for k in range(1, len(recs)):
        a, b = recs[k - 1]["vals"][253], recs[k]["vals"][253]
        if b - a >= LONG_GAP_S and (at_s is None or abs((a - t0) - at_s) <= 1):
            cut = a
            break
    if cut is None:
        raise ValueError("no stop of 6 hours or more at that point in this file")

    sides = ([r["vals"] for r in recs if r["vals"][253] <= cut], [r["vals"] for r in recs if r["vals"][253] > cut])
    stats = [_stats(s) for s in sides]
    total_ride = (stats[0]["ride_s"] + stats[1]["ride_s"]) or 1
    shares = [stats[0]["ride_s"] / total_ride, stats[1]["ride_s"] / total_ride]
    d_shift = stats[1]["d0_cm"]                # second file's distances start again from 0
    d_first0 = stats[0]["d0_cm"] if sides[0] else 0
    out = [bytearray(), bytearray()]
    side = None                                # None = before any timestamp: both
    for it in items:
        if it["kind"] == "def":
            out[0] += it["raw"]
            out[1] += it["raw"]
            continue
        gn, v = it["gn"], it["vals"]
        if gn in (SESSION, LAP):
            st_ = v.get(2, v.get(253, 0))
            en_ = v.get(253, st_)
            if st_ <= cut < en_:               # spans the cut: one in each file, totals again
                for p in (0, 1):
                    copy = dict(it, raw=bytearray(it["raw"]))
                    _patch_summary(copy, stats[p], shares[p])
                    out[p] += copy["raw"]
            else:
                out[0 if en_ <= cut else 1] += it["raw"]
            continue
        if gn == ACTIVITY:
            for p in (0, 1):
                copy = dict(it, raw=bytearray(it["raw"]))
                _patch_summary(copy, stats[p], shares[p])
                out[p] += copy["raw"]
            continue
        if 253 in v:
            side = 0 if v[253] <= cut else 1
        if side is None:
            out[0] += it["raw"]
            out[1] += it["raw"]
            continue
        if gn == RECORD and 5 in v:
            base = d_shift if side == 1 else d_first0
            if base:
                copy = dict(it, raw=bytearray(it["raw"]))
                _set(copy, 5, v[5] - base)
                out[side] += copy["raw"]
                continue
        out[side] += it["raw"]
    return tuple(_wrap(header, bytes(body)) for body in out)


def _wrap(header, body):
    hdr = bytearray(header)
    struct.pack_into("<I", hdr, 4, len(body))
    if len(hdr) >= 14:
        struct.pack_into("<H", hdr, 12, fit_crc(hdr[:12]))
    whole = bytes(hdr) + body
    return whole + struct.pack("<H", fit_crc(whole))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("fit")
    ap.add_argument("out_prefix")
    ap.add_argument("--at", type=int, default=None, help="seconds from the start where the stop begins")
    a = ap.parse_args()
    data = open(a.fit, "rb").read()
    try:
        parts = split_fit(data, a.at)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    for n, p in enumerate(parts, 1):
        with open(f"{a.out_prefix}-{n}.fit", "wb") as fh:
            fh.write(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
