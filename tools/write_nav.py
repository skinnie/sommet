#!/usr/bin/env python3
"""Writes the navigation database to an Ambit3, reads its settings, or simulates.

DRY-RUN BY DEFAULT for the two actions that modify the watch: without --write
nothing is emitted, only the exact bytes are logged. A malformed body can reboot or
hang the watch.

    ./tools/write_nav.py reset
    ./tools/write_nav.py route GPX [GPX...] --meta CAPTURE
    ./tools/write_nav.py reset --write         # actually emits

    ./tools/write_nav.py settings              # READ-ONLY, needs the cable
    ./tools/write_nav.py settings --from CAPTURE   # decodes a capture, no watch

The GPX order is the descriptor order: most recently modified first.

Distance, ascent and descent are worked out from each GPX (build_route.route_from_gpx,
hardware-checked 2026-10-27); the timestamp stays neutral. --meta takes all four from a
capture instead.

`settings` never writes: it sends the 0x1100 query, four zero bytes, which is what
SuuntoLink sends on every connection, and decodes the reply. Its point is
WhitelistedBleDevices, the watch's BLE pairing bond, which is the first step of
milestone 7 in HANDOFF.md.
"""

import argparse
import json
import os
import pathlib
import sys
from xml.sax.saxutils import escape as xml_escape

import ambit_format as F
from ambit_pcap import CMD_NAMES, FlashImage, encode_message, messages, write_packs
from build_route import emit_packs, route_from_gpx, serialize, stamp_from_capture

VENDOR_ID = 0x1493
PRODUCT_IDS = {
    0x001B: "Ambit3 Peak (Emu)", 0x001C: "Ambit3 Sport (Finch)",
    0x001E: "Ambit3 Run (Ibisbill)", 0x002C: "Ambit3 Vertical (Kaka)",
    0x002B: "Traverse (Jabiru)", 0x002D: "Traverse Alpha (Loon)",
    # Real, 2026-08-08: confirmed via `lsusb` against André's actual connected watch
    # ("ID 1493:002a Suunto Kailash") while testing kailash_tracklog.py/kailash_eventlog.py -
    # missing here meant Link.open() raised "no Ambit3 on the USB bus" even with the real
    # device plugged in, since hid.enumerate() only ever looked at the product IDs above.
    0x002A: "Kailash (Hoopoe)",
    # Real, 2026-08-22: confirmed live against André's actual Ambit1 (serial
    # 1614984607001600, fw 2.5.7.0) - CMD_DEVICE_INFO (0x0000) and CMD_STATUS (0x0306) are
    # byte-for-byte openambit's shared device_driver_common.c commands (device_info.py's own
    # header already said so), common to the WHOLE family including the pre-SBEM Ambit1/2,
    # not just Ambit3+ - only PRODUCT_IDS was missing these, hid.enumerate() never looked for
    # them. Confirmed the SBEM object-model commands (settings 0x1100, memory map 0x0b21,
    # POIs 0x0b24) do NOT extend to Ambit1: they return a 0-byte reply, not an error - real
    # ambit1/2 settings/waypoints/logs need the legacy PMEM 2.0 protocol instead, which this
    # file does not implement (see tools/legacy_link.py). Ambit2 product IDs are added
    # alongside Bluebird on the same evidence (shared device_driver_ambit driver, device_support.c).
    0x0010: "Ambit (Bluebird)", 0x0019: "Ambit2 (Duck)",
    0x001A: "Ambit2 S (Colibri)", 0x001D: "Ambit2 R (Greentit)",
    # Generation groups, used by the workout-install poka-yoke (2026-09-02): the two watch
    # families take DIFFERENT paths to on-watch guided training, and mixing them silently
    # produces a workout that installs cleanly yet never appears. Ambit1/2 have no native
    # WORKOUT menu at all - a "workout" there is an ordinary App the user runs (legacy path).
    # Ambit3 (Peak/Sport/Run/Vertical) DO have the [Next]-3s -> WORKOUT menu, and a guided
    # workout MUST go in via guided_workout.py (Apps entry byte0=1 + guidance display), NOT
    # via workout_install.py's display-field app mechanism - the latter installs fine but the
    # workout never lists (hardware-confirmed the hard way, a whole session of failed writes).
    # Real, 2026-08-22, live on André's own Ambit1: unlike the Ambit3/Kailash family (BSL
    # keeps the app's own product_id, only the 0x0000 model STRING flips to "BSL"),
    # Bluebird's bootloader re-enumerates under its OWN distinct product_id - real lsusb
    # output: "ID 1493:0011 Suunto AmbitBSL". Needed here (not just firmware_write.py's own
    # LEGACY_BSL_PID map) because Link.open() looks up any explicit product_id in this table
    # unconditionally for its display label - see firmware_write.py's poll_pid_reopen().
    # Deliberately no "bluebird" substring anywhere in this label - resolve_product_id()'s
    # --device matching is a plain case-insensitive substring test, and anything containing
    # "bluebird" makes "--device Bluebird" ambiguous against 0x0010 above the instant a
    # watch is (however briefly) sitting in its bootloader. codename_for_pid() isn't used
    # for this pid by anything real, so the mismatch with the "(codename)" convention here
    # is deliberate, not an oversight.
    0x0011: "Ambit Bootloader (AmbitBSL)",
}


AMBIT12_PRODUCT_IDS = frozenset({0x0010, 0x0019, 0x001A, 0x001D})  # Ambit(1) + Ambit2 family
AMBIT3_PRODUCT_IDS = frozenset({0x001B, 0x001C, 0x001E, 0x002C})   # Peak/Sport/Run/Vertical


def is_ambit12(pid):
    """True for an Ambit1/Ambit2-generation watch (no native WORKOUT menu - guided training is
    an ordinary App the user runs). See PRODUCT_IDS' generation-groups note."""
    return pid in AMBIT12_PRODUCT_IDS


def is_ambit3(pid):
    """True for an Ambit3-generation watch (Peak/Sport/Run/Vertical) - the family with the
    [Next]-3s -> WORKOUT menu that guided_workout.py targets."""
    return pid in AMBIT3_PRODUCT_IDS


def codename_for_pid(pid):
    """The model codename ('Emu', 'Finch', 'Hoopoe' ...) for a USB product_id, from the
    PRODUCT_IDS label ('Ambit3 Peak (Emu)' -> 'Emu'). This is how a watch in the bootloader
    is identified: its device_info model string reads "BSL", but its USB product_id stays
    model-specific, so the codename (and thus the right firmware) is still known. Returns
    None for an unknown pid."""
    label = PRODUCT_IDS.get(pid)
    if label and "(" in label:
        return label.rsplit("(", 1)[1].rstrip(")")
    return None


def resolve_product_id(name):
    """`--device NAME` -> a PRODUCT_IDS key, or raises SystemExit with the real candidate
    list if NAME matches none or more than one (an ambiguous match picking the wrong watch
    silently would defeat the entire point of this flag)."""
    name_lower = name.lower()
    matches = [pid for pid, label in PRODUCT_IDS.items() if name_lower in label.lower()]
    if len(matches) == 1:
        return matches[0]
    labels = ", ".join(f"{v!r}" for v in PRODUCT_IDS.values())
    if not matches:
        raise SystemExit(f"--device {name!r} matches none of: {labels}")
    raise SystemExit(f"--device {name!r} is ambiguous, matches more than one of: {labels}")


CMD_DEVICE_INFO = 0x0000
CMD_SETTINGS_READ = 0x1100
CMD_MEMORY_MAP = 0x0B21
CMD_DATA_WRITE = 0x0B16
CMD_DATA_TAIL = 0x0B18
CMD_NAV_COMMIT = 0x0B04
CMD_POI_READ = 0x0B24
CMD_POI_WRITE = 0x0B25
CMD_POI_COUNT = 0x0B02   # "waypoint_count": on the Ambit3 it counts the POI store
CMD_LOG_HEADERS = 0x1200
CMD_FLASH_READ = 0x0B17
FLASH_CHUNK = 1024

# 0x1200 asks for an object by identifier, unlike 0x1100 and 0x0b24 which take four zero
# bytes and return everything. Here: sml.DeviceLogBook, entry 0x8d, empty.
LOGBOOK_REQUEST = (bytes.fromhex("00000000") + (1).to_bytes(2, "little")
                   + (10).to_bytes(2, "little") + b"SBEM0102" + bytes([0x8D, 0x00]))

# The three read-only queries: command, request payload, and the entries worth printing
# when --all is not given.
QUERIES = {
    "settings": (CMD_SETTINGS_READ, b"\0\0\0\0", (0x41, 0x43)),
    "pois": (CMD_POI_READ, b"\0\0\0\0", (0x55,)),
    "logbook": (CMD_LOG_HEADERS, LOGBOOK_REQUEST, (0x59, 0x5A, 0x8A)),
}

POI_ENTRY = 0x55
# Most bytes in one 0x55 entry written to the watch: its length stays one byte, since 0xFF
# announces the extended header, which corrupted the POI store when written (2026-08-30).
POI_ENTRY_MAX = 0xFE
# Prefix of an SBEM payload sent to the watch, as against 0x...0100 on a reply.
SBEM_WRITE_PREFIX = bytes.fromhex("000000000101")

# Entries of the DeviceSettings tree worth calling out, see tools/sbem_schema.py.
BLE_WHITELIST_ENTRY = 0x41
POD_ENTRY = 0x43

# Fields that are key material or identify a phone. --redact replaces their value with
# a length and a short digest: still enough to tell two reads apart or match them, not
# enough to use the key. Report a bond with --redact rather than retyping the values.
SECRET_FIELDS = ("MAC", "IdentityResolvingKey", "EncodingKey", "EncodingRnd")


def show_value(name, value, redacted):
    if not (redacted and value and name in SECRET_FIELDS):
        return repr(value)
    import hashlib
    text = str(value)
    return (f"<{len(text.split(':'))} bytes, "
            f"sha256:{hashlib.sha256(text.encode()).hexdigest()[:8]}>")


def open_hid(hid, path):
    """Two different modules import as `hid`, both are packaged, and their APIs differ.

    PyPI `hid`, ctypes bindings, exposes `Device(path=...)`. cython-hidapi, which Debian
    and Mint ship as `python3-hid`, exposes `device()` plus `open_path()` and no `Device`
    at all. Accept either, so that a plain `apt install python3-hid` is enough and nobody
    has to be told which packaging to prefer.

    `enumerate()` is identical in both, and `read(size, timeout)` agrees as long as the
    timeout stays positional: the keyword is `timeout` in one and `timeout_ms` in the
    other. `read()` returns bytes in one and a list of ints in the other, which is why
    every slice of a reply is wrapped in `bytes()`.
    """
    if hasattr(hid, "Device"):
        return hid.Device(path=path)
    device = hid.device()
    device.open_path(path)
    return device


class _BleHandle:
    """Stands in for the HID device when Link talks over Bluetooth: tools that only check
    `link.device is not None` or close it keep working; raw HID access is refused (firmware
    flashing never runs over Bluetooth)."""

    def close(self):
        pass

    def read(self, *_a, **_k):
        raise RuntimeError("raw HID reads are not available over Bluetooth")

    def write(self, *_a, **_k):
        raise RuntimeError("raw HID writes are not available over Bluetooth")


