#!/usr/bin/env python3
"""Read and safely write the Bryton Aero 60 athlete profile (`System/Profile.bin`), stdlib only.

    ./tools/bryton_profile.py read  /media/.../BRYTON            # -> profile JSON
    ./tools/bryton_profile.py write /media/.../BRYTON --ftp 225 --lthr 186 --max-hr 205

The Aero 60 keeps the athlete profile the user sets in Settings->Profile (Gender, Birthday/age,
Height, Weight, Max HR, LTHR, FTP, MAP) inside `System/Profile.bin` (6695 B; the rest of that file
is display grid/zone-layout data, pairs with Grid.ini). Field offsets and the fact that writing is
safe were both proven on hardware (2026-09-24): patching LTHR 146->186 and FTP 209->225 directly in
the file made the device's Settings->Profile show the new values, so there is NO enforced checksum.

Two important shape facts (see the offset table below):
  * Some values are stored TWICE - a "user" copy and a zone-table header copy - and both must be
    written or the two screens disagree (Max HR, LTHR). Others live only in the zone header (FTP,
    MAP). All duplicate offsets per field are listed here and written together.
  * The zone tables (~0xcbb..0xd30) store PERCENT boundaries (FTP zones 55/75/90/105/120/150),
    independent of the base value - so changing a threshold does NOT require recomputing them.

Height/Weight are float32; the thresholds are little-endian uint16; Gender/Age are single bytes.
This tool only ever touches these known offsets and never changes the file's length. It backs up
Profile.bin to Profile.bin.bak (once) before its first write. Profile.bin is a config file on a FAT
volume, not firmware: the worst realistic failure is the profile resetting, recoverable from the
backup.
"""

import argparse
import json
import os
import struct
import sys

# field -> (kind, [offsets...]).  kind: "u16" | "u8" | "f32".  First offset is the canonical one;
# extra offsets are duplicate copies the device keeps in sync and that we write together.
FIELDS = {
    "gender":   ("u8",  [0x910]),   # 1 = male, 0 = female (device shows Male/Female)
    "age":      ("u8",  [0x911]),   # years (the device derives its Birthday view from this)
    "height":   ("f32", [0x912]),   # cm
    "weight":   ("f32", [0x91a]),   # kg
    "max_hr":   ("u16", [0x922, 0xcbb]),
    "lthr":     ("u16", [0x924, 0xcd9]),
    "rest_hr":  ("u16", [0x926]),   # resting HR (device default 100); best-effort
    "ftp":      ("u16", [0xd15]),
    "map":      ("u16", [0xcf7]),   # Maximal Aerobic Power (W); Bryton-only, no intervals.icu field
}

# Plausible ranges - a hard bounds check so a bad request can't scribble nonsense into the profile.
BOUNDS = {
    "gender": (0, 1), "age": (5, 120), "height": (80, 250), "weight": (20, 250),
    "max_hr": (100, 240), "lthr": (80, 230), "rest_hr": (30, 120),
    "ftp": (30, 600), "map": (40, 800),
}

PROFILE_REL = os.path.join("System", "Profile.bin")
EXPECT_SIZE = 6695


def _profile_path(mount: str) -> str:
    p = mount if mount.endswith("Profile.bin") else os.path.join(mount, PROFILE_REL)
    if not os.path.isfile(p):
        raise FileNotFoundError(f"no Profile.bin at {p}")
    return p


def _get(data: bytes, kind: str, off: int):
    if kind == "u8":
        return data[off]
    if kind == "u16":
        return struct.unpack_from("<H", data, off)[0]
    if kind == "f32":
        return round(struct.unpack_from("<f", data, off)[0], 1)
    raise ValueError(kind)


def read(mount: str) -> dict:
    data = open(_profile_path(mount), "rb").read()
    return {name: _get(data, kind, offs[0]) for name, (kind, offs) in FIELDS.items()}


def _pack(kind: str, value):
    if kind == "u8":
        return bytes([int(round(value)) & 0xFF])
    if kind == "u16":
        return struct.pack("<H", int(round(value)) & 0xFFFF)
    if kind == "f32":
        return struct.pack("<f", float(value))
    raise ValueError(kind)


def write(mount: str, changes: dict) -> dict:
    """Patch the given {field: value} into Profile.bin (all copies). Returns {field: (old, new)}.
    Backs up to Profile.bin.bak once. Validates every value against BOUNDS first."""
    for name, value in changes.items():
        if name not in FIELDS:
            raise KeyError(f"unknown profile field {name!r}")
        lo, hi = BOUNDS[name]
        if not (lo <= value <= hi):
            raise ValueError(f"{name}={value} out of range [{lo},{hi}] - refusing to write")

    path = _profile_path(mount)
    data = bytearray(open(path, "rb").read())
    if len(data) != EXPECT_SIZE:
        raise ValueError(f"Profile.bin is {len(data)} B, expected {EXPECT_SIZE} - refusing to write")

    bak = path + ".bak"
    if not os.path.exists(bak):
        with open(bak, "wb") as fh:
            fh.write(data)

    result = {}
    for name, value in changes.items():
        kind, offs = FIELDS[name]
        result[name] = (_get(data, kind, offs[0]), None)
        blob = _pack(kind, value)
        for off in offs:
            data[off:off + len(blob)] = blob
        result[name] = (result[name][0], _get(data, kind, offs[0]))

    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)

    # verify from disk
    back = open(path, "rb").read()
    if len(back) != EXPECT_SIZE:
        raise RuntimeError("post-write size changed - restore from Profile.bin.bak")
    for name, value in changes.items():
        kind, offs = FIELDS[name]
        got = _get(bytes(back), kind, offs[0])
        want = round(float(value), 1) if kind == "f32" else int(round(value))
        if got != want:
            raise RuntimeError(f"verify failed for {name}: wrote {want}, read {got}")
    return result


def _cmd_read(args):
    print(json.dumps(read(args.mount), indent=2))
    return 0


def _cmd_write(args):
    changes = {}
    for name in FIELDS:
        v = getattr(args, name.replace("-", "_"), None)
        if v is not None:
            changes[name] = v
    if not changes:
        print("nothing to write - pass at least one field (e.g. --ftp 225)", file=sys.stderr)
        return 2
    res = write(args.mount, changes)
    for name, (old, new) in res.items():
        print(f"  {name}: {old} -> {new}")
    print(f"OK (backup at {os.path.join(args.mount, PROFILE_REL)}.bak)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bryton Aero 60 profile read/write")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read"); r.add_argument("mount"); r.set_defaults(fn=_cmd_read)
    w = sub.add_parser("write"); w.add_argument("mount")
    w.add_argument("--gender", type=int); w.add_argument("--age", type=int)
    w.add_argument("--height", type=float); w.add_argument("--weight", type=float)
    w.add_argument("--max-hr", dest="max_hr", type=int)
    w.add_argument("--lthr", type=int); w.add_argument("--rest-hr", dest="rest_hr", type=int)
    w.add_argument("--ftp", type=int); w.add_argument("--map", dest="map", type=int)
    w.set_defaults(fn=_cmd_write)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as exc:
        print(f"bryton_profile: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
