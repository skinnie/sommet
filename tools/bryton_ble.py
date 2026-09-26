#!/usr/bin/env python3
"""Bryton Aero 60 over Bluetooth (André, 2026-09-26: "since we reverse engineer everything else
... we should do it also", no sniffing). Everything here comes from the Bryton Active 3.0.110
decompile (bleplugin/NewSettingUtil + ParserUtil) and was checked live against his Aero 60.

    ./tools/bryton_ble.py scan                          -> {ok, devices:[{kind, name, address, rssi}]}
    ./tools/bryton_ble.py hello --address ADDR          -> {ok, batteryBars, batteryOf, profile, settings}
    ./tools/bryton_ble.py set-profile --address ADDR [--ftp W] [--lthr B] [--max-hr B] [--map W]
                                      [--weight KG] [--height CM]

Setting channel (service 50b85566…, characteristic 50B81188…, write + notify):
  request  [cmd, op, (item), …, sum%256]   op 2 = get, 1 = set
  reply    [cmd, 1, …, sum%256]            the app ACKs every reply with [cmd, 10]
The Aero 60 is an "old-style" device: it doesn't answer the capability query (op 4), so the
version-0 encodings are used throughout. It only advertises (as "AERO60", service 0x2014) while
it isn't connected to the phone app. No bonding is needed for these commands.

Writes are read-modify-write: the two-float user items (height/weight) carry a second value the
app passes through (130.0 on the live device - meaning unknown), so it's read and written back
unchanged. Every value is bounds-checked before anything is sent. bleak + stdlib.
"""

import argparse
import asyncio
import json
import struct
import sys

SCAN_SERVICE = "00002014-0000-1000-8000-00805f9b34fb"
SETTING_CHAR = "50b81188-c2de-a994-094d-340090726877"

CMD_BACKLIGHT, CMD_BATTERY, CMD_KEYTONE, CMD_SOUND = 20, 24, 25, 26
CMD_AUTO_PAUSE, CMD_AUTO_LAP, CMD_UNIT, CMD_USER = 27, 28, 29, 30
CMD_MAP, CMD_FTP, CMD_MHR, CMD_LTHR, CMD_GPS = 32, 33, 34, 35, 40
USER_HEIGHT, USER_WEIGHT, USER_BDAY, USER_GENDER = 0, 1, 2, 3
ACK_OK = 10

# name -> setting cmd (all u16 LE after [cmd, 1, 0]; NewSettingUtil.setZone, base form)
ZONES = {"ftp": CMD_FTP, "max_hr": CMD_MHR, "lthr": CMD_LTHR, "map": CMD_MAP}
BOUNDS = {"ftp": (50, 600), "map": (80, 800), "max_hr": (100, 240), "lthr": (80, 230),
          "weight": (20, 250), "height": (80, 250)}


def _frame(*body):
    b = list(body) + [0]
    b[-1] = sum(b[:-1]) % 256
    return bytes(b)


def _valid(d):
    return len(d) > 2 and sum(d[:-1]) % 256 == d[-1]


class Link:
    """One BLE connection; request() sends a frame and returns the matching reply."""

    def __init__(self, client):
        self.c = client
        self.q = asyncio.Queue()

    async def start(self):
        await self.c.start_notify(SETTING_CHAR, lambda _h, d: self.q.put_nowait(bytes(d)))

    async def request(self, frame, timeout=3.0):
        cmd = frame[0]
        await self.c.write_gatt_char(SETTING_CHAR, frame, response=True)
        loop = asyncio.get_event_loop()
        end = loop.time() + timeout
        while (rem := end - loop.time()) > 0:
            try:
                d = await asyncio.wait_for(self.q.get(), rem)
            except asyncio.TimeoutError:
                break
            if _valid(d):
                await self.c.write_gatt_char(SETTING_CHAR, bytes([d[0], ACK_OK]), response=True)
            if d[0] == cmd and len(d) >= 2:
                return d
        return None

    async def get(self, *body):
        return await self.request(_frame(body[0], 2, *body[1:]))


