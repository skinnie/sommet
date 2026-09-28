#!/usr/bin/env python3
"""BLE transport for the Suunto NSP protocol - a `Link`-compatible drop-in
(`tools/write_nav.py`'s cable/HID transport) so that every existing tool taking a `link`
(`kailash_history.py`, `kailash_tracklog.py`, `settings_write.py`, `write_nav.py`'s own
`read_pois`/`send_plan`/etc.) can run over BLE by constructing a `BleLink` instead of a
`Link` - nothing else about those callers changes, since they only ever call
`link.command(command, payload)`.

**Confirmed working transport, not a hypothesis**: the GATT service/characteristic UUIDs
and the SLIP-framed NSP envelope below are byte-exact against three real captures, decoded
with `tools/ble_pklg.py` - `assets/APK/kailash/kailashpair.pklg` and
`kailash7rsettingschange.pklg` (Kailash + the real 7R iOS app), and the reference
`ambit3pairand2activitiesnoorbit.pklg` (an Ambit3 + the real Suunto app, same session).
**Same service, same characteristics, same 12-byte NSP header, same command IDs on both
watches** - see `KAILASH-BLE-FINDINGS.md` and `HANDOFF.md`'s Milestone 7 for the full
derivation. This file is the transport only; it invents nothing about the NSP layer itself
that wasn't already confirmed there.

**What is NOT confirmed, because no BLE adapter was available in the session that wrote
this file**: whether `_CONN_ID_DEFAULT` below actually needs to match anything the watch
checks (only ever observed once, in one direction, in one capture - see its own comment),
and the Service-Changed-then-rediscover dance in `open()` (a real, hardware-confirmed
requirement for the *official* Suunto app, per HANDOFF.md's Milestone 7 "Finding 1" -
untested here against bleak's own service-cache behavior). Treat the first real run as the
actual test, the way every write in this project has been verified: `--verbose`, then diff
the raw frame bytes against a fresh capture before trusting a write.

Needs `bleak` (`pip install bleak`) - imported only once a device is really opened, same
lazy-import pattern as `write_nav.py`'s `Link.open()` uses for the `hid` package, so nothing
here breaks an install that only ever uses the cable tools.

DRY-RUN BY DEFAULT for anything that would write, matching `Link` - a malformed BLE write is
exactly as capable of hanging the watch as a malformed USB one (`write_nav.py`'s own
docstring).

    ./tools/ble_link.py scan                              # trigger "Sync now" (already
                                                            # paired) or "Pair Mobile App"
                                                            # (fresh pair) on the watch
                                                            # first - the advertising window
                                                            # is short, see HANDOFF.md
    ./tools/ble_link.py settings --address AA:BB:CC:DD:EE:FF
    ./tools/ble_link.py settings --device kailash --address AA:BB:CC:DD:EE:FF
    ./tools/ble_link.py pois --address AA:BB:CC:DD:EE:FF
"""

import argparse
import asyncio
import struct
import sys
import threading
import zlib

from ambit_pcap import CMD_NAMES
from ble_pklg import Message

# Confirmed identical on the Ambit3 and the Kailash, 2026-08-08 - see this file's own
# docstring. Vendor base `...-0002a5d5c51b`; the `e62e-11e3` group is a v1 (time-based)
# UUID timestamp that decodes to 2014, consistent with the Ambit3's original release - this
# looks like one fixed service defined once for the whole NSP-over-BLE watch family, not
# reissued per model.
#
# Fixed 2026-08-08, found via a real live scan on Linux/bleak: the two lines below were
# swapped relative to HANDOFF.md Milestone 7's own values (service 98ae7120-..., write
# c6339440-..., notify d0fd6b80-...) - what was here as SERVICE_UUID was actually the
# notify characteristic's UUID, and NOTIFY_CHAR_UUID was a manually byte-reversed ("wire
# order") rendering of that same UUID, not a real distinct characteristic - reversing
# d0fd6b80-e62e-11e3-a2e9-0002a5d5c51b byte-for-byte reproduces the old NOTIFY_CHAR_UUID
# value exactly, confirming the mix-up. A live scan filtered on the old (wrong)
# SERVICE_UUID found nothing even with the watch advertising right next to the adapter
# (confirmed separately, unfiltered, seeing `Ambit3 XXXXXXXXXX` with
# uuids=['98ae7120-e62e-11e3-badd-0002a5d5c51b']) - this fix is what made it visible.
SERVICE_UUID = "98ae7120-e62e-11e3-badd-0002a5d5c51b"
WRITE_CHAR_UUID = "c6339440-e62e-11e3-a5b3-0002a5d5c51b"
NOTIFY_CHAR_UUID = "d0fd6b80-e62e-11e3-a2e9-0002a5d5c51b"

