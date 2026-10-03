#!/usr/bin/env python3
"""Wahoo ELEMNT device settings - backlight, LEDs, sounds, auto pause / lap, rotate maps, include
zeros, planned-workout options - read and written without the Wahoo companion app (André,
2026-10-03: "follow your order. be sure to have them usb and bluetooth").

    ./tools/wahoo_settings.py get [--via auto|ble|usb]          # JSON {settings: {key: value}}
    ./tools/wahoo_settings.py set [--via ...] '{"ledMode": 1}'  # only the given keys; read back

Every setting is one entry of the ELEMNT app's config ("BoltCfg"), identified by a one-byte code -
the same number as the "type" in its settings file and in the cfg backup TLV (bolt-65535.cfg).
  * Bluetooth (characteristic a026e019): get = "01 <code>", the ELEMNT answers "01 <code> <value>";
    set = "01 <code> <value>" (companion app connector/packets/bolt/cfg/a.java BBoltCfgPacket), the
    ELEMNT applies it to the right scope (global or ride profile) and confirms with a long-form
    change notice. Proven on André's ELEMNT 2026-10-03 (LED mode Off -> Speed -> Off, read back).
  * USB (adb root shell on the ELEMNT): 16 settings are global entries {"type":<code>,"boltId":""}
    of shared_prefs/BCfgManager-Bolt.xml; the 7 ride-profile ones (auto pause / lap, include
    zeros, auto lap on interval) are plain keys of shared_prefs/StdCfgManager.xml (own numbering,
    plus "<key>-updateTime"). Both learned 2026-10-03 by changing each setting over Bluetooth and
    diffing the files (every value restored). Written like the page layout: both files swapped in
    with one device-side command (force-stop; cat over) so the launcher's restart loads them.
Values are little-endian; meanings from the companion app's menus and BoltBT (whose "include
zeros" labels are inverted - André's ELEMNT shows cadence 0 = off, power 1 = on).
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
import wahoo_pages as P        # noqa: E402 - shared adb helpers (find_serial, root shell, restart)

BLE_SUFFIX = "-0a7d-4ab3-97fa-f1500f9feb8b"
BLE_CFG = "a026e019" + BLE_SUFFIX
BLE_KEEPALIVE = "a026e01c" + BLE_SUFFIX

# key: (code, kind, choices). kind: u8 | bool | u16 | float. choices: [(value, label)] for menus.
SETTINGS = {
    # Display
    "backlight":         (0x00, "u8", [(0, "On"), (1, "Timed"), (2, "Off")]),
    "backlightSeconds":  (0x1b, "u8", None),
    "autoShutdownMin":   (0x11, "u8", [(0, "Off"), (15, "15 min"), (30, "30 min"), (60, "1 h"), (120, "2 h")]),
    # LEDs
    "ledMode":           (0x04, "u8", [(0, "Off"), (1, "Speed"), (5, "Power"), (6, "Heart rate")]),
    "ledWorkout":        (0x05, "bool", None),
    "ledNotification":   (0x06, "bool", None),
    "ledNavigation":     (0x07, "bool", None),
    "ledSegments":       (0x24, "bool", None),
    "ledPlans":          (0x2e, "bool", None),
    # Sounds
    "soundWorkout":      (0x08, "bool", None),
    "soundNotification": (0x09, "bool", None),
    "soundNavigation":   (0x0a, "bool", None),
    "soundPlans":        (0x2d, "bool", None),
    # Ride
    "autoPauseSpeed":    (0x0c, "float", None),     # m/s; 0 = auto pause off
    "autoLapMode":       (0x18, "u8", [(0, "Off"), (1, "Distance"), (2, "Time")]),
    "autoLapMeters":     (0x19, "u16", None),
    "autoLapSeconds":    (0x1a, "u16", None),
    "rotateMaps":        (0x20, "bool", None),
    "zerosInCadence":    (0x2b, "bool", None),
    "zerosInPower":      (0x31, "bool", None),
    # Planned workouts
    "plansNotifyOtherPages": (0x2c, "bool", None),
    "plansAutoLapInterval":  (0x30, "bool", None),
    "plansSegments":         (0x2f, "bool", None),
}
# The 7 ride-profile settings: key in StdCfgManager.xml (its own numbering).
STD_KEYS = {"autoPauseSpeed": "4", "autoLapMode": "0", "autoLapMeters": "1", "autoLapSeconds": "2",
            "zerosInCadence": "49", "zerosInPower": "60", "plansAutoLapInterval": "58"}
BOLT_PREFS = "/data/data/com.wahoofitness.bolt/shared_prefs/BCfgManager-Bolt.xml"
STD_PREFS = "/data/data/com.wahoofitness.bolt/shared_prefs/StdCfgManager.xml"
AUTO_PAUSE_ON = 0.4470               # m/s - the companion app's "on" value (1 mph)
BY_CODE = {code: key for key, (code, _k, _c) in SETTINGS.items()}


def encode(key, value):
    code, kind, choices = SETTINGS[key]
    if kind == "bool":
        return bytes([1 if value else 0])
    if kind == "u8":
        v = int(value)
        if not 0 <= v <= 255 or (choices and v not in [c[0] for c in choices]):
            raise ValueError("%s: invalid value %r" % (key, value))
        return bytes([v])
    if kind == "u16":
        v = int(value)
        if not 0 <= v <= 65535:
            raise ValueError("%s: invalid value %r" % (key, value))
        return struct.pack("<H", v)
    if kind == "float":
        v = float(value)
        if not 0 <= v < 20:
            raise ValueError("%s: invalid value %r" % (key, value))
        return struct.pack("<f", v)
    raise ValueError(kind)


def decode(key, raw):
    _code, kind, _c = SETTINGS[key]
    if kind == "bool":
        return bool(raw[0]) if raw else None
    if kind == "u8":
        return raw[0] if raw else None
    if kind == "u16":
        return struct.unpack("<H", raw[:2])[0] if len(raw) >= 2 else None
    if kind == "float":
        # Full float32 precision: rounding here and writing the value back changed the ELEMNT's
        # 0.8333333 (3 km/h) auto-pause speed to 0.8333 once (2026-10-03).
        return struct.unpack("<f", raw[:4])[0] if len(raw) >= 4 else None
    return None


async def _ble(writes):
    """Read every setting; with `writes` ({key: value}), write those first and read back."""
    from bleak import BleakClient, BleakScanner
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: bool(d.name) and d.name.upper().startswith("ELEMNT"), timeout=20)
    if dev is None:
        raise RuntimeError("no ELEMNT advertising over Bluetooth (on? not connected to a phone?)")
    got = {}
    event = asyncio.Event()

    def on_cfg(_s, data):
        b = bytes(data)
        if len(b) >= 2 and b[0] == 1 and b[1] in BY_CODE:
            got[b[1]] = b[2:]
            event.set()

    async def ask(c, code):
        got.pop(code, None)
        for _ in range(3):
            event.clear()
            await c.write_gatt_char(BLE_CFG, bytes([1, code]), response=False)
            try:
                await asyncio.wait_for(event.wait(), 1.5)
            except asyncio.TimeoutError:
                pass
            if code in got:
                return

    async with BleakClient(dev, timeout=20) as c:
        await c.start_notify(BLE_CFG, on_cfg)
        await c.write_gatt_char(BLE_KEEPALIVE, b"\x00", response=False)
        await asyncio.sleep(0.5)
        for key, value in (writes or {}).items():
            code = SETTINGS[key][0]
            await c.write_gatt_char(BLE_CFG, bytes([1, code]) + encode(key, value), response=False)
            await asyncio.sleep(0.4)
        for key, (code, _k, _c) in SETTINGS.items():
            await ask(c, code)
    return {key: decode(key, got[code]) for key, (code, _k, _c) in SETTINGS.items() if code in got}


# ---- USB --------------------------------------------------------------------------------------

def _xml_entry(xml, name):
    """(start, end, tag, value) of the <tag name="name" value="..."/> entry, or None."""
    m = re.search(r'<(\w+) name="%s" value="([^"]*)" />' % re.escape(html.escape(name, quote=True)), xml)
    return (m.start(), m.end(), m.group(1), m.group(2)) if m else None


def _bolt_name(key):
    return '{"type":%d,"boltId":""}' % SETTINGS[key][0]


def _from_xml(key, tag, value):
    kind = SETTINGS[key][1]
    if kind == "bool":
        return value == "true"
    if kind == "float":
        return struct.unpack("<f", struct.pack("<f", float(value)))[0]
    return int(value)


def _to_xml(key, value):
    kind = SETTINGS[key][1]
    if kind == "bool":
        return "true" if value else "false"
    if kind == "float":
        return repr(struct.unpack("<f", struct.pack("<f", float(value)))[0])
    return str(int(value))


def _usb_files(serial):
    out = {}
    for path in (BOLT_PREFS, STD_PREFS):
        code, xml = P._adb("exec-out", "cat " + path, serial=serial)
        if code != 0 or "<map>" not in xml:
            raise RuntimeError("couldn't read the ELEMNT's settings over USB (is adb root?)")
        out[path] = xml
    return out


def _usb_read(serial):
    files = _usb_files(serial)
    settings = {}
    for key in SETTINGS:
        path, name = (STD_PREFS, STD_KEYS[key]) if key in STD_KEYS else (BOLT_PREFS, _bolt_name(key))
        e = _xml_entry(files[path], name)
        if e:
            settings[key] = _from_xml(key, e[2], e[3])
    return settings


def _usb_write(serial, changes):
    files = _usb_files(serial)
    now_ms = str(int(time.time() * 1000))
    for key, value in changes.items():
        path, name = (STD_PREFS, STD_KEYS[key]) if key in STD_KEYS else (BOLT_PREFS, _bolt_name(key))
        xml = files[path]
        e = _xml_entry(xml, name)
        if not e:
            raise RuntimeError("%s isn't in the ELEMNT's settings file - change it over Bluetooth" % key)
        new = '<%s name="%s" value="%s" />' % (e[2], html.escape(name, quote=True), _to_xml(key, value))
        xml = xml[:e[0]] + new + xml[e[1]:]
        if key in STD_KEYS:                       # keep the app's change bookkeeping consistent
            t = _xml_entry(xml, name + "-updateTime")
            if t:
                xml = xml[:t[0]] + '<long name="%s-updateTime" value="%s" />' % (name, now_ms) + xml[t[1]:]
        files[path] = xml
    tmp = {}
    for path, xml in files.items():
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as fh:
            fh.write(xml)
        remote = "/data/local/tmp/sommet_" + path.rsplit("/", 1)[1]
        if P._adb("push", fh.name, remote, serial=serial, timeout=60)[0] != 0:
            raise RuntimeError("adb push failed")
        tmp[path] = remote
    # One device-side command, so the launcher's ~1 s restart of the app can't slip in between.
    cmd = "am force-stop %s; " % P.WAHOO_PACKAGE + "; ".join(
        "cat %s > %s; rm %s" % (r, path, r) for path, r in tmp.items())
    P._adb("shell", cmd, serial=serial)
    time.sleep(4)


def _pick(via):
    if via in ("auto", "usb"):
        serial = P.find_serial()
        if serial:
            return "usb", serial
        if via == "usb":
            raise RuntimeError("No Wahoo ELEMNT on adb - press power twice and re-plug the USB cable")
    return "ble", None


def read(via="auto"):
    how, serial = _pick(via)
    return (_usb_read(serial) if how == "usb" else asyncio.run(_ble(None))), how


def write(changes, via="auto"):
    for key in changes:
        if key not in SETTINGS:
            raise ValueError("unknown setting %r" % key)
        encode(key, changes[key])                  # validate before connecting
    how, serial = _pick(via)
    if how == "usb":
        _usb_write(serial, changes)
        after = _usb_read(serial)
    else:
        after = asyncio.run(_ble(changes))
    # Compare what goes on the wire, so a float that round-trips through float32 still matches.
    wrong = [k for k, v in changes.items()
             if after.get(k) is None or encode(k, v) != encode(k, after[k])]
    if wrong:
        raise RuntimeError("the ELEMNT didn't take: %s" % ", ".join(wrong))
    return after, how


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["get", "set", "schema"])
    ap.add_argument("changes", nargs="?", help="set: JSON {key: value} (else stdin)")
    ap.add_argument("--via", choices=["auto", "usb", "ble"], default="auto")
    args = ap.parse_intermixed_args()
    try:
        if args.command == "schema":
            out = {"ok": True, "settings": {k: {"code": c, "kind": kd,
                                                "choices": [{"v": v, "t": t} for v, t in (ch or [])]}
                                            for k, (c, kd, ch) in SETTINGS.items()},
                   "autoPauseOn": AUTO_PAUSE_ON}
        elif args.command == "get":
            settings, how = read(args.via)
            out = {"ok": True, "settings": settings, "via": how}
        else:
            changes = json.loads(args.changes if args.changes is not None else sys.stdin.read())
            settings, how = write(changes, args.via)
            out = {"ok": True, "settings": settings, "via": how}
    except Exception as e:                     # noqa: BLE001 - one JSON error line for the UI
        out = {"ok": False, "error": str(e)}
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
