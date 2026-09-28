#!/usr/bin/env python3
"""Hammerhead Karoo logging proxy - this host stands in for the Hammerhead Companion phone app.

Real request, 2026-09-28 (André): "yes, build the logging proxy" - step one of offline GPX to the
Karoo 3 (docs/reference/hammerhead-karoo-companion.md). The Companion never sends route bytes;
it uploads to Hammerhead's cloud and the Karoo downloads the route itself, through the phone's
BLE HTTP proxy when it has no Wi-Fi. This tool IS that proxy: it forwards every request the
Karoo makes to the real internet, unchanged, and records request + response to a JSONL file.
One captured route sync tells us which URLs and JSON a local "cloud" would have to serve.

Everything below is transcribed from Companion 1.56.0 (jadx, ../hammerhead-re/out), not guessed:

  * Role: the PHONE is the GATT server, the Karoo connects in as client (KarooGattServer).
    Same inverted role as tools/ble_server.py, whose BlueZ lessons are reused here.
  * Messages both ways use one pull-based framing (MessageEvent, KarooServerMessageFragmenter,
    KarooClientMessageBuilder), little-endian integers (EncodedInteger16/32):
      START   00 type:u16 len:u32      on the message-event characteristic
      chunk   seq:u8 data...           Karoo->us on 9baf0006 (write), us->Karoo on 9baf0007 (notify)
      END     01 type:u16
      RESET   02 type:u16 received:u32 seq:u8     "resend from here"
      REQUEST 03 type:u16 count:u8     Karoo asks for `count` more chunks of our message
    Our chunks carry at most (MTU - 4) data bytes after the sequence byte, the Karoo pulls one
    more chunk after the last to receive END (fragmenter.handleEvent: index == length -> END).
  * HTTP proxy (HTTPProxyService): HTTP_URL starts a request; HEADERS/BODY append; HTTP_TIMEOUT
    is a BIG-endian int32 (ByteBuffer.getInt); HTTP_TX_ID/HTTP_TAG are strings; HTTP_REQUEST_TYPE
    (one byte, GET=0 HEAD POST PUT DELETE PATCH CONNECT OPTIONS TRACE) completes it. Headers are
    "k:v" joined by "≤≥" (HttpResponse.HEADERS_ENTRY_DELIMITER). The reply is four messages in
    this order: STATUS_CODE (u16 LE), HEADERS, TX_ID, BODY - or TX_ID, FAILURE on an I/O error.
  * Handshake (KarooDataListeningService): on connect, on REQUEST_CAPABILITIES and on the old
    INTERNET_CHECK we send CAPABILITIES (CompanionAppCapabilities JSON); INTERNET_CHECK_V2 gets
    INTERNET_CHECK_RESPONSE {"hasInternet": ...}.

NOT KNOWN YET, and what a first run is for: whether the Karoo accepts a companion that isn't the
phone it was paired with in the Hammerhead app (it bonds by MAC; the phone never sends an
account token in the steady state, but AUTH_HANDOFF exists), and what a route sync requests.

    ./tools/karoo_proxy.py listen --karoo AA:BB:CC:DD:EE:FF      # connect + pair + proxy + log
    ./tools/karoo_proxy.py listen                                # advertise, wait for the Karoo
    ./tools/karoo_proxy.py listen --document-synced <route id>   # also poke a route sync
    ./tools/karoo_proxy.py show ~/.cache/AmbitApp/karoo_proxy/<file>.jsonl

The log holds the Karoo's own Authorization headers and account e-mail - it is written 0600 and
stays on this machine; do not attach it anywhere without redacting.
"""

import argparse
import base64
import json
import os
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

# --- identifiers (KarooGattProfile) -------------------------------------------------------------

SERVICE_UUID = "9baf0001-7deb-4901-b0ca-d98764f41cc6"
NOTIFICATION_UUID = "9baf0002-7deb-4901-b0ca-d98764f41cc6"
NOTIFICATION_ATTRIBUTE_UUID = "9baf0003-7deb-4901-b0ca-d98764f41cc6"
MESSAGE_EVENT_UUID = "9baf0005-7deb-4901-b0ca-d98764f41cc6"
RECEIVE_CHUNK_UUID = "9baf0006-7deb-4901-b0ca-d98764f41cc6"     # Karoo -> us
SEND_CHUNK_UUID = "9baf0007-7deb-4901-b0ca-d98764f41cc6"        # us -> Karoo
MEDIA_UPDATE_UUID = "2f7cabce-808d-411f-9a0c-bb92ba96c102"
MEDIA_COMMAND_UUID = "9b3c81d8-57b1-4a8a-b8df-0e56f7ca51c2"