class Link:
    """HID transport (or, with AMBIT_TRANSPORT=ble, the Bluetooth daemon). In dry-run no
    device is opened."""

    def __init__(self, dry_run=True, verbose=False, product_id=None, serial=None):
        self.dry_run = dry_run
        self.verbose = verbose
        # Real, 2026-08-08: with an Ambit3 and a Kailash plugged in at once (a real, ongoing
        # setup this session, not hypothetical), open()'s old "try every known product_id,
        # take whichever opens first" always landed on whichever PRODUCT_IDS lists earlier -
        # silently the wrong watch for anything that needs to target one specifically. None
        # (the default) keeps that exact prior behavior; pass a specific key from
        # PRODUCT_IDS to open only that one even with others also connected.
        #
        # AMBIT_PRODUCT_ID env var (2026-08-16): the desktop backend sets this from the watch
        # the user picked in the Home watch-switcher, so EVERY tool targets that one watch
        # without each endpoint having to thread a --product-id flag. An explicit product_id
        # argument still wins; the env var is only the default when none was passed.
        if product_id is None:
            env_pid = os.environ.get("AMBIT_PRODUCT_ID")
            if env_pid:
                try:
                    parsed = int(env_pid, 0)
                    if parsed in PRODUCT_IDS:
                        product_id = parsed
                except ValueError:
                    pass
        self.product_id = product_id
        # AMBIT_SERIAL env / serial arg (2026-09-04): pick ONE physical watch by its USB serial
        # number, so two watches of the SAME model (two Ambit3 Peaks, same product_id 0x001b)
        # can be told apart and driven individually. Product_id alone can't distinguish them.
        # An explicit serial argument wins over the env var; None means "don't filter by serial"
        # (prior behavior - first matching product_id wins).
        if serial is None:
            env_serial = os.environ.get("AMBIT_SERIAL")
            if env_serial:
                serial = env_serial.strip()
        self.serial = serial or None
        self.opened_product_id = None  # set by open() to the pid that actually opened
        self.opened_serial = None      # set by open() to the serial that actually opened
        self.sequence = 0
        self.device = None
        self.sent = []
        # AMBIT_TRANSPORT=ble (2026-10-27, 🟣79): the desktop backend sets this for the Ambit3
        # sport-mode / app tools when the watch is on Bluetooth and not on the cable. open() then
        # talks to the Bluetooth daemon's control socket (desktop/backend/ble_bridge.py) instead
        # of USB HID; every command() goes through it. Same tool code, other transport.
        self.ble = os.environ.get("AMBIT_TRANSPORT") == "ble"
        self._bridge = None

    def open(self):
        """Listing a USB device needs no privilege, opening it does, so the two failures
        are told apart: nothing plugged in is not the same problem as a device present
        and unopenable.

        Both possible `hid` module backings (see open_hid()'s own docstring) go through
        libusb, not the kernel hidraw interface - real permission for either is decided by
        `/dev/bus/usb/<bus>/<device>`'s own mode, not any hidraw udev rule (a rule for
        `SUBSYSTEM=="hidraw"` has zero effect on this transport - confirmed 2026-08-07,
        see V3_CHANGELOG.md). On most desktop distros that node's permission is granted
        dynamically per logged-in session (systemd-logind's "uaccess" ACL tagging) right
        after the kernel creates it on plug-in - there is a real, short race between the
        node existing and that tag being applied, so a reconnect can transiently look like
        a permissions failure and clear up half a second later on its own. Retried for
        exactly that reason, not papering over a real, persistent failure: if it's still
        unopenable after retrying, something else is actually wrong.
        """
        if self.dry_run:
            return None
        if self.ble:
            return self._open_ble()
        import hid  # imported only when a device is really opened
        import time

        wanted = ({self.product_id: PRODUCT_IDS[self.product_id]} if self.product_id is not None
                  else PRODUCT_IDS)

        attempts = 5
        delay_s = 0.4
        failures = []
        for attempt in range(1, attempts + 1):
            found = [(entry, label, product_id) for product_id, label in wanted.items()
                     for entry in hid.enumerate(VENDOR_ID, product_id)]
            # Narrow to ONE physical watch by serial when asked (two same-model watches on the
            # bus). hid.enumerate() reports each device's serial_number; match it exactly.
            if self.serial is not None:
                found = [f for f in found if (f[0].get("serial_number") or "") == self.serial]
            if not found:
                if attempt < attempts:
                    time.sleep(delay_s)
                    continue
                if self.product_id is not None:
                    raise RuntimeError(
                        f"no {PRODUCT_IDS[self.product_id]} on the USB bus (looked only for "
                        f"product_id 0x{self.product_id:04x} - other Suunto watches may still "
                        "be connected). Check the cable and that `lsusb` lists it.")
                # "Suunto watch", not "Ambit3": PRODUCT_IDS spans the whole supported
                # family (Ambit/Ambit2/Ambit3/Traverse/Kailash), and André hit this
                # message mid-watch-swap on 2026-08-12 reading it as "the app is still
                # looking for the Ambit" while his Kailash was the one plugged in.
                raise RuntimeError(
                    "no supported Suunto watch on the USB bus. Check the cable, then "
                    "that `lsusb` lists a device whose id starts with 1493:")
            failures = []
            for entry, label, product_id in found:
                try:
                    self.device = open_hid(hid, entry["path"])
                except Exception as exc:  # every backend raises its own type here
                    failures.append(f"{entry['path']!r}: {exc}")
                    continue
                # Which product_id actually opened - the model identity survives even in the
                # bootloader (a BSL watch keeps its model-specific USB product_id, e.g. Emu
                # stays 0x001b, only its device_info string becomes "BSL"), so this is how a
                # bricked watch is identified for recovery. See codename_for_pid().
                self.opened_product_id = product_id
                self.opened_serial = entry.get("serial_number")
                print(f"  watch: {label}")
                return label
            if attempt < attempts:
                time.sleep(delay_s)
        raise RuntimeError(
            f"{len(failures)} Suunto watch(es) on the USB bus, still not openable after {attempts} "
            f"tries over {attempts * delay_s:.1f}s (past the usual reconnect race).\n"
            # Most often another program holds it (2026-10-22: Sommet's own backend, while a
            # Linux Mint user's Workout Builder tried to install) - say that before permissions.
            "  Most likely another program has the watch open: Sommet (its backend keeps hold "
            "of it), SuuntoLink, or another tool. Close it, or install from inside Sommet, "
            "and try again.\n"
            "  If nothing else is running, check permissions with:\n"
            "    lsusb | grep 1493   # confirm the device and its bus/device numbers\n"
            "    ls -la /dev/bus/usb/<bus>/<device>   # from lsusb above\n"
            "  Should read crw-rw-rw- (or at least rw for your user) shortly after "
            "plugging in - if it's still root-only\n  after a few seconds, your "
            "distro's systemd-logind uaccess rules aren't tagging this device; that's "
            "a\n  session/seat issue, not something a udev MODE rule for hidraw can "
            "fix (this transport doesn't use hidraw).\n"
            "  The backend said: " + "; ".join(failures))

    def _open_ble(self):
        """The Bluetooth side of open(): a client of the running daemon, refused unless the
        watch on the Bluetooth link is the one this call was pinned to (model and serial) - a
        write must never land on another watch than the one picked."""
        try:
            import ble_bridge                                # frozen build: bundled
        except ImportError:
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "desktop" / "backend"))
            import ble_bridge                                # noqa: PLC0415
        from ble_settings import product_id_from_model       # noqa: PLC0415
        bridge = ble_bridge.BleBridge()                      # client only: the backend owns the daemon
        status = bridge.status()
        if not status.get("handshake_done"):
            raise RuntimeError("the watch isn't connected over Bluetooth")
        info = status.get("device_info") or {}
        pid = product_id_from_model(info.get("model"))
        if self.product_id is not None and pid is not None and pid != self.product_id:
            raise RuntimeError(f"the watch on Bluetooth is a {PRODUCT_IDS.get(pid, info.get('model'))}, "
                               f"not the selected {PRODUCT_IDS.get(self.product_id)}")
        if self.serial and info.get("serial") and info["serial"] != self.serial:
            raise RuntimeError(f"the watch on Bluetooth is serial {info['serial']}, "
                               f"not the selected {self.serial}")
        self._bridge = bridge
        self.device = _BleHandle()
        self.opened_product_id = pid
        self.opened_serial = info.get("serial")
        label = PRODUCT_IDS.get(pid, info.get("model") or "watch")
        print(f"  watch: {label} (Bluetooth)")
        return label

    def command(self, command, payload=b"", expect_reply=True, quiet=False):
        if self._bridge is not None:
            name = CMD_NAMES.get(command, f"0x{command:04x}")
            if not quiet:
                print(f"  -> 0x{command:04x} {name:22} {len(payload):5} B  (Bluetooth)")
            self.sequence += 1
            reply = self._bridge.command(command, payload, expect_reply=expect_reply, timeout=30.0)
            self.sent.append((command, payload, reply))
            return reply if expect_reply else b""
        reports = encode_message(command, payload, self.sequence)
        name = CMD_NAMES.get(command, f"0x{command:04x}")
        if not quiet:
            print(f"  {'[dry-run] ' if self.dry_run else ''}-> 0x{command:04x} "
                  f"{name:22} {len(payload):5} B  {len(reports)} report(s)")
        if self.verbose:
            for report in reports:
                print(f"        {report.hex(' ')}")
        self.sent.append((command, payload, reports))
        self.sequence += 1
        if self.dry_run or not expect_reply:
            return b""
        for report in reports:
            self.device.write(report)
        return self._read_reply()

    def _read_reply(self):
        """Reassembles a reply, 42 payload bytes at +20 in the first report then 54 at
        +8. Loops on the announced total rather than on the part count of the header,
        as ambit_pcap.messages() does: a 0x1100 reply is 589 bytes over 12 reports."""
        import struct

        head = self.device.read(64, 20000)  # positional: see open_hid()
        if not head or head[0] != 0x3F:
            raise RuntimeError("no reply from the watch")
        total, = struct.unpack("<I", bytes(head[16:20]))
        body = bytearray(total)
        body[:min(42, total)] = head[20:20 + min(42, total)]
        # Each continuation report says which part it is (u16 at +4) and is placed there, not
        # appended in arrival order. Real, 2026-10-03 (Ambit3 Sport): a 1024 B CustomModes read
        # came back with two of its reports swapped; a read-modify-write (guided_workout.py
        # --append) trusted it and wrote the Transition mode back with 2 displays missing.
        got, seen = min(42, total), set()
        while got < total:
            more = self.device.read(64, 20000)
            if not more:
                raise RuntimeError(f"truncated reply: {got}/{total} bytes")
            part, = struct.unpack("<H", bytes(more[4:6]))
            offset = 42 + (part - 1) * 54
            if more[2] != 0x5E or part < 1 or offset >= total or part in seen:
                raise RuntimeError(f"unexpected reply report (type 0x{more[2]:02x}, part {part})")
            seen.add(part)
            chunk = min(54, total - offset)
            body[offset:offset + chunk] = more[8:8 + chunk]
            got += chunk
        return bytes(body)


def read_flash(link, address, size, label=""):
    """Reads a flash region through 0x0b17: [u32 address][u32 length] out, the same eight
    bytes then the data back, 1024 at a time as SuuntoLink does in `ambit3full`.

    This is the read path `HANDOFF.md` wanted for milestone 4 and never had. It is what
    makes a backup possible, and it is self-checking: the region carries its own CRC.
    """
    import struct

    out = b""
    while len(out) < size:
        want = min(FLASH_CHUNK, size - len(out))
        reply = link.command(CMD_FLASH_READ,
                             struct.pack("<II", address + len(out), want), quiet=True)
        if len(reply) < 8:
            raise RuntimeError(f"0x0b17 at 0x{address + len(out):06x}: short reply")
        got_address, got_size = struct.unpack("<II", reply[:8])
        if (got_address, got_size) != (address + len(out), want):
            raise RuntimeError(
                f"0x0b17 asked 0x{address + len(out):06x}/{want}, "
                f"got 0x{got_address:06x}/{got_size}")
        out += reply[8:8 + got_size]
        print(f"\r  {label} {len(out)}/{size} B", end="", flush=True)
    print()
    return out


def show_navigation(flash):
    """Decodes the navigation database read off the watch, with the structures the
    serializer already uses. The CRCs make the read self-validating: they cover the
    descriptors and the points, so if they match, the bytes came back intact."""
    header = F.RouteHeader.parse(flash.read(F.ROUTE_BASE, 32))
    waypoint_header = F.WaypointHeader.parse(flash.read(F.WAYPOINT_BASE, 6))
    routes, points = header.route_count, header.point_count
    print(f"  routes {routes}   points {points}   waypoints {waypoint_header.count}")

    if header.magic != F.ROUTE_HEADER_MAGIC:
        print(f"  !! route header magic 0x{header.magic:04x}, expected "
              f"0x{F.ROUTE_HEADER_MAGIC:04x}")
        return False, False

    descriptors = flash.read(F.ROUTE_DESC, 52 * routes)
    body = flash.read(F.ROUTE_POINTS, 12 * points)
    # An empty database carries a literal zero rather than the CRC of nothing, which is
    # what the reset plan writes and what routedelete shows.
    crc = F.crc16_ccitt_false(descriptors + body) if routes else 0
    wsize = waypoint_header.size          # 52, or 54 on a Traverse (F.WAYPOINT_SIZE_ALT)
    wpt_blob = flash.read(F.WAYPOINT_DESC, wsize * waypoint_header.count)
    wpt_crc = F.crc16_ccitt_false(wpt_blob)
    print(f"  {'OK   ' if crc == header.checksum else 'FAIL '} route CRC "
          f"0x{crc:04x} against 0x{header.checksum:04x}"
          + ("  (empty database, a literal zero)" if not routes else ""))
    print(f"  {'OK   ' if wpt_crc == waypoint_header.checksum else 'FAIL '} waypoint CRC "
          f"0x{wpt_crc:04x} against 0x{waypoint_header.checksum:04x}")

    for i in range(routes):
        d = F.RouteDescriptor.parse(flash.read(F.ROUTE_DESC + 52 * i, 52))
        e = F.RouteIndexEntry.parse(flash.read(F.ROUTE_INDEX + 20 * i, 20))
        section = [F.RoutePoint.parse(flash.read(F.ROUTE_POINTS + 12 * k, 12))
                   for k in range(d.start_index, d.start_index + d.point_count)]
        alt = [q.altitude for q in section if q.altitude != F.ALTITUDE_NONE]
        print(f"  route[{i}] {d.name!r}  {d.point_count} points  {d.distance} m  "
              f"ascent {d.ascent} descent {d.descent}  waypoints {e.waypoint_count}")
        print(f"           altitude " + (f"{min(alt)} to {max(alt)} m on "
                                         f"{len(alt)}/{len(section)} points"
                                         if alt else "absent on every point"))
    for i in range(waypoint_header.count):
        w = F.WaypointDescriptor.parse(flash.read(F.WAYPOINT_DESC + wsize * i, wsize))
        tail = F.WaypointTail.parse(w.tail)
        print(f"  waypoint[{i}] {w.name!r} route={w.route_name!r}  "
              f"{w.lat / 1e7:.7f}, {w.lon / 1e7:.7f}  type {tail.type} rank {tail.rank}")
    # Two independent integrity results, not one: routes and route-waypoints are separate
    # payloads with separate CRCs. The Traverse decodes routes perfectly (route CRC OK) but
    # its route-waypoint descriptor layout differs from the Ambit3 Peak, so the waypoint CRC
    # can fail while every route is valid. Returning them apart lets a routes-only consumer
    # (the Routes page) succeed on valid routes instead of a waypoint mismatch blanking them.
    return crc == header.checksum, wpt_crc == waypoint_header.checksum


