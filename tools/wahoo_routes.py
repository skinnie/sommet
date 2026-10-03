#!/usr/bin/env python3
"""Routes on a Wahoo ELEMNT from Sommet - send a GPX / TCX / FIT route, list, delete - over the USB
cable or Bluetooth, without the Wahoo companion app (André, 2026-10-03: item 3 of the Wahoo plan,
"be sure to have them usb and bluetooth").

    ./tools/wahoo_routes.py list   [--via auto|usb|ble]
    ./tools/wahoo_routes.py send   [--via ...] FILE.gpx "Route name"
    ./tools/wahoo_routes.py delete [--via usb] "Route name"

How the ELEMNT (BoltApp 1.77.10.1) takes routes, found 2026-10-03:
  * Its route provider "StdRouteProviderSdFolder" scans /sdcard/routes when routes SYNC (the SYNC
    key on its Routes screen); every GPX / TCX / FIT there is converted to a FIT course in
    files/routes/12/<name>.fit and becomes a row of the CloudRouteDao table of BoltApp.sqlite -
    that table IS the route list. (The older files/routes/EXT_FOLDER that BoltOn used is migrated
    away and ignored on this firmware.)
  * USB: push to /sdcard/routes, then the app's own adb broadcast
    StdProviderSyncManager.SYNC_LOCAL_PROVIDERS with --es category Routes (without the category
    extra it is silently ignored) imports it within seconds; then confirm the row is there.
  * Bluetooth: the file goes to /sdcard/routes through the BLE file channel (wahoo_ble_files.py);
    nothing triggers a route sync over BLE (the app syncs on pull-to-refresh, the adb broadcast and
    at its own start), so the rider presses SYNC on the ELEMNT - or it imports at next power-on.
  * Delete: there is no "delete" on the device or in the companion app. The route row is marked
    isDeleted (+ updateTimeMs) in BoltApp.sqlite - app stopped, database swapped in one device-side
    command, journal checked inactive first - and the files are removed; the next sync carries the
    deletion. Proven on 3 test routes. USB only (it edits the database).
NOTE: when the ELEMNT has any connection (Wi-Fi, or a phone with the Wahoo app) its route sync
also registers imported routes in the rider's Wahoo account - seen 2026-10-03, even with the
ELEMNT's Wi-Fi off. Deleting carries over the same way.
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wahoo_pages as P        # noqa: E402 - shared adb helpers

APP = "/data/data/com.wahoofitness.bolt"
DB = APP + "/databases/BoltApp.sqlite"
SD_ROUTES = "/sdcard/routes"
IMPORTED = APP + "/files/routes/12"
EXTS = (".gpx", ".tcx", ".fit")
BACKUP_DIR = os.path.join(os.path.expanduser("~"), "AmbitAppBackups", "wahoo")


def named_copy(path, name, tmpdir):
    """A copy of the route whose own name is `name` - the ELEMNT shows the name stored INSIDE a
    GPX / TCX, not the file name (seen 2026-10-03). FIT files are sent as they are."""
    import xml.etree.ElementTree as ET
    ext = os.path.splitext(path)[1].lower()
    out = os.path.join(tmpdir, "route" + ext)
    if ext == ".fit":
        shutil.copy(path, out)
        return out
    tree = ET.parse(path)
    root = tree.getroot()
    ns = root.tag[1:root.tag.index("}")] if root.tag.startswith("{") else ""
    q = (lambda t: "{%s}%s" % (ns, t)) if ns else (lambda t: t)
    ET.register_namespace("", ns)
    if ext == ".gpx":
        targets = [root.find(q("metadata"))] + root.findall(q("trk")) + root.findall(q("rte"))
        tag = "name"
    else:                                                      # TCX: Courses/Course/Name
        courses = root.find(q("Courses"))
        targets = courses.findall(q("Course")) if courses is not None else []
        tag = "Name"
    for t in targets:
        if t is None:
            continue
        el = t.find(q(tag))
        if el is None:
            el = ET.Element(q(tag))
            t.insert(0, el)
        el.text = name
    tree.write(out, xml_declaration=True, encoding="UTF-8")
    return out


def safe_name(name):
    name = re.sub(r"[^\w .,()'-]", "", (name or "").strip(), flags=re.UNICODE)[:60].strip()
    if not name:
        raise ValueError("route name is empty")
    return name


# ---- USB --------------------------------------------------------------------------------------

def _db_rows(serial):
    """Route rows from a copy of the ELEMNT's database (read-only; the live file is untouched)."""
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, "BoltApp.sqlite")
        with open(local, "wb") as fh:
            fh.write(P._adb("exec-out", "cat " + DB, serial=serial, text=False)[1])
        con = sqlite3.connect(local)
        rows = con.execute("select id, name, distanceM, ascentM, providerType, fitnessAppId, isDeleted, "
                           "providerId from CloudRouteDao").fetchall()
        con.close()
    return [{"id": r[0], "name": r[1], "distanceKm": round((r[2] or 0) / 1000.0, 2),
             "ascentM": round(r[3] or 0), "provider": r[4], "imported": r[5] == 14,
             "deleted": bool(r[6]), "file": r[7]} for r in rows]


