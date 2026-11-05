#!/usr/bin/env python3
"""Device-side orchestration for the Edge data-screen editor desktop UI: finds the Edge over
USB/MTP (gvfs), lists activity profiles, reads a profile's screens, and writes an edited profile
back to Garmin/NewFiles. All operations print ONE JSON object so the C++ EdgeScreensService can
parse them. The FIT read/edit itself is done by garmin_edge_screens.py (same dir); this file only
adds the device (MTP) plumbing, mirroring tools/mtp_import.py's gio/gvfs approach.

  edge_screens_device.py --catalog                     # {"fields":[{id,name},...]}
  edge_screens_device.py --list-profiles               # {"mount":..,"profiles":[{file,name},...]}
  edge_screens_device.py --load  <profile.fit-name>    # {"screens":[{index,layout,fields:[...]}]}
  edge_screens_device.py --save  <profile.fit-name> --screen N --fields "Speed,Power,..."
                                                       # {"ok":true,"written":"...NewFiles/..."}
Read-only unless --save; --save writes to NewFiles (the device's own sanctioned import dir) and
the Edge applies it on next power-up."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
EDITOR = os.path.join(HERE, "garmin_edge_screens.py")
sys.path.insert(0, HERE)
# Call the editor's functions IN-PROCESS (import), not via a nested subprocess: when this file is
# itself launched by the desktop app's QProcess, a nested `sys.executable` subprocess proved
# unreliable (profiles listed but loading a profile returned nothing). Direct import is robust.
import garmin_edge_screens as ges
from garmin_edge_screens import sport_name as _sport_name


def _fail(msg):
    print(json.dumps({"ok": False, "error": msg}))
    sys.exit(0)  # exit 0 so the caller reads our JSON error rather than a process-failure


def _gvfs_root():
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    return "/run/user/%d/gvfs" % uid


def _mass_edge():
    """A USB-drive Edge (Edge 500 & co.): (None, "<mount>/Garmin") via mtp_import's discovery."""
    try:
        import mtp_import
        for dev in mtp_import._mass_storage_devices():
            if dev.get("kind") == "edge":
                return None, os.path.join(dev["mount"], "Garmin")
    except Exception:                      # noqa: BLE001 - no mass-storage Edge is not an error
        pass
    return None, None


def find_edge():
    """Return (gvfs_host_dir, garmin_dir) for a connected Edge, or (None, None). An MTP Edge has a
    gvfs host dir; a USB-drive Edge (Edge 500) returns host None and its mounted Garmin folder."""
    root = _gvfs_root()
    if not os.path.isdir(root):
        return _mass_edge()
    for entry in os.listdir(root):
        if entry.startswith("mtp:") and "Garmin_Edge" in entry:
            host = os.path.join(root, entry)
            # storage is usually "Internal Storage"; take the first dir that has a Garmin/ child
            try:
                for store in os.listdir(host):
                    g = os.path.join(host, store, "Garmin")
                    if os.path.isdir(g):
                        return host, g
            except OSError:
                pass
    return _mass_edge()


def _family(garmin_dir):
    """'edge500' for a USB-drive Edge, 'edge' for the MTP ones (different field-id tables)."""
    return "edge" if "/gvfs/" in garmin_dir else "edge500"