def route_to_gpx(flash, index):
    """Full-point GPX export of one on-watch route - real feature, 2026-08-07, matching the
    confirmed-working capability already shipped in the real Android app
    (opensportsync-main's NavigationService.ts route export - "Real GPS-point export ... is
    a confirmed, working capability", RoutesPage.qml's own prior comment). Each on-watch
    point is stored as (x, y) metres relative to the route descriptor's own mid_lat/mid_lon
    (see build_routes()/serialize() in this same file for the forward direction) -
    ambit_format.inverse_xy() is that same already-tested projection run backwards, not new
    math. <rte>/<rtept>, not <trk>/<trkpt>: this project's own established convention for
    routes/waypoints versus recorded activity tracks (see exercise_log.py's to_gpx() for the
    latter)."""
    header = F.RouteHeader.parse(flash.read(F.ROUTE_BASE, 32))
    if not (0 <= index < header.route_count):
        raise ValueError(f"route index {index} out of range (0..{header.route_count - 1})")
    d = F.RouteDescriptor.parse(flash.read(F.ROUTE_DESC + 52 * index, 52))
    points = [F.RoutePoint.parse(flash.read(F.ROUTE_POINTS + 12 * k, 12))
              for k in range(d.start_index, d.start_index + d.point_count)]

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="ambit-app write_nav.py"'
        ' xmlns="http://www.topografix.com/GPX/1/1">',
        # Real bug, found live 2026-08-11 building existing_routes_as_gpx(): this
        # function's own output couldn't round-trip back through read_gpx() (used by
        # route_from_gpx()/build_routes() to re-import a route) because read_gpx()'s
        # META_NAME regex specifically looks for <metadata><name>, matching SuuntoLink's
        # own real export format - the <rte><name> below was never enough on its own,
        # despite being valid GPX. A standard, harmless addition - no existing reader of
        # this function's output (POST /api/routes/export) is hurt by an extra
        # <metadata> block - and it's what makes a read-modify-write cycle actually work.
        f'  <metadata><name>{xml_escape(d.name)}</name></metadata>',
        f'  <rte><name>{xml_escape(d.name)}</name>',
    ]
    coords = []
    for p in points:
        lat, lon = F.inverse_xy(d.mid_lat, d.mid_lon, p.x, p.y)
        coords.append((f"{lat:.7f}", f"{lon:.7f}"))
        ele = f'<ele>{p.altitude:.1f}</ele>' if p.altitude != F.ALTITUDE_NONE else ''
        lines.append(f'    <rtept lat="{lat:.7f}" lon="{lon:.7f}">{ele}</rtept>')
    lines.append('  </rte>')
    # The route's own waypoints travel with it (2026-10-06). Without them a re-send of this
    # export - which is how every "add a route" keeps the routes already on the watch - came
    # back with only a synthesized Start/End, silently dropping named waypoints such as the
    # direction pins of route_pins.py. Each is written at the exact coordinates of the route
    # point nearest to it, searching forward from the previous one (rank order), so
    # build_route's point match holds even on a loop whose start and end coincide.
    own = []
    route_key = F.decode_name(F.encode_name(d.name, "utf-8"))
    wh = F.WaypointHeader.parse(flash.read(F.WAYPOINT_BASE, 6))
    # Records are 52 bytes, 54 on a Traverse (the header's magic says which). Only when the
    # table decodes (its CRC matches): a table that doesn't would carry junk names along.
    ws = wh.size
    wpt_ok = F.crc16_ccitt_false(flash.read(F.WAYPOINT_DESC, ws * wh.count)) == wh.checksum
    for i in range(wh.count if wpt_ok else 0):
        w = F.WaypointDescriptor.parse(flash.read(F.WAYPOINT_DESC + ws * i, ws))
        if w.route_name == route_key:
            own.append((F.WaypointTail.parse(w.tail).rank, w))
    cursor, wpt_lines = 0, []
    for _, w in sorted(own, key=lambda t: t[0]):
        if not coords:
            break
        lat, lon = w.lat / 1e7, w.lon / 1e7
        best = min(range(cursor, len(coords)),
                   key=lambda k: (float(coords[k][0]) - lat) ** 2 + (float(coords[k][1]) - lon) ** 2)
        cursor = best
        wpt_lines.append(f'  <wpt lat="{coords[best][0]}" lon="{coords[best][1]}">'
                         f'<name>{xml_escape(w.name)}</name><type>Waypoint</type></wpt>')
    lines[3:3] = wpt_lines
    lines.append('</gpx>')
    return "\n".join(lines)


def existing_routes_as_gpx(link):
    """Every route currently on the watch, as GPX text - real safety fix, 2026-08-11.

    `build_routes()`/`send_plan()` do not merge with what is already on the watch: the
    "route" action rewrites the ENTIRE Routes region from exactly the GPX paths it is
    given, by design (the CLI's own usage, `route file1.gpx file2.gpx ...`, expects the
    caller to list every route it wants kept - that is the documented contract, not a
    bug). The desktop app's own upload flow (`server.py`'s `/api/routes`,
    `RouteService::uploadPendingRoute()`) never implemented that contract - it only ever
    sent the ONE newly-imported route, silently deleting every other route already on the
    watch. Confirmed as a real, live incident, 2026-08-11: uploading one test route over
    BLE wiped two of André's existing routes, over what turned out to be the exact same
    code path USB already used - not a BLE-specific bug, a pre-existing gap in the whole
    app's route-upload feature that had simply never been exercised against a watch that
    already had other real routes on it.

    Callers that want to ADD a route without losing what is already there must read this
    FIRST and pass its result alongside the new route to `build_routes()` - see
    `tools/ble_routes.py`'s `write_route()` for the reference caller."""
    flash = read_nav_flash(link)
    header = F.RouteHeader.parse(flash.read(F.ROUTE_BASE, 32))
    if header.magic != F.ROUTE_HEADER_MAGIC:
        return []                      # empty/uninitialized database, nothing to preserve
    return [route_to_gpx(flash, i) for i in range(header.route_count)]


def read_nav_flash(link):
    """Waypoints + Routes, read off the watch into a `FlashImage` - shared by
    `existing_routes_as_gpx()` and `ble_routes.read_nav_summary()` (server.py's own
    `/api/nav`, over BLE - real gap, found live 2026-08-11: the write side of routes got
    ported to BLE the same night this was added, but the LIST side - what RoutesPage.qml
    actually shows - was missed, so the page looked broken over BLE even though writes
    worked). Not the full `run_nav()` read (Apps/TrainingProgram/GpsSGEE excluded) - same
    "only what navigation actually needs" trim that function already applies."""
    waypoints = read_flash(link, F.WAYPOINT_BASE, F.REGIONS[F.WAYPOINT_BASE][1],
                           label="Waypoints")
    routes = read_flash(link, F.ROUTE_BASE, F.REGIONS[F.ROUTE_BASE][1], label="Routes")
    flash = FlashImage()
    flash.write(F.WAYPOINT_BASE, waypoints)
    flash.write(F.ROUTE_BASE, routes)
    return flash


def nav_summary_json(flash):
    """Every on-watch route's real points, in one shot from the *same* already-read flash
    data show_navigation() decodes from - real request 2026-08-08 ("add a map for each
    gpx"): the point-per-route decode this needs is exactly route_to_gpx()'s own (same
    inverse_xy() call), just collected for every route into a JSON-able dict instead of one
    route's XML, so RoutesPage.qml can show a real thumbnail per on-watch route without a
    single extra USB round trip - nav already reads the whole database once."""
    header = F.RouteHeader.parse(flash.read(F.ROUTE_BASE, 32))
    if header.magic != F.ROUTE_HEADER_MAGIC:
        return {"routes": []}

    routes_out = []
    for i in range(header.route_count):
        d = F.RouteDescriptor.parse(flash.read(F.ROUTE_DESC + 52 * i, 52))
        e = F.RouteIndexEntry.parse(flash.read(F.ROUTE_INDEX + 20 * i, 20))
        points = [F.RoutePoint.parse(flash.read(F.ROUTE_POINTS + 12 * k, 12))
                  for k in range(d.start_index, d.start_index + d.point_count)]
        track = []
        for p in points:
            lat, lon = F.inverse_xy(d.mid_lat, d.mid_lon, p.x, p.y)
            track.append({
                "lat": lat, "lon": lon,
                "ele": None if p.altitude == F.ALTITUDE_NONE else p.altitude,
            })
        routes_out.append({
            "name": d.name,
            "distanceMeters": d.distance,
            "ascentMeters": d.ascent,
            "descentMeters": d.descent,
            "pointCount": d.point_count,
            "waypointCount": e.waypoint_count,
            "track": track,
        })
    return {"routes": routes_out}


def run_nav(args):
    """READ-ONLY: reads the two navigation regions off the watch and decodes them.

    Nothing here writes. It is the first time this project reads the database rather than
    inferring it from a capture, which also makes it the backup that milestone 4 asked for
    and never had.
    """
    if args.from_capture:
        flash = FlashImage.from_pcap(args.from_capture)
        print(f"### {args.from_capture}, navigation database as written")
        route_ok, _wpt_ok = show_navigation(flash)
        return 0 if route_ok else 1

    link = Link(dry_run=False, verbose=args.verbose, product_id=args.product_id)
    print("read-only: 0x0b17 reads flash, nothing is written")
    link.open()
    regions = {}
    # Real, 2026-08-09 ("check if we can implement the same speed hack for routes and
    # POis that we did for activities") - POIs are already fast (write_nav.py's own `pois`
    # action, a single small SBEM query, no flash region read at all - nothing to optimize
    # there). Routes had a real, confirmed-dead-weight read: show_navigation()/
    # nav_summary_json() (grepped directly) never reference Apps or TrainingProgram at
    # all - only Waypoints/Routes. Apps alone is 200,000 bytes, by far the single biggest
    # region this function reads, for data a plain "show my routes/POIs" call never uses.
    # --save is the one real exception - milestone 4's actual backup use of this same
    # function genuinely does want every region, so it still gets the full read.
    # Read what THIS watch says it has, not what our static table lists. Real bug, found
    # 2026-08-11: the Routes page returned Bad Gateway on the Ambit3 Peak because
    # F.REGIONS gained a GlonassSGEE entry (added 2026-08-10 for the Kailash) and this loop
    # read every entry in it. The Peak declares no such region, so the 0x0b17 read at
    # 0x1339e0 came back short and took the whole navigation read down with it - a region
    # that has nothing to do with navigation breaking the page that shows routes.
    #
    # Asking the watch is also the cross-device answer: a Traverse, which really does have
    # GlonassSGEE, gets it read; a watch without one is never asked.
    declared = read_memory_map(link)
    for base, (name, size, _) in sorted(F.REGIONS.items()):
        if name == "GpsSGEE":
            continue  # 140000 bytes of ephemeris, nothing to do with navigation
        if name in ("Apps", "TrainingProgram") and not args.save:
            continue
        if name not in declared:
            # --save is a backup and wants everything it can get, so still try the region -
            # but a watch that cannot serve it must not sink the whole backup.
            if not args.save:
                continue
            try:
                regions[name] = read_flash(link, base, size, label=name)
            except RuntimeError as exc:
                print(f"  skipped {name}: this watch does not declare it ({exc})")
            continue
        try:
            regions[name] = read_flash(link, base, size, label=name)
        except RuntimeError as exc:
            # A region the watch declares in its memory map but nonetheless cannot fully
            # serve over 0x0b17 - seen on Kailash, whose nav layout differs from the Ambit3
            # family (Routes/POIs are hidden for it in the app anyway). Skip it with a warning
            # rather than letting one region's short reply sink the whole read.
            print(f"  skipped {name}: read failed ({exc})")

    flash = FlashImage()
    for base, (name, _, _) in sorted(F.REGIONS.items()):
        if name in regions:
            flash.write(base, regions[name])

    if args.save:
        for name, blob in regions.items():
            path = pathlib.Path(f"{args.save}-{name.lower()}.bin")
            path.write_bytes(blob)
            print(f"  saved {len(blob)} B to {path}")
        # The POI list too, as read (0x0b24), so Restore puts back exactly these POIs, not
        # whatever the watch holds by then. Best-effort: a backup never fails over it.
        try:
            pois = read_pois(link)
            if pois and F.SBEM_MAGIC in pois:
                path = pathlib.Path(f"{args.save}-pois.bin")
                path.write_bytes(pois)
                print(f"  saved {len(poi_records_of(pois))} POI(s) to {path}")
        except (RuntimeError, TimeoutError, OSError) as exc:
            print(f"  skipped POIs: {exc}")

    if args.route_gpx is not None:
        gpx = route_to_gpx(flash, args.route_gpx)
        if args.route_gpx_out:
            pathlib.Path(args.route_gpx_out).write_text(gpx, encoding="utf-8")
            print(f"  wrote route[{args.route_gpx}] GPX to {args.route_gpx_out}")
        else:
            print(gpx)

    if args.routes_gpx_json:
        # All routes' proven GPX from the ONE flash read above - the sync snapshot's fast path,
        # replacing N separate `nav --route-gpx INDEX` subprocesses that each re-read the region.
        header = F.RouteHeader.parse(flash.read(F.ROUTE_BASE, 32))
        routes_out = []
        if header.magic == F.ROUTE_HEADER_MAGIC:
            for i in range(header.route_count):
                d = F.RouteDescriptor.parse(flash.read(F.ROUTE_DESC + 52 * i, 52))
                routes_out.append({"name": d.name, "pointCount": d.point_count,
                                   "gpx": route_to_gpx(flash, i)})
        print(json.dumps({"ok": True, "routes": routes_out}))
        return 0

    route_ok, wpt_ok = show_navigation(flash)
    if route_ok and not wpt_ok:
        # A waypoint-CRC mismatch on a watch whose routes are valid (the Traverse) is a
        # decode gap for the route-waypoint layout, not route corruption. Flag it, but don't
        # fail the read - the routes above (and the JSON below) are decoded and correct.
        print("  note: route-waypoint CRC mismatch on this device; the routes themselves "
              "are valid and unaffected")

    if args.json:
        # Printed last, on its own line, after show_navigation()'s human-readable output -
        # same "find the last JSON-parseable line" convention sgee.py --status --json
        # already established, so callers don't need a separate output mode that skips the
        # human-readable diagnostics entirely.
        print(json.dumps(nav_summary_json(flash)))

    # Gate success on the routes (the payload every caller of this reads); a waypoint-only
    # mismatch stays a printed warning above, so it never blanks three valid routes.
    return 0 if route_ok else 1