SYNC_LOCAL = ("am broadcast -a com.wahoofitness.support.cloud.StdProviderSyncManager.SYNC_LOCAL_PROVIDERS"
              " --es category Routes")


def _usb_sync(serial):
    """Run the ELEMNT's local route sync (re-scans /sdcard/routes, carries deletions) through the
    sync manager's own adb receiver - it acts only when the "category" extra names its provider
    family ("Routes"). No screen or key involved: the earlier SYNC-key approach once started a ride
    (the middle key is START on the home screen, 2026-10-03)."""
    P._adb("shell", SYNC_LOCAL, serial=serial)


def _usb_list(serial):
    return [r for r in _db_rows(serial) if not r["deleted"]]


def _usb_send(serial, path, name):
    ext = os.path.splitext(path)[1].lower()
    tmp = "/data/local/tmp/sommet_route" + ext
    with tempfile.TemporaryDirectory() as td:
        if P._adb("push", named_copy(path, name, td), tmp, serial=serial, timeout=120)[0] != 0:
            raise RuntimeError("adb push failed")
    P._adb("shell", 'cat %s > "%s/%s%s"; rm %s' % (tmp, SD_ROUTES, name, ext, tmp), serial=serial)
    _usb_sync(serial)
    for _ in range(20):
        time.sleep(1.5)
        # Matched on the file name (providerId): a FIT keeps its own internal name.
        rows = [r for r in _usb_list(serial) if r["file"] == name or r["name"] == name]
        if rows:
            return rows[0]
    raise RuntimeError("the ELEMNT didn't import the route")


