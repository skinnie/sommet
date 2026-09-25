#!/usr/bin/env python3
"""Bryton Aero 60 data screens - read and edit `System/Grid.ini` on the mounted device (USB mass
storage, no Bluetooth). Format read off André's own Aero 60 (2026-09-25) and the Bryton Active app
(SettingIniGridData / IniFileUtil / GridSettingActivity + its grid_table.json and lang_rider450.json;
the Aero 60 uses the Rider 450 grid):

    [PageN_Cycling]          N = 1..9
    <size>-<cell>=<fieldId>  one field list per layout size (2..10 fields), cells 1..size
    type=<size>              how many fields the screen shows now (its active layout)
    isEnabled=0|1|2          0 hidden, 1 shown, 2 always shown (the device's fixed screens)
    name=<id>                special screens: 1012 Lap 1, 1013 Lap 2, 1014 Follow Track, 1015 Altitude
    [System] DataPage=1

Pages 8/9 (Follow Track / Altitude) only have the 2-field layout. Edits change values of keys that
already exist (like the app: it only sets a key it finds), keep the line order and the file's
trailing NUL, write atomically, and keep a one-time Grid.ini.bak next to it.

    ./tools/bryton_grid.py read  <mount>
    ./tools/bryton_grid.py write <mount> '<JSON [{"page":1,"count":4,"fields":[2,16,8,5],"enabled":1}]>'
    ./tools/bryton_grid.py fields
"""

import json
import os
import re
import shutil
import sys

GRID_PATH = os.path.join("System", "Grid.ini")
SPECIAL = {"1012": "Lap 1", "1013": "Lap 2", "1014": "Follow Track", "1015": "Altitude"}

# (group, [(fieldId, name)]) in the app's picker order (grid_table.json rider450 GridGroup + Group,
# names = lang_rider450.json ENG). Every cell offers the same groups on the Aero 60.
GROUPS = [
    ("Time", [
        (7, "Time"), (8, "Ride Time"), (9, "Trip Time"), (51, "Lap Time"), (60, "Lap Count"), (52, "Last Lap Time"), (10, "Sunrise"), (11, "Sunset"),
    ]),
    ("Speed", [
        (2, "Speed"), (3, "Avg Speed"), (4, "Max Speed"), (46, "Lap Avg Speed"), (48, "Last Lap Avg Speed"), (47, "Lap Max Speed"),
    ]),
    ("Distance", [
        (5, "Distance"), (49, "Lap Distance"), (59, "ODO"), (50, "Last Lap Distance"), (64, "Trip1"), (65, "Trip2"),
    ]),
    ("Altitude", [
        (14, "Altitude"), (18, "Grade"), (16, "Alt. Gain"), (17, "Alt. Loss"), (19, "Uphill Dist."), (20, "Downhill Dist."), (15, "Max Alt."),
    ]),
    ("Energy", [
        (81, "Power Kilojoules"), (0, "Calories"),
    ]),
    ("Temperature", [
        (1, "Temp."),
    ]),
    ("HR", [
        (21, "Heart Rate"), (22, "Avg Heart Rate"), (23, "Max Heart Rate"), (43, "Max Heart Rate %"), (44, "LTHR%"), (80, "Heart Rate Zone"), (42, "LTHR Zone"), (53, "Lap Avg HR"), (56, "Lap LTHR%"), (55, "Lap MHR %"), (57, "Last Lap  Avg HR"),
    ]),
    ("Cadence", [
        (24, "Cadence"), (25, "Avg Cadence"), (26, "Max Cadence"), (61, "Lap Avg Cadence"), (58, "Last Lap  Avg Cadence"),
    ]),
    ("Power", [
        (29, "Power Now"), (31, "Avg Power"), (30, "Max Power"), (38, "Lap Avg  Power"), (39, "Lap Max  Power"), (28, "3s Power"), (79, "10s Power"), (27, "30s Power"), (69, "Normalized Power"), (70, "Training Stress Score"), (68, "Intensity Factor"), (66, "Specific Power"), (32, "FTP Zone"), (36, "FTP%"), (33, "MAP Zone"), (37, "MAP%"), (89, "Lap  Normalized Power"), (40, "Last Lap Avg Power"), (41, "Last Lap  Max Power"), (91, "Left Power"), (92, "Right Power"),
    ]),
    ("Pedal Analysis", [
        (71, "Current PB L-R"), (72, "Avg PB L-R"), (76, "Current PS L-R"), (77, "Avg PS L-R"), (78, "Max PS L-R"), (73, "Current TE L-R"), (74, "Avg TE L-R"), (75, "Max TE L-R"),
    ]),
    ("Heading", [
        (82, "Heading"),
    ]),
    ("Di2 / E-Shifting", [
        (83, "Di2 battery level"), (84, "Front Gear"), (85, "Rear Gear"), (86, "Gears"), (87, "Gear Combo"), (88, "Gear Ratio"), (90, "ESS battery level"),
    ]),
]
FIELDS = {fid: name for _, items in GROUPS for fid, name in items}

