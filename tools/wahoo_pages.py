#!/usr/bin/env python3
"""Wahoo ELEMNT data pages - read and edit the whole page layout (built-in pages too) over the
USB cable or Bluetooth, without the Wahoo companion app (André, 2026-10-02: "2nd step change
screens, I prefer cable than bluetooth..but both can come"; then "why cant we do BLE and Cable
for page editing?").

    ./tools/wahoo_pages.py list   [--via auto|usb|ble]     # JSON: pages + their fields
    ./tools/wahoo_pages.py fields                          # JSON: the field catalog, grouped
    ./tools/wahoo_pages.py set    [--via ...] '<json>'     # write a new layout (or JSON on stdin)

`set` takes {"pages": [{"id": 1, "fields": [10, 158, ...], "enabled": true}, {"new": true,
"fields": [201, 70]} ...]} in display order. Built-in pages must all be listed (they can be
reordered and their fields swapped, but keep their field count); custom pages may be added or
left out (deleted). Every write is read back from the device and compared.

The layout format (found on André's original ELEMNT, BoltApp 1.77.10.1, 2026-10-02) is the same
on both links ("V0"):
    00 | next_page_id u16 | count u8 | count x page
    page: 00 | id u16 | type u8 | b4 u8 | enabled u8 | name_len u8 + name | link u16 | k u8 |
          k x u16 field type          (field/page types = Wahoo's CruxDefnType / page types)
  * USB: adb on the ELEMNT is a root shell. The layout is the {"type":53,"boltId":""} entry of
    /data/data/com.wahoofitness.bolt/shared_prefs/BCfgManager-Bolt.xml, stored as a Java signed
    byte array "[0,10,0,8,...]". The app keeps that file in memory and the launcher restarts it
    within ~1 s of a force-stop, so the new file is swapped in with ONE device-side command
    (force-stop; cat over the file - cat keeps owner/mode); the restarted app loads it.
  * BLE: GATT service a026ee06-...; characteristic a026e019. Get = "06 <req>"; the layout comes
    back as blob packets "[3 more | 4 last] <req> <seq> <payload>", acked "05 <req> <seq> 00".
    Set = the same blob framing towards the device, which acks every packet "05 <req> <seq> 01"
    and "... 00" when complete (same framing as the companion app uses). The ELEMNT
    rotates its BLE address, so it is found by name; it only advertises while no phone holds it.
"""

import argparse
import asyncio
import html
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import time

WAHOO_PACKAGE = "com.wahoofitness.bolt"
PREFS_FILE = "/data/data/com.wahoofitness.bolt/shared_prefs/BCfgManager-Bolt.xml"
PREFS_KEY = 'name="{&quot;type&quot;:53,&quot;boltId&quot;:&quot;&quot;}">'
BLE_SUFFIX = "-0a7d-4ab3-97fa-f1500f9feb8b"
BLE_CFG = "a026e019" + BLE_SUFFIX
BLE_KEEPALIVE = "a026e01c" + BLE_SUFFIX
CUSTOM_PAGE_TYPE = 5
RESIZABLE_TYPES = {0, 1, CUSTOM_PAGE_TYPE}   # Workout, Lap, Custom: field count may change
MAX_FIELDS = 11            # the built-in Workout page holds 10-11; more won't fit on screen

PAGE_TYPES = {0: "Workout", 1: "Lap", 2: "Elevation", 3: "Map", 4: "KICKR", 5: "Custom",
              6: "Graph", 8: "Segment", 15: "Planned workout", 25: "Pedal monitor"}

