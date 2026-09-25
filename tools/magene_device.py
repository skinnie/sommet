#!/usr/bin/env python3
"""Magene C406 (Pro) device control over BLE - everything the OneLap app does with the head unit
except ride download (that's magene_import.py) and firmware upgrade. Decoded from the OneLap APK's
native BLE library `com.onelap.lib_ble` (see assets/onelap-re/NOTES.md), NOT from packet captures.

    ./tools/magene_device.py battery        --address <addr>
    ./tools/magene_device.py info           --address <addr>
    ./tools/magene_device.py set-time       --address <addr>
    ./tools/magene_device.py set-timezone   --address <addr> [--offset-seconds N]
    ./tools/magene_device.py altitude       --address <addr>            # trigger baro re-calibrate
    ./tools/magene_device.py read-profile   --address <addr>
    ./tools/magene_device.py status         --address <addr> [--sync-clock]   # battery+info(+clock)
    ./tools/magene_device.py set-profile    --address <addr> [--ftp 250] [--weight 72] ...
        (only the fields given change; the rest are read back from the device and kept)
    ./tools/magene_device.py read-pages     --address <addr>            # data screens
    ./tools/magene_device.py write-pages    --address <addr> --pages '[[16,177,113,48],[64,65]]'
        (field codes per page - tools/magene_pages.py has the catalogue and the format)
    ./tools/magene_device.py read-settings  --address <addr>
    ./tools/magene_device.py set-settings   --address <addr> --set autoPause=5 --set keyTone=0 ...
        (device "function settings": read-modify-write of the whole block, like the app)

Transport is the same as magene_import.py: the C406 command channel CC02 (write a frame, read the
ack/data as a notification). Every command is a bare frame `40 <cmd> [payload…]` - no length or
checksum wrapper (confirmed from the decompiled *Command builders). Bonding is required, so this
reuses magene_import._connect (which pairs on connect).

Command bytes (prefix BASIC_MCCR = 0x40):
  READ_USER_INFO 0x40  WRITE_USER_INFO 0x41  READ_FUNC 0x4c  WRITE_FUNC 0x4d
  WRITE_TIME_STAMP 0x4e  WRITE_TIME_ZONE 0x4f  WRITE_TIME_ZONE_OFFSET 0x57  ALTITUDE_CORRECT 0x55
Standard GATT (no Magene command): Battery 0x2A19, Device Information service 0x180A.
"""

import argparse
import asyncio
import json
import struct
import sys
import time

from magene_import import _connect, CC02, _ride_list, _name_for
import magene_pages

BATTERY_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"
DEVINFO = {
    "00002a29-0000-1000-8000-00805f9b34fb": "manufacturer",
    "00002a24-0000-1000-8000-00805f9b34fb": "model",
    "00002a25-0000-1000-8000-00805f9b34fb": "serial",
    "00002a26-0000-1000-8000-00805f9b34fb": "firmware",
    "00002a27-0000-1000-8000-00805f9b34fb": "software",
    "00002a28-0000-1000-8000-00805f9b34fb": "hardware",
}


async def _cmd_once(client, frame, expect_prefix=None, timeout=5.0):
    """Write a CC02 frame and return the first matching notification (or None)."""
    reply = asyncio.get_event_loop().create_future()

    def _cb(_, data):
        b = bytes(data)
        if reply.done():
            return
        if expect_prefix is None or b[:len(expect_prefix)] == expect_prefix:
            reply.set_result(b)

    await client.start_notify(CC02, _cb)
    try:
        await client.write_gatt_char(CC02, frame, response=True)
        try:
            return await asyncio.wait_for(reply, timeout=timeout)
        except asyncio.TimeoutError:
            return None
    finally:
        await client.stop_notify(CC02)


async def read_battery(client):
    try:
        v = await client.read_gatt_char(BATTERY_CHAR)
        return v[0]
    except Exception:
        return None