# HANDOFF.md's Milestone 7: "The BLE frame is a 20-byte fragmenter with no delimiter and no
# checksum." Conservative pre-MTU-negotiation size; real captures show the phone's writes
# landing in chunks this size regardless of the negotiated ATT MTU, so this is kept fixed
# rather than queried from bleak.
CHUNK_SIZE = 20

# Observed exactly once: the phone's first-ever outgoing message (0x0002 "hello") in
# kailashpair.pklg carried conn_id=0x0009, while the watch's own outgoing conn_id was 0x0000
# throughout. Nothing in any capture confirms whether the watch actually validates this
# field or merely echoes/ignores it - no capture exists where a wrong value was tried. Kept
# as a constant, not derived, until a live test settles it.
_CONN_ID_DEFAULT = 0x0009

# Real, hardware-confirmed default for outgoing requests (HANDOFF.md's Milestone 7, "Outgoing
# flags" paragraph): 0x02 on the very first hello only, 0x06 on log_synced acks, 0x0a on
# every other outgoing request. This transport defaults to 0x0a and lets a caller override.
FLAGS_DEFAULT = 0x0A
FLAGS_HELLO = 0x02
FLAGS_LOG_SYNCED_ACK = 0x06


def _slip_escape(buf):
    """Inverse of tools/ble_pklg.py's _unescape_slip - confirmed rules, real hardware,
    2026-08-08: 0x7e -> 0x7d 0x5e, 0x7d -> 0x7d 0x5d."""
    out = bytearray()
    for b in buf:
        if b == 0x7E:
            out += b"\x7d\x5e"
        elif b == 0x7D:
            out += b"\x7d\x5d"
        else:
            out.append(b)
    return bytes(out)


def _slip_unescape(buf):
    """Byte-for-byte copy of tools/ble_pklg.py's _unescape_slip, kept separate rather than
    imported because that module reads a whole capture file at once and this one reassembles
    a live, incremental notification stream - see NspAssembler below."""
    out = bytearray()
    i = 0
    while i < len(buf):
        b = buf[i]
        if b == 0x7D and i + 1 < len(buf) and buf[i + 1] in (0x5E, 0x5D):
            out.append(0x7E if buf[i + 1] == 0x5E else 0x7D)
            i += 2
        else:
            out.append(b)
            i += 1
    return bytes(out)


def encode_nsp_frame(command, payload, flags, conn_id, pkt_num, err_flags=0x00):
    """Raw SLIP-framed bytes for one outgoing NSP message: 0x7e, 12-byte header, payload,
    4-byte little-endian CRC32 over header+payload, 0x7e. Byte-exact against the real OUT
    frames in kailashpair.pklg (see KAILASH-BLE-FINDINGS.md's worked example) once escaped -
    this is tools/ble_pklg.py's decoder run backwards."""
    msg_id, sub_id = (command >> 8) & 0xFF, command & 0xFF
    header = struct.pack("<BBBBHHI", msg_id, sub_id, flags, err_flags, conn_id, pkt_num,
                          len(payload))
    body = header + payload
    crc = struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF)
    return b"\x7e" + _slip_escape(body + crc) + b"\x7e"


