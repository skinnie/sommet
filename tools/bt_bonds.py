#!/usr/bin/env python3
"""The Bluetooth devices this computer is paired with that Sommet cares about - Suunto watches
and the Magene C406 - for Home's "Forget Bluetooth device" (André, 2026-09-26: "Forget
bluetooth device which would show all paired devices").

    ./tools/bt_bonds.py list            -> {ok, devices:[{address, name, kind}]}
    ./tools/bt_bonds.py forget ADDR     -> {ok, address}

Linux/BlueZ via `bluetoothctl` (stdlib only). Scoped on purpose: only Suunto (OUI 0C:8C:DC or
an Ambit3/Traverse/Kailash/Suunto NSP name - ble_server.py's own rule) and Magene (its
8ce5cc01 service - magene_import.py's CC_SERVICE) are listed, and `forget` re-checks the
address is one of those before removing anything, so a mouse or headset is never touched.
Elsewhere (no bluetoothctl): {ok:false, unsupported:true} - the OS's own Bluetooth settings.
"""

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


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
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