async def read_info(client):
    out = {}
    for uuid, name in DEVINFO.items():
        try:
            v = await client.read_gatt_char(uuid)
            out[name] = v.decode("utf-8", "replace").strip("\x00")
        except Exception:
            out[name] = None
    return out


async def set_time(client):
    # WriteTimeStampCommand: 40 4e + unix seconds as uint32 LE.
    frame = b"\x40\x4e" + struct.pack("<I", int(time.time()))
    ack = await _cmd_once(client, frame, expect_prefix=b"\x40\x4e")
    return ack is not None


async def set_timezone(client, offset_seconds):
    # WriteTimeZoneCommand: 40 4f + tz(uint8 = whole-hour offset). WriteTimeZoneOffsetCommand:
    # 40 57 + offset seconds (uint32 LE). Send both, like the app.
    tz_hours = int(round(offset_seconds / 3600.0)) & 0xFF
    ok = await _cmd_once(client, b"\x40\x4f" + bytes([tz_hours]), expect_prefix=b"\x40\x4f")
    off = await _cmd_once(client, b"\x40\x57" + struct.pack("<I", offset_seconds & 0xFFFFFFFF),
                          expect_prefix=b"\x40\x57")
    return (ok is not None) or (off is not None)


async def altitude_correct(client):
    # AltitudeCorrectCommand: bare 40 55 - tells the device to re-baseline its barometric
    # altitude to its current GPS fix. (The OneLap flow waits for a GPS fix first; here the
    # device uses its own GNSS. A location-seeded variant can pass a payload later if needed.)
    ack = await _cmd_once(client, b"\x40\x55", expect_prefix=b"\x40\x55", timeout=8.0)
    return ack is not None


async def read_profile(client):
    # Reply: 40 40 <status> <sex u8><age u8><height u8><maxHR u8><LTHR u8><FTP u16LE>
    #        <bikeWeight*100 u16LE><weight*100 u16LE> - same struct as the write, per byte.
    ack = await _cmd_once(client, b"\x40\x40", expect_prefix=b"\x40\x40")
    if not ack or len(ack) < 14:
        return None
    b = ack[3:]
    return {
        "sex": b[0], "age": b[1], "height": b[2], "maxHr": b[3], "lthr": b[4],
        "ftp": struct.unpack("<H", b[5:7])[0],
        "bikeWeight": struct.unpack("<H", b[7:9])[0] / 100.0,
        "weight": struct.unpack("<H", b[9:11])[0] / 100.0,
    }


async def set_profile(client, sex, age, height, max_hr, lthr, ftp, weight, bike_weight):
    # WriteUserInfoProduct: 13-byte frame
    #   40 41 <sex u8> <age u8> <height u8> <maxHR u8> <LTHR u8> <FTP u16LE>
    #        <bikeWeight*100 u16LE> <weight*100 u16LE>
    bw = int(round((bike_weight if bike_weight and bike_weight > 0 else 10.0) * 100))
    frame = (b"\x40\x41"
             + bytes([sex & 0xFF, age & 0xFF, height & 0xFF, max_hr & 0xFF, lthr & 0xFF])
             + struct.pack("<H", ftp & 0xFFFF)
             + struct.pack("<H", bw & 0xFFFF)
             + struct.pack("<H", int(round(weight * 100)) & 0xFFFF))
    ack = await _cmd_once(client, frame, expect_prefix=b"\x40\x41")
    return ack is not None


PROFILE_FIELDS = ("sex", "age", "height", "maxHr", "lthr", "ftp", "weight", "bikeWeight")