def descriptor_for_product_id(product_id):
    """Real bug, found 2026-08-08 investigating whether Kailash's settings are writable over
    cable (task: "Investigate Kailash settings write path"): show_settings()/show_entries()
    both unconditionally called sbem_schema.default_descriptor(), which globs for Ambit3's
    own reference firmware (2.4.17) - exactly the same silent-wrong-schema bug already found
    and fixed in kailash_history.py's own KAILASH_DESCRIPTOR. Returns Kailash's own real
    descriptor for its product_id (0x002A), None for everything else - callers fall back to
    sbem_schema.default_descriptor() in that case, unchanged from before this existed."""
    import sbem_schema
    if product_id == 0x002A:
        path = (sbem_schema.ASSETS / "APK" / "kailash" / "Suunto 7R" / "Container"
                / "Documents" / "descr+79DC39510E000100+2.0.5")
        return path if path.exists() else None
    if product_id in (0x002B, 0x002D):
        # Traverse (Jabiru) / Traverse Alpha (Loon) - same silent-wrong-schema bug as Kailash's
        # (ported from main, 2026-08-16): the Ambit3 Peak reference descriptor mis-decodes a live
        # Traverse's settings - entry 0x04 (compass_declination) is 1 byte where it says 4, and
        # language/units read wrong values ("Dansk" when the watch is English). Its own real
        # SuuntoLink descriptor at the current firmware (2.0.22) decodes correctly.
        path = (sbem_schema.ASSETS / "WIndows apps" / "Suuntolink"
                / "descr+A30E115119001200+2.0.22")
        return path if path.exists() else None
    if product_id == 0x002C:
        # Ambit3 Vertical (Kaka) fw 1.1.22: no SuuntoLink descriptor was ever captured, so this
        # one is REBUILT FROM THE FIRMWARE (assets/Firmware/re-out/sfi2_descriptor.py, ticket 06,
        # proven byte for byte on the Peak, Traverse and Kailash). The Peak's descriptor is wrong
        # for it: 28 of the 33 settings the app reads sit under other entry numbers.
        path = sbem_schema.ASSETS / "descr+FROM-FIRMWARE-Kaka+1.1.22"
        return path if path.exists() else None
    return None


def schema_for_link(link):
    """The schema descriptor of the watch `link` is open on - its own, read from the watch
    itself when no stored copy matches its serial and firmware (sbem_schema.for_link). This is
    what every live read should use: descriptor_for_product_id() and default_descriptor() only
    find files on a machine that has SuuntoLink's data folder or a private assets/, which left
    Settings and POIs dead for everyone else (reported 2026-10-05 by a user who never installed
    SuuntoLink). Those two stay as the fallback if the watch refuses the read, so nothing that
    worked before stops working."""
    import sbem_schema
    try:
        return sbem_schema.for_link(link)
    except Exception as exc:  # hid backends and short replies each raise their own type
        print(f"  note: schema not readable from the watch ({exc}); using a stored descriptor",
              file=sys.stderr)
        return (descriptor_for_product_id(getattr(link, "opened_product_id", None))
                or sbem_schema.default_descriptor())


def settings_from_capture(capture):
    """The 0x1100 reply of a capture, for exercising the decoding without a watch."""
    for m in messages(capture):
        if m.command == CMD_SETTINGS_READ and m.incoming and m.payload:
            return m.payload
    raise ValueError(f"no 0x1100 reply in {capture}")


def show_settings(payload, show_all=False, redacted=False, descriptor=None):
    """Decodes a 0x1100 reply through the SuuntoLink schema. Returns the list of BLE
    bonds carrying a key, or None when the schema is missing and the question cannot
    be answered. Never return an empty list in that case: an absent descriptor once
    read as "never paired" against a capture that did carry a bond."""
    import sbem_schema

    head = payload.find(sbem_schema.MAGIC)
    if head < 0:
        print("  no SBEM0102 payload in the reply")
        return None

    descriptor = descriptor or sbem_schema.default_descriptor()
    if not descriptor.exists():
        print(f"  CANNOT DECIDE: the SuuntoLink descriptor is missing.\n"
              f"  Expected a descr+SERIAL+{sbem_schema.REFERENCE_FW} file in "
              f"{descriptor.parent}, whatever\n"
              f"  serial it carries; it comes from SuuntoLink's data folder. Without "
              f"it the entries\n  cannot be named, and this command cannot tell a "
              f"paired watch from an unpaired one.")
        for entry_id, data in sbem_schema.entries(payload[head:]):
            print(f"  0x{entry_id:02x} [{len(data)}] {data[:32].hex(' ')}")
        return None

    schema = sbem_schema.load(descriptor)
    entries = list(sbem_schema.entries(payload[head:]))
    print(f"  {len(entries)} entries in the DeviceSettings tree")

    bonds, slots = [], 0
    for entry_id, data in entries:
        if not (show_all or entry_id in (BLE_WHITELIST_ENTRY, POD_ENTRY)):
            continue
        print(f"  0x{entry_id:02x} {schema.label(entry_id) or '?'}  [{len(data)}]")
        for record in schema.decode_entry(entry_id, data) or []:
            fields = {schema.field_name(entry_id, f.fid): v for f, v in record}
            print("        " + "  ".join(f"{k}={show_value(k, v, redacted)}"
                                        for k, v in fields.items()))
            if entry_id != BLE_WHITELIST_ENTRY:
                continue
            slots += 1
            if fields.get("EncodingKey"):
                bonds.append(fields)

    if bonds:
        print(f"\n  {len(bonds)} BLE bond(s) carrying a key out of {slots} slot(s). "
              "The 16 bytes of\n  EncodingKey are the candidate for the NSP session "
              "token, see milestone 7\n  in HANDOFF.md.")
        if any(not b.get("IsNspCapable") for b in bonds):
            print("  Note: a bond has IsNspCapable=0. Pairing does not set it, from "
                  "inside the Suunto\n  app or outside, so it has to be written through "
                  "0x1101. Whether the watch then\n  accepts the key as a token is the "
                  "open question of milestone 7.")
        if redacted:
            print("  Key material is redacted, so this output is safe to send as is.")
        else:
            print("  These are real link keys. Re-run with --redact to get output that "
                  "is safe\n  to paste or send.")
    else:
        print(f"\n  {slots} whitelist slot(s), none carrying a key: this watch has no "
              "bond.\n  Pair it with a phone, then read again.")
    return bonds


def read_pois(link, capture=None):
    """The watch's complete POI list, through 0x0b24.

    A navigation write erases it, whatever `tools/README.md` used to assume: confirmed on
    hardware 2026-08-04, a reset with no 0x0b25 lost every POI. Which is why SuuntoLink
    reads the list before writing and puts it back afterwards, in every capture we have.

    In dry-run there is no watch to ask, so the reply is taken from the capture being
    compared. That keeps --compare byte-exact rather than skipping the message.
    """
    try:
        reply = link.command(CMD_POI_READ, b"\0\0\0\0")
    except RuntimeError:
        # Over Bluetooth an empty store's refusal (err=1, no data) comes back as an error
        # (ble_server.WatchRefused, or BleBridgeError through the bridge); over USB it is just
        # b"". The count tells an empty store from a real failure (Android does the same).
        if not link.dry_run and poi_count(link) == 0:
            return b""
        raise
    if not link.dry_run:
        return _rest_of_poi_pages(link, reply)
    if capture:
        for m in messages(capture):
            if m.command == CMD_POI_READ and m.incoming and m.payload:
                return m.payload
    return b""


def _rest_of_poi_pages(link, first):
    """0x0b24 is paged: [u16 nextCursor][u16 0][u16 isLast][data], the SBEM header on the
    first page only, the request [u16 cursor][u16 0] (Ambit3 firmware, MSP430X Emu 2.4.17,
    0x0b24 case at 0x1a632; SuuntoLink's 4-POI capture is one page with isLast = 1). One
    request used to be all we sent, so a long list came back cut, and a route write then
    put back only that part. Returns every page as one buffer shaped like a single reply.
    An empty store answers with an error and no data; over USB that is simply b"" here."""
    out = bytearray(first)
    page = first
    for _ in range(32):
        if len(page) < 6 or int.from_bytes(page[4:6], "little"):
            return bytes(out)
        cursor = int.from_bytes(page[0:2], "little")
        page = link.command(CMD_POI_READ, cursor.to_bytes(2, "little") + b"\0\0")
        if len(page) < 6:
            raise RuntimeError(f"the POI list page at cursor {cursor} came back short")
        out += page[6:]
    raise RuntimeError("the POI list is longer than 32 pages")


def poi_count(link):
    """How many POIs the watch holds (0x0b02, u32 LE): in the Ambit3 firmware it counts the
    same two lists 0x0b24 walks, and SuuntoLink's own POI import opens with it. None in a
    dry-run or on a short reply."""
    reply = link.command(CMD_POI_COUNT, b"")
    if link.dry_run or len(reply) < 4:
        return None
    return int.from_bytes(reply[:4], "little")


def check_poi_count(link, expected):
    """After POIs were put back: says so when the watch holds a different number. 0x0b25 acks
    even what it did not store, so the count is the only proof. True when it matches or there
    is no watch to ask (dry-run)."""
    held = poi_count(link)
    if held is None or held == expected:
        return True
    print(f"!! the watch holds {held} POIs, {expected} were put back")
    return False


def poi_entry_payloads(records):
    """POI record bodies as the 0x0b25 payloads that append them: packed in order into 0x55
    entries of at most POI_ENTRY_MAX bytes, one 0x0b25 each. SuuntoLink's poiimport writes one
    such entry (single-byte length, its 5 POIs in 249 bytes) and never the extended header
    (0x55 0xFF + u32); that form is only what the watch sends on a read, and writing it
    corrupted the POI store (2026-08-30). 0x0b25 appends (Ambit3 firmware FUN_0004da50), so
    several in a row add up. The Android twin, ambit3_write_poi_bodies(), put 9 POIs back on
    André's Peak this way (2026-10-25). Empty records are skipped: a POI the watch lists as an
    empty entry (a name over 15 bytes) has no data to put back."""
    payloads, chunk = [], b""

    def flush():
        nonlocal chunk
        if chunk:
            payloads.append(SBEM_WRITE_PREFIX + F.SBEM_MAGIC
                            + bytes([POI_ENTRY, len(chunk)]) + chunk)
            chunk = b""

    for rec in records:
        if not rec:
            continue
        if len(rec) > POI_ENTRY_MAX:
            raise ValueError(f"a {len(rec)}-byte POI record does not fit one 0x55 entry")
        if len(chunk) + len(rec) > POI_ENTRY_MAX:
            flush()
        chunk += rec
    flush()
    return payloads


