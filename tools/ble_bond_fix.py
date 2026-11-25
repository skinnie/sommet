#!/usr/bin/env python3
"""Make a Linux Bluetooth pairing last, without touching the computer (watch-protocol/09).

Linux gives the watch its identity during pairing only with the adapter's privacy on, and
privacy only changes with the adapter switched off, which drops every other Bluetooth device
(the kernel's smp.c build_pairing_cmd and mgmt.c set_privacy). Without that identity the
watch stores the pairing with an all-zero IRK and the address 00:00:00:00:00:00. When the
link drops, the watch hands that record back to its Bluetooth chip, the chip reads "IRK
starts with eight zero bytes" as "no bond" and throws the key away, and the next connection
gets "key missing" (nRF51 S130 app 2.1.9 FUN_00024258/FUN_0002421c, read in
sommet-builds/re-linux-pairing/FINDINGS.md).

So after a pairing made with privacy off, this rewrites that one record on the watch with
a settings write (0x1101): a random IRK whose first eight bytes are not all zero, and the
computer's adapter address (least significant byte first, as the watch stores it, address
class 0 = public). The watch's own key (EncodingKey), EncodingRnd and EncodingDiv are kept
byte for byte. Seen on André's Ambit3 Peak (firmware 2.4.17), 2026-11-25: the record read
back as the zero record, the write was answered over Bluetooth with the link up, and after a
real link drop the watch came back within a second with no code.

Since 2026-11-25 (André: "linking a watch should never affect the system", then "b") Sommet
no longer turns privacy on while a watch pairs: the backend runs this tool by itself after a
pairing (AfterPairing below). The Settings switch "Keep Bluetooth privacy on"
(bt_privacy.set_always) stays as the fallback for a watch this does not cover.

Works over the cable or, with AMBIT_TRANSPORT=ble, over the Bluetooth link (write_nav.Link).
Reads only, unless --write is given. Never prints a key or an address.

    ble_bond_fix.py --address AA:BB:CC:DD:EE:FF [--write] [--json]
"""
import argparse
import json
import os
import pathlib
import re
import struct
import sys
import threading
import time

import sbem_schema
import settings_write as S

WHITELIST_FIELD = "WhitelistedBleDevices+Device.DeviceId"
PODS_FIELD = "Pods+Pod.Type"
TEMPLATE = [PODS_FIELD, WHITELIST_FIELD]
PHONE_POD_TYPE = 6            # Pods record pointing at the paired phone's whitelist record
PHONE_ID_LOW = 0xF000         # the phone service's id in the low 16 bits of its DeviceId
ZERO_MAC = "00:00:00:00:00:00"
ADDRESS = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


class Record:
    """One WhitelistedBleDevices record: u32 DeviceId, u8 AddrClass, then the MAC, IRK,
    EncodingKey and EncodingRnd as NUL-terminated colon-hex strings, then u16 EncodingDiv,
    u8 IsAuthenticated, u8 IsNspCapable (147 bytes with all four strings full).
    Layout from the MSP430 settings writer (appBlePairingBsml.c FUN_00028584) and the
    emulator's write (FINDINGS.md 2.3, 2.4)."""

    def __init__(self, data):
        data = bytes(data)
        if len(data) < 5 + 4 + 4:
            raise ValueError("whitelist record too short")
        self.device_id, self.addr_class = struct.unpack_from("<IB", data)
        parts, off = [], 5
        for _ in range(4):
            end = data.find(b"\0", off)
            if end < 0:
                raise ValueError("whitelist record: a text field has no end")
            parts.append(data[off:end])
            off = end + 1
        if len(data) - off != 4:
            raise ValueError("whitelist record: unexpected length after the text fields")
        self.mac, self.irk, self.key, self.rnd = (p.decode("ascii") for p in parts)
        self.tail = data[off:]                     # EncodingDiv, IsAuthenticated, IsNspCapable

    def to_bytes(self):
        out = struct.pack("<IB", self.device_id, self.addr_class)
        for text in (self.mac, self.irk, self.key, self.rnd):
            out += text.encode("ascii") + b"\0"
        return out + self.tail


def _hex_bytes(text):
    return bytes.fromhex(text.replace(":", "")) if text else b""


def irk_is_blank(irk_text):
    """What the chip calls "no bond": the IRK's first eight bytes are zero (or it is absent)."""
    return not any(_hex_bytes(irk_text)[:8])


