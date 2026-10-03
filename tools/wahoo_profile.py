#!/usr/bin/env python3
"""Wahoo ELEMNT rider profile - FTP, max / resting HR, weight, height (+ the HR and power zone
ceilings the ELEMNT derives from them) - over the USB cable or Bluetooth, for Sommet's "Sync
profile" with intervals.icu like the Bryton / Magene (André, 2026-10-03: item 2 of the Wahoo plan,
"be sure to have them usb and bluetooth").

    ./tools/wahoo_profile.py read  [--via auto|usb|ble]
    ./tools/wahoo_profile.py write [--via ...] '{"ftp": 230, "max_hr": 195, "weight": 91.5}'

The profile is the ELEMNT app's "comp" config (the companion app's BCompCfgPacket):
  * Bluetooth (a026e019): get "00 <code>", set "00 <code> <value>". FTP must carry the time it was
    set ("00 07 <ftp u16> <unix seconds u32>") - without it the ELEMNT silently ignores the write.
  * USB (adb root): plain keys of shared_prefs/StdCfgManager.xml (+ "<key>-updateTime" in ms; FTP
    also bumps BCompCfg-7-updateTime in BCfgManager-Comp.xml), swapped in like the settings files.
Codes and keys were learned 2026-10-03 on André's ELEMNT by changing each value over Bluetooth
and diffing the files (every value restored). The ELEMNT stores ABSOLUTE zone ceilings (watts /
bpm), so when FTP or max HR changes the zone ceilings are scaled by the same ratio - otherwise its
zones would describe the old thresholds. Ceilings >= 2000 W are the open-ended top zones and stay.
"""

import argparse
import asyncio
import html
import json
import os
import re
import struct
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wahoo_pages as P        # noqa: E402 - shared adb helpers

BLE_CFG, BLE_KEEPALIVE = P.BLE_CFG, P.BLE_KEEPALIVE
STD_PREFS = "/data/data/com.wahoofitness.bolt/shared_prefs/StdCfgManager.xml"
COMP_PREFS = "/data/data/com.wahoofitness.bolt/shared_prefs/BCfgManager-Comp.xml"

# name: (ble code, width, StdCfgManager key)
FIELDS = {
    "height_cm": (1, 1, "7"),
    "weight_hg": (2, 2, "27"),
    "ftp":       (7, 2, "6"),
    "rest_hr":   (8, 1, "11"),
    "max_hr":    (13, 1, "10"),
}
HR_ZONES = [(9, 1, "12"), (10, 1, "13"), (11, 1, "14"), (12, 1, "15")]
PWR_ZONES = [(29, 2, "17"), (30, 2, "18"), (31, 2, "19"), (32, 2, "20"),
             (38, 2, "39"), (39, 2, "40"), (40, 2, "41")]
OPEN_ZONE = 2000                     # ceilings from here up are the unbounded top zones


def _public(raw):
    """Device record -> the dialog's field names (weight in kg, height in cm)."""
    out = {"ftp": raw.get("ftp"), "max_hr": raw.get("max_hr"), "rest_hr": raw.get("rest_hr"),
           "weight": round(raw["weight_hg"] / 10.0, 1) if raw.get("weight_hg") is not None else None,
           "height": raw.get("height_cm")}
    out["hr_zones"] = raw.get("hr_zones")
    out["power_zones"] = raw.get("power_zones")
    return out


