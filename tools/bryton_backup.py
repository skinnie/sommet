#!/usr/bin/env python3
"""Back up / restore a Bryton Aero 60's *configuration* — the parts a user sets up and would hate to
lose after a reset — over USB mass storage, stdlib only.

What this covers (André, 2026-11-08, "activity profiles/screens and sensors"):
  * SCREENS (activity data pages): System/Grid.ini — a plain file on the device, copied verbatim.
    This is the whole screens picture; tools/bryton_grid.py reads/edits the same file.
  * SENSORS: NOT here. The Aero 60 keeps paired sensors in internal flash, not on the USB volume —
    they are reachable only over BLE (Bryton Active's "command 44"; see tools/bryton_ble.py). Sensor
    backup is a separate step (bryton_ble.py sensors) that drops a sensors.json into the SAME backup
    folder this tool creates, so a backup can hold both halves.

A backup is a folder  <root>/bryton/<deviceId>/<timestamp>/  holding the copied files plus a
manifest.json (schema below). Restore validates the saved Grid.ini parses as real screens, keeps a
one-time Grid.ini.bak on the device, then copies the file back. Grid.ini is a config file on a FAT
volume, not firmware: the worst realistic failure is screens resetting, recoverable from the .bak.

    ./tools/bryton_backup.py backup  /media/<user>/BRYTON [--root ~/AmbitAppBackups]
    ./tools/bryton_backup.py list    [--root ~/AmbitAppBackups]
    ./tools/bryton_backup.py restore /media/<user>/BRYTON <backup-folder>

Every command prints one JSON line on stdout.
"""
import argparse
import datetime as _dt
import json
import os
import shutil
import sys

# Reuse bryton_grid.read() for validation (same folder; the backend runs tools with this dir importable).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bryton_grid  # noqa: E402

SCHEMA = "sommet-device-backup/1"
GRID_REL = os.path.join("System", "Grid.ini")
DEVICE_TXT = os.path.join("System", "device.txt")
DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), "AmbitAppBackups")


def _device_meta(mount):
    """{model, deviceId} from System/device.txt ([MODEL] Model=…, [UUID] UUID=…). Best-effort:
    an unreadable field just comes back as '' so a backup can still be made."""
    model, uuid = "", ""
    path = os.path.join(mount, DEVICE_TXT)
    try:
        for raw in open(path, "r", errors="replace"):
            line = raw.strip()
            if line.startswith("Model="):
                model = line.split("=", 1)[1].strip()
            elif line.startswith("UUID="):
                uuid = line.split("=", 1)[1].strip()
    except OSError:
        pass
    return model or "Bryton", uuid or "unknown"


def _grid_path(mount):
    p = mount if mount.endswith("Grid.ini") else os.path.join(mount, GRID_REL)
    if not os.path.isfile(p):
        raise FileNotFoundError(f"no Grid.ini at {p} — is this a Bryton volume?")
    return p


def _screen_count(mount):
    """How many data screens Grid.ini holds (validation + a human summary). Raises if it doesn't
    parse as the expected [PageN_Cycling] format."""
    pages = bryton_grid.read(mount)
    if not pages:
        raise ValueError("Grid.ini has no screens — refusing (not a valid Bryton grid)")
    return len(pages)


def backup(mount, root=DEFAULT_ROOT):
    grid = _grid_path(mount)                 # device-side validation that the file exists
    count = _screen_count(mount)             # parses <mount>/System/Grid.ini; raises if not real
    model, device_id = _device_meta(mount)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(root, "bryton", device_id, stamp)
    # Mirror the device layout (System/Grid.ini) so bryton_grid.read(dest) validates a backup, and
    # so a later sensors.json / more System/ files sit naturally beside it.
    os.makedirs(os.path.join(dest, "System"), exist_ok=True)
    shutil.copy2(grid, os.path.join(dest, GRID_REL))
    manifest = {
        "schema": SCHEMA,
        "source": "bryton",
        "model": model,
        "deviceId": device_id,
        "created": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "contents": {"screens": GRID_REL},   # "sensors": "sensors.json" added later by the BLE step
        "files": [GRID_REL],
        "screenCount": count,
    }
    with open(os.path.join(dest, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    return {"ok": True, "path": dest, "model": model, "deviceId": device_id,
            "screenCount": count, "manifest": manifest}


def list_backups(root=DEFAULT_ROOT):
    base = os.path.join(root, "bryton")
    out = []
    if os.path.isdir(base):
        for device_id in sorted(os.listdir(base)):
            ddir = os.path.join(base, device_id)
            if not os.path.isdir(ddir):
                continue
            for stamp in sorted(os.listdir(ddir), reverse=True):
                sdir = os.path.join(ddir, stamp)
                mpath = os.path.join(sdir, "manifest.json")
                if not os.path.isfile(mpath):
                    continue
                try:
                    man = json.load(open(mpath))
                except (OSError, json.JSONDecodeError):
                    continue
                man["path"] = sdir
                man["hasSensors"] = os.path.isfile(os.path.join(sdir, "sensors.json"))
                out.append(man)
    return {"ok": True, "backups": out}


def restore(mount, backup_dir):
    src = os.path.join(backup_dir, GRID_REL)
    if not os.path.isfile(src):
        raise FileNotFoundError(f"no {GRID_REL} in {backup_dir}")
    # Validate the SAVED file parses as real screens before touching the device. backup_dir mirrors
    # the device layout (System/Grid.ini), so bryton_grid.read() reads it directly.
    pages = bryton_grid.read(backup_dir)
    if not pages:
        raise ValueError("saved Grid.ini has no screens — refusing to restore")
    dst = _grid_path(mount)
    bak = dst + ".bak"
    if not os.path.exists(bak):
        shutil.copy2(dst, bak)   # one-time safety of what's on the device now
    shutil.copy2(src, dst)
    return {"ok": True, "restored": dst, "fromBackup": backup_dir,
            "screenCount": len(pages), "backupOfPrevious": bak}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bryton Aero 60 config backup/restore (screens)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup"); b.add_argument("mount"); b.add_argument("--root", default=DEFAULT_ROOT)
    l = sub.add_parser("list"); l.add_argument("--root", default=DEFAULT_ROOT)
    r = sub.add_parser("restore"); r.add_argument("mount"); r.add_argument("backup_dir")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "backup":
            res = backup(args.mount, os.path.expanduser(args.root))
        elif args.cmd == "list":
            res = list_backups(os.path.expanduser(args.root))
        else:
            res = restore(args.mount, args.backup_dir)
    except (OSError, ValueError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