def needs_fix(rec):
    """A pairing made without the central's identity: zero address and IRK, key present."""
    return rec.mac.lower() == ZERO_MAC and irk_is_blank(rec.irk) and bool(rec.key)


def watch_order(address):
    """'AA:BB:CC:DD:EE:FF' -> 'ff:ee:dd:cc:bb:aa', how the watch stores an address."""
    if not ADDRESS.match(address or ""):
        raise ValueError("not a Bluetooth address")
    return ":".join(reversed(address.lower().split(":")))


def new_irk(rand=os.urandom):
    """16 random bytes whose first eight are not all zero, in the watch's colon-hex form."""
    while True:
        raw = rand(16)
        if any(raw[:8]):
            return ":".join(f"{b:02x}" for b in raw)


def _group_id(schema, suffix):
    field = S._find_field(schema, suffix)
    group = S._group_containing(schema, field.fid)
    return group if group is not None else field.fid


def phone_slot(schema, payload):
    """(index, Record) of the paired phone's record in a 0x1100 reply, or (None, None).
    The Pods record of type 6 names it by DeviceId; failing that, the record whose DeviceId
    carries the phone service id in its low 16 bits."""
    _prefix, entries = S._split_entries(payload)
    wl_id, pods_id = _group_id(schema, WHITELIST_FIELD), _group_id(schema, PODS_FIELD)
    records = [data for eid, data in entries if eid == wl_id]
    wanted = None
    for eid, data in entries:
        if eid == pods_id and len(data) >= 5 and data[0] == PHONE_POD_TYPE:
            wanted = struct.unpack_from("<I", data, 1)[0]
    for i, data in enumerate(records):
        try:
            rec = Record(data)
        except ValueError:
            continue
        if (wanted is not None and rec.device_id == wanted) or \
                (wanted is None and rec.device_id & 0xFFFF == PHONE_ID_LOW):
            return i, rec
    return None, None


def fixed_record(rec, address, rand=os.urandom):
    out = Record(rec.to_bytes())
    out.addr_class = 0
    out.mac = watch_order(address)
    out.irk = new_irk(rand)
    return out


def plan(schema, payload, address, rand=os.urandom):
    """What a fix would write: {"state": ..., "slot", "payload"}. States: no_phone (no phone
    pairing on the watch), fine (the record already has an identity), fix (payload set)."""
    slot, rec = phone_slot(schema, payload)
    if rec is None:
        return {"state": "no_phone"}
    if not needs_fix(rec):
        return {"state": "fine", "slot": slot}
    new = fixed_record(rec, address, rand)
    out, omitted = S.build_write_payload(schema, payload, TEMPLATE,
                                         patches=[(_group_id(schema, WHITELIST_FIELD), slot,
                                                   new.to_bytes())])
    if omitted:
        # A record left out of the write could be lost on the watch: refuse rather than guess.
        raise ValueError(f"cannot rewrite the pairing list as is: {', '.join(omitted)}")
    return {"state": "fix", "slot": slot, "payload": out, "record": new}


def check_after(schema, before, after, slot, new):
    """None when the re-read shows exactly the planned change: the phone record now holds the
    new address and IRK, its keys unchanged, every other whitelist and Pods record unchanged."""
    _p, b_entries = S._split_entries(before)
    _p, a_entries = S._split_entries(after)
    wl_id, pods_id = _group_id(schema, WHITELIST_FIELD), _group_id(schema, PODS_FIELD)
    b_wl = [d for e, d in b_entries if e == wl_id]
    a_wl = [d for e, d in a_entries if e == wl_id]
    if len(b_wl) != len(a_wl):
        return "the number of pairing records changed"
    if [d for e, d in b_entries if e == pods_id] != [d for e, d in a_entries if e == pods_id]:
        return "the Pods list changed"
    for i, (b, a) in enumerate(zip(b_wl, a_wl)):
        if i == slot:
            got, was = Record(a), Record(b)
            if (got.mac.lower(), got.irk.lower(), got.addr_class) != \
                    (new.mac.lower(), new.irk.lower(), 0):
                return "the watch did not take the new address and IRK"
            if (got.key, got.rnd, got.tail, got.device_id) != \
                    (was.key, was.rnd, was.tail, was.device_id):
                return "the watch's key changed"
        elif a != b:
            return f"pairing record {i} changed"
    return None