def _write_newfile(g, fitname, src):
    """Copy an edited profile into the Edge's Garmin/NewFiles. MTP -> gio copy; USB drive -> a
    plain copy + fsync. Returns (ok, dest_or_error)."""
    if "/gvfs/" in g:
        dest = _mtp_uri(g, "NewFiles/" + fitname)
        cp = subprocess.run(["gio", "copy", src, dest], capture_output=True, text=True)
        return (cp.returncode == 0), (dest if cp.returncode == 0 else (cp.stderr or cp.stdout))
    dest = os.path.join(g, "NewFiles", fitname)
    try:
        data = open(src, "rb").read()
        with open(dest, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if open(dest, "rb").read() != data:
            return False, "verify after write failed"
    except OSError as e:
        return False, str(e)
    return True, dest


def _apply_note(g, what):
    if "/gvfs/" in g:
        return "%s Reboot the Edge to apply (it imports NewFiles on power-up)." % what
    return "%s Unplug the Edge to apply (it reads NewFiles when it leaves USB mode)." % what


def _mtp_uri(garmin_dir, sub):
    """Build an mtp:// URI for gio from the gvfs FUSE path (gio copy needs the URI form)."""
    # garmin_dir = /run/user/UID/gvfs/mtp:host=XXX/Internal Storage/Garmin
    m = re.search(r"/gvfs/(mtp:host=[^/]+)/(.*)$", garmin_dir)
    host = m.group(1)
    rest = m.group(2)  # "Internal Storage/Garmin"
    hostname = host.split("=", 1)[1]
    return "mtp://%s/%s/%s" % (hostname, rest, sub)


def do_catalog():
    _h, g = find_edge()
    table = ges.names_for(_family(g)) if g else ges.FIELD_NAMES
    fields = [{"id": fid, "name": table[fid]} for fid in sorted(table)]
    print(json.dumps({"ok": True, "fields": fields}))


def do_list_profiles():
    host, g = find_edge()
    if not g:
        _fail("No Garmin Edge found over USB. Plug it in and unlock it.")
    sports = os.path.join(g, "Sports")
    profs = []
    try:
        for f in sorted(os.listdir(sports)):
            if not f.lower().endswith(".fit"):
                continue
            name = f[:-4]
            if _sport_name:  # real profile name from the SPORT message (e.g. "ROAD")
                try:
                    nm = _sport_name(os.path.join(sports, f))
                    if nm:
                        name = nm
                except Exception:
                    pass
            profs.append({"file": f, "name": name})
    except OSError as e:
        _fail("Cannot read Sports folder: %s" % e)
    print(json.dumps({"ok": True, "garmin": g, "profiles": profs}))


def _pull_profile(g, fitname):
    sports_fit = os.path.join(g, "Sports", fitname)
    if not os.path.exists(sports_fit):
        _fail("Profile not found on device: %s" % fitname)
    tmp = tempfile.NamedTemporaryFile(suffix=".fit", delete=False)
    tmp.close()
    # gvfs FUSE reads are fine with a plain copy
    import shutil
    shutil.copyfile(sports_fit, tmp.name)
    return tmp.name


def do_load(fitname):
    host, g = find_edge()
    if not g:
        _fail("No Garmin Edge found over USB.")
    local = _pull_profile(g, fitname)
    try:
        rawb, screens = ges.parse(local)
        settings = ges.parse_settings(rawb)  # mesg-13 profile settings, for screen toggles
    except Exception as e:
        _fail("read failed: %s" % e)
    finally:
        try:
            os.unlink(local)
        except OSError:
            pass

    def toggles_for(stype):
        specs = ges.screen_toggles(stype)
        if not specs:
            return []
        out = []
        for t in specs:
            cur = settings.get(t["field"])
            out.append({"field": t["field"], "name": t["name"], "kind": t["kind"],
                        "options": t.get("options", []),
                        "value": (cur[0] if cur else 0)})
        return out

    data, specials = [], []
    for s in screens:
        if s.get("enabled") != 1:
            continue
        st = s.get("stype")
        if st is not None and st >= ges.DATA_SCREEN_TYPE_MAX:
            # A named/special screen (Map/Elevation/ClimbPro/…). Surface it with its name and current
            # metrics. Editable types carry their allowed (count, f8) states so the UI can offer a picker.
            states = ges.special_count_states(st)  # None => not editable
            editable = states is not None
            slots = s["slots"] or []
            f3 = s.get("layout") if s.get("layout") not in (255, None) else 0
            f8 = s.get("variant") if s.get("variant") not in (255, None) else 0
            if editable:
                rc = ges.special_render_count(st, f3)
                shown = slots[:rc]
            else:
                rc, shown = None, slots
            sfields = [{"id": f, "name": ges.name(f)} for f in shown if f not in (65535, 255)]
            specials.append({"index": s["index"], "stype": st, "name": ges.special_name(st),
                             "editable": editable, "hasFields": st not in ges.SPECIAL_NO_FIELDS,
                             "capacity": len(slots), "count": (rc if rc is not None else len(sfields)),
                             "variant": f8,
                             "countStates": ([[c, v] for c, v in states] if states else []),
                             "order": (s.get("order") if s.get("order") not in (None, 255) else 999),
                             "toggles": toggles_for(st),
                             "fields": sfields})
            continue
        fields = [{"id": f, "name": ges.name(f, s.get("family", "edge"))} for f in ges.shown_slots(s)]
        # capacity = physical field-7 slot count; the layout can be resized 1..capacity in place
        # (do_set writes a fixed-size u16[capacity], so it cannot grow beyond this).
        data.append({"index": s["index"], "stype": (st if st is not None else 0),
                     "layout": s["layout"], "capacity": len(s["slots"] or []),
                     "variant": (s.get("variant") if s.get("variant") not in (255, None) else 0),
                     "family": s.get("family", "edge"),
                     "fields": fields})
    # Order data screens the way the device shows them (Screen 1,2,3…) and number them.
    data.sort(key=lambda d: d["stype"])
    for n, d in enumerate(data, 1):
        d["number"] = n
    specials.sort(key=lambda s: s["order"])  # carousel order
    # "specials" stays an int (count) for the existing UI; "specialList" carries the detail.
    print(json.dumps({"ok": True, "screens": data,
                      "specials": len(specials), "specialList": specials}))


def do_save(fitname, screen, fields, variant=None, toggles=None):
    host, g = find_edge()
    if not g:
        _fail("No Garmin Edge found over USB.")
    local = _pull_profile(g, fitname)
    out_fit = local + ".new"
    import contextlib
    try:
        with open(os.devnull, "w") as dn, contextlib.redirect_stdout(dn):
            ges.do_set(local, screen, fields, None, out_fit, variant)  # writes out_fit; prints suppressed
            # Apply any screen toggles (mesg-13 settings) into the SAME file so one NewFiles write carries
            # both the field/layout change and the toggle change (they'd otherwise clobber each other).
            for pair in (toggles or "").split(","):
                pair = pair.strip()
                if not pair:
                    continue
                fld, val = pair.split(":")
                ges.do_toggle(out_fit, int(fld), int(val), out_fit)
    except SystemExit as e:
        _fail("edit failed: %s" % e)
    except Exception as e:
        _fail("edit failed: %s" % e)
    finally:
        try:
            os.unlink(local)
        except OSError:
            pass
    if not os.path.exists(out_fit):
        _fail("edit produced no file")
    ok, dest = _write_newfile(g, fitname, out_fit)
    os.unlink(out_fit)
    if not ok:
        _fail("write to device failed: %s" % dest)
    print(json.dumps({"ok": True, "written": dest,
                      "note": _apply_note(g, "Saved.")}))


def do_add(fitname, fields, variant=None):
    host, g = find_edge()
    if not g:
        _fail("No Garmin Edge found over USB.")
    local = _pull_profile(g, fitname)
    out_fit = local + ".new"
    import contextlib
    try:
        with open(os.devnull, "w") as dn, contextlib.redirect_stdout(dn):
            ges.do_add(local, fields, None, out_fit, variant)  # enables the next data-screen slot; prints suppressed
    except SystemExit as e:
        _fail("add failed: %s" % e)
    except Exception as e:
        _fail("add failed: %s" % e)
    finally:
        try:
            os.unlink(local)
        except OSError:
            pass
    if not os.path.exists(out_fit):
        _fail("add produced no file")
    ok, dest = _write_newfile(g, fitname, out_fit)
    os.unlink(out_fit)
    if not ok:
        _fail("write to device failed: %s" % dest)
    print(json.dumps({"ok": True, "written": dest,
                      "note": _apply_note(g, "Data screen added.")}))


def do_set_toggle(fitname, field, value):
    host, g = find_edge()
    if not g:
        _fail("No Garmin Edge found over USB.")
    local = _pull_profile(g, fitname)
    out_fit = local + ".new"
    import contextlib
    try:
        with open(os.devnull, "w") as dn, contextlib.redirect_stdout(dn):
            ges.do_toggle(local, field, value, out_fit)
    except SystemExit as e:
        _fail("toggle failed: %s" % e)
    except Exception as e:
        _fail("toggle failed: %s" % e)
    finally:
        try:
            os.unlink(local)
        except OSError:
            pass
    if not os.path.exists(out_fit):
        _fail("toggle produced no file")
    ok, dest = _write_newfile(g, fitname, out_fit)
    os.unlink(out_fit)
    if not ok:
        _fail("write to device failed: %s" % dest)
    print(json.dumps({"ok": True, "written": dest,
                      "note": _apply_note(g, "Setting changed.")}))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalog", action="store_true")
    ap.add_argument("--list-profiles", action="store_true")
    ap.add_argument("--load", metavar="PROFILE.fit")
    ap.add_argument("--save", metavar="PROFILE.fit")
    ap.add_argument("--add", metavar="PROFILE.fit")
    ap.add_argument("--screen", type=int)
    ap.add_argument("--fields")
    ap.add_argument("--variant", type=int)
    ap.add_argument("--toggle", metavar="PROFILE.fit")
    ap.add_argument("--field", type=int)
    ap.add_argument("--value", type=int)
    ap.add_argument("--toggles", help="screen toggles to apply on --save: 'field:value,field:value'")
    a = ap.parse_args()
    if a.catalog:
        do_catalog()
    elif a.list_profiles:
        do_list_profiles()
    elif a.load:
        do_load(a.load)
    elif a.save:
        if a.screen is None or a.fields is None:
            _fail("--save needs --screen and --fields")
        do_save(a.save, a.screen, a.fields, a.variant, a.toggles)
    elif a.add:
        do_add(a.add, a.fields, a.variant)
    elif a.toggle:
        if a.field is None or a.value is None:
            _fail("--toggle needs --field and --value")
        do_set_toggle(a.toggle, a.field, a.value)
    else:
        ap.error("pick one of --catalog / --list-profiles / --load / --save / --add / --toggle")


if __name__ == "__main__":
    main()