# Field catalog: Wahoo's CruxDefnType ids, cycling-relevant ones only (no swim/run/treadmill),
# grouped the way the picker shows them.
FIELDS = [
    ("Speed", [(201, "Current Speed"), (158, "Current Speed (vs workout avg)"), (2, "Avg Speed (workout)"), (5, "Max Speed (workout)"),
               (1, "Avg Speed (lap)"), (155, "Current Speed (vs lap avg)"), (6, "Max Speed (lap)"),
               (3, "Avg Speed (last lap)"), (4, "Avg Speed (fastest lap)"), (373, "Min speed")]),
    ("Distance", [(10, "Distance (workout)"), (11, "Distance (lap)"), (254, "Distance (last lap)")]),
    ("Time", [(30, "Time of Day"), (32, "Active Time (workout)"), (31, "Total Time (overall)"), (33, "Paused Time (workout)"),
              (34, "Current Lap Active Time"), (35, "Last lap time"), (36, "Best lap time"), (141, "Workout Start Time")]),
    ("Climbing", [(45, "Current Elevation"), (40, "Total Ascent (workout)"), (41, "Total Descent (workout)"), (44, "Grade"),
                  (47, "VAM (m/hr)"), (46, "Max Elevation (workout)"), (237, "Min Elevation (workout)"),
                  (257, "Avg Grade (workout)"), (42, "Total Ascent (lap)"), (43, "Total Descent (lap)"),
                  (258, "Avg Grade (lap)"), (255, "Total Ascent (last lap)"), (256, "Total Descent (last lap)")]),
    ("Heart rate", [(70, "Heart Rate"), (71, "Avg Heart Rate (workout)"), (73, "Max Heart Rate (workout)"), (72, "Avg Heart Rate (lap)"),
                    (74, "Max Heart Rate (lap)"), (268, "Avg Heart Rate (last lap)"), (342, "Max Heart Rate (last lap)"),
                    (75, "Heart Rate Zone"), (76, "HR % of max")]),
    ("Cadence", [(60, "Cadence"), (61, "Avg Cadence (workout)"), (63, "Max Cadence (workout)"),
                 (62, "Avg Cadence (lap)"), (64, "Max Cadence (lap)"), (269, "Avg Cadence (last lap)")]),
    ("Power", [(180, "Avg Power (3 sec)"), (392, "Power"), (161, "Avg Power (5 sec)"), (162, "Avg Power (20 sec)"),
               (163, "Avg Power (30 sec)"), (164, "Avg Power (1 min)"), (165, "Avg Power (5 min)"), (166, "Avg Power (20 min)"),
               (102, "Avg Power (workout)"), (104, "Max Power (workout)"), (103, "Avg Power (lap)"),
               (105, "Max Power (lap)"), (260, "Avg Power (last lap)"), (107, "Normalized Power"),
               (272, "Lap NP"), (108, "Intensity Factor"), (106, "Training Stress Score"), (310, "Variability Index"),
               (101, "Power To Weight (Watts/Kg)"), (273, "Power/FTP % (workout)"), (277, "Power (3 sec)/FTP %"), (244, "Power Zone"),
               (109, "Left/Right Balance"), (282, "Pedal Smoothness"), (286, "Torque Effectiveness"),
               (91, "Kilojoules (workout)"), (157, "Power (vs workout avg)"), (156, "Power (vs lap avg)")]),
    ("Navigation", [(20, "Distance Remaining (route)"), (21, "Distance Next Cue"),
                    (22, "ETA"), (23, "Time to destination"), (433, "Ascent Remaining (route)"),
                    (24, "Heading")]),
    ("Climb", [(427, "Ascent Remaining (summit)"), (428, "Distance Remaining (summit)"),
               (429, "Climb time left"), (431, "Avg Grade (summit)"), (435, "Avg Grade Remaining (summit)")]),
    ("Segment", [(238, "Segment time"), (239, "Segment ahead/behind"), (240, "Segment target time"),
                 (241, "Segment distance left"), (242, "Segment estimated time")]),
    ("Lap", [(154, "Lap Number")]),
    ("Workout", [(294, "Target Power"), (295, "Target Cadence"), (296, "Target Heartrate"),
                 (297, "Interval time left"), (298, "Workout time left"),
                 (299, "Interval Count")]),
    ("Other", [(90, "Calories (workout)"), (50, "Temperature"), (53, "Avg Temperature (workout)"),
               (130, "Battery"), (210, "Current Gear"), (216, "Current Gear (visual)"),
               (217, "Current Gear Ratio"), (316, "Tyre Pressure")]),
]
FIELD_NAMES = {fid: name for _g, fields in FIELDS for fid, name in fields}


# ---- layout codec (V0, identical on USB and BLE) ---------------------------------------------

