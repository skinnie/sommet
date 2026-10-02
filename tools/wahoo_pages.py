#!/usr/bin/env python3
"""Wahoo ELEMNT data screens over USB (adb) - read the page layout and edit the custom pages,
without the Wahoo companion app (André, 2026-10-02: "2nd step change screens, I prefer cable
than bluetooth ... the aim is to be independent of wahoo companion app").

    ./tools/wahoo_pages.py list                       # JSON: pages + their fields
    ./tools/wahoo_pages.py fields                     # JSON: the field catalog, grouped
    ./tools/wahoo_pages.py set-custom '[[201,70,60],[180,102]]'   # replace ALL custom pages
    (every command takes --serial S; the first Wahoo on adb is used otherwise)

How it works (found 2026-10-02 on André's original ELEMNT, BoltApp 1.77.10.1):
  * READ. The device rewrites /sdcard/config_backup/elemnt_config.zip ~5 s after any settings
    change, as long as that folder exists. The zip holds bolt-65535.cfg, a TLV file
    (u8 ver, u16 profile, u16 count, then {u16 id, u16 len, data}); field 53 is the display
    config:  01 | len u16 | next_page_id u16 | n u8 | n x page
    page:    01 | len u16 | id u16 | type u8 | b4 | 01 00 | ff ff | k u8 | k x u16 field type
    (field/page type numbers are Wahoo's CruxDefnType / CruxBoltDisplayPageType).
  * WRITE. The device app takes adb broadcasts (BADisplayCfgManager): ADD_CUSTOM_PAGE with
    extra defns="<field ids>" appends a custom page (type 5); DEL_LAST_PAGE removes the last
    page. Both save straight to the device's display config. There's no "edit page N", so
    custom pages are edited by popping them off the end and re-adding them in order. Built-in pages
    (Workout, Lap, Elevation, Map, ...) are never touched: changing those needs Bluetooth, as
    the companion app does it. RESET (factory layout) is deliberately not used.
  * /sdcard/config_restore is NOT a way in: the app only applies it during first-run setup
    (after a factory reset).
"""

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile

WAHOO_PACKAGE = "com.wahoofitness.bolt"
BACKUP_DIR = "/sdcard/config_backup"
BACKUP_ZIP = BACKUP_DIR + "/elemnt_config.zip"
ACTION = "com.wahoofitness.bolt.service.displaycfg.BADisplayCfgManager."
DISPLAY_CFG_FIELD = 53
CUSTOM_PAGE_TYPE = 5
MAX_FIELDS = 11            # the built-in Workout page holds 10-11; more won't fit on screen

PAGE_TYPES = {0: "Workout", 1: "Lap", 2: "Elevation", 3: "Map", 4: "KICKR", 5: "Custom",
              6: "Graph", 8: "Segment", 15: "Planned workout", 25: "Pedal monitor"}

# Field catalog: Wahoo's CruxDefnType ids, cycling-relevant ones only (no swim/run/treadmill),
# grouped the way the picker shows them.
FIELDS = [
    ("Speed", [(201, "Speed"), (158, "Speed (vs avg)"), (2, "Avg speed"), (5, "Max speed"),
               (1, "Lap avg speed"), (155, "Lap avg speed (vs avg)"), (6, "Lap max speed"),
               (3, "Last lap avg speed"), (4, "Best avg speed"), (373, "Min speed")]),
    ("Distance", [(10, "Distance"), (11, "Lap distance"), (254, "Last lap distance")]),
    ("Time", [(30, "Time of day"), (32, "Ride time"), (31, "Total time"), (33, "Paused time"),
              (34, "Lap time"), (35, "Last lap time"), (36, "Best lap time"), (141, "Start time")]),
    ("Climbing", [(45, "Elevation"), (40, "Ascent"), (41, "Descent"), (44, "Grade"),
                  (47, "Vertical speed"), (46, "Max elevation"), (237, "Min elevation"),
                  (257, "Avg grade"), (42, "Lap ascent"), (43, "Lap descent"),
                  (258, "Lap avg grade"), (255, "Last lap ascent"), (256, "Last lap descent")]),
    ("Heart rate", [(70, "Heart rate"), (71, "Avg HR"), (73, "Max HR"), (72, "Lap avg HR"),
                    (74, "Lap max HR"), (268, "Last lap avg HR"), (342, "Last lap max HR"),
                    (75, "HR zone"), (76, "HR % of max")]),
    ("Cadence", [(60, "Cadence"), (61, "Avg cadence"), (63, "Max cadence"),
                 (62, "Lap avg cadence"), (64, "Lap max cadence"), (269, "Last lap avg cadence")]),
    ("Power", [(180, "Power 3s"), (392, "Power"), (161, "Power 5s"), (162, "Power 20s"),
               (163, "Power 30s"), (164, "Power 1min"), (165, "Power 5min"), (166, "Power 20min"),
               (102, "Avg power"), (104, "Max power"), (103, "Lap avg power"),
               (105, "Lap max power"), (260, "Last lap avg power"), (107, "Normalized power"),
               (272, "Lap NP"), (108, "Intensity factor"), (106, "TSS"), (310, "Variability index"),
               (101, "Power/weight"), (273, "% FTP"), (277, "% FTP 3s"), (244, "Power zone"),
               (109, "L/R balance"), (282, "Pedal smoothness"), (286, "Torque effectiveness"),
               (91, "Work (kJ)")]),
    ("Navigation", [(20, "Distance to destination"), (21, "Distance to next turn"),
                    (22, "ETA"), (23, "Time to destination"), (433, "Ascent remaining"),
                    (24, "Heading")]),
    ("Climb", [(427, "Climb ascent left"), (428, "Climb distance left"),
               (429, "Climb time left"), (431, "Climb avg grade"), (435, "Climb grade left")]),
    ("Segment", [(238, "Segment time"), (239, "Segment ahead/behind"), (240, "Segment target time"),
                 (241, "Segment distance left"), (242, "Segment estimated time")]),
    ("Lap", [(154, "Lap number")]),
    ("Workout", [(294, "Target power"), (295, "Target cadence"), (296, "Target HR"),
                 (297, "Interval time left"), (298, "Workout time left"),
                 (299, "Interval count")]),
    ("Other", [(90, "Calories"), (50, "Temperature"), (53, "Avg temperature"),
               (130, "ELEMNT battery"), (210, "Gear"), (216, "Gear (visual)"),
               (217, "Gear ratio"), (316, "Tyre pressure")]),
]
FIELD_NAMES = {fid: name for _g, fields in FIELDS for fid, name in fields}


