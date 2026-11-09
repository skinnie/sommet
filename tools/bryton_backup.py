#!/usr/bin/env python3
"""Back up / restore a Bryton Aero 60's *configuration* — the parts a user sets up and would hate to
lose after a reset — over USB mass storage, stdlib only.

What this covers (André, 2026-11-08, "activity profiles/screens and sensors"):
  * SCREENS (activity data pages): System/Grid.ini — a plain file on the device, copied verbatim.
    This is the whole screens picture; tools/bryton_grid.py reads/edits the same file.
  * SENSORS: not on the USB volume (internal flash) — read over Bluetooth by
    `bryton_ble.py sensors --out FILE` (commands 43/44, verified against an H10 on ANT+), then
    passed here with --sensors so the backup holds both halves as sensors.json. `backup-ble` makes
    a sensors-only backup when the Bryton is on Bluetooth but not plugged in. Sensors are a RECORD
    (type, ANT+/BLE, id) for re-pairing; restore puts back the screens only.

A backup is a folder  <root>/bryton/<deviceId>/<timestamp>/  holding the copied files plus a
manifest.json (schema below). Restore validates the saved Grid.ini parses as real screens, keeps a
one-time Grid.ini.bak on the device, then copies the file back. Grid.ini is a config file on a FAT
volume, not firmware: the worst realistic failure is screens resetting, recoverable from the .bak.

    ./tools/bryton_backup.py backup  /media/<user>/BRYTON [--sensors sensors.json] [--root ~/SommetBackups]
    ./tools/bryton_backup.py backup-ble --address AA:BB:… --sensors sensors.json
    ./tools/bryton_backup.py list    [--root ~/SommetBackups]
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
DEFAULT_ROOT = os.path.join(os.path.expanduser("~"), "SommetBackups")


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


def _load_sensors(path):
    """The JSON `bryton_ble.py sensors` wrote. Returns (data, paired-list) or raises."""
    data = json.load(open(path))
    if not data.get("ok") or not isinstance(data.get("sensors"), list):
        raise ValueError("sensors file is not a successful bryton_ble.py sensors read")
    return data, [s for s in data["sensors"] if s.get("paired")]


def _write_backup(dest, manifest, grid_src=None, sensors_src=None):
    files, contents = [], {}
    if grid_src:
        # Mirror the device layout (System/Grid.ini) so bryton_grid.read(dest) validates a backup.
        os.makedirs(os.path.join(dest, "System"), exist_ok=True)
        shutil.copy2(grid_src, os.path.join(dest, GRID_REL))
        files.append(GRID_REL); contents["screens"] = GRID_REL
    if sensors_src:
        os.makedirs(dest, exist_ok=True)
        shutil.copy2(sensors_src, os.path.join(dest, "sensors.json"))
        files.append("sensors.json"); contents["sensors"] = "sensors.json"
    manifest.update({"schema": SCHEMA, "source": "bryton", "files": files, "contents": contents,
                     "created": _dt.datetime.now().astimezone().isoformat(timespec="seconds")})
    with open(os.path.join(dest, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


def _summary(paired):
    return [{"bike": s["bike"] + 1, "type": s["type"], "hw": s["hw"], "id": s["id"]} for s in paired]


def backup(mount, root=DEFAULT_ROOT, sensors=None):
    grid = _grid_path(mount)                 # device-side validation that the file exists
    count = _screen_count(mount)             # parses <mount>/System/Grid.ini; raises if not real
    paired = _load_sensors(sensors)[1] if sensors else None
    model, device_id = _device_meta(mount)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(root, "bryton", device_id, stamp)
    manifest = {"model": model, "deviceId": device_id, "screenCount": count}
    if paired is not None:
        manifest["sensors"] = _summary(paired)
    manifest = _write_backup(dest, manifest, grid_src=grid, sensors_src=sensors)
    return {"ok": True, "path": dest, "model": model, "deviceId": device_id,
            "screenCount": count, "manifest": manifest}


def backup_ble(address, sensors, root=DEFAULT_ROOT, device_id=None):
    """Sensors-only backup, when the Bryton is reachable over Bluetooth but not mounted over USB.
    Filed under the device's UUID if the caller knows it, else under its Bluetooth address."""
    _data, paired = _load_sensors(sensors)
    device_id = device_id or ("ble-" + address.replace(":", "").upper())
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(root, "bryton", device_id, stamp)
    manifest = _write_backup(dest, {"model": "Aero60", "deviceId": device_id, "address": address,
                                    "screenCount": 0, "sensors": _summary(paired)},
                             sensors_src=sensors)
    return {"ok": True, "path": dest, "deviceId": device_id, "manifest": manifest}


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
    out.sort(key=lambda m: m.get("created", ""), reverse=True)   # newest first, across devices
    return {"ok": True, "backups": out}


def restore(mount, backup_dir):
    """Restores the SCREENS only. A backup's sensors.json is a record (which sensor, ANT+/BLE, id)
    to re-pair from by hand: writing sensor pairings back over BLE is not implemented/verified."""
    src = os.path.join(backup_dir, GRID_REL)
    if not os.path.isfile(src):
        raise FileNotFoundError(f"no screens ({GRID_REL}) in {backup_dir} — sensors-only backups "
                                "can't be restored automatically yet")
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
    b.add_argument("--sensors", help="JSON from `bryton_ble.py sensors --out` to include")
    bb = sub.add_parser("backup-ble"); bb.add_argument("--address", required=True)
    bb.add_argument("--sensors", required=True); bb.add_argument("--device-id")
    bb.add_argument("--root", default=DEFAULT_ROOT)
    l = sub.add_parser("list"); l.add_argument("--root", default=DEFAULT_ROOT)
    r = sub.add_parser("restore"); r.add_argument("mount"); r.add_argument("backup_dir")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "backup":
            res = backup(args.mount, os.path.expanduser(args.root), args.sensors)
        elif args.cmd == "backup-ble":
            res = backup_ble(args.address, args.sensors, os.path.expanduser(args.root), args.device_id)
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
