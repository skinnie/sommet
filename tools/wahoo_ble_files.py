#!/usr/bin/env python3
"""Send, list and fetch files on a Wahoo ELEMNT over Bluetooth - the file channel of its BLE service, as
the open-source BoltOn app (gitlab.com/Hague/bolton, MIT) does it. Used by Sommet to put routes
(and later plans) on the ELEMNT without a cable (André, 2026-10-03, "be sure to have them usb and
bluetooth").

    ./tools/wahoo_ble_files.py send LOCAL /sdcard/routes/Name.gpx
    ./tools/wahoo_ble_files.py list /data/data/com.wahoofitness.bolt/files/routes/12/
    ./tools/wahoo_ble_files.py pull "/sdcard/exports/<ride>.fit" ride.fit
    ./tools/wahoo_ble_files.py rides                      # the ride files the ELEMNT keeps
    ./tools/wahoo_ble_files.py fetch DEST NAME.fit ...    # those rides, over one connection

Protocol (characteristic a026e036, all multi-packet messages framed "cmd id seq <=17 bytes"):
  06/07  start transfer = 00 <op> <utf-8 path> 00 <offset u32> - 01 pull (the ELEMNT sends the
         file, raw), 02 push (we send it, gzipped chunks). A pull answers "08 id <seq> 00 01 01
         <result> <size u32>" (result 0 = OK, 1 = no such file), then streams the file in 4096-byte
         chunks - "0a id seq <data>" packets, "0b" closing each chunk, seq restarting per chunk, no
         acks - and ends "09 00 id 00" (2026-10-03: a 7900-byte ride and a 300 KB file came back
         byte-identical; ~2 KB/s). Keep-alive writes every 5 s or the link drops at ~80 s.
         Not used: 03 also pulls, gzipped; some other values delete the file (a push from offset 0
         deletes the old file first) - not pinned down, so there is no BLE delete here.
  08     "more than one chunk follows" = 08 id 00 01 02 00 00 00 00 00
  0a/0b  each 4096-byte chunk, gzipped, prefixed 00 01 00 00 (or, for the last chunk, the size
         of that chunk as u32 LE); the ELEMNT answers each chunk "0c id ..."
  09     end = 09 00 id; the ELEMNT answers "09 00 id <status>", 0 = written.
  01/02  list a folder = 00 03 00 <utf-8 path> 00 01; the answer comes as 04/05 packets:
         <3 bytes> 00 03 00 <path NUL> <count u16> {format u8, name NUL, time u32, size u32,
         [format 3: checksum NUL]}; acknowledged with 03 00.
"""

import argparse
import asyncio
import gzip
import json
import os
import struct
import sys

SUF = "-0a7d-4ab3-97fa-f1500f9feb8b"
FILE_UUID = "a026e036" + SUF
KEEPALIVE = "a026e01c" + SUF
CHUNK = 4096


def _packets(cmd_body, cmd_end, msg_id, payload):
    out, seq = [], 0
    for i in range(0, max(len(payload), 1), 17):
        part = payload[i:i + 17]
        cmd = cmd_end if i + 17 >= len(payload) else cmd_body
        out.append(bytes([cmd, msg_id, seq & 255]) + part)
        seq += 1
    return out


async def send(local, remote, msg_id=0x21):
    from bleak import BleakClient, BleakScanner
    data = open(local, "rb").read()
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: bool(d.name) and d.name.upper().startswith("ELEMNT"), timeout=20)
    if dev is None:
        raise RuntimeError("no ELEMNT advertising over Bluetooth (on? not connected to a phone?)")
    q = asyncio.Queue()

    def on_file(_s, d):
        b = bytes(d)
        if len(b) >= 2 and b[0] in (0x0c, 0x09):
            q.put_nowait(b)

    async def write_all(c, pkts):
        for p in pkts:
            await c.write_gatt_char(FILE_UUID, p, response=False)
            await asyncio.sleep(0.02)

    async def wait(kind, timeout=20):
        while True:
            b = await asyncio.wait_for(q.get(), timeout)
            if b[0] == kind:
                return b

    async with BleakClient(dev, timeout=20) as c:
        await c.start_notify(FILE_UUID, on_file)
        await c.write_gatt_char(KEEPALIVE, b"\x00", response=False)
        ka = asyncio.ensure_future(_keepalive(c))
        await asyncio.sleep(0.3)
        name = b"\x00\x02" + remote.encode("utf-8") + b"\x00" * 5
        await write_all(c, _packets(0x06, 0x07, msg_id, name))
        chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
        if len(chunks) > 1:
            await write_all(c, [bytes([0x08, msg_id, 0, 1, 2, 0, 0, 0, 0, 0])])
        for n, chunk in enumerate(chunks):
            last = n == len(chunks) - 1
            head = struct.pack("<I", len(data) % CHUNK) if last else b"\x00\x01\x00\x00"
            await write_all(c, _packets(0x0a, 0x0b, msg_id, head + gzip.compress(chunk)))
            await wait(0x0c)
        await write_all(c, [bytes([0x09, 0, msg_id])])
        done = await wait(0x09)
        ka.cancel()
    if len(done) < 4 or done[3] != 0:
        raise RuntimeError("the ELEMNT refused the file (%s)" % done.hex())
    return {"remote": remote, "bytes": len(data)}