def run(link, schema, address, write, wait=time.sleep, tries=3, pause_s=10.0):
    """Check, and with `write` fix, the phone record. A watch that has just stored a pairing
    record answers no settings write for a few seconds (emulator, 2026-11-25: no reply 2 s
    after the pairing, a reply after 30 s), so an unanswered write is followed by a re-read:
    the change counts only if the watch shows it, and is sent again (up to `tries` times)
    while the watch still shows the old record."""
    before = link.command(S.CMD_SETTINGS_READ, b"\0\0\0\0")
    step = plan(schema, before, address)
    result = {"ok": True, "state": step["state"], "slot": step.get("slot"), "written": False}
    if step["state"] != "fix" or not write:
        return result
    problem = "the watch did not answer"
    for attempt in range(tries):
        if attempt:
            wait(pause_s)
        try:
            link.command(S.CMD_SETTINGS_WRITE, step["payload"])
        except (RuntimeError, TimeoutError, OSError):
            wait(pause_s)                      # unanswered: did it land anyway? Read and see.
        try:
            after = link.command(S.CMD_SETTINGS_READ, b"\0\0\0\0")
        except (RuntimeError, TimeoutError, OSError):
            continue
        problem = check_after(schema, before, after, step["slot"], step["record"])
        if problem is None:
            break
        _slot, now = phone_slot(schema, after)
        if now is None or not needs_fix(now):
            break                              # something else changed: never write again blind
    result.update(written=problem is None, ok=problem is None, error=problem)
    return result