def poi_restore_payloads(reply):
    """Turns a 0x0b24 reply into the 0x0b25 payloads that put the same POIs back, [] when
    there are none.

    The watch reports one SBEM entry per POI; the write packs them in the reverse of the order
    read. On `routedelete` that reversal is also the order SuuntoLink uses, most recently
    modified first, which is the same rule it applies to routes, and for its small list the
    result is byte-for-byte the payload in the capture. Reversing needs neither the schema nor
    any decoding of a POI's insides, so nothing here can mangle a POI it does not understand.
    A longer list goes back as several entries (poi_entry_payloads); it used to go back as one
    entry with the extended header, the form that corrupted the store.
    """
    if not reply or F.SBEM_MAGIC not in reply:
        return []  # no watch to ask and no capture to borrow from, as in a bare dry-run
    records = [data for entry_id, data in F.sbem_entries(reply)
               if entry_id == POI_ENTRY]
    return poi_entry_payloads(list(reversed(records)))


def poi_restore_count(reply):
    """How many POIs poi_restore_payloads(reply) puts back (empty entries have no data)."""
    if not reply or F.SBEM_MAGIC not in reply:
        return 0
    return sum(1 for entry_id, data in F.sbem_entries(reply) if entry_id == POI_ENTRY and data)


def put_pois_back(link, reply):
    """After a route write's 0x0b04 commit emptied the POI store: writes back the POIs read
    before it (`reply`, a read_pois() result) and checks the count. Returns how many went
    back, or None when the watch holds a different number."""
    payloads = poi_restore_payloads(reply)
    for payload in payloads:
        link.command(CMD_POI_WRITE, payload)
    if not payloads:
        return 0
    expected = poi_restore_count(reply)
    return expected if check_poi_count(link, expected) else None


def poi_write_payload_add(reply, new_record):
    """The 0x0b25 for `new_record` followed by the POIs in `reply`, new record first as in
    `poiimport`. For ADDING one POI pass reply=b"": 0x0b25 appends what it is given to the
    store (Ambit3 firmware FUN_0004da50, no implicit clear), so sending "new + existing"
    doubled every POI on each add. `poiimport` sends the whole list only because SuuntoLink
    empties the store with 0x0b04 first (assets/pcap/ambit3poiimport)."""
    records = ([data for entry_id, data in F.sbem_entries(reply)
                if entry_id == POI_ENTRY]
               if reply and F.SBEM_MAGIC in reply else [])
    body = new_record + b"".join(records)
    if len(body) > POI_ENTRY_MAX:
        # Never the extended header (see poi_entry_payloads); an add sends one record anyway.
        raise ValueError(f"{len(body)} bytes of POIs do not fit one 0x55 entry")
    return SBEM_WRITE_PREFIX + F.SBEM_MAGIC + bytes([POI_ENTRY, len(body)]) + body


def build_poi_record(name, lat, lon, stamp=None, type_=F.WAYPOINT_TYPE_DEFAULT):
    """One POI as the SBEM entry-0x55 body the watch stores, for poi_write_payload_add().

    `type_` is the Ambit POI type byte (0-17, see F.WAYPOINT_TYPES) - the icon the watch
    shows for this POI. Defaults to 17 ("Waypoint"), what the watch writes for a POI it
    creates itself.

    Layout, field for field the same as the Android app's ambit3_add_poi_to_watch()
    (device_driver_ambit3.c) - the path Milestone 6 confirmed working against the real
    watch on 2026-08-06:

        name\\0  route_name\\0 (empty: standalone, not tied to a route)  timestamp\\0
        [route_index=0][type=17][sub_type=0][type_index=0][flags=1]
        [i32 LE lat*1e7][i32 LE lon*1e7]

    type=17/flags=1 match what the watch itself writes for a POI it creates (SuuntoLink
    leaves both at 0 for an imported one) - picked so this looks like a watch-made POI.

    The timestamp is LOCAL time, no offset suffix, not UTC: tested directly on hardware
    2026-08-06, the watch's own POI screen echoes this field back verbatim without
    converting it, so storing UTC would display the wrong wall-clock time (see the C
    implementation's comment for the full story of how the old UTC assumption fell)."""
    import time
    if stamp is None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    record = F.poi_name_bytes(name) + b"\0"   # 15 bytes at most, see poi_name_bytes
    record += b"\0"                       # empty route_name
    record += stamp.encode() + b"\0"
    record += bytes([0, type_, 0, 0, 1])
    record += int(round(lat * 1e7)).to_bytes(4, "little", signed=True)
    record += int(round(lon * 1e7)).to_bytes(4, "little", signed=True)
    return record


def build_set_pois_payloads(pois):
    """The 0x0b25 payloads that SET the watch's POI list to exactly `pois` (each
    {name,lat,lon,type?}), for the clear-then-set flow (a preceding nav 0x0b04 commit clears the
    region). [] for an empty list, so the caller sends no 0x0b25 and the commit's clear stands
    (all POIs removed). Packed by poi_entry_payloads(): entries of at most 254 bytes, one
    0x0b25 each, never the extended header."""
    records = []
    for p in pois:
        t = p.get("type")
        t = F.WAYPOINT_TYPE_DEFAULT if t in (None, "") else int(t)
        records.append(build_poi_record(p["name"], float(p["lat"]), float(p["lon"]), type_=t))
    return poi_entry_payloads(records)


def run_addpoi(args):
    """Adds ONE POI, keeping every POI already on the watch. Unlike a route write this
    never touches the Waypoints/Routes flash regions and needs no commit: it sends only
    the new record through 0x0b25, which appends it, and checks the count (0x0b02) went
    up by one. Sending "new + the list already there", as this used to (2026-08-11), kept
    the old POIs but doubled them on every add - the duplicates seen on the Peak and in
    set_pois.py's own note of 2026-08-30. The Android twin of this exact flow was
    confirmed on the Peak over Bluetooth on 2026-10-24 (0 -> 1 -> 2 POIs, no duplicates)."""
    if not args.name or not args.name.strip():
        raise SystemExit("addpoi: --name must not be empty (the watch shows POIs by name)")
    if args.lat is None or args.lon is None:
        raise SystemExit("addpoi: --lat and --lon are required")
    if not (-90.0 <= args.lat <= 90.0) or not (-180.0 <= args.lon <= 180.0):
        raise SystemExit(f"addpoi: {args.lat}, {args.lon} is not a coordinate on Earth")
    try:
        poi_type = (F.waypoint_type_id(args.type) if args.type is not None
                    else F.WAYPOINT_TYPE_DEFAULT)
    except ValueError as e:
        raise SystemExit(f"addpoi: {e}")

    link = Link(dry_run=not args.write, verbose=args.verbose, product_id=args.product_id)
    if args.write:
        print("!! REAL WRITE requested")
        link.open()
    else:
        print("dry-run mode: not a byte will be emitted")

    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    before = poi_count(link)
    record = build_poi_record(args.name.strip(), args.lat, args.lon, type_=poi_type)
    # Only the new record: 0x0b25 appends (see poi_write_payload_add). It also acks when it
    # stored nothing, so the count is what says the POI is there.
    link.command(CMD_POI_WRITE, poi_write_payload_add(b"", record))
    after = poi_count(link)
    if before is not None and after != before + 1:
        print(f"!! the watch did not keep the POI ({before} -> {after} POIs)")
        return 1
    print(f"POI {args.name.strip()!r} ({args.lat:.6f}, {args.lon:.6f}) "
          f"type={F.waypoint_type_name(poi_type)} "
          f"{'written' if args.write else 'would be written'}"
          + (f", {before} -> {after} POIs" if before is not None else ""))
    return 0


# Alone, 0x0b04 empties both POI lists and nothing else (Ambit3 firmware 0x1a65c: two
# FUN_000688ca calls). Checked on André's Peak 2026-10-25: the routes read back identical.
CMD_POI_CLEAR = CMD_NAV_COMMIT
POI_BACKUPS_KEPT = 30     # copies kept per watch, as the Android app keeps (WatchBackup.ts)


def poi_place(record):
    """(name, lat_e7, lon_e7) of one POI record, the fields build_poi_record() lays out; None
    for an empty entry or one too short to be a POI."""
    off, fields = 0, []
    for _ in range(3):                       # name, route name, timestamp, each NUL-ended
        end = record.find(b"\0", off)
        if end < 0:
            return None
        fields.append(record[off:end])
        off = end + 1
    off += 5                                 # route index, type, sub type, type index, flags
    if len(record) < off + 8:
        return None
    return (fields[0].decode("utf-8", "replace"),
            int.from_bytes(record[off:off + 4], "little", signed=True),
            int.from_bytes(record[off + 4:off + 8], "little", signed=True))


def poi_records_to_keep(reply, targets):
    """The records of `reply` to keep when removing `targets` ([(name, lat, lon)], degrees):
    each target takes the first POI with its name and coordinates (to 1e-7 degree, how the
    watch stores them) not already taken, so of two identical POIs one stays. Empty entries go
    too: nothing in them can be written back. Returns (keep, empty_count); raises LookupError
    naming a target that is not there. Same rule as the Android app's poisToKeep()."""
    records = ([data for entry_id, data in F.sbem_entries(reply) if entry_id == POI_ENTRY]
               if reply and F.SBEM_MAGIC in reply else [])
    places = [poi_place(r) if r else None for r in records]
    taken = set()
    for name, lat, lon in targets:
        want = (name, int(round(lat * 1e7)), int(round(lon * 1e7)))
        hit = next((i for i, place in enumerate(places) if i not in taken and place == want), None)
        if hit is None:
            raise LookupError(f"“{name}” is no longer on the watch. Read the list again.")
        taken.add(hit)
    keep = [r for i, r in enumerate(records) if i not in taken and r]
    return keep, sum(1 for r in records if not r)


def serial_from_device_info(reply):
    """The serial in a device-info reply (model[16], serial[16], ...), "" when there is none."""
    return reply[16:32].split(b"\0")[0].decode("ascii", "replace").strip() if len(reply) >= 32 else ""


def save_poi_backup(reply, serial, backup_dir):
    """Saves a raw POI read as <backup_dir>/POIs/<serial>/POIs_pre_<stamp>.bin, the layout the
    Android app uses, and keeps the newest POI_BACKUPS_KEPT. Returns the path."""
    import time
    folder = pathlib.Path(backup_dir) / "POIs" / (serial or "unknown")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"POIs_pre_{time.strftime('%Y%m%d-%H%M%S')}.bin"
    path.write_bytes(reply)
    for old in sorted(folder.glob("POIs_pre_*.bin"))[:-POI_BACKUPS_KEPT]:
        old.unlink(missing_ok=True)
    return path


def set_poi_records(link, records):
    """Replaces the watch's POI list with `records` (in the order read): 0x0b04 empties it,
    the count must then be 0, the records go back reversed in entries of at most 254 bytes
    (poi_entry_payloads, the order that kept André's list in order on 2026-10-25), and the
    count must match. Raises RuntimeError when a step does not hold."""
    link.command(CMD_POI_CLEAR, b"")
    left = poi_count(link)
    if left not in (None, 0):
        raise RuntimeError(f"the watch still holds {left} POIs after emptying the list")
    for payload in poi_entry_payloads(list(reversed(records))):
        link.command(CMD_POI_WRITE, payload)
    if not check_poi_count(link, len(records)):
        raise RuntimeError(f"the watch did not keep the {len(records)} POIs written back")


DEFAULT_BACKUP_DIR = pathlib.Path.home() / "AmbitAppBackups"   # server.py's BACKUP_DIR


def pre_write_backup_path(prefix, link=None, kind="custom_modes", ext="bin"):
    """Where a tool saves what it is about to overwrite on the watch:
    ~/AmbitAppBackups/<kind>/<prefix>_<serial>_<time>.<ext>, the folder is created. Never next
    to the code: in the packaged app that folder is temporary or read-only, and a relative path
    lands wherever the app was started from (desktop audit 2026-10-29). The serial is the
    watch that opened, else AMBIT_SERIAL (the backend pins every call to one watch)."""
    import os
    import time
    serial = str(getattr(link, "opened_serial", None) or os.environ.get("AMBIT_SERIAL") or "")
    serial = "".join(c for c in serial if c.isalnum())
    folder = DEFAULT_BACKUP_DIR / kind
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return folder / (f"{prefix}_{serial}_{stamp}.{ext}" if serial else f"{prefix}_{stamp}.{ext}")


def poi_records_of(reply):
    """The non-empty POI records of a read_pois() reply, in the order read."""
    if not reply or F.SBEM_MAGIC not in reply:
        return []
    return [d for i, d in F.sbem_entries(reply) if i == POI_ENTRY and d]


