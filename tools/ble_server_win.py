#!/usr/bin/env python3
"""NSP-over-BLE for WINDOWS: the same host role as tools/ble_server.py, on Windows' own Bluetooth.

The host runs the GATT server and the watch connects into it as a client (see ble_server.py's
docstring). On Linux BlueZ does the hosting; here Windows' GattServiceProvider does, through the
WinRT projection (`winrt-*` packages, which bleak already pulls into the frozen backend).

WHAT WAS PROVEN FIRST (P5Q, Windows 10, CSR8510 "Orico" stick, 2026-11-21, GattWatchTest.ps1):
  - The stick fails Windows' peripheral-role check (IsPeripheralRoleSupported = False), so an
    ADVERTISED service fails (0x8007013C). Nothing needs to advertise: the watch advertises and
    this computer dials it, exactly as on Linux and Android. Published unadvertised, the service
    is served over the link we open.
  - ~19 s after the link came up the watch subscribed to our notify channel and wrote its
    opener in 20-byte chunks: 0x1201, 17 B, CRC ok.
  - WinRT GATT hosting needs the user's desktop session: over SSH CreateAsync answers
    RadioNotAvailable. The backend starts this daemon from the app, which is in that session.
  - Basic PairAsync failed at once (an unpackaged app has no pairing UI of its own); custom
    pairing with the passkey the watch shows (Passkey Entry, see ble_server.py's Agent) is used.

Nothing is changed for the rest of Sommet: ServerLink, handle_rx and the control socket's
requests are ble_server.py's own. Only the socket differs: Windows' Python has no Unix sockets,
so the control socket is TCP on 127.0.0.1 with a random port and a random token, both written
to ~/.cache/Sommet/ble.port (the user's own profile). Every request must carry the token.

Windows' Bluetooth stays Windows': headphones, mice and pens keep working next to this.

    sommet-backend.exe --tool tools/ble_server_win.py listen [--verbose] [--timeout N]
"""

import argparse
import asyncio
import collections
import json
import os
import secrets
import socket
import struct
import sys
import threading
import time
import uuid
import zlib
from pathlib import Path

from ble_link import NspAssembler, _slip_unescape
from ble_server import (NAME_PREFIXES, NOTIFY_CHAR_UUID, SCAN_ON_S, SCAN_PERIOD_S, SERVICE_UUID,
                        SUUNTO_OUI, WRITE_CHAR_UUID, ControlSocketServer, ServerLink, _Stamped,
                        handle_rx)

PORT_FILE = Path.home() / ".cache" / "Sommet" / "ble.port"

# A link that starts no session is dropped so the watch can call again (ble_server.py's
# EMPTY_LINK_S). Longer here: on the P5Q the first subscription came ~19 s after the link.
EMPTY_LINK_S = 40.0
PASSKEY_WAIT_S = 60.0


# --- the GLib face ServerLink needs, on an asyncio loop ------------------------------------
class LoopTimers:
    """`GLib.timeout_add(ms, fn, *args)` on an asyncio loop: fn runs on the loop thread, again
    every `ms` while it returns True. Safe to call from any thread, like GLib's."""

    def __init__(self, loop):
        self.loop = loop

    def timeout_add(self, ms, fn, *args):
        def run():
            if fn(*args):
                self.loop.call_later(ms / 1000.0, run)

        self.loop.call_soon_threadsafe(self.loop.call_later, ms / 1000.0, run)
        return 0

    def timeout_add_seconds(self, s, fn, *args):
        return self.timeout_add(s * 1000, fn, *args)


class InOrder:
    """Write requests are taken on WinRT threads and their values fetched asynchronously, so
    two chunks could finish out of order. Each gets a number when it arrives and is handed on
    strictly in that order (a frame split across chunks must be reassembled as sent)."""

    def __init__(self, deliver):
        self._deliver = deliver
        self._next_in = 0
        self._next_out = 0
        self._done = {}
        self._lock = threading.Lock()

    def ticket(self):
        with self._lock:
            n = self._next_in
            self._next_in += 1
            return n

    def finish(self, n, data):
        """`data` None = that chunk could not be read; skipped so the rest still flows."""
        ready = []
        with self._lock:
            self._done[n] = data
            while self._next_out in self._done:
                ready.append(self._done.pop(self._next_out))
                self._next_out += 1
        for d in ready:
            if d is not None:
                self._deliver(d)