# ServerMessage.Type ordinals (phone -> Karoo)
S_CAPABILITIES, S_HTTP_STATUS_CODE, S_HTTP_HEADERS, S_HTTP_BODY, S_HTTP_FAILURE = 0, 1, 2, 3, 4
S_SHARED_LOCATION, S_REQUEST_AUTH, S_HTTP_TX_ID, S_AUTH_HANDOFF, S_DOCUMENT_SYNCED = 5, 6, 7, 8, 9
S_INTERNET_CHECK_RESPONSE, S_PAIRING_COMPLETED, S_SHARED_APK_URL, S_LIVE_TRACKING = 10, 11, 12, 13
SERVER_TYPES = ["CAPABILITIES", "HTTP_STATUS_CODE", "HTTP_HEADERS", "HTTP_BODY", "HTTP_FAILURE",
                "SHARED_LOCATION", "REQUEST_AUTH", "HTTP_TX_ID", "AUTH_HANDOFF", "DOCUMENT_SYNCED",
                "INTERNET_CHECK_RESPONSE", "PAIRING_COMPLETED", "SHARED_APK_URL", "LIVE_TRACKING"]

# ClientMessage.Type ordinals (Karoo -> phone)
CLIENT_TYPES = ["CAPABILITIES", "HTTP_URL", "HTTP_HEADERS", "HTTP_BODY", "HTTP_TIMEOUT",
                "HTTP_REQUEST_TYPE", "PROXY_AUTH", "INTERNET_CHECK", "HTTP_TX_ID", "MESSAGE_RESULT",
                "HTTP_TAG", "LIVE_TRACKING", "KEEP_ALIVE", "SYNC_COMPLETE", "REQUEST_CAPABILITIES",
                "INTERNET_CHECK_V2", "BATTERY_STATUS", "INITIATE_PARTNER_SYNC"]
C = {name: i for i, name in enumerate(CLIENT_TYPES)}