# Layout geometry per size: [[width %, height %] per cell] (grid_table.json rider450 GridTable).
GRID_TABLE = {"2": [[100, 75], [100, 25]], "3": [[100, 75], [50, 25], [50, 25]], "4": [[100, 33], [100, 33], [50, 34], [50, 34]], "5": [[100, 50], [50, 25], [50, 25], [50, 25], [50, 25]], "6": [[50, 33], [50, 33], [50, 33], [50, 33], [50, 34], [50, 34]], "7": [[50, 33], [50, 33], [50, 33], [50, 33], [100, 16], [50, 17], [50, 17]], "8": [[50, 33], [50, 33], [50, 33], [50, 33], [50, 16], [50, 16], [50, 17], [50, 17]], "9": [[50, 33], [50, 33], [100, 16], [50, 16], [50, 16], [50, 17], [50, 17], [50, 17], [50, 17]], "10": [[50, 33], [50, 33], [50, 16], [50, 16], [50, 16], [50, 16], [50, 17], [50, 17], [50, 17], [50, 17]]}

_KEY = re.compile(r"^(\d+)-(\d+)$")


def _load(mount):
    raw = open(os.path.join(mount, GRID_PATH), "rb").read()
    tail = raw[len(raw.rstrip(b"\x00")):]           # the device NUL-terminates the file
    lines = raw[:len(raw) - len(tail)].decode("utf-8").split("\n")
    return lines, tail


def _sections(lines):
    """{section: {key: line index}} over the raw lines."""
    out, cur = {}, None
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            cur = s[1:-1]
            out[cur] = {}
        elif cur is not None and "=" in s:
            out[cur][s.split("=", 1)[0].strip()] = i
    return out


def _val(lines, idx):
    return lines[idx].split("=", 1)[1].strip()


def read(mount):
    lines, _ = _load(mount)
    secs = _sections(lines)
    pages = []
    for name, keys in secs.items():
        m = re.match(r"^Page(\d+)_Cycling$", name)
        if not m:
            continue
        sizes = sorted({int(_KEY.match(k).group(1)) for k in keys if _KEY.match(k)})
        count = int(_val(lines, keys["type"])) if "type" in keys else (sizes[0] if sizes else 0)
        layouts = {n: [int(_val(lines, keys[f"{n}-{c}"])) for c in range(1, n + 1) if f"{n}-{c}" in keys]
                   for n in sizes}
        special = _val(lines, keys["name"]) if "name" in keys else None
        enabled = int(_val(lines, keys["isEnabled"])) if "isEnabled" in keys else 1
        pages.append({"page": int(m.group(1)), "title": SPECIAL.get(special, f"Data {m.group(1)}"),
                      "special": special, "enabled": enabled, "fixed": enabled == 2,
                      "count": count, "sizes": sizes, "fields": layouts.get(count, []),
                      "layouts": {str(n): v for n, v in layouts.items()}})
    return sorted(pages, key=lambda p: p["page"])


def write(mount, changes):
    """changes: [{"page", "count"?, "fields"?, "enabled"?}] - validated against the file before any
    byte is written; raises ValueError."""
    path = os.path.join(mount, GRID_PATH)
    lines, tail = _load(mount)
    secs = _sections(lines)
    for ch in changes:
        sec = f"Page{int(ch['page'])}_Cycling"
        keys = secs.get(sec)
        if keys is None:
            raise ValueError(f"no screen {ch['page']} on this device")
        sizes = sorted({int(_KEY.match(k).group(1)) for k in keys if _KEY.match(k)})
        count = int(ch.get("count") or _val(lines, keys["type"]))
        if count not in sizes:
            raise ValueError(f"screen {ch['page']}: {count} fields isn't a layout it has ({sizes})")
        if "count" in ch:
            lines[keys["type"]] = f"type={count}"
        if ch.get("fields") is not None:
            fields = [int(f) for f in ch["fields"]]
            if len(fields) != count:
                raise ValueError(f"screen {ch['page']}: {count} fields expected, got {len(fields)}")
            for f in fields:
                if f not in FIELDS:
                    raise ValueError(f"screen {ch['page']}: unknown field id {f}")
            for c, f in enumerate(fields, 1):
                k = f"{count}-{c}"
                if k not in keys:
                    raise ValueError(f"screen {ch['page']}: no cell {k} in the file")
                lines[keys[k]] = f"{k}={f}"
        if ch.get("enabled") is not None:
            cur = int(_val(lines, keys["isEnabled"])) if "isEnabled" in keys else None
            want = int(ch["enabled"])
            if cur == 2 and want != 2:
                raise ValueError(f"screen {ch['page']} is always shown on the device")
            if want not in (0, 1) and not (cur == 2 and want == 2):
                raise ValueError("enabled must be 0 or 1")
            if "isEnabled" not in keys:
                raise ValueError(f"screen {ch['page']} has no isEnabled key")
            lines[keys["isEnabled"]] = f"isEnabled={want}"
    data = "\n".join(lines).encode("utf-8") + tail
    bak = path + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(path, bak)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return read(mount)


def catalogue():
    return {"groups": [{"group": g, "fields": [{"id": i, "name": n} for i, n in items]} for g, items in GROUPS],
            "gridTable": GRID_TABLE}


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    try:
        if argv and argv[0] == "read":
            print(json.dumps({"ok": True, "pages": read(argv[1])}))
        elif argv and argv[0] == "write":
            print(json.dumps({"ok": True, "pages": write(argv[1], json.loads(argv[2]))}))
        else:
            print(json.dumps({"ok": True, **catalogue()}))
        return 0
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