# ---- function settings (READ_FUNC 0x4c / WRITE_FUNC 0x4d) ---------------------------------------
# Decoded from OneLap 1.9.3: DecodeProFuncProduct (layout), BikeComputerFuncViewModel (writes),
# BikeComputerFuncActivity / BackLightActivity / AutoCircleActivity / LocalBikeComputerFuncDataSource
# (allowed values). Read reply = `40 4c <status> <block…>`; the app keeps block = reply[3:], patches
# single fields in place and writes back `40 4d <block>` (same length); ack `40 4d <status>`, 0 = ok.
#
# The block layout depends on the reply length (the app branches on it the same way):
#   len > 19  -> tz is a u32 of seconds (read-only here: the time-zone commands 4f/57 set it),
#               and the auto-backlight + auto-lap groups exist. The C406 Pro replies 24 bytes.
#   len == 15 -> an older unit with a key-function byte; not supported for writing here.
# Offsets below are into the FULL reply (the app's getIntValue offsets); block offset = off - 3.
# Value formats as the app reads them: "B" u8, "H" u16 LE, "I" u32 LE.

def _settings_layout(n):
    """[(name, reply_offset, fmt)] for a reply of n bytes, or None if unsupported."""
    if n <= 19:
        return None
    lay = [("timezoneOffset", 3, "I"),
           ("autoBacklight", 7, "B"), ("backlightDuration", 8, "H"), ("backlightLevel", 10, "B"),
           ("autoOff", 11, "B"), ("autoPause", 12, "B"), ("promptTone", 13, "B"),
           ("keyTone", 14, "B"), ("startReminder", 15, "B"), ("estimatedPower", 16, "B"),
           ("hrAlert", 17, "B"), ("powerAlert", 18, "H")]
    if n >= 24:
        lay += [("autoLap", 20, "B"), ("autoLapType", 21, "B"), ("autoLapValue", 22, "H")]
    return lay


# Allowed values (what the OneLap UI offers). 0 = off for the alert/auto fields.
_ONOFF = {0, 1}
SETTINGS_ALLOWED = {
    "autoBacklight": _ONOFF,
    "backlightDuration": {0, 5, 10, 15, 30, 60},        # seconds; 0 = always on
    "backlightLevel": {0, 1, 2},                        # low / medium / high
    "autoOff": {0, 5, 10, 15, 20, 30, 40, 60},          # minutes; 0 = off
    "autoPause": set(range(0, 11)),                     # km/h threshold; 0 = off
    "promptTone": _ONOFF, "keyTone": _ONOFF, "startReminder": _ONOFF, "estimatedPower": _ONOFF,
    "hrAlert": {0} | set(range(100, 241)),              # bpm; 0 = off
    "powerAlert": {0} | set(range(100, 2501)),          # W; 0 = off
    "autoLap": _ONOFF,
    "autoLapType": _ONOFF,                              # 0 = distance, 1 = time
    "autoLapValue": set(range(1, 1001)),                # distance: km x 10; time: minutes
}
_FMT_SIZE = {"B": 1, "H": 2, "I": 4}


def decode_settings(reply):
    lay = _settings_layout(len(reply))
    if lay is None:
        return None
    out = {}
    for name, off, fmt in lay:
        out[name] = struct.unpack_from("<" + fmt, reply, off)[0]
    return out


async def read_settings_raw(client):
    ack = await _cmd_once(client, b"\x40\x4c", expect_prefix=b"\x40\x4c")
    if not ack or len(ack) < 4 or ack[2] != 0:
        return None
    return ack


def patch_settings(reply, changes):
    """New write block (reply[3:] with `changes` patched in). Every name, value and offset is
    checked before a byte is touched; raises ValueError on anything unknown or out of range."""
    lay = _settings_layout(len(reply))
    if lay is None:
        raise ValueError(f"unsupported settings block ({len(reply)}-byte reply)")
    where = {name: (off, fmt) for name, off, fmt in lay}
    block = bytearray(reply[3:])
    for name, value in changes.items():
        if name not in SETTINGS_ALLOWED or name not in where:
            raise ValueError(f"{name} is not a writable setting on this device")
        value = int(value)
        if value not in SETTINGS_ALLOWED[name]:
            raise ValueError(f"{name}={value} is outside what the device accepts")
        off, fmt = where[name]
        boff = off - 3
        if boff < 0 or boff + _FMT_SIZE[fmt] > len(block):
            raise ValueError(f"{name} offset {boff} outside the {len(block)}-byte block")
        struct.pack_into("<" + fmt, block, boff, value)
    return bytes(block)


