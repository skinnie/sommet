#!/usr/bin/env python3
"""NSP-over-BLE for the DESKTOP - the host side, in the role the watch actually expects.

Real request, 2026-08-11 (André): "port the bluetooth stack of android to desktop".

WHY THIS FILE EXISTS RATHER THAN tools/ble_link.py DOING IT. This project established on real
hardware (PROJECT_OVERVIEW.md; the 2026-08-09 correction; and the working Kotlin module at
android/.../ble/AmbitBleModule.kt) that BLE here is the ANCS pattern inverted from the
obvious one:

    the HOST runs the GATT server, and the WATCH connects into it as a GATT client.

The watch advertises the NSP service UUID as a *solicitation* - "connect to me if you host
NSP" - it does not offer that service itself. That is why every GATT-client approach only
ever found two generic services on it. `tools/ble_link.py` was written as a client (bleak,
BleakClient) in a session with no BLE adapter, before that correction; its NSP framing is
byte-exact against three captures and is reused here, but its transport role was wrong and
bleak cannot host a server at all - it is central-role only.

WHAT THIS IS A TRANSCRIPTION OF. Not new reverse-engineering. The service definition is
exactly what AmbitBleModule.kt's openServerAndConnect() builds, which in turn came from
Suunto's own decompiled BLEService.setService() (assets/APK/movescountapp). Same service,
same two characteristics, same CCCD:

    service 98ae7120-e62e-11e3-badd-0002a5d5c51b
      d0fd6b80-...  NOTIFY (+ CCCD 0x2902)   host -> watch     our TX
      c6339440-...  WRITE_NO_RESPONSE        watch -> host     our RX

Note the direction flip against ble_link.py: as a client it WROTE to c6339440; as the server
we RECEIVE on it. Same wire, opposite end.

STATUS: the D-Bus/BlueZ plumbing below is exercised (registers, scans, runs), but a real
watch session needs André to trigger "Pair Mobile App" (fresh) or "Sync now" (already paired)
on the watch - the advertising window is short. Nothing here writes to a watch; it is a
transport plus a read-only smoke test, in keeping with this project's dry-run-by-default rule.

    ./tools/ble_server.py listen            # host the service and wait for the watch
    ./tools/ble_server.py listen --verbose  # dump every frame both ways
"""

import argparse
import collections
import json
import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path

# The framing is NOT reimplemented here. ble_link.py's SLIP/NSP envelope is byte-exact
# against three real captures, and it is transport-agnostic - it never cared which end of the
# wire it sat on. Only the ROLE was wrong there (it drove a GATT client); the bytes were
# always right, so they are imported rather than copied.
from ble_link import (CHUNK_SIZE, FLAGS_DEFAULT, NspAssembler, encode_nsp_frame)
from ambit_pcap import CMD_NAMES

BLUEZ = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
AGENT_IFACE = "org.bluez.Agent1"
AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"

# Identical on the Ambit3 and the Kailash - see this file's docstring for the two independent
# sources these come from.
SERVICE_UUID = "98ae7120-e62e-11e3-badd-0002a5d5c51b"
NOTIFY_CHAR_UUID = "d0fd6b80-e62e-11e3-a2e9-0002a5d5c51b"
WRITE_CHAR_UUID = "c6339440-e62e-11e3-a5b3-0002a5d5c51b"

BASE_PATH = "/org/ambitapp/nsp"
AGENT_PATH = BASE_PATH + "/agent"

# Real request, 2026-08-11 (André, via the desktop-app conversation): "port the bluetooth
# stack of android to desktop" doesn't stop at this file being reachable from a terminal -
# the actual Qt/Python app (desktop/backend/server.py) needs to reach the SAME connection.
# Unlike USB, a BLE session can't be opened fresh per HTTP request (write_nav.py's own
# per-call Link.open()/close() pattern) - the watch's advertising window is short and the
# GATT server + GLib mainloop above have to stay up across many commands. So this process
# stays the one thing holding the connection, and everything else - the desktop backend,
# any other tool - reaches it through a local control socket instead of trying to open BLE
# itself. Mirrors the Android shape (AmbitBleModule owns the connection, every ReactMethod
# call reaches into it) with a Unix socket standing in for the shared in-process g_device.
SOCKET_PATH = Path.home() / ".cache" / "AmbitApp" / "ble.sock"


