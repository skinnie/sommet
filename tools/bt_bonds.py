#!/usr/bin/env python3
"""The Bluetooth devices this computer is paired with that Sommet cares about - Suunto watches
and the Magene C406 - for Home's "Forget Bluetooth device" (André, 2026-09-26: "Forget
bluetooth device which would show all paired devices").

    ./tools/bt_bonds.py list            -> {ok, devices:[{address, name, kind}]}
    ./tools/bt_bonds.py forget ADDR     -> {ok, address}

Linux/BlueZ via `bluetoothctl` (stdlib only); Windows through its own pairing API (WinRT, the
packages the Windows download ships for Bluetooth; Suunto watches only, André 2026-11-23: the
button listed nothing there and removed nothing from Windows). Scoped on purpose: only Suunto (OUI 0C:8C:DC or
an Ambit3/Traverse/Kailash/Suunto NSP name - ble_server.py's own rule) and Magene (its
8ce5cc01 service - magene_import.py's CC_SERVICE) are listed, and `forget` re-checks the
address is one of those before removing anything, so a mouse or headset is never touched.
Elsewhere (macOS): {ok:false, unsupported:true} - the OS's own Bluetooth settings.
"""

import asyncio
import json
import re
import shutil
import subprocess
import sys

SUUNTO_OUI = "0C:8C:DC"
SUUNTO_NAMES = ("Ambit3", "Traverse", "Suunto NSP", "Kailash")
MAGENE_SERVICE = "8ce5cc01-0a4d-11e9-ab14-d663bd873d93"
ADDR_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")


def _ctl(*args, timeout=15):
    return subprocess.run(["bluetoothctl", *args], capture_output=True, text=True,
                          timeout=timeout).stdout


def _info(address):
    out = _ctl("info", address)
    name = re.search(r"^\s*Name:\s*(.*)$", out, re.M)
    return {
        "name": name.group(1).strip() if name else "",
        "paired": re.search(r"^\s*Paired:\s*yes", out, re.M) is not None,
        "magene": MAGENE_SERVICE in out.lower(),
    }


def _kind(address, info):
    if address.upper().startswith(SUUNTO_OUI) or info["name"].startswith(SUUNTO_NAMES):
        return "suunto"
    if info["magene"]:
        return "magene"
    return None


def list_devices():
    out = _ctl("devices", "Paired")
    devices = []
    for m in re.finditer(r"^Device\s+([0-9A-F:]{17})\s+(.*)$", out, re.M):
        address = m.group(1).upper()
        info = _info(address)
        if not info["paired"]:
            continue
        kind = _kind(address, info)
        if kind:
            devices.append({"address": address, "name": info["name"] or m.group(2).strip(),
                            "kind": kind})
    return devices


def forget(address):
    address = (address or "").upper()
    if not ADDR_RE.match(address):
        raise ValueError("bad Bluetooth address")
    info = _info(address)
    if _kind(address, info) is None:
        raise ValueError("not a Suunto watch or Magene - Sommet won't unpair it")
    out = _ctl("remove", address, timeout=30)
    if "not available" in out.lower() or "failed" in out.lower():
        raise ValueError(out.strip().splitlines()[-1] if out.strip() else "remove failed")
    return {"ok": True, "address": address}


# ---- Windows -----------------------------------------------------------------------------

def address_text(value):
    """WinRT gives a Bluetooth address as a 48-bit number."""
    return ":".join(f"{(int(value) >> shift) & 0xFF:02X}" for shift in range(40, -8, -8))


def is_suunto(address, name):
    return _kind(address, {"name": name or "", "magene": False}) == "suunto"


async def _windows_paired():
    """[(address, DeviceInformation)] of the Suunto watches Windows is paired with."""
    from winrt.windows.devices import enumeration as enum  # noqa: PLC0415
    from winrt.windows.devices.bluetooth import BluetoothLEDevice  # noqa: PLC0415
    selector = BluetoothLEDevice.get_device_selector_from_pairing_state(True)
    found = []
    for info in await enum.DeviceInformation.find_all_async_aqs_filter(selector):
        device = await BluetoothLEDevice.from_id_async(info.id)
        if device is None:
            continue
        address = address_text(device.bluetooth_address)
        device.close()
        if is_suunto(address, info.name):
            found.append((address, info))
    return found


def windows_list():
    return [{"address": address, "name": info.name, "kind": "suunto"}
            for address, info in asyncio.run(_windows_paired())]


def windows_forget(address):
    address = (address or "").upper()
    if not ADDR_RE.match(address):
        raise ValueError("bad Bluetooth address")

    async def go():
        # Only what the list above holds, so a mouse or headset can never be removed.
        targets = [info for found, info in await _windows_paired() if found == address]
        if not targets:
            raise ValueError("that watch is not paired with this computer")
        for info in targets:
            result = await info.pairing.unpair_async()
            if result.status.name not in ("UNPAIRED", "ALREADY_UNPAIRED"):
                raise ValueError(f"Windows did not remove it ({result.status.name})")
    asyncio.run(go())
    return {"ok": True, "address": address}


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    if sys.platform == "win32":
        try:
            if a and a[0] == "list":
                out = {"ok": True, "devices": windows_list()}
            elif len(a) == 2 and a[0] == "forget":
                out = windows_forget(a[1])
            else:
                raise ValueError("usage: bt_bonds.py list | forget ADDR")
        except ImportError:
            out = {"ok": False, "unsupported": True,
                   "error": "use Windows' Bluetooth settings"}
        except (ValueError, OSError) as exc:
            out = {"ok": False, "error": str(exc)}
        print(json.dumps(out))
        return 0 if out.get("ok") else 1
    if not shutil.which("bluetoothctl"):
        print(json.dumps({"ok": False, "unsupported": True,
                          "error": "no bluetoothctl here - use the system's Bluetooth settings"}))
        return 1
    try:
        if a and a[0] == "list":
            out = {"ok": True, "devices": list_devices()}
        elif len(a) == 2 and a[0] == "forget":
            out = forget(a[1])
        else:
            raise ValueError("usage: bt_bonds.py list | forget ADDR")
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        out = {"ok": False, "error": str(exc)}
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