def _adb(*args, serial=None, timeout=30):
    exe = shutil.which("adb")
    if not exe:
        raise RuntimeError("adb not found - install android-tools / platform-tools")
    cmd = [exe] + (["-s", serial] if serial else []) + list(args)
    # stdin=DEVNULL: `adb shell` forwards our stdin to the device, so without it the first adb
    # call swallowed the page list the backend pipes in (found 2026-10-02).
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                         stdin=subprocess.DEVNULL)
    return out.returncode, out.stdout.replace("\r", "")


def find_serial(serial=None):
    """The adb serial of a connected Wahoo (has the com.wahoofitness.bolt app)."""
    _c, out = _adb("devices", timeout=15)
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device" or (serial and parts[0] != serial):
            continue
        _c, pk = _adb("shell", "pm list packages " + WAHOO_PACKAGE, serial=parts[0])
        if "package:" + WAHOO_PACKAGE in pk:
            return parts[0]
    raise RuntimeError("No Wahoo ELEMNT on adb - press power twice and re-plug the USB cable")


def _remote_mtime(serial):
    """Modification stamp of the backup zip (or None). `ls -l` is all the old toolbox has."""
    _c, out = _adb("shell", "ls -l " + BACKUP_ZIP, serial=serial, timeout=10)
    line = out.strip()
    return line if line and "No such file" not in line else None


def decode_display_cfg(data):
    ver = data[0]
    _ln, next_id = struct.unpack_from("<HH", data, 1)
    count = data[5]
    o = 6
    pages = []
    for _ in range(count):
        pl, = struct.unpack_from("<H", data, o + 1)
        body = data[o + 3:o + 3 + pl]
        o += 3 + pl
        pid, = struct.unpack_from("<H", body, 0)
        ptype = body[2]
        k = body[8]
        fields = list(struct.unpack_from("<%dH" % k, body, 9))
        pages.append({
            "id": pid,
            "type": ptype,
            "typeName": PAGE_TYPES.get(ptype, "Page type %d" % ptype),
            "custom": ptype == CUSTOM_PAGE_TYPE,
            "fields": [{"id": f, "name": FIELD_NAMES.get(f, "Field %d" % f)} for f in fields],
        })
    if o != len(data):
        raise ValueError("display config: %d trailing bytes" % (len(data) - o))
    return {"version": ver, "nextPageId": next_id, "pages": pages}


def decode_cfg(cfg):
    """bolt-*.cfg TLV -> {field_id: bytes}."""
    ver = cfg[0]
    _profile, count = struct.unpack_from("<HH", cfg, 1)
    o = 5
    fields = {}
    for _ in range(count):
        if ver == 0:
            fid, = struct.unpack_from("<H", cfg, o); o += 2
        else:
            fid, = struct.unpack_from("<I", cfg, o); o += 4
        ln, = struct.unpack_from("<H", cfg, o); o += 2
        fields[fid] = cfg[o:o + ln]
        o += ln
    return fields