async def list_dir(path, msg_id=0x22):
    from bleak import BleakClient, BleakScanner
    if not path.endswith("/"):
        path += "/"
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: bool(d.name) and d.name.upper().startswith("ELEMNT"), timeout=20)
    if dev is None:
        raise RuntimeError("no ELEMNT advertising over Bluetooth (on? not connected to a phone?)")
    parts, done = {}, asyncio.Event()

    def on_file(_s, d):
        b = bytes(d)
        if len(b) >= 3 and b[0] in (0x04, 0x05) and b[1] == msg_id:
            parts[b[2]] = b[3:]
            if b[0] == 0x05:
                done.set()

    async with BleakClient(dev, timeout=20) as c:
        await c.start_notify(FILE_UUID, on_file)
        await c.write_gatt_char(KEEPALIVE, b"\x00", response=False)
        await asyncio.sleep(0.3)
        req = b"\x00\x03\x00" + path.encode("utf-8") + b"\x00\x01"
        for pkt in _packets(0x01, 0x02, msg_id, req):
            await c.write_gatt_char(FILE_UUID, pkt, response=False)
            await asyncio.sleep(0.02)
        await asyncio.wait_for(done.wait(), 20)
        await c.write_gatt_char(FILE_UUID, b"\x03\x00", response=False)
    msg = b"".join(parts[k] for k in sorted(parts))
    return _parse_listing(msg, path)


async def _keepalive(c, every=5.0):
    """Write the keep-alive byte every few seconds for as long as the transfer runs: without it
    the ELEMNT drops the link after about 80 s (a 150 KB pull, 2026-10-03)."""
    while True:
        await asyncio.sleep(every)
        try:
            await c.write_gatt_char(KEEPALIVE, b"\x00", response=False)
        except Exception:                      # noqa: BLE001 - the transfer itself reports errors
            return


async def _start(c, op, remote, msg_id):
    req = b"\x00" + bytes([op]) + remote.encode("utf-8") + b"\x00" * 5
    for pkt in _packets(0x06, 0x07, msg_id, req):
        await c.write_gatt_char(FILE_UUID, pkt, response=False)
        await asyncio.sleep(0.02)


async def _find():
    from bleak import BleakScanner
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: bool(d.name) and d.name.upper().startswith("ELEMNT"), timeout=20)
    if dev is None:
        raise RuntimeError("no ELEMNT advertising over Bluetooth (on? not connected to a phone?)")
    return dev


async def pull(remote, local=None, msg_id=0x23, client=None):
    """The ELEMNT's file `remote` -> bytes (also written to `local` when given)."""
    from bleak import BleakClient
    rsp, chunks, cur, done = {}, [], [], asyncio.Event()
    got_rsp = asyncio.Event()

    def on_file(_s, d):
        b = bytes(d)
        if len(b) < 3 or b[1] != msg_id and b[0] != 0x09:
            return
        if b[0] == 0x08 and len(b) >= 11:
            rsp["result"], rsp["size"] = b[6], struct.unpack_from("<I", b, 7)[0]
            got_rsp.set()
        elif b[0] in (0x0a, 0x0b):
            cur.append(b[3:])
            if b[0] == 0x0b:
                chunks.append(b"".join(cur))
                cur.clear()
        elif b[0] == 0x09 and len(b) >= 3 and b[2] == msg_id:
            done.set()

    async def run(c):
        await c.start_notify(FILE_UUID, on_file)
        ka = asyncio.ensure_future(_keepalive(c))
        try:
            await c.write_gatt_char(KEEPALIVE, b"\x00", response=False)
            await asyncio.sleep(0.3)
            await _start(c, 1, remote, msg_id)
            await asyncio.wait_for(got_rsp.wait(), 20)
            if rsp["result"] != 0:
                raise FileNotFoundError("the ELEMNT has no %s (result %d)" % (remote, rsp["result"]))
            if rsp["size"]:
                # ~2 KB/s over BLE; scale the wait with the size, generously.
                await asyncio.wait_for(done.wait(), 30 + rsp["size"] / 1000)
        finally:
            ka.cancel()
            await c.stop_notify(FILE_UUID)

    if client is not None:
        await run(client)
    else:
        async with BleakClient(await _find(), timeout=20) as c:
            await run(c)
    data = b"".join(chunks) + b"".join(cur)
    if len(data) != rsp["size"]:
        raise RuntimeError("got %d of %d bytes of %s" % (len(data), rsp["size"], remote))
    if local:
        with open(local, "wb") as fh:
            fh.write(data)
    return data