def _plan(raw, changes):
    """{(code, width, key): new int value} for the requested public changes, zones scaled."""
    out = {}
    if "ftp" in changes and changes["ftp"] is not None:
        new = int(round(float(changes["ftp"])))
        if not 50 <= new <= 700:
            raise ValueError("FTP %s W out of range" % changes["ftp"])
        old = raw["ftp"]
        out[(7, 2, "6")] = new
        if old and new != old:
            for (code, w, key), ceil in zip(PWR_ZONES, raw["power_zones"]):
                if ceil < OPEN_ZONE:
                    out[(code, w, key)] = int(round(ceil * new / old))
    if "max_hr" in changes and changes["max_hr"] is not None:
        new = int(changes["max_hr"])
        if not 120 <= new <= 230:
            raise ValueError("max HR %s out of range" % changes["max_hr"])
        old = raw["max_hr"]
        out[(13, 1, "10")] = new
        if old and new != old:
            for (code, w, key), ceil in zip(HR_ZONES, raw["hr_zones"]):
                out[(code, w, key)] = int(round(ceil * new / old))
    if "rest_hr" in changes and changes["rest_hr"] is not None:
        v = int(changes["rest_hr"])
        if not 30 <= v <= 100:
            raise ValueError("resting HR %s out of range" % v)
        out[(8, 1, "11")] = v
    if "weight" in changes and changes["weight"] is not None:
        kg = float(changes["weight"])
        if not 30 <= kg <= 200:
            raise ValueError("weight %s kg out of range" % kg)
        out[(2, 2, "27")] = int(round(kg * 10))
    if "height" in changes and changes["height"] is not None:
        cm = int(round(float(changes["height"])))
        if not 120 <= cm <= 230:
            raise ValueError("height %s cm out of range" % cm)
        out[(1, 1, "7")] = cm
    return out


# ---- Bluetooth --------------------------------------------------------------------------------

async def _ble(writes=None):
    from bleak import BleakClient, BleakScanner
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: bool(d.name) and d.name.upper().startswith("ELEMNT"), timeout=20)
    if dev is None:
        raise RuntimeError("no ELEMNT advertising over Bluetooth (on? not connected to a phone?)")
    got = {}
    event = asyncio.Event()

    def on_cfg(_s, data):
        b = bytes(data)
        if len(b) >= 3 and b[0] == 0:
            got[b[1]] = b[2:]
            event.set()

    async def ask(c, code):
        got.pop(code, None)
        for _ in range(3):
            event.clear()
            await c.write_gatt_char(BLE_CFG, bytes([0, code]), response=False)
            try:
                await asyncio.wait_for(event.wait(), 1.5)
            except asyncio.TimeoutError:
                pass
            if code in got:
                return got[code]
        return None

    async with BleakClient(dev, timeout=20) as c:
        await c.start_notify(BLE_CFG, on_cfg)
        await c.write_gatt_char(BLE_KEEPALIVE, b"\x00", response=False)
        await asyncio.sleep(0.5)
        for (code, w, _key), value in (writes or {}).items():
            msg = bytes([0, code]) + int(value).to_bytes(w, "little")
            if code == 7:                                   # FTP: + time it was set
                msg += struct.pack("<I", int(time.time()))
            await c.write_gatt_char(BLE_CFG, msg, response=False)
            await asyncio.sleep(0.5)
        raw = {}
        for name, (code, w, _k) in FIELDS.items():
            v = await ask(c, code)
            raw[name] = int.from_bytes(v[:w], "little") if v else None
        raw["hr_zones"] = [int.from_bytes((await ask(c, code))[:w], "little") for code, w, _k in HR_ZONES]
        raw["power_zones"] = [int.from_bytes((await ask(c, code))[:w], "little") for code, w, _k in PWR_ZONES]
    return raw


# ---- USB --------------------------------------------------------------------------------------

def _entry(xml, name):
    m = re.search(r'<(\w+) name="%s" value="([^"]*)" />' % re.escape(html.escape(name, quote=True)), xml)
    return (m.start(), m.end(), m.group(1), m.group(2)) if m else None


def _usb_read(serial):
    code, xml = P._adb("exec-out", "cat " + STD_PREFS, serial=serial)
    if code != 0 or "<map>" not in xml:
        raise RuntimeError("couldn't read the ELEMNT's settings over USB (is adb root?)")
    def val(key):
        e = _entry(xml, key)
        return int(e[3]) if e else None
    raw = {name: val(key) for name, (_c, _w, key) in FIELDS.items()}
    raw["hr_zones"] = [val(k) for _c, _w, k in HR_ZONES]
    raw["power_zones"] = [val(k) for _c, _w, k in PWR_ZONES]
    return raw