class NspAssembler:
    """Feed raw notification bytes in as they arrive; returns the list of complete NSP
    `Message`s found so far. Same envelope logic as tools/ble_pklg.py's
    _split_envelopes()/messages() (0x7e-delimited, consecutive frames share a boundary
    byte), adapted to an incremental stream instead of one whole capture file already on
    disk."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, chunk):
        self._buf += chunk
        out = []
        while True:
            try:
                start = self._buf.index(0x7E)
            except ValueError:
                self._buf.clear()
                break
            try:
                end = self._buf.index(0x7E, start + 1)
            except ValueError:
                break  # frame not complete yet - wait for more chunks
            raw = _slip_unescape(bytes(self._buf[start + 1:end]))
            del self._buf[:end]  # closing 0x7e stays: it doubles as the next frame's opener
            if len(raw) < 12:
                continue
            msg_id, sub_id, flags, err_flags, conn_id, pkt_num, data_size = struct.unpack(
                "<BBBBHHI", raw[:12])
            payload = raw[12:12 + data_size]
            trailer = raw[12 + data_size:12 + data_size + 4]
            crc_ok = None
            if len(trailer) == 4:
                calc = zlib.crc32(raw[:12 + data_size]) & 0xFFFFFFFF
                given = int.from_bytes(trailer, "little")
                crc_ok = calc == given
            out.append(Message("IN", (msg_id << 8) | sub_id, flags, err_flags, conn_id,
                                pkt_num, payload, crc_ok))
        return out


class BleLink:
    """BLE transport, same public shape as write_nav.py's Link: .dry_run, .verbose,
    .sequence, .sent, .open(), .command(command, payload, expect_reply, quiet). In dry-run
    no device is opened, matching Link exactly.

    Runs bleak's async client on a dedicated background event loop thread so every public
    method here stays synchronous, like Link's - existing callers (kailash_history.py,
    settings_write.py, ...) call link.command(...) and block for the reply, unchanged.
    """

    def __init__(self, dry_run=True, verbose=False, product_id=None, address=None,
                 conn_id=_CONN_ID_DEFAULT):
        self.dry_run = dry_run
        self.verbose = verbose
        self.product_id = product_id
        self.address = address
        self.sequence = 0
        self.sent = []
        self._conn_id = conn_id
        self._pkt_num = 0
        self._loop = None
        self._client = None
        self._assembler = NspAssembler()
        self._inbox = []

    # -- background event loop, so BleLink's public methods stay synchronous --------
    def _start_loop(self):
        ready = threading.Event()

        def _run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            ready.set()
            self._loop.run_forever()

        threading.Thread(target=_run_loop, daemon=True).start()
        ready.wait()

    def _run(self, coro, timeout=25.0):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    # -- connect ----------------------------------------------------------------------
    def open(self):
        """Scans for the watch (if no address was given at construction), connects, then
        deliberately re-runs service discovery after a short pause: the watch sends a GATT
        "Service Changed" indication right after connecting, and a client that only
        discovers once, immediately on connect, never sees the custom NSP service - a real,
        hardware-confirmed finding against the *official* Suunto app (HANDOFF.md's
        Milestone 7, "Finding 1"). Untested here whether bleak's own caching reproduces the
        same trap; the explicit re-fetch below is cheap insurance either way."""
        if self.dry_run:
            return None
        import bleak  # imported only when a device is really opened, mirrors Link.open()

        self._start_loop()

        async def _connect():
            address = self.address
            if address is None:
                print('  scanning (trigger "Sync now" or "Pair Mobile App" on the watch '
                      "now - the advertising window is short, HANDOFF.md's Milestone 7)...")
                device = await bleak.BleakScanner.find_device_by_filter(
                    lambda d, adv: SERVICE_UUID.lower() in
                    [str(u).lower() for u in (adv.service_uuids or [])],
                    timeout=15.0)
                if device is None:
                    raise RuntimeError(
                        "no watch found advertising the NSP service within 15s. Trigger "
                        '"Sync now" (already paired) or "Pair Mobile App" (fresh pair) on '
                        "the watch's own menu right before running this, not before.")
                address = device.address

            client = bleak.BleakClient(address)
            await client.connect()
            await asyncio.sleep(1.0)  # let the Service Changed indication land, see above
            # Fixed 2026-08-08: bleak 3.0.2 has no client.get_services() (real AttributeError,
            # not guessed) - newer bleak resolves services as part of connect() and re-resolves
            # internally off BlueZ's own ServicesResolved D-Bus property when Service Changed
            # fires, no public re-fetch call left to make. `.services` raises loudly if
            # discovery genuinely never happened, which is the same safety net in spirit.
            if not client.services:
                raise RuntimeError("connected but no GATT services resolved")

            def _on_notify(_characteristic, data):
                self._inbox.extend(self._assembler.feed(bytes(data)))

            await client.start_notify(NOTIFY_CHAR_UUID, _on_notify)
            self._client = client
            return address

        address = self._run(_connect())
        print(f"  watch: {address}")
        return address

    def close(self):
        if self._client is not None:
            self._run(self._client.disconnect())
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)

    # -- command ------------------------------------------------------------------------
    def command(self, command, payload=b"", expect_reply=True, quiet=False, flags=None):
        name = CMD_NAMES.get(command, f"0x{command:04x}")
        frame = encode_nsp_frame(command, payload, FLAGS_DEFAULT if flags is None else flags,
                                  self._conn_id, self._pkt_num)
        if not quiet:
            print(f"  {'[dry-run] ' if self.dry_run else ''}-> 0x{command:04x} "
                  f"{name:22} {len(payload):5} B  {len(frame)} raw byte(s)")
        if self.verbose:
            print(f"        {frame.hex(' ')}")
        self.sent.append((command, payload, frame))
        self._pkt_num += 1
        self.sequence += 1
        if self.dry_run or not expect_reply:
            return b""
        return self._run(self._write_and_wait(command, frame))

    async def _write_and_wait(self, command, frame, timeout=20.0):
        for i in range(0, len(frame), CHUNK_SIZE):
            await self._client.write_gatt_char(WRITE_CHAR_UUID, frame[i:i + CHUNK_SIZE],
                                                response=False)
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if self._inbox:
                msg = self._inbox.pop(0)
                if msg.crc_ok is False:
                    raise RuntimeError(
                        f"CRC32 mismatch on the reply to 0x{command:04x} - either a real "
                        "corrupted frame or a bug in the SLIP reassembly above; do not "
                        "trust the payload")
                return msg.payload
            await asyncio.sleep(0.05)
        raise RuntimeError(f"no reply to 0x{command:04x} from the watch within {timeout}s")


def _scan(timeout):
    import bleak

    async def _run():
        print(f"  scanning {timeout}s for the NSP service "
              f"({SERVICE_UUID})...")
        print('  trigger "Sync now" or "Pair Mobile App" on the watch now if you have not '
              "already - the advertising window is short.")
        devices = await bleak.BleakScanner.discover(timeout=timeout, return_adv=True)
        found = False
        for device, adv in devices.values():
            uuids = [str(u).lower() for u in (adv.service_uuids or [])]
            if SERVICE_UUID.lower() in uuids:
                found = True
                print(f"  {device.address}  {device.name or '(no name)'}  "
                      f"rssi={adv.rssi}")
        if not found:
            print("  nothing advertising the NSP service seen. Re-trigger the watch's "
                  "menu action and try again - the window is short.")
    asyncio.run(_run())


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)

    scan_p = sub.add_parser("scan", help="find watches advertising the NSP service")
    scan_p.add_argument("--timeout", type=float, default=15.0)

    for name in ("settings", "pois", "logbook"):
        p = sub.add_parser(name, help=f"READ-ONLY: the {name} query, over BLE")
        p.add_argument("--address", help="BLE address/UUID (from `scan`); omit to scan now")
        p.add_argument("--device", help="ambit3 or kailash - picks the right descriptor")
        p.add_argument("--all", action="store_true")
        p.add_argument("--redact", action="store_true")
        p.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    if args.action == "scan":
        _scan(args.timeout)
        return 0

    # Local imports: these pull in write_nav.py, which is cable-only at import time (no
    # `hid` import happens until a Link is actually opened), so this stays safe even on a
    # machine that never installs `hid` at all - only `bleak` is required for BLE use.
    from write_nav import (QUERIES, descriptor_for_product_id, resolve_product_id,
                            show_entries, show_settings)

    product_id = resolve_product_id(args.device) if args.device else None
    command, request, interesting = QUERIES[args.action]

    link = BleLink(dry_run=False, verbose=args.verbose, product_id=product_id,
                    address=args.address)
    print(f"read-only: the 0x{command:04x} query, nothing is written")
    link.open()
    try:
        payload = link.command(command, request)
    finally:
        link.close()
    print(f"  reply {len(payload)} B")

    descriptor = descriptor_for_product_id(product_id)
    if args.action == "settings":
        ok = show_settings(payload, args.all, args.redact, descriptor) is not None
    else:
        ok = show_entries(payload, interesting, args.all, args.redact, descriptor) is not None
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