def _pull_layout(serial):
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, "elemnt_config.zip")
        code, _o = _adb("pull", BACKUP_ZIP, local, serial=serial, timeout=60)
        if code != 0 or not os.path.isfile(local):
            return None
        with zipfile.ZipFile(local) as z:
            cfg = z.read("bolt-65535.cfg")
    data = decode_cfg(cfg).get(DISPLAY_CFG_FIELD)
    if not data:
        raise RuntimeError("the ELEMNT's config has no display layout (field 53)")
    return decode_display_cfg(data)


def _broadcast(serial, action, defns=None):
    args = ["shell", "am broadcast -a " + ACTION + action]
    if defns is not None:
        args[1] += " --es defns '%s'" % " ".join(str(int(d)) for d in defns)
    code, out = _adb(*args, serial=serial, timeout=20)
    if code != 0 or "Broadcast completed" not in out:
        raise RuntimeError("adb broadcast %s failed" % action)


def _change_and_wait(serial, action, defns=None, timeout=25):
    """Send one layout change, then wait until the device has rewritten the backup zip."""
    # The old zip is removed first, so any zip that shows up afterwards is the fresh one.
    _adb("shell", "rm -f " + BACKUP_ZIP, serial=serial)
    _broadcast(serial, action, defns)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        if _remote_mtime(serial):
            time.sleep(1.5)           # let the zip finish writing
            return
    raise RuntimeError("the ELEMNT didn't confirm the change (no fresh config backup)")


def read_layout(serial):
    """The current page layout. Makes sure the live-backup folder exists; if there's no zip yet,
    nudges the device with an add+delete of an empty custom page (a no-op on the layout)."""
    _adb("shell", "mkdir -p " + BACKUP_DIR, serial=serial)
    layout = None
    if _remote_mtime(serial):
        layout = _pull_layout(serial)
    if layout is None:
        for _ in range(8):            # a first backup sometimes appears on its own within seconds
            time.sleep(1)
            if _remote_mtime(serial):
                layout = _pull_layout(serial)
                break
    if layout is None:
        _change_and_wait(serial, "ADD_CUSTOM_PAGE", [201])
        _change_and_wait(serial, "DEL_LAST_PAGE")
        layout = _pull_layout(serial)
    if layout is None:
        raise RuntimeError("couldn't read the ELEMNT's config backup")
    return layout


def set_custom(serial, wanted):
    """Make the trailing custom pages equal `wanted` (a list of field-id lists). Pages before
    the first custom page are built-ins and stay as they are. Only the pages that differ are
    popped and re-added, so an unchanged prefix is never rewritten."""
    for fields in wanted:
        if not fields:
            raise ValueError("a custom page needs at least one field")
        if len(fields) > MAX_FIELDS:
            raise ValueError("at most %d fields per page" % MAX_FIELDS)
        for f in fields:
            if int(f) not in FIELD_NAMES:
                raise ValueError("unknown field id %s" % f)
    layout = read_layout(serial)
    pages = layout["pages"]
    first_custom = len(pages)
    while first_custom > 0 and pages[first_custom - 1]["custom"]:
        first_custom -= 1
    if first_custom == 0:
        raise RuntimeError("refusing: the ELEMNT would be left with only custom pages")
    current = [[f["id"] for f in p["fields"]] for p in pages[first_custom:]]
    keep = 0
    while keep < min(len(current), len(wanted)) and current[keep] == [int(f) for f in wanted[keep]]:
        keep += 1
    for _ in range(len(current) - keep):
        _change_and_wait(serial, "DEL_LAST_PAGE")
    for fields in wanted[keep:]:
        _change_and_wait(serial, "ADD_CUSTOM_PAGE", fields)
    layout = _pull_layout(serial)
    got = [[f["id"] for f in p["fields"]] for p in layout["pages"] if p["custom"]]
    if got != [[int(f) for f in w] for w in wanted]:
        raise RuntimeError("the ELEMNT's layout doesn't match what was written: %s" % got)
    return layout


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["list", "fields", "set-custom"])
    ap.add_argument("pages", nargs="?", help="set-custom: JSON list of field-id lists")
    ap.add_argument("--serial")
    args = ap.parse_args()
    try:
        if args.command == "fields":
            out = {"ok": True, "maxFields": MAX_FIELDS,
                   "groups": [{"group": g, "fields": [{"id": i, "name": n} for i, n in f]}
                              for g, f in FIELDS]}
        else:
            wanted = None
            if args.command == "set-custom":
                wanted = json.loads(args.pages if args.pages is not None else sys.stdin.read())
            serial = find_serial(args.serial)
            if wanted is None:
                out = dict(read_layout(serial), ok=True)
            else:
                out = dict(set_custom(serial, wanted), ok=True)
            out["serial"] = serial
    except Exception as e:                     # noqa: BLE001 - one JSON error line for the UI
        out = {"ok": False, "error": str(e)}
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