def backup_pois_before_route_write(link, pois, serial, backup_dir=None):
    """A route write's 0x0b04 commit empties the POI store; until they are put back the POIs
    exist only in memory. A copy goes to disk first, as remove_pois() does, so a link that
    drops in between loses nothing for good. Returns the path, or None (dry-run, no POIs)."""
    if link.dry_run or not poi_records_of(pois):
        return None
    return str(save_poi_backup(pois, serial or getattr(link, "opened_serial", None) or "",
                               backup_dir or DEFAULT_BACKUP_DIR))


def put_pois_back_safely(link, pois, backup=None):
    """put_pois_back(), and if it fails or the count is wrong, one more go from an empty store
    (set_poi_records: 0x0b04, count 0, write, count) so a half-done restore is never doubled.
    Returns (count put back, None) or (None, error text naming the backup)."""
    try:
        back = put_pois_back(link, pois)
        if back is not None:
            return back, None
        first = "the watch did not keep every POI put back"
    except (RuntimeError, TimeoutError, OSError) as e:
        first = str(e)
    records = poi_records_of(pois)
    try:
        set_poi_records(link, records)
        return len(records), None
    except (RuntimeError, TimeoutError, OSError) as e:
        where = f" A copy of them is saved in {backup}." if backup else ""
        return None, f"the POIs could not be put back ({first}; then {e}).{where}"


def remove_pois(link, targets, backup_dir):
    """Removes `targets` ([(name, lat, lon)]) from an Ambit3-family watch, keeping every other
    POI byte for byte: device info, read the list (0x0b24, every page), save a copy, then
    set_poi_records() with the rest. If that fails after the list was emptied, the copy is
    written back once. Never touches the routes. The Android twin is
    PoiService.removePoisFromWatch(), tested on André's Peak 2026-10-25.

    Returns {ok, left, emptyCleared, backup}, or {ok: False, error, backup?}."""
    info = link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    reply = read_pois(link)
    try:
        keep, empty = poi_records_to_keep(reply, targets)
    except LookupError as e:
        return {"ok": False, "error": str(e)}
    backup = None if link.dry_run else str(save_poi_backup(
        reply, serial_from_device_info(info) or getattr(link, "opened_serial", None) or "", backup_dir))
    try:
        set_poi_records(link, keep)
    except (RuntimeError, TimeoutError, OSError) as e:
        records = [d for i, d in F.sbem_entries(reply) if i == POI_ENTRY and d]
        try:
            set_poi_records(link, records)
            return {"ok": False, "error": f"Removing failed ({e}). The POIs were put back "
                    "as they were.", "restored": True, "backup": backup}
        except (RuntimeError, TimeoutError, OSError) as e2:
            return {"ok": False, "error": f"Removing failed and the POIs could not be put "
                    f"back ({e}; {e2}). A copy is saved in {backup}.", "restored": False,
                    "backup": backup}
    return {"ok": True, "dry_run": link.dry_run, "left": len(keep), "emptyCleared": empty,
            "backup": backup}


def run_delpoi(args):
    """CLI for remove_pois(): one POI, by --name --lat --lon as `pois` lists it. Prints a
    JSON result on the last line. Dry-run by default (it then reads nothing either)."""
    if not args.name or args.lat is None or args.lon is None:
        raise SystemExit("delpoi: --name, --lat and --lon are required")
    link = Link(dry_run=not args.write, verbose=args.verbose, product_id=args.product_id)
    if args.write:
        print("!! REAL WRITE requested")
        link.open()
    else:
        print("dry-run mode: not a byte will be emitted")
    result = remove_pois(link, [(args.name, args.lat, args.lon)],
                         args.backup_dir or pathlib.Path.home() / "AmbitAppBackups")
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


def routes_to_keep_as_gpx(flash, name):
    """GPX of every route in `flash` except the one named `name`, compared as the watch stores
    names (15 UTF-8 bytes, stored_name). Same rule as the Android app's existingRoutesToKeep().
    Raises LookupError when no route has that name."""
    header = F.RouteHeader.parse(flash.read(F.ROUTE_BASE, 32))
    count = header.route_count if header.magic == F.ROUTE_HEADER_MAGIC else 0
    keep, found = [], False
    for i in range(count):
        d = F.RouteDescriptor.parse(flash.read(F.ROUTE_DESC + 52 * i, 52))
        if stored_name(d.name) == stored_name(name):
            found = True
        else:
            keep.append(route_to_gpx(flash, i))
    if not found:
        raise LookupError(f"“{name}” is no longer on the watch. Read the list again.")
    return keep


def remove_route(link, name, serial=None, backup_dir=None):
    """Removes route `name` from an Ambit3-family watch: the routes database is rewritten with
    every other route (or reset when none is left), and the POIs that write erases are put
    back (put_pois_back). Refuses when the watch's memory map is not the one these addresses
    are for. Real writes only: a dry-run has no routes to read. The Android twin is
    NavigationService.removeRouteFromWatch(), tested on André's Peak 2026-10-25.

    Returns {ok, routesLeft, poisPutBack} or {ok: False, error}."""
    import tempfile
    if link.dry_run:
        return {"ok": False, "error": "removing a route needs the watch: run it with --write"}
    info = link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    pois = read_pois(link)
    if not check_memory_map(read_memory_map(link)):
        return {"ok": False, "error": "this watch's memory map is not the one Sommet writes "
                "routes for; nothing was written"}
    try:
        keep = routes_to_keep_as_gpx(read_nav_flash(link), name)
    except LookupError as e:
        return {"ok": False, "error": str(e)}
    wsize = waypoint_size(link)
    paths = []
    try:
        for text in keep:
            with tempfile.NamedTemporaryFile("w", suffix=".gpx", delete=False) as f:
                f.write(text)
                paths.append(f.name)
        flash, layout = (build_routes([pathlib.Path(p) for p in paths], None, wsize) if paths
                         else build_reset(wsize))
        backup = backup_pois_before_route_write(
            link, pois, serial or serial_from_device_info(info), backup_dir)
        try:
            send_plan(link, flash, layout)
        except (RuntimeError, TimeoutError, OSError) as e:
            # The commit may have emptied the POI store before the failure.
            _back, err = put_pois_back_safely(link, pois, backup)
            return {"ok": False, "error": f"Removing the route failed ({e})."
                    + (f" Also, {err}" if err else " The POIs were put back."), "backup": backup}
    finally:
        for path in paths:
            pathlib.Path(path).unlink(missing_ok=True)
    back, err = put_pois_back_safely(link, pois, backup)
    if back is None:
        return {"ok": False, "routesLeft": len(keep), "backup": backup,
                "error": f"The route is removed, but {err}"}
    return {"ok": True, "routesLeft": len(keep), "poisPutBack": back, "backup": backup}


def run_delroute(args):
    """CLI for remove_route(): `delroute --name NAME --write`. JSON result on the last line."""
    if not args.name:
        raise SystemExit("delroute: --name is required")
    link = Link(dry_run=not args.write, verbose=args.verbose, product_id=args.product_id)
    if args.write:
        print("!! REAL WRITE requested")
        link.open()
    result = remove_route(link, args.name, backup_dir=args.backup_dir)
    print(json.dumps(result))
    return 0 if result.get("ok") else 1


def read_memory_map(link):
    """Addresses and sizes declared by the watch. In dry-run the reference values,
    the ones from the capture, are returned."""
    if link.dry_run:
        link.command(CMD_MEMORY_MAP, b"\0\0\0\0")
        return {name: (base, size) for base, (name, size, _) in F.REGIONS.items()}
    import re
    import struct

    reply = link.command(CMD_MEMORY_MAP, b"\0\0\0\0")
    found = {}
    # GlonassSGEE added 2026-08-10 - Kailash declares it, the Ambit3 family does not, so a
    # watch without it simply yields no entry here (callers must not assume it exists).
    # CustomModes/Apps added 2026-08-10 for the sport-mode display editor - it resolves the
    # region from the watch rather than trusting F.CUSTOM_MODES_BASE, the same discipline
    # the GLONASS work used. A watch without a region simply yields no entry (Kailash has no
    # CustomModes at all), which callers must handle rather than assume.
    # ExerciseLog/EventLog/TrackLog added 2026-08-15: the watch declares them in the same
    # 0x0b21 map, but only the six nav/settings regions were listed here, so a caller that
    # needed a log region's real per-device base (exercise_log.py) fell back to a hardcoded
    # Ambit3-Peak address and crashed on watches that put their log elsewhere - the Traverse's
    # ExerciseLog is not at the Peak's 0x27ac40 at all. Resolving it from the watch, the same
    # discipline the nav regions already use, is the fix. A watch that doesn't declare a given
    # region simply yields no entry, which callers must handle rather than assume.
    # TrainingProgram added 2026-08-19: the watch declares it (Ambit3 Peak: base 0x001000
    # size 3072) but it was missing from this regex, so check_memory_map silently SKIPPED
    # verifying it - training_program.py wrote to the hardcoded base with no declared-region
    # confirmation (it happened to be correct, but that's the exact "verify region before
    # write" discipline the other regions already follow).
    for match in re.finditer(
            rb"(Waypoints|Routes|GpsSGEE|GlonassSGEE|CustomModes|Apps|TrainingProgram|ExerciseLog|EventLog|TrackLog)\x00",
            reply):
        cursor = match.end()
        end = reply.index(b"\0", cursor)          # hash in hexadecimal
        start, size = struct.unpack("<II", reply[end + 1:end + 9])
        found[match.group(1).decode()] = (start, size)
    return found


def check_memory_map(found):
    ok = True
    for base, (name, size, _) in F.REGIONS.items():
        if name not in found:
            continue
        start, declared = found[name]
        good = (start, declared) == (base, size)
        ok &= good
        print(f"  {'OK   ' if good else 'WARNING  '} {name:10} "
              f"0x{start:06x} size {declared}"
              + ("" if good else f"  (reference 0x{base:06x} / {size})"))
    return ok


def refuse_bad_custom_modes(blob):
    """Last gate before any CustomModes write reaches a watch. The closing hash covers exactly the
    bytes written, and the firmware re-hashes only the used extent - a padded region leaves a hash
    it rejects on the next restart ("Connect to Moveslink" / err:62, then factory-default sport
    modes). That's what reset André's Peak on 2026-09-26: training_calendar.py wrote back the raw
    12 KB region it had read. Also enforces the field Type/Shortcut invariant (Finding 54)."""
    import custom_modes
    extent = custom_modes.used_extent(blob)
    if len(blob) != extent:
        raise ValueError(f"refusing to write CustomModes: {len(blob)} bytes given, {extent} used - "
                         "write only the used extent (custom_modes.used_extent) or the watch "
                         "rejects the region after a restart")
    bad = custom_modes.check_field_type_shortcut_invariant(blob)
    if bad:
        raise ValueError(f"refusing to write CustomModes: field Type/Shortcut invariant broken ({bad})")


def send_plan(link, flash, layout, commit=True):
    """commit=False for GpsSGEE: confirmed against assets/ambit3 pcap/orbitsync, 2026-08-05
    - its data_write/data_tail_len pair is followed directly by unrelated queries, no
    CMD_NAV_COMMIT, unlike Routes/Waypoints which always need one.

    Apps/CustomModes never get the commit either: SuuntoLink never sends it for them, and on
    its own 0x0b04 empties both POI lists (CMD_POI_CLEAR). A sport-mode edit sent with it
    erased every POI on the watch (desktop audit 2026-10-29), so it is refused here.
    Regions are matched by label prefix, so "CustomModes (rename ...)" is checked too."""
    for name, _address, blob in layout:
        if not blob:
            continue
        if commit and name.startswith(("CustomModes", "Apps")):
            raise ValueError(f"refusing to write {name} with the 0x0b04 nav commit: it empties "
                             "the watch's POI lists - pass commit=False for Apps/CustomModes")
        if name.startswith("CustomModes"):
            refuse_bad_custom_modes(blob)
    for command, address, body in emit_packs(flash, layout):
        if command == CMD_DATA_WRITE:
            head = address.to_bytes(4, "little") + len(body).to_bytes(2, "little") \
                + b"\0\0"
            link.command(CMD_DATA_WRITE, head + body)
        else:
            # [u32 address][u32 supplied by the application] + 64 hex characters
            head = address.to_bytes(4, "little") + b"\0\0\0\0"
            link.command(CMD_DATA_TAIL, head + body)
    if commit:
        link.command(CMD_NAV_COMMIT)


def build_reset(wpt_size=F.WAYPOINT_SIZE):
    flash = FlashImage()
    layout = [("waypoint header", F.WAYPOINT_BASE, F.waypoint_header_reset(wpt_size)),
              ("tail", F.WAYPOINT_BASE, None),
              ("route header", F.ROUTE_BASE, F.ROUTE_HEADER_RESET),
              ("tail", F.ROUTE_BASE, None)]
    for _, address, blob in layout:
        if blob:
            flash.write(address, blob)
    return flash, layout