def frame_ok(data):
    """True when `data` is exactly one whole NSP frame: 0x7e, the escaped frame, 0x7e, with the
    length its header gives and a matching CRC32 (ble_link.NspAssembler's own rules)."""
    if len(data) < 14 or data[0] != 0x7E or data[-1] != 0x7E or 0x7E in data[1:-1]:
        return False
    raw = _slip_unescape(bytes(data[1:-1]))
    if len(raw) < 16:
        return False
    size = struct.unpack_from("<I", raw, 8)[0]
    if len(raw) != 12 + size + 4:
        return False
    return zlib.crc32(raw[:12 + size]) & 0xFFFFFFFF == int.from_bytes(raw[-4:], "little")


class Reorder:
    """Puts the watch's 20-byte chunks back in order (P5Q, 2026-11-21: Windows raised two
    chunks of one frame the wrong way round, several times in one minute).

    The watch writes with ATT Write Command (opcode 0x52 on c6339440 in the Suunto app's capture,
    assets/ble 2026-08-09/), which has no reply, so Windows hands them to us on several threads
    with no order kept. The frame itself says how to fix it: the watch cuts each frame into
    chunks of its own, and 0x7e only marks a frame's two ends (inside it is escaped). So a
    chunk starting with 0x7e opens a frame, one ending with it closes it, any other is a middle
    one, and only one order of the middles gives the length and the CRC32 the frame carries.

    Tried: arrival order, then every middle moved up to MAX_MOVE places (a race between threads
    swaps neighbours, it does not shuffle a whole frame). A frame still not whole after
    GIVE_UP_S goes on in arrival order, and the assembler drops it as damaged, as before."""

    MAX_MOVE = 3
    GIVE_UP_S = 3.0

    def __init__(self, emit, clock=time.monotonic):
        self._emit = emit
        self._clock = clock
        self._pool = []                # (arrival time, chunk) not yet part of a whole frame

    def reset(self):
        self._pool.clear()

    def feed(self, chunk):
        self._pool.append((self._clock(), bytes(chunk)))
        while self._take_frame():
            pass
        now = self._clock()
        stale = [c for t, c in self._pool if now - t > self.GIVE_UP_S]
        if stale:
            for c in stale:
                self._emit(c)
            self._pool = [(t, c) for t, c in self._pool if now - t <= self.GIVE_UP_S]

    def _take_frame(self):
        chunks = [c for _, c in self._pool]
        whole = [i for i, c in enumerate(chunks) if len(c) >= 2 and c[0] == 0x7E and c[-1] == 0x7E]
        if whole:
            self._send([whole[0]], chunks)
            return True
        starts = [i for i, c in enumerate(chunks) if c[0] == 0x7E]
        ends = [i for i, c in enumerate(chunks) if c[-1] == 0x7E and c[0] != 0x7E or c == b"\x7e"]
        middles = [i for i, c in enumerate(chunks) if 0x7E not in c]
        for s in starts:
            for e in ends:
                order = self._find(chunks, s, middles, e)
                if order is not None:
                    self._send(order, chunks)
                    return True
        return False

    def _find(self, chunks, s, middles, e):
        head, tail = chunks[s], chunks[e]
        # A frame's middles are every middle chunk waiting (frames come one at a time: the
        # watch answers one request before the next is sent). Arrival order first.
        if frame_ok(head + b"".join(chunks[m] for m in middles) + tail):
            return [s, *middles, e]
        n = len(middles)
        for i in range(n):
            for j in range(max(0, i - self.MAX_MOVE), min(n, i + self.MAX_MOVE + 1)):
                if i == j:
                    continue
                trial = middles[:i] + middles[i + 1:]
                trial.insert(j, middles[i])
                if frame_ok(head + b"".join(chunks[m] for m in trial) + tail):
                    return [s, *trial, e]
        return None

    def _send(self, order, chunks):
        for i in order:
            self._emit(chunks[i])
        taken = set(order)
        self._pool = [p for i, p in enumerate(self._pool) if i not in taken]


