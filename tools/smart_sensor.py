#!/usr/bin/env python3
"""Reads the Suunto Smart Sensor's own identity/status over BLE - manufacturer, model,
serial, hardware/firmware/software revision, battery level, and (if it's being worn) a
live heart rate sample.

This is plain standard BLE GATT - the Device Information Service (0x180A), Battery
Service (0x180F), and Heart Rate Service (0x180D) - not the vendor NSP protocol
`ble_link.py` speaks to the watches. No pairing/auth needed to read most of it, though a
device the OS has never seen at all still needs a normal scan first.

Real, confirmed live 2026-08-13 against an actual Suunto Smart Sensor
(0C:8C:DC:0D:D8:4C): it identifies itself as a **Movesense**-platform sensor
(Manufacturer="Suunto", Model="Movesense", HW="00.D9", FW="6.0077", SW="1.1.0") - the
"Suunto Smart Sensor"/"BlueBelt" name is only Suunto's own branding/device-catalog
codename (`assets/.../devices.xml`), not what the hardware calls itself.

Talks to BlueZ directly via `bluetoothctl`/`busctl` subprocess calls, not bleak -
deliberately: bleak's own `BleakClient.connect()` always re-runs an active discovery scan
to locate the device first (`bleak/backends/bluezdbus/client.py`), which fails for a
device BlueZ already has connected (a connected peripheral stops advertising, so a fresh
scan can't see it) - exactly the common case here, since the belt is usually already
paired/connected from an earlier session. Reading GATT characteristics this way needs no
persistent connection, so a one-shot `busctl call ... ReadValue` per field is fine; heart
rate is the one exception - BlueZ ties a `StartNotify` subscription to the D-Bus
connection that requested it, so that piece alone uses a real persistent connection
(`dbus_fast`, already a bleak dependency) instead of a one-shot subprocess.

    ./tools/smart_sensor.py --status --json
    ./tools/smart_sensor.py --status                  # human-readable

Real gotcha hit live 2026-08-13: heart_rate_bpm came back null through the app every time,
identity/battery fine, while this same file run directly always worked. Cause: identity/
battery only ever shell out to `busctl`/`bluetoothctl`, so they work under any Python, but
the HR listener needs `dbus_fast` in the SAME interpreter that runs this file -
`desktop/backend/server.py`'s `PYTHON = sys.executable` is whatever process happened to
start the backend, not necessarily this repo's own venv. If HR is always null despite good
contact, check `dbus_fast` is installed for that specific interpreter, not just this one.
"""

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time

DEVICE_NAME_HINT = "smart sensor"

UUID_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"
UUID_MODEL = "00002a24-0000-1000-8000-00805f9b34fb"
UUID_SERIAL = "00002a25-0000-1000-8000-00805f9b34fb"
UUID_HW_REVISION = "00002a27-0000-1000-8000-00805f9b34fb"
UUID_FW_REVISION = "00002a26-0000-1000-8000-00805f9b34fb"
UUID_SW_REVISION = "00002a28-0000-1000-8000-00805f9b34fb"
UUID_BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"
UUID_HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"

_PATH_RE = re.compile(r"(/org/bluez/\S+)")