def load_backup_nav(prefix):
    """(flash, header) from a `nav --save` backup's -routes.bin and -waypoints.bin, checked the
    way a read off the watch is: both files whole, and the route CRC over descriptors + points
    matching the header. header is None for a backup of a watch that never held routes. Raises
    ValueError for a backup that is cut short or does not check out, before anything is sent."""
    routes = pathlib.Path(f"{prefix}-routes.bin").read_bytes()
    waypoints = pathlib.Path(f"{prefix}-waypoints.bin").read_bytes()
    if len(routes) != F.ROUTE_REGION_SIZE or len(waypoints) != F.WAYPOINT_REGION_SIZE:
        raise ValueError(f"this backup's route files are incomplete ({len(routes)} and "
                         f"{len(waypoints)} bytes, expected {F.ROUTE_REGION_SIZE} and "
                         f"{F.WAYPOINT_REGION_SIZE})")
    flash = FlashImage()
    flash.write(F.WAYPOINT_BASE, waypoints)
    flash.write(F.ROUTE_BASE, routes)
    header = F.RouteHeader.parse(flash.read(F.ROUTE_BASE, 32))
    if header.magic != F.ROUTE_HEADER_MAGIC:
        return flash, None
    if header.route_count:
        crc = F.crc16_ccitt_false(flash.read(F.ROUTE_DESC, 52 * header.route_count)
                                  + flash.read(F.ROUTE_POINTS, 12 * header.point_count))
        if crc != header.checksum:
            raise ValueError(f"this backup's routes do not check out (CRC 0x{crc:04x}, "
                             f"header 0x{header.checksum:04x})")
    return flash, header


def backup_route_names(prefix):
    """The route names a `nav --save` backup lists in its route header (what Restore puts
    back), without decoding the points."""
    flash, header = load_backup_nav(prefix)
    if header is None:
        return []
    return [F.RouteDescriptor.parse(flash.read(F.ROUTE_DESC + 52 * i, 52)).name
            for i in range(header.route_count)]


def backup_routes_as_gpx(prefix):
    """[{name, gpx}] for every route a `nav --save` backup lists in its own route header, and
    only those. A removed route's bytes stay in flash behind the header (a route write rewrites
    the headers, not the area behind them); counting what is there, as build_restore() does,
    brought a deleted route back on Restore (desktop audit 2026-10-29)."""
    flash, header = load_backup_nav(prefix)
    if header is None:
        return []
    return [{"name": F.RouteDescriptor.parse(flash.read(F.ROUTE_DESC + 52 * i, 52)).name,
             "gpx": route_to_gpx(flash, i)} for i in range(header.route_count)]


def build_routes_from_gpx_texts(texts, wsize=F.WAYPOINT_SIZE):
    """build_routes() for GPX held in memory, build_reset() when there is none."""
    import tempfile
    paths = []
    try:
        for text in texts:
            with tempfile.NamedTemporaryFile("w", suffix=".gpx", delete=False,
                                             encoding="utf-8") as f:
                f.write(text)
                paths.append(f.name)
        if not paths:
            return build_reset(wsize)
        return build_routes([pathlib.Path(p) for p in paths], None, wsize)
    finally:
        for path in paths:
            pathlib.Path(path).unlink(missing_ok=True)


def backup_poi_list(prefix):
    """The POIs a backup holds, for Restore: ("reply", bytes) from -pois.bin, the list as read
    off the watch; else ("list", [{name, lat, lon, type}]) from the -sync.json snapshot that
    backups from 2026-09-12 on carry; else (None, None), a backup that kept no POIs."""
    raw = pathlib.Path(f"{prefix}-pois.bin")
    if raw.exists():
        data = raw.read_bytes()
        if F.SBEM_MAGIC in data:
            return "reply", data
    snap = pathlib.Path(f"{prefix}-sync.json")
    if snap.exists():
        try:
            cat = (json.loads(snap.read_text()).get("categories") or {}).get("pois") or {}
        except (OSError, ValueError):
            cat = {}
        if cat.get("supported") and isinstance(cat.get("items"), list):
            return "list", cat["items"]
    return None, None


def build_restore(prefix):
    """Undoes a reset (`restore --from-leftovers`): rebuilds the two headers from regions saved
    by `nav --save`, without touching the data behind them. Not what the Backup page's Restore
    uses: this also brings back routes removed before the save (see backup_routes_as_gpx).

    A reset rewrites only the two headers: 6 bytes and 32 bytes. Everything else -
    descriptors, points, index tables - stays in flash untouched, which a region read off
    the watch on 2026-08-04 showed directly. The leftovers there reproduced the CRCs of the
    `route128km` capture exactly, 0x8aaf and 0x6270, so both routes, all 1188 points with
    their 852 altitudes and all 11 waypoints had survived an erase byte for byte.

    So undoing an erase means writing correct counts and CRCs back into two headers. The
    closing hashes are exact rather than guessed, because the saved region gives the whole
    of what the flash will hold once the header is patched.
    """
    routes = pathlib.Path(f"{prefix}-routes.bin").read_bytes()
    waypoints = pathlib.Path(f"{prefix}-waypoints.bin").read_bytes()
    if len(routes) != F.ROUTE_REGION_SIZE or len(waypoints) != F.WAYPOINT_REGION_SIZE:
        raise ValueError(f"expected {F.ROUTE_REGION_SIZE} and "
                         f"{F.WAYPOINT_REGION_SIZE} bytes, got {len(routes)} and "
                         f"{len(waypoints)}")

    # Count what survived, reading the tables rather than the zeroed counters.
    descriptors, points = b"", 0
    base = F.ROUTE_DESC - F.ROUTE_BASE
    for i in range(F.MAX_ROUTES):
        blob = routes[base + 52 * i:base + 52 * (i + 1)]
        if blob[:1] in (b"\xff", b"\x00"):
            break
        descriptors += blob
        points += F.RouteDescriptor.parse(blob).point_count
    wpt_blob = b""
    base = F.WAYPOINT_DESC - F.WAYPOINT_BASE
    ws = F.WaypointHeader.parse(waypoints[:6]).size     # 52, or 54 on a Traverse
    for i in range(F.MAX_WAYPOINTS):
        blob = waypoints[base + ws * i:base + ws * (i + 1)]
        if blob[:1] == b"\xff" or blob[:8] == b"\0" * 8:
            break
        wpt_blob += blob
    route_count, waypoint_count = len(descriptors) // 52, len(wpt_blob) // ws
    print(f"  recovered {route_count} route(s), {points} points, "
          f"{waypoint_count} waypoint(s)")
    if not route_count:
        raise ValueError("no route left in the saved region, nothing to restore")

    body = routes[F.ROUTE_POINTS - F.ROUTE_BASE:][:12 * points]
    route_header = F.RouteHeader(
        route_count, points, F.crc16_ccitt_false(descriptors + body)).build()
    waypoint_header = F.WaypointHeader.build_for(wpt_blob, waypoint_count, ws)

    # The flash image holds the whole region so the closing hash is computed over what the
    # watch will really contain; only the headers are in the layout, so only they go out.
    flash = FlashImage()
    flash.write(F.ROUTE_BASE, route_header + routes[len(route_header):])
    flash.write(F.WAYPOINT_BASE, waypoint_header + waypoints[len(waypoint_header):])
    layout = [("waypoint header", F.WAYPOINT_BASE, waypoint_header),
              ("tail", F.WAYPOINT_BASE, None),
              ("route header", F.ROUTE_BASE, route_header),
              ("tail", F.ROUTE_BASE, None)]
    return flash, layout


def waypoint_size(link):
    """The watch's waypoint record size: 52, or 54 on a Traverse / Traverse Alpha.

    A live link asks the watch itself - its waypoint header's magic is 0x300 + the size, and a
    factory-fresh Traverse already carries 0x0336 (backups/traverse-fw1.0.4-pristine) - so a
    wrong product id can't make us write the other layout. A rehearsal has no watch to ask and
    goes by the product id."""
    pid = getattr(link, "opened_product_id", None) or getattr(link, "product_id", None)
    if not getattr(link, "dry_run", False):
        try:
            magic = int.from_bytes(read_flash(link, F.WAYPOINT_BASE, 6, label="Waypoints")[:2], "little")
            size = F.waypoint_size_for_magic(magic)
            if size:
                return size
        except Exception:                                    # noqa: BLE001 - fall back to the model
            pass
    return F.waypoint_size_for_product(pid)


def stored_name(name):
    """A route name as the watch keeps it (15 UTF-8 bytes), for comparing names."""
    return F.decode_name(F.encode_name(name or "", "utf-8")).strip()


def replace_same_name(routes):
    """The new route (last) replaces any route already on the watch with the same name
    (2026-10-22, the Android rule in RouteMerge.ts). The watch ties waypoints to routes BY NAME,
    so two routes of one name share their pins: each re-send of a route (e.g. the planner's
    default "Sommet plan") read the other's pins back as its own (route_to_gpx) and doubled
    them, until serialize refused at 100 waypoints. Callers pass the routes already on the
    watch first and the new one last. Duplicates among the existing routes are left alone."""
    if len(routes) < 2:
        return routes
    new_name = stored_name(routes[-1].name)
    replaced = [r.name for r in routes[:-1] if stored_name(r.name) == new_name]
    if not replaced:
        return routes
    print(f"  replacing {len(replaced)} route(s) already named {routes[-1].name!r} "
          "with the new one")
    return [r for r in routes[:-1] if stored_name(r.name) != new_name] + [routes[-1]]


def build_routes(gpx_paths, meta_capture, wpt_size=F.WAYPOINT_SIZE):
    stamps = []
    if meta_capture:
        msgs = messages(meta_capture)
        reference = FlashImage(write_packs(msgs))
        stamps = [stamp_from_capture(reference, msgs, i)
                  for i in range(len(gpx_paths))]
    else:
        # Distance and climb worked out from each GPX (route_from_gpx); the date stays neutral.
        stamps = [(None, None, None, (1, 1, 0, 0, 0, 0)) for _ in gpx_paths]
    routes = [route_from_gpx(path, *stamp) for path, stamp in zip(gpx_paths, stamps)]
    routes = replace_same_name(routes)
    for route in routes:
        print(f"  route {route.name!r}: {len(route.points)} points, "
              f"{len(route.waypoints)} waypoint(s)")
    return serialize(routes, wpt_size)


def compare_with_capture(link, capture):
    """Compares the 0x0b16 and 0x0b18 with those of the capture, payload by payload.
    Sequence numbers, which are session-specific, are out of the comparison: the HID
    framing is checked separately by hid_roundtrip.py.

    The POI write is only comparable in dry-run. A live run reads the list off the watch,
    which legitimately holds different POIs from whoever recorded the capture, so including
    it would turn `--write --compare` into a guaranteed failure on a payload that is
    correct. In dry-run the list comes from the capture itself, so it is compared.
    """
    compared = (CMD_DATA_WRITE, CMD_DATA_TAIL)
    if link.dry_run:
        compared += (CMD_POI_WRITE,)
    else:
        print("  note  the POI write is out of the comparison: a live run takes that "
              "list from the\n        watch, not from the capture")
    expected = [(m.command, m.payload) for m in messages(capture)
                if not m.incoming and m.command in compared]
    produced = [(command, payload) for command, payload, _ in link.sent
                if command in compared]
    if len(produced) != len(expected):
        print(f"\n  FAIL  {len(produced)} messages produced against "
              f"{len(expected)} in the capture")
        return False
    ok = True
    for i, (got, want) in enumerate(zip(produced, expected)):
        if got[0] != want[0]:
            print(f"  FAIL  message {i}: 0x{got[0]:04x} against 0x{want[0]:04x}")
            ok = False
        elif got[1] != want[1]:
            ok = False
            # the second word of the 0x0b18 is supplied by the application, flag it
            differing = [k for k in range(min(len(got[1]), len(want[1])))
                         if got[1][k] != want[1][k]]
            only_extra = got[0] == CMD_DATA_TAIL and all(4 <= k < 8 for k in differing)
            print(f"  {'INFO ' if only_extra else 'FAIL '} message {i} "
                  f"0x{got[0]:04x}: bytes {differing[:8]}"
                  + ("  (word supplied by the application)" if only_extra else ""))
            if only_extra:
                ok = True
    kinds = "0x0b16/0x0b18/0x0b25" if link.dry_run else "0x0b16/0x0b18"
    print(f"\n  {'OK   ' if ok else 'FAIL '} {len(produced)} {kinds} "
          f"payloads compared to {capture}")
    return ok


def reply_from_capture(capture, command):
    for m in messages(capture):
        if m.command == command and m.incoming and m.payload:
            return m.payload
    raise ValueError(f"no 0x{command:04x} reply in {capture}")


