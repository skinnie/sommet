#!/usr/bin/env python3
"""Pull recorded rides off a Magene C406 (Pro) bike computer over BLE - the device has no USB
data mode (it only charges over the cable; it mounts nothing), so this is the only import path.

    ./tools/magene_import.py --list                          # JSON: nearby C406s (BLE scan)
    ./tools/magene_import.py --rides <address>                # JSON: rides stored on it
    ./tools/magene_import.py --pull <dest-dir> --address <addr>   # copy every ride as .fit
    ./tools/magene_import.py --pull <dest> --address <addr> --since 2026-09-01-00-00-00.fit

Protocol reverse-engineered from scratch against a real C406 Pro (2026-09-24), building on the
GATT service/characteristic UUIDs and the Pages/ride-list wire format documented (but not
implemented) by the open-source OpenBikeCompanion project (github.com/Taxom/OpenBikeCompanion,
GPL-3.0) - their own README calls the ride-download chunk framing "work in progress" with no
code behind it, so that part below (`_reassemble_fit`) is this project's own finding, not a
port. Bytes captured; both the FIT header CRC and file CRC verified against the FIT spec's
CRC-16 to confirm the framing before trusting it - see docs/PROTOCOL.md in that repo for the
rest of the command set (Pages, function settings) this file does not need.

Pairing: the C406 shows a "please pair" screen (no Bluetooth icon) and STAYS there until a
companion BLE-bonds with it. A plain connect - all that's needed to read pages or pull rides -
does not satisfy that; the device sits on the pair screen forever (André, 2026-09-24, hardware).
So _connect() bonds (a Just-Works pairing, no passkey) on connect: the first time (device on its
pair screen) it bonds and the device drops to its normal screen with the BT icon on; afterwards
the bonded device advertises when idle and reconnects from its normal screen, and the pair call
is a harmless no-op. Removing the bond (OS Bluetooth settings / `bluetoothctl remove`) sends it
back to the pair screen.

BLE transport (service 8ce5cc01-0a4d-11e9-ab14-d663bd873d93):
  CC02 (8ce5cc02-...) - command channel: write a command, get status/data back as notifications.
  CC03 (8ce5cc03-...) - bulk channel: ride data is streamed here in ~244-byte notifications.

Commands used here (2-byte opcode, little-endian multi-byte fields):
  40 49 <cursor u32>        -> 40 49 <status> <count u8> <more u8> [<ride_id u32> x count]
                                Ride IDs are the ride's UTC start time as a raw Unix timestamp
                                (confirmed: matches the FIT file's own session start_time).
                                cursor 0 = first page. Pagination (more != 0) is INFERRED from
                                OpenBikeCompanion's docs, not hardware-tested (only ever saw one
                                ride on the test unit): the next cursor is the last ride_id seen.
  40 4a <ride_id u32>        -> 40 4a <status>, then the FIT file streamed as CC03 notifications.

CC03 chunk framing (found here, hardware-confirmed via CRC on 2026-09-24):
  bytes 0:4   ride_id (echoes the request, constant across every chunk of one download)
  bytes 4:8   bytes of FIT data remaining AFTER this chunk, u32 LE (0 on the last chunk)
  bytes 8:10  constant 0x0004 - unknown purpose, always this value in testing
  bytes 10:12 sequence number, u16 LE, 1-based
  byte  12    a per-chunk marker byte, NOT part of the FIT stream (0xe7 on every chunk but the
              last, which carries whatever the next real FIT byte happens to be - i.e. it isn't
              a fixed sentinel, just something that must be dropped along with the 12-byte
              header). Concatenating byte[13:] of every chunk in sequence order reproduces the
              FIT file exactly (header CRC-16 and file CRC-16 both verified byte-for-byte).
"""

import argparse
import asyncio
import datetime
import json
import sys

CC_SERVICE = "8ce5cc01-0a4d-11e9-ab14-d663bd873d93"
CC02 = "8ce5cc02-0a4d-11e9-ab14-d663bd873d93"
CC03 = "8ce5cc03-0a4d-11e9-ab14-d663bd873d93"

SCAN_SECONDS = 6.0
DOWNLOAD_TIMEOUT = 20.0