def decode_layout(blob):
    """V0 (Bluetooth; the settings file until the app re-saves it) or V1 (what the ELEMNT app
    writes to its settings file after a restart - seen 2026-10-03). Same page body; V1 adds a
    total length after the version and a length after each page's record version."""
    if not blob or blob[0] not in (0, 1):
        raise ValueError("unknown layout version %r" % (blob[:1],))
    fmt = blob[0]
    o = 1
    if fmt == 1:
        total, = struct.unpack_from("<H", blob, o)
        if total != len(blob) - 3:
            raise ValueError("layout V1: length %d, have %d" % (total, len(blob) - 3))
        o += 2
    next_id, = struct.unpack_from("<H", blob, o)
    count = blob[o + 2]
    o += 3
    pages = []
    for _ in range(count):
        if blob[o] != fmt:
            raise ValueError("page record version %d at %d (layout V%d)" % (blob[o], o, fmt))
        o += 1
        if fmt == 1:
            plen, = struct.unpack_from("<H", blob, o)
            o += 2
            end = o + plen
        pid, = struct.unpack_from("<H", blob, o)
        ptype, b4, enabled, name_len = blob[o + 2], blob[o + 3], blob[o + 4], blob[o + 5]
        name = blob[o + 6:o + 6 + name_len].decode("utf-8", "replace")
        o += 6 + name_len
        link, = struct.unpack_from("<H", blob, o)
        k = blob[o + 2]
        fields = list(struct.unpack_from("<%dH" % k, blob, o + 3))
        o += 3 + 2 * k
        if fmt == 1 and o != end:
            raise ValueError("layout V1: page %d length mismatch" % pid)
        pages.append({
            "id": pid, "type": ptype, "typeName": PAGE_TYPES.get(ptype, "Page type %d" % ptype),
            "custom": ptype == CUSTOM_PAGE_TYPE, "resizable": ptype in RESIZABLE_TYPES,
            "enabled": bool(enabled), "name": name, "b4": b4, "link": link,
            "fields": [{"id": f, "name": FIELD_NAMES.get(f, "Field %d" % f)} for f in fields],
        })
    if o != len(blob):
        raise ValueError("layout: %d trailing bytes" % (len(blob) - o))
    return {"format": fmt, "nextPageId": next_id, "pages": pages}


def encode_layout(layout, fmt=None):
    """Encode in `fmt` (0 or 1; default: the format the layout was read in)."""
    fmt = layout.get("format", 0) if fmt is None else fmt
    body = bytearray()
    for p in layout["pages"]:
        name = p.get("name", "").encode("utf-8")
        fields = [f["id"] if isinstance(f, dict) else int(f) for f in p["fields"]]
        rec = struct.pack("<H", p["id"])
        rec += bytes([p["type"], p["b4"], 1 if p["enabled"] else 0, len(name)]) + name
        rec += struct.pack("<H", p["link"]) + bytes([len(fields)])
        rec += struct.pack("<%dH" % len(fields), *fields)
        body += bytes([fmt]) + (struct.pack("<H", len(rec)) if fmt == 1 else b"") + rec
    head = struct.pack("<H", layout["nextPageId"]) + bytes([len(layout["pages"])])
    out = head + body
    if fmt == 1:
        return bytes([1]) + struct.pack("<H", len(out)) + out
    return bytes([0]) + out


def apply_edit(current, wanted):
    """Build the new layout from the current one and the requested page list (display order).
    Built-in pages: all must stay, same field count; custom pages: free."""
    by_id = {p["id"]: p for p in current["pages"]}
    next_id = current["nextPageId"]
    seen = set()
    pages = []
    for w in wanted:
        fields = [int(f) for f in w.get("fields", [])]
        if not fields:
            raise ValueError("a page needs at least one field")
        if len(fields) > MAX_FIELDS:
            raise ValueError("at most %d fields per page" % MAX_FIELDS)
        if w.get("new"):
            p = {"id": next_id, "type": CUSTOM_PAGE_TYPE, "b4": 5, "link": 0xFFFF, "name": "",
                 "enabled": True}
            next_id += 1
        else:
            if w.get("id") not in by_id:
                raise ValueError("unknown page id %r" % w.get("id"))
            if w["id"] in seen:
                raise ValueError("page %d listed twice" % w["id"])
            seen.add(w["id"])
            p = dict(by_id[w["id"]])
            if p["type"] not in RESIZABLE_TYPES and len(fields) != len(p["fields"]):
                raise ValueError("%s page keeps %d fields" % (p["typeName"], len(p["fields"])))
        p["fields"] = fields
        p["enabled"] = bool(w.get("enabled", p["enabled"]))
        if "name" in w:                       # "" = the ELEMNT's default name for the page type
            name = str(w["name"]).strip()
            if len(name.encode("utf-8")) > 30:
                raise ValueError("page names are at most 30 characters")
            p["name"] = name
        pages.append(p)
    missing = [p for pid, p in by_id.items() if pid not in seen and not p["custom"]]
    if missing:
        raise ValueError("built-in pages can't be removed: %s"
                         % ", ".join(p["typeName"] for p in missing))
    if not any(p["enabled"] for p in pages):
        raise ValueError("at least one page must stay enabled")
    return {"format": current.get("format", 0), "nextPageId": next_id, "pages": pages}


# ---- USB (adb, root shell on the ELEMNT) -------------------------------------------------------

