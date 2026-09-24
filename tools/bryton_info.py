#!/usr/bin/env python3
"""Read a Bryton Aero 60's identity, firmware versions and lifetime totals from the plain-text
INI files it keeps on its USB mass-storage volume - read-only, stdlib only (André, 2026-09-24,
reversed from the Bryton Active app; the same files the app's `system.ini`/`release.ini` channel
exchanges over BLE are just sitting on the mounted drive).

    ./tools/bryton_info.py read /media/<user>/BRYTON      # JSON: model, firmware, odometer, trip

Sources under <mount>/System:
  * device.txt   - [MODEL] Model, [Product] Type, [UUID] UUID, per-component fw versions,
                   [WorkoutDownload] IsDisable
  * release.ini  - firmware component manifest (name/version/size) + [MODEL] Model
  * System.ini   - [BT] Addr, [System] ODO_km/ODO_mi + Trip21_km/Trip21_mi (lifetime totals)
Everything is read-only here; nothing on the device is written.
"""

import argparse
import configparser
import json
import os
import sys


def _read_ini(path):
    """Parse one Bryton INI leniently (they carry a trailing blank/space line and are ASCII).
    Returns a case-preserving {section: {key: value}}, or {} if unreadable."""
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str                      # keep key case (ODO_km, not odo_km)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            # The device pads these files to a block boundary with trailing NUL bytes; configparser
            # treats a NUL line as a parse error, so strip them (verified on a real Aero 60 -
            # device.txt/System.ini are NUL-padded, release.ini isn't).
            cp.read_string(fh.read().replace("\x00", ""))
    except (OSError, configparser.Error):
        return {}
    return {sec: dict(cp.items(sec)) for sec in cp.sections()}


def _num(s):
    """A float from a Bryton numeric string, or None."""
    try:
        return float(str(s).strip())
    except (TypeError, ValueError):
        return None


def read_info(mount):
    sysdir = os.path.join(mount, "System")
    device = _read_ini(os.path.join(sysdir, "device.txt"))
    release = _read_ini(os.path.join(sysdir, "release.ini"))
    system = _read_ini(os.path.join(sysdir, "System.ini"))

    model = (device.get("MODEL", {}).get("Model")
             or release.get("MODEL", {}).get("Model") or "")
    # Firmware versions: prefer device.txt (what's actually installed); release.ini carries the
    # same versions plus component file names/sizes, so fold those in for the manifest view.
    def comp(name):
        d = device.get(name, {})
        r = release.get(name, {})
        return {k: v for k, v in {
            "version": d.get("Version") or r.get("Version"),
            "name": r.get("Name"),
            "size": int(r["Size"]) if r.get("Size", "").isdigit() else None,
        }.items() if v is not None}

    firmware = {c: comp(c) for c in
                ("OS", "BootLoader", "DeviceLang", "NotificationLang", "TimeZone", "Bluetooth")}
    firmware = {c: v for c, v in firmware.items() if v}     # drop components this unit lacks

    odo_km = _num(system.get("System", {}).get("ODO_km"))
    odo_mi = _num(system.get("System", {}).get("ODO_mi"))
    trip_km = _num(system.get("System", {}).get("Trip21_km"))
    trip_mi = _num(system.get("System", {}).get("Trip21_mi"))

    return {
        "ok": True,
        "mount": mount,
        "model": model,                                     # e.g. "Aero60"
        "productType": device.get("Product", {}).get("Type"),   # e.g. "Cycle"
        "uuid": device.get("UUID", {}).get("UUID"),
        "btAddr": system.get("BT", {}).get("Addr"),
        "osVersion": firmware.get("OS", {}).get("version"),
        "firmware": firmware,
        "workoutDownloadDisabled": device.get("WorkoutDownload", {}).get("IsDisable") == "1",
        "odometerKm": odo_km,
        "odometerMi": odo_mi,
        "tripKm": trip_km,
        "tripMi": trip_mi,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["read"], help="read the device info as JSON")
    ap.add_argument("mount", help="the mounted Bryton volume (e.g. /media/<user>/BRYTON)")
    args = ap.parse_args()

    if not os.path.isdir(os.path.join(args.mount, "System")):
        print(json.dumps({"ok": False, "error": "not a Bryton volume (no System/ dir)",
                          "mount": args.mount}))
        return 1
    print(json.dumps(read_info(args.mount)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
