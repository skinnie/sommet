#!/usr/bin/env python3
"""Persistent registry of Suunto watches this app has seen, keyed by serial.

Why this exists: to flash or recover a watch we need its model codename + hardware version
to fetch the right firmware (firmware_check.py). A watch stuck in the bootloader (BSL) still
reports its SERIAL and hw version, but reports its model as "BSL" - so on its own it cannot
tell us its codename, and we cannot pick the right image. Recording (codename, hw) against
the serial on every normal app-mode connection lets a later BSL recovery look the watch up
by serial and download the correct firmware with no SuuntoLink.

This is the data layer only. The GUI recovery flow that consumes it (auto-identify by
serial, else a "which watch do you want to recover?" picker, else the SuuntoLink message)
is described in FIRMWARE_FLASHER_DESIGN.md.

Storage: a JSON object keyed by serial at $SOMMET_DATA/known_watches.json (default
~/.config/Sommet/known_watches.json), so it is shared across every tool and the backend.
"""

import json
import os
import pathlib
import time

from write_nav import PRODUCT_IDS


def registry_path():
    base = os.environ.get("SOMMET_DATA")
    root = pathlib.Path(base) if base else pathlib.Path.home() / ".config" / "Sommet"
    return root / "known_watches.json"


def product_name(codename):
    """Friendly product name for a model codename, taken from the PRODUCT_IDS labels
    ('Ambit3 Peak (Emu)' -> 'Ambit3 Peak'). Falls back to the codename itself."""
    for label in PRODUCT_IDS.values():
        if label.endswith(f"({codename})"):
            return label.rsplit("(", 1)[0].strip()
    return codename


def load():
    try:
        return json.loads(registry_path().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def record(info):
    """Upsert a watch from a device_info dict {model, serial, fw_version, hw_version}.
    No-op for a BSL watch (its model is the bootloader, not a real codename) or a missing
    serial. Returns the stored entry, or None if nothing was recorded."""
    model, serial = info.get("model"), info.get("serial")
    if not serial or model in (None, "", "BSL"):
        return None
    entry = {
        "codename": model,
        "product": product_name(model),
        "hw_version": info.get("hw_version"),
        "last_fw": info.get("fw_version"),
        "last_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    reg = load()
    reg[serial] = entry
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg, indent=2, sort_keys=True))
    return entry


def lookup(serial):
    """The stored entry for a serial, or None. Used at flash time to identify a watch that
    is currently in BSL (which hides its codename but still reports its serial)."""
    return load().get(serial)


def known():
    """Every known watch, newest-seen first, each carrying its own 'serial' - the list a
    recovery picker offers when a BSL watch's serial isn't recognized."""
    out = [{**v, "serial": s} for s, v in load().items()]
    out.sort(key=lambda e: e.get("last_seen", ""), reverse=True)
    return out


if __name__ == "__main__":
    import sys
    if "--json" in sys.argv[1:]:
        print(json.dumps({"ok": True, "watches": known()}))
    else:
        for w in known():
            print(f"  {w['product']:16} ({w['codename']})  hw {w.get('hw_version')}  "
                  f"serial {w['serial']}  last fw {w.get('last_fw')}  seen {w.get('last_seen')}")
        print(f"  registry: {registry_path()}")