def is_watch(name, address, service_uuids, known=()):
    """The watch calling: the NSP UUID in its advert (what the P5Q and BlueZ saw), a watch name,
    Suunto's address prefix, or a watch this computer is already paired with."""
    if any(str(u).lower() == SERVICE_UUID for u in service_uuids or ()):
        return True
    if name and any(name.startswith(p) for p in NAME_PREFIXES):
        return True
    return address_text(address).startswith(SUUNTO_OUI) or address in known


def address_text(address):
    h = f"{int(address):012X}"
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


# --- control socket over TCP ---------------------------------------------------------------
class TcpControlServer(ControlSocketServer):
    """ble_server.py's control socket, on 127.0.0.1 with a token instead of a 0600 Unix socket.
    `forget` is this transport's own (Windows' pairing list, not BlueZ)."""

    def __init__(self, *args, forget=None, port_file=PORT_FILE, **kwargs):
        super().__init__(*args, **kwargs)
        self._forget = forget
        self.port_file = Path(port_file)
        self.token = secrets.token_hex(16)

    def _handle(self, request):
        if not secrets.compare_digest(str(request.pop("token", "")), self.token):
            return {"ok": False, "error": "bad token"}
        if request.get("op") == "forget":
            if self._forget is None:
                return {"ok": False, "error": "forget is not available"}
            try:
                return {"ok": True, "removed": self._forget()}
            except Exception as exc:                       # noqa: BLE001 - reported to the caller
                return {"ok": False, "error": str(exc)}
        return super()._handle(request)

    def bind(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        sock.listen(4)
        self.port_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.port_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"port": sock.getsockname()[1], "token": self.token,
                                   "pid": os.getpid()}), encoding="utf-8")
        tmp.replace(self.port_file)
        return sock

    def serve_forever(self, sock=None):
        sock = sock or self.bind()
        print(f"  control socket: 127.0.0.1:{sock.getsockname()[1]}")
        try:
            while True:
                conn, _ = sock.accept()
                threading.Thread(target=self._serve_client, args=(conn,), daemon=True).start()
        finally:
            sock.close()
            self.remove_port_file()

    def remove_port_file(self):
        try:
            if json.loads(self.port_file.read_text(encoding="utf-8")).get("token") == self.token:
                self.port_file.unlink()
        except (OSError, ValueError):
            pass


class Agent:
    """The passkey hand-off ble_server.py's Agent offers the control socket (`submit_passkey`,
    `pending_passkey_device`), for Windows' custom pairing."""

    def __init__(self):
        self.pending_passkey_device = None
        self._value = None
        self._ready = threading.Event()

    def submit_passkey(self, passkey):
        if self.pending_passkey_device is None:
            return False
        self._value = int(passkey)
        self._ready.set()
        return True

    def wait(self, device, timeout=PASSKEY_WAIT_S):
        self._value = None
        self._ready.clear()
        self.pending_passkey_device = device
        try:
            return self._value if self._ready.wait(timeout) else None
        finally:
            self.pending_passkey_device = None

    def cancel(self):
        self._value = None
        self._ready.set()


class LinkState:
    """What `status` reports about the link (the fields ble_server.LinkState gives)."""

    def __init__(self):
        self.path = None
        self.last_drop = None
        self.pairing_hint = None

    def status(self):
        return {"linked_device": self.path, "pairing_hint": self.pairing_hint,
                "bluez_stuck": None, "quick_drops": 0, "last_drop": self.last_drop}


# --- the WinRT side ------------------------------------------------------------------------
def _winrt():
    """Imported only here, so the rest of this file (and its tests) loads anywhere."""
    from winrt.windows.devices.bluetooth import (BluetoothCacheMode, BluetoothConnectionStatus,  # noqa: PLC0415
                                                 BluetoothError, BluetoothLEDevice)
    from winrt.windows.devices.bluetooth import advertisement as adv  # noqa: PLC0415
    from winrt.windows.devices.bluetooth import genericattributeprofile as gatt  # noqa: PLC0415
    from winrt.windows.devices import enumeration as enum  # noqa: PLC0415
    from winrt.windows.storage import streams  # noqa: PLC0415
    return BluetoothCacheMode, BluetoothConnectionStatus, BluetoothError, BluetoothLEDevice, adv, gatt, enum, streams