def _fit_crc16(data: bytes) -> int:
    """FIT SDK's CRC-16 (used to validate a reassembled ride before trusting it)."""
    table = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
             0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400]
    crc = 0
    for byte in data:
        tmp = table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ table[byte & 0xF]
        tmp = table[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ table[(byte >> 4) & 0xF]
    return crc


def _reassemble_fit(chunks):
    """chunks: CC03 notification payloads in arrival order, all from one ride download.
    Returns the FIT file bytes, or None if the stream doesn't check out."""
    chunks = sorted(chunks, key=lambda c: int.from_bytes(c[10:12], "little"))
    stream = b"".join(c[13:] for c in chunks)
    if len(stream) < 14 or stream[8:12] != b".FIT":
        return None
    header_len = stream[0]
    data_size = int.from_bytes(stream[4:8], "little")
    total = header_len + data_size + 2
    if len(stream) < total:
        return None
    fit_bytes = stream[:total]
    if _fit_crc16(fit_bytes[:12]) != int.from_bytes(fit_bytes[12:14], "little"):
        return None
    if _fit_crc16(fit_bytes[:header_len + data_size]) != \
            int.from_bytes(fit_bytes[header_len + data_size:total], "little"):
        return None
    return fit_bytes


async def _scan():
    from bleak import BleakScanner
    found = {}

    def _cb(device, adv):
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if CC_SERVICE in uuids:
            found[device.address] = {
                "address": device.address,
                "name": adv.local_name or device.name or "",
                "rssi": adv.rssi,
            }

    async with BleakScanner(_cb):
        await asyncio.sleep(SCAN_SECONDS)
    return list(found.values())


async def _connect(address):
    from bleak import BleakClient
    client = BleakClient(address, timeout=20.0)
    await client.connect()
    # Bond, not just connect. The C406 sits on its "please pair" screen and shows no Bluetooth
    # icon until a companion BLE-bonds with it - a plain connect (which is all that's needed to
    # read pages and pull rides) leaves it stuck there forever (André, 2026-09-24, hardware). It's
    # a Just-Works pairing (no passkey). Idempotent: once bonded the device reconnects from its
    # normal screen and this is a no-op, so it's safe to call on every connect. Best-effort - a
    # backend that can't pair, or an already-bonded device that reports "already exists", must not
    # fail the sync, which works fine over an existing bond.
    try:
        await client.pair()
    except Exception:
        pass
    return client


async def _ride_list(client):
    """All ride IDs currently stored on the device, oldest call first."""
    rides = []
    cursor = 0
    seen_cursors = set()
    while True:
        if cursor in seen_cursors:
            break  # guard against a misread "more" flag looping forever
        seen_cursors.add(cursor)

        reply = asyncio.get_event_loop().create_future()

        def _cb(_, data, reply=reply):
            if bytes(data[:2]) == b"\x40\x49" and not reply.done():
                reply.set_result(bytes(data))

        await client.start_notify(CC02, _cb)
        try:
            await client.write_gatt_char(CC02, b"\x40\x49" + cursor.to_bytes(4, "little"),
                                          response=True)
            msg = await asyncio.wait_for(reply, timeout=10.0)
        finally:
            await client.stop_notify(CC02)

        if len(msg) < 5 or msg[2] != 0:
            break
        count, more = msg[3], msg[4]
        page_ids = [int.from_bytes(msg[5 + 4 * i:9 + 4 * i], "little") for i in range(count)]
        rides.extend(page_ids)
        if not more or not page_ids:
            break
        cursor = page_ids[-1]
    return rides


async def _download_ride(client, ride_id):
    chunks = []
    done = asyncio.get_event_loop().create_future()

    def _cb(_, data):
        b = bytes(data)
        if len(b) < 13 or int.from_bytes(b[0:4], "little") != ride_id:
            return
        chunks.append(b)
        remaining = int.from_bytes(b[4:8], "little")
        if remaining == 0 and not done.done():
            done.set_result(True)

    await client.start_notify(CC03, _cb)
    try:
        ack = asyncio.get_event_loop().create_future()

        def _ack_cb(_, data):
            if bytes(data[:2]) == b"\x40\x4a" and not ack.done():
                ack.set_result(bytes(data))

        await client.start_notify(CC02, _ack_cb)
        try:
            await client.write_gatt_char(
                CC02, b"\x40\x4a" + ride_id.to_bytes(4, "little"), response=True)
            reply = await asyncio.wait_for(ack, timeout=10.0)
            if len(reply) < 3 or reply[2] != 0:
                return None
            await asyncio.wait_for(done, timeout=DOWNLOAD_TIMEOUT)
        finally:
            await client.stop_notify(CC02)
    finally:
        await client.stop_notify(CC03)

    return _reassemble_fit(chunks)


def _name_for(ride_id):
    dt = datetime.datetime.fromtimestamp(ride_id, datetime.timezone.utc)
    return dt.strftime("%Y-%m-%d-%H-%M-%S.fit")


async def _cmd_list():
    return await _scan()


async def _cmd_rides(address):
    client = await _connect(address)
    try:
        return await _ride_list(client)
    finally:
        await client.disconnect()


async def _cmd_pull(dest, address, since=None, only=None):
    import os
    os.makedirs(dest, exist_ok=True)
    client = await _connect(address)
    copied = []
    try:
        for ride_id in await _ride_list(client):
            name = _name_for(ride_id)
            if only is not None and name not in only:
                continue
            if since and name <= since:
                continue
            fit_bytes = await _download_ride(client, ride_id)
            if fit_bytes is None:
                continue
            out = os.path.join(dest, f"c406__{name}")
            with open(out, "wb") as f:
                f.write(fit_bytes)
            copied.append({"kind": "c406", "name": name, "path": out})
    finally:
        await client.disconnect()
    return copied


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="JSON of nearby C406s (BLE scan)")
    ap.add_argument("--rides", metavar="ADDRESS", help="JSON of ride IDs stored on the device")
    ap.add_argument("--pull", metavar="DEST", help="copy every ride into DEST as .fit")
    ap.add_argument("--address", metavar="ADDR", help="device BLE address, required with --pull")
    ap.add_argument("--since", metavar="NAME", help="only files whose name sorts after NAME")
    ap.add_argument("--only-stdin", action="store_true",
                     help="with --pull, read JSON {\"files\":[{\"name\"}]} from stdin and pull "
                          "only those (incremental sync)")
    args = ap.parse_args()

    if args.rides:
        rides = asyncio.run(_cmd_rides(args.rides))
        print(json.dumps({"ok": True, "rides": [
            {"rideId": r, "name": _name_for(r)} for r in rides]}))
        return 0

    if args.pull:
        if not args.address:
            print(json.dumps({"ok": False, "error": "--pull requires --address"}))
            return 1
        only = None
        if args.only_stdin:
            try:
                spec = json.loads(sys.stdin.read() or "{}")
                only = {f.get("name") for f in spec.get("files", [])}
            except (json.JSONDecodeError, AttributeError):
                only = set()
        copied = asyncio.run(_cmd_pull(args.pull, args.address, since=args.since, only=only))
        print(json.dumps({"ok": True, "copied": copied, "count": len(copied)}))
        return 0

    # default / --list
    devices = asyncio.run(_cmd_list())
    print(json.dumps({"ok": True, "devices": devices}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
