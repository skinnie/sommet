#!/usr/bin/env python3
"""Bryton Aero 60 over Bluetooth (André, 2026-09-26: "since we reverse engineer everything else
... we should do it also", no sniffing). Everything here comes from the Bryton Active 3.0.110
decompile (bleplugin/NewSettingUtil + ParserUtil) and was checked live against his Aero 60.

    ./tools/bryton_ble.py scan                          -> {ok, devices:[{kind, name, address, rssi}]}
    ./tools/bryton_ble.py hello --address ADDR          -> {ok, batteryBars, batteryOf, profile, settings}
    ./tools/bryton_ble.py set-profile --address ADDR [--ftp W] [--lthr B] [--max-hr B] [--map W]
                                      [--weight KG] [--height CM]
    ./tools/bryton_ble.py set-settings --address ADDR  < {"backlight": 6, "autoPause": 1, ...}
                                                        -> {ok, written, settings}
    ./tools/bryton_ble.py rides --address ADDR          -> {ok, rides:[{name, fileId, seconds, sport}]}
    ./tools/bryton_ble.py pull --address ADDR --dest DIR [--only-stdin]
                                                        -> {ok, copied:[{kind, name, path}]}

Every connection first says "I'm the new Bryton Active app" (setting 47, `[47, 1, 0]`), as the
app does; without it the device shows "please use new Active app" and refuses file transfers.

Rides (command + data channels, service 454D7788…): a command is `[cmd, type<<5 | seq<<2, …]`
(type 0 command / 1 response / 2 action). The device answers `[cmd, 1<<5|seq<<2, status]`
(2 = OK, 6 = nothing to sync); on OK we send the action "go" (3) and it streams numbered
packets on the data channel: #0 = [0,0, count u16 BE, last size, cmd, …], #1..count = payload,
then an end packet whose bytes 3..6 are the payload's byte sum (u32 BE). Every 10th packet is
acknowledged with action 6 (continue).
  list  `[11, seq, 0]`  -> 36-byte records: fileId (u32 BE at +1, epoch s; the device's file
        name is that time as yyMMddHHmmss), seconds (+5), metres (+9), sport (+18). Only rides
        the phone hasn't synced are listed (the Aero 60 has no "all files" mode).
  get   `[17, seq, fileId u32 BE, type 1 (FIT), offset u32 BE, size u32 BE]` - size 4 at offset 0
        returns the file size first, then the file in <= 39600-byte ranges.
Pulling a ride doesn't mark it synced on the device - the Bryton app still gets it too.

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
import os
import struct
import sys

SCAN_SERVICE = "00002014-0000-1000-8000-00805f9b34fb"
SETTING_CHAR = "50b81188-c2de-a994-094d-340090726877"
COMMAND_CHAR = "454d2288-a122-058d-9b42-9d0f3772af82"
DATA_CHAR = "454d1188-a122-058d-9b42-9d0f3772af82"
CMD_NEW_APP = 47
CMD_FILE_LIST, CMD_FILE_RANGE = 11, 17
TYPE_COMMAND, TYPE_RESPONSE, TYPE_ACTION = 0, 1, 2
ACTION_GO, ACTION_CONTINUE = 3, 6
STATUS_OK, STATUS_NOTHING = 2, 6
FLOW = 10                       # packets per "continue" ack (BbcpUtil.flowCtrl default)
RANGE = 39600                   # max bytes per range request (CMD_17_CHUNK_SIZE)
FIT_TYPE = 1

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

    async def new_app(self):
        """Tell the device this is the new Bryton Active app (it ACKs with [47, 10])."""
        await self.c.write_gatt_char(SETTING_CHAR, _frame(CMD_NEW_APP, 1, 0), response=True)
        await asyncio.sleep(1.0)


class Files:
    """The command + data channels: one request at a time, reassembling the data stream."""

    def __init__(self, client):
        self.c = client
        self.seq = 0
        self.status = None
        self.cur = None

    async def start(self):
        await self.c.start_notify(COMMAND_CHAR, lambda _h, d: asyncio.ensure_future(self._on_cmd(bytes(d))))
        await self.c.start_notify(DATA_CHAR, lambda _h, d: asyncio.ensure_future(self._on_data(bytes(d))))

    async def _on_cmd(self, d):
        if len(d) < 3 or (d[1] >> 5) != TYPE_RESPONSE:
            return
        seq = (d[1] >> 2) & 7
        if d[2] == STATUS_OK:
            await self.c.write_gatt_char(COMMAND_CHAR, bytes([d[0], (TYPE_ACTION << 5) | (seq << 2), ACTION_GO]),
                                         response=True)
        elif self.cur is not None:
            self.status = d[2]
            self.cur["ev"].set()

    async def _on_data(self, d):
        if self.cur is None or len(d) < 2:
            return
        idx = struct.unpack(">H", d[:2])[0]
        if idx == 0:
            self.cur["count"] = struct.unpack(">H", d[2:4])[0]
            self.cur["pk"] = {}
            if self.cur["count"] == 0:
                self.cur["ev"].set()
            return
        if "count" not in self.cur:
            return
        if idx > self.cur["count"]:
            self.cur["sum"] = struct.unpack(">I", d[3:7])[0] if len(d) >= 7 else None
            self.cur["ev"].set()
            return
        self.cur["pk"][idx] = d[2:]
        if idx % FLOW == FLOW - 1:
            await self.c.write_gatt_char(COMMAND_CHAR, bytes([self.cur["cmd"], (TYPE_ACTION << 5) | (self.seq << 2),
                                                              ACTION_CONTINUE]), response=True)

    async def request(self, cmd, payload=b"", raw_seq=False, timeout=60.0):
        """Send a command, return the reassembled data (b"" = nothing / status != OK)."""
        self.seq = (self.seq + 1) % 8
        self.status = None
        self.cur = {"cmd": cmd, "ev": asyncio.Event()}
        second = self.seq if raw_seq else (TYPE_COMMAND << 5) | (self.seq << 2)
        await self.c.write_gatt_char(COMMAND_CHAR, bytes([cmd, second]) + payload, response=True)
        try:
            await asyncio.wait_for(self.cur["ev"].wait(), timeout)
        except asyncio.TimeoutError:
            raise RuntimeError(f"the Bryton didn't answer command {cmd}")
        cur, self.cur = self.cur, None
        if self.status is not None and self.status != STATUS_OK:
            if self.status == STATUS_NOTHING:
                return b""
            raise RuntimeError(f"the Bryton refused command {cmd} (status {self.status})")
        n = cur.get("count", 0)
        missing = [i for i in range(1, n + 1) if i not in cur.get("pk", {})]
        if missing:
            raise RuntimeError(f"{len(missing)} of {n} packets lost")
        body = b"".join(cur["pk"][i] for i in range(1, n + 1))
        if cur.get("sum") is not None and sum(body) != cur["sum"]:
            raise RuntimeError("checksum mismatch")
        return body


def _ride_name(file_id):
    import datetime
    return datetime.datetime.fromtimestamp(file_id, datetime.timezone.utc).strftime("%y%m%d%H%M%S") + ".fit"


async def list_rides(files):
    body = await files.request(CMD_FILE_LIST, bytes([0]), raw_seq=True)
    rides = []
    for i in range(len(body) // 36):
        r = body[i * 36:(i + 1) * 36]
        fid = struct.unpack(">I", r[1:5])[0]
        rides.append({"name": _ride_name(fid), "fileId": fid,
                      "seconds": struct.unpack(">I", r[5:9])[0],
                      "meters": struct.unpack(">I", r[9:13])[0], "sport": r[18]})
    return rides


async def get_ride(files, file_id):
    head = await files.request(CMD_FILE_RANGE, struct.pack(">IBII", file_id, FIT_TYPE, 0, 4))
    if len(head) < 4:
        raise RuntimeError("the Bryton didn't give the file size")
    total = struct.unpack(">I", head[:4])[0]
    out = b""
    while len(out) < total:
        want = min(RANGE, total - len(out))
        part = await files.request(CMD_FILE_RANGE, struct.pack(">IBII", file_id, FIT_TYPE, len(out), want))
        if not part:
            raise RuntimeError("the Bryton stopped sending")
        out += part[:want]
    if out[8:12] != b".FIT":
        raise RuntimeError("not a FIT file")
    return out


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


# Device settings the app writes as a plain [cmd, 1, value] on the Aero 60 (version-0 encodings;
# NewSettingUtil.setBacklight/setGps/setAutoPause/setKeyTone/setSound/setUnit), with the values the
# app's own menus allow (BackLightMenuUtil default menu, GpsMenuUtil default menu).
SETTINGS = {
    "backlight": (CMD_BACKLIGHT, range(0, 7)),   # 0 5s, 1 15s, 2 30s, 3 1min, 4 2min, 5 never, 6 auto
    "gpsMode": (CMD_GPS, range(0, 5)),           # 0 off, 1 GPS+Gal+QZ, 2 power save, 3 GPS+GLONASS, 4 GPS+BeiDou
    "autoPause": (CMD_AUTO_PAUSE, range(0, 2)),
    "keytone": (CMD_KEYTONE, range(0, 2)),
    "sound": (CMD_SOUND, range(0, 2)),
    "unit": (CMD_UNIT, range(0, 2)),             # 0 metric, 1 imperial
}


def check_settings(changes):
    for name, value in changes.items():
        if name not in SETTINGS:
            raise ValueError(f"unknown setting {name}")
        if int(value) not in SETTINGS[name][1]:
            raise ValueError(f"{name}={value} not allowed")


async def set_settings(link, changes):
    check_settings(changes)
    done = []
    for name, value in changes.items():
        cmd = SETTINGS[name][0]
        if await link.request(_frame(cmd, 1, int(value))) is None:
            raise RuntimeError(f"no answer writing {name}")
        done.append(name)
    return done


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
    # Validate writes BEFORE connecting, so a bad request never reaches the device.
    changes = None
    if args.action == "set-settings":
        changes = json.loads(sys.stdin.read() or "{}")
        if not changes:
            raise ValueError("nothing to set")
        check_settings(changes)
    async with BleakClient(args.address, timeout=20) as client:
        link = Link(client)
        await link.start()
        await link.new_app()
        if args.action in ("rides", "pull"):
            files = Files(client)
            await files.start()
            rides = await list_rides(files)
            if args.action == "rides":
                return {"ok": True, "rides": rides}
            only = None
            if args.only_stdin:
                spec = json.loads(sys.stdin.read() or "{}")
                only = {f.get("name") for f in spec.get("files", [])}
            os.makedirs(args.dest, exist_ok=True)
            copied = []
            for r in rides:
                if only is not None and r["name"] not in only:
                    continue
                data = await get_ride(files, r["fileId"])
                path = os.path.join(args.dest, "bryton__" + r["name"])
                with open(path, "wb") as fh:
                    fh.write(data)
                copied.append({"kind": "bryton", "name": r["name"], "path": path})
            return {"ok": True, "copied": copied}
        if args.action == "hello":
            out = {"ok": True, "address": args.address, **(await read_all(link))}
            # The same connection lists the rides the phone hasn't synced (the card's "N new").
            try:
                files = Files(client)
                await files.start()
                out["rides"] = [r["name"] for r in await list_rides(files)]
            except Exception as exc:  # noqa: BLE001 - the card still works without the ride list
                out["ridesError"] = str(exc)
            return out
        if args.action == "set-settings":
            written = await set_settings(link, changes)
            after = await read_all(link)
            return {"ok": True, "written": written, "settings": after["settings"]}
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
    ap.add_argument("action", choices=["scan", "hello", "set-profile", "set-settings", "rides", "pull"])
    ap.add_argument("--dest")
    ap.add_argument("--only-stdin", action="store_true")
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