def run_query(args):
    """READ-ONLY: the three queries send a request and decode the reply, and none of them
    writes, so none takes --write.

    `logbook` returns one page. The watch pages a long list, newest move first, and the
    continuation cursor sits in the reply prefix; paging is not implemented because a run
    made to look at the newest activity does not need it.
    """
    command, request, interesting = QUERIES[args.action]

    if args.from_capture:
        try:
            payload = reply_from_capture(args.from_capture, command)
        except ValueError as exc:
            print(f"  {exc}.")
            return 1
        print(f"### {args.from_capture}, 0x{command:04x} reply ({len(payload)} B)")
    else:
        link = Link(dry_run=False, verbose=args.verbose, product_id=args.product_id)
        print(f"read-only: the 0x{command:04x} query, nothing is written")
        link.open()
        payload = link.command(command, request)
        print(f"  reply {len(payload)} B")

    descriptor = schema_for_link(link) if not args.from_capture else None
    if args.action == "settings":
        return 0 if show_settings(payload, args.all, args.redact, descriptor) is not None else 1
    # For POIs, a reply with no SBEM0102 payload is not an error - it means the watch has zero
    # POIs (read_pois()'s own docstring already notes this). Real bug, 2026-08-15: a Traverse
    # with 0 waypoints returned no payload, show_entries() read that as "cannot answer" -> exit
    # 1 -> the desktop /api/pois 502'd and the POIs page errored. `empty_is_ok` makes an empty
    # POI database a valid empty result. (settings keeps the strict behaviour: there, an empty
    # reply genuinely can't distinguish a paired from an unpaired watch.)
    return 0 if show_entries(payload, interesting, args.all, args.redact, descriptor,
                             empty_is_ok=(args.action == "pois")) is not None else 1


def show_entries(payload, interesting, show_all=False, redacted=False, descriptor=None,
                 empty_is_ok=False):
    """Names and decodes a reply's SBEM entries. Returns None when the schema is missing,
    for the same reason show_settings() does: an unnamed dump must not read as an answer.
    With `empty_is_ok` (POIs), a reply carrying no SBEM0102 payload is a real empty database,
    not a failure - reported as zero records (return 0) rather than None.
    """
    import sbem_schema

    head = payload.find(sbem_schema.MAGIC)
    if head < 0:
        if empty_is_ok:
            print("  no POIs on this watch (empty database)")
            return 0
        print("  no SBEM0102 payload in the reply")
        return None
    descriptor = descriptor or sbem_schema.default_descriptor()
    if not descriptor.exists():
        print(f"  CANNOT DECIDE: the SuuntoLink descriptor is missing from "
              f"{descriptor.parent},\n  so the entries cannot be named. See "
              f"tools/sbem_schema.py.")
        for entry_id, data in sbem_schema.entries(payload[head:]):
            print(f"  0x{entry_id:02x} [{len(data)}] {data[:32].hex(' ')}")
        return None

    schema = sbem_schema.load(descriptor)
    shown = 0
    for entry_id, data in sbem_schema.entries(payload[head:]):
        if not (show_all or entry_id in interesting):
            continue
        print(f"  0x{entry_id:02x} {schema.label(entry_id) or '?'}  [{len(data)}]")
        for record in schema.decode_entry(entry_id, data) or []:
            shown += 1
            print("        " + "  ".join(
                f"{schema.field_name(entry_id, f.fid)}="
                f"{show_value(schema.field_name(entry_id, f.fid), v, redacted)}"
                for f, v in record))
    print(f"\n  {shown} record(s)")
    return shown


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action",
                        choices=("reset", "route", "settings", "pois",
                                 "logbook", "nav", "restore", "addpoi", "delpoi", "delroute"))
    parser.add_argument("gpx", nargs="*")
    parser.add_argument("--name", metavar="NAME",
                        help="addpoi: the POI's name as the watch will show it")
    parser.add_argument("--lat", type=float, metavar="DEG",
                        help="addpoi: latitude, decimal degrees")
    parser.add_argument("--lon", type=float, metavar="DEG",
                        help="addpoi: longitude, decimal degrees")
    parser.add_argument("--type", metavar="ID_OR_NAME", default=None,
                        help="addpoi: POI type - a 0-17 id or a name (Building, Cave, Camp, "
                             "Car, Crossroads, Beginning, End, Food, Forest, Geocache, "
                             "Lodging, Meadow, Mountain, Sight, Road, Rock, Water, Waypoint). "
                             "Default Waypoint (17), what the watch itself uses.")
    parser.add_argument("--backup-dir", metavar="DIR", default=None,
                        help="delpoi: where the copy of the POI list goes (default "
                             "~/AmbitAppBackups; saved as POIs/<serial>/POIs_pre_<time>.bin)")
    parser.add_argument("--write", action="store_true",
                        help="actually emits; without this option nothing is sent")
    parser.add_argument("--meta", metavar="CAPTURE",
                        help="takes distance, ascent, descent and timestamp from it "
                             "(default: worked out from the GPX)")
    parser.add_argument("--compare", metavar="CAPTURE",
                        help="checks the simulated payloads against a capture")
    parser.add_argument("--from", metavar="CAPTURE", dest="from_capture",
                        help="settings, pois, logbook, nav: decode a capture, no watch")
    parser.add_argument("--all", action="store_true",
                        help="settings: every entry, not just the BLE bonds and pods")
    parser.add_argument("--redact", action="store_true",
                        help="settings: mask keys and MAC, output safe to send")
    parser.add_argument("--save", metavar="PREFIX",
                        help="nav: also write the raw regions to PREFIX-*.bin")
    parser.add_argument("--route-gpx", type=int, metavar="INDEX",
                        help="nav: export on-watch route INDEX's full points as GPX "
                             "(<rte>/<rtept>) - see --route-gpx-out")
    parser.add_argument("--route-gpx-out", metavar="PATH",
                        help="nav --route-gpx: write to this file instead of stdout")
    parser.add_argument("--json", action="store_true",
                        help="nav: also prints every on-watch route's real points as JSON "
                             "(on the last stdout line) - no extra USB read, reuses the "
                             "same flash data already read for the summary above it")
    parser.add_argument("--routes-gpx-json", action="store_true",
                        help="nav: print {ok, routes:[{name, pointCount, gpx}]} on the last "
                             "stdout line - every on-watch route's proven route_to_gpx() text "
                             "from the SINGLE flash read already done, so a caller (the two-"
                             "watch sync snapshot) gets all routes' GPX without one slow USB "
                             "read per route")
    parser.add_argument("--set-pois-json", metavar="PATH",
                        help="route/reset/restore: instead of restoring the POIs read off the "
                             "watch, SET the watch's whole POI list to the JSON list in PATH "
                             "([{name,lat,lon,type?}, ...]). The nav write + 0x0b04 commit above "
                             "clears the POI region, so this 0x0b25 sets rather than appends - "
                             "the SuuntoLink poiimport sequence. An empty list clears all POIs.")
    parser.add_argument("--from-leftovers", action="store_true",
                        help="restore: undo a reset by rebuilding the headers from whatever "
                             "routes are still in the saved regions, removed ones included. "
                             "Without it, restore writes back exactly the routes and POIs the "
                             "backup lists.")
    parser.add_argument("--verbose", action="store_true",
                        help="logs every 64-byte report")
    parser.add_argument("--device", metavar="NAME",
                        help="case-insensitive substring match against PRODUCT_IDS' own "
                             "labels (e.g. 'kailash', 'ambit3 peak') - which watch to open "
                             "when more than one is plugged in at once; without this, the "
                             "first one that opens wins, same as always")
    args = parser.parse_args()
    args.product_id = resolve_product_id(args.device) if args.device else None

    if args.action == "route" and not args.gpx:
        parser.error("route expects at least one GPX")
    if args.action == "restore" and len(args.gpx) != 1:
        parser.error("restore expects the prefix used by `nav --save`")
    if args.action == "nav":
        if args.write:
            parser.error("nav is read-only, --write has nothing to write")
        return run_nav(args)
    if args.action == "addpoi":
        return run_addpoi(args)
    if args.action == "delpoi":
        return run_delpoi(args)
    if args.action == "delroute":
        return run_delroute(args)
    if args.action in QUERIES:
        if args.write:
            parser.error(f"{args.action} is read-only, --write has nothing to write")
        return run_query(args)
    if args.from_capture or args.all or args.redact or args.save:
        parser.error("--from, --all, --redact and --save do not apply to reset or route")

    link = Link(dry_run=not args.write, verbose=args.verbose, product_id=args.product_id)
    if args.write:
        print("!! REAL WRITE requested")
        link.open()
    else:
        print("dry-run mode: not a byte will be emitted")

    info = link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    # SuuntoLink reads the POI list here, before the memory map, and writes it back
    # after the commit. Skipping that is what erased André's POIs on 2026-08-04.
    pois = read_pois(link, args.compare or args.meta)
    check_memory_map(read_memory_map(link))

    wsize = waypoint_size(link)
    if wsize != F.WAYPOINT_SIZE:
        print(f"  waypoint records of {wsize} bytes (Traverse layout)")
    restore_names, restore_pois_kind, restore_pois = None, None, None
    if args.action == "reset":
        flash, layout = build_reset(wsize)
    elif args.action == "restore" and args.from_leftovers:
        flash, layout = build_restore(args.gpx[0])
    elif args.action == "restore":
        # The backup's own routes and POIs, so the watch ends up matching it.
        routes = backup_routes_as_gpx(args.gpx[0])
        restore_names = [r["name"] for r in routes]
        flash, layout = build_routes_from_gpx_texts([r["gpx"] for r in routes], wsize)
        if args.set_pois_json is None:
            restore_pois_kind, restore_pois = backup_poi_list(args.gpx[0])
        print(f"  restoring {len(routes)} route(s) from the backup")
    else:
        flash, layout = build_routes([pathlib.Path(p) for p in args.gpx], args.meta, wsize)
    backup = None
    if args.set_pois_json is None:
        backup = backup_pois_before_route_write(link, pois, serial_from_device_info(info),
                                                args.backup_dir)
        if backup:
            print(f"  POIs saved first: {backup}")
    try:
        send_plan(link, flash, layout)
    except (RuntimeError, TimeoutError, OSError):
        if args.set_pois_json is None and not link.dry_run:
            _back, err = put_pois_back_safely(link, pois, backup)
            print("  POIs put back after the failed write" if err is None else f"!! {err}")
        raise

    # The 0x0b04 commit in send_plan() just cleared the POI SBEM region, so the 0x0b25 below
    # SETS the whole list (the SuuntoLink poiimport sequence) rather than appending. With
    # --set-pois-json we set a caller-supplied list instead of restoring what was read.
    pois_from, pois_total = "watch", poi_restore_count(pois)
    if restore_pois_kind == "reply":
        # Restore: the backup's POIs as they were read off the watch, byte for byte.
        pois_from, pois_total = "backup", poi_restore_count(restore_pois)
        if pois_total:
            _back, err = put_pois_back_safely(link, restore_pois, backup)
            if err:
                raise RuntimeError(err)
        print(f"  POIs: {pois_total} from the backup")
    elif restore_pois_kind == "list":
        # Older backups: the POI list from their -sync.json snapshot.
        pois_from, pois_total = "backup", len(restore_pois)
        payloads = build_set_pois_payloads(restore_pois)
        for payload in payloads:
            link.command(CMD_POI_WRITE, payload)
        if payloads and not check_poi_count(link, len(restore_pois)):
            raise RuntimeError(f"the watch did not keep the {len(restore_pois)} POIs from the "
                               f"backup" + (f"; its POIs before the restore are in {backup}"
                                            if backup else ""))
        print(f"  POIs: {pois_total} from the backup")
    elif args.set_pois_json is not None:
        set_list = json.loads(pathlib.Path(args.set_pois_json).read_text())
        payloads = build_set_pois_payloads(set_list)   # [] for an empty list -> POIs cleared
        for payload in payloads:
            link.command(CMD_POI_WRITE, payload)
        if payloads:
            print(f"  set POIs: {len(set_list)} written into the cleared region")
            check_poi_count(link, len(set_list))
        else:
            print(f"  set POIs: empty list -> all POIs cleared")
    else:
        restored = poi_restore_payloads(pois)
        if restored:
            _back, err = put_pois_back_safely(link, pois, backup)
            if err:
                raise RuntimeError(err)
        elif link.dry_run and not (args.compare or args.meta):
            # A dry-run has no watch to ask, so it cannot show the 0x0b25 a live run will
            # send. Saying "no POI" here once made a rehearsal announce one message fewer than
            # the real write, on a watch that did have a POI. A rehearsal must not undercount.
            print("  a live run would read the watch's POI list here and write it back "
                  "afterwards,\n  which this rehearsal cannot show: expect one more 0x0b25 "
                  "than the count below.\n  Give --compare or --meta to rehearse that message "
                  "against a capture.")
        else:
            print("  no POI to put back")

    total = sum(len(payload) for _, payload, _ in link.sent)
    reports = sum(len(r) for _, _, r in link.sent)
    print(f"\n{len(link.sent)} messages, {total} payload bytes, "
          f"{reports} reports of 64 bytes"
          + ("" if args.write else " — nothing was emitted"))
    if args.compare:
        return 0 if compare_with_capture(link, args.compare) else 1
    if args.json and restore_names is not None:
        print(json.dumps({"ok": True, "wrote": bool(args.write), "routes": restore_names,
                          "pois": pois_total, "poisFrom": pois_from}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