async def read_all(link):
    out = {"profile": {}, "settings": {}}
    b = await link.get(CMD_BATTERY)
    if b and len(b) >= 5:
        # old-style grid: [24, 1, totalBars, level, sum]; level 0 = charging, 1 = empty,
        # 2..6 = 1..5 bars (ConstSettingChannel.newSettingBatteryGrid)
        total, level = b[2], b[3]
        out["batteryOf"] = total
        out["charging"] = level == 0
        out["batteryBars"] = max(0, level - 1) if level >= 1 else None
    prof = out["profile"]
    h = await link.get(CMD_USER, USER_HEIGHT)
    if h and len(h) >= 12:
        prof["height"] = round(struct.unpack_from("<f", h, 3)[0], 1)
    w = await link.get(CMD_USER, USER_WEIGHT)
    if w and len(w) >= 12:
        prof["weight"] = round(struct.unpack_from("<f", w, 3)[0], 1)
    bd = await link.get(CMD_USER, USER_BDAY)
    if bd and len(bd) >= 8:
        prof["birthday"] = "%04d-%02d-%02d" % (struct.unpack_from("<H", bd, 3)[0], bd[5], bd[6])
    for name, cmd in ZONES.items():
        z = await link.get(cmd, 0)
        if z and len(z) >= 6:
            prof[name] = struct.unpack_from("<H", z, 3)[0]
    s = out["settings"]
    for name, cmd, body in (("backlight", CMD_BACKLIGHT, ()), ("unit", CMD_UNIT, ()),
                            ("sound", CMD_SOUND, ()), ("keytone", CMD_KEYTONE, ()),
                            ("autoPause", CMD_AUTO_PAUSE, (0,)), ("gpsMode", CMD_GPS, (0,))):
        r = await link.get(cmd, *body)
        if r and len(r) >= 4:
            s[name] = r[2]
    al = await link.get(CMD_AUTO_LAP, 0)
    if al and len(al) >= 6:
        s["autoLapType"] = al[2]
        s["autoLapMeters"] = struct.unpack_from("<H", al, 3)[0]
    return out


async def set_profile(link, fields):
    for name, value in fields.items():
        lo, hi = BOUNDS[name]
        if not (lo <= value <= hi):
            raise ValueError(f"{name}={value} out of range {lo}..{hi}")
    done = []
    for name, value in fields.items():
        if name in ZONES:
            v = int(round(value))
            r = await link.request(_frame(ZONES[name], 1, 0, v & 0xFF, v >> 8))
        else:
            item = USER_HEIGHT if name == "height" else USER_WEIGHT
            cur = await link.get(CMD_USER, item)
            if not cur or len(cur) < 12:
                raise RuntimeError(f"couldn't read {name} before writing it")
            second = cur[7:11]                       # unknown second float - written back as read
            r = await link.request(_frame(CMD_USER, 1, item, *struct.pack("<f", float(value)), *second))
        if r is None:
            raise RuntimeError(f"no answer writing {name}")
        done.append(name)
    return done


async def run(args):
    from bleak import BleakClient, BleakScanner

    if args.action == "scan":
        found = await BleakScanner.discover(timeout=args.timeout, return_adv=True)
        devices = []
        for addr, (dev, adv) in found.items():
            uu = [u.lower() for u in (adv.service_uuids or [])]
            name = adv.local_name or dev.name or ""
            if SCAN_SERVICE in uu or name.upper().startswith("AERO"):
                devices.append({"kind": "brytonble", "name": name or "Bryton", "address": addr,
                                "rssi": adv.rssi})
        devices.sort(key=lambda d: -(d["rssi"] or -999))
        return {"ok": True, "devices": devices}

    if not args.address:
        raise ValueError("--address is required")
    async with BleakClient(args.address, timeout=20) as client:
        link = Link(client)
        await link.start()
        if args.action == "hello":
            return {"ok": True, "address": args.address, **(await read_all(link))}
        if args.action == "set-profile":
            fields = {k: v for k, v in (("ftp", args.ftp), ("lthr", args.lthr), ("max_hr", args.max_hr),
                                        ("map", args.map), ("weight", args.weight),
                                        ("height", args.height)) if v is not None}
            if not fields:
                raise ValueError("nothing to set")
            written = await set_profile(link, fields)
            after = await read_all(link)
            return {"ok": True, "written": written, "profile": after["profile"]}
    raise ValueError(f"unknown action {args.action}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["scan", "hello", "set-profile"])
    ap.add_argument("--address")
    ap.add_argument("--timeout", type=float, default=8.0)
    for f in ("--ftp", "--lthr", "--max-hr", "--map"):
        ap.add_argument(f, type=int)
    ap.add_argument("--weight", type=float)
    ap.add_argument("--height", type=float)
    args = ap.parse_args(argv)
    try:
        out = asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001 - one JSON line either way, for the backend
        out = {"ok": False, "error": str(exc) or exc.__class__.__name__}
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