def _adb(*args, serial=None, timeout=30, text=True):
    exe = shutil.which("adb")
    if not exe:
        raise RuntimeError("adb not found - install android-tools / platform-tools")
    cmd = [exe] + (["-s", serial] if serial else []) + list(args)
    # stdin=DEVNULL: `adb shell` forwards our stdin to the device (it ate the piped JSON once).
    out = subprocess.run(cmd, capture_output=True, text=text, timeout=timeout,
                         stdin=subprocess.DEVNULL)
    return out.returncode, (out.stdout.replace("\r", "") if text else out.stdout)


def wahoo_on_usb_bus():
    """True if a WahooFitness device is on the USB bus (Linux sysfs) - used to tell "not
    plugged" from "plugged but the adb server never noticed it"."""
    import glob
    for m in glob.glob("/sys/bus/usb/devices/*/manufacturer"):
        try:
            if "wahoo" in open(m).read().lower():
                return True
        except OSError:
            pass
    return False


def find_serial(serial=None, _retry=True):
    """The adb serial of a connected Wahoo (has the com.wahoofitness.bolt app), or None."""
    if not shutil.which("adb"):
        return None
    _c, out = _adb("devices", timeout=15)
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device" or (serial and parts[0] != serial):
            continue
        _c, pk = _adb("shell", "pm list packages " + WAHOO_PACKAGE, serial=parts[0])
        if "package:" + WAHOO_PACKAGE in pk:
            return parts[0]
    # Seen on André's X230 (2026-10-02/03): the ELEMNT is on the USB bus with its adb interface
    # up, but an adb server started earlier never picks it up. Restart the server once.
    if _retry and wahoo_on_usb_bus():
        _adb("kill-server", timeout=15)
        _adb("start-server", timeout=30)
        return find_serial(serial, _retry=False)
    return None


def _usb_prefs(serial):
    code, xml = _adb("exec-out", "cat " + PREFS_FILE, serial=serial)
    if code != 0 or PREFS_KEY not in xml:
        raise RuntimeError("couldn't read the ELEMNT's settings over USB (is adb root?)")
    return xml


def _prefs_blob(xml):
    i = xml.index(PREFS_KEY) + len(PREFS_KEY)
    j = xml.index("</string>", i)
    return bytes(int(x) & 255 for x in html.unescape(xml[i:j]).strip("[]").split(",")), i, j


def usb_read(serial):
    return _prefs_blob(_usb_prefs(serial))[0]


def usb_write(serial, blob):
    xml = _usb_prefs(serial)
    _old, i, j = _prefs_blob(xml)
    arr = "[" + ",".join(str(b - 256 if b > 127 else b) for b in blob) + "]"
    new = xml[:i] + arr + xml[j:]
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as fh:
        fh.write(new)
        local = fh.name
    tmp = "/data/local/tmp/sommet_bcfg.xml"
    code, _o = _adb("push", local, tmp, serial=serial, timeout=60)
    if code != 0:
        raise RuntimeError("adb push failed")
    # One device-side command, so the launcher's ~1 s restart of the app can't slip in between.
    _adb("shell", "am force-stop %s; cat %s > %s; rm %s" % (WAHOO_PACKAGE, tmp, PREFS_FILE, tmp),
         serial=serial)
    time.sleep(4)                                  # app restarts and loads the new file


# ---- BLE (bleak) ----------------------------------------------------------------------------

async def _ble_session(action, blob=None):
    from bleak import BleakClient, BleakScanner
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: bool(d.name) and d.name.upper().startswith("ELEMNT"), timeout=20)
    if dev is None:
        raise RuntimeError("no ELEMNT advertising over Bluetooth (on? not connected to a phone?)")
    state = {"req": None, "chunks": {}, "acks": []}
    done = asyncio.Event()

    def on_cfg(_s, data):
        b = bytes(data)
        if len(b) >= 3 and b[0] in (3, 4) and b[1] == state["req"]:
            state["chunks"][b[2]] = b[3:]
            if b[0] == 4:
                done.set()
        elif len(b) >= 4 and b[0] == 5 and b[1] == state["req"]:
            state["acks"].append(b)
            if b[3] == 0:
                done.set()

    async def get(c, req):
        state.update(req=req, chunks={})
        done.clear()
        await c.write_gatt_char(BLE_CFG, bytes([6, req]), response=False)
        await asyncio.wait_for(done.wait(), 15)
        last = max(state["chunks"])
        await c.write_gatt_char(BLE_CFG, bytes([5, req, last & 255, 0]), response=False)
        return b"".join(state["chunks"][k] for k in sorted(state["chunks"]))

    async with BleakClient(dev, timeout=20) as c:
        await c.start_notify(BLE_CFG, on_cfg)
        await c.write_gatt_char(BLE_KEEPALIVE, b"\x00", response=False)
        await asyncio.sleep(0.5)
        if action == "get":
            return await get(c, 1)
        state.update(req=2, acks=[])
        done.clear()
        pay = 17                                    # 20-byte packets, 3-byte header
        for n, i in enumerate(range(0, len(blob), pay)):
            op = 4 if i + pay >= len(blob) else 3
            await c.write_gatt_char(BLE_CFG, bytes([op, 2, n & 255]) + blob[i:i + pay],
                                    response=False)
            await asyncio.sleep(0.03)
        await asyncio.wait_for(done.wait(), 15)
        return await get(c, 3)


