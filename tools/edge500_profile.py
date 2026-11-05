#!/usr/bin/env python3
"""Read and safely write the athlete profile of a mass-storage Garmin Edge (Edge 500 and its
2009-2013 siblings) over USB, stdlib only. Same JSON shape as tools/bryton_profile.py, so the
desktop's compare-with-intervals.icu flow is shared.

    ./tools/edge500_profile.py read  /media/<user>/GARMIN          # -> profile JSON
    ./tools/edge500_profile.py write /media/<user>/GARMIN --ftp 250 --max-hr 190 --weight 72.5

Where the values live (read from a real Edge 500, fw 3.30, 2026-11-05):
  * Garmin/Settings/Settings.fit (FIT file type 2), mesg 3 user_profile:
        1 gender (0 female, 1 male) · 2 age · 3 height (cm, u8) · 4 weight (kg x10, u16)
        8 resting_heart_rate
  * Garmin/Sports/Cycling.fit (FIT file type 3), mesg 7 zones_target:
        1 max_heart_rate · 2 threshold_heart_rate (LTHR) · 3 functional_threshold_power (FTP)
        5 hr_calc_type · 7 pwr_calc_type (1 = zones are % of max HR / % of FTP)
    plus mesg 8 hr_zone (1 = high_bpm) and mesg 9 power_zone (1 = high_value).

How a write reaches the device: the Edge's own GarminDevice.xml lists FIT_TYPE_2 and FIT_TYPE_3
as InputToUnit via Garmin/NewFiles - the folder Garmin Training Center used. We never edit the
live files in Settings/ or Sports/. We patch a COPY byte for byte (every field we touch is
already in the file's definition, fixed size, so nothing shifts), recompute the FIT CRC, and
drop it into Garmin/NewFiles/. The Edge takes it in when it leaves USB mode. The originals are
copied to a local backup folder first.

Zones: when the zone type is "% of max HR" / "% of FTP" (calc type 1), the zone limits stored in
the file are scaled by new/old, so the zones keep the same percentages the device would show.
"""

import argparse
import json
import os
import shutil
import struct
import sys
import time

SETTINGS_REL = os.path.join("Garmin", "Settings", "Settings.fit")
CYCLING_REL = os.path.join("Garmin", "Sports", "Cycling.fit")
NEWFILES_REL = os.path.join("Garmin", "NewFiles")

USER_PROFILE, ZONES_TARGET, HR_ZONE, POWER_ZONE = 3, 7, 8, 9

# name -> (file, mesg, field, scale)
FIELDS = {
    "gender":  ("settings", USER_PROFILE, 1, 1),
    "age":     ("settings", USER_PROFILE, 2, 1),
    "height":  ("settings", USER_PROFILE, 3, 1),     # cm
    "weight":  ("settings", USER_PROFILE, 4, 10),    # kg, stored x10
    "rest_hr": ("settings", USER_PROFILE, 8, 1),
    "max_hr":  ("cycling", ZONES_TARGET, 1, 1),
    "lthr":    ("cycling", ZONES_TARGET, 2, 1),
    "ftp":     ("cycling", ZONES_TARGET, 3, 1),
}

BOUNDS = {
    "gender": (0, 1), "age": (5, 120), "height": (80, 250), "weight": (20, 250),
    "max_hr": (100, 240), "lthr": (80, 230), "rest_hr": (30, 120), "ftp": (30, 600),
}

# FIT base type -> (struct code, size, invalid value)
BASE = {0x00: ("B", 1, 0xFF), 0x01: ("b", 1, 0x7F), 0x02: ("B", 1, 0xFF), 0x0A: ("B", 1, 0x00),
        0x83: ("h", 2, 0x7FFF), 0x84: ("H", 2, 0xFFFF), 0x8B: ("H", 2, 0x0000),
        0x85: ("i", 4, 0x7FFFFFFF), 0x86: ("I", 4, 0xFFFFFFFF), 0x8C: ("I", 4, 0)}

_CRC = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
        0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]