def to_buffer(streams, data):
    w = streams.DataWriter()
    w.write_bytes(bytes(data))
    return w.detach_buffer()


def from_buffer(streams, buf):
    try:
        return bytes(memoryview(buf))
    except TypeError:
        r = streams.DataReader.from_buffer(buf)
        out = bytearray(buf.length)
        r.read_bytes(out)
        return bytes(out)


class NotifyChannel:
    """The `notify_chrc` ServerLink drives: `.notify(chunk)` and `.notifying`."""

    def __init__(self, chrc, streams, loop, verbose=False):
        self.chrc = chrc
        self.verbose = verbose
        self._streams = streams
        self._loop = loop
        self._queue = collections.deque()
        self._busy = False

    @property
    def notifying(self):
        return len(self.chrc.subscribed_clients) > 0

    def notify(self, chunk):
        """Called on the loop thread (ServerLink paces chunks with timeout_add). One send at a
        time, the next started from the previous one's Completed callback, so our chunks reach
        the watch in order, and the loop never waits inside Windows (see WinHost._on_write)."""
        self._queue.append(bytes(chunk))
        if not self._busy:
            self._busy = True
            self._send_next()

    def _send_next(self):
        if not self._queue:
            self._busy = False
            return
        chunk = self._queue.popleft()
        try:
            op = self.chrc.notify_value_async(to_buffer(self._streams, chunk))
        except Exception as exc:                           # noqa: BLE001
            print(f"  notify failed: {exc}")
            self._loop.call_soon(self._send_next)
            return

        def completed(o, _status):
            try:
                states = [r.status.name + (f" {r.protocol_error}" if r.protocol_error else "")
                          for r in o.get_results()]
                if self.verbose or any(st != "SUCCESS" for st in states):
                    print(f"  TX {len(chunk):3d} B  {chunk.hex()}  -> {', '.join(states) or 'no client'}")
            except Exception as exc:                       # noqa: BLE001
                print(f"  notify failed: {exc}")
            self._loop.call_soon_threadsafe(self._send_next)

        op.completed = completed