class AfterPairing:
    """When the backend runs the fix by itself (Linux). One small JSON file remembers a pairing
    still to complete, so it survives a restart of Sommet:

    - paired(): Sommet's code box was answered for a watch. From then on that pairing is
      "pending", with this computer's adapter address (public addresses only: a random one
      changes, and the watch could not find the computer by it anyway).
    - Over Bluetooth: in a session of that same watch (BlueZ device), SETTLE_S after the
      handshake, at most TRIES_PER_SESSION tries RETRY_S apart. The Bluetooth jobs queue
      (the backend's watch lock) keeps it out of a sync or a write.
    - Over the cable: when the watch with the pending pairing's serial (known from its first
      Bluetooth session) is plugged in; again at most every CABLE_RETRY_S.
    - "fine" (the watch already has an identity: privacy was on, or done before) and
      "no_phone" end it as well as a successful write. A failure keeps it pending and
      failed() true, which Home shows. Forget ends it.

    `run(address, serial, over_ble)` does one try and returns ble_bond_fix's result dict.
    Nothing here logs an address, a serial or a key."""

    SETTLE_S = 8.0
    RETRY_S = 45.0
    TRIES_PER_SESSION = 2
    CABLE_RETRY_S = 600.0

    def __init__(self, path, run, log=lambda message: None, clock=time.monotonic):
        self.path = pathlib.Path(path)
        self.run = run
        self.log = log
        self.clock = clock
        self.lock = threading.Lock()
        self.busy = False
        self.session = None          # clock() when this watch's Bluetooth session was first seen
        self.session_tries = 0
        self.last_try = None
        self.last_cable_try = None
        self.pending = self._load()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        p = data.get("pending") if isinstance(data, dict) else None
        if isinstance(p, dict) and p.get("device") and ADDRESS.match(p.get("address") or ""):
            return p
        return None

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".new")
            tmp.write_text(json.dumps({"pending": self.pending}), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            self.log(f"pairing fix: could not save its state ({exc.__class__.__name__})")

    def paired(self, device, address, address_type=None):
        """Sommet's code box was answered for `device` (a BlueZ device path or its last part)."""
        dev = (device or "").split("/")[-1]
        if not dev:
            return
        with self.lock:
            if address_type not in (None, "public") or not ADDRESS.match(address or ""):
                self.pending = None
                self.log("pairing fix: this computer's Bluetooth address is not a fixed one, "
                         "so the watch's pairing record cannot name it (Settings: Keep "
                         "Bluetooth privacy on is the way for this computer)")
            else:
                self.pending = {"device": dev, "address": address, "serial": None,
                                "tries": 0, "error": None}
                self.log("pairing fix: a new pairing; the watch's pairing record is completed "
                         "in its first session")
            self.session, self.session_tries, self.last_try, self.last_cable_try = \
                None, 0, None, None
            self._save()

    def forgotten(self):
        with self.lock:
            if self.pending is not None:
                self.pending = None
                self._save()
            self.session = None

    def failed(self):
        p = self.pending
        return bool(p and p.get("error"))

    def due_over_ble(self, status, usb_present):
        """Looks at the helper's status (polled by the app). True when a try over the
        Bluetooth link should start now. Never while a watch is on the cable: a tool would then
        talk to that watch instead (the backend's own rule, server.use_ble)."""
        p = self.pending
        if p is None:
            return False
        linked = bool(status.get("handshake_done")) and status.get("linked_device") == p["device"]
        if not linked:
            self.session = None
            return False
        serial = (status.get("device_info") or {}).get("serial")
        if serial and p.get("serial") != serial:
            with self.lock:
                if self.pending is p:
                    p["serial"] = serial
                    self._save()
        now = self.clock()
        if self.session is None:
            self.session, self.session_tries, self.last_try = now, 0, None
        if self.busy or usb_present:
            return False
        if now - self.session < self.SETTLE_S or self.session_tries >= self.TRIES_PER_SESSION:
            return False
        return self.last_try is None or now - self.last_try >= self.RETRY_S

    def due_over_cable(self, watches):
        """The serial to fix over the cable now, or None. `watches`: list_watches.py entries."""
        p = self.pending
        if p is None or self.busy or not p.get("serial"):
            return None
        if not any(isinstance(w, dict) and w.get("serial") == p["serial"] for w in watches or []):
            return None
        now = self.clock()
        if self.last_cable_try is not None and now - self.last_cable_try < self.CABLE_RETRY_S:
            return None
        return p["serial"]

    def try_now(self, over_ble, serial=None):
        """One try (blocks: run it on a thread of its own). None when nothing was tried."""
        with self.lock:
            p = self.pending
            if p is None or self.busy:
                return None
            self.busy = True
            if over_ble:
                self.session_tries += 1
                self.last_try = self.clock()
            else:
                self.last_cable_try = self.clock()
        how = "over Bluetooth" if over_ble else "over the cable"
        try:
            result = self.run(p["address"], serial, over_ble)
        except Exception as exc:                                  # noqa: BLE001 - kept, said
            # The class only: a subprocess error's text holds the command line (the address).
            result = {"ok": False, "error": f"it stopped ({exc.__class__.__name__})"}
        result = result if isinstance(result, dict) else {"ok": False, "error": "no answer"}
        with self.lock:
            self.busy = False
            if self.pending is not p:                 # forgotten or paired again meanwhile
                return result
            if result.get("ok"):
                self.pending = None
                self.log({"fix": f"pairing fix: done {how}, the watch's pairing record names "
                                 "this computer now",
                          "fine": "pairing fix: not needed, the watch already knows this "
                                  "computer",
                          "no_phone": "pairing fix: the watch holds no app pairing; nothing "
                                      "to do"}.get(result.get("state"),
                                                   f"pairing fix: finished {how}"))
            else:
                p["tries"] = int(p.get("tries") or 0) + 1
                p["error"] = str(result.get("error") or "no answer")[:200]
                self.log(f"pairing fix: try {p['tries']} {how} did not work: {p['error']}")
            self._save()
        return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--address", required=True, help="this computer's Bluetooth adapter address")
    ap.add_argument("--write", action="store_true", help="send the fix (default: only check)")
    ap.add_argument("--json", action="store_true", help="one JSON line")
    args = ap.parse_args(argv)
    if not ADDRESS.match(args.address):
        print(json.dumps({"ok": False, "error": "not a Bluetooth address"}))
        return 2

    from write_nav import Link, schema_for_link             # noqa: PLC0415
    try:
        link = Link(dry_run=False, verbose=False)
        link.open()
        link.command(S.CMD_DEVICE_INFO, b"\x02\x48\x03\x00")   # SuuntoLink's opener, see settings_write
        descriptor = schema_for_link(link)
        if not descriptor.exists():
            print(json.dumps({"ok": False,
                              "error": f"missing schema descriptor: {descriptor.name}"}))
            return 1
        result = run(link, sbem_schema.load(descriptor), args.address, args.write)
    except ValueError as exc:
        result = {"ok": False, "error": str(exc)}
    except (RuntimeError, TimeoutError, OSError) as exc:
        # A watch that is gone or busy: said in one line, never a traceback with link details.
        result = {"ok": False, "error": f"the watch did not answer ({exc.__class__.__name__})"}
    print(json.dumps(result) if args.json else
          "  ".join(f"{k}={v}" for k, v in result.items()))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