RIDES = "/sdcard/exports"


async def fetch_rides(dest, names):
    """Pull ride files from /sdcard/exports over ONE connection, named like the cable import
    (DEST/wahoo__<name>) - [{kind, name, path}] for the files that came through."""
    from bleak import BleakClient
    os.makedirs(dest, exist_ok=True)
    copied, errors = [], []
    async with BleakClient(await _find(), timeout=20) as c:
        for n, name in enumerate(names):
            out = os.path.join(dest, "wahoo__" + os.path.basename(name))
            try:
                await pull("%s/%s" % (RIDES, os.path.basename(name)), out, msg_id=0x30 + n % 0x40, client=c)
                copied.append({"kind": "wahoo", "name": os.path.basename(name), "path": out})
            except (RuntimeError, FileNotFoundError, asyncio.TimeoutError) as e:
                errors.append("%s: %s" % (name, e))
    return copied, errors


def _parse_listing(msg, path):
    o = msg.find(b"\x00\x03\x00")
    if o < 0:
        raise RuntimeError("unexpected listing reply %s" % msg[:16].hex())
    o += 3
    end = msg.index(b"\x00", o)
    o = end + 1
    count, = struct.unpack_from("<H", msg, o)
    o += 2
    files = []
    for _ in range(count):
        fmt = msg[o]
        o += 1
        end = msg.index(b"\x00", o)
        name = msg[o:end].decode("utf-8", "replace")
        o = end + 1
        stamp, size = struct.unpack_from("<II", msg, o)    # BoltOn labels these the other way
        o += 8
        if fmt == 3:
            o = msg.index(b"\x00", o) + 1
        files.append({"name": name, "dir": fmt == 0, "size": size, "time": stamp})
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["send", "list", "pull", "rides", "fetch"])
    ap.add_argument("args", nargs="*", help="send: LOCAL REMOTE; pull: REMOTE LOCAL; list: FOLDER; "
                                            "fetch: DEST NAME... (or --only-stdin)")
    ap.add_argument("--only-stdin", action="store_true",
                    help="fetch: read {\"files\": [{\"name\"}]} from stdin")
    args = ap.parse_args()
    try:
        if args.command == "list":
            out = {"ok": True, "files": asyncio.run(list_dir(args.args[0]))}
        elif args.command == "rides":
            files = asyncio.run(list_dir(RIDES))
            rides = sorted(f["name"] for f in files if not f["dir"] and f["name"].lower().endswith(".fit"))
            out = {"ok": True, "files": rides}
        elif args.command == "fetch":
            names = list(args.args[1:])
            if args.only_stdin:
                spec = json.loads(sys.stdin.read() or "{}")
                names += [f.get("name") for f in spec.get("files", []) if f.get("name")]
            copied, errors = asyncio.run(fetch_rides(args.args[0], names))
            out = {"ok": bool(copied) or not names, "copied": copied, "count": len(copied), "errors": errors}
            if not out["ok"]:
                out["error"] = "; ".join(errors)
        elif args.command == "pull":
            data = asyncio.run(pull(args.args[0], args.args[1]))
            out = {"ok": True, "remote": args.args[0], "local": args.args[1], "bytes": len(data)}
        else:
            out = dict(asyncio.run(send(args.args[0], args.args[1])), ok=True)
    except Exception as e:                     # noqa: BLE001 - one JSON error line for callers
        out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