def _run(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _find_address():
    """Address + advertised name of the belt, by whatever BlueZ already knows about it -
    already-connected first (the fast, common case), then already-paired-but-disconnected,
    then (only if neither knows it) a real scan so a never-before-seen belt is still found."""
    for filt in ("Connected", "Paired"):
        proc = _run(["bluetoothctl", "devices", filt], timeout=6)
        for line in proc.stdout.splitlines():
            parts = line.split(" ", 2)
            if len(parts) == 3 and parts[0] == "Device" and DEVICE_NAME_HINT in parts[2].lower():
                return parts[1], parts[2]

    _run(["bluetoothctl", "--timeout", "8", "scan", "on"], timeout=12)
    proc = _run(["bluetoothctl", "devices"], timeout=6)
    for line in proc.stdout.splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[0] == "Device" and DEVICE_NAME_HINT in parts[2].lower():
            return parts[1], parts[2]
    return None, None


def _device_path(address):
    """The dev_XX_XX_.. object path BlueZ filed this address under - found by searching the
    live tree rather than assuming hci0, since which adapter owns it isn't otherwise known."""
    marker = "dev_" + address.replace(":", "_")
    proc = _run(["busctl", "tree", "org.bluez"], timeout=10)
    for line in proc.stdout.splitlines():
        if marker not in line:
            continue
        m = _PATH_RE.search(line)
        if not m:
            continue
        path = m.group(1)
        return path[:path.index(marker) + len(marker)]
    return None


def _adapter_path(device_path):
    """The owning adapter's own object path (e.g. /org/bluez/hci0) - RemoveDevice is a
    method on the adapter, not the device itself."""
    idx = device_path.rfind("/dev_")
    return device_path[:idx] if idx != -1 else None


def forget():
    """Unpairs and fully removes the belt from BlueZ (org.bluez.Adapter1.RemoveDevice) -
    the same effect as `bluetoothctl remove <address>`. Real request, 2026-08-13 (André,
    after testing the paired/connected flow end to end): a way back to a clean slate so
    Pair can be exercised again, without a terminal. Not destructive to the belt itself -
    it has no PIN/bond secret worth losing (Just Works pairing), so this is just a
    Bluetooth-side reset, safely repeatable."""
    address, name = _find_address()
    if not address:
        return {"ok": True, "found": False}
    device_path = _device_path(address)
    if not device_path:
        return {"ok": False, "error": f"BlueZ knows {address} but has no D-Bus object for it"}
    adapter_path = _adapter_path(device_path)
    if not adapter_path:
        return {"ok": False, "error": f"couldn't determine the adapter owning {device_path}"}
    proc = _run(["busctl", "call", "org.bluez", adapter_path, "org.bluez.Adapter1",
                 "RemoveDevice", "o", device_path], timeout=15)
    if proc.returncode != 0:
        return {"ok": False, "error": proc.stderr.strip() or "RemoveDevice failed"}
    return {"ok": True, "found": True, "address": address, "name": name}


def _get_property(path, iface, name):
    proc = _run(["busctl", "get-property", "org.bluez", path, iface, name], timeout=8)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _wait_services_resolved(device_path, timeout=30.0):
    """A device this process just connected (as opposed to one BlueZ already had
    connected) has its GATT tree populated asynchronously - real behavior hit live
    2026-08-13: reading characteristics immediately after a fresh Connect() found none at
    all, because `ServicesResolved` hadn't flipped yet. Already-connected devices return
    immediately here since it's already true. The generous 30s default is sized for the
    slowest real case measured - a just-forgotten belt reconnecting completely from
    scratch (no cached GATT database at all) genuinely took ~15s of real BLE discovery
    time on this hardware in a clean, isolated test - not the already-known-device case,
    which resolves in well under a second and returns from here immediately regardless."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resolved = _get_property(device_path, "org.bluez.Device1", "ServicesResolved")
        if resolved and "true" in resolved:
            return True
        time.sleep(0.3)
    return False


def _ensure_connected(device_path):
    connected = _get_property(device_path, "org.bluez.Device1", "Connected")
    if connected and "true" in connected:
        return _wait_services_resolved(device_path)
    proc = _run(["busctl", "call", "org.bluez", device_path, "org.bluez.Device1", "Connect"],
                timeout=20)
    if proc.returncode != 0:
        return False
    return _wait_services_resolved(device_path)


def _char_uuid_map(device_path):
    """{uuid: object_path} for every GATT characteristic under this device - only known
    after Connect() has resolved services, which is why this runs after _ensure_connected."""
    result = {}
    proc = _run(["busctl", "tree", "org.bluez"], timeout=10)
    char_paths = []
    for line in proc.stdout.splitlines():
        if device_path not in line or "/char" not in line:
            continue
        m = _PATH_RE.search(line)
        if m:
            char_paths.append(m.group(1))
    for path in char_paths:
        raw = _get_property(path, "org.bluez.GattCharacteristic1", "UUID")
        if not raw:
            continue
        # `busctl get-property` prints e.g.: s "00002a29-0000-1000-8000-00805f9b34fb"
        m = re.search(r'"([0-9a-f-]{36})"', raw)
        if m:
            result[m.group(1).lower()] = path
    return result


def _read_value(path):
    """Raw bytes of a characteristic, or None (unreadable/notify-only/gone) - never raises,
    every field here is best-effort against real hardware that may not implement it."""
    proc = _run(["busctl", "call", "org.bluez", path,
                 "org.bluez.GattCharacteristic1", "ReadValue", "a{sv}", "0"], timeout=8)
    if proc.returncode != 0:
        return None
    tokens = proc.stdout.split()
    if len(tokens) < 2 or tokens[0] != "ay":
        return None
    try:
        count = int(tokens[1])
        return bytes(int(b) for b in tokens[2:2 + count])
    except ValueError:
        return None


def _read_str(uuid_map, uuid):
    path = uuid_map.get(uuid)
    if not path:
        return None
    data = _read_value(path)
    return data.decode("utf-8", errors="replace").strip("\x00").strip() if data else None


def _read_battery(uuid_map):
    path = uuid_map.get(UUID_BATTERY_LEVEL)
    data = _read_value(path) if path else None
    return data[0] if data else None


async def _start_hr_listener(char_path):
    """Opens the real persistent D-Bus connection HR notifications need (see this file's
    own docstring for why it alone can't use the one-shot `busctl call` pattern every other
    field uses) and subscribes - but does NOT wait. Returns a session handle to hand to
    `_finish_hr_listener` once the rest of the read is done, so the belt spends the whole
    operation being listened to instead of only a short tacked-on-the-end window."""
    if char_path is None:
        return None
    try:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus
    except ImportError:
        return None

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        introspection = await bus.introspect("org.bluez", char_path)
        obj = bus.get_proxy_object("org.bluez", char_path, introspection)
        char_iface = obj.get_interface("org.bluez.GattCharacteristic1")
        props_iface = obj.get_interface("org.freedesktop.DBus.Properties")

        got = {}

        def _on_changed(_interface, changed, _invalidated):
            variant = changed.get("Value")
            if not variant:
                return
            data = bytes(variant.value)
            if not data:
                return
            flags = data[0]
            got["bpm"] = int.from_bytes(data[1:3], "little") if flags & 0x01 else data[1]

        props_iface.on_properties_changed(_on_changed)
        await char_iface.call_start_notify()
        return {"bus": bus, "char_iface": char_iface, "got": got, "started": time.monotonic()}
    except Exception:
        bus.disconnect()
        return None


async def _finish_hr_listener(session, min_total_seconds):
    """The most recent bpm seen since `_start_hr_listener`, topping up the wait (if the
    rest of the read finished fast) so every read spends at least `min_total_seconds`
    actually listening - real behavior hit live 2026-08-13: a flat 5-8s window tacked on
    AFTER already reading every other field could run out before the belt's own HR
    algorithm had locked onto a stable reading, even while genuinely being worn. Returns
    None (not an error) if nothing arrived at all - not worn, dry electrodes, or poor
    contact are all real, expected, non-error outcomes for this class of strap."""
    if session is None:
        return None
    remaining = min_total_seconds - (time.monotonic() - session["started"])
    if remaining > 0:
        await asyncio.sleep(remaining)
    try:
        await session["char_iface"].call_stop_notify()
    except Exception:
        pass
    session["bus"].disconnect()
    return session["got"].get("bpm")


async def read_status(hr_min_seconds=12.0):
    loop = asyncio.get_running_loop()
    address, name = _find_address()
    if not address:
        return {"ok": True, "found": False}

    device_path = _device_path(address)
    if not device_path:
        return {"ok": False, "error": f"BlueZ knows {address} but has no D-Bus object for it"}

    if not _ensure_connected(device_path):
        return {"ok": False, "error": f"could not connect to {name} ({address}), or its "
                                       "GATT services never resolved"}

    uuid_map = await loop.run_in_executor(None, _char_uuid_map, device_path)

    # HR notifications start listening now, BEFORE the other (blocking, one-shot `busctl`)
    # reads below run - each of those is handed to the executor so this process's own
    # event loop stays free to receive them concurrently, rather than the listener only
    # getting whatever's left of a fixed window after everything else already finished.
    hr_session = await _start_hr_listener(uuid_map.get(UUID_HR_MEASUREMENT))

    manufacturer = await loop.run_in_executor(None, _read_str, uuid_map, UUID_MANUFACTURER)
    model = await loop.run_in_executor(None, _read_str, uuid_map, UUID_MODEL)
    serial = await loop.run_in_executor(None, _read_str, uuid_map, UUID_SERIAL)
    hw_revision = await loop.run_in_executor(None, _read_str, uuid_map, UUID_HW_REVISION)
    fw_revision = await loop.run_in_executor(None, _read_str, uuid_map, UUID_FW_REVISION)
    sw_revision = await loop.run_in_executor(None, _read_str, uuid_map, UUID_SW_REVISION)
    battery_percent = await loop.run_in_executor(None, _read_battery, uuid_map)
    heart_rate = await _finish_hr_listener(hr_session, hr_min_seconds)

    return {
        "ok": True,
        "found": True,
        "address": address,
        "name": name,
        "manufacturer": manufacturer,
        "model": model,
        "serial": serial,
        "hw_revision": hw_revision,
        "fw_revision": fw_revision,
        "sw_revision": sw_revision,
        "battery_percent": battery_percent,
        "heart_rate_bpm": heart_rate,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--status", action="store_true",
                     help="find and read the Smart Sensor's identity/battery/heart rate")
    ap.add_argument("--forget", action="store_true",
                     help="unpair/remove the belt from Bluetooth entirely")
    ap.add_argument("--json", action="store_true",
                     help="print one JSON object instead of human-readable lines - for "
                          "desktop/backend/server.py, not meant for a person to read")
    args = ap.parse_args()
    if not args.status and not args.forget:
        ap.error("nothing to do - pass --status or --forget")

    result = forget() if args.forget else asyncio.run(read_status())

    if args.json:
        print(json.dumps(result))
        return 0 if result.get("ok") else 1

    if not result.get("ok"):
        print(f"  error: {result.get('error')}")
        return 1
    if not result.get("found"):
        print("  nothing to do - no Suunto Smart Sensor known to Bluetooth")
        return 0
    if args.forget:
        print(f"  forgot {result['name']} ({result['address']})")
        return 0
    print(f"  {result['name']}  ({result['address']})")
    print(f"  manufacturer     {result.get('manufacturer')}")
    print(f"  model            {result.get('model')}")
    print(f"  serial           {result.get('serial')}")
    print(f"  hardware rev     {result.get('hw_revision')}")
    print(f"  firmware rev     {result.get('fw_revision')}")
    print(f"  software rev     {result.get('sw_revision')}")
    print(f"  battery          {result.get('battery_percent')}%")
    hr = result.get("heart_rate_bpm")
    print(f"  heart rate       {str(hr) + ' bpm' if hr is not None else 'not worn / no reading'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