def _local_offset_seconds():
    # tm_gmtoff reflects DST actually in effect now (time.daylight only says the zone HAS DST).
    return int(time.localtime().tm_gmtoff)


async def run(args):
    client = await _connect(args.address)
    try:
        if args.action == "battery":
            pct = await read_battery(client)
            return {"ok": pct is not None, "batteryPercent": pct}
        if args.action == "info":
            return {"ok": True, "info": await read_info(client)}
        if args.action == "status":
            # One connection for the device card: battery + identity, and (on connect) the clock.
            out = {"ok": True, "batteryPercent": await read_battery(client),
                   "info": await read_info(client)}
            if args.sync_clock:
                out["clockSet"] = await set_time(client)
                out["timezoneSet"] = await set_timezone(client, _local_offset_seconds())
            return out
        if args.action == "set-time":
            return {"ok": await set_time(client)}
        if args.action == "set-timezone":
            off = args.offset_seconds
            if off is None:
                off = _local_offset_seconds()
            return {"ok": await set_timezone(client, int(off))}
        if args.action == "altitude":
            return {"ok": await altitude_correct(client)}
        if args.action == "read-profile":
            prof = await read_profile(client)
            return {"ok": prof is not None, "profile": prof}
        if args.action == "set-profile":
            # The device stores the whole profile as one struct, so read it and change only the
            # fields given - never fill the rest with defaults.
            current = await read_profile(client)
            if current is None:
                return {"ok": False, "error": "could not read the current profile"}
            given = {"sex": args.sex, "age": args.age, "height": args.height,
                     "maxHr": args.max_hr, "lthr": args.lthr, "ftp": args.ftp,
                     "weight": args.weight, "bikeWeight": args.bike_weight}
            merged = {k: (given[k] if given[k] is not None else current[k]) for k in PROFILE_FIELDS}
            ok = await set_profile(client, merged["sex"], merged["age"], merged["height"],
                                   merged["maxHr"], merged["lthr"], merged["ftp"],
                                   merged["weight"], merged["bikeWeight"])
            return {"ok": ok, "profile": merged}
        if args.action == "hello":
            # Everything the app needs when the C406 is found / selected, in ONE connection (each
            # connect shows on the device as a drop + reconnect - André, 2026-09-25): identity,
            # battery, clock + time zone, profile and the ride list.
            out = {"ok": True, "batteryPercent": await read_battery(client),
                   "info": await read_info(client)}
            out["clockSet"] = await set_time(client)
            out["timezoneSet"] = await set_timezone(client, _local_offset_seconds())
            out["profile"] = await read_profile(client)
            out["rides"] = [_name_for(r) for r in await _ride_list(client)]
            return out
        if args.action == "read-config":
            # GPS settings page: device settings + data screens in one connection.
            out = {"ok": True, "settings": None, "pages": None}
            raw = await read_settings_raw(client)
            if raw is not None:
                out["settings"] = decode_settings(raw)
            pr = await _cmd_once(client, b"\x40\x42", expect_prefix=b"\x40\x42")
            if pr and len(pr) >= 3 and pr[2] == 0:
                try:
                    out["pages"] = magene_pages.describe(magene_pages.decode_pages(pr[3:]))
                except ValueError as exc:
                    out["pagesError"] = f"unrecognised pages layout ({exc})"
            return out
        if args.action == "read-pages":
            raw = await _cmd_once(client, b"\x40\x42", expect_prefix=b"\x40\x42")
            if not raw or len(raw) < 3 or raw[2] != 0:
                return {"ok": False, "error": "no pages reply"}
            try:
                pages = magene_pages.decode_pages(raw[3:])
            except ValueError as exc:
                return {"ok": False, "error": f"unrecognised pages layout ({exc})", "raw": raw.hex()}
            return {"ok": True, "pages": magene_pages.describe(pages), "raw": raw.hex()}
        if args.action == "write-pages":
            try:
                block = magene_pages.encode_pages(json.loads(args.pages or "[]"))
            except (ValueError, json.JSONDecodeError) as exc:
                return {"ok": False, "error": str(exc)}
            # Only write a format we can read back: the current layout must decode first.
            raw = await _cmd_once(client, b"\x40\x42", expect_prefix=b"\x40\x42")
            try:
                magene_pages.decode_pages(raw[3:] if raw and len(raw) > 3 else b"")
            except ValueError as exc:
                return {"ok": False, "error": f"device layout not recognised, not writing ({exc})"}
            ack = await _cmd_once(client, b"\x40\x43" + block, expect_prefix=b"\x40\x43", timeout=8.0)
            if ack is not None and len(ack) >= 3 and ack[2] == 2:     # 2 = still applying
                await asyncio.sleep(1.5)
            # Success = the device now reads back exactly what was written.
            after = await _cmd_once(client, b"\x40\x42", expect_prefix=b"\x40\x42")
            ok = after is not None and after[3:] == block
            pages = None
            try:
                pages = magene_pages.describe(magene_pages.decode_pages(after[3:]))
            except (ValueError, TypeError):
                pass
            return {"ok": ok, "pages": pages}
        if args.action == "read-settings":
            raw = await read_settings_raw(client)
            if raw is None:
                return {"ok": False, "error": "no settings reply"}
            dec = decode_settings(raw)
            return {"ok": dec is not None, "settings": dec, "raw": raw.hex(),
                    **({} if dec else {"error": f"unsupported {len(raw)}-byte settings block"})}
        if args.action == "set-settings":
            changes = {}
            for kv in args.set or []:
                k, _, v = kv.partition("=")
                changes[k.strip()] = int(v)
            if not changes:
                return {"ok": False, "error": "nothing to set (use --set name=value)"}
            raw = await read_settings_raw(client)
            if raw is None:
                return {"ok": False, "error": "could not read the current settings"}
            try:
                block = patch_settings(raw, changes)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            ack = await _cmd_once(client, b"\x40\x4d" + block, expect_prefix=b"\x40\x4d")
            ok = ack is not None and len(ack) >= 3 and ack[2] == 0
            after = await read_settings_raw(client)
            return {"ok": ok, "before": decode_settings(raw),
                    "settings": decode_settings(after) if after else None,
                    "raw": after.hex() if after else None}
        return {"ok": False, "error": f"unknown action {args.action}"}
    finally:
        await client.disconnect()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["battery", "info", "status", "set-time", "set-timezone",
                                        "altitude", "read-profile", "set-profile",
                                        "read-settings", "set-settings",
                                        "read-pages", "write-pages", "hello", "read-config"])
    ap.add_argument("--address", required=True)
    ap.add_argument("--sync-clock", action="store_true", help="with status: also set time + tz")
    ap.add_argument("--offset-seconds", type=int, default=None)
    # set-profile: only the fields given are changed; the rest are kept from the device.
    ap.add_argument("--sex", type=int, default=None)
    ap.add_argument("--age", type=int, default=None)
    ap.add_argument("--height", type=int, default=None)
    ap.add_argument("--max-hr", type=int, default=None)
    ap.add_argument("--lthr", type=int, default=None)
    ap.add_argument("--ftp", type=int, default=None)
    ap.add_argument("--weight", type=float, default=None)
    ap.add_argument("--bike-weight", type=float, default=None)
    ap.add_argument("--pages", help="write-pages: JSON list of pages, each a list of field codes")
    ap.add_argument("--set", action="append", metavar="NAME=VALUE",
                    help="set-settings: one setting to change (repeatable)")
    args = ap.parse_args()
    print(json.dumps(asyncio.run(run(args))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