def ble_read():
    return asyncio.run(_ble_session("get"))


def ble_write(blob):
    return asyncio.run(_ble_session("set", blob))


# ---- front door -------------------------------------------------------------------------------

def _pick(via, serial):
    if via in ("auto", "usb"):
        s = find_serial(serial)
        if s:
            return "usb", s
        if via == "usb":
            raise RuntimeError("No Wahoo ELEMNT on adb - press power twice and re-plug the USB cable")
    return "ble", None


def read(via="auto", serial=None):
    how, s = _pick(via, serial)
    blob = usb_read(s) if how == "usb" else ble_read()
    return dict(decode_layout(blob), via=how)


def write(wanted, via="auto", serial=None):
    how, s = _pick(via, serial)
    current_blob = usb_read(s) if how == "usb" else ble_read()
    new = apply_edit(decode_layout(current_blob), wanted)
    blob = encode_layout(new)
    if blob == current_blob:
        return dict(decode_layout(blob), via=how, changed=False)
    if how == "usb":
        usb_write(s, blob)
        back = usb_read(s)
    else:
        back = ble_write(blob)
    if back != blob:
        raise RuntimeError("the ELEMNT's layout doesn't match what was written")
    return dict(decode_layout(back), via=how, changed=True)


def reset(via="auto", serial=None):
    """Put the ELEMNT's pages back to Wahoo's default layout (custom pages are dropped).
    USB: the device app's own adb broadcast BADisplayCfgManager.RESET; BLE: BoltBT's "07"."""
    how, s = _pick(via, serial)
    if how == "usb":
        _adb("shell", "am broadcast -a com.wahoofitness.bolt.service.displaycfg."
                      "BADisplayCfgManager.RESET", serial=s)
        time.sleep(3)
        return dict(decode_layout(usb_read(s)), via=how)
    asyncio.run(_ble_reset())
    return dict(decode_layout(ble_read()), via=how)


async def _ble_reset():
    from bleak import BleakClient, BleakScanner
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: bool(d.name) and d.name.upper().startswith("ELEMNT"), timeout=20)
    if dev is None:
        raise RuntimeError("no ELEMNT advertising over Bluetooth (on? not connected to a phone?)")
    got = asyncio.Event()
    reply = {}

    def on_cfg(_s, data):
        b = bytes(data)
        if b[:1] == b"\x07":
            reply["b"] = b
            got.set()

    async with BleakClient(dev, timeout=20) as c:
        await c.start_notify(BLE_CFG, on_cfg)
        await c.write_gatt_char(BLE_KEEPALIVE, b"\x00", response=False)
        await c.write_gatt_char(BLE_CFG, b"\x07", response=False)
        await asyncio.wait_for(got.wait(), 10)
    if len(reply["b"]) > 1 and reply["b"][1] != 0:
        raise RuntimeError("the ELEMNT refused the reset (%s)" % reply["b"].hex())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["list", "fields", "set", "reset"])
    ap.add_argument("layout", nargs="?", help="set: JSON {\"pages\": [...]} (else stdin)")
    ap.add_argument("--via", choices=["auto", "usb", "ble"], default="auto")
    ap.add_argument("--serial")
    args = ap.parse_intermixed_args()
    try:
        if args.command == "fields":
            out = {"ok": True, "maxFields": MAX_FIELDS,
                   "groups": [{"group": g, "fields": [{"id": i, "name": n} for i, n in f]}
                              for g, f in FIELDS]}
        elif args.command == "list":
            out = dict(read(args.via, args.serial), ok=True)
        elif args.command == "reset":
            out = dict(reset(args.via, args.serial), ok=True)
        else:
            spec = json.loads(args.layout if args.layout is not None else sys.stdin.read())
            out = dict(write(spec["pages"], args.via, args.serial), ok=True)
    except Exception as e:                     # noqa: BLE001 - one JSON error line for the UI
        out = {"ok": False, "error": str(e)}
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