def fit_crc(data, crc=0):
    for b in data:
        t = _CRC[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ t ^ _CRC[b & 0xF]
        t = _CRC[crc & 0xF]
        crc = ((crc >> 4) & 0x0FFF) ^ t ^ _CRC[(b >> 4) & 0xF]
    return crc & 0xFFFF


def scan(data):
    """Every data message as {"mesg", "fields": {num: (offset, struct_code, endian)}}.
    Settings/sport files carry no compressed-timestamp records; one would raise."""
    hs = data[0]
    end = hs + struct.unpack_from("<I", data, 4)[0]
    if data[8:12] != b".FIT" or end + 2 > len(data):
        raise ValueError("not a FIT file")
    p, defs, out = hs, {}, []
    while p < end:
        h = data[p]; p += 1
        if h & 0x80:
            raise ValueError("compressed-timestamp record in a settings file")
        local = h & 0x0F
        if h & 0x40:
            e = "<" if data[p + 1] == 0 else ">"
            g = struct.unpack_from(e + "H", data, p + 2)[0]
            n = data[p + 4]; p += 5
            fl = [tuple(data[p + 3 * i:p + 3 * i + 3]) for i in range(n)]; p += 3 * n
            dev = []
            if h & 0x20:
                nd = data[p]; p += 1
                dev = [data[p + 3 * i + 1] for i in range(nd)]; p += 3 * nd
            defs[local] = (g, e, fl, sum(dev))
            continue
        g, e, fl, devsize = defs[local]
        rec = {"mesg": g, "fields": {}}
        for num, size, bt in fl:
            code = BASE.get(bt)
            if code and code[1] == size:
                rec["fields"][num] = (p, code[0], e)
            p += size
        p += devsize
        out.append(rec)
    return out


def _get(data, loc):
    off, code, e = loc
    return struct.unpack_from(e + code, data, off)[0]


def _put(buf, loc, value):
    off, code, e = loc
    struct.pack_into(e + code, buf, off, value)


def _first(recs, mesg):
    for r in recs:
        if r["mesg"] == mesg:
            return r
    return None


def _paths(mount):
    s, c = os.path.join(mount, SETTINGS_REL), os.path.join(mount, CYCLING_REL)
    for p in (s, c):
        if not os.path.isfile(p):
            raise FileNotFoundError(f"{p} missing - is this an Edge 500 drive?")
    return s, c


def _valid(v, code):
    inv = {"B": 0xFF, "H": 0xFFFF, "b": 0x7F, "h": 0x7FFF, "I": 0xFFFFFFFF}.get(code)
    return v != inv


def read(mount):
    s, c = _paths(mount)
    files = {"settings": open(s, "rb").read(), "cycling": open(c, "rb").read()}
    recs = {k: scan(v) for k, v in files.items()}
    prof = {}
    for name, (fk, mesg, fnum, scale) in FIELDS.items():
        r = _first(recs[fk], mesg)
        loc = r and r["fields"].get(fnum)
        if not loc:
            prof[name] = None
            continue
        v = _get(files[fk], loc)
        prof[name] = (round(v / scale, 1) if scale != 1 else v) if _valid(v, loc[1]) else None
    zt = _first(recs["cycling"], ZONES_TARGET)
    prof["hr_zone_type"] = _get(files["cycling"], zt["fields"][5]) if zt and 5 in zt["fields"] else None
    prof["power_zone_type"] = _get(files["cycling"], zt["fields"][7]) if zt and 7 in zt["fields"] else None
    prof["hr_zones"] = [_get(files["cycling"], r["fields"][1]) for r in recs["cycling"]
                        if r["mesg"] == HR_ZONE and 1 in r["fields"]]
    prof["power_zones"] = [_get(files["cycling"], r["fields"][1]) for r in recs["cycling"]
                           if r["mesg"] == POWER_ZONE and 1 in r["fields"]]
    return prof


def _scale_zones(buf, recs, mesg, old, new, cap):
    """Scale zone limits by new/old (zones stay the same % of max HR / FTP)."""
    if not old or not new or old == new:
        return
    for r in recs:
        loc = r["mesg"] == mesg and r["fields"].get(1)
        if not loc:
            continue
        v = _get(buf, loc)
        if v in (0, 0xFF, 0xFFFF) or v >= cap:
            continue                      # zone 0 floor / open-ended top zone stay as they are
        _put(buf, loc, max(1, min(cap - 1, int(round(v * new / old)))))


def build(settings, cycling, changes):
    """Return (new_settings | None, new_cycling | None, {name: (old, new)}). Pure, no I/O."""
    for name, value in changes.items():
        if name not in FIELDS:
            raise KeyError(f"unknown field {name}")
        lo, hi = BOUNDS[name]
        if not lo <= float(value) <= hi:
            raise ValueError(f"{name}={value} out of range {lo}..{hi}")
    bufs = {"settings": bytearray(settings), "cycling": bytearray(cycling)}
    recs = {k: scan(bytes(v)) for k, v in bufs.items()}
    touched, result = set(), {}
    for name, value in changes.items():
        fk, mesg, fnum, scale = FIELDS[name]
        r = _first(recs[fk], mesg)
        loc = r and r["fields"].get(fnum)
        if not loc:
            raise ValueError(f"{name}: field {mesg}.{fnum} not in this device's file")
        old_raw = _get(bufs[fk], loc)
        new_raw = int(round(float(value) * scale))
        if old_raw == new_raw:
            continue
        _put(bufs[fk], loc, new_raw)
        touched.add(fk)
        result[name] = (old_raw / scale if scale != 1 else old_raw, value)
        zt = _first(recs["cycling"], ZONES_TARGET)
        if name == "max_hr" and zt and 5 in zt["fields"] and _get(bufs["cycling"], zt["fields"][5]) == 1:
            _scale_zones(bufs["cycling"], recs["cycling"], HR_ZONE, old_raw, new_raw, 255)
        if name == "ftp" and zt and 7 in zt["fields"] and _get(bufs["cycling"], zt["fields"][7]) == 1:
            _scale_zones(bufs["cycling"], recs["cycling"], POWER_ZONE, old_raw, new_raw, 2000)
    out = {}
    for fk in ("settings", "cycling"):
        if fk not in touched:
            out[fk] = None
            continue
        b = bufs[fk]
        end = b[0] + struct.unpack_from("<I", b, 4)[0]
        struct.pack_into("<H", b, end, fit_crc(b[:end]))
        scan(bytes(b))                     # re-parse: must still be a well-formed FIT
        out[fk] = bytes(b)
    return out["settings"], out["cycling"], result


def backup_dir():
    base = os.environ.get("SOMMET_BACKUP_DIR")
    if not base:
        if sys.platform.startswith("win"):
            base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Sommet")
        elif sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support/Sommet")
        else:
            base = os.path.expanduser("~/.local/share/Sommet")
        base = os.path.join(base, "edge-backups")
    return os.path.join(base, time.strftime("%Y%m%d-%H%M%S"))


def write(mount, changes):
    s, c = _paths(mount)
    settings, cycling = open(s, "rb").read(), open(c, "rb").read()
    new_s, new_c, result = build(settings, cycling, changes)
    if not result:
        return {"changed": {}, "written": []}
    bdir = backup_dir()
    os.makedirs(bdir, exist_ok=True)
    shutil.copy2(s, os.path.join(bdir, "Settings.fit"))
    shutil.copy2(c, os.path.join(bdir, "Cycling.fit"))
    nf = os.path.join(mount, NEWFILES_REL)
    if not os.path.isdir(nf):
        raise FileNotFoundError(f"{nf} missing")
    written = []
    for data, fname in ((new_s, "SETTINGS.FIT"), (new_c, "CYCLING.FIT")):
        if data is None:
            continue
        dst = os.path.join(nf, fname)
        tmp = dst + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, dst)
        if open(dst, "rb").read() != data:
            raise RuntimeError(f"verify failed for {dst}")
        written.append(dst)
    return {"changed": {k: {"old": o, "new": n} for k, (o, n) in result.items()},
            "written": written, "backup": bdir,
            "note": "Unplug the Edge: it reads Garmin/NewFiles when it leaves USB mode."}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Garmin Edge 500 profile read/write")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read"); r.add_argument("mount")
    w = sub.add_parser("write"); w.add_argument("mount")
    w.add_argument("--gender", type=int); w.add_argument("--age", type=int)
    w.add_argument("--height", type=int); w.add_argument("--weight", type=float)
    w.add_argument("--max-hr", dest="max_hr", type=int); w.add_argument("--lthr", type=int)
    w.add_argument("--rest-hr", dest="rest_hr", type=int); w.add_argument("--ftp", type=int)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "read":
            print(json.dumps(read(args.mount)))
            return 0
        changes = {n: getattr(args, n) for n in FIELDS if getattr(args, n, None) is not None}
        if not changes:
            print("nothing to write - pass at least one field (e.g. --ftp 250)", file=sys.stderr)
            return 2
        print(json.dumps(write(args.mount, changes)))
        return 0
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as exc:
        print(f"edge500_profile: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