def _usb_delete(serial, name):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        db, jr = os.path.join(tmp, "BoltApp.sqlite"), os.path.join(tmp, "journal")
        # Consistent copy while the app is stopped (the launcher restarts it within ~1 s).
        P._adb("shell", "am force-stop %s; cat %s > /data/local/tmp/s_db; cat %s-journal > /data/local/tmp/s_j"
               % (P.WAHOO_PACKAGE, DB, DB), serial=serial)
        P._adb("pull", "/data/local/tmp/s_db", db, serial=serial)
        P._adb("pull", "/data/local/tmp/s_j", jr, serial=serial)
        with open(jr, "rb") as fh:
            head = fh.read(12)
        if head[:8] == bytes.fromhex("d9d505f920a163d7") and any(head[8:12]):
            raise RuntimeError("the ELEMNT's database is mid-transaction - try again in a moment")
        shutil.copy(db, os.path.join(BACKUP_DIR, "BoltApp-%s.sqlite" % stamp))
        con = sqlite3.connect(db)
        files = [r[0] for r in con.execute(
            "select providerId from CloudRouteDao where (name=? or providerId=?) and isDeleted=0", (name, name))]
        n = con.execute("update CloudRouteDao set isDeleted=1, updateTimeMs=? "
                        "where (name=? or providerId=?) and isDeleted=0",
                        (int(time.time()) * 1000, name, name)).rowcount
        con.commit()
        con.close()
        if n == 0:
            raise RuntimeError("no route named %r on the ELEMNT" % name)
        if P._adb("push", db, "/data/local/tmp/s_db", serial=serial)[0] != 0:
            raise RuntimeError("adb push failed")
        # Remove the source and the imported course too, or the next sync imports it again.
        rm = " ".join('"%s/%s".* "%s/%s".*' % (SD_ROUTES, f, IMPORTED, f) for f in set(files + [name]))
        P._adb("shell", 'am force-stop %s; cat /data/local/tmp/s_db > %s; rm /data/local/tmp/s_db '
                        '/data/local/tmp/s_j; rm -f %s' % (P.WAHOO_PACKAGE, DB, rm), serial=serial)
    time.sleep(5)
    _usb_sync(serial)
    time.sleep(8)
    if any(r["name"] == name or r["file"] == name for r in _usb_list(serial)):
        raise RuntimeError("the route is still listed after the sync")
    return {"deleted": name, "backup": "BoltApp-%s.sqlite" % stamp}


# ---- Bluetooth --------------------------------------------------------------------------------

def _ble_tool(*args, timeout=240):
    here = os.path.dirname(os.path.abspath(__file__))
    out = subprocess.run([sys.executable, os.path.join(here, "wahoo_ble_files.py"), *args],
                         capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    res = json.loads(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() else {"ok": False}
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Bluetooth file transfer failed")
    return res


def _ble_list():
    imported = _ble_tool("list", IMPORTED + "/")["files"]
    pending = _ble_tool("list", SD_ROUTES + "/")["files"]
    have = {os.path.splitext(f["name"])[0] for f in imported if not f["dir"]}
    out = [{"name": n, "imported": True} for n in sorted(have)]
    out += [{"name": os.path.splitext(f["name"])[0], "pendingSync": True} for f in pending
            if not f["dir"] and os.path.splitext(f["name"])[0] not in have]
    return out


def _ble_send(path, name):
    ext = os.path.splitext(path)[1].lower()
    with tempfile.TemporaryDirectory() as td:
        res = _ble_tool("send", named_copy(path, name, td), "%s/%s%s" % (SD_ROUTES, name, ext))
    return {"name": name, "bytes": res.get("bytes"), "pendingSync": True,
            "note": "On the ELEMNT: Routes -> SYNC to import it (or it imports at next power-on)."}


# ---- front door -------------------------------------------------------------------------------

def _pick(via):
    if via in ("auto", "usb"):
        s = P.find_serial()
        if s:
            return "usb", s
        if via == "usb":
            raise RuntimeError("No Wahoo ELEMNT on adb - press power twice and re-plug the USB cable")
    return "ble", None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["list", "send", "delete"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--via", choices=["auto", "usb", "ble"], default="auto")
    a = ap.parse_intermixed_args()
    try:
        how, serial = _pick(a.via)
        if a.command == "list":
            out = {"routes": _usb_list(serial) if how == "usb" else _ble_list()}
        elif a.command == "send":
            path, name = a.args[0], safe_name(a.args[1] if len(a.args) > 1 else
                                              os.path.splitext(os.path.basename(a.args[0]))[0])
            if os.path.splitext(path)[1].lower() not in EXTS:
                raise ValueError("the ELEMNT takes GPX, TCX or FIT routes")
            out = {"route": _usb_send(serial, path, name) if how == "usb" else _ble_send(path, name)}
        else:
            if how != "usb":
                raise RuntimeError("deleting a route needs the USB cable (it edits the ELEMNT's database)")
            out = _usb_delete(serial, safe_name(a.args[0]))
        out.update(ok=True, via=how)
    except Exception as e:                     # noqa: BLE001 - one JSON error line for the UI
        out = {"ok": False, "error": str(e)}
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