class WinHost:
    def __init__(self, verbose=False):
        (self.CacheMode, self.ConnStatus, self.BtError, self.LEDevice, self.adv, self.gatt,
         self.enum, self.streams) = _winrt()
        self.verbose = verbose
        self.loop = asyncio.get_running_loop()
        self.timers = LoopTimers(self.loop)
        self.agent = Agent()
        self.state = LinkState()
        self.assembler = NspAssembler()
        self.rx_total = [0]
        self.link = None
        self.device = None
        self.session = None
        self.known = set()            # addresses of watches already paired with this computer
        self.watcher = None
        self.dialing = False
        self._reorder = Reorder(self._on_chunk)
        self._order = InOrder(self._reorder.feed)

    # ---- hosting ----
    async def host(self):
        g = self.gatt
        res = await g.GattServiceProvider.create_async(uuid.UUID(SERVICE_UUID))
        if res.error != self.BtError.SUCCESS:
            raise RuntimeError(f"Windows refused to host the watch service: {res.error.name} "
                               "(RADIO_NOT_AVAILABLE: Bluetooth off, or not the desktop session)")
        self.provider = res.service_provider
        P = g.GattCharacteristicProperties
        p = g.GattLocalCharacteristicParameters()
        p.characteristic_properties = P.NOTIFY | P.READ
        n = await self.provider.service.create_characteristic_async(uuid.UUID(NOTIFY_CHAR_UUID), p)
        p = g.GattLocalCharacteristicParameters()
        p.characteristic_properties = P.WRITE_WITHOUT_RESPONSE | P.WRITE
        w = await self.provider.service.create_characteristic_async(uuid.UUID(WRITE_CHAR_UUID), p)
        for r, what in ((n, "notify"), (w, "write")):
            if r.error != self.BtError.SUCCESS:
                raise RuntimeError(f"{what} characteristic: {r.error.name}")
        self.notify_chrc = NotifyChannel(n.characteristic, self.streams, self.loop, self.verbose)
        self.link = ServerLink(self.notify_chrc, self.timers)
        n.characteristic.add_subscribed_clients_changed(self._on_subscribed)
        w.characteristic.add_write_requested(self._on_write)
        ap = g.GattServiceProviderAdvertisingParameters()
        ap.is_connectable = False      # the watch is dialed, never dials us (see docstring)
        ap.is_discoverable = False
        self.provider.start_advertising(ap)
        print(f"  NSP service hosted ({SERVICE_UUID}), published to linked devices only: "
              f"{self.provider.advertisement_status.name}")

    def _on_subscribed(self, chrc, _args):
        count = len(chrc.subscribed_clients)
        print(f"  watch {'subscribed' if count else 'unsubscribed'} (clients: {count})")
        if count and not self.link.handshake_done.is_set():
            self.loop.call_soon_threadsafe(self._fresh_session)

    def _fresh_session(self):
        self._reorder.reset()
        self.assembler.reset()
        self.link.reset()

    def _on_write(self, _chrc, args):
        """On a WinRT thread. The request is fetched through its own completion callback, also
        on a WinRT thread, and only the bytes go to the loop. Awaiting GetRequestAsync on the
        asyncio loop froze it inside Windows (P5Q, 2026-11-21: the loop stuck in native code,
        writes #0 and #1 never read; the C# probe that used Completed never stuck)."""
        n = self._order.ticket()
        if self.verbose:
            print(f"  write #{n} on thread {threading.get_ident()}")
        deferral = args.get_deferral()
        op = args.get_request_async()

        def completed(o, _status):
            data = None
            try:
                req = o.get_results()
                data = from_buffer(self.streams, req.value)
                if req.option == self.gatt.GattWriteOption.WRITE_WITH_RESPONSE:
                    req.respond()
            except Exception as exc:                       # noqa: BLE001
                print(f"  write request #{n} failed: {exc}")
            finally:
                deferral.complete()
                self.loop.call_soon_threadsafe(self._order.finish, n, data)

        op.completed = completed

    def _on_chunk(self, chunk):
        self.rx_total[0] += len(chunk)
        handle_rx(self.assembler, self.link, chunk, self.verbose)

    # ---- finding and dialing the watch ----
    async def paired_watches(self):
        sel = self.LEDevice.get_device_selector_from_pairing_state(True)
        out = []
        for info in await self.enum.DeviceInformation.find_all_async_aqs_filter(sel):
            if any((info.name or "").startswith(p) for p in NAME_PREFIXES):
                out.append(info)
        return out

    async def forget(self):
        removed = 0
        for info in await self.paired_watches():
            r = await info.pairing.unpair_async()
            print(f"  unpaired {info.name}: {r.status.name}")
            removed += r.status == self.enum.DeviceUnpairingResultStatus.UNPAIRED
        self.known.clear()
        return removed

    async def remember_paired(self):
        for info in await self.paired_watches():
            try:
                dev = await self.LEDevice.from_id_async(info.id)
                if dev:
                    self.known.add(dev.bluetooth_address)
                    print(f"  already paired with this computer: {info.name}")
                    dev.close()
            except Exception:                              # noqa: BLE001
                pass

    def start_scanning(self):
        a = self.adv
        self.watcher = a.BluetoothLEAdvertisementWatcher()
        self.watcher.scanning_mode = a.BluetoothLEScanningMode.ACTIVE
        self.watcher.add_received(self._on_advert)
        self._scan_burst()

    def _scan_burst(self):
        # In bursts, and not at all while a watch is linked or being dialed (ble_server.py: a
        # scan that never stopped kept a Bluetooth mouse from reconnecting).
        if self.watcher is None:
            return
        if self.device is None and not self.dialing:
            try:
                self.watcher.start()
                self.loop.call_later(SCAN_ON_S, self._scan_pause)
            except Exception as exc:                       # noqa: BLE001
                print(f"  scan could not start: {exc}")
        self.loop.call_later(SCAN_PERIOD_S, self._scan_burst)

    def _scan_pause(self):
        try:
            self.watcher.stop()
        except Exception:                                  # noqa: BLE001
            pass

    def _on_advert(self, _w, args):
        if self.dialing or self.device is not None:
            return
        a = args.advertisement
        if not is_watch(a.local_name, args.bluetooth_address, list(a.service_uuids), self.known):
            return
        self.dialing = True
        print(f"  watch calling: '{a.local_name or '?'}' rssi {args.raw_signal_strength_in_dbm} dBm")
        asyncio.run_coroutine_threadsafe(self.dial(args.bluetooth_address), self.loop)

    async def dial(self, address):
        self._scan_pause()
        try:
            dev = await self.LEDevice.from_bluetooth_address_async(address)
            if dev is None:
                print("  Windows could not open the watch")
                return
            self.device = dev
            self.state.path = dev.name or address_text(address)
            self.state.pairing_hint = None
            dev.add_connection_status_changed(self._on_connection)
            pairing = dev.device_information.pairing
            print(f"  device '{dev.name}', paired: {pairing.is_paired}")
            if not pairing.is_paired and pairing.can_pair:
                await self.pair(dev)
            self.session = await self.gatt.GattSession.from_device_id_async(dev.bluetooth_device_id)
            self.session.maintain_connection = True
            res = await dev.get_gatt_services_async(self.CacheMode.UNCACHED)
            print(f"  link: {dev.connection_status.name}; the watch's own services: "
                  f"{len(res.services) if res.services else 0}")
            self.known.add(address)
            self.loop.call_later(EMPTY_LINK_S, self._check_empty, dev)
        except Exception as exc:                           # noqa: BLE001
            print(f"  dial failed: {exc}")
            self.drop("dial failed")
        finally:
            self.dialing = False

    async def pair(self, dev):
        """Passkey Entry: the watch shows six digits, the user types them in Sommet (the
        passkey dialog the Linux daemon already drives through `pending_passkey_device`)."""
        e = self.enum
        K = e.DevicePairingKinds
        custom = dev.device_information.pairing.custom

        def on_request(_c, args):
            kind = args.pairing_kind
            if kind == K.PROVIDE_PIN:
                deferral = args.get_deferral()

                def wait():
                    print("  pairing: the watch shows a 6-digit code - type it in Sommet (60 s)")
                    value = self.agent.wait(self.state.path)
                    if value is not None:
                        args.accept_with_pin(f"{value:06d}")
                    else:
                        print("  pairing: no code typed")
                    deferral.complete()

                threading.Thread(target=wait, daemon=True).start()
            elif kind == K.DISPLAY_PIN:
                print(f"  pairing: type {args.pin} on the watch")
                args.accept()
            else:
                print(f"  pairing: {kind.name} - accepted")
                args.accept()

        token = custom.add_pairing_requested(on_request)
        try:
            kinds = K.PROVIDE_PIN | K.CONFIRM_ONLY | K.DISPLAY_PIN | K.CONFIRM_PIN_MATCH
            r = await custom.pair_with_protection_level_async(
                kinds, e.DevicePairingProtectionLevel.ENCRYPTION_AND_AUTHENTICATION)
            print(f"  pairing result: {r.status.name}, level {r.protection_level_used.name}")
            if r.status not in (e.DevicePairingResultStatus.PAIRED,
                                e.DevicePairingResultStatus.ALREADY_PAIRED):
                self.state.pairing_hint = (f"Pairing did not finish ({r.status.name}). Choose "
                                           "“Pair with app” on the watch again.")
        finally:
            custom.remove_pairing_requested(token)

    def _on_connection(self, dev, _args):
        if dev.connection_status == self.ConnStatus.DISCONNECTED:
            self.loop.call_soon_threadsafe(self.drop, "the watch disconnected", dev)

    def _check_empty(self, dev):
        if self.device is dev and not self.link.handshake_done.is_set() and not self.agent.pending_passkey_device:
            self.drop(f"no session within {EMPTY_LINK_S:.0f} s", dev)

    def drop(self, why, dev=None):
        if dev is not None and dev is not self.device:
            return
        if self.device is not None:
            print(f"  link closed: {why}")
            self.state.last_drop = {"device": self.state.path, "why": why,
                                    "subscribed": self.link.handshake_done.is_set(),
                                    "at": time.strftime("%H:%M:%S")}
        self.agent.cancel()
        for obj in (self.session, self.device):
            try:
                if obj is not None:
                    obj.close()
            except Exception:                              # noqa: BLE001
                pass
        self.session = self.device = None
        self.state.path = None
        self.link.reset()
        self._reorder.reset()
        self.assembler.reset()