HTTP_METHODS = ["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "CONNECT", "OPTIONS", "TRACE"]
HEADERS_ENTRY_DELIMITER = "≤≥"

EV_START, EV_END, EV_RESET, EV_REQUEST = 0, 1, 2, 3

DEFAULT_MTU = 23          # KarooGattServer.DEFAULT_MTU
MTU_OVERHEAD = 4          # KarooGattServer.MTU_OVERHEAD -> max chunk data = mtu - 4

# What Companion 1.56.0 announces (CompanionAppCapabilities defaults; every client and server
# type it knows). Sent with every field explicit rather than relying on serializer defaults.
COMPANION_VERSION = "1.56.0"

LOG_DIR = Path.home() / ".cache" / "AmbitApp" / "karoo_proxy"


def type_name(names, t):
    return names[t] if 0 <= t < len(names) else f"#{t}"


def capabilities_json(has_internet=True):
    return {
        "version": COMPANION_VERSION,
        "supportsHttp": True,
        "hasInternet": bool(has_internet),
        "supportsInternetCheck": True,
        "displaysLiveTracking": True,
        "internetCheckV2": True,
        "pairingCompleted": True,
        "clientMsgTypes": list(range(len(CLIENT_TYPES))),
        "serverMsgTypes": list(range(len(SERVER_TYPES))),
    }


# --- framing (transport-free, unit-tested in test_karoo_proxy.py) -------------------------------

def event_bytes(kind, msg_type, length=None, seq=None):
    """MessageEvent.toNotifyBytes: [kind][type u16 LE][length u32 LE]?[seq u8]?"""
    out = bytes([kind]) + struct.pack("<H", msg_type)
    if length is not None:
        out += struct.pack("<I", length)
    if seq is not None:
        out += bytes([seq & 0xFF])
    return out


class ClientAssembler:
    """Rebuilds Karoo -> us messages (KarooClientMessageBuilder.handleEvent).

    `on_reset(msg_type, received, next_seq)` is called when a chunk arrives out of sequence; the
    phone answers that with a RESET event so the Karoo resends from the byte it names.
    """

    def __init__(self, on_message, on_reset=None, log=None):
        self.on_message = on_message
        self.on_reset = on_reset
        self.log = log or (lambda *_: None)
        self._clear()

    def _clear(self):
        self.msg_type = 0
        self.expected = 0
        self.data = b""
        self.next_seq = 0
        self.awaiting_reset = False

    def event(self, value):
        """A write to the message-event characteristic. Returns the parsed kind (or None)."""
        if not value:
            return None
        kind = value[0]
        if kind == EV_START and len(value) >= 7:
            msg_type, length = struct.unpack_from("<HI", value, 1)
            self._clear()
            self.msg_type, self.expected = msg_type, length
        elif kind == EV_END:
            if self.awaiting_reset:
                self.log("END while waiting for a stream reset - ignored")
            elif len(self.data) == self.expected:
                msg_type, data = self.msg_type, self.data
                self._clear()
                self.on_message(msg_type, data)
            else:
                self.log(f"END with {len(self.data)} of {self.expected} bytes - message dropped")
                self._clear()
        return kind

    def chunk(self, value):
        """A write to the receive-chunk characteristic: [seq][data]."""
        if not value:
            return
        seq, data = value[0], value[1:]
        if seq == self.next_seq:
            self.data += data
            self.next_seq = (self.next_seq + 1) & 0xFF
            self.awaiting_reset = False
            return
        if not self.awaiting_reset:
            self.log(f"expected seq {self.next_seq}, got {seq} at {len(self.data)} bytes - reset")
            if self.on_reset:
                self.on_reset(self.msg_type, len(self.data), self.next_seq)
        self.awaiting_reset = True


class ServerSender:
    """Sends us -> Karoo messages one at a time (KarooServerMessageFragmenter + sendServerMessage).

    `notify_event(bytes)` and `notify_chunk(bytes)` push to the two characteristics; `mtu()` is
    read at every chunk so a later MTU exchange takes effect. A message is finished once END has
    gone out; the next queued message then gets its START.
    """

    def __init__(self, notify_event, notify_chunk, mtu=lambda: DEFAULT_MTU, log=None):
        self.notify_event = notify_event
        self.notify_chunk = notify_chunk
        self.mtu = mtu
        self.log = log or (lambda *_: None)
        self.queue = []
        self.current = None       # dict(type, data, index, seq)
        self.lock = threading.RLock()

    def send(self, msg_type, data):
        with self.lock:
            self.queue.append((msg_type, bytes(data)))
            if self.current is None:
                self._start_next()

    def _start_next(self):
        if not self.queue:
            self.current = None
            return
        msg_type, data = self.queue.pop(0)
        self.current = {"type": msg_type, "data": data, "index": None, "seq": None,
                        "started": time.monotonic()}
        self.notify_event(event_bytes(EV_START, msg_type, len(data)))

    def _emit(self):
        cur = self.current
        if cur["index"] == len(cur["data"]):
            self.notify_event(event_bytes(EV_END, cur["type"]))
            self._start_next()
            return
        end = min(cur["index"] + self.mtu() - MTU_OVERHEAD, len(cur["data"]))
        self.notify_chunk(bytes([cur["seq"] & 0xFF]) + cur["data"][cur["index"]:end])

    def request(self, msg_type, count):
        """REQUEST from the Karoo: `count` chunk requests for message `msg_type`."""
        with self.lock:
            for _ in range(count):
                cur = self.current
                if cur is None or cur["type"] != msg_type:
                    self.log(f"chunk request for {type_name(SERVER_TYPES, msg_type)} "
                             "with no such message in flight - ignored")
                    return
                if cur["index"] is None:
                    cur["index"], cur["seq"] = 0, 0
                else:
                    cur["index"] = min(cur["index"] + self.mtu() - MTU_OVERHEAD, len(cur["data"]))
                    cur["seq"] = (cur["seq"] + 1) & 0xFF
                self._emit()

    def reset(self, msg_type, received, seq):
        """RESET from the Karoo: resend from byte `received` with sequence number `seq`."""
        with self.lock:
            cur = self.current
            if cur is None or cur["type"] != msg_type:
                return
            cur["index"], cur["seq"] = min(received, len(cur["data"])), seq
            self._emit()

    def abandon(self):
        """Link dropped: forget everything in flight (the Karoo starts over on reconnect)."""
        with self.lock:
            self.queue.clear()
            self.current = None


# --- HTTP proxy ---------------------------------------------------------------------------------

def parse_headers(raw):
    out = []
    for entry in raw.decode("utf-8", "replace").split(HEADERS_ENTRY_DELIMITER):
        key, sep, value = entry.partition(":")
        if sep:
            out.append((key, value))
    return out


def encode_headers(pairs):
    return HEADERS_ENTRY_DELIMITER.join(f"{k}:{v}" for k, v in pairs).encode("utf-8")


class HttpRequest:
    def __init__(self, url):
        self.url = url
        self.headers = b""
        self.body = b""
        self.timeout = 30
        self.tx_id = None
        self.tag = None
        self.method = None


def perform(request, opener=urllib.request.urlopen):
    """Runs one proxied request. Returns (status, header pairs, body) or raises OSError.

    OkHttp here (HTTPProxyService.performHttpRequest) sets read/write timeouts to half the
    Karoo's HTTP_TIMEOUT; the same halving is kept. A 4xx/5xx is a normal response to pass back,
    not a failure - only transport errors become HTTP_FAILURE.
    """
    url = request.url.decode("utf-8", "replace")
    send_body = request.body if (request.body or request.method in ("POST", "PUT", "PATCH")) else None
    req = urllib.request.Request(url, data=send_body, method=request.method)
    for key, value in parse_headers(request.headers):
        req.add_header(key, value)
    try:
        with opener(req, timeout=max(1, request.timeout // 2)) as resp:
            return resp.status, list(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, list(err.headers.items()), err.read()


def body_for_log(data, headers=()):
    """Text bodies are logged as text, anything else as base64 - a route may well be binary."""
    ctype = next((v for k, v in headers if k.lower() == "content-type"), "")
    encoded = any(k.lower() == "content-encoding" for k, _ in headers)
    if not encoded:
        try:
            text = data.decode("utf-8")
            if "json" in ctype or "text" in ctype or "xml" in ctype or text.isprintable() or not ctype:
                return {"text": text}
        except UnicodeDecodeError:
            pass
    return {"base64": base64.b64encode(data).decode("ascii")}


class Session:
    """Everything above the framing: handshake answers, the HTTP proxy, logging.

    `send(type, bytes)` queues a server message; `log_event(dict)` appends to the capture.
    HTTP requests run on worker threads so a slow server never stalls BLE traffic.
    """

    def __init__(self, send, log_event, has_internet=True, performer=perform, document_synced=None):
        self.send = send
        self.log_event = log_event
        self.has_internet = has_internet
        self.performer = performer
        self.document_synced = document_synced
        self.pending = None
        self.sent_document = False

    def send_capabilities(self, why):
        self.log_event({"dir": "out", "type": "CAPABILITIES", "why": why})
        self.send(S_CAPABILITIES, json.dumps(capabilities_json(self.has_internet)).encode())

    def connected(self):
        # KarooDataListeningService: capabilities go out as soon as the Karoo subscribes.
        self.send_capabilities("connected")

    def on_message(self, msg_type, data):
        name = type_name(CLIENT_TYPES, msg_type)
        if msg_type in (C["HTTP_URL"], C["HTTP_HEADERS"], C["HTTP_BODY"], C["HTTP_TIMEOUT"],
                        C["HTTP_TX_ID"], C["HTTP_TAG"], C["HTTP_REQUEST_TYPE"]):
            self._http_part(msg_type, data)
            return
        entry = {"dir": "in", "type": name, "len": len(data)}
        if data:
            try:
                entry["json"] = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                entry["hex"] = data.hex()
        self.log_event(entry)
        if msg_type in (C["REQUEST_CAPABILITIES"], C["INTERNET_CHECK"]):
            self.send_capabilities(name)
        elif msg_type == C["INTERNET_CHECK_V2"]:
            self.log_event({"dir": "out", "type": "INTERNET_CHECK_RESPONSE",
                            "hasInternet": self.has_internet})
            self.send(S_INTERNET_CHECK_RESPONSE,
                      json.dumps({"hasInternet": self.has_internet}).encode())
        elif msg_type == C["CAPABILITIES"] and self.document_synced and not self.sent_document:
            # The Karoo has introduced itself - the moment ImportHandler would push a document.
            self.sent_document = True
            self.log_event({"dir": "out", "type": "DOCUMENT_SYNCED", "id": self.document_synced})
            self.send(S_DOCUMENT_SYNCED, self.document_synced.encode("utf-8"))

    def _http_part(self, msg_type, data):
        if msg_type == C["HTTP_URL"]:
            self.pending = HttpRequest(data)
            return
        req = self.pending
        if req is None:
            self.log_event({"dir": "in", "type": CLIENT_TYPES[msg_type],
                            "note": "no HTTP_URL before it - ignored"})
            return
        if msg_type == C["HTTP_HEADERS"]:
            req.headers += data
        elif msg_type == C["HTTP_BODY"]:
            req.body += data
        elif msg_type == C["HTTP_TIMEOUT"] and len(data) >= 4:
            req.timeout = struct.unpack(">i", data[:4])[0]      # big-endian: ByteBuffer.getInt
        elif msg_type == C["HTTP_TX_ID"]:
            req.tx_id = data.decode("utf-8", "replace")
        elif msg_type == C["HTTP_TAG"]:
            req.tag = data.decode("utf-8", "replace")
        elif msg_type == C["HTTP_REQUEST_TYPE"]:
            self.pending = None
            if not data or data[0] >= len(HTTP_METHODS):
                self.log_event({"dir": "in", "type": "HTTP_REQUEST_TYPE", "hex": data.hex(),
                                "note": "unknown method - request discarded"})
                return
            req.method = HTTP_METHODS[data[0]]
            threading.Thread(target=self._run_http, args=(req,), daemon=True).start()

    def _run_http(self, req):
        started = time.time()
        entry = {"dir": "http", "tx_id": req.tx_id, "tag": req.tag, "method": req.method,
                 "url": req.url.decode("utf-8", "replace"), "timeout": req.timeout,
                 "request_headers": parse_headers(req.headers),
                 "request_body": body_for_log(req.body, parse_headers(req.headers))}
        tx = [(S_HTTP_TX_ID, req.tx_id.encode("utf-8"))] if req.tx_id is not None else []
        try:
            status, headers, body = self.performer(req)
        except Exception as exc:                            # noqa: BLE001 - reported to the Karoo
            entry.update(error=repr(exc), ms=int((time.time() - started) * 1000))
            self.log_event(entry)
            for msg in tx + [(S_HTTP_FAILURE, repr(exc).encode("utf-8"))]:
                self.send(*msg)
            return
        entry.update(status=status, response_headers=headers,
                     response_body=body_for_log(body, headers), response_len=len(body),
                     ms=int((time.time() - started) * 1000))
        self.log_event(entry)
        for msg in ([(S_HTTP_STATUS_CODE, struct.pack("<H", status & 0xFFFF)),
                     (S_HTTP_HEADERS, encode_headers(headers))] + tx + [(S_HTTP_BODY, body)]):
            self.send(*msg)


class JsonlLog:
    def __init__(self, path, echo=True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        self.fh = os.fdopen(fd, "a", encoding="utf-8")
        self.echo = echo
        self.lock = threading.Lock()

    def __call__(self, entry):
        entry = {"t": round(time.time(), 3), **entry}
        with self.lock:
            self.fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self.fh.flush()
        if self.echo:
            print("  " + summarize(entry))


def summarize(e):
    if e.get("dir") == "http":
        tail = (f"-> {e['status']} {e.get('response_len', 0)} B" if "status" in e
                else f"-> FAILED {e.get('error')}")
        return f"HTTP {e['method']} {e['url']} {tail} ({e.get('ms')} ms)"
    bits = [e.get("dir", "?"), e.get("type", "?")]
    for key in ("why", "note", "id", "hasInternet", "len"):
        if key in e:
            bits.append(f"{key}={e[key]}")
    return " ".join(str(b) for b in bits)


# --- BlueZ transport ----------------------------------------------------------------------------

BLUEZ = "org.bluez"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
GATT_MANAGER_IFACE = "org.bluez.GattManager1"
GATT_SERVICE_IFACE = "org.bluez.GattService1"
GATT_CHRC_IFACE = "org.bluez.GattCharacteristic1"
LE_ADVERTISING_MANAGER_IFACE = "org.bluez.LEAdvertisingManager1"
LE_ADVERTISEMENT_IFACE = "org.bluez.LEAdvertisement1"
AGENT_IFACE = "org.bluez.Agent1"
AGENT_MANAGER_IFACE = "org.bluez.AgentManager1"
BASE_PATH = "/org/ambitapp/karoo"


def listen(karoo=None, log_path=None, document_synced=None, verbose=False, timeout=0):
    import dbus                                            # noqa: PLC0415 - BLE deps stay optional
    import dbus.mainloop.glib                              # noqa: PLC0415
    import dbus.service as dbus_service                    # noqa: PLC0415
    from gi.repository import GLib                         # noqa: PLC0415

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()

    log_path = log_path or LOG_DIR / time.strftime("%Y%m%d-%H%M%S.jsonl")
    log = JsonlLog(log_path)
    print(f"  capture: {log_path}  (0600 - holds the Karoo's auth headers)")

    state = {"mtu": DEFAULT_MTU}

    def on_main(fn, *args):
        # D-Bus signals (our notifications) must be raised on the GLib main loop thread -
        # the HTTP worker threads only ever schedule, never emit (tools/ble_server.py's lesson).
        GLib.timeout_add(0, lambda: (fn(*args), False)[1])

    class Characteristic(dbus_service.Object):
        def __init__(self, index, uuid, flags, service):
            self.path = f"{service.path}/char{index}"
            self.uuid, self.flags, self.service = uuid, flags, service
            self.notifying = False
            self.on_write = None
            self.on_subscribe = None
            dbus_service.Object.__init__(self, bus, self.path)

        def properties(self):
            return {GATT_CHRC_IFACE: {"Service": dbus.ObjectPath(self.service.path),
                                      "UUID": self.uuid, "Flags": self.flags,
                                      "Notifying": dbus.Boolean(self.notifying)}}

        @dbus_service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return self.properties()[GATT_CHRC_IFACE]

        @dbus_service.method(GATT_CHRC_IFACE, in_signature="a{sv}", out_signature="ay")
        def ReadValue(self, options):
            # KarooGattServer answers every read with GATT_FAILURE - there is no standing value.
            raise dbus.exceptions.DBusException("org.bluez.Error.NotSupported")

        @dbus_service.method(GATT_CHRC_IFACE, in_signature="aya{sv}")
        def WriteValue(self, value, options):
            if "mtu" in options:
                state["mtu"] = int(options["mtu"])
            data = bytes(bytearray(value))
            if verbose:
                print(f"  RX {self.uuid[:8]} {data.hex()}")
            if self.on_write:
                self.on_write(data)

        @dbus_service.method(GATT_CHRC_IFACE)
        def StartNotify(self):
            if not self.notifying:
                self.notifying = True
                if self.on_subscribe:
                    self.on_subscribe()

        @dbus_service.method(GATT_CHRC_IFACE)
        def StopNotify(self):
            self.notifying = False

        @dbus_service.signal(DBUS_PROP_IFACE, signature="sa{sv}as")
        def PropertiesChanged(self, interface, changed, invalidated):
            pass

        def notify(self, payload):
            if not self.notifying:
                return
            if verbose:
                print(f"  TX {self.uuid[:8]} {payload.hex()}")
            self.PropertiesChanged(GATT_CHRC_IFACE, {"Value": dbus.Array(
                [dbus.Byte(b) for b in payload], signature="y")}, [])

    class Service(dbus_service.Object):
        def __init__(self):
            self.path = BASE_PATH + "/service0"
            self.characteristics = []
            dbus_service.Object.__init__(self, bus, self.path)

        def properties(self):
            return {GATT_SERVICE_IFACE: {"UUID": SERVICE_UUID, "Primary": dbus.Boolean(True),
                                         "Characteristics": dbus.Array(
                                             [dbus.ObjectPath(c.path) for c in self.characteristics],
                                             signature="o")}}

        @dbus_service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return self.properties()[GATT_SERVICE_IFACE]

    class Application(dbus_service.Object):
        def __init__(self, service):
            self.path, self.service = BASE_PATH, service
            dbus_service.Object.__init__(self, bus, self.path)

        @dbus_service.method(DBUS_OM_IFACE, out_signature="a{oa{sa{sv}}}")
        def GetManagedObjects(self):
            out = {self.service.path: self.service.properties()}
            for chrc in self.service.characteristics:
                out[chrc.path] = chrc.properties()
            return out

    class Advertisement(dbus_service.Object):
        # KarooServiceAdvertiser: connectable, the service UUID only, no name/tx power (a
        # 128-bit UUID plus a name overflows the 31-byte budget - ble_server.py's lesson).
        def __init__(self):
            self.path = BASE_PATH + "/advert0"
            dbus_service.Object.__init__(self, bus, self.path)

        @dbus_service.method(DBUS_PROP_IFACE, in_signature="s", out_signature="a{sv}")
        def GetAll(self, interface):
            return {"Type": "peripheral", "ServiceUUIDs": dbus.Array([SERVICE_UUID], signature="s")}

        @dbus_service.method(LE_ADVERTISEMENT_IFACE)
        def Release(self):
            pass

    class Agent(dbus_service.Object):
        """DisplayYesNo: the phone side of a Karoo bond is Android's own pairing dialog, which
        is a numeric comparison at most. Codes are printed so they can be checked against the
        Karoo's screen; nothing needs typing in."""

        def __init__(self):
            self.path = BASE_PATH + "/agent"
            dbus_service.Object.__init__(self, bus, self.path)

        @dbus_service.method(AGENT_IFACE)
        def Release(self):
            pass

        @dbus_service.method(AGENT_IFACE, in_signature="os")
        def AuthorizeService(self, device, uuid):
            pass

        @dbus_service.method(AGENT_IFACE, in_signature="ou")
        def RequestConfirmation(self, device, passkey):
            print(f"  pairing: confirming code {passkey:06d} - it should match the Karoo's screen")

        @dbus_service.method(AGENT_IFACE, in_signature="ouq")
        def DisplayPasskey(self, device, passkey, entered):
            print(f"  pairing: type {passkey:06d} on the Karoo")

        @dbus_service.method(AGENT_IFACE, in_signature="o")
        def RequestAuthorization(self, device):
            print("  pairing: accepted (just works)")

        @dbus_service.method(AGENT_IFACE)
        def Cancel(self):
            print("  pairing: cancelled")

    service = Service()
    enc_notify = ["notify", "encrypt-read"]
    notification = Characteristic(0, NOTIFICATION_UUID, ["read", "notify", "encrypt-read"], service)
    attribute = Characteristic(1, NOTIFICATION_ATTRIBUTE_UUID,
                               ["write", "notify", "encrypt-write"], service)
    message_event = Characteristic(2, MESSAGE_EVENT_UUID,
                                   ["read", "write", "notify", "encrypt-read", "encrypt-write"],
                                   service)
    receive_chunk = Characteristic(3, RECEIVE_CHUNK_UUID,
                                   ["write", "write-without-response", "encrypt-write"], service)
    send_chunk = Characteristic(4, SEND_CHUNK_UUID, enc_notify, service)
    media_update = Characteristic(5, MEDIA_UPDATE_UUID, ["write", "notify", "encrypt-write"], service)
    media_command = Characteristic(6, MEDIA_COMMAND_UUID, ["write", "notify", "encrypt-write"],
                                   service)
    service.characteristics = [notification, attribute, message_event, receive_chunk, send_chunk,
                               media_update, media_command]

    sender = ServerSender(message_event.notify, send_chunk.notify, mtu=lambda: state["mtu"],
                          log=lambda m: print("  " + m))

    def send(msg_type, data):
        on_main(sender.send, msg_type, data)

    session = Session(send, log, document_synced=document_synced)
    assembler = ClientAssembler(
        session.on_message,
        on_reset=lambda t, received, seq: message_event.notify(event_bytes(EV_RESET, t, received, seq)),
        log=lambda m: print("  " + m))

    def on_event_write(value):
        kind = value[0] if value else None
        if kind == EV_REQUEST and len(value) >= 4:
            sender.request(struct.unpack_from("<H", value, 1)[0], value[3])
        elif kind == EV_RESET and len(value) >= 8:
            msg_type, received = struct.unpack_from("<HI", value, 1)
            sender.reset(msg_type, received, value[7])
        else:
            assembler.event(value)

    message_event.on_write = on_event_write
    receive_chunk.on_write = assembler.chunk
    attribute.on_write = lambda v: None        # notification attributes: we send no notifications
    media_command.on_write = lambda v: log({"dir": "in", "type": "MEDIA_COMMAND", "hex": v.hex()})

    def subscribed():
        print("  Karoo subscribed to messages - link is live")
        log({"dir": "link", "type": "SUBSCRIBED", "mtu": state["mtu"]})
        session.connected()

    message_event.on_subscribe = subscribed

    manager = dbus.Interface(bus.get_object(BLUEZ, "/"), DBUS_OM_IFACE)
    adapter = next((p for p, i in manager.GetManagedObjects().items()
                    if GATT_MANAGER_IFACE in i and LE_ADVERTISING_MANAGER_IFACE in i), None)
    if not adapter:
        print("  no Bluetooth adapter with GattManager1 + LEAdvertisingManager1 found")
        return 1
    dbus.Interface(bus.get_object(BLUEZ, adapter), DBUS_PROP_IFACE).Set(
        ADAPTER_IFACE, "Powered", dbus.Boolean(True))

    agent = Agent()
    agent_manager = dbus.Interface(bus.get_object(BLUEZ, "/org/bluez"), AGENT_MANAGER_IFACE)
    try:
        agent_manager.RegisterAgent(agent.path, "DisplayYesNo")
        agent_manager.RequestDefaultAgent(agent.path)
    except dbus.exceptions.DBusException as exc:
        print(f"  could not register pairing agent ({exc}) - using the desktop's")

    def on_device_props(interface, changed, invalidated, path=None):
        if interface != DEVICE_IFACE or "Connected" not in changed:
            return
        if bool(changed["Connected"]):
            log({"dir": "link", "type": "CONNECTED", "device": path.split("/")[-1]})
        else:
            # BlueZ does not reliably StopNotify on a dropped link (ble_server.py, 2026-08-11).
            log({"dir": "link", "type": "DISCONNECTED", "device": path.split("/")[-1]})
            message_event.notifying = send_chunk.notifying = False
            sender.abandon()
            assembler._clear()

    bus.add_signal_receiver(on_device_props, dbus_interface=DBUS_PROP_IFACE,
                            signal_name="PropertiesChanged", arg0=DEVICE_IFACE,
                            path_keyword="path")

    loop = GLib.MainLoop()
    app = Application(service)
    advert = Advertisement()
    gatt = dbus.Interface(bus.get_object(BLUEZ, adapter), GATT_MANAGER_IFACE)
    ads = dbus.Interface(bus.get_object(BLUEZ, adapter), LE_ADVERTISING_MANAGER_IFACE)
    gatt.RegisterApplication(app.path, {}, reply_handler=lambda: print(
        f"  Karoo companion service registered ({SERVICE_UUID})"),
        error_handler=lambda e: (print(f"  registration failed: {e}"), loop.quit()))
    ads.RegisterAdvertisement(advert.path, {}, reply_handler=lambda: print("  advertising"),
                              error_handler=lambda e: print(f"  advertising unavailable: {e}"))

    if karoo:
        # KarooGattServer.updateBondedDevices: the phone dials the bonded Karoo itself
        # (gattServer.connect(device, autoConnect=true)) - here Pair() when not yet bonded,
        # else Connect(). Both async: they outlast D-Bus's 25 s default reply timeout.
        dev_path = f"{adapter}/dev_{karoo.upper().replace(':', '_')}"

        def dial():
            try:
                device = dbus.Interface(bus.get_object(BLUEZ, dev_path), DEVICE_IFACE)
                props = dbus.Interface(bus.get_object(BLUEZ, dev_path), DBUS_PROP_IFACE)
                paired = bool(props.Get(DEVICE_IFACE, "Paired"))
            except dbus.exceptions.DBusException:
                print(f"  {karoo} not known to BlueZ yet - scanning for it")
                adapter_obj = dbus.Interface(bus.get_object(BLUEZ, adapter), ADAPTER_IFACE)
                try:
                    adapter_obj.SetDiscoveryFilter({"Transport": "le"})
                    adapter_obj.StartDiscovery()
                except dbus.exceptions.DBusException:
                    pass
                GLib.timeout_add_seconds(5, dial)
                return False
            try:
                dbus.Interface(bus.get_object(BLUEZ, adapter), ADAPTER_IFACE).StopDiscovery()
            except dbus.exceptions.DBusException:
                pass
            call = device.Connect if paired else device.Pair
            print(f"  {'connecting to' if paired else 'pairing with'} {karoo}")
            call(reply_handler=lambda: print(f"  {'connected' if paired else 'paired'} - "
                                             "waiting for the Karoo to subscribe"),
                 error_handler=lambda e: (print(f"  {e} - retrying in 5 s"),
                                          GLib.timeout_add_seconds(5, dial)),
                 timeout=60)
            return False

        GLib.timeout_add(500, dial)
    else:
        print("  no --karoo address: advertising only, waiting for the Karoo to connect")

    if timeout:
        GLib.timeout_add_seconds(timeout, lambda: (loop.quit(), False)[1])
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\n  stopped")
    finally:
        for fn in (lambda: ads.UnregisterAdvertisement(advert.path),
                   lambda: gatt.UnregisterApplication(app.path),
                   lambda: agent_manager.UnregisterAgent(agent.path)):
            try:
                fn()
            except Exception:                              # noqa: BLE001 - bus already gone
                pass
    return 0


def show(path, bodies=False):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        print(time.strftime("%H:%M:%S", time.localtime(entry["t"])), summarize(entry))
        if bodies and entry.get("dir") == "http":
            for key in ("request_body", "response_body"):
                body = entry.get(key) or {}
                text = body.get("text")
                if text:
                    print(f"    {key}: {text[:2000]}")
                elif body.get("base64"):
                    print(f"    {key}: <{len(base64.b64decode(body['base64']))} bytes binary>")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="action", required=True)
    lp = sub.add_parser("listen", help="host the companion service, proxy and log")
    lp.add_argument("--karoo", help="the Karoo's Bluetooth address (dial + pair it)")
    lp.add_argument("--log", help="capture file (default ~/.cache/AmbitApp/karoo_proxy/<time>.jsonl)")
    lp.add_argument("--document-synced", metavar="ID",
                    help="after the Karoo's CAPABILITIES, send DOCUMENT_SYNCED(ID) as the "
                         "Companion does after an import - makes the Karoo sync that route now")
    lp.add_argument("--verbose", action="store_true", help="print every raw GATT write/notify")
    lp.add_argument("--timeout", type=int, default=0, help="stop after N seconds")
    sp = sub.add_parser("show", help="summarize a capture")
    sp.add_argument("path")
    sp.add_argument("--bodies", action="store_true", help="print text bodies too")
    args = parser.parse_args()
    if args.action == "show":
        show(args.path, args.bodies)
        return 0
    return listen(karoo=args.karoo, log_path=args.log, document_synced=args.document_synced,
                  verbose=args.verbose, timeout=args.timeout)


if __name__ == "__main__":
    sys.exit(main())
