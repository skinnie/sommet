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
    ./tools/magene_device.py set-profile    --address <addr> --sex 1 --age 38 --height 178 \
                                            --max-hr 190 --lthr 170 --ftp 250 --weight 72 [--bike-weight 8]

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

from magene_import import _connect, CC02

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


async def run(args):
    client = await _connect(args.address)
    try:
        if args.action == "battery":
            pct = await read_battery(client)
            return {"ok": pct is not None, "batteryPercent": pct}
        if args.action == "info":
            return {"ok": True, "info": await read_info(client)}
        if args.action == "set-time":
            return {"ok": await set_time(client)}
        if args.action == "set-timezone":
            off = args.offset_seconds
            if off is None:
                off = -time.timezone if time.daylight == 0 else -time.altzone
            return {"ok": await set_timezone(client, int(off))}
        if args.action == "altitude":
            return {"ok": await altitude_correct(client)}
        if args.action == "read-profile":
            prof = await read_profile(client)
            return {"ok": prof is not None, "profile": prof}
        if args.action == "set-profile":
            return {"ok": await set_profile(client, args.sex, args.age, args.height,
                                            args.max_hr, args.lthr, args.ftp, args.weight,
                                            args.bike_weight)}
        return {"ok": False, "error": f"unknown action {args.action}"}
    finally:
        await client.disconnect()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["battery", "info", "set-time", "set-timezone",
                                        "altitude", "read-profile", "set-profile"])
    ap.add_argument("--address", required=True)
    ap.add_argument("--offset-seconds", type=int, default=None)
    ap.add_argument("--sex", type=int, default=1)
    ap.add_argument("--age", type=int, default=30)
    ap.add_argument("--height", type=int, default=175)
    ap.add_argument("--max-hr", type=int, default=190)
    ap.add_argument("--lthr", type=int, default=170)
    ap.add_argument("--ftp", type=int, default=200)
    ap.add_argument("--weight", type=float, default=70.0)
    ap.add_argument("--bike-weight", type=float, default=0.0)
    args = ap.parse_args()
    print(json.dumps(asyncio.run(run(args))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