def other_daemon(port_file=PORT_FILE):
    """True when another daemon answers on the port in `port_file`: two would both host the
    service and the watch would talk to only one of them (P5Q, 2026-11-21)."""
    try:
        port = json.loads(Path(port_file).read_text(encoding="utf-8"))["port"]
        socket.create_connection(("127.0.0.1", int(port)), 1).close()
        return True
    except (OSError, ValueError, KeyError):
        return False


def _watchdog(loop, stall_s=5.0):
    """Writes every thread's stack to the log when the loop stops ticking for `stall_s` (a
    frozen daemon looks exactly like a silent watch otherwise). Once per stall."""
    import faulthandler                                    # noqa: PLC0415
    beat = [time.monotonic()]

    def tick():
        beat[0] = time.monotonic()
        loop.call_later(1.0, tick)

    loop.call_soon(tick)

    def watch():
        dumped = False
        while True:
            time.sleep(1.0)
            stalled = time.monotonic() - beat[0] > stall_s
            if stalled and not dumped:
                print(f"  the daemon's loop has not run for {stall_s:.0f} s - thread stacks:")
                sys.stdout.flush()
                faulthandler.dump_traceback(file=sys.__stderr__, all_threads=True)
                sys.__stderr__.flush()
            dumped = stalled

    threading.Thread(target=watch, daemon=True).start()