class ControlSocketServer:
    """Local Unix-socket bridge onto one `ServerLink`, so a separate process (the desktop
    backend) can issue `link.command()` calls against the connection this process is holding
    open, without needing D-Bus/BlueZ access itself.

    One line-delimited JSON request per line, one JSON reply per line:
      {"op": "status"}
        -> {"ok": true, "subscribed": bool, "rx_total": int}
      {"op": "command", "cmd": int, "payload_hex": "", "expect_reply": true,
       "timeout": 20.0}
        -> {"ok": true, "payload_hex": "..."} or {"ok": false, "error": "..."}
      {"op": "set_dry_run", "value": true}
        -> {"ok": true, "dry_run": bool}
      {"op": "submit_passkey", "passkey": 123456}
        -> {"ok": true} or {"ok": false, "error": "..."}
        A fresh pairing needs this: `status`'s "pending_passkey_device" is non-null while
        the watch is showing a 6-digit code and BlueZ is waiting for the KeyboardDisplay
        agent to type it in - see Agent's own docstring for why this can't be automated.
      {"op": "forget"}
        -> {"ok": true, "removed": int}
        Real request, 2026-08-13 (André, live BLE testing: "we need to add a button to
        forget the watch"). Goes straight at BlueZ through the `bus`/`dbus`/`adapter` this
        process already holds (same `forget_suunto_bonds()` `listen(--forget)` runs at
        startup), not through `ble_bridge.BleBridge.stop()`+`start(forget=True)` - that
        pair only works when THIS backend process is the one that originally spawned the
        daemon (`self._proc`), and `start()`'s own docstring is explicit that an ADOPTED
        daemon (started by another process/session, or a plain terminal run) can't be torn
        down that way. A control-socket op reaches whichever daemon is actually listening,
        spawned-by-us or not.

    `command` calls are serialized with a lock: `ServerLink` (like `write_nav.Link`) only
    tracks one in-flight command's reply at a time, and this socket can have more than one
    client - same reasoning as `desktop/backend/server.py`'s own WATCH_LOCK around
    subprocess tool calls, applied here instead to concurrent socket requests.
    """

    def __init__(self, link, rx_total, agent=None, path=SOCKET_PATH,
                 bus=None, dbus_module=None, adapter=None, glib=None, link_state=None):
        self.link = link
        self.rx_total = rx_total
        self.agent = agent
        self.link_state = link_state
        self.path = Path(path)
        self._cmd_lock = threading.Lock()
        self.bus = bus
        self.dbus_module = dbus_module
        self.adapter = adapter
        self.glib = glib

    def _handle(self, request):
        op = request.get("op")
        if op == "status":
            # "subscribed" is the transport-level signal (CCCD write); "handshake_done"/
            # "device_info" report the higher-level bootstrap exchange - a watch can be
            # subscribed for a moment before it's pushed 0x1201/0x0002, so callers that
            # need real device identity (server.py's /api/device) should wait for
            # handshake_done, not just subscribed.
            # "sync_notices" counts the watch asking for a sync (answered by the daemon);
            # "pairing_hint" is non-null when the watch keeps refusing this computer's key.
            # "subscribed": the session's transport is up. A paired watch that reconnects never
            # subscribes again, so a completed handshake counts too.
            linked = self.link_state is None or self.link_state.path is not None
            out = {"ok": True, "subscribed": self.link.handshake_done.is_set() or
                   (linked and bool(self.link.notify_chrc.notifying)),
                   "handshake_done": self.link.handshake_done.is_set(),
                   "device_info": self.link.device_info,
                   "dry_run": self.link.dry_run, "rx_total": self.rx_total[0],
                   "pending_passkey_device":
                       self.agent.pending_passkey_device if self.agent else None,
                   "sync_notices": self.link.sync_notices,
                   "last_sync_notice_at": self.link.last_sync_notice_at}
            if self.link_state is not None:
                out.update(self.link_state.status())
            return out
        if op == "set_dry_run":
            self.link.dry_run = bool(request.get("value", True))
            return {"ok": True, "dry_run": self.link.dry_run}
        if op == "submit_passkey":
            if not self.agent:
                return {"ok": False, "error": "no pairing agent registered"}
            ok = self.agent.submit_passkey(request.get("passkey"))
            return {"ok": ok} if ok else {"ok": False, "error": "no passkey request pending"}
        if op == "forget":
            if self.bus is None or self.adapter is None:
                return {"ok": False, "error": "no BlueZ handle held by this daemon"}
            # Real bug, found live 2026-08-13 testing this for the first time: unlike
            # every other op here, this one makes real synchronous D-Bus calls
            # (GetManagedObjects/RemoveDevice) - calling those straight from THIS thread
            # (the control socket's own, not the GLib main loop's) hung every request to
            # a timeout. "command" never has this problem because `_send()` only ever
            # schedules work onto the main loop via `GLib.timeout_add` (thread-safe to
            # call from anywhere) and waits on a plain `threading.Event` - the actual
            # D-Bus traffic always happens on the main-loop thread. Doing the same here.
            #
            # `GLib.idle_add` (tried first) still hung: this laptop's BLE neighborhood is
            # noisy (dozens of nearby devices - see `consider()`'s own "seen ..." log line
            # for every one of them), so BlueZ's PropertiesChanged/InterfacesAdded signals
            # keep the loop busy enough, back-to-back, to starve a
            # G_PRIORITY_DEFAULT_IDLE source indefinitely. `timeout_add(0, ...)` runs at
            # G_PRIORITY_DEFAULT instead - the same priority `_send()`'s own pacing
            # already runs at, proven to actually get scheduled in this exact noisy
            # environment all night.
            done = threading.Event()
            result = {}

            def run():
                try:
                    result["removed"] = forget_suunto_bonds(
                        self.bus, self.dbus_module, self.adapter)
                    self.link.reset()
                except Exception as exc:                      # noqa: BLE001
                    result["error"] = str(exc)
                done.set()
                return False                                   # don't repeat

            self.glib.timeout_add(0, run)
            if not done.wait(25.0):
                return {"ok": False, "error": "forget timed out on the main loop"}
            if "error" in result:
                return {"ok": False, "error": result["error"]}
            return {"ok": True, "removed": result["removed"]}
        if op == "command":
            try:
                payload = bytes.fromhex(request.get("payload_hex", ""))
                with self._cmd_lock:
                    reply = self.link.command(
                        int(request["cmd"]), payload,
                        expect_reply=bool(request.get("expect_reply", True)),
                        quiet=True,
                        timeout=float(request.get("timeout", 20.0)))
                return {"ok": True, "payload_hex": reply.hex()}
            except Exception as exc:                       # noqa: BLE001 - reported to the caller, not swallowed
                return {"ok": False, "error": str(exc)}
        return {"ok": False, "error": f"unknown op {op!r}"}

    def _serve_client(self, conn):
        with conn, conn.makefile("rb") as rf, conn.makefile("wb") as wf:
            for line in rf:
                line = line.strip()
                if not line:
                    continue
                try:
                    request = json.loads(line)
                    response = self._handle(request)
                except Exception as exc:                   # noqa: BLE001 - malformed request, not a crash
                    response = {"ok": False, "error": f"bad request: {exc}"}
                wf.write((json.dumps(response) + "\n").encode("utf-8"))
                wf.flush()

    def serve_forever(self):
        """Runs in its own thread - accept() blocks, and this must never share a thread with
        the GLib mainloop `listen()` already needs for D-Bus."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass                                            # first run, nothing stale to clear
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.path))
        # Owner-only: this socket is a direct line to writing the watch's flash, same trust
        # boundary as the desktop backend's own localhost-only HTTP server.
        os.chmod(self.path, 0o600)
        sock.listen(4)
        print(f"  control socket: {self.path}")
        try:
            while True:
                conn, _ = sock.accept()
                threading.Thread(target=self._serve_client, args=(conn,), daemon=True).start()
        finally:
            sock.close()
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

# The name fallback, and on Linux it is not a fallback at all - it is the only live signal.
# BlueZ exposes advertised SERVICE uuids on Device1.UUIDs, but has no D-Bus property for
# service SOLICITATION uuids, which is what these watches actually broadcast. Android has a
# dedicated API for them (serviceSolicitationUuids) and still keeps a name check; here there
# is nothing else to go on while the watch is soliciting. Real miss, 2026-08-11: a UUID-only
# match found the Ambit3 once - from BlueZ's CACHE, populated by an earlier connection's
# service discovery - and never once while it was actually advertising.
#
# "Suunto NSP" is the Kailash: confirmed from a real Android scan and from kailashpair.pklg
# that this, not "Kailash" or "Hoopoe", is its advertised name.
NAME_PREFIXES = ("Ambit3", "Traverse", "Suunto NSP", "Kailash")

# Suunto's OUI. Confirmed 2026-08-11 from a raw btmon capture of a real "Pair Mobile App"
# press: the advertiser was 0C:8C:DC:XX:XX:XX. The same prefix this project already strips
# from Android's bond config when forgetting a pairing, so it is Suunto's across the family.
#
# Matching on it is a backstop for the case a watch advertises with neither a recognised name
# nor the service UUID visible to BlueZ. The same capture settled what it actually sends: the
# NSP UUID rides in AD type 0x07 (complete 128-bit service class list), NOT as a solicitation
# (0x15) - so Device1.UUIDs does carry it and the UUID rule works. An earlier reading here
# claimed 0x15 from a naive byte count; the packet structure says otherwise.
SUUNTO_OUI = "0C:8C:DC"


def _dbus():
    """Imported lazily, exactly like ble_link.py defers bleak and write_nav.py defers hid -
    so a cable-only install is never broken by a missing BLE dependency."""
    import dbus                                            # noqa: PLC0415
    import dbus.mainloop.glib                              # noqa: PLC0415
    import dbus.service                                    # noqa: PLC0415
    from gi.repository import GLib                         # noqa: PLC0415
    return dbus, dbus.service, GLib


def build_classes():
    """The D-Bus object tree BlueZ expects, built after the lazy import above."""
    dbus, dbus_service, GLib = _dbus()

    class Application(dbus_service.Object):
        """The ObjectManager root. BlueZ reads the whole tree from this one call."""

        def __init__(self, bus, service):
            self.path = BASE_PATH
            self.service = service
            dbus_service.Object.__init__(self, bus, self.path)

        @dbus_service.method(DBUS_OM_IFACE, out_signature="a{oa{sa{sv}}}")
        def GetManagedObjects(self):
            out = {self.service.path: self.service.properties()}
            for chrc in self.service.characteristics:
                out[chrc.path] = chrc.properties()
            return out

    class Characteristic(dbus_service.Object):
        def __init__(self, bus, index, uuid, flags, service):
            self.path = service.path + "/char" + str(index)
            self.uuid = uuid
            self.flags = flags
            self.service = service
            self.notifying = False
            # Set by the owner to receive inbound frames (the watch writing to us).
            self.on_write = None
            # Fired once the watch subscribes: BlueZ raises StartNotify then, and the Kotlin
            # module treats exactly that as the "transport is live" signal.
            self.on_subscribed = None
            dbus_service.Object.__init__(self, bus, self.path)

        def properties(self):
            return {GATT_CHRC_IFACE: {
                "Service": dbus.ObjectPath(self.service.path),
                "UUID": self.uuid,
                "Flags": self.flags,
                "Notifying": dbus.Boolean(self.notifying),
            }}

        @dbus_service.method(DBUS_PROP_IFACE, in_signature="s",
                             out_signature="a{sv}")
        def GetAll(self, interface):
            if interface != GATT_CHRC_IFACE:
                raise dbus.exceptions.DBusException(
                    "org.bluez.Error.InvalidArguments")
            return self.properties()[GATT_CHRC_IFACE]

        @dbus_service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
        def WriteValue(self, value, options):
            # The watch writing to us - our RX. BlueZ hands over one ATT write at a time;
            # reassembly into NSP frames is the caller's job (ble_link.py already does it).
            if self.on_write:
                self.on_write(bytes(bytearray(value)))

        @dbus_service.method(GATT_CHRC_IFACE, out_signature="ay")
        def ReadValue(self, options):
            # The notify characteristic is declared readable to mirror the Android service
            # (PERMISSION_READ), but it carries no standing value - data only ever arrives
            # as notifications.
            return dbus.Array([], signature="y")

        @dbus_service.method(GATT_CHRC_IFACE)
        def StartNotify(self):
            if self.notifying:
                return
            self.notifying = True
            print("  watch subscribed - transport is live")
            if self.on_subscribed:
                self.on_subscribed()

        @dbus_service.method(GATT_CHRC_IFACE)
        def StopNotify(self):
            self.notifying = False
            print("  watch unsubscribed")

        @dbus_service.signal(DBUS_PROP_IFACE, signature="sa{sv}as")
        def PropertiesChanged(self, interface, changed, invalidated):
            pass

        def notify(self, payload):
            """Our TX. A notification is delivered by announcing a Value change - that is how
            BlueZ's D-Bus GATT server sends, there is no explicit send method.

            Sent whatever `notifying` says: BlueZ delivers it to each device whose subscription
            it holds. A PAIRED watch keeps its subscription across links (BlueZ keeps a paired
            device's CCC state), so on a reconnect it writes to us without subscribing again and
            StartNotify never comes. Gating on `notifying` (cleared at every drop) left every
            link after the first one unanswered: the watch repeated its opener, the watchdog
            dropped it, over and over (André's Peak, 2026-10-25 17:19)."""
            self.PropertiesChanged(
                GATT_CHRC_IFACE,
                {"Value": dbus.Array([dbus.Byte(b) for b in payload], signature="y")},
                [])
            return True

    class Service(dbus_service.Object):
        def __init__(self, bus, index):
            self.path = BASE_PATH + "/service" + str(index)
            self.characteristics = []
            dbus_service.Object.__init__(self, bus, self.path)

        def properties(self):
            return {GATT_SERVICE_IFACE: {
                "UUID": SERVICE_UUID,
                "Primary": dbus.Boolean(True),
                "Characteristics": dbus.Array(
                    [dbus.ObjectPath(c.path) for c in self.characteristics],
                    signature="o"),
            }}

        @dbus_service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            if interface != GATT_SERVICE_IFACE:
                raise dbus.exceptions.DBusException(
                    "org.bluez.Error.InvalidArguments")
            return self.properties()[GATT_SERVICE_IFACE]

    class Agent(dbus_service.Object):
        """org.bluez.Agent1, registered with "KeyboardDisplay" capability.

        Real gap, found live 2026-08-11: a FRESH pairing needs *something* to answer
        BlueZ's passkey request, and this file registered no agent of its own - it depended
        entirely on whatever agent the desktop environment happened to already have running
        (Cinnamon's applet, here), invisible to this process and not guaranteed to exist on
        every machine.

        Capability is "KeyboardDisplay", not "NoInputNoOutput" - deliberately, and only
        after that guess failed live and a look at this project's own real history
        (HANDOFF.md, "2026-08-08: real Linux/BlueZ pairing session") explained why. Decoding
        the watch's real IO Capability fields from a real capture settled the SMP method
        unambiguously: watch = Display Only, requests MITM -> **LE Legacy Passkey Entry**
        (watch displays a 6-digit code, the CENTRAL types it in), not Just Works, not
        Numeric Comparison. A NoInputNoOutput agent forces a Just-Works downgrade the watch
        happens not to refuse in principle, but that same document also records a live,
        reproducible connect-then-disconnect failure mode around exactly this area
        (`le-connection-abort-by-local` and friends) - and this session's own NoInputNoOutput
        attempt reproduced a disconnect right after pairing, not just a theoretical risk.
        The one fresh pairing that document records actually reaching `Paired: yes, Bonded:
        yes` used a KeyboardDisplay agent with the real passkey typed in - the same real
        constraint Android hit and solved with its own `promptForPin()` UI (`setPin()` is
        the only public submission API there too).

        There is no OCR/camera reading the watch's screen here, so `RequestPasskey()` can't
        answer synchronously - it uses D-Bus async_callbacks and blocks on `submit_passkey()`
        being called from the control socket once a human (today: relayed through the
        conversation; later: a real UI field) reports what the watch is showing.
        """

        def __init__(self, bus, path):
            self.path = path
            self.pending_passkey_device = None
            self._passkey_value = None
            self._passkey_ready = threading.Event()
            self._cancel_reason = None
            dbus_service.Object.__init__(self, bus, self.path)

        def submit_passkey(self, passkey):
            """Called from the control socket thread once a human has read the code off the
            watch's screen. Returns False if nothing is actually pending."""
            if self.pending_passkey_device is None:
                return False
            self._passkey_value = int(passkey)
            self._passkey_ready.set()
            return True

        def cancel_pending(self, device, reason):
            """Wakes a RequestPasskey() wait early with a rejection instead of leaving it
            hung until its own 60s timeout - for when the device disconnects mid-pairing.

            Suunto's own app (jadx diff, 2026-08-21 - see ambit_app_ble_stability_suunto_app_diff
            memory) has a dedicated `BluetoothOperationWaitBonding` queue step that treats a
            GATT disconnect DURING bonding as an explicit, named failure rather than an
            ambiguous generic disconnect. Same idea here: `start_discovery()`'s own disconnect
            handler calls this instead of just letting the passkey wait time out on its own,
            once it sees `pending_passkey_device` match the device that just dropped."""
            if self.pending_passkey_device != device:
                return
            self._cancel_reason = reason
            self._passkey_value = None
            self._passkey_ready.set()

        @dbus_service.method(AGENT_IFACE, in_signature="", out_signature="")
        def Release(self):
            pass

        @dbus_service.method(AGENT_IFACE, in_signature="os", out_signature="")
        def AuthorizeService(self, device, uuid):
            print(f"  pairing: auto-authorizing service {uuid} for {device}")

        @dbus_service.method(AGENT_IFACE, in_signature="o", out_signature="s")
        def RequestPinCode(self, device):
            raise dbus.exceptions.DBusException(
                "org.bluez.Error.Rejected", "this watch family uses Passkey Entry, not a "
                "legacy PIN - see RequestPasskey")

        @dbus_service.method(AGENT_IFACE, in_signature="ouq", out_signature="")
        def DisplayPasskey(self, device, passkey, entered):
            # Would fire if the WATCH had to type a code WE display - not this watch
            # family's real direction (it is Display Only), kept only so an unexpected
            # negotiation doesn't hang silently.
            print(f"  pairing: WE are displaying passkey {passkey:06d} for {device} "
                  f"({entered} digits entered so far)")

        @dbus_service.method(AGENT_IFACE, in_signature="os", out_signature="")
        def DisplayPinCode(self, device, pincode):
            print(f"  pairing: WE are displaying PIN {pincode} for {device}")

        @dbus_service.method(AGENT_IFACE, in_signature="o", out_signature="u",
                             async_callbacks=("reply_handler", "error_handler"))
        def RequestPasskey(self, device, reply_handler, error_handler):
            self.pending_passkey_device = device
            self._passkey_value = None
            self._passkey_ready.clear()
            self._cancel_reason = None
            print(f"  pairing: watch {device} is showing a 6-digit passkey - read it off "
                  "the watch's screen and report it back (up to 60s)")

            def wait_for_passkey():
                if self._passkey_ready.wait(60.0) and self._cancel_reason is None:
                    reply_handler(self._passkey_value)
                else:
                    error_handler(dbus.exceptions.DBusException(
                        "org.bluez.Error.Rejected",
                        self._cancel_reason or "passkey not provided within 60s"))
                self.pending_passkey_device = None
                self._cancel_reason = None

            threading.Thread(target=wait_for_passkey, daemon=True).start()

        @dbus_service.method(AGENT_IFACE, in_signature="ou", out_signature="")
        def RequestConfirmation(self, device, passkey):
            print(f"  pairing: auto-confirming passkey {passkey:06d} for {device}")

        @dbus_service.method(AGENT_IFACE, in_signature="o", out_signature="")
        def RequestAuthorization(self, device):
            print(f"  pairing: auto-authorizing {device}")

        @dbus_service.method(AGENT_IFACE, in_signature="", out_signature="")
        def Cancel(self):
            print("  pairing: cancelled")

    return Application, Service, Characteristic, Agent, dbus, GLib


# The watch's sync notice: 0x1201 with flags 0x06 after the handshake, ending in the SBEM entry
# f6 01 <v>. It sends one every 30 minutes while linked and when "Sync now" is picked on a
# watch that is already linked. The Suunto app and the Android app (protocol_ble.c) answer each
# with an empty 0x1201, flags 0x09, same pktNum; unanswered, the watch repeats it every 5 s,
# three times. Before the handshake the same frame is the watch's opener (answered with 0x0000).
SYNC_NOTICE = 0x1201
SYNC_NOTICE_FL = 0x06
SYNC_NOTICE_ACK = 0x09

# A request whose reply comes back under another command number. Over Bluetooth the watch
# answers device info (0x0000) with 0x0002, the same payload as its hello (model, serial, fw,
# hw): André's Peak 2026-10-25, request pkt 11 answered by 0x0002 pkt 11, flags 0x0a.
REPLY_COMMAND = {0x0000: 0x0002}


class WatchRefused(RuntimeError):
    """The watch answered with errFlags set: it refused the command (protocol_ble.c returns an
    error for these too). Seen once in 1429 logged frames: the first POI write (0x0b25) right
    after a fresh pairing, which the watch did not keep."""


class ServerLink:
    """`write_nav.Link`'s public shape, driven from the SERVER end.

    Existing tools only ever call `link.command(command, payload)` and block for the reply, so
    presenting that same method here is what lets settings_write.py, custom_modes.py,
    kailash_history.py and the rest run over Bluetooth with no change at all.

    Two things differ from ble_link.py's client version and both come from the role flip:
    sending is a NOTIFICATION on d0fd6b80 rather than a GATT write, and there is no connect
    step - the watch arrives on its own, so `open()` is a wait rather than an action.
    """

    # Server-role driver flags. NOT ble_link.py's FLAGS_DEFAULT (0x0A) - that value was
    # reverse-engineered from the 7R app acting as a BLE CLIENT (HANDOFF.md Milestone 7's
    # "Outgoing flags" paragraph). We are the opposite role (server), and
    # protocol_ble.c's ble_send_command - the native code that actually got phone-driven
    # requests working post-handshake on real hardware, 2026-08-09 - uses 0x05 for every
    # driver-path request once the handshake below has completed. Reusing 0x0A here would
    # be the client's own value in the server's mouth.
    DRIVER_FLAGS = 0x05

    def __init__(self, notify_chrc, glib):
        self.notify_chrc = notify_chrc
        self._glib = glib
        self.dry_run = True            # matches Link: nothing writes unless asked
        self.verbose = False
        self.sequence = 0
        self.sent = []
        self._pkt_num = 0
        self._conn_id = 0x0009
        self._reply = None
        self._reply_ready = threading.Event()
        self._expecting = None
        self._pending_pkt = None
        # pktNums of requests that timed out: their reply, if it still comes, is not taken as
        # the answer to a later request (every logged reply echoes its request's pktNum).
        self._late_pkts = collections.deque(maxlen=32)
        # Frames waiting to go out, as 20-byte chunks. Only the GLib loop touches it, so a
        # sync-notice answer can never land in the middle of a command's chunks.
        self._tx = collections.deque()
        self._tx_busy = False
        self.sync_notices = 0
        self.last_sync_notice_at = None

        # Server-side bootstrap handshake (HANDOFF.md Milestone 7 items 9-10, ported
        # byte-for-byte from protocol_ble.c's libambit_ble_handshake_device_info() - the
        # fix that got Android's own connect+identify working on real hardware). Over BLE
        # the WATCH drives the opening exchange: it pushes 0x1201 first and won't send
        # anything else until we answer it with 0x0000; only then does it push 0x0002
        # (hello - model/serial/fw/hw, not a reply to a request). A naive "send 0x0000,
        # wait for a reply" - the USB pattern device_info.py already uses - just hangs:
        # confirmed the same way here, live, 2026-08-11, that Android's HANDOFF.md entry
        # already documented for the native code.
        self.handshake_done = threading.Event()
        self.device_info = {}

    def reset(self):
        """Called when the watch's ACL link drops without BlueZ ever calling StopNotify on
        our characteristic (a real, observed gap - see `listen()`'s own disconnect-signal
        handler) - without this, a stale `handshake_done`/`device_info` from the PREVIOUS
        connection would make the next one look already-identified when it isn't, and
        `command()` would try to drive requests into a connId/pktNum sequence the watch
        has no memory of.

        A command still waiting is woken at once and fails ("the watch disconnected"), instead
        of waiting out its timeout. Frames not yet sent are dropped: they belong to the old
        link. Runs on the GLib loop (the disconnect and subscribe handlers)."""
        self.handshake_done.clear()
        self.device_info = {}
        self._pkt_num = 0
        self._conn_id = 0x0009
        self._reply = None
        self._expecting = None
        self._pending_pkt = None
        self._late_pkts.clear()
        self._tx.clear()
        self._reply_ready.set()

    def on_message(self, message):
        """Called from the D-Bus thread for every decoded inbound frame. Before the
        handshake completes, incoming frames are the watch's own opener pushes, not replies
        to anything we sent - dispatched to _handle_handshake_frame() instead of the normal
        _expecting match."""
        if not self.handshake_done.is_set():
            self._handle_handshake_frame(message)
            return
        if message.command == SYNC_NOTICE and message.flags == SYNC_NOTICE_FL:
            self._answer_sync_notice(message)
            return
        if self._expecting is None:
            return
        if message.command != REPLY_COMMAND.get(self._expecting, self._expecting):
            return
        if message.pkt_num != self._pending_pkt:
            if message.pkt_num in self._late_pkts:
                print(f"  late reply 0x{message.command:04x} (packet {message.pkt_num}) to a "
                      "request that already timed out - ignored")
                return
            print(f"  reply 0x{message.command:04x} carries packet {message.pkt_num}, "
                  f"the request was {self._pending_pkt}")
        self._reply = message
        self._reply_ready.set()

    def _answer_sync_notice(self, message):
        """An empty 0x1201, flags 0x09, same pktNum: what the Suunto app and protocol_ble.c
        send back. Counted in `status` so the backend can start a sync later."""
        self.sync_notices += 1
        self.last_sync_notice_at = time.time()
        payload = message.payload
        value = payload[-1] if len(payload) >= 3 and payload[-3] == 0xF6 else None
        print(f"  watch sync notice (packet {message.pkt_num}, value {value}) - answered")
        self._send_raw(SYNC_NOTICE, flags=SYNC_NOTICE_ACK, conn_id=self._conn_id,
                       pkt_num=message.pkt_num, payload=b"")

    def _handle_handshake_frame(self, message):
        if message.command == 0x1201:
            # The watch's opener. Byte-exact against the working Suunto capture
            # (protocol_ble.c's own comment, itself sourced from assets/ble 2026-08-09/):
            # flags=0x01, connId=0x0000, pktNum=0x0000, payload 03 00 00 00. Answering
            # this is what makes the watch advance to sending its 0x0002 hello - without
            # it the watch just re-sends 0x1201 every ~5s.
            self._send_raw(0x0000, flags=0x01, conn_id=0x0000, pkt_num=0x0000,
                           payload=b"\x03\x00\x00\x00")
            return
        if message.command == 0x0002:
            # hello: [model 16][id 16][fw 4][hw 4]... - same offsets
            # libambit_ble_handshake_device_info() reads, ported directly rather than
            # re-derived. fw is a plain 3-byte major.minor.patch (HANDOFF.md's own worked
            # example: 02 04 11 = "2.4.17"); hw mirrors the USB device_info reply's
            # 16-bit-third-component convention (device_info.py's read_device_info(), and
            # protocol_ble.c's own comment says this explicitly - "mirroring the USB
            # device-info reply layout").
            payload = message.payload
            info = {}
            if len(payload) >= 16:
                info["model"] = payload[0:16].split(b"\0")[0].decode("utf-8", "replace")
            if len(payload) >= 32:
                info["serial"] = payload[16:32].split(b"\0")[0].decode("utf-8", "replace")
            if len(payload) >= 36:
                fw = payload[32:36]
                info["fw_version"] = f"{fw[0]}.{fw[1]}.{fw[2]}"
            if len(payload) >= 40:
                hw = payload[36:40]
                info["hw_version"] = f"{hw[0]}.{hw[1]}.{hw[2] | (hw[3] << 8)}"
            self.device_info = info
            # The watch's own connId (9 on every logged frame); protocol_ble.c learns it the
            # same way.
            if message.conn_id:
                self._conn_id = message.conn_id
            # Driver-path commands (getLogs etc.) continue the pktNum sequence from here -
            # protocol_ble.c: "device_info was the watch's pkt 0 exchange... the phone's
            # first real request is pkt 1."
            self._pkt_num = 1
            self.handshake_done.set()
            return
        # any other early push (a retried 0x1201, etc.) is ignored, same as the native code

    # Real bug, found live 2026-08-11 on the first large write this project has ever sent
    # AS THE BLE SERVER (a ~1024-byte-per-command route write - every command proven
    # working before this was a handful of bytes, one ATT fragment). notifyCharacteristicChanged
    # (here, BlueZ's PropertiesChanged signal) has no built-in flow control - emitting it
    # does not wait for the actual over-the-air transmission to complete, so blasting every
    # 20-byte fragment of a large payload back-to-back in one loop can outrun what the LE
    # link layer can actually deliver in a connection interval and silently drop fragments,
    # with no CRC failure to show for it (the corruption is in what the watch received, not
    # in the small reply we get back - which is why settings writes, one ATT fragment each,
    # never surfaced this). Confirmed as the cause: a real route write reported success
    # (all replies came back, CRC ok) but the route never appeared on the watch. Paced with
    # a real per-fragment delay instead of a tight loop.
    NOTIFY_PACING_MS = 15

    def _send(self, frame):
        """Notifications must be emitted on the GLib main loop thread - D-Bus signals are not
        safe to raise from another one - so the send is handed to the loop and this returns
        immediately. command() then blocks on the reply event instead. Paced via chained
        `timeout_add` calls rather than a blocking `time.sleep()` loop, so the mainloop stays
        free to service other D-Bus traffic (like the watch's own concurrent pushes) between
        fragments.

        Frames go through one queue, whole: a sync-notice answer sent while a command's chunks
        are still going out waits for them. (Two sends used to run side by side and could mix
        their chunks.) `timeout_add(0)`, not `idle_add`: an idle source can starve on this
        noisy loop (see the "forget" op).
        """
        chunks = [frame[i:i + CHUNK_SIZE] for i in range(0, len(frame), CHUNK_SIZE)]
        self._glib.timeout_add(0, self._enqueue, chunks)

    def _enqueue(self, chunks):
        self._tx.extend(chunks)
        if not self._tx_busy:
            self._tx_busy = True
            self._emit_next()
        return False                   # one-shot

    def _emit_next(self):
        if not self._tx:
            self._tx_busy = False
            return False
        self.notify_chrc.notify(self._tx.popleft())
        if self._tx:
            self._glib.timeout_add(self.NOTIFY_PACING_MS, self._emit_next)
        else:
            self._tx_busy = False
        return False                   # one-shot; the next chunk is its own timeout

    def _send_raw(self, command, flags, conn_id, pkt_num, payload):
        """Like _send(), but with explicit header fields instead of self._conn_id/
        self._pkt_num - the handshake's 0x0000 answer uses fixed values (flags=0x01,
        connId=0, pktNum=0) that don't fit the driver-path bookkeeping command() does."""
        frame = encode_nsp_frame(command, payload, flags, conn_id, pkt_num)
        self._send(frame)

    def command(self, command, payload=b"", expect_reply=True, quiet=False, flags=None,
                timeout=20.0):
        if not self.handshake_done.is_set():
            raise RuntimeError(
                "handshake not complete yet - the watch hasn't pushed its 0x1201/0x0002 "
                "opener, so there's no connId/pktNum sequence to send a driver-path "
                "request into. Wait for ServerLink.handshake_done before calling command().")
        name = CMD_NAMES.get(command, f"0x{command:04x}")
        pkt = self._pkt_num & 0xFFFF   # a u16 on the wire; a long session must not overflow it
        frame = encode_nsp_frame(command, payload,
                                 self.DRIVER_FLAGS if flags is None else flags,
                                 self._conn_id, pkt)
        if not quiet:
            print(f"  {'[dry-run] ' if self.dry_run else ''}-> 0x{command:04x} "
                  f"{name:22} {len(payload):5} B  {len(frame)} raw byte(s)")
        self.sent.append((command, payload, frame))
        self._pkt_num = (pkt + 1) & 0xFFFF
        self.sequence += 1
        if self.dry_run or not expect_reply:
            return b""
        self._reply = None
        self._reply_ready.clear()
        self._pending_pkt = pkt
        self._expecting = command
        self._send(frame)
        got = self._reply_ready.wait(timeout)
        reply = self._reply
        self._expecting = None
        self._pending_pkt = None
        if not got:
            self._late_pkts.append(pkt)
            raise TimeoutError(f"no reply to 0x{command:04x} within {timeout}s")
        if reply is None:
            raise RuntimeError(f"the watch disconnected before answering 0x{command:04x}")
        if reply.err_flags:
            raise WatchRefused(f"the watch refused 0x{command:04x} {name} "
                               f"(error flags 0x{reply.err_flags:02x})")
        return reply.payload


def handle_rx(assembler, link, chunk, verbose=False):
    """One chunk the watch wrote: logs the frames it completes and hands each to the link. A
    frame whose CRC is wrong or missing is dropped, as protocol_ble.c does: its bytes are not
    the watch's, and a reply made of them would be taken as the answer."""
    if verbose:
        print(f"  RX {len(chunk):3d} B  {chunk.hex()}")
    for message in assembler.feed(chunk):
        name = CMD_NAMES.get(message.command, f"0x{message.command:04x}")
        crc = ("crc ok" if message.crc_ok else
               "CRC MISMATCH" if message.crc_ok is False else "no crc")
        err = f"  error flags 0x{message.err_flags:02x}" if message.err_flags else ""
        print(f"  <- 0x{message.command:04x} {name:22} "
              f"{len(message.payload):5} B  [{crc}]{err}")
        if verbose and message.payload:
            print(f"        {message.payload[:64].hex(' ')}"
                  f"{' ...' if len(message.payload) > 64 else ''}")
        if message.crc_ok is not True:
            print("        dropped: damaged frame")
            continue
        link.on_message(message)


# ambit3_get_compact_serial (ambit_pcap.py's own name for it) - the clean numeric serial
# ("XXXXXXXXXX") the handshake's 0x0002 hello does NOT carry (it only has the hello's own
# raw id string, e.g. "8A153C5111000900" - see ServerLink._handle_handshake_frame's own
# comment). Found by decoding the real working Suunto app's own capture directly,
# 2026-08-11 (assets/ble 2026-08-09/btsnoop_suuntoapp_2026-08-09.log, via tshark + this
# file's own SLIP/frame logic - same method as ble_pklg.py, just against a btsnoop capture
# instead of a .pklg one): the app's first real post-handshake driver-path request is
# exactly this command, flags=0x05, pktNum=1 (matching DRIVER_FLAGS and the handshake's own
# pkt_num=1 reset), and the watch's reply is byte-for-byte the connect+identify flow's
# missing piece.
CMD_GET_COMPACT_SERIAL = 0x0B1E
# The real request payload from that capture, copied verbatim rather than guessed - its
# individual bytes look like a nonce/challenge (not all-zero, not obviously derivable) and
# the capture is the only evidence of what the watch actually accepts, so byte-exact reuse
# is safer than inventing a "simplified" version that might not be. Semantics unconfirmed;
# reuse is deliberate, not lazy - see this project's own "known formulas over derivation"
# preference applied to a captured value instead of a textbook one.
COMPACT_SERIAL_REQUEST = bytes.fromhex("00000000f90858ee017e00000892c98538")


def parse_compact_serial(payload):
    """Byte 8 is some flag (0x01 in the real capture, meaning unconfirmed); the serial
    itself is a null-terminated ASCII string starting at byte 9 - confirmed decoding
    "XXXXXXXXXX" out of the real capture's reply, which matches this project's own
    independently-recorded real serial for the same watch (HANDOFF.md)."""
    if len(payload) < 10:
        return None
    serial = payload[9:].split(b"\x00", 1)[0].decode("ascii", "replace")
    return serial or None


def find_adapter(bus, dbus):
    """The first adapter that can host our GATT service."""
    manager = dbus.Interface(bus.get_object(BLUEZ, "/"), DBUS_OM_IFACE)
    for path, ifaces in manager.GetManagedObjects().items():
        if GATT_MANAGER_IFACE in ifaces:
            return path
    return None


def forget_suunto_bonds(bus, dbus, adapter):
    """Drop every existing Suunto bond before scanning.

    André, 2026-08-11: "just unpair every suunto ambit, kailash, traverse before running.
    It's not like people have plenty." A stale bond is not harmless here - a watch already
    bonded to this host has no reason to advertise, so it sits invisible while we wait for a
    solicitation it will never send, and "Pair Mobile App" has nothing to do. Clearing it
    first makes every run start from the same known state.

    Scoped to Suunto by OUI and name, so no unrelated device the user owns is touched. This
    mirrors what the Android side already does deliberately (forget the bond on every
    rebuild) rather than being a new idea.
    """
    manager = dbus.Interface(bus.get_object(BLUEZ, "/"), DBUS_OM_IFACE)
    adapter_obj = dbus.Interface(bus.get_object(BLUEZ, adapter), ADAPTER_IFACE)
    removed = 0
    for path, ifaces in manager.GetManagedObjects().items():
        device = ifaces.get("org.bluez.Device1")
        if not device:
            continue
        address = str(device.get("Address", "")).upper()
        name = str(device.get("Name", device.get("Alias", "")) or "")
        ours = address.startswith(SUUNTO_OUI) or any(
            name.startswith(prefix) for prefix in NAME_PREFIXES)
        if not ours or not device.get("Paired", False):
            continue
        try:
            adapter_obj.RemoveDevice(path)
            print(f"  unpaired {name or address} - it can advertise again now")
            removed += 1
        except Exception as exc:
            print(f"  could not unpair {name or address}: {exc}")
    if removed == 0:
        print("  no existing Suunto bonds to clear")
    return removed


# Non-blocking pause between StopDiscovery() and Connect(), scheduled on the GLib main
# loop rather than a blocking time.sleep() - the mainloop thread must never block, same
# constraint _send()'s own chained timeout_add pacing already documents. Suunto's own app
# (jadx diff, 2026-08-21 - ambit_app_ble_stability_suunto_app_diff memory) added a generic
# `QueueOperationDelay` primitive between 4.73.6 and 6.12.7 for exactly this kind of
# empirically-needed settle time around BLE calls; this is that same idea applied to the one
# place in this file two radio operations (scan-stop, connect) happen back-to-back.
CONNECT_SETTLE_DELAY_MS = 300

# Minimum time between successive Connect() attempts to the SAME device path. Without this,
# a device that fails/drops repeatedly in a noisy BLE neighborhood (this file's own
# `consider()` log line already documents dozens of nearby devices on a real desk) gets
# re-dialed on every subsequent RSSI/property update - which on some adapters arrive several
# times a second - turning one flaky link into a tight connect-fail-retry loop that never
# lets the radio settle. Mirrors the spirit of Suunto's own retry primitives without copying
# their exact backoff curve (unknown/undocumented) - a flat floor is enough here since the
# real failure mode observed is "retried too fast", not "gave up too early".
CONNECT_RETRY_BACKOFF_S = 3.0

# A link the watch made that starts no session. On the tablet a good link starts its session
# within ~1.3 s; one that has started nothing after 6 s never will, and dropping it makes the
# watch call again (AmbitBleModule.kt EMPTY_LINK_MS, btsnoop 2026-10-25). A little longer here
# until the desktop's own timing is measured (the log is timestamped now). The wait starts
# again after a passkey prompt: a fresh pairing subscribes only once it is done.
EMPTY_LINK_S = 8.0
EMPTY_LINK_CHECK_MS = 1000

# The scan runs in bursts, and not at all while a watch is linked or being dialed. A scan that
# never stopped kept André's Bluetooth mouse from reconnecting (2026-10-25: it came back a
# minute after the daemon stopped). The tablet listens far less (30 ms in every 1280 ms) and
# still catches the watch, which keeps calling for longer than one period on "Sync now" or
# "Pair with app".
SCAN_ON_S = 3
SCAN_PERIOD_S = 10

# A paired watch that drops this soon after connecting, before subscribing, refused this
# computer's key: BlueZ disconnects ~86 ms in when the watch answers its encryption with "PIN
# or Key Missing" (btmon, 2026-08-21). Same loop when the computer forgot the watch but the watch
# kept its key. BlueZ 5.72 gives no disconnect reason over D-Bus, so it is told by the timing,
# after a few in a row, and `status` says what to do instead of looping in silence.
QUICK_DROP_S = 2.0
QUICK_DROPS_FOR_HINT = 3
HINT_PAIR_AGAIN = ("The watch no longer accepts this computer's pairing. On the watch: "
                   "Bluetooth > Unpair > Forget, then Pair > Pair with app, and type in "
                   "Sommet the code it shows.")
HINT_PAIR_AGAIN_BOTH = ("The watch no longer accepts this computer's pairing. On the watch: "
                        "Bluetooth > Unpair > Forget. In Sommet: \"Forget Bluetooth device\" "
                        "on this watch. Then on the watch: Pair > Pair with app, and type in "
                        "Sommet the code it shows.")

# Once the hint is up, the watch is dialed at most every REFUSED_RETRY_S. Every try shows
# "connected" then "disconnected" on its screen: on 2026-10-25 a Peak that had stopped
# accepting this computer's key was dialed every 10 s for 8 minutes, 45 times. Forgetting the
# device (InterfacesRemoved) ends the wait, so a fresh pairing starts at once.
REFUSED_RETRY_S = 300.0

# BlueZ 5.72 on this kernel (7.0) can list a lost link as connected for good. The kernel now
# answers a Disconnect for a link it no longer has with "Disconnected (0x0e)"; bluetoothd only
# knows the older "Not Connected" answer, logs "Failed to disconnect device: Disconnected
# (0x0e)", never marks the device disconnected, and never replies to the D-Bus call. Asking
# again gets the same answer (2026-10-25, 19:06 and 21:41). Connect() then has nothing to do,
# so the watch can't link until Bluetooth is turned off and on, the device is forgotten, or
# the computer restarts. The daemon checks DROP_CHECK_TIMEOUT_S after each drop it asks for.
DROP_CHECK_TIMEOUT_S = 10
HINT_BLUEZ_STUCK = ("This computer's Bluetooth lost track of the watch: it still lists it as "
                    "connected. Turn Bluetooth off and on (or restart the computer), then "
                    "press Sync now on the watch.")


def retry_floor(state):
    """Seconds between two dials of the same watch (see REFUSED_RETRY_S)."""
    return REFUSED_RETRY_S if state.pairing_hint else CONNECT_RETRY_BACKOFF_S


class LinkState:
    """The watch's link as the daemon sees it, for the watchdog and for `status`. Only the GLib
    loop writes it."""

    def __init__(self):
        self.path = None               # the watch linked now, or None
        self.connected_at = None       # time.monotonic() of that link
        self.quiet_since = None        # the watchdog's clock; restarts after a passkey prompt
        self.subscribed = False        # this link started a session (the handshake completed)
        self.quick_drops = 0
        self.pairing_hint = None
        self.last_drop = None
        self.stuck = None              # a watch BlueZ lists as connected after losing it

    def status(self):
        return {"linked_device": self.path.split("/")[-1] if self.path else None,
                "pairing_hint": HINT_BLUEZ_STUCK if self.stuck else self.pairing_hint,
                "bluez_stuck": self.stuck.split("/")[-1] if self.stuck else None,
                "quick_drops": self.quick_drops, "last_drop": self.last_drop}

    def lost_by_bluez(self, path, paired):
        """BlueZ still lists `path` as connected after a drop we asked for (HINT_BLUEZ_STUCK).
        The link is over: recorded as a drop, and the watch waits until BlueZ lets it go."""
        self.dropped(path, paired)
        self.stuck = path

    def released(self, path):
        """BlueZ reported `path` disconnected. True if it was the stuck one."""
        if self.stuck != path:
            return False
        self.stuck = None
        return True

    def forgotten(self, path):
        """`path` was removed from BlueZ (forgotten in Sommet, or a stale cache entry). A new
        pairing starts clean: no hint, no long wait, nothing stuck."""
        if self.stuck == path:
            self.stuck = None
        if self.last_drop and self.last_drop["device"] == path.split("/")[-1]:
            self.quick_drops = 0
            self.pairing_hint = None

    def linked(self, path):
        self.path = path
        self.connected_at = self.quiet_since = time.monotonic()
        self.subscribed = False

    def on_session(self):
        self.subscribed = True
        self.quick_drops = 0
        self.pairing_hint = None

    def dropped(self, path, paired):
        """Records a drop; returns the hint when this one makes the pattern."""
        if path != self.path:
            return None
        after = time.monotonic() - self.connected_at
        self.last_drop = {"device": path.split("/")[-1], "after_s": round(after, 2),
                          "subscribed": self.subscribed, "at": time.strftime("%H:%M:%S")}
        quick = after < QUICK_DROP_S and not self.subscribed
        self.path = self.connected_at = self.quiet_since = None
        if not quick:
            if self.subscribed:
                self.quick_drops = 0
            return None
        self.quick_drops += 1
        if self.quick_drops >= QUICK_DROPS_FOR_HINT:
            self.pairing_hint = HINT_PAIR_AGAIN_BOTH if paired else HINT_PAIR_AGAIN
            return self.pairing_hint
        return None


def start_discovery(bus, dbus, glib, adapter, verbose, on_disconnect=None, agent=None,
                    state=None, session_started=lambda: False):
    """Scan, and connect to anything soliciting the NSP service.

    Deliberately an unfiltered scan. The Kotlin module hit exactly this on real hardware: the
    Kailash only SOLICITS the UUID, so a service-UUID scan filter matched nothing at all
    ("scan registered, zero results"). Every device is examined here and judged on its own
    UUID list instead.

    `on_disconnect`, if given, is called (with the device path) whenever a device already in
    `seen` reports Connected -> False. Real bug, found live 2026-08-11: `seen` exists so a
    device already being connected-to isn't redundantly dialed again mid-attempt - but with
    nothing ever removing a path from it, one failed/dropped connection permanently
    blacklisted the watch for the rest of the process's life, even though the watch kept
    re-advertising and the user kept retrying "Sync now" on it. Every real connect attempt
    this session that didn't complete on the first try needed this to recover.

    `agent`, if given, is consulted on every disconnect: if the dropped device is the one the
    pairing Agent is currently waiting on a passkey for, the wait is cancelled immediately
    (see `Agent.cancel_pending`'s own docstring) rather than left to its own 60s timeout.

    `state` (a LinkState) follows the linked watch: the empty-link watchdog (EMPTY_LINK_S)
    drops a link that starts no session, only one watch is dialed while another is linked,
    and a run of quick drops sets the "pair again" hint. `session_started` says whether the
    watch's handshake has completed on this link (a paired watch never subscribes again on a
    reconnect, so subscribing is not the sign).
    """
    adapter_obj = dbus.Interface(bus.get_object(BLUEZ, adapter), ADAPTER_IFACE)
    seen = set()
    logged_others = set()   # foreign devices already logged once (they advertise many times a second)
    last_attempt_at = {}
    state = state if state is not None else LinkState()
    retry_now = set()                  # paths the watchdog dropped: no backoff on their next call
    waiting_on = set()                 # "another watch is linked" already logged for these

    def short(path):
        return path.split("/")[-1]

    def device_prop(path, name, default=None):
        try:
            props = dbus.Interface(bus.get_object(BLUEZ, path), DBUS_PROP_IFACE)
            return props.Get("org.bluez.Device1", name)
        except Exception:
            return default             # the device went away; nothing to read

    def watch_empty_link(path):
        """The empty-link watchdog (see EMPTY_LINK_S). Checks once a second while `path` is
        the linked watch; drops the link once it has started no session for EMPTY_LINK_S."""
        if state.path != path or state.subscribed:
            return False               # gone, or the session started
        if session_started():
            state.on_session()
            return False
        now = time.monotonic()
        if agent is not None and agent.pending_passkey_device == path:
            state.quiet_since = now    # a passkey prompt is up: wait for it, then start over
            return True
        if now - state.quiet_since < EMPTY_LINK_S:
            return True                # keep checking
        print(f"  the watch linked but started no session in {EMPTY_LINK_S:.0f} s "
              f"({short(path)}) - dropping the link so it can call again")
        retry_now.add(path)
        drop_link(path)
        return False

    def drop_link(path):
        """Disconnects `path`, then checks BlueZ let it go (see HINT_BLUEZ_STUCK). If it
        still lists the watch as connected, the link is over for us anyway: the session is
        reset, the scan resumes, and the watch is not dialed until BlueZ releases it."""
        def check(error=None):
            if error is not None:
                print(f"  could not drop the link: {error}")
            if state.path != path or not device_prop(path, "Connected", False):
                return                 # gone: on_connection_state has handled it
            print(f"  BlueZ still lists {short(path)} as connected: {HINT_BLUEZ_STUCK}")
            state.lost_by_bluez(path, bool(device_prop(path, "Paired", False)))
            seen.discard(path)
            resume_discovery()
            if on_disconnect:
                on_disconnect(path)
        try:
            dbus.Interface(bus.get_object(BLUEZ, path), "org.bluez.Device1").Disconnect(
                reply_handler=check, error_handler=check, timeout=DROP_CHECK_TIMEOUT_S)
        except Exception as exc:
            check(exc)

    def linked(path):
        if state.path == path:
            return
        state.linked(path)
        glib.timeout_add(EMPTY_LINK_CHECK_MS, watch_empty_link, path)

    dialing = set()                    # Connect() calls still in flight
    scanning = [False]

    def scan_start():
        """One burst (SCAN_ON_S), skipped while a watch is linked or being dialed."""
        if state.path is not None or dialing:
            return
        try:
            adapter_obj.StartDiscovery()
        except Exception:
            pass                       # already running is fine
        scanning[0] = True
        glib.timeout_add_seconds(SCAN_ON_S, scan_stop)

    def scan_stop():
        """Stop our scan. `scanning` stays True until BlueZ confirms, so scan_tick retries:
        a StopDiscovery refused mid-connect used to be taken as done, and the scan then ran
        for as long as the watch stayed linked (2026-10-28, 19:32 on; a Bluetooth mouse
        couldn't reconnect meanwhile)."""
        if scanning[0]:
            try:
                adapter_obj.StopDiscovery()
                scanning[0] = False
            except Exception as exc:
                if "No discovery started" in str(exc) or "NotReady" in str(exc):
                    scanning[0] = False    # nothing of ours to stop
                else:
                    print(f"  could not stop the scan yet ({exc}) - retrying")
        return False                   # one-shot

    def scan_tick():
        if scanning[0] and (state.path is not None or dialing):
            scan_stop()                # a stop that failed earlier: try again
        scan_start()
        return True                    # every SCAN_PERIOD_S

    def resume_discovery():
        # consider() stops discovery before connecting (an active scan and a connect fight
        # for the same radio - see its own comment) and nothing was resuming it afterward -
        # real bug, found live 2026-08-11: on a dropped or failed connection, this device
        # was still un-blacklisted from `seen` above, but discovery itself stayed off, so no
        # new advertisement could ever be seen to retry with. A burst now, at once: after a
        # drop the watch usually calls again within seconds.
        scan_start()

    def on_connection_state(interface, changed, invalidated, path=None):
        if interface != "org.bluez.Device1" or not path or "Connected" not in changed:
            return
        if not bool(changed["Connected"]) and state.released(path):
            print(f"  BlueZ let go of {short(path)} - it can link again")
            resume_discovery()
            return
        if path not in seen:
            return
        dialing.discard(path)
        if bool(changed["Connected"]):
            linked(path)
            return
        was_linked = state.path == path
        hint = state.dropped(path, bool(device_prop(path, "Paired", False)))
        if was_linked and state.last_drop:
            print(f"  the link lasted {state.last_drop['after_s']} s"
                  f"{'' if state.last_drop['subscribed'] else ', no session started'}")
        if agent is not None and agent.pending_passkey_device == path:
            # Distinct, expected failure - not a generic drop. Suunto's app treats a
            # disconnect mid-bonding as its own named case (BluetoothOperationWaitBonding)
            # rather than routing it through the same path as every other disconnect; doing
            # the same here also means the RequestPasskey() thread doesn't sit hung for up
            # to 60s waiting on a passkey nobody can submit any more.
            agent.cancel_pending(path, "device disconnected while a passkey was pending")
            print(f"  watch disconnected mid-pairing ({path.split('/')[-1]}) - cancelled "
                  "the pending passkey wait, will retry on the next advertisement")
        else:
            print(f"  watch disconnected ({path.split('/')[-1]}) - will retry on the next "
                  "advertisement/Sync now")
        if hint:
            print(f"  {state.quick_drops} links in a row dropped before any session: {hint} "
                  f"Calling it again in {REFUSED_RETRY_S / 60:.0f} min.")
        seen.discard(path)
        resume_discovery()
        if on_disconnect:
            on_disconnect(path)

    bus.add_signal_receiver(
        on_connection_state, dbus_interface=DBUS_PROP_IFACE, signal_name="PropertiesChanged",
        arg0="org.bluez.Device1", path_keyword="path")

    def consider(path, ifaces):
        device = ifaces.get("org.bluez.Device1")
        if not device or path in seen:
            return
        uuids = [str(u).lower() for u in device.get("UUIDs", [])]
        name = str(device.get("Name", device.get("Alias", "")) or "")
        solicits = SERVICE_UUID.lower() in uuids
        named = any(name.startswith(prefix) for prefix in NAME_PREFIXES)
        addressed = str(device.get("Address", "")).upper().startswith(SUUNTO_OUI)
        # Real bug, found live 2026-08-11: a genuinely different real Suunto product
        # ("Suunto Smart Sensor" - presumably a heart-rate strap, not this watch family)
        # ALSO solicits this exact same NSP service UUID - apparently a shared BLE
        # framework across Suunto's whole product line, not unique to Ambit3/Traverse/
        # Kailash. Every real target device already advertises a matching name too
        # (Kailash's own "Suunto NSP" is in NAME_PREFIXES for exactly this reason), so a
        # device that HAS a readable name and it does not match ours is confidently NOT
        # our target, regardless of solicits/OUI - the daemon connected to the sensor
        # instead of the watch and sat there forever waiting for it to subscribe (it
        # never would). Only a device with NO advertised name at all still falls back to
        # solicits/OUI - the one case a name-only check can't rule anything out for.
        if name and not named:
            if verbose and path not in logged_others:
                logged_others.add(path)
                print(f"  seen {path.split('/')[-1]} {name} (named, not a Suunto watch - ignoring)")
            return
        if not solicits and not named and not addressed:
            if verbose and path not in logged_others:
                logged_others.add(path)
                print(f"  seen {path.split('/')[-1]} {name or '(no name)'}")
            return
        # Backoff floor - see CONNECT_RETRY_BACKOFF_S's own comment. Checked, not just
        # relied on via `seen`: a failed/dropped connection removes the path from `seen`
        # specifically so a later advertisement can retry it, and property-change signals
        # (RSSI etc.) can arrive many times a second, so `seen` alone doesn't throttle
        # anything here.
        if path == state.stuck:
            return                     # BlueZ still lists it as connected (HINT_BLUEZ_STUCK)
        since_last = time.monotonic() - last_attempt_at.get(path, 0.0)
        if since_last < retry_floor(state) and path not in retry_now:
            return
        # One watch at a time: every command, the handshake and the notices go through one
        # ServerLink, so a second watch linking in would mix two sessions. It waits until the
        # linked one drops.
        if state.path is not None and state.path != path:
            if path not in waiting_on:
                waiting_on.add(path)
                print(f"  {name or short(path)} is calling - waiting: another watch "
                      f"({short(state.path)}) is linked")
            return
        waiting_on.discard(path)
        retry_now.discard(path)
        seen.add(path)
        why = "NSP uuid" if solicits else ("name match" if named else "Suunto address")
        print(f"  watch found: {name or '(no name)'} [{why}] - connecting "
              f"(we host the service, it connects in)")
        # Discovery MUST stop before connecting. An active scan and a connection attempt
        # fight for the same radio, and BlueZ resolves it by aborting the connection -
        # "le-connection-abort-by-local", which reads like a refusal by the watch and is
        # really our own scan getting in the way.
        scanning[0] = True             # stop it even mid-burst, or if another path started it
        scan_stop()
        # If the watch already has a link to us there is nothing to initiate - it connects in
        # on its own, and our outbound attempt is at best redundant ("Operation already in
        # progress") and at worst the thing that kills the session
        # ("le-connection-abort-by-local"). Both were seen for real, 2026-08-11.
        if device.get("Connected", False):
            print("  already connected - waiting for it to subscribe")
            linked(path)
            return

        # Asynchronously, with handlers. A blocking Connect() times out on D-Bus's own
        # 25s default long before BlueZ finishes connecting and pairing - the first run
        # failed exactly that way ("Did not receive a reply"), which looked like a refusal
        # and was really just impatience.
        def on_connect_failed(exc):
            print(f"  connect failed: {exc}")
            # Same un-blacklist/resume-scanning need as a later disconnect - a failed
            # Connect() (le-connection-abort-by-local and friends are common and often
            # transient, per this project's own real history with this watch family) must
            # not permanently rule the device out of future attempts either.
            dialing.discard(path)
            seen.discard(path)
            resume_discovery()

        def on_connected():
            dialing.discard(path)
            print("  connected - waiting for it to subscribe")

        def do_connect():
            last_attempt_at[path] = time.monotonic()
            dbus.Interface(bus.get_object(BLUEZ, path), "org.bluez.Device1").Connect(
                reply_handler=on_connected,
                error_handler=on_connect_failed,
                timeout=60)
            return False               # one-shot

        dialing.add(path)

        # Scheduled after a short settle delay rather than fired immediately - see
        # CONNECT_SETTLE_DELAY_MS's own comment. Must go through the main loop (timeout_add),
        # never a blocking sleep here: this function runs on the GLib main loop thread, and
        # blocking it stalls all other D-Bus traffic (the same reasoning _send()'s pacing
        # comment already documents for notifications).
        glib.timeout_add(CONNECT_SETTLE_DELAY_MS, do_connect)

    bus.add_signal_receiver(
        consider, dbus_interface=DBUS_OM_IFACE, signal_name="InterfacesAdded")

    def on_removed(path, interfaces):
        # Forgotten in Sommet (RemoveDevice) before a fresh pairing: no long wait for it.
        if "org.bluez.Device1" in interfaces:
            # A watch that linked without pairing is a temporary device: when its link ends,
            # BlueZ deletes it without a Connected=false first (seen 2026-10-26 14:21, after
            # a fresh connect with no pairing). Treat that as the disconnect it is, or the
            # daemon keeps thinking it is linked and never scans again.
            if state.path == path and path in seen:
                on_connection_state("org.bluez.Device1", {"Connected": False}, [], path=path)
            last_attempt_at.pop(path, None)
            seen.discard(path)
            state.forgotten(path)

    bus.add_signal_receiver(
        on_removed, dbus_interface=DBUS_OM_IFACE, signal_name="InterfacesRemoved")

    # InterfacesAdded fires only for a device BlueZ has NEVER seen. A watch it already
    # cached - which is any watch used once before - re-appears as a PROPERTY UPDATE on the
    # existing object instead, so a listener on InterfacesAdded alone goes deaf to exactly
    # the devices most likely to be ours. Real miss, 2026-08-11: an earlier run found
    # "Ambit3 XXXXXXXXXX" straight from the cache, then a later run with a freshness check
    # saw nine other devices and never the watch, because by then it was cached and only
    # ever emitted RSSI updates. Both signals are needed.
    def on_props(interface, changed, invalidated, path=None):
        if interface != "org.bluez.Device1" or not path:
            return
        if "RSSI" not in changed and "UUIDs" not in changed and "ServiceData" not in changed:
            return
        try:
            props = dbus.Interface(bus.get_object(BLUEZ, path), DBUS_PROP_IFACE)
            consider(path, {"org.bluez.Device1": props.GetAll("org.bluez.Device1")})
        except Exception:
            pass                       # device vanished mid-scan; nothing to do

    bus.add_signal_receiver(
        on_props, dbus_interface=DBUS_PROP_IFACE, signal_name="PropertiesChanged",
        arg0="org.bluez.Device1", path_keyword="path")

    # Devices BlueZ already knows about never fire InterfacesAdded again - a watch paired
    # earlier would otherwise be invisible to this scan. But a REMEMBERED device is not
    # necessarily a present one: BlueZ caches it long after it stopped advertising, and
    # connecting to a watch that is not currently soliciting just fails. Only cached devices
    # BlueZ still reports as in range are considered.
    manager = dbus.Interface(bus.get_object(BLUEZ, "/"), DBUS_OM_IFACE)
    for path, ifaces in manager.GetManagedObjects().items():
        device = ifaces.get("org.bluez.Device1") or {}
        # A PAIRED device is the exception, and it turned out to be the important one. A
        # watch already bonded to this host does not have to advertise for us to reach it -
        # BlueZ can connect to a known device by address. The freshness check was written to
        # skip stale cache entries and ended up discarding exactly the device most likely to
        # be ours: real miss, 2026-08-11 - the Ambit3 sat there paired=True, RSSI=None
        # through several attempts while we waited for an advertisement it had no reason to
        # send.
        if (device.get("RSSI") is None and not device.get("Connected", False)
                and not device.get("Paired", False)):
            continue                   # remembered, not paired, not advertising
        consider(path, ifaces)

    try:
        # The filter is NOT optional, and its absence is the likeliest reason a scan sees
        # everything except the device you want. With no filter BlueZ applies its own
        # defaults - which can exclude LE-only advertisers and de-duplicate repeats - so a
        # watch that btmon shows advertising never reaches a D-Bus client at all. Real miss,
        # 2026-08-11: btmon captured the Ambit3 advertising the NSP UUID (AD type 0x07) from
        # 0C:8C:DC:XX:XX:XX while this scan, running at the same time, reported nothing.
        #
        # Transport "le" asks for LE advertisements explicitly; DuplicateData keeps repeat
        # adverts coming (a watch advertises the same payload over and over, and without this
        # only the first is delivered); the RSSI floor is deliberately at the limit so nothing
        # is dropped for being faint.
        adapter_obj.SetDiscoveryFilter({
            "Transport": "le",
            "DuplicateData": dbus.Boolean(True),
            "RSSI": dbus.Int16(-127),
        })
        print(f"  scanning for the watch in bursts ({SCAN_ON_S} s every {SCAN_PERIOD_S} s, "
              "LE transport, duplicates on), none while a watch is linked")
    except Exception as exc:
        print(f"  could not set the discovery filter: {exc}")
    scan_start()
    glib.timeout_add_seconds(SCAN_PERIOD_S, scan_tick)


def listen(verbose=False, timeout=0, forget=False, serve_socket=True):
    Application, Service, Characteristic, Agent, dbus, GLib = build_classes()
    import dbus.mainloop.glib                              # noqa: PLC0415

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    adapter = find_adapter(bus, dbus)
    if not adapter:
        print("  no Bluetooth adapter with GattManager1 found.")
        return 1
    print(f"  adapter: {adapter}")

    # Powered on and discoverable, or the watch has nothing to find.
    props = dbus.Interface(bus.get_object(BLUEZ, adapter), DBUS_PROP_IFACE)
    props.Set(ADAPTER_IFACE, "Powered", dbus.Boolean(True))

    # Register our own pairing agent (see Agent's own docstring for why) BEFORE anything
    # that could trigger pairing - forget_suunto_bonds()/discovery/connect below - so a
    # fresh pair started moments after this process comes up always has an agent to answer
    # to, not just ones that happen after some other agent got there first.
    agent = Agent(bus, AGENT_PATH)
    agent_manager = dbus.Interface(bus.get_object(BLUEZ, "/org/bluez"), AGENT_MANAGER_IFACE)
    try:
        agent_manager.RegisterAgent(AGENT_PATH, "KeyboardDisplay")
        agent_manager.RequestDefaultAgent(AGENT_PATH)
        print("  pairing agent registered (KeyboardDisplay - fresh pairs need a passkey "
              "relayed via /api/ble/passkey)")
    except Exception as exc:
        print(f"  could not register pairing agent: {exc} - fresh pairing will depend on "
              "whatever agent (if any) the desktop environment already provides")

    service = Service(bus, 0)
    # Order matters only for readability; BlueZ keys off the UUIDs.
    notify_chrc = Characteristic(bus, 0, NOTIFY_CHAR_UUID, ["notify", "read"], service)
    write_chrc = Characteristic(bus, 1, WRITE_CHAR_UUID,
                                ["write-without-response", "write"], service)
    service.characteristics = [notify_chrc, write_chrc]
    app = Application(bus, service)

    rx_total = [0]
    # One assembler for the whole session: the watch's writes arrive as 20-byte ATT chunks
    # that do not align with frame boundaries, so reassembly has to be stateful across them.
    assembler = NspAssembler()

    def on_write(chunk):
        rx_total[0] += len(chunk)
        handle_rx(assembler, link, chunk, verbose)

    write_chrc.on_write = on_write
    link_state = LinkState()

    # The Link-compatible face of this transport. Constructed before the characteristic
    # callbacks are armed so a frame arriving early is never dropped - the same "arm the RX
    # stash first" lesson the Kotlin module learned when the Kailash sent its hello once.
    link = ServerLink(notify_chrc, GLib)

    # A subscription is the start of a session: the watch's next frame is its 0x1201 opener,
    # which must get the handshake answer, not a sync-notice one. Without a reset here, a link
    # whose drop BlueZ never signalled left handshake_done set from the old session.
    def on_subscribed():
        if not link.handshake_done.is_set():
            assembler.reset()
            link.reset()

    notify_chrc.on_subscribed = on_subscribed

    if serve_socket:
        control = ControlSocketServer(link, rx_total, agent=agent,
                                       bus=bus, dbus_module=dbus, adapter=adapter, glib=GLib,
                                       link_state=link_state)
        threading.Thread(target=control.serve_forever, daemon=True).start()

    # Real gap, found testing this live 2026-08-11: BlueZ's GATT server has no
    # onConnectionStateChange-equivalent callback the way Android's BluetoothGattServerCallback
    # does - when the watch's ACL link drops, BlueZ does NOT reliably call StopNotify on our
    # characteristic first, so `notify_chrc.notifying` (and therefore ble_bridge's
    # "subscribed"/"handshake_done" status) can keep reporting stale-true after the watch is
    # actually gone, and a subsequent command() call just hangs to its own timeout instead of
    # failing fast. Wired into start_discovery()'s own disconnect signal (which also has to
    # exist, to un-blacklist the device path for a retry) rather than a second, separate
    # D-Bus listener for the same signal.
    def reset_link_on_disconnect(path):
        # `notifying` stays as BlueZ left it: a paired watch is still subscribed next time.
        link.reset()
        assembler.reset()

    gatt_manager = dbus.Interface(bus.get_object(BLUEZ, adapter), GATT_MANAGER_IFACE)

    loop = GLib.MainLoop()

    def registered():
        print(f"  NSP service registered ({SERVICE_UUID})")
        print('  now trigger "Pair Mobile App" (first time) or "Sync now" (already paired) '
              "on the watch - its solicitation window is short.")

    def failed(error):
        print(f"  registration failed: {error}")
        loop.quit()

    gatt_manager.RegisterApplication(app.path, {},
                                     reply_handler=registered, error_handler=failed)
    # No advertisement of our own: the watch is the one calling, and the tablet never
    # advertises either. The old optional "AmbitApp" one only added radio traffic next to the
    # scan (removed 2026-10-25 with the scan bursts, after it kept a mouse from reconnecting).

    # NOT automatic any more. Clearing bonds every run fixed the first pairing and broke
    # every one after it: the bond a successful session establishes is exactly what lets the
    # watch reconnect, and deleting it each time put us back to square one (real regression,
    # 2026-08-11 - a working session was followed by le-connection-abort-by-local because the
    # bond it had just created was wiped). Now opt-in, for when pairing is genuinely stuck.
    if forget:
        forget_suunto_bonds(bus, dbus, adapter)

    # The real mechanism, mirroring AmbitBleModule.scanAndConnect(): find the watch
    # soliciting our service, then connect to it while we host the server.
    start_discovery(bus, dbus, GLib, adapter, verbose,
                    on_disconnect=reset_link_on_disconnect, agent=agent,
                    state=link_state, session_started=link.handshake_done.is_set)

    if timeout > 0:
        def stop():
            print(f"  stopping after {timeout}s "
                  f"({rx_total[0]} B received, "
                  f"{'subscribed' if notify_chrc.notifying else 'never subscribed'})")
            loop.quit()
            return False
        GLib.timeout_add_seconds(timeout, stop)

    try:
        loop.run()
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        try:
            gatt_manager.UnregisterApplication(app.path)
            agent_manager.UnregisterAgent(AGENT_PATH)
        except Exception:
            pass                       # teardown on an already-gone bus is not a failure
    return 0


class _Stamped:
    """Puts the date and time in front of every line written to `stream`. The daemon log had
    none, so how long a link lasted, or what came just before a drop, could only be guessed."""

    def __init__(self, stream):
        self._stream = stream
        self._line_start = True

    def write(self, text):
        out = []
        for part in text.splitlines(keepends=True):
            if self._line_start:
                out.append(time.strftime("%m-%d %H:%M:%S "))
            out.append(part)
            self._line_start = part.endswith("\n")
        self._stream.write("".join(out))
        return len(text)

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    listen_p = sub.add_parser("listen", help="host the NSP service and wait for the watch")
    listen_p.add_argument("--verbose", action="store_true", help="dump every frame")
    listen_p.add_argument("--timeout", type=int, default=0,
                          help="stop after N seconds (0 = until Ctrl-C)")
    listen_p.add_argument("--forget", action="store_true",
                          help="unpair existing Suunto bonds first - use when pairing is "
                               "stuck, NOT routinely: the bond is what lets a paired watch "
                               "reconnect")
    listen_p.add_argument("--no-socket", action="store_true",
                          help="don't start the local control socket (desktop/backend/server.py "
                               "uses it; a bare CLI session doesn't need it)")
    args = parser.parse_args()
    if args.action == "listen":
        sys.stdout = _Stamped(sys.stdout)
        return listen(verbose=args.verbose, timeout=args.timeout, forget=args.forget,
                      serve_socket=not args.no_socket)
    return 1


if __name__ == "__main__":
    sys.exit(main())