def _set(xml, name, value, tag="int"):
    e = _entry(xml, name)
    new = '<%s name="%s" value="%s" />' % (e[2] if e else tag, html.escape(name, quote=True), value)
    if e:
        return xml[:e[0]] + new + xml[e[1]:]
    return xml.replace("</map>", "    %s\n</map>" % new)


def _usb_write(serial, writes):
    files = {}
    for path in (STD_PREFS, COMP_PREFS):
        code, xml = P._adb("exec-out", "cat " + path, serial=serial)
        if code != 0 or "<map>" not in xml:
            raise RuntimeError("couldn't read the ELEMNT's settings over USB (is adb root?)")
        files[path] = xml
    now_ms = int(time.time()) * 1000                # the app stores whole seconds here
    for (code, _w, key), value in writes.items():
        files[STD_PREFS] = _set(files[STD_PREFS], key, value)
        if _entry(files[STD_PREFS], key + "-updateTime"):
            files[STD_PREFS] = _set(files[STD_PREFS], key + "-updateTime", now_ms, "long")
        comp_key = "BCompCfg-%d-updateTime" % code
        if _entry(files[COMP_PREFS], comp_key):
            files[COMP_PREFS] = _set(files[COMP_PREFS], comp_key, now_ms, "long")
    remote = {}
    for path, xml in files.items():
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as fh:
            fh.write(xml)
        r = "/data/local/tmp/sommet_" + path.rsplit("/", 1)[1]
        if P._adb("push", fh.name, r, serial=serial, timeout=60)[0] != 0:
            raise RuntimeError("adb push failed")
        remote[path] = r
    P._adb("shell", "am force-stop %s; " % P.WAHOO_PACKAGE + "; ".join(
        "cat %s > %s; rm %s" % (r, path, r) for path, r in remote.items()), serial=serial)
    time.sleep(4)


# ---- front door -------------------------------------------------------------------------------

def _pick(via):
    if via in ("auto", "usb"):
        s = P.find_serial()
        if s:
            return "usb", s
        if via == "usb":
            raise RuntimeError("No Wahoo ELEMNT on adb - press power twice and re-plug the USB cable")
    return "ble", None


def read(via="auto"):
    how, s = _pick(via)
    raw = _usb_read(s) if how == "usb" else asyncio.run(_ble())
    return _public(raw), how


def write(changes, via="auto"):
    how, s = _pick(via)
    raw = _usb_read(s) if how == "usb" else asyncio.run(_ble())
    writes = _plan(raw, changes)
    if not writes:
        return _public(raw), how
    if how == "usb":
        _usb_write(s, writes)
        after = _usb_read(s)
    else:
        after = asyncio.run(_ble(writes))
    lookup = dict(FIELDS)
    for (code, w, key), value in writes.items():
        name = next((n for n, f in lookup.items() if f[0] == code), None)
        got = after.get(name) if name else None
        if name is None:
            zones = HR_ZONES if code in [c for c, _w, _k in HR_ZONES] else PWR_ZONES
            idx = [c for c, _w, _k in zones].index(code)
            got = after["hr_zones" if zones is HR_ZONES else "power_zones"][idx]
        if got != value:
            raise RuntimeError("the ELEMNT didn't take code %d (wanted %s, has %s)" % (code, value, got))
    return _public(after), how


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["read", "write"])
    ap.add_argument("changes", nargs="?", help="write: JSON {ftp, max_hr, rest_hr, weight, height}")
    ap.add_argument("--via", choices=["auto", "usb", "ble"], default="auto")
    args = ap.parse_intermixed_args()
    try:
        if args.command == "read":
            prof, how = read(args.via)
        else:
            changes = json.loads(args.changes if args.changes is not None else sys.stdin.read())
            prof, how = write(changes, args.via)
        out = dict(prof, ok=True, via=how)
    except Exception as e:                     # noqa: BLE001 - one JSON error line for the UI
        out = {"ok": False, "error": str(e)}
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