async def _listen(verbose, timeout, forget, serve_socket):
    if serve_socket and other_daemon():
        raise RuntimeError("another Sommet Bluetooth service is already running")
    _watchdog(asyncio.get_running_loop())
    host = WinHost(verbose)
    await host.host()
    if forget:
        await host.forget()
    await host.remember_paired()
    control = None
    if serve_socket:
        def do_forget():
            removed = asyncio.run_coroutine_threadsafe(host.forget(), host.loop).result(25)
            host.loop.call_soon_threadsafe(host.drop, "forgotten")
            return removed

        control = TcpControlServer(host.link, host.rx_total, agent=host.agent,
                                   link_state=host.state, forget=do_forget)
        sock = control.bind()
        threading.Thread(target=control.serve_forever, args=(sock,), daemon=True).start()
    host.start_scanning()
    print('  now choose "Pair with app" (first time) or "Sync now" on the watch')
    try:
        if timeout > 0:
            await asyncio.sleep(timeout)
            print(f"  stopping after {timeout}s ({host.rx_total[0]} B received)")
        else:
            await asyncio.Event().wait()
    finally:
        host.drop("stopping")
        try:
            host.provider.stop_advertising()
        except Exception:                                  # noqa: BLE001
            pass
        if control is not None:
            control.remove_port_file()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)
    lp = sub.add_parser("listen", help="host the NSP service and wait for the watch")
    lp.add_argument("--verbose", action="store_true")
    lp.add_argument("--timeout", type=int, default=0)
    lp.add_argument("--forget", action="store_true",
                    help="unpair Suunto watches from Windows first (when pairing is stuck)")
    lp.add_argument("--no-socket", action="store_true")
    args = parser.parse_args()
    # Line by line: the frozen backend ignores PYTHONUNBUFFERED, and the daemon's log is a file.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    sys.stdout = _Stamped(sys.stdout)
    code = 0
    try:
        asyncio.run(_listen(args.verbose, args.timeout, args.forget, not args.no_socket))
    except KeyboardInterrupt:
        print("\n  stopped")
    except RuntimeError as exc:
        print(f"  {exc}")
        code = 1
    # Out at once. A normal interpreter exit waited for ever on WinRT's own threads (P5Q,
    # 2026-11-21: a "--timeout 25" run printed its last line and never ended, and kept the
    # service, so the next daemon could not reach the watch).
    sys.stdout.flush()
    os._exit(code)


if __name__ == "__main__":
    main()
