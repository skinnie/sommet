#!/usr/bin/env python3
"""AmbitApp V2's Python backend bridge - wraps this repo's existing, hardware-proven CLI
tools (`tools/write_nav.py`, `tools/exercise_log.py`, `tools/sgee.py`) behind a local
HTTP/JSON API, so the Qt/QML app's C++ Services layer never needs to know Python exists.
See `../README.md`'s "Architecture decision" section for why the backend stays Python
rather than being ported to C++: everything here is already hardware-tested, and
re-deriving it in C++ would mean redoing real reverse-engineering work for no gain.

Same stdlib-only, no-framework style as `tools/workout_gui.py` - `BaseHTTPRequestHandler`,
`ThreadingHTTPServer`, JSON in/out, nothing else to install.

**Every endpoint that can write to the watch runs its underlying tool WITHOUT `--write`
unless the request body explicitly sets `"confirm": true`.** That is not this file's own
idea of caution - it mirrors `write_nav.py`/`sgee.py`'s own default-safe CLI design (dry-run
unless told otherwise) exactly, on purpose: a bug in this bridge should fail the same safe
way those tools already do.

**Deliberately calls the real CLI tools via subprocess, not their internal functions
directly.** Reuses the exact, already-tested entry point (argument validation, dry-run
default, POI preservation dance, all of it) instead of re-wiring internals here in a way
that could subtly diverge from what real hardware has actually verified - the same
reasoning behind this project's own "bounds-check before write" lesson (a real out-of-bounds
flash write happened once, from code that skipped the tool that already got this right).

**Untested against real hardware as of 2026-08-07.** Written by reading the actual CLI
tools' argparse interfaces and output shapes directly (not guessed), but this sandbox has no
watch attached to verify against - a real device test is still owed before trusting this
with an actual write.

    ./backend/server.py                 # serves http://127.0.0.1:8766
    ./backend/server.py --port 9000
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import struct
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import available_timezones

import ble_bridge

# When frozen by PyInstaller (the packaged desktop download - see
# tools/packaging/ambit_backend.spec), __file__ lives inside a temporary extraction dir, not
# the repo, so the repo-relative paths below don't exist. The spec bundles tools/, the app
# catalog and the demo data as data files under sys._MEIPASS instead; use those when frozen.
FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    _RES = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    _BACKEND_DIR = _RES / "backend"
    TOOLS_DIR = _RES / "tools"
    CATALOG_DIR = _RES / "data" / "suunto_apps"
else:
    _BACKEND_DIR = Path(__file__).resolve().parent
    TOOLS_DIR = _BACKEND_DIR.parent.parent / "tools"
    # Real, 2026-08-09 (App Slot picker) - extract_apps_catalog.py's own real, distributable
    # output (data/suunto_apps/{catalog.json,catalog.bin}), tracked in the repo (unlike
    # assets/, gitignored research-only) since this actually ships with the app.
    CATALOG_DIR = _BACKEND_DIR.parent.parent / "data" / "suunto_apps"
PYTHON = sys.executable

# Step 10 (Backup). write_nav.py's own `nav --save PREFIX` / `restore PREFIX --write` is a
# real, hardware-tested backup/restore mechanism (see that file's own run_nav()/
# build_restore() - "the backup that milestone 4 asked for and never had") - this just picks
# a real place on disk for PREFIX to live, one directory per timestamped backup.
BACKUP_DIR = Path.home() / "AmbitAppBackups"
FIRMWARE_DIR = BACKUP_DIR / "firmware"

# Training Program (2026-08-12, "movescount era called them training program") - saved plans
# are user documents (authored offline, kept across sessions, re-installable as dates roll
# by), so they live in the user's home like backups do, not inside the repo. One JSON file
# per plan, the same schema tools/training_plan.py documents.
PLANS_DIR = Path.home() / "AmbitAppPlans"
# Ember (fasting/food/coffee/water) log store - so the desktop can LOG, not just display
# (André, 2026-08-25: "on the desktop side we miss to add coffee, water etc"). One JSON file,
# the same file-backed pattern as LEGACY_SPORT_MODES_FILE. Phone<->desktop convergence (one
# shared store on the NAS) is a later step; this makes the desktop a real logging surface now.
EMBER_DIR = PLANS_DIR / "ember"
EMBER_FILE = EMBER_DIR / "log.json"
# Phone<->desktop convergence (André, 2026-08-25): the NAS holds one shared per-day store
# (ember-YYYY-MM-DD.json via sync.php); both this desktop and the phone PWA push+pull+merge it,
# deduping events by a stable `uid`. Sync target lives OUTSIDE the repo (holds the token) in
# ember/sync.json = {"url": "http://192.168.1.102/ember/sync.php", "token": "..."}; absent = no
# sync (local-only, unchanged). Coffee-family drink ids so a phone "drink" counts as a coffee
# here too - mirrors the phone's own COFFEE_IDS list.
EMBER_SYNC_FILE = EMBER_DIR / "sync.json"
EMBER_COFFEE_IDS = {"black_coffee", "espresso", "americano", "coffee_milk", "latte", "cappuccino"}

# GPS Track Pod (2026-08-12, "just blind, as experimental") - retrieved GPX tracks and
# diagnostic log bundles both land here, same "a real place in the user's home" reasoning as
# BACKUP_DIR/PLANS_DIR above. See tools/gps_track_pod.py's own module docstring for why this
# whole feature is marked experimental: built without a real device to test against.
# Legacy (Ambit1/2) sport modes (2026-08-23). This family's protocol has NO sport-mode read -
# not in this project, not in openambit, not in openambit2 (verified exhaustively: the driver
# struct has no read slot, sport_mode_serialize has serialize but no deserialize, and
# PMEM20_SPORT_MODE_START is referenced only by the write). So the watch cannot be the source
# of truth for these - the HOST has to hold the master copy, which is exactly how the real
# thing always worked: openambit pulled the authoritative set from the Movescount cloud
# (syncGET /userdevices/<serial>) and wrote it wholesale; openambit2, post-shutdown, swapped
# that cloud for a local ~/.openambit/sport_modes.json. Same architecture here, same "a real
# place in the user's home" reasoning as PLANS_DIR above: this file IS the user's sport modes,
# editable offline, pushed to the watch on demand.
LEGACY_SPORT_MODES_FILE = Path.home() / "AmbitAppPlans" / "legacy_sport_modes.json"

GPSTRACKPOD_DIR = Path.home() / "AmbitAppBackups" / "gpstrackpod"

# Suunto T6 (2026-08-14, "implement Suunto t6 ... only as experimental") - exported heart-rate
# logs (FIT + a JSON sample sidecar for the merge) land here; merged GPS+HR activities land in
# the sibling folder. Same built-blind, no-real-device-to-test-against reasoning as the GPS
# Track Pod above - see tools/suunto_t6.py's own module docstring.
SUUNTOT6_DIR = Path.home() / "AmbitAppBackups" / "suuntot6"
SUUNTOX6HR_DIR = Path.home() / "AmbitAppBackups" / "suuntox6hr"
LEGACYMERGE_DIR = Path.home() / "AmbitAppBackups" / "legacy-merged"

# Confirmed live and fully unauthenticated, 2026-08-05 (docs/sgee_andre.md) - no AppKey/account
# needed, unlike the rest of that host's API surface.
GPS_ORBIT_URL = "https://devices.suunto-operations.com/devices/gpsorbit/binary"
# Real, 2026-08-10 (sgee_andre.md's "GLONASS on the Kailash"): the same service serves
# GLONASS ephemeris, equally unauthenticated, and SuuntoLink's own movescount.js downloads
# BOTH for the SGEE format. Suunto only ever WRITES it to three models because Devices.xml
# lists three - a hardcoded allowlist that forgot the Kailash, which has both a GLONASS
# receiver and its own GlonassSGEE region. We deliberately do not copy that pattern: the
# watch is asked whether it declares the region (see sgee.py's glonass_status()), so any
# device that supports it gets the data, including ones this project has never seen.
GLONASS_ORBIT_URL = "https://devices.suunto-operations.com/devices/glonassorbit/binary"

# Place search - real request, 2026-08-11 (André, for the POI and Kailash-home map pickers:
# "add a search on the map so the user can search for a place"). Nominatim, chosen and
# confirmed with him: it is the same OpenStreetMap data the app's own tiles come from, works
# worldwide, and needs no API key or account. IGN's own geocoder was considered and rejected
# for this - it is France-only by design, so a search box built on it would silently fail
# everywhere else.
#
# Deliberately here rather than in QML. Nominatim's usage policy requires an identifying
# User-Agent and at most one request per second, and both are things a single choke point can
# actually guarantee - a QML XMLHttpRequest per keystroke could not. The UI searches on Enter,
# not per keystroke, for the same reason.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "AmbitApp/1.0 (open-source Suunto Ambit companion; place search)"
_NOMINATIM_LOCK = threading.Lock()
_NOMINATIM_LAST = [0.0]


# ThreadingHTTPServer gives every incoming request its own thread, and the QML app fires
# several independent requests close together in real use (Home's device+weather refresh,
# a page's own onCompleted, a Backup/Firmware check) - each run_tool() call opens the watch's
# USB connection fresh in its own subprocess, and only one process can hold it at a time.
# Without this lock those threads race each other for the device and the loser sees a real,
# correctly-reported "none openable" error, even though nothing was actually wrong with the
# watch or the connection - confirmed live, 2026-08-07, see V3_CHANGELOG.md. write_nav.py's
# own retry (Link.open(), 5 tries/2s) only covers the reconnect-permission race, not two of
# this backend's own requests overlapping - that needs serializing here instead.
WATCH_LOCK = threading.Lock()

# --- Testing mode ---------------------------------------------------------------------
# Real request, 2026-08-11 (André): "add on feature on settings: testing mode, where it
# simulates that an ambit 3 is connected, so people can test it without the watch."
#
# When on, every endpoint that would touch USB answers from desktop/backend/demo_data
# instead. The CustomModes fixture is a GENUINE flash image lifted from a capture, so the
# real decoder, encoder and round-trip check all run exactly as they do against hardware -
# a hand-built blob would prove nothing and would drift from the format. See
# tools/gen_demo_data.py for what is scrubbed and why.
#
# Deliberately in-memory and off by default: it is a way to look around the app, not a
# state anyone should end up in without asking for it. Every reply it produces carries
# "demo": true so a UI can say so rather than quietly showing fiction.
DEMO_DIR = _BACKEND_DIR / "demo_data"
DEMO = {"enabled": False, "custommodes": None, "variant": "Emu"}

# Which devices Testing mode can pretend to be. Everything here is cross-linked from real
# data - the capability record comes from SuuntoLink's own module and the product name from
# its Devices.xml - so the list stays right as devices are added, rather than being a
# hand-kept copy. Dive computers are excluded: this app does not support them, and offering
# one would be pretending harder than the data allows.
DEMO_DEVICE_PREFIXES = ("Suunto Ambit", "Suunto Traverse", "Kailash")


def demo_ambit():
    """True when Testing mode is standing in for a WATCH.

    The simulated Garmin is not one: it is a mass-storage device, handled entirely on the Qt
    side by pointing GarminService at a folder tree. If these Ambit paths answered while the
    eTrex was selected, the app would show a watch and a Garmin plugged in at once - which
    cannot happen, and which was the whole reason the Qt side substitutes rather than adds.
    """
    return DEMO["enabled"] and DEMO["variant"] != "GarminEtrex"


def demo_custom_modes_path():
    """The demo region on disk, copied to a scratch file the first time it is needed so
    edits in Testing mode behave like edits on a watch - they persist for the session and
    are thrown away when the backend restarts."""
    import tempfile
    if DEMO["custommodes"] is None:
        src = DEMO_DIR / "custommodes.bin"
        fd, path = tempfile.mkstemp(prefix="ambitapp-demo-", suffix=".bin")
        with os.fdopen(fd, "wb") as out:
            out.write(src.read_bytes())
        DEMO["custommodes"] = path
    return DEMO["custommodes"]


def demo_json(name):
    with open(DEMO_DIR / name) as fh:
        return json.load(fh)

# Real, 2026-08-09 (App Slot picker's own catalog search) - loaded once and cached rather
# than shelled out to a subprocess per keystroke: this is a pure local-file lookup (no watch
# involved at all, doesn't need WATCH_LOCK), and the metadata file alone is 5MB - reparsing
# that JSON on every search request would make the picker feel sluggish for no reason.
# threading.Lock, not WATCH_LOCK: guards the lazy-load race between request threads, nothing
# to do with the watch.
_CATALOG_LOCK = threading.Lock()
_catalog_entries = None


def load_catalog_entries():
    global _catalog_entries
    with _CATALOG_LOCK:
        if _catalog_entries is None:
            with open(CATALOG_DIR / "catalog.json") as f:
                _catalog_entries = json.load(f)["entries"]
        return _catalog_entries


def search_catalog(query="", variant=None, category_id=None, limit=50):
    """Metadata-only search (name substring, optionally filtered to a real watch variant
    codename via compatibleVariants - see extract_apps_catalog.py's own module docstring
    for where that field comes from) - never touches catalog.bin, so this stays fast
    regardless of how large the binary blob is."""
    entries = load_catalog_entries()
    query = query.strip().lower()
    results = []
    for e in entries:
        if query and query not in e["name"].lower():
            continue
        if variant and variant not in e.get("compatibleVariants", []):
            continue
        if category_id is not None and e.get("categoryId") != category_id:
            continue
        results.append({k: v for k, v in e.items()
                         if k not in ("binaryOffset", "binaryLength")})
        if len(results) >= limit:
            break
    return results


def catalog_entry_binary(rule_id):
    """The one real entry's own compiled bytecode, sliced straight out of catalog.bin by
    its real offset/length (extract_apps_catalog.py's own doing) - never loads the whole
    9.4MB blob into memory for a single install."""
    entries = load_catalog_entries()
    entry = next((e for e in entries if e["ruleId"] == rule_id), None)
    if entry is None:
        return None, None
    with open(CATALOG_DIR / "catalog.bin", "rb") as f:
        f.seek(entry["binaryOffset"])
        binary = f.read(entry["binaryLength"])
    return entry, binary


# The watch the user picked in the Home watch-switcher (its USB product_id), or None for
# "whichever is plugged". Set by POST /api/device/select, read by run_tool() which hands it to
# every tool via the AMBIT_PRODUCT_ID env var so all of them target the same one watch even
# when several Suunto watches share the USB bus (2026-08-16, porting the Android multi-watch
# picker; before this, every tool independently grabbed whichever product_id enumerated first,
# so two plugged watches raced and pages loaded inconsistently).
SELECTED_PRODUCT_ID = None

# Real, 2026-08-22 (André's Ambit1, serial 1614984607001600): these product IDs speak the
# older, pre-SBEM PMEM 2.0 protocol - write_nav.py's own SBEM queries (settings 0x1100,
# memory map 0x0b21, POIs 0x0b24) come back empty on them, not an error, confirmed live. Only
# device identity/battery (0x0000/0x0306) are common to the whole family - /api/device and
# /api/devices already work for these unmodified. Settings/waypoints/training-logs route
# through tools/legacy_link.py instead - see that file and tools/vendor/ambit_legacy_cli/.
LEGACY_PRODUCT_IDS = {0x0010, 0x0019, 0x001A, 0x001D}  # Bluebird, Duck, Colibri, Greentit


def device_key():
    """A stable per-watch identity for tagging activities, so the desktop cache can keep
    several watches' histories side by side instead of one watch's index-N clobbering
    another's (André, 2026-08-26 - "can we interchange watches?"). The pinned product id
    (hex) distinguishes different models, which is the real fleet here (Ambit1/2/3/Traverse/
    Kailash); two watches of the SAME model would still share it - the serial would be the
    fully-unique key, a cheap future upgrade. "watch" when nothing is pinned."""
    return hex(SELECTED_PRODUCT_ID) if SELECTED_PRODUCT_ID is not None else "watch"


def selected_is_legacy():
    """True when the pinned watch (or, with none pinned, whichever is plugged) is an
    Ambit1/2. Falls back to a real device_info.py query when nothing is pinned - cheap (one
    0x0000/0x0306 round trip, already needed for /api/device) and correct even with the
    default "whichever is plugged" selection, unlike guessing from SELECTED_PRODUCT_ID alone."""
    if SELECTED_PRODUCT_ID is not None:
        return SELECTED_PRODUCT_ID in LEGACY_PRODUCT_IDS
    code, out, err = run_tool("device_info.py", ["--json"])
    info = None
    try:
        info = json.loads(out.strip().splitlines()[-1]) if out.strip() else None
    except Exception:  # noqa: BLE001 - not parseable means "don't know", not legacy
        info = None
    return bool(info) and info.get("model") in ("Bluebird", "Duck", "Colibri", "Greentit")


# Kailash (Hoopoe). Its product id is the one this project had to add a fallback bucket for
# ([[ambit_app_kailash_usb_crash_root_cause]]); named here so backup can dispatch on it the
# same way selected_is_legacy() does for Ambit1/2.
KAILASH_PRODUCT_IDS = {0x002A}


def selected_is_kailash():
    """True when the pinned watch (or, with none pinned, whichever is plugged) is a Kailash.
    Same shape and same fallback as selected_is_legacy() above - one cheap device_info.py
    round trip when nothing is pinned, rather than guessing from SELECTED_PRODUCT_ID alone."""
    if SELECTED_PRODUCT_ID is not None:
        return SELECTED_PRODUCT_ID in KAILASH_PRODUCT_IDS
    code, out, err = run_tool("device_info.py", ["--json"])
    try:
        info = json.loads(out.strip().splitlines()[-1]) if out.strip() else None
    except Exception:  # noqa: BLE001 - not parseable means "don't know", not Kailash
        info = None
    return bool(info) and info.get("model") == "Hoopoe"


def run_tool(script, args, timeout=180):
    """Runs one of tools/*.py exactly as a person at a terminal would. Returns
    (returncode, stdout, stderr); never raises for a nonzero exit, the caller decides what
    that means for the specific tool. Serialized across all callers via WATCH_LOCK - see its
    own comment for why."""
    env = os.environ.copy()
    if SELECTED_PRODUCT_ID is not None:
        env["AMBIT_PRODUCT_ID"] = hex(SELECTED_PRODUCT_ID)
    else:
        env.pop("AMBIT_PRODUCT_ID", None)
    with WATCH_LOCK:
        # In a normal checkout PYTHON is the real interpreter, so it runs tools/<script>
        # directly. In the frozen download PYTHON is this same helper's own executable (there
        # is no separate python), so it can't run a .py file - instead it re-invokes itself
        # with a "--tool" sentinel (frozen_entry.py) that runpy-runs the requested script.
        cmd = ([PYTHON, "--tool", str(TOOLS_DIR / script), *args] if FROZEN
               else [PYTHON, str(TOOLS_DIR / script), *args])
        proc = subprocess.run(
            cmd, cwd=TOOLS_DIR, capture_output=True, text=True, timeout=timeout, env=env)
        return proc.returncode, proc.stdout, proc.stderr


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep stdout clean; errors still surface via response bodies

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    # --- Ember log store (fasting/food/coffee/water) ---------------------------------
    @staticmethod
    def _ember_load():
        try:
            data = json.loads(EMBER_FILE.read_text())
        except Exception:
            data = {"entries": [], "fasts": []}
        data.setdefault("deleted", [])  # tombstones: uids removed here or on the phone
        return data

    @staticmethod
    def _ember_save(data):
        EMBER_DIR.mkdir(parents=True, exist_ok=True)
        EMBER_FILE.write_text(json.dumps(data, indent=2))

    # ---- phone<->desktop sync (shared NAS store) --------------------------------------------
    # Throttle: GET /api/ember pulls, but we don't want a pull on every rapid refresh. Class-level
    # so it's shared across the per-request handler instances.
    _ember_last_pull = 0.0

    @staticmethod
    def _ember_sync_cfg():
        try:
            cfg = json.loads(EMBER_SYNC_FILE.read_text())
            return cfg if isinstance(cfg, dict) and cfg.get("url") else None
        except Exception:
            return None

    @staticmethod
    def _ember_daykey(ts):
        return datetime.fromtimestamp((ts or 0) / 1000).strftime("%Y-%m-%d")

    @staticmethod
    def _ember_ensure_uids(data):
        # Stable, device-tagged ids so a re-push of the same local event dedupes, and a pulled
        # event keeps its origin device's id (so it never gets re-tagged or duplicated).
        changed = False
        for e in data.get("entries", []):
            if not e.get("uid"):
                e["uid"] = "dt-%s-%s" % (e.get("ts"), e.get("type", ""))
                changed = True
        for f in data.get("fasts", []):
            if not f.get("uid"):
                f["uid"] = "dtf-%s" % (f.get("start"),)
                changed = True
        return changed

    @staticmethod
    def _ember_norm_remote_entry(e):
        # A phone "drink" from the coffee family counts as a coffee here too.
        t = e.get("type")
        if t == "drink" and e.get("drinkId") in EMBER_COFFEE_IDS:
            t = "coffee"
        return {"uid": e.get("uid"), "ts": e.get("ts"), "type": t, "name": e.get("name", "Drink"),
                "kcal": int(e.get("kcal", 0) or 0), "caffeineMg": int(e.get("caffeineMg", 0) or 0),
                "volumeMl": int(e.get("volumeMl", 0) or 0), "drinkId": e.get("drinkId")}

    @staticmethod
    def _ember_http(url, token, method, params=None, body=None):
        import urllib.request
        import urllib.parse
        import urllib.error
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url + q, data=data, method=method)
        if token:
            req.add_header("X-Ember-Token", token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                raw = r.read().decode()
            return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as ex:
            # 404 (no file yet) is normal on first GET; anything else we just skip this round.
            return {} if ex.code == 404 else None
        except Exception:
            return None

    @classmethod
    def _ember_sync(cls, data):
        """Two-way merge with the NAS store for today+yesterday. Mutates & saves `data`."""
        cfg = cls._ember_sync_cfg()
        if not cfg:
            return False
        url, token = cfg["url"], cfg.get("token", "")
        changed = cls._ember_ensure_uids(data)
        now = int(time.time() * 1000)
        days = sorted({cls._ember_daykey(now), cls._ember_daykey(now - 86400 * 1000)})
        deleted = set(data.get("deleted", []))  # tombstones: uids that must stay gone everywhere
        seen_e = {e["uid"] for e in data["entries"]}
        seen_f = {f["uid"] for f in data["fasts"]}
        for day in days:
            remote = cls._ember_http(url, token, "GET", {"date": day})
            if remote is None:
                continue  # endpoint unreachable this round; leave local untouched
            deleted |= set(remote.get("deleted", []))  # absorb tombstones from the other device
            for e in remote.get("entries", []):
                uid = e.get("uid")
                if uid and uid not in seen_e and uid not in deleted:
                    data["entries"].append(cls._ember_norm_remote_entry(e))
                    seen_e.add(uid)
                    changed = True
            for f in remote.get("fasts", []):
                uid = f.get("uid")
                if not uid or uid in deleted:
                    continue
                if uid not in seen_f:
                    data["fasts"].append({"uid": uid, "start": f.get("start"), "end": f.get("end"),
                                          "goalHours": f.get("goalHours", 16)})
                    seen_f.add(uid)
                    changed = True
                else:
                    for lf in data["fasts"]:  # a fast ended on the other device
                        if lf.get("uid") == uid and lf.get("end") is None and f.get("end"):
                            lf["end"] = f["end"]
                            changed = True
            # a tombstone from the other device drops the matching local item
            before = len(data["entries"]) + len(data["fasts"])
            data["entries"] = [e for e in data["entries"] if e.get("uid") not in deleted]
            data["fasts"] = [f for f in data["fasts"] if f.get("uid") not in deleted]
            if len(data["entries"]) + len(data["fasts"]) != before:
                changed = True
            # push the union for this day back up (tombstoned uids excluded from entries/fasts,
            # carried in `deleted` so the other device drops them too)
            day_e = [{"uid": e["uid"], "ts": e.get("ts"), "type": e.get("type"), "name": e.get("name"),
                      "kcal": e.get("kcal", 0), "caffeineMg": e.get("caffeineMg", 0),
                      "volumeMl": e.get("volumeMl", 0), "drinkId": e.get("drinkId")}
                     for e in data["entries"] if cls._ember_daykey(e.get("ts", 0)) == day]
            day_f = [{"uid": f["uid"], "start": f.get("start"), "end": f.get("end"),
                      "goalHours": f.get("goalHours", 16)}
                     for f in data["fasts"] if cls._ember_daykey(f.get("start", 0)) == day]
            cls._ember_http(url, token, "POST", None,
                            {"source": "ember", "date": day, "updated": now,
                             "entries": day_e, "fasts": day_f, "deleted": sorted(deleted)})
        if set(data.get("deleted", [])) != deleted:
            changed = True
        data["deleted"] = sorted(deleted)
        if changed:
            cls._ember_save(data)
        return changed

    @staticmethod
    def _ember_summary(data):
        now = int(time.time() * 1000)
        t0 = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        since = now - 14 * 86400 * 1000
        entries = data.get("entries", [])
        fasts = data.get("fasts", [])
        today = [e for e in entries if e.get("ts", 0) >= t0]
        active = next((f for f in fasts if f.get("end") is None), None)
        perday = {}
        for e in entries:
            if e.get("ts", 0) < since:
                continue
            k = datetime.fromtimestamp(e["ts"] / 1000).strftime("%Y-%m-%d")
            r = perday.setdefault(k, {"date": k, "kcal": 0, "coffee": 0, "waterL": 0.0})
            r["kcal"] += e.get("kcal", 0)
            r["waterL"] += e.get("volumeMl", 0) / 1000.0
            if e.get("type") == "coffee":
                r["coffee"] += 1
        for r in perday.values():
            r["waterL"] = round(r["waterL"], 2)
        done = [{"start": f["start"], "end": f["end"], "hours": round((f["end"] - f["start"]) / 3600000, 1)}
                for f in fasts if f.get("end") and f["end"] >= since]
        return {
            "today": {
                "kcal": sum(e.get("kcal", 0) for e in today),
                "coffees": sum(1 for e in today if e.get("type") == "coffee"),
                "waterMl": sum(e.get("volumeMl", 0) for e in today),
                "fastActive": bool(active),
                "fastStart": active["start"] if active else None,
                "fastGoalHours": active.get("goalHours", 16) if active else 16,
            },
            "days": [perday[k] for k in sorted(perday)],
            "fasts": done,
        }

    def _handle_ember_get(self):
        data = self._ember_load()
        # Pull+merge from the NAS (throttled), so phone-logged items appear here.
        try:
            if time.time() - self.__class__._ember_last_pull > 5:
                self._ember_sync(data)
                self.__class__._ember_last_pull = time.time()
        except Exception:
            pass
        self._send_json(200, self._ember_summary(data))

    def _handle_ember_log(self, body):
        data = self._ember_load()
        typ = body.get("type")
        now = int(time.time() * 1000)
        if typ == "coffee":
            data["entries"].append({"ts": now, "type": "coffee", "name": "Coffee", "kcal": 2, "caffeineMg": 95})
        elif typ == "coffee-undo":
            for i in range(len(data["entries"]) - 1, -1, -1):
                if data["entries"][i].get("type") == "coffee":
                    uid = data["entries"][i].get("uid")
                    if uid:  # already synced -> tombstone it so the delete propagates
                        data.setdefault("deleted", []).append(uid)
                    del data["entries"][i]
                    break
        elif typ == "water":
            data["entries"].append({"ts": now, "type": "water", "name": "Water", "volumeMl": int(body.get("volumeMl", 250))})
        elif typ == "meal":
            data["entries"].append({"ts": now, "type": "meal", "name": body.get("name", "Meal"),
                                    "kcal": int(body.get("kcal", 0)), "protein": int(body.get("protein", 0)),
                                    "carbs": int(body.get("carbs", 0)), "fat": int(body.get("fat", 0))})
        elif typ == "drink":
            # a specific beverage from the fast-aware drinks list; counts as a coffee/water where
            # relevant, and ends the fast if it breaks it (same behaviour as the phone app).
            et = "coffee" if body.get("isCoffee") else ("water" if body.get("volumeMl") else "drink")
            data["entries"].append({"ts": now, "type": et, "name": body.get("name", "Drink"),
                                    "kcal": int(body.get("kcal", 0)), "caffeineMg": int(body.get("caffeineMg", 0)),
                                    "volumeMl": int(body.get("volumeMl", 0))})
            if body.get("breaksFast"):
                for f in data["fasts"]:
                    if f.get("end") is None:
                        f["end"] = now
        elif typ == "fast-start":
            for f in data["fasts"]:
                if f.get("end") is None:
                    f["end"] = now
            data["fasts"].append({"start": now, "end": None, "goalHours": int(body.get("goalHours", 16))})
        elif typ == "fast-end":
            for f in data["fasts"]:
                if f.get("end") is None:
                    f["end"] = now
        else:
            self._send_json(400, {"error": "unknown type"})
            return
        self._ember_save(data)
        # Push this new log up to the shared store immediately.
        try:
            self._ember_sync(data)
        except Exception:
            pass
        self._send_json(200, self._ember_summary(data))

    def _guard_local(self):
        """Reject cross-origin / DNS-rebinding requests. This backend is a localhost-only
        bridge that can read and WRITE the watch and touch the filesystem, so a web page the
        user merely visits must not be able to drive it (drive-by CSRF), and a hostname an
        attacker resolves to 127.0.0.1 must not either (DNS rebinding). The app's own callers
        - Qt's QNetworkAccessManager and QML XMLHttpRequest - send neither a web (http/https)
        Origin nor a foreign Host, and OAuth loopback callbacks are top-level navigations with
        no Origin, so all of them pass untouched. Returns True to proceed, else answers 403."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip().lower().strip("[]")
        if host and host not in ("127.0.0.1", "localhost", "::1"):
            self._send_json(403, {"ok": False, "error": "forbidden host"})
            return False
        origin = (self.headers.get("Origin") or "").strip().lower()
        if origin.startswith(("http://", "https://")) \
                and not origin.startswith(("http://127.0.0.1", "http://localhost")):
            self._send_json(403, {"ok": False, "error": "forbidden origin"})
            return False
        return True

    def do_GET(self):
        if not self._guard_local():
            return
        if self.path == "/api/health":
            self._send_json(200, {"ok": True})
        elif self.path == "/api/ember":
            self._handle_ember_get()
        elif self.path == "/api/nav":
            self._handle_nav()
        elif self.path == "/api/activities" or self.path.startswith("/api/activities?"):
            self._handle_activities()
        elif self.path == "/api/garmin/weight" or self.path.startswith("/api/garmin/weight?"):
            self._handle_garmin_weight()
        elif self.path.startswith("/api/garmin/activities"):
            self._handle_garmin_sync("activities")
        elif self.path.startswith("/api/garmin/health"):
            self._handle_garmin_sync("health")
        elif self.path.startswith("/api/garmin/sleep"):
            self._handle_garmin_sync("sleep")
        elif self.path == "/api/pois":
            self._handle_pois_read()
        elif self.path == "/api/backups":
            self._handle_backups_list()
        elif self.path == "/api/device":
            self._handle_device()
        elif self.path == "/api/devices":
            self._handle_devices_list()
        elif self.path == "/api/ble/status":
            self._handle_ble_status()
        elif self.path == "/api/ble/logs/summary":
            self._handle_ble_logs_summary()
        elif self.path == "/api/firmware":
            self._handle_firmware_check()
        elif self.path == "/api/firmware/known":
            self._handle_firmware_known()
        elif self.path == "/api/kailash/history":
            self._handle_kailash_history()
        elif self.path == "/api/kailash/tracklog":
            self._handle_kailash_tracklog()
        elif self.path == "/api/settings" or self.path.startswith("/api/settings?"):
            self._handle_settings_read()
        elif self.path == "/api/legacy/settings":
            self._handle_legacy_settings()
        elif self.path == "/api/customodes":
            self._handle_customodes_read()
        elif self.path == "/api/customodes/field-types":
            self._handle_customodes_field_types()
        elif self.path.startswith("/api/customodes/row-menu"):
            self._handle_customodes_row_menu()
        elif self.path.startswith("/api/customodes/capabilities"):
            self._handle_customodes_capabilities()
        elif self.path == "/api/customodes/activities":
            self._handle_customodes_activities()
        elif self.path.startswith("/api/geocode"):
            self._handle_geocode()
        elif self.path == "/api/demo/devices":
            self._handle_demo_devices()
        elif self.path == "/api/demo":
            self._send_json(200, self._demo_state())
        elif self.path == "/api/agps/status":
            self._handle_agps_status()
        elif self.path == "/api/apps":
            self._handle_apps_read()
        elif self.path == "/api/apps/logging":
            self._handle_apps_logging_read()
        elif self.path == "/api/apps/catalog_status":
            self._handle_apps_catalog_status()
        elif self.path.startswith("/api/apps/catalog"):
            self._handle_apps_catalog()
        elif self.path == "/api/time/zones":
            self._handle_time_zones()
        elif self.path == "/api/gpstrackpod/status":
            self._handle_gpstrackpod_status()
        elif self.path == "/api/gpstrackpod/tracks":
            self._handle_gpstrackpod_tracks()
        elif self.path == "/api/suuntot6/status":
            self._handle_suuntot6_status()
        elif self.path == "/api/suuntot6/logs":
            self._handle_suuntot6_logs()
        elif self.path == "/api/suuntox6hr/status":
            self._handle_suuntox6hr_status()
        elif self.path == "/api/suuntox6hr/logs":
            self._handle_suuntox6hr_logs()
        elif self.path == "/api/legacywatch/status":
            self._handle_legacywatch_status()
        elif self.path == "/api/legacymerge/sources":
            self._handle_legacymerge_sources()
        elif self.path == "/api/legacymerge/devices":
            self._handle_legacymerge_devices()
        elif self.path == "/api/smartsensor/status":
            self._handle_smartsensor_status()
        elif self.path == "/api/trainingprogram":
            self._handle_trainingprogram_list()
        elif self.path == "/api/legacy/sport-modes":
            self._handle_legacy_sport_modes_read()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if not self._guard_local():
            return
        try:
            body = self._read_json_body()
        except json.JSONDecodeError as e:
            self._send_json(400, {"error": f"invalid JSON body: {e}"})
            return

        if self.path == "/api/ble/connect":
            self._handle_ble_connect(body)
        elif self.path == "/api/ble/disconnect":
            self._handle_ble_disconnect()
        elif self.path == "/api/ble/passkey":
            self._handle_ble_passkey(body)
        elif self.path == "/api/ble/forget":
            self._handle_ble_forget()
        elif self.path == "/api/routes":
            self._handle_route_write(body)
        elif self.path == "/api/routes/export":
            self._handle_route_export(body)
        elif self.path == "/api/agps/update":
            self._handle_agps_update(body)
        elif self.path == "/api/firmware/download":
            self._handle_firmware_download(body)
        elif self.path == "/api/firmware/flash":
            self._stream_firmware_flash(body)
        elif self.path == "/api/backup":
            self._handle_backup_create(body)
        elif self.path == "/api/restore":
            self._handle_restore(body)
        elif self.path == "/api/settings":
            self._handle_settings_write(body)
        elif self.path == "/api/intervals/activity-level":
            self._handle_intervals_activity_level(body)
        elif self.path == "/api/intervals/stats-to-watch":
            self._handle_intervals_stats_to_watch(body)
        elif self.path == "/api/intervals/upload":
            self._handle_intervals_upload(body)
        elif self.path == "/api/intervals/workouts":
            self._handle_intervals_workouts(body)
        elif self.path == "/api/ember/log":
            self._handle_ember_log(body)
        elif self.path == "/api/garmin/weight/login":
            self._handle_garmin_weight_login(body)
        elif self.path == "/api/garmin/upload":
            self._handle_garmin_upload(body)
        elif self.path == "/api/device/select":
            self._handle_device_select(body)
        elif self.path == "/api/time/sync":
            self._handle_time_sync(body)
        elif self.path == "/api/demo":
            self._handle_demo(body)
        elif self.path == "/api/customodes/rename":
            self._handle_customodes_rename(body)
        elif self.path == "/api/customodes/field":
            self._handle_customodes_field(body)
        elif self.path == "/api/customodes/interval-timer":
            self._handle_customodes_interval_timer(body)
        elif self.path == "/api/customodes/display-field":
            self._handle_customodes_display_field(body)
        elif self.path == "/api/customodes/displays":
            self._handle_customodes_displays(body)
        elif self.path == "/api/customodes/mode":
            self._handle_customodes_mode(body)
        elif self.path == "/api/customodes/multisport":
            self._handle_customodes_multisport(body)
        elif self.path == "/api/apps/install":
            self._handle_apps_install(body)
        elif self.path == "/api/apps/logging":
            self._handle_apps_logging_write(body)
        elif self.path == "/api/hrv/install":
            self._handle_hrv_install(body)
        elif self.path == "/api/apps/import":
            self._handle_apps_import(body)
        elif self.path == "/api/workout/compile":
            self._handle_workout_compile(body)
        elif self.path == "/api/workout/install":
            self._handle_workout_install(body)
        elif self.path == "/api/pois":
            self._handle_poi_add(body)
        elif self.path == "/api/gpstrackpod/retrieve":
            self._handle_gpstrackpod_retrieve(body)
        elif self.path == "/api/gpstrackpod/logs":
            self._handle_gpstrackpod_logs()
        elif self.path == "/api/suuntot6/retrieve":
            self._handle_suuntot6_retrieve(body)
        elif self.path == "/api/suuntox6hr/retrieve":
            self._handle_suuntox6hr_retrieve(body)
        elif self.path == "/api/legacymerge/run":
            self._handle_legacymerge_run(body)
        elif self.path == "/api/legacymerge/live":
            self._handle_legacymerge_live(body)
        elif self.path == "/api/smartsensor/forget":
            self._handle_smartsensor_forget()
        elif self.path == "/api/trainingprogram":
            self._handle_trainingprogram_save(body)
        elif self.path == "/api/trainingprogram/delete":
            self._handle_trainingprogram_delete(body)
        elif self.path == "/api/trainingprogram/install":
            self._handle_trainingprogram_install(body)
        elif self.path == "/api/trainingprogram/sync-calendar":
            self._handle_trainingprogram_sync_calendar(body)
        elif self.path == "/api/legacy/sport-modes/write-presets":
            self._handle_legacy_sport_mode_write_presets(body)
        elif self.path == "/api/legacy/sport-modes":
            self._handle_legacy_sport_modes_save(body)
        elif self.path == "/api/legacy/sport-modes/write":
            self._handle_legacy_sport_modes_write(body)
        else:
            self.send_response(404)
            self.end_headers()

    # --- read-only: always safe, no --write anywhere below this line ---

    def _handle_nav(self):
        """Routes and POIs currently on the watch. Returns raw CLI output (still used for
        the summary line RouteService.cpp's own regex already parses) plus, since 2026-08-08
        ("add a map for each gpx"), real per-route point tracks via `nav --json` - one
        already-existing JSON line appended after the human-readable output, decoded from
        the exact same flash data already read for the summary, no extra USB round trip."""
        if demo_ambit():
            self._send_json(200, demo_json("nav.json"))
            return
        if ble_bridge.bridge.status().get("handshake_done"):
            self._handle_nav_ble()
            return
        if selected_is_legacy():
            self._handle_nav_legacy()
            return
        code, out, err = run_tool("write_nav.py", ["nav", "--json"])
        routes = self._parse_last_json_line(out)  # same "last JSON line" convention
        self._send_json(200 if code == 0 else 502, {
            "ok": code == 0, "raw_output": out, "stderr": err,
            "routes": (routes or {}).get("routes", [])})

    @staticmethod
    def _legacy_route_groups(waypoints):
        """Ambit1/2 routes are waypoints sharing a route_name; libambit reads them as waypoints
        and never fills routes (so /api/nav's SBEM path shows 0). Group them: a route_name with
        >=2 waypoints is a route (its turn-points ordered by index); the rest stay POIs. Same
        rule as the Android NavigationService/PoiService port. Returns (routes, loose_waypoints).
        """
        import math
        groups = {}
        loose = []
        for w in waypoints:
            rn = str(w.get("route_name", "")).strip()
            if rn:
                groups.setdefault(rn, []).append(w)
            else:
                loose.append(w)
        routes = []
        for name, pts in groups.items():
            if len(pts) < 2:
                loose.extend(pts)
                continue
            pts.sort(key=lambda w: w.get("index", 0))
            track = [{"lat": w.get("lat", 0.0), "lon": w.get("lon", 0.0), "ele": None} for w in pts]
            dist = 0.0
            for a, b in zip(track, track[1:]):
                R, r = 6371000.0, math.radians
                dlat, dlon = r(b["lat"] - a["lat"]), r(b["lon"] - a["lon"])
                h = (math.sin(dlat / 2) ** 2
                     + math.cos(r(a["lat"])) * math.cos(r(b["lat"])) * math.sin(dlon / 2) ** 2)
                dist += 2 * R * math.asin(min(1.0, math.sqrt(h)))
            routes.append({"name": name, "pointCount": len(track), "waypointCount": len(track),
                           "distanceMeters": round(dist), "ascentMeters": 0, "descentMeters": 0,
                           "track": track})
        return routes, loose

    def _handle_nav_legacy(self):
        """GET /api/nav for Ambit1/2: the SBEM route region is empty on this family. Read the
        legacy waypoints (tools/legacy_link.py settings, which now carries each waypoint's
        route_name) and reconstruct routes from them - so the desktop matches the Android app
        (both showed 0 legacy routes before, 2026-08-27)."""
        code, out, err = run_tool("legacy_link.py", ["settings"])
        info = self._parse_last_json_line(out)
        if info is None or not info.get("ok"):
            self._send_json(502, {"ok": False, "error": "legacy_link.py settings produced no "
                                   "parseable JSON", "raw_output": out, "stderr": err})
            return
        routes, _loose = self._legacy_route_groups(info.get("waypoints", []))
        self._send_json(200, {"ok": True, "routes": routes,
                               "raw_output": "legacy Ambit1/2 - %d route(s) reconstructed from "
                               "route-tagged waypoints\n" % len(routes)})

    def _handle_nav_ble(self):
        """The BLE path for GET /api/nav - tools/ble_routes.py's read_nav_summary(). Real
        gap, found live 2026-08-11: route WRITES worked over BLE the same night this was
        added, but the Routes page itself showed nothing/"failed" because this endpoint -
        what actually lists on-watch routes - was still USB-only. `raw_output` is left
        empty; RouteService.cpp's own regex fallback only runs when "routes" is empty,
        which it deliberately isn't here."""
        sys.path.insert(0, str(TOOLS_DIR))
        import ble_routes                                    # noqa: PLC0415
        try:
            ble_bridge.bridge.set_dry_run(False)
            summary = ble_routes.read_nav_summary(ble_bridge.bridge)
        except ble_bridge.BleBridgeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        except (RuntimeError, TimeoutError) as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, {"ok": True, "transport": "ble", "raw_output": "",
                              "routes": summary.get("routes", [])})

    def _handle_activities(self):
        """Recorded moves, as GPX and FIT. Read-only by construction - exercise_log.py's
        `nav` equivalent never has a --write flag, it only reads flash.

        Query: ?known_count=N - real, 2026-08-11 (desktop Activities page perf audit found
        this endpoint decoded and returned the watch's ENTIRE recorded history on every
        single call, even though desktop's own ActivityService already has the older ones
        cached). N is how many oldest activities the caller already has (ActivityService's
        own local index count) - passed straight to exercise_log.py's own --known-count,
        which skips decoding (not re-reading flash for) that many. Omitted/0 behaves exactly
        as before: every activity, same as a fresh cache. total_entries in the response is
        the watch's real current count (from exercise_log.py's own master.json sidecar) -
        the caller compares it against what it thinks it knows; if the watch's log ever
        shrank (wrapped/reset) since the caller's known_count was recorded, old cached
        indices no longer mean the same activity, and the caller has to ask again with 0."""
        if demo_ambit():
            self._send_json(200, demo_json("activities.json"))
            return
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        known_count = query.get("known_count", ["0"])[0]
        # Experimental "mark synced workouts as synced" toggle (Settings, OFF by default).
        # The desktop DeviceService appends ?mark_synced=1 when the user has it on; the
        # backend never marks on its own. See tools/mark_synced.py.
        mark_synced = query.get("mark_synced", ["0"])[0] in ("1", "true")
        if ble_bridge.bridge.status().get("handshake_done"):
            self._handle_activities_ble(int(known_count), mark_synced=mark_synced)
            return
        if selected_is_legacy():
            self._handle_activities_legacy()
            return
        with tempfile.TemporaryDirectory() as tmpdir:
            tool_args = ["--gpx-out", tmpdir, "--fit-out", tmpdir, "--known-count", known_count]
            if mark_synced:
                tool_args.append("--mark-synced")
            # Optional per-app native-stream mapping for logged Suunto App outputs (off by
            # default). The desktop sends ?map=APP=STREAM (repeatable); pass each straight to
            # exercise_log.py's own --map. Values are user-chosen app names + a fixed stream
            # allowlist enforced by the tool, so this is safe to forward verbatim.
            for m in query.get("map", []):
                if "=" in m:
                    tool_args += ["--map", m]
            code, out, err = run_tool("exercise_log.py", tool_args)
            if code != 0:
                self._send_json(502, {"ok": False, "raw_output": out, "stderr": err})
                return
            activities = []
            tmp = Path(tmpdir)
            for gpx_path in sorted(tmp.glob("move*.gpx")):
                n = gpx_path.stem[len("move"):]
                fit_path = tmp / f"move{n}.fit"
                # Logged Suunto App outputs (LogRule=1), if this move has any: exercise_log.py
                # writes a per-move move{n}.rules.json sidecar {slot: {label, times, values}}.
                rules_path = tmp / f"move{n}.rules.json"
                rule_outputs = (json.loads(rules_path.read_text())
                                if rules_path.exists() else None)
                activities.append({
                    "index": int(n),
                    "gpx": gpx_path.read_text(),
                    "fit_base64": (base64.b64encode(fit_path.read_bytes()).decode("ascii")
                                   if fit_path.exists() else None),
                    "rule_outputs": rule_outputs,
                })
            master_path = tmp / "master.json"
            total_entries = (json.loads(master_path.read_text())["total_entries"]
                              if master_path.exists() else len(activities))
            self._send_json(200, {"ok": True, "activities": activities,
                                   "total_entries": total_entries, "device": device_key(),
                                   "raw_output": out})

    def _handle_activities_legacy(self):
        """The Ambit1/2 path for GET /api/activities - tools/legacy_link.py's `logs`
        (openambit's PMEM 2.0 log read, the only implementation of this older protocol in
        the project - see tools/vendor/ambit_legacy_cli/). Real, hardware-tested 2026-08-22
        against André's Ambit1 (0 logs currently on that watch - the code path runs clean,
        content itself is unverified beyond that). GPX only for now: no FIT encoder exists
        yet for this family's log format, so fit_base64 is always null - same "real GPS
        track, no FIT" shape ActivityService already tolerates from tools/gps_track_pod.py.
        known_count/mark_synced (query params on the SBEM path above) don't apply here: the
        legacy log read has no partial-skip or synced-flag mechanism in this project yet.

        The whole-watch log read scales with activity count (a real Ambit2 with 32 moves
        outran the old 120s inner timeout, 2026-08-26); legacy_link.py's logs() now allows
        30 min, so give run_tool a matching outer budget, not the 180s default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            code, out, err = run_tool("legacy_link.py", ["logs", tmpdir], timeout=1830)
            if code != 0:
                self._send_json(502, {"ok": False, "raw_output": out, "stderr": err})
                return
            info = self._parse_last_json_line(out)
            if info is None or not info.get("ok"):
                self._send_json(502, {"ok": False, "error": "legacy_link.py logs produced "
                                       "no parseable JSON", "raw_output": out, "stderr": err})
                return
            activities = []
            for entry in info.get("logs", []):
                gpx_path = Path(entry["gpx_file"])
                gpx = gpx_path.read_text() if gpx_path.exists() else ""
                activities.append({
                    "index": entry["index"],
                    "gpx": self._enrich_legacy_gpx(gpx, entry),
                    "fit_base64": None,
                })
            self._send_json(200, {"ok": True, "activities": activities,
                                   "total_entries": info.get("total_entries", len(activities)),
                                   "device": device_key(), "raw_output": out})

    @staticmethod
    def _enrich_legacy_gpx(gpx, entry):
        """The legacy CLI writes a bare lat/lon/time GPX, but ActivityService.parseGpx reads
        every summary stat (distance/duration/ascent/...) from a GPX <extensions> block -
        that's how the Ambit3/exercise_log.py path populates them. Without it an Ambit1/2
        activity shows a track but ZERO distance/duration/etc. (found 2026-08-26 on a real
        Ambit2 walk: 2.2 km / 26 min on the watch, blank in the app). The legacy `logs` index
        already carries all those numbers, so inject the same extensions here. Inserted before
        <trkseg> (a schema-valid trk position parseGpx reads regardless); no-op on an empty
        GPX or one that already has extensions."""
        if not gpx or "<extensions>" in gpx or "<trkseg>" not in gpx:
            return gpx
        fields = []
        dur = int(round(entry.get("duration_ms", 0) / 1000))
        if dur:
            fields.append("<duration>%d</duration>" % dur)
        for tag, key in (("distance", "distance_m"), ("ascent", "ascent_m"),
                          ("descent", "descent_m"), ("energy", "energy_consumption_kcal"),
                          ("sport_type", "activity_type"), ("avg_hr", "heartrate_avg_bpm"),
                          ("max_hr", "heartrate_max_bpm")):
            v = entry.get(key, 0)
            if v:
                fields.append("<%s>%s</%s>" % (tag, v, tag))
        if not fields:
            return gpx
        ext = "<extensions>" + "".join(fields) + "</extensions>"
        return gpx.replace("<trkseg>", ext + "<trkseg>", 1)

    def _handle_legacy_settings(self):
        """GET /api/legacy/settings - Ambit1/2 personal settings + waypoints, real read via
        tools/legacy_link.py's `settings` (openambit's PMEM 2.0 personal_settings_get).
        Deliberately a separate endpoint from /api/settings, not a branch inside it: that
        endpoint's schema (settings_write.py's Ambit3/Kailash field tables) doesn't apply to
        this much smaller, differently-shaped struct, and this family has no write path yet
        either (see the ambit-app-ambit12-settings-write memory) - hijacking the existing
        read+write Settings page would invite a write UI this protocol can't back up.
        Real, hardware-tested 2026-08-22 against André's Ambit1 (weight/birthyear/HR/etc
        all read correctly; 0 waypoints/routes currently on that watch)."""
        if not selected_is_legacy():
            self._send_json(409, {"ok": False, "error": "the selected/connected watch is "
                                   "not an Ambit1/2 - use /api/settings instead"})
            return
        code, out, err = run_tool("legacy_link.py", ["settings"])
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "legacy_link.py settings produced "
                                   "no parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_legacy_sport_mode_write_presets(self, body):
        """POST /api/legacy/sport-modes/write-presets - Ambit1/2 only. Blind-overwrites the
        watch's sport modes with the same 19 factory presets openambit2 ships (see
        ambit_legacy_cli.c's own header comment: this family's driver has no sport-mode READ
        function in openambit OR openambit2, so there is nothing to preserve first - every
        write here replaces whatever is currently on the watch, no undo). body:
        {"confirm": true} required for a real write; omitted/false runs --dry-run instead,
        which reports the payload shape without touching the watch - same confirm-gate
        convention as /api/trainingprogram/install."""
        if not selected_is_legacy():
            self._send_json(409, {"ok": False, "error": "the selected/connected watch is "
                                   "not an Ambit1/2"})
            return
        confirm = bool((body or {}).get("confirm", False))
        code, out, err = run_tool(
            "legacy_link.py",
            ["sport-mode-write-presets"] + ([] if confirm else ["--dry-run"]))
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {
                "ok": False, "error": "legacy_link.py sport-mode-write-presets produced no "
                "parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    # The factory starting point, offered when the user has no master copy yet. Same 10 the
    # CLI's own preset table ships (openambit2's first 10, capped to this family's real
    # getMaxSportModes()) - kept here too so the UI can show/seed them without a watch present.
    LEGACY_FACTORY_SPORT_MODES = [
        {"name": "Running", "activityId": 10, "modeId": 1, "gpsInterval": 1,
         "recordingInterval": 1, "altiBaroMode": 0, "hrBelt": True, "footPod": False,
         "bikePod": False, "cadencePod": False, "autolapM": 1000},
        {"name": "Trail Running", "activityId": 13, "modeId": 2, "gpsInterval": 1,
         "recordingInterval": 1, "altiBaroMode": 1, "hrBelt": True, "footPod": False,
         "bikePod": False, "cadencePod": False, "autolapM": 5000},
        {"name": "Cycling", "activityId": 11, "modeId": 3, "gpsInterval": 1,
         "recordingInterval": 1, "altiBaroMode": 0, "hrBelt": True, "footPod": False,
         "bikePod": True, "cadencePod": True, "autolapM": 5000},
        {"name": "Mountain Biking", "activityId": 3, "modeId": 4, "gpsInterval": 1,
         "recordingInterval": 1, "altiBaroMode": 1, "hrBelt": True, "footPod": False,
         "bikePod": True, "cadencePod": False, "autolapM": 0},
        {"name": "Hiking", "activityId": 8, "modeId": 5, "gpsInterval": 5,
         "recordingInterval": 5, "altiBaroMode": 1, "hrBelt": False, "footPod": False,
         "bikePod": False, "cadencePod": False, "autolapM": 0},
        {"name": "Trekking", "activityId": 9, "modeId": 6, "gpsInterval": 10,
         "recordingInterval": 10, "altiBaroMode": 1, "hrBelt": False, "footPod": False,
         "bikePod": False, "cadencePod": False, "autolapM": 0},
        {"name": "Nordic Walking", "activityId": 14, "modeId": 7, "gpsInterval": 5,
         "recordingInterval": 5, "altiBaroMode": 0, "hrBelt": True, "footPod": True,
         "bikePod": False, "cadencePod": False, "autolapM": 0},
        {"name": "Rock Climbing", "activityId": 25, "modeId": 8, "gpsInterval": 30,
         "recordingInterval": 15, "altiBaroMode": 1, "hrBelt": True, "footPod": False,
         "bikePod": False, "cadencePod": False, "autolapM": 0},
        {"name": "Ski (Downhill)", "activityId": 26, "modeId": 9, "gpsInterval": 2,
         "recordingInterval": 2, "altiBaroMode": 1, "hrBelt": True, "footPod": False,
         "bikePod": False, "cadencePod": False, "autolapM": 0},
        {"name": "Ski Touring", "activityId": 27, "modeId": 10, "gpsInterval": 5,
         "recordingInterval": 5, "altiBaroMode": 1, "hrBelt": True, "footPod": False,
         "bikePod": False, "cadencePod": False, "autolapM": 0},
    ]
    LEGACY_MAX_SPORT_MODES = 10   # SuuntoLink's own getMaxSportModes(AMBIT/AMBIT2*) - verified

    def _handle_legacy_sport_modes_read(self):
        """GET /api/legacy/sport-modes - the HOST's master copy of the user's Ambit1/2 sport
        modes, plus whether it has ever been saved. This family's protocol has no sport-mode
        read (verified against openambit AND openambit2 - see LEGACY_SPORT_MODES_FILE's own
        comment), so this file is the source of truth, exactly as the Movescount cloud was for
        the real thing. Never touches the watch - it is a plain local read."""
        modes, saved = None, True
        try:
            modes = json.loads(LEGACY_SPORT_MODES_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            modes, saved = None, False
        if not isinstance(modes, list):
            modes, saved = None, False
        if saved:
            self._send_json(200, {
                "ok": True, "saved": True, "source": "file",
                "maxModes": self.LEGACY_MAX_SPORT_MODES, "modes": modes})
            return

        # No master copy yet. Seed it from the WATCH's own real modes rather than a factory
        # table - the whole point of the 2026-08-23 Ambit1 work is that this family CAN be
        # read (docs/ambit1_sport_mode_format.md), so showing invented presets next to a
        # connected watch would be showing data that isn't there. Falls back to the factory
        # set only when there is no readable Ambit1 (no watch, or a different model).
        if selected_is_legacy():
            try:
                sys.path.insert(0, str(TOOLS_DIR))
                import legacy_link                              # noqa: PLC0415
                env_pid = hex(SELECTED_PRODUCT_ID) if SELECTED_PRODUCT_ID is not None else None
                old = os.environ.get("AMBIT_PRODUCT_ID")
                if env_pid:
                    os.environ["AMBIT_PRODUCT_ID"] = env_pid
                try:
                    with WATCH_LOCK:
                        info = legacy_link.ambit1_sport_mode_read()
                finally:
                    if env_pid:
                        if old is None:
                            os.environ.pop("AMBIT_PRODUCT_ID", None)
                        else:
                            os.environ["AMBIT_PRODUCT_ID"] = old
                if info.get("ok") and info.get("modes"):
                    self._send_json(200, {
                        "ok": True, "saved": False, "source": "watch",
                        "maxModes": self.LEGACY_MAX_SPORT_MODES,
                        "modes": info["modes"]})
                    return
            except (RuntimeError, OSError):
                pass    # not an Ambit1, or the read failed - try the 90-byte reader next

        # Ambit2 (and the rest of the Bluebird family): the ambit1_sport_mode_read above is
        # 0x0010-only. tools/legacy_sport_modes.py reads region 0x2000's 90-byte layout and
        # decodes the real modes incl. the hrbelt/pods bitfield (solved from a real capture,
        # 2026-08-26 - docs/ambit2_protocol_findings.md), so the page shows the watch's own
        # modes rather than invented presets. Falls through to factory if nothing decodes.
        if selected_is_legacy():
            try:
                sys.path.insert(0, str(TOOLS_DIR))
                import legacy_sport_modes                       # noqa: PLC0415
                env_pid = hex(SELECTED_PRODUCT_ID) if SELECTED_PRODUCT_ID is not None else None
                old = os.environ.get("AMBIT_PRODUCT_ID")
                if env_pid:
                    os.environ["AMBIT_PRODUCT_ID"] = env_pid
                try:
                    with WATCH_LOCK:
                        modes = legacy_sport_modes.read_app_modes()
                finally:
                    if env_pid:
                        if old is None:
                            os.environ.pop("AMBIT_PRODUCT_ID", None)
                        else:
                            os.environ["AMBIT_PRODUCT_ID"] = old
                if modes:
                    self._send_json(200, {
                        "ok": True, "saved": False, "source": "watch",
                        "maxModes": self.LEGACY_MAX_SPORT_MODES,
                        "modes": modes})
                    return
            except (RuntimeError, OSError):
                pass    # reader unavailable or read failed - fall through to the factory set

        self._send_json(200, {
            "ok": True, "saved": False, "source": "factory",
            "maxModes": self.LEGACY_MAX_SPORT_MODES,
            "modes": self.LEGACY_FACTORY_SPORT_MODES})

    def _handle_legacy_sport_modes_save(self, body):
        """POST /api/legacy/sport-modes - replaces the host master copy. Local only; writing to
        the watch is the separate /write below, so editing and pushing stay distinct actions
        (the same split openambit2 has between its editor's Save and Write to Watch)."""
        modes = (body or {}).get("modes")
        if not isinstance(modes, list) or not modes:
            self._send_json(400, {"ok": False, "error": "body needs a non-empty \"modes\" list"})
            return
        if len(modes) > self.LEGACY_MAX_SPORT_MODES:
            self._send_json(400, {
                "ok": False,
                "error": f"this watch holds at most {self.LEGACY_MAX_SPORT_MODES} sport modes"})
            return
        LEGACY_SPORT_MODES_FILE.parent.mkdir(parents=True, exist_ok=True)
        LEGACY_SPORT_MODES_FILE.write_text(json.dumps(modes, indent=2))
        self._send_json(200, {"ok": True, "count": len(modes)})

    def _handle_legacy_sport_modes_write(self, body):
        """POST /api/legacy/sport-modes/write - pushes the host master copy to the watch.
        Ambit1/2 only. body {"confirm": true} for a real write; otherwise --dry-run. Writes
        whatever is in the request's own "modes" if given (so the UI can push unsaved edits),
        else the saved master copy, else the factory set."""
        if not selected_is_legacy():
            self._send_json(409, {"ok": False, "error": "the selected/connected watch is "
                                   "not an Ambit1/2"})
            return
        modes = (body or {}).get("modes")
        if not isinstance(modes, list) or not modes:
            try:
                modes = json.loads(LEGACY_SPORT_MODES_FILE.read_text())
            except (OSError, json.JSONDecodeError):
                modes = None
            if not isinstance(modes, list) or not modes:
                modes = self.LEGACY_FACTORY_SPORT_MODES
        if len(modes) > self.LEGACY_MAX_SPORT_MODES:
            self._send_json(400, {
                "ok": False,
                "error": f"this watch holds at most {self.LEGACY_MAX_SPORT_MODES} sport modes"})
            return

        confirm = bool((body or {}).get("confirm", False))
        sys.path.insert(0, str(TOOLS_DIR))
        import legacy_link                                    # noqa: PLC0415
        env_pid = hex(SELECTED_PRODUCT_ID) if SELECTED_PRODUCT_ID is not None else None
        old = os.environ.get("AMBIT_PRODUCT_ID")
        if env_pid:
            os.environ["AMBIT_PRODUCT_ID"] = env_pid
        try:
            with WATCH_LOCK:
                info = legacy_link.sport_mode_write(modes, dry_run=not confirm)
        except RuntimeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        finally:
            if env_pid:
                if old is None:
                    os.environ.pop("AMBIT_PRODUCT_ID", None)
                else:
                    os.environ["AMBIT_PRODUCT_ID"] = old
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_activities_ble(self, known_count, mark_synced=False):
        """The BLE path for GET /api/activities - tools/ble_activities.py. Same real
        decode as USB (to_gpx()/to_fit(), unchanged), the ExerciseLog region read directly
        via the driver-path command() proven reliable for routes tonight - no temp
        directory needed since this calls the encode functions in-process instead of
        shelling out to exercise_log.py's own file-writing CLI mode.

        Real note from live testing: this region can be large and the read is currently
        slow over BLE (many small round trips - confirmed correct, CRC-clean throughout,
        just not fast yet). No client-facing timeout added here on purpose - better to let
        it finish than fail a real read; a caller that wants a bound should set its own."""
        sys.path.insert(0, str(TOOLS_DIR))
        import ble_activities                                # noqa: PLC0415
        try:
            ble_bridge.bridge.set_dry_run(False)
            master, entries = ble_activities.read_activities(ble_bridge.bridge, known_count)
        except ble_bridge.BleBridgeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        except (RuntimeError, TimeoutError) as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        activities = []
        count = known_count
        for header, samples in entries:
            count += 1
            fit_bytes = None
            try:
                fit_bytes = ble_activities.to_fit(header, samples)
            except Exception:                                # noqa: BLE001 - GPX is the primary format; a FIT-encode edge case shouldn't fail the whole activity
                pass
            activities.append({
                "index": count,
                "gpx": ble_activities.to_gpx(header, samples),
                "fit_base64": (base64.b64encode(fit_bytes).decode("ascii")
                               if fit_bytes else None),
            })
        # Experimental synced write-back over BLE (unverified - see mark_synced.mark_ble's
        # own docstring). Only the moves decoded this run. Best-effort: a write failure here
        # must not fail the read the user actually asked for.
        if mark_synced and entries:
            try:
                import mark_synced as mark_synced_mod         # noqa: PLC0415
                mark_synced_mod.mark_ble(ble_bridge.bridge, entries)
            except Exception as exc:                          # noqa: BLE001
                print(f"mark-synced (BLE) failed, activities still returned: {exc}")
        self._send_json(200, {"ok": True, "transport": "ble", "activities": activities,
                               "total_entries": master["entries"], "device": device_key()})

    def _handle_pois_read(self):
        """POIs currently on the watch. Raw output, deliberately - unlike routes'
        `show_navigation()`, `pois`'s own `show_entries()` prints fields using whatever the
        real SuuntoLink schema descriptor names them, not a fixed f-string this project can
        read and confirm ahead of time (see write_nav.py's own show_entries()). Guessing a
        parser for that without a real watch to check the actual field names against risks
        silently showing wrong data - same reasoning as /api/nav, applied honestly rather
        than skipped."""
        if demo_ambit():
            self._send_json(200, demo_json("pois.json"))
            return
        if ble_bridge.bridge.status().get("handshake_done"):
            self._handle_pois_read_ble()
            return
        if selected_is_legacy():
            self._handle_pois_read_legacy()
            return
        code, out, err = run_tool("write_nav.py", ["pois"])
        self._send_json(200 if code == 0 else 502, {
            "ok": code == 0, "raw_output": out, "stderr": err})

    def _handle_pois_read_legacy(self):
        """The Ambit1/2 path for GET /api/pois. write_nav.py's `pois` (SBEM 0x0b24) comes
        back EMPTY on this family - they predate SBEM (see legacy_link.py) - so an Ambit2
        with real waypoints showed none in the app (found 2026-08-26 against a 14-waypoint
        Ambit2; the 2026-08-22 Ambit1 test had 0 waypoints, so this gap was never hit). The
        waypoints ARE read by legacy_link.py's `settings` (PMEM 2.0). Reuse that read and
        re-emit them in the exact `Name='..' Location.Latitude=.. Location.Longitude=..`
        text (lat/lon as 1e7 integers) that PoiService::parseOnWatchPois already parses, so
        the same on-watch POI card works unchanged - same raw_output contract as the SBEM
        and BLE branches above."""
        code, out, err = run_tool("legacy_link.py", ["settings"])
        info = self._parse_last_json_line(out)
        if info is None or not info.get("ok"):
            self._send_json(502, {"ok": False, "error": "legacy_link.py settings produced "
                                   "no parseable JSON", "raw_output": out, "stderr": err})
            return
        # Route turn-points (route_name shared by >=2 waypoints) belong to the Routes page,
        # not here - keep only the standalone POIs, same split as /api/nav and the Android app.
        _routes, waypoints = self._legacy_route_groups(info.get("waypoints", []))
        lines = [f"  watch: legacy Ambit1/2 - {len(waypoints)} waypoint(s) via PMEM 2.0"]
        for wp in waypoints:
            name = str(wp.get("name", "")).replace("'", "")
            lat = int(round(float(wp.get("lat", 0.0)) * 1e7))
            lon = int(round(float(wp.get("lon", 0.0)) * 1e7))
            lines.append(f"  Name='{name}'  Location.Latitude={lat}  "
                         f"Location.Longitude={lon}")
        self._send_json(200, {"ok": True, "raw_output": "\n".join(lines) + "\n"})

    def _handle_pois_read_ble(self):
        """The BLE path for GET /api/pois - tools/ble_pois.py's read_pois_summary(). Real
        gap, found live 2026-08-13 (André: POIs suspected of never having been ported to
        BLE, same as routes-listing before it) - confirmed true: this endpoint had no BLE
        branch at all until now."""
        sys.path.insert(0, str(TOOLS_DIR))
        import ble_pois                                      # noqa: PLC0415
        try:
            ble_bridge.bridge.set_dry_run(False)
            raw_output = ble_pois.read_pois_summary(ble_bridge.bridge)
        except ble_bridge.BleBridgeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        except (RuntimeError, TimeoutError) as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, {"ok": True, "transport": "ble", "raw_output": raw_output})

    def _handle_route_export(self, body):
        """Body: {"index": int}. Real request 2026-08-07 ("replicate the function from our
        android app: export as gpx") - read-only (0x0b17, same as /api/nav), never writes.
        `index` matches the order write_nav.py's `nav` already lists on-watch routes in
        (RouteService.onWatchRoutes' own array position), not a separate lookup."""
        try:
            index = int(body.get("index"))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "missing/invalid \"index\""})
            return

        with tempfile.NamedTemporaryFile("w", suffix=".gpx", delete=False) as f:
            gpx_path = f.name
        try:
            code, out, err = run_tool(
                "write_nav.py", ["nav", "--route-gpx", str(index), "--route-gpx-out", gpx_path])
            gpx_text = Path(gpx_path).read_text(encoding="utf-8") if code == 0 else ""
        finally:
            Path(gpx_path).unlink(missing_ok=True)

        self._send_json(200 if code == 0 and gpx_text else 502, {
            "ok": code == 0 and bool(gpx_text), "gpx": gpx_text,
            "raw_output": out, "stderr": err})

    # --- writes: dry-run unless the caller explicitly confirms ---

    def _handle_poi_add(self, body):
        """Body: {"name": str, "lat": float, "lon": float, "type"?: str/int}. This was an honest 501 until
        2026-08-11: the working implementation existed only in the Android app's native
        ambit3_add_poi_to_watch() (confirmed on real hardware 2026-08-06, Milestone 6).
        write_nav.py's `addpoi` is that same algorithm ported back: read the whole POI
        list (0x0b24), rewrite it with the new record first (0x0b25), never touching the
        Waypoints/Routes flash regions and needing no commit. The read-before-write is
        load-bearing - skipping it is what erased the POI store on 2026-08-04."""
        name = (body.get("name") or "").strip()
        try:
            lat = float(body.get("lat"))
            lon = float(body.get("lon"))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "missing/invalid \"lat\"/\"lon\""})
            return
        if not name:
            self._send_json(400, {"error": "a POI needs a name - the watch lists them by it"})
            return
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            self._send_json(400, {"error": f"{lat}, {lon} is not a coordinate on Earth"})
            return

        if ble_bridge.bridge.status().get("handshake_done"):
            self._handle_poi_add_ble(name, lat, lon)
            return

        if selected_is_legacy():
            # Real write, 2026-08-22: openambit's libambit_navigation_write, preserves
            # existing waypoints (read-append-write, same discipline as write_nav.py's own
            # addpoi above) - see tools/legacy_link.py and the ambit-app-hardware-fleet-check
            # memory for the live test this was validated against before being wired here.
            code, out, err = run_tool("legacy_link.py", ["poi-add", name, str(lat), str(lon)])
            info = self._parse_last_json_line(out)
            if info is None or not info.get("ok"):
                self._send_json(502, {"ok": False, "error": (info or {}).get(
                    "error", "legacy_link.py poi-add produced no parseable JSON"),
                    "raw_output": out, "stderr": err})
                return
            self._send_json(200, {"ok": True, "raw_output": out})
            return

        # POI type (icon) - optional; default "Waypoint" (17) if omitted. addpoi validates
        # the id/name and errors cleanly, so no need to pre-check it here.
        args = ["addpoi", "--name", name, "--lat", f"{lat:.7f}", "--lon", f"{lon:.7f}", "--write"]
        poi_type = body.get("type")
        if poi_type not in (None, ""):
            args += ["--type", str(poi_type)]
        code, out, err = run_tool("write_nav.py", args)
        if code != 0:
            self._send_json(502, {"ok": False, "error": (err or out or "").strip()
                                  or "POI write failed", "raw_output": out})
            return
        self._send_json(200, {"ok": True, "raw_output": out})

    def _handle_poi_add_ble(self, name, lat, lon):
        """The BLE path for POST /api/pois - tools/ble_pois.py's add_poi(). Same gap as
        the read side (found live 2026-08-13): never ported despite routes/settings/
        activities all having their own BLE write paths already."""
        sys.path.insert(0, str(TOOLS_DIR))
        import ble_pois                                      # noqa: PLC0415
        try:
            ble_bridge.bridge.set_dry_run(False)
            result = ble_pois.add_poi(ble_bridge.bridge, name, lat, lon)
        except ble_bridge.BleBridgeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        except (RuntimeError, TimeoutError) as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, {"ok": True, "transport": "ble", **result})

    def _handle_route_write(self, body):
        """Body: {"name": str, "gpx": "<gpx xml text>", "confirm": bool}. Without `confirm`,
        runs the exact same rehearsal `write_nav.py route FILE` (no --write) already does -
        real validation, nothing emitted - so a client can sanity-check before committing to
        an actual write.

        **Real incident, 2026-08-11**: this endpoint (and its BLE sibling) only ever sent
        the ONE new route to `write_nav.py route`, which rebuilds the ENTIRE on-watch
        Routes region from exactly the paths it's given - the CLI's own documented
        contract, never honoured by this app's own upload flow. A real BLE test that
        night wiped two of André's existing routes; the exact same bug existed here too,
        just never triggered. Fixed: with `confirm:true`, existing on-watch routes are now
        read first (`_existing_route_gpx_paths()`) and included alongside the new one, so
        an add-a-route tap can no longer silently delete every other route. Skipped for a
        rehearsal (`confirm:false`) - nothing is written either way, so there is nothing to
        preserve, and reading the whole Routes region first would only slow the preview
        down for no safety benefit."""
        gpx_text = body.get("gpx")
        if not gpx_text:
            self._send_json(400, {"error": "missing \"gpx\" (GPX file text)"})
            return
        confirm = bool(body.get("confirm", False))

        with tempfile.NamedTemporaryFile("w", suffix=".gpx", delete=False) as f:
            f.write(gpx_text)
            gpx_path = f.name
        existing_paths = []
        try:
            if ble_bridge.bridge.status().get("handshake_done"):
                self._handle_route_write_ble(gpx_path, confirm)
                return
            if confirm:
                existing_paths = self._existing_route_gpx_paths()
            args = ["route", *existing_paths, gpx_path]
            if confirm:
                args.append("--write")
            code, out, err = run_tool("write_nav.py", args)
        finally:
            Path(gpx_path).unlink(missing_ok=True)
            for p in existing_paths:
                Path(p).unlink(missing_ok=True)

        self._send_json(200 if code == 0 else 502, {
            "ok": code == 0, "wrote": confirm and code == 0,
            "routes_kept": len(existing_paths),
            "raw_output": out, "stderr": err})

    def _existing_route_gpx_paths(self):
        """Every route currently on the watch, each exported to its own temp GPX file -
        the USB-path counterpart to `write_nav.existing_routes_as_gpx()` (BLE calls that
        directly; USB's own architecture is subprocess-per-tool, so this shells out to the
        same already-tested `nav --route-gpx`/`--json` CLI surface `/api/routes/export`
        already uses, once per existing route, rather than importing write_nav.py's
        internals here). Caller is responsible for deleting the returned paths."""
        code, out, err = run_tool("write_nav.py", ["nav", "--json"])
        summary = self._parse_last_json_line(out)
        route_count = len(summary.get("routes", [])) if summary else 0
        paths = []
        for index in range(route_count):
            with tempfile.NamedTemporaryFile("w", suffix=".gpx", delete=False) as f:
                gpx_path = f.name
            code, out, err = run_tool(
                "write_nav.py", ["nav", "--route-gpx", str(index),
                                 "--route-gpx-out", gpx_path])
            if code == 0 and Path(gpx_path).stat().st_size > 0:
                paths.append(gpx_path)
            else:
                Path(gpx_path).unlink(missing_ok=True)
        return paths

    def _handle_route_write_ble(self, gpx_path, confirm):
        """The BLE path for route writes - tools/ble_routes.py. Same rehearsal-first
        contract as the USB path: confirm=false leaves ble_bridge in its default dry_run
        state (nothing sent), confirm=true is a REAL flash write over BLE - unverified
        against any real capture this project has (see ble_routes.py's own docstring), so
        a first real attempt should be small and cautious. WATCH_LOCK isn't needed here the
        way it is for USB - the BLE connection is inherently single-session, not something
        two subprocesses can race for."""
        sys.path.insert(0, str(TOOLS_DIR))
        import ble_routes                                    # noqa: PLC0415
        try:
            ble_bridge.bridge.set_dry_run(not confirm)
            result = ble_routes.write_route(ble_bridge.bridge, [gpx_path])
        except ble_bridge.BleBridgeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        except (RuntimeError, TimeoutError) as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, {"transport": "ble", "wrote": confirm, **result})

    def _parse_last_json_line(self, out):
        """Shared by every tool invocation that prints human-readable progress lines (real
        commands, real byte counts) *and* a machine-readable summary - sgee.py --status
        --json and write_nav.py nav --json both end with exactly one JSON line after their
        own diagnostic output, not at a fixed position, so lines are tried in order and the
        last successfully-parsed one wins rather than assuming line count/position."""
        parsed = None
        for line in out.strip().splitlines():
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
        return parsed

    def _handle_agps_status(self):
        """GET /api/agps/status - real, read-only 0x0b15 query, no network fetch and
        nothing written. What HomeViewModel shows before any Update tap, and what
        /api/agps/update's offline fallback also reports."""
        if demo_ambit():
            self._send_json(200, demo_json("agps_status.json"))
            return
        status, error = self._read_orbit_status()
        if status is None:
            self._send_json(502, {"ok": False, "error": error})
            return
        self._send_json(200, {"ok": True, **status})

    def _read_orbit_status(self):
        """Shared by /api/agps/status and /api/agps/update's own initial freshness check -
        real bug, found live 2026-08-11: /api/agps/update had its OWN hardcoded USB-only
        `run_tool("sgee.py", ["--status", "--json"])` call, never updated when the BLE path
        was added to /api/agps/status - so a BLE-connected watch's Home page could show a
        perfectly correct GPS orbit status, then immediately show "Failed" again the moment
        the once-per-connection auto-update (DeviceService::fetchDeviceInfo(), matching
        syncTime()'s own auto-sync) ran and hit this exact same status read a second time
        via the wrong path. Returns (status_dict, None) or (None, error_message)."""
        if ble_bridge.bridge.status().get("handshake_done"):
            sys.path.insert(0, str(TOOLS_DIR))
            import ble_sgee                                   # noqa: PLC0415
            try:
                ble_bridge.bridge.set_dry_run(False)
                return ble_sgee.read_status(ble_bridge.bridge), None
            except ble_bridge.BleBridgeError as exc:
                return None, str(exc)
            except (RuntimeError, TimeoutError) as exc:
                return None, str(exc)
        code, out, err = run_tool("sgee.py", ["--status", "--json"])
        status = self._parse_last_json_line(out)
        if status is None:
            return None, "couldn't read orbit status from the watch"
        return status, None

    def _handle_agps_status_ble(self):
        """The BLE path for GET /api/agps/status - tools/ble_sgee.py. Real, read-only
        0x0b15 (+ a GlonassSGEE flash read if the watch declares that region)."""
        sys.path.insert(0, str(TOOLS_DIR))
        import ble_sgee                                       # noqa: PLC0415
        try:
            ble_bridge.bridge.set_dry_run(False)
            status = ble_sgee.read_status(ble_bridge.bridge)
        except ble_bridge.BleBridgeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        except (RuntimeError, TimeoutError) as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, {"ok": True, "transport": "ble", **status})

    def _handle_agps_update(self, body):
        """Body: {"confirm": bool}. Real request 2026-08-07 ("it is not validity, it is
        update - if it's less than one day, say No update needed, otherwise download if
        online, otherwise just read the watch's own date"):

        1. Always reads the watch's own current orbit date first (0x0b15, real but
           read-only, same call as /api/agps/status) - this needs no network at all.
        2. If that's under a day old, skips the network fetch and the write entirely -
           "no update needed" is a real state this reports, not just a UI label.
        3. Otherwise tries the live download; if that fails (most likely: no internet),
           falls back to reporting the watch's already-known current date rather than
           erroring outright - genuinely nothing more can be done without a network, and
           that is not the same situation as a bug.
        4. If the download succeeds, runs sgee.py against it - with --write only if
           confirmed, same rehearsal-first pattern as routes.
        """
        if demo_ambit():
            # Testing mode: no watch and no network write. The sample watch's stored orbit
            # (agps_status.json) is yesterday's, so an update would fetch+write - simulate a
            # successful update so the Update button demonstrates its "Updated" outcome.
            self._send_json(200, {"ok": True, "wrote": True,
                                  "watch_date": demo_json("agps_status.json").get("date"),
                                  "gps": {"wrote": True},
                                  "glonass": {"supported": False}})
            return
        confirm = bool(body.get("confirm", False))
        # Real, 2026-08-10 (André: "on kailash settings, give the option to disable it,
        # name it ephemeris gps only"). An APP preference, not a field on the watch - the
        # UI owns and persists it and passes it here; nothing about it is stored on the
        # device. Default false, i.e. we send GLONASS too wherever the watch supports it.
        gps_only = bool(body.get("gps_only", False))

        watch_status, error = self._read_orbit_status()
        if watch_status is None:
            self._send_json(502, {
                "ok": False, "error": error or "couldn't read the watch's current orbit status"})
            return

        def fresh(date_str, time_str="00:00:00"):
            """Under a day old? Same rule for both constellations."""
            try:
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S") \
                    .replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                return False
            return (datetime.now(timezone.utc) - dt).total_seconds() < 86400

        def fetch_and_write(url, glonass=False):
            """Download one ephemeris file and hand it to sgee.py (USB) or
            ble_sgee.write_orbit() (BLE, tools/ble_sgee.py - same bounds-checked
            build_sgee_for_region()/send_plan() sgee.py itself uses, unchanged). Returns a
            small result dict; never raises for an offline network, which is a real state
            rather than an error (see this method's own docstring)."""
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    data = resp.read()
            except urllib.error.URLError as e:
                return {"wrote": False, "offline": True,
                        "error": f"couldn't reach the orbital data server: {e}"}
            if ble_bridge.bridge.status().get("handshake_done"):
                sys.path.insert(0, str(TOOLS_DIR))
                import ble_sgee                                # noqa: PLC0415
                try:
                    ble_bridge.bridge.set_dry_run(not confirm)
                    result = ble_sgee.write_orbit(ble_bridge.bridge, data, glonass=glonass)
                    return {**result, "fetched_bytes": len(data)}
                except (ble_bridge.BleBridgeError, RuntimeError, TimeoutError) as exc:
                    return {"ok": False, "wrote": False, "error": str(exc),
                            "fetched_bytes": len(data)}
            with tempfile.NamedTemporaryFile("wb", suffix=".bin", delete=False) as f:
                f.write(data)
                bin_path = f.name
            try:
                args = [bin_path] + (["--glonass"] if glonass else [])
                if confirm:
                    args.append("--write")
                code, out, err = run_tool("sgee.py", args)
            finally:
                Path(bin_path).unlink(missing_ok=True)
            return {"ok": code == 0, "wrote": confirm and code == 0,
                    "fetched_bytes": len(data), "raw_output": out, "stderr": err}

        result = {"ok": True, "watch_date": watch_status.get("date"),
                  "watch_valid": watch_status.get("valid", False)}

        # --- GPS ---
        if watch_status.get("valid") and fresh(watch_status.get("date"),
                                                watch_status.get("time", "00:00:00")):
            result["gps"] = {"skipped": True, "reason": "No update needed",
                             "watch_date": watch_status.get("date")}
        else:
            result["gps"] = fetch_and_write(GPS_ORBIT_URL)

        # --- GLONASS ---
        # Capability comes from the WATCH (does it declare a GlonassSGEE region), never
        # from a model list - see GLONASS_ORBIT_URL's own comment. Note there is no 0x0b15
        # equivalent for GLONASS, so freshness is read out of the region's own header.
        glo = watch_status.get("glonass") or {}
        if not glo.get("supported"):
            result["glonass"] = {"supported": False}
        elif gps_only:
            result["glonass"] = {"supported": True, "skipped": True,
                                 "reason": "Ephemeris GPS only is on"}
        elif glo.get("valid") and fresh(glo.get("date")):
            result["glonass"] = {"supported": True, "skipped": True,
                                 "reason": "No update needed",
                                 "watch_date": glo.get("date")}
        else:
            result["glonass"] = {"supported": True, **fetch_and_write(
                GLONASS_ORBIT_URL, glonass=True)}

        failed = [k for k in ("gps", "glonass")
                  if result[k].get("ok") is False]
        result["ok"] = not failed
        result["wrote"] = any(result[k].get("wrote") for k in ("gps", "glonass"))
        self._send_json(200 if not failed else 502, result)

    def _handle_backups_list(self):
        """Every backup made so far - just a directory listing, real prefixes `nav --save`/
        `restore` already understand, nothing invented here."""
        BACKUP_DIR.mkdir(exist_ok=True)
        backups = []
        seen_prefixes = set()
        for routes_file in sorted(BACKUP_DIR.glob("*-routes.bin"), reverse=True):
            prefix = str(routes_file)[:-len("-routes.bin")]
            waypoints_file = Path(f"{prefix}-waypoints.bin")
            if not waypoints_file.exists():
                continue  # an incomplete save - both files always get written together
            backups.append({
                "prefix": prefix,
                "label": Path(prefix).name,
                "createdAt": routes_file.stat().st_mtime,
                "hasEmber": Path(f"{prefix}-ember.json").exists(),
                "hasRoutes": True,
                "hasKailash": Path(f"{prefix}-kailash-history.json").exists()
                              or Path(f"{prefix}-kailash-tracklog.json").exists(),
            })
            seen_prefixes.add(prefix)
        # Ember-only backups (no watch connected when the backup was made - a Garmin owner, or
        # no watch at all) have no -routes.bin, so the loop above never finds them. List them
        # too, or they'd be made successfully but then invisible forever.
        for ember_file in sorted(BACKUP_DIR.glob("*-ember.json"), reverse=True):
            prefix = str(ember_file)[:-len("-ember.json")]
            if prefix in seen_prefixes:
                continue
            backups.append({
                "prefix": prefix,
                "label": Path(prefix).name,
                "createdAt": ember_file.stat().st_mtime,
                "hasEmber": True,
                "hasRoutes": False,
                "hasKailash": Path(f"{prefix}-kailash-history.json").exists()
                              or Path(f"{prefix}-kailash-tracklog.json").exists(),
            })
            seen_prefixes.add(prefix)
        # Kailash archives have neither -routes.bin nor -ember.json, so neither loop above
        # finds them - they would be written successfully and then be invisible forever, the
        # same trap the Ember-only loop exists to avoid.
        for kail_file in sorted(BACKUP_DIR.glob("*-kailash-history.json"), reverse=True):
            prefix = str(kail_file)[:-len("-kailash-history.json")]
            if prefix in seen_prefixes:
                continue
            backups.append({
                "prefix": prefix,
                "label": Path(prefix).name,
                "createdAt": kail_file.stat().st_mtime,
                "hasEmber": False,
                "hasRoutes": False,
                "hasKailash": True,
            })
            seen_prefixes.add(prefix)
        backups.sort(key=lambda b: b["createdAt"], reverse=True)
        self._send_json(200, {"ok": True, "backups": backups})

    def _handle_ble_status(self):
        """GET /api/ble/status - read-only, safe to poll like DeviceService's own USB
        heartbeat. "subscribed" is the real "transport is live" signal (the watch has
        subscribed to our notify characteristic) - same meaning as
        AmbitBleModule.kt's nativeInitStarted gate on the Android side."""
        self._send_json(200, ble_bridge.bridge.status())

    def _handle_ble_logs_summary(self):
        """GET /api/ble/logs/summary - the first real post-handshake 0x1200 request in the
        activity-log sequence (tools/ble_logs.py). Deliberately NOT the full activity list:
        that needs a pagination loop this project hasn't verified against a live watch yet
        (see ble_logs.py's own docstring for exactly what's missing and why it isn't
        guessed). Returns the raw reply as hex - real, tested plumbing, not a parsed
        result - so this is honest about being a first slice, not the finished feature."""
        if not ble_bridge.bridge.status().get("handshake_done"):
            self._send_json(409, {"ok": False, "error": "no BLE connection with a "
                                   "completed handshake - connect first"})
            return
        sys.path.insert(0, str(TOOLS_DIR))
        import ble_logs                                      # noqa: PLC0415
        try:
            ble_bridge.bridge.set_dry_run(False)
            reply = ble_logs.fetch_log_summary(ble_bridge.bridge)
        except ble_bridge.BleBridgeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        except (RuntimeError, TimeoutError) as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, {"ok": True, "payload_hex": reply.hex(), "len": len(reply)})

    def _handle_ble_connect(self, body):
        """POST /api/ble/connect {"forget": bool} - starts the ble_server.py daemon (a
        no-op if one is already running) and returns as soon as it's reachable. `forget`
        mirrors ble_server.py's own --forget: NOT the default, since a bond just
        established is what lets a reconnect work - only opt in when pairing is stuck,
        same guidance PROJECT_RULES.md gives for the watch's own "always unpair" menu
        action, not something to do on every connect tap.

        Deliberately does NOT block waiting for the watch to subscribe (an earlier version
        did, up to 25s) - a fresh pairing needs a human to read a passkey off the watch and
        report it back via /api/ble/passkey (HANDOFF.md Milestone 7 item 16), which can
        easily take longer than any bounded wait here would allow, and blocking this
        request would leave the UI with nothing to poll while that's happening. The caller
        polls GET /api/ble/status instead - "subscribed"/"handshake_done"/
        "pending_passkey_device" are exactly the states a connect UI needs to show."""
        try:
            ble_bridge.bridge.start(forget=bool(body.get("forget", False)))
        except ble_bridge.BleBridgeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, {"ok": True, **ble_bridge.bridge.status()})

    def _handle_ble_disconnect(self):
        """POST /api/ble/disconnect - tears the daemon down. Leaves the watch's bond alone;
        see ble_bridge.BleBridge.stop()'s own comment."""
        ble_bridge.bridge.stop()
        self._send_json(200, {"ok": True})

    def _handle_ble_forget(self):
        """POST /api/ble/forget - real request 2026-08-13 ("we need to add a button to
        forget the watch"), for exactly the recovery dance PROJECT_RULES.md already
        recommends for a stuck pairing (always Unpair, never Replace) but that, until now,
        only the watch's own menu could do - the Linux side of the same bond had no UI
        equivalent. ble_bridge.BleBridge.forget() reaches the daemon over its control
        socket regardless of which process spawned it (or if a person started it by hand),
        unlike stop()+start(forget=True) which only works when this backend is the one
        that spawned it. No daemon running at all is not an error here - "already
        forgotten" is a fine outcome for a forget request."""
        if not ble_bridge.bridge.status().get("running"):
            self._send_json(200, {"ok": True, "removed": 0})
            return
        try:
            removed = ble_bridge.bridge.forget()
        except ble_bridge.BleBridgeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, {"ok": True, "removed": removed})

    def _handle_ble_passkey(self, body):
        """POST /api/ble/passkey {"passkey": 123456} - a fresh pairing needs this: the
        watch (LE Legacy Passkey Entry, HANDOFF.md Milestone 7 item 16) shows a 6-digit
        code with no way for this app to read it directly, so the UI has to ask the person
        in front of the watch and relay it here. /api/ble/status's own
        "pending_passkey_device" tells the UI when to show that prompt at all."""
        passkey = body.get("passkey")
        if not isinstance(passkey, int):
            self._send_json(400, {"ok": False, "error": "passkey must be an integer"})
            return
        try:
            ok = ble_bridge.bridge.submit_passkey(passkey)
        except ble_bridge.BleBridgeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        self._send_json(200 if ok else 409,
                        {"ok": ok, **({} if ok else {"error": "no passkey request pending"})})

    def _handle_device(self):
        """Model/serial/firmware/hardware/battery - tools/device_info.py, added 2026-08-07.
        Real commands write_nav.py already sends (0x0000) or already names from captures but
        never sent (0x0306) - see that file's own docstring for exactly where the reply
        layout came from (openambit's real implementation) and how it was cross-checked
        (hw_version matched HANDOFF.md's independently-documented value exactly once a real
        parsing bug was fixed). Confirmed working against real hardware in this same
        session, unlike most of this backend."""
        if demo_ambit():
            info = demo_json("device.json")
            sys.path.insert(0, str(TOOLS_DIR))
            import row_bridge                                # noqa: PLC0415
            row = row_bridge.load_rows().get("variants", {}).get(DEMO["variant"], {})
            info["model"] = DEMO["variant"]
            if row.get("productName"):
                info["name"] = row["productName"]
            self._send_json(200, info)
            return
        if ble_bridge.bridge.status().get("handshake_done"):
            self._handle_device_ble()
            return
        code, out, err = run_tool("device_info.py", ["--json"])
        if code != 0:
            self._send_json(502, {"ok": False, "raw_output": out, "stderr": err})
            return
        last_line = out.strip().splitlines()[-1] if out.strip() else ""
        try:
            info = json.loads(last_line)
        except json.JSONDecodeError:
            self._send_json(502, {"ok": False, "error": "device_info.py --json produced "
                                   "no parseable JSON", "raw_output": out})
            return
        self._send_json(200, {"ok": True, **info})

    def _handle_devices_list(self):
        """GET /api/devices - every Suunto watch on the USB bus, for the Home watch-switcher
        (2026-08-16, porting the Android multi-watch picker). tools/list_watches.py mirrors
        write_nav.Link's own enumerate walk, so the list is exactly the set Link could open.
        Includes `selected` (the product_id currently pinned via /api/device/select, or null)."""
        code, out, err = run_tool("list_watches.py", [])
        last_line = out.strip().splitlines()[-1] if out.strip() else ""
        try:
            info = json.loads(last_line)
        except (json.JSONDecodeError, IndexError):
            self._send_json(502, {"ok": False, "error": "list_watches.py produced no parseable "
                                   "JSON", "raw_output": out, "stderr": err})
            return
        info["selected"] = SELECTED_PRODUCT_ID
        self._send_json(200, info)

    def _handle_device_select(self, body):
        """POST /api/device/select {"productId": int|null} - pin which watch every subsequent
        tool targets when several share the USB bus (or null to go back to "whichever is
        plugged"). run_tool() hands the choice to the tools via AMBIT_PRODUCT_ID."""
        global SELECTED_PRODUCT_ID
        pid = body.get("productId")
        if pid is None:
            SELECTED_PRODUCT_ID = None
            self._send_json(200, {"ok": True, "selected": None})
            return
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            self._send_json(400, {"ok": False, "error": f"productId must be an integer, got {pid!r}"})
            return
        SELECTED_PRODUCT_ID = pid
        self._send_json(200, {"ok": True, "selected": pid})

    def _handle_device_ble(self):
        """The BLE path for /api/device, taken once ble_bridge reports the bootstrap
        handshake is done.

        Real bug, caught live on hardware, 2026-08-11: this originally called
        device_info.py's read_device_info() unchanged, on the assumption that
        `ble_bridge.bridge` presenting the same `.command()` shape as a USB `Link` was
        enough. It isn't - that function SENDS a 0x0000 request and waits for a reply,
        which is the USB (phone-drives) pattern. Over BLE the watch drives the opening
        exchange instead (see ServerLink's own handshake code and HANDOFF.md Milestone 7
        items 9-10, ported from Android's real fix for the identical bug) - the phone
        answers the watch's pushed 0x1201/0x0002, it never gets to ask. Model/serial/fw/hw
        therefore come from `ble_bridge`'s status(), populated by that handshake, not from
        a request made here.

        Battery is different: 0x0306 is a genuine phone-driven request in the flow that
        runs AFTER the handshake (protocol_ble.c's driver path, flags=0x05) - `command()`
        now refuses to run before handshake_done for exactly this reason, so calling it
        here is safe once we already know the handshake completed."""
        sys.path.insert(0, str(TOOLS_DIR))
        import ble_server                                    # noqa: PLC0415
        status = ble_bridge.bridge.status()
        info = dict(status.get("device_info") or {})
        try:
            ble_bridge.bridge.set_dry_run(False)
            battery_reply = ble_bridge.bridge.command(0x0306, b"")
            if len(battery_reply) >= 2:
                info["battery_percent"] = battery_reply[1]
            # The handshake's hello only carries its own raw id string (see the docstring
            # above) - this is the real numeric serial, a second driver-path request
            # (ble_server.py's own parse_compact_serial(), reverse-engineered from the real
            # Suunto app's capture, 2026-08-11). Overrides the hello's id on success; falls
            # back to it silently if this one request fails, rather than failing the whole
            # device read over one non-essential field.
            try:
                serial_reply = ble_bridge.bridge.command(
                    ble_server.CMD_GET_COMPACT_SERIAL, ble_server.COMPACT_SERIAL_REQUEST)
                serial = ble_server.parse_compact_serial(serial_reply)
                if serial:
                    info["serial"] = serial
            except (ble_bridge.BleBridgeError, RuntimeError, TimeoutError):
                pass
        except ble_bridge.BleBridgeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        except (RuntimeError, TimeoutError) as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        self._send_json(200, {"ok": True, "transport": "ble", **info})

    def _handle_firmware_check(self):
        """Latest firmware available for the connected watch + a real download URL -
        tools/firmware_check.py, added 2026-08-07 (see V3_CHANGELOG.md). Read-only: this
        only asks Suunto's own device-info service what's available, nothing is downloaded
        or written to the watch here."""
        if demo_ambit():
            self._send_json(200, demo_json("firmware.json"))
            return
        code, out, err = run_tool("firmware_check.py", ["--json"])
        if code != 0:
            self._send_json(502, {"ok": False, "raw_output": out, "stderr": err})
            return
        last_line = out.strip().splitlines()[-1] if out.strip() else ""
        try:
            info = json.loads(last_line)
        except json.JSONDecodeError:
            self._send_json(502, {"ok": False, "error": "firmware_check.py --json produced "
                                   "no parseable JSON", "raw_output": out})
            return
        # firmware_check.py reports the model as a codename ("Emu", "Jabiru", ...); add the
        # friendly product name so the Firmware page reads "Suunto Ambit 3 Peak", consistent
        # with Home and Watch settings, instead of falling back to the codename.
        if info.get("model") and not info.get("product"):
            sys.path.insert(0, str(TOOLS_DIR))
            import row_bridge                                    # noqa: PLC0415
            row = row_bridge.load_rows().get("variants", {}).get(info["model"], {})
            if row.get("productName"):
                info["product"] = row["productName"]
        self._send_json(200, info)

    def _handle_smartsensor_status(self):
        """GET /api/smartsensor/status - Suunto Smart Sensor (the HR belt) identity/
        battery/heart-rate, tools/smart_sensor.py, added 2026-08-13. Entirely independent
        of DeviceService's watch (this is a second, unrelated BLE peripheral) - read-only,
        real hardware-confirmed. Generous timeout: a just-forgotten belt reconnecting from
        scratch (no cached GATT database) can genuinely take ~35-45s real BLE discovery
        time on this hardware; an already-known/connected belt (the common case) is much
        faster, well under 20s."""
        code, out, err = run_tool("smart_sensor.py", ["--status", "--json"], timeout=70)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "smart_sensor.py --json produced "
                                   "no parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_smartsensor_forget(self):
        """POST /api/smartsensor/forget - unpairs/removes the belt from BlueZ entirely
        (org.bluez.Adapter1.RemoveDevice), tools/smart_sensor.py --forget. Real request,
        2026-08-13 (André, after testing the paired flow end to end): a way back to a
        clean slate to re-exercise Pair, with no terminal needed. Not destructive to the
        belt - it holds no bond secret worth losing (Just Works pairing) - so this is a
        safely repeatable Bluetooth-side reset, not a real device action."""
        code, out, err = run_tool("smart_sensor.py", ["--forget", "--json"], timeout=20)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "smart_sensor.py --json produced "
                                   "no parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_kailash_history(self):
        """GET /api/kailash/history - Kailash only. Real, read-only 0x1200
        sml.DeviceHistory query (tools/kailash_history.py --json, added 2026-08-08): visited
        cities/countries, travel stats, and the "activity mode" logbook, all bundled in the
        same reply - see that tool's own docstring for how the query and its two real unit
        conversions (Duration=raw/10, Location=float32 radians) were found and confirmed
        against real hardware. Live-verified end to end this same session against André's own
        watch (1 city/country, Lille France - matches the watch's own "7R" screen exactly)."""
        code, out, err = run_tool("kailash_history.py", ["--json"])
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "kailash_history.py --json produced "
                                   "no parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_kailash_tracklog(self):
        """GET /api/kailash/tracklog - Kailash only. Real, read-only flash read of the
        TrackLog region (tools/kailash_tracklog.py --json, added 2026-08-08), reshaped into
        a JSON array `activities` of the same activity shape ActivityService/GarminService
        already use (name/startTime/distanceMeters/durationSeconds/track/gpxText) so the
        existing ActivityCard/MapView QML needs no new code to show them. Real, 2026-08-09:
        each real DeviceHistory session (the "activity mode" logbook) gets its own real GPS
        segment, correlated by timestamp against these TrackLog points (see
        kailash_tracklog.py's own split_into_activities() docstring) - falls back to one
        bundled activity if nothing correlates. No ascent/FIT here - TrackLog carries no
        confirmed altitude field and there's no FIT writer for this format (see that tool's
        own docstring). A real ~1.3MB flash read over USB, not a short SBEM query - slower
        than most endpoints here, hence the longer timeout. Live-verified this same session:
        56 real points, distance/track matching André's own known location (Lille, France)."""
        code, out, err = run_tool("kailash_tracklog.py", ["--json"], timeout=300)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "kailash_tracklog.py --json produced "
                                   "no parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    # Ambit1 settings, mapped onto the SAME schema the Ambit3 page consumes so one Watch
    # settings screen serves both. André, 2026-08-23: "ambit setting just show like before,
    # not at all like ambit 3...please solve it, matching device capabilities".
    #
    # (key, label, screen, control, choices, unit) - labels/controls/screens deliberately
    # reuse settings_write.py's own AMBIT3_DISPLAY wording so a field is named identically on
    # both watches. Only fields the Ambit1 actually reports are listed; anything the device
    # has no field for is simply absent rather than shown greyed or faked.
    LEGACY_SETTING_SPECS = [
        # --- General -------------------------------------------------------------------
        ("language",             "Language",              "general",  "dropdown",
         [[0,"Dansk"],[1,"Deutsch"],[2,"English"],[3,"Espanol"],[4,"Francais"],[5,"Italiano"],
          [6,"Nederlands"],[7,"Norsk"],[8,"Portugues"],[9,"Suomi"],[10,"Svenska"],
          [11,"Chinese"],[12,"Japanese"],[13,"Korean"],[14,"Cestina"],[15,"Polski"],
          [16,"Russian"]], None),
        ("backlight_mode",       "Backlight mode",        "general",  "dropdown",
         [[0,"Normal"],[1,"Off"],[2,"Night"],[3,"Toggle"]], None),
        ("backlight_brightness", "Backlight brightness",  "general",  "slider", None, "%"),
        ("display_brightness",   "Display contrast",      "general",  "slider", None, "%"),
        ("display_is_negative",  "Display",               "general",  "radio",
         [[0,"Light"],[1,"Dark"]], None),
        ("tones_mode",           "Tones",                 "general",  "radio",
         [[0,"Off"],[1,"On"]], None),
        ("timemode_button_lock", "Button lock, time mode","general",  "radio",
         [[0,"Actions only"],[1,"All buttons"]], None),
        ("sportmode_button_lock","Button lock, sport mode","general", "radio",
         [[0,"Actions only"],[1,"All buttons"]], None),
        ("sync_time_w_gps",      "GPS time keeping",      "general",  "radio",
         [[0,"Off"],[1,"On"]], None),
        ("gps_position_format",  "GPS position format",   "general",  "dropdown",
         [[0,"WGS84 hd.d"],[1,"WGS84 hd m.m"],[2,"WGS84 hd m s.s"],[3,"UTM"],[4,"MGRS"]], None),
        ("alti_baro_mode",       "Alti-baro profile",     "general",  "radio",
         [[0,"Automatic"],[1,"Altimeter"],[2,"Barometer"]], None),
        # storm_alarm / fused_alti_disabled are NOT listed on purpose. They exist in
        # libambit's shared struct, but across BOTH of André's captures - in which he
        # deliberately exercised every settings control - SuuntoLink never wrote either byte
        # (@61, @62). Neither appears in the Ambit1's Devices.xml options. The "Storm alarm"
        # on this watch is an installed App, not a built-in setting. Showing them would be
        # exposing struct padding as features.
        ("compass_declination_dir", "Compass declination", "general", "dropdown",
         [[0,"Off"],[1,"East"],[2,"West"]], None),
        ("compass_declination_deg", "Declination degrees", "general", "number", None, "deg"),
        ("time_format",          "Time format",           "general",  "radio",
         [[0,"24h"],[1,"12h"]], None),
        ("date_format",          "Date format",           "general",  "dropdown",
         [[0,"dd.mm.yy"],[1,"mm/dd/yy"],[2,"yy-mm-dd"]], None),
        # alarm_enable / alarm hour+minute are NOT listed. André: "I see alarm on watch
        # settings...never seen on suunto link..I believe it is only on the watch no?" -
        # correct. SuuntoLink has no clock-alarm control at all: its only "alarm" strings are
        # getSunriseAlarmTime / createSunAlertInput (sunrise-sunset alerts) and
        # TXT_STORM_ALARM. And across both captures it never wrote bytes 26-28. So the alarm
        # is set on the watch itself; offering a bare On/Off here - with no time, and with a
        # write nothing has ever demonstrated - would be inventing a control.
        # Same for dual_time (@31-32), never written either.
        # --- Units ---------------------------------------------------------------------
        ("units_mode",           "Units",                 "units",    "radio",
         [[0,"Metric"],[1,"Imperial"],[2,"Advanced"]], None),
        # --- Personal ------------------------------------------------------------------
        ("weight_kg",            "Weight",                "personal", "number", None, "kg"),
        ("length_cm",            "Height",                "personal", "number", None, "cm"),
        ("birthyear",            "Birth year",            "personal", "number", None, None),
        ("is_male",              "Gender",                "personal", "radio",
         [[0,"Female"],[1,"Male"]], None),
        ("max_hr",               "Max HR",                "personal", "number", None, "bpm"),
        ("rest_hr",              "Rest HR",               "personal", "number", None, "bpm"),
        ("fitness_level",        "Activity level",        "personal", "number", None, None),
        # Bike POD calibration. The wire stores a FACTOR x10000; SuuntoLink shows the wheel
        # circumference in mm and prints the factor underneath (André's own screenshot:
        # "2050 mm" with "1.000" below it). 2050 never appears in any settings write, so the
        # mm figure is derived: mm = factor * 2050. His min/max probes land exactly on the
        # ends of SuuntoLink's own input range - factor 0.02 -> 41 mm, factor 2.0 -> 4100 mm.
        # Shown in mm here for the same reason SuuntoLink does: a circumference is meaningful,
        # a bare 1.0 is not.
        ("bikepod_calibration",  "Bike POD 1 calibration","general",  "number", None, "mm"),
        ("bikepod_calibration2", "Bike POD 2 calibration","general",  "number", None, "mm"),
    ]
    # The per-unit choices only apply when units_mode is Advanced - same rule as the Ambit3.
    LEGACY_UNIT_SPECS = [
        ("distance",     "Distance",      [[0,"km"],[1,"mi"]]),
        ("altitude",     "Altitude",      [[0,"m"],[1,"ft"]]),
        ("height",       "Height",        [[0,"cm"],[1,"ft"]]),
        ("weight",       "Weight",        [[0,"kg"],[1,"lbs"]]),
        ("temperature",  "Temperature",   [[0,"C"],[1,"F"]]),
        ("pressure",     "Air pressure",  [[0,"hPa"],[1,"inHg"]]),
        ("speed",        "Speed",         [[0,"km/h"],[1,"mph"]]),
        ("verticalspeed","Vertical speed",[[0,"m/s"],[1,"ft/min"]]),
        ("heartrate",    "Heart rate",    [[0,"bpm"],[1,"%"]]),
        ("compass",      "Compass",       [[0,"degrees"],[1,"mils"]]),
    ]

    # Fields safe to offer as editable: their wire meaning is confirmed and the UI value maps
    # 1:1 onto the stored byte. weight_kg is deliberately absent - the UI shows kg while the
    # wire field is a scaled u16.
    LEGACY_WRITABLE = {
        "language", "backlight_mode", "backlight_brightness", "display_brightness",
        "display_is_negative", "tones_mode", "timemode_button_lock", "sportmode_button_lock",
        "sync_time_w_gps", "gps_position_format", "alti_baro_mode",
        "time_format", "date_format", "units_mode",
        "birthyear", "max_hr", "rest_hr", "fitness_level", "is_male", "length_cm",
        "compass_declination_dir", "compass_declination_deg", "navigation_style",
        # Scaled fields - editable now that the conversion is known and confirmed against the
        # capture (weight x100: a real write of 25000 read back as 250.0 kg; calibration
        # x10000: 10000/200/20000 in the capture are the factors 1.0 / 0.02 / 2.0).
        "weight_kg", "bikepod_calibration", "bikepod_calibration2",
    }
    # UI key -> the CLI's own field name where they differ.
    LEGACY_WRITE_KEY = {"length_cm": "length", "weight_kg": "weight"}
    # UI value -> wire value. The watch stores these scaled; the UI edits the real quantity.
    LEGACY_WRITE_SCALE = {"weight_kg": 100}
    # Wheel circumference that corresponds to a calibration factor of exactly 1.000, taken
    # from SuuntoLink's own display. Used only to convert between the mm the user edits and
    # the factor the watch stores.
    BIKEPOD_REFERENCE_MM = 2050

    def _handle_settings_write_ambit1(self, body):
        """The Ambit1 half of POST /api/settings - one field per call, read-modify-write."""
        fields = (body or {}).get("fields") or []
        confirm = bool((body or {}).get("confirm", False))
        results = []
        sys.path.insert(0, str(TOOLS_DIR))
        import legacy_link                                      # noqa: PLC0415
        env_pid = hex(SELECTED_PRODUCT_ID) if SELECTED_PRODUCT_ID is not None else None
        old_env = os.environ.get("AMBIT_PRODUCT_ID")
        if env_pid:
            os.environ["AMBIT_PRODUCT_ID"] = env_pid
        try:
            for f in fields:
                key = f.get("field")
                if key and key.startswith("unit_"):
                    cli_key = "units." + key[len("unit_"):]
                elif key in self.LEGACY_WRITABLE:
                    cli_key = self.LEGACY_WRITE_KEY.get(key, key)
                else:
                    results.append({"field": key, "ok": False,
                                     "error": "not writable on this watch"})
                    continue
                try:
                    value = f.get("value")
                    scale = self.LEGACY_WRITE_SCALE.get(key)
                    if scale:
                        value = int(round(float(value) * scale))
                    elif key.startswith("bikepod_calibration"):
                        # mm back to the stored factor x10000
                        value = int(round(float(value) / self.BIKEPOD_REFERENCE_MM * 10000))
                    with WATCH_LOCK:
                        r = legacy_link.settings_write(cli_key, value,
                                                        dry_run=not confirm)
                    r["field"] = key
                    results.append(r)
                except (RuntimeError, ValueError, TypeError) as exc:
                    results.append({"field": key, "ok": False, "error": str(exc)})
        finally:
            if env_pid:
                if old_env is None:
                    os.environ.pop("AMBIT_PRODUCT_ID", None)
                else:
                    os.environ["AMBIT_PRODUCT_ID"] = old_env
        ok = all(r.get("ok") for r in results) and bool(results)
        self._send_json(200 if ok else 502,
                        {"ok": ok, "wrote": confirm and ok, "fields": results})

    def _handle_settings_read_ambit1(self):
        """The Ambit1 half of GET /api/settings - its real settings in the Ambit3's schema.

        Writable as of 2026-08-23: the settings WRITE format was solved from André's own USB
        capture (command 0x0b01 carrying the same 132-byte struct the read returns - see
        ambit_legacy_cli.c's cmd_settings_write). Only the fields whose meaning is confirmed
        are marked writable. Scaled fields (weight x100, bike POD calibration x10000) are
        converted by LEGACY_WRITE_SCALE on the way out, so the UI edits the real quantity."""
        sys.path.insert(0, str(TOOLS_DIR))
        import legacy_link                                      # noqa: PLC0415
        env_pid = hex(SELECTED_PRODUCT_ID) if SELECTED_PRODUCT_ID is not None else None
        old_env = os.environ.get("AMBIT_PRODUCT_ID")
        if env_pid:
            os.environ["AMBIT_PRODUCT_ID"] = env_pid
        try:
            with WATCH_LOCK:
                raw = legacy_link.settings()
        except RuntimeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        finally:
            if env_pid:
                if old_env is None:
                    os.environ.pop("AMBIT_PRODUCT_ID", None)
                else:
                    os.environ["AMBIT_PRODUCT_ID"] = old_env
        if not raw.get("ok"):
            self._send_json(502, raw)
            return

        # Calibration factor -> the circumference SuuntoLink shows.
        for ck in ("bikepod_calibration", "bikepod_calibration2"):
            if ck in raw:
                raw[ck] = round(float(raw[ck]) * self.BIKEPOD_REFERENCE_MM)

        # Split the declination u16 back into the two bytes the watch really stores.
        if "compass_declination" in raw:
            v = int(raw["compass_declination"] or 0)
            raw["compass_declination_dir"] = v & 0xff
            raw["compass_declination_deg"] = (v >> 8) & 0xff

        out = {}
        for key, label, screen, control, choices, unit in self.LEGACY_SETTING_SPECS:
            if key not in raw:
                continue
            entry = {"ok": True, "value": raw[key], "path": key,
                     "writable": key in self.LEGACY_WRITABLE, "screen": screen,
                     "label": label, "control": control}
            if choices:
                entry["choices"] = choices
            if unit:
                entry["unit"] = unit
            if key == "weight_kg":
                entry["decimals"] = 2
            if key.startswith("bikepod_calibration"):
                entry["decimals"] = 0
            # Declination degrees only mean anything once a direction is chosen - André:
            # "if compass declination if off, no declination degrees, if west or east then
            # this menu can appear down it". Same dependency SuuntoLink's own control has.
            if key == "compass_declination_deg":
                entry["showWhen"] = {"field": "compass_declination_dir", "notValue": 0}
            out[key] = entry

        units = raw.get("units") or {}
        for key, label, choices in self.LEGACY_UNIT_SPECS:
            if key not in units:
                continue
            out[f"unit_{key}"] = {
                "ok": True, "value": units[key], "path": f"units.{key}", "writable": True,
                "screen": "units", "label": label, "control": "radio", "choices": choices}

        self._send_json(200, {"ok": True, "variant": "Bluebird", "settings": out})

    def _handle_settings_read(self):
        """GET /api/settings[?device=kailash] - Ambit3/Traverse by default; pass
        ?device=kailash for Kailash's own smaller, separately-curated table (added
        2026-08-08, same day as the Ambit3 one - see
        tools/settings_write.py's own docstring for both tables' real sources: SuuntoLink's
        screenshots for the Ambit3, the real 7R iOS app's own screenshots for Kailash).
        Real, read-only 0x1100 DeviceSettings query - see custom_modes_andre.md for how
        entry IDs were confirmed to only ever be looked up fresh per-device, after a real
        bug where a hardcoded ID from one watch's schema silently hit a different field on
        another. Every value in both tables live-verified against real screenshots."""
        if demo_ambit():
            self._send_json(200, demo_json("settings.json"))
            return
        if ble_bridge.bridge.status().get("handshake_done"):
            self._handle_settings_read_ble()
            return
        # Ambit1: same endpoint and same schema, different reader - settings_write.py speaks
        # SBEM/0x1100, which this family predates.
        if selected_is_legacy():
            self._handle_settings_read_ambit1()
            return
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        device = query.get("device", [None])[0]
        args = ["--json"]
        if device:
            args += ["--device", device]
        code, out, err = run_tool("settings_write.py", args)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "settings_write.py --json produced "
                                   "no parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_settings_read_ble(self):
        """The BLE path for GET /api/settings - tools/ble_settings.py. Real 0x1100 read,
        never simulated (matches settings_write.py's own CLI, which reads for real even in
        its dry-run mode - only the write step there is optional). Device family comes from
        the handshake's own model string (ble_settings.product_id_from_model()), not a
        `?device=` query param - there's no USB descriptor to override it with, and the
        watch already told us what it is."""
        sys.path.insert(0, str(TOOLS_DIR))
        import ble_settings                                  # noqa: PLC0415
        status = ble_bridge.bridge.status()
        model = status.get("device_info", {}).get("model")
        try:
            ble_bridge.bridge.set_dry_run(False)
            info = ble_settings.read_settings(ble_bridge.bridge, model)
        except ble_bridge.BleBridgeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        except (RuntimeError, TimeoutError) as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        self._send_json(200 if info.get("ok") else 502, {"transport": "ble", **info})

    def _handle_intervals_activity_level(self, body):
        """POST /api/intervals/activity-level. Body: {athlete_id, api_key, confirm, device?}.
        Recompute the Suunto activity class from the athlete's last 4 weeks of intervals.icu
        training and write it to Personal.ActivityLevel. The app calls this on every sync (USB
        or BLE) so the class stays current with real training load (André, 2026-08-18); only
        the activity class is refreshed here - weight/height/HR are static. Reuses the shared
        settings-write path, so it works over both transports; without confirm:true it's a
        dry-run (computes + shows current vs new, writes nothing)."""
        athlete_id = body.get("athlete_id")
        api_key = body.get("api_key")
        if not athlete_id or not api_key:
            self._send_json(400, {"ok": False, "error": "missing athlete_id/api_key"})
            return
        if demo_ambit():
            self._send_json(200, {"ok": True, "activity_class": 6.0, "wrote": False, "demo": True})
            return
        confirm = bool(body.get("confirm", False))
        if not ble_bridge.bridge.status().get("handshake_done"):
            # USB: stats_to_watch computes the class AND only writes when it actually differs
            # from the watch (idempotent - safe to fire on every connect).
            args = [str(athlete_id), str(api_key), "--only", "activity_level", "--json"]
            if body.get("device"):
                args += ["--device", body["device"]]
            if confirm:
                args.append("--write")
            code, out, err = run_tool("stats_to_watch.py", args)
            info = self._parse_last_json_line(out)
            if info is None:
                self._send_json(502, {"ok": False, "error": ("stats_to_watch produced no JSON: "
                                       + (err or out or "")).strip()[:200]})
                return
            self._send_json(200 if info.get("ok") else 502, info)
            return
        # BLE: compute the class (network only), then write it through the BLE settings path.
        code, out, err = run_tool("intervals_stats.py",
                                  [str(athlete_id), str(api_key), "--activity-class"])
        lines = (out or "").strip().splitlines()
        try:
            cls_val = float(lines[-1]) if lines else None
        except ValueError:
            cls_val = None
        if cls_val is None:
            self._send_json(502, {"ok": False, "error": ("could not compute activity class: "
                                   + (err or out or "no output")).strip()[:200]})
            return
        if confirm:
            self._handle_settings_write_ble("activity_level", cls_val, True)
        else:
            self._send_json(200, {"ok": True, "activity_class": cls_val, "wrote": False,
                                  "dry_run": True, "transport": "ble"})

    def _handle_intervals_stats_to_watch(self, body):
        """POST /api/intervals/stats-to-watch. Body: {athlete_id, api_key, confirm, device?}.
        Write the FULL personal profile (weight, height, gender, max/rest HR, and the derived
        activity class) from intervals.icu to the watch - the "Import my stats -> watch" sync
        toggle (André, 2026-08-18). USB only for now: it reuses stats_to_watch.py's proven
        Personal.* write path (idempotent, dry-run without confirm). BLE writes only the
        activity class today (see /api/intervals/activity-level); the static fields need the
        per-field BLE settings path wired before this can run over Bluetooth, so we say so
        rather than half-writing."""
        athlete_id = body.get("athlete_id")
        api_key = body.get("api_key")
        if not athlete_id or not api_key:
            self._send_json(400, {"ok": False, "error": "missing athlete_id/api_key"})
            return
        if demo_ambit():
            self._send_json(200, {"ok": True, "wrote": False, "demo": True})
            return
        if ble_bridge.bridge.status().get("handshake_done"):
            self._send_json(501, {"ok": False, "error": "Writing the full profile over Bluetooth "
                                  "isn't supported yet - connect the watch by cable for this. "
                                  "(Activity level does sync over Bluetooth.)"})
            return
        confirm = bool(body.get("confirm", False))
        # No --only: stats_to_watch considers every mapped field and writes just the ones that
        # differ from the watch. --json gives the app the machine-readable preview/result.
        args = [str(athlete_id), str(api_key), "--json"]
        if body.get("device"):
            args += ["--device", body["device"]]
        if confirm:
            args.append("--write")
        code, out, err = run_tool("stats_to_watch.py", args)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": ("stats_to_watch produced no JSON: "
                                   + (err or out or "")).strip()[:200]})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_intervals_workouts(self, body):
        """POST /api/intervals/workouts. Body: {athlete_id, api_key, start, end, mode}. Pull the
        athlete's PLANNED workouts from intervals.icu in [start, end] and return them as dated plan
        entries [{date, mode, workout}] for the Training Program calendar. Reuses
        intervals_workout.py --from-intervals (the same tool a terminal user runs): it reconstructs
        each workout's HR bands from the athlete's own zones and, when the watch is on the cable,
        resolves them to the watch's max/rest (Karvonen) - otherwise it returns the intervals.icu
        bands. Read-only against the watch; nothing is written here (install is the calendar's own
        Install step)."""
        athlete_id = body.get("athlete_id")
        api_key = body.get("api_key")
        start = body.get("start")
        end = body.get("end")
        mode = body.get("mode")
        missing = [k for k, v in (("athlete_id", athlete_id), ("api_key", api_key),
                                  ("start", start), ("end", end), ("mode", mode)) if not v]
        if missing:
            self._send_json(400, {"ok": False, "error": "missing: " + ", ".join(missing)})
            return
        args = ["--from-intervals", "--json",
                "--athlete-id", str(athlete_id), "--api-key", str(api_key),
                "--start", str(start), "--end", str(end), "--mode", str(mode)]
        code, out, err = run_tool("intervals_workout.py", args)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": ("intervals_workout produced no JSON: "
                                   + (err or out or "")).strip()[:200]})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_intervals_upload(self, body):
        """POST /api/intervals/upload. Body: {athlete_id, api_key, name?, fit_base64? | gpx?}.
        Push one activity file to intervals.icu via tools/intervals_upload.py (reuses the same
        HTTP-Basic API-key auth as every other intervals call here). Watch moves send fit_base64
        (the FIT carries the logged Suunto App streams that intervals gets from nowhere else);
        eTrex moves are GPX-only, so they send gpx. The desktop's export-scope selector decides
        which activities this is called for - the backend just uploads whatever it's handed."""
        athlete_id = body.get("athlete_id")
        api_key = body.get("api_key")
        if not athlete_id or not api_key:
            self._send_json(400, {"ok": False, "error": "missing athlete_id/api_key"})
            return
        fit_b64 = body.get("fit_base64")
        gpx = body.get("gpx")
        if not fit_b64 and not gpx:
            self._send_json(400, {"ok": False, "error": "no fit_base64 or gpx to upload"})
            return
        suffix = ".fit" if fit_b64 else ".gpx"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(base64.b64decode(fit_b64) if fit_b64 else gpx.encode("utf-8"))
            path = f.name
        try:
            args = [str(athlete_id), str(api_key), path, "--json"]
            if body.get("name"):
                args += ["--name", str(body["name"])]
            code, out, err = run_tool("intervals_upload.py", args)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        info = self._parse_last_json_line(out)
        if info is None:
            # intervals_upload prints a human line on success even without a JSON body; treat a
            # clean exit as success, a non-zero exit (e.g. a real duplicate/401) as the error.
            if code == 0:
                self._send_json(200, {"ok": True, "raw_output": out})
            else:
                self._send_json(502, {"ok": False, "error": (err or out or "upload failed")
                                      .strip()[:300]})
            return
        self._send_json(200 if info.get("ok", True) else 502, info)

    # Garmin Connect body-composition (Garmin Index scale) for the Weight page's "Garmin"
    # source. tools/garmin_weight.py owns the OAuth (garminconnect/garth) + the fetch; here we
    # just shell out. The token store lives under AmbitAppBackups so a login persists.
    GARMIN_TOKENS = str(BACKUP_DIR / "garmin_tokens")

    def _handle_garmin_weight(self):
        """GET /api/garmin/weight?days=N - body composition from Garmin Connect (cached login)."""
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        days = query.get("days", ["365"])[0]
        code, out, err = run_tool("garmin_weight.py",
                                  ["--days", days, "--tokens", self.GARMIN_TOKENS, "--json"],
                                  timeout=120)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": (err or out or
                                  "garmin_weight produced no JSON").strip()[:300]})
            return
        self._send_json(200 if info.get("ok") else 200, info)  # needLogin is a 200 with ok:false

    def _handle_garmin_sync(self, what):
        """GET /api/garmin/{activities,health}?days=N via tools/garmin_sync.py (cached login)."""
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        days = query.get("days", ["30"])[0]
        code, out, err = run_tool("garmin_sync.py",
                                  [f"--{what}", "--days", days,
                                   "--tokens", self.GARMIN_TOKENS, "--json"], timeout=180)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": (err or out or
                                  "garmin_sync produced no JSON").strip()[:300]})
            return
        self._send_json(200, info)  # ok:false + needLogin is still a normal 200

    def _handle_garmin_upload(self, body):
        """POST /api/garmin/upload. Body: {fit_base64? | gpx?, name?}. Upload one activity to
        Garmin Connect (garmin_sync.py --upload). Garmin dedups by start time, so a re-upload is
        reported as a duplicate rather than an error."""
        fit_b64 = body.get("fit_base64")
        gpx = body.get("gpx")
        if not fit_b64 and not gpx:
            self._send_json(400, {"ok": False, "error": "no fit_base64 or gpx to upload"})
            return
        suffix = ".fit" if fit_b64 else ".gpx"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(base64.b64decode(fit_b64) if fit_b64 else gpx.encode("utf-8"))
            path = f.name
        try:
            code, out, err = run_tool("garmin_sync.py",
                                      ["--upload", path, "--tokens", self.GARMIN_TOKENS,
                                       "--json"], timeout=120)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": (err or out or
                                  "garmin upload produced no JSON").strip()[:300]})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_garmin_weight_login(self, body):
        """POST /api/garmin/weight/login. Body: {email, password, mfa?}. One-time Garmin login;
        the password is used once, only the OAuth token store is kept."""
        email = body.get("email")
        password = body.get("password")
        if not email or not password:
            self._send_json(400, {"ok": False, "error": "missing email/password"})
            return
        args = ["--login", str(email), str(password), "--tokens", self.GARMIN_TOKENS, "--json"]
        if body.get("mfa"):
            args += ["--mfa", str(body["mfa"])]
        code, out, err = run_tool("garmin_weight.py", args, timeout=120)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": (err or out or
                                  "garmin login produced no JSON").strip()[:300]})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_settings_write(self, body):
        # Ambit1: its own 0x0b01 read-modify-write, not the SBEM 0x1101 path.
        if selected_is_legacy():
            self._handle_settings_write_ambit1(body)
            return

        """POST /api/settings. Body: {"key": str, "value": number, "confirm": bool,
        "device": str (optional, e.g. "kailash")}. Same rehearsal-first pattern as every
        other write in this backend: without confirm:true, runs settings_write.py's own
        --set dry-run (shows current value and what would be written, sends nothing); with
        confirm:true, adds --write for a real 0x1101 send, confirmed by settings_write.py's
        own re-read (its own `ok` is only true if the watch's re-read actually reflects the
        new value - see that file's own write_one())."""
        key = body.get("key")
        value = body.get("value")
        if key is None or value is None:
            self._send_json(400, {"error": "missing \"key\" or \"value\""})
            return
        confirm = bool(body.get("confirm", False))
        if ble_bridge.bridge.status().get("handshake_done"):
            self._handle_settings_write_ble(key, value, confirm)
            return
        device = body.get("device")
        args = ["--set", f"{key}={value}", "--json"]
        if device:
            args += ["--device", device]
        if confirm:
            args.append("--write")
        code, out, err = run_tool("settings_write.py", args)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "settings_write.py --json produced "
                                   "no parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_settings_write_ble(self, key, value, confirm):
        """The BLE path for POST /api/settings - tools/ble_settings.py. Without confirm,
        shows the current value only (a real 0x1100 read - settings_write.py's own dry-run
        never simulates that part either). With confirm, a real 0x1101 (or Kailash's
        single-entry push) write via write_one(), unverified over BLE until tested against
        real hardware the same careful way route writes need to be (see ble_settings.py's
        own docstring)."""
        sys.path.insert(0, str(TOOLS_DIR))
        import ble_settings                                  # noqa: PLC0415
        status = ble_bridge.bridge.status()
        model = status.get("device_info", {}).get("model")
        try:
            ble_bridge.bridge.set_dry_run(False)
            if not confirm:
                current = ble_settings.read_settings(ble_bridge.bridge, model)
                info = {"ok": True, "dry_run": True, "key": key,
                        "current": current.get("settings", {}).get(key),
                        "would_write": value}
            else:
                info = ble_settings.write_setting(ble_bridge.bridge, key, value, model)
        except ble_bridge.BleBridgeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        except (RuntimeError, TimeoutError) as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        self._send_json(200 if info.get("ok") else 502, {"transport": "ble", **info})

    def _handle_time_sync(self, body):
        """POST /api/time/sync. Body: {} (this device's own local time) or
        {"timezone": "Area/City"} (a real IANA name, from /api/time/zones - "from a
        different timezone", real 2026-08-10 request). No rehearsal step here unlike every
        other write in this backend - set_time.py's own docstring explains why (two plain,
        always-safe clock-set commands, no flash/PMEM involved) - this always sends --write
        for real."""
        args = ["--write", "--json"]
        tz = body.get("timezone")
        if tz:
            args += ["--timezone", tz]
        code, out, err = run_tool("set_time.py", args)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "set_time.py --json produced "
                                   "no parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_time_zones(self):
        """GET /api/time/zones - the real IANA tz database names, from Python's own bundled
        zoneinfo (no network fetch, no separate data file - see set_time.py's own docstring
        on why this is genuinely offline already, not a scoped-down version of an online
        list)."""
        self._send_json(200, {"ok": True, "zones": sorted(available_timezones())})

    def _handle_customodes_read(self):
        """GET /api/customodes - Ambit3's real sport modes (CustomModes flash region),
        added 2026-08-08 alongside the first hardware-confirmed CustomModes content edits
        (see custom_modes_andre.md). Real, read-only 0x0b17 flash read, decoded through
        tools/custom_modes.py's own to_json() - field names there already match what the
        write endpoints below expect (SETTING_FIELDS' own names, FIELD_TYPES' own names),
        so this needs no separate name-mapping layer."""
        # Ambit1: same endpoint, same JSON shape, different decoder. André, 2026-08-23:
        # "all watches should look like ambit 3, but for sure with adapted features" - so the
        # Ambit1 feeds the SAME list/detail page instead of getting a bespoke screen of its
        # own. Its region is a different format entirely (76-byte blob, pre-SBEM - see
        # docs/ambit1_sport_mode_format.md), so ONLY the decoder differs; every consumer
        # above this line is shared.
        if selected_is_legacy():
            self._handle_customodes_read_ambit1()
            return

        # Testing mode decodes the fixture through the SAME tool, via its own --from, so
        # this path is the real one minus the USB read.
        args = ["--json"] + (["--from", demo_custom_modes_path()] if demo_ambit() else [])
        code, out, err = run_tool("custom_modes.py", args)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "custom_modes.py --json produced "
                                   "no parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_customodes_read_ambit1(self):
        """The Ambit1 half of GET /api/customodes - its real modes, mapped onto exactly the
        shape tools/custom_modes.py's to_json() emits so CustomModesService and
        SportModesPage need no per-device branch.

        Fields the Ambit1 genuinely does not have are reported honestly rather than faked:
          displays  - decoded as of 2026-08-23, built-in system screens stripped.
          rules     - one app per mode on this device, not decoded here either.
          hrLimitsUse - the Ambit1 has NO use_heartrate_limits field at all (that is the
                      `usehrlimits` capability Devices.xml reports missing). The limits
                      themselves are real and stored; only the on/off flag has nowhere to
                      live, which is why SuuntoLink's own checkbox never sticks. Derived
                      from whether a limit is actually set."""
        sys.path.insert(0, str(TOOLS_DIR))
        import legacy_link                                      # noqa: PLC0415
        env_pid = hex(SELECTED_PRODUCT_ID) if SELECTED_PRODUCT_ID is not None else None
        old = os.environ.get("AMBIT_PRODUCT_ID")
        if env_pid:
            os.environ["AMBIT_PRODUCT_ID"] = env_pid
        try:
            with WATCH_LOCK:
                info = legacy_link.ambit1_sport_mode_read()
        except RuntimeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        finally:
            if env_pid:
                if old is None:
                    os.environ.pop("AMBIT_PRODUCT_ID", None)
                else:
                    os.environ["AMBIT_PRODUCT_ID"] = old

        if not info.get("ok"):
            self._send_json(502, info)
            return

        app_names = self._ambit1_app_names()
        modes = []
        for i, m in enumerate(info.get("modes") or []):
            hr_high, hr_low = m.get("hrMax", 0), m.get("hrMin", 0)
            modes.append({
                "name": m.get("name", ""),
                "activityId": m.get("activityId", 0),
                "appCount": 0,
                "customModeId": i,
                "useHw": m.get("pods", 0),
                "altiBaroMode": m.get("altiBaroMode", 0),
                "recordingInterval": m.get("recordingInterval", 0),
                "gpsInterval": m.get("gpsInterval", 0),
                "autolap": m.get("autolapM", 0),
                "hrHigh": hr_high,
                "hrLow": hr_low,
                "hrLimitsUse": 1 if (hr_high or hr_low) else 0,
                "autoStart": 0,
                "autoPause": m.get("autoPause", 0),
                "autoScrolling": 0,
                "intTimerFlags": m.get("useIntervalTimer", 0),
                "intTimerCount": m.get("intervalRepetitions", 0),
                "intervalTimer": {
                    "enabled": m.get("useIntervalTimer", 0) == 1,
                    # 0x0100 = time (seconds), 0x0000 = distance (metres) - confirmed both
                    # ways against SuuntoLink's own writes, see the format doc.
                    "type": "time" if m.get("intervalMaxUnit") == 0x0100 else "distance",
                    "high": m.get("intervalMax", 0),
                    "low": m.get("intervalMin", 0),
                    "repetitions": m.get("intervalRepetitions", 0),
                },
                "backlightMode": 0,
                "displayMode": 0,
                "quickNavigation": 0,
                # Real displays, decoded off the watch (ambit1_sport_mode.c). The built-in
                # system screens are already stripped there, so this count matches what the
                # Ambit3 page shows for its own watch - and stays inside the device's own
                # 8-display maximum.
                "displays": self._ambit1_label_displays(m.get("displays") or [],
                                                        app_names),
                "rules": [],
            })
        self._send_json(200, {
            "ok": True, "formatType": "ambit1",
            "exerciseModes": modes, "sportModes": []})

    # Ambit1 Apps region. Base/size come from SuuntoLink's own Devices.xml per-device record
    # (rulestorelocation / rulestoresize), NOT libambit's PMEM20_APP_START - that constant is
    # the Ambit3's 600000 and reads back nothing at all on an Ambit1.
    LEGACY_APPS_ADDR = 160000
    LEGACY_APPS_SIZE = 20000

    def _ambit1_app_names(self):
        """Names of the Suunto Apps installed on an Ambit1, in slot order.

        Why this exists: a display row that holds an app shows "Suunto App Slot 1" because on
        this device the Apps directory has NO per-entry header - unlike the Ambit3, whose
        entry block carries the name, the Ambit1's IAMRULE binary starts directly at the
        entry offset. So the name has to come out of the binary itself.

        Directory: [u16 count][u8 count^2][u32 entry_offset x (count+1)], header_len =
        3 + 4*(count+1); the last table entry is the used extent. (The Ambit3 stores that
        second field as a u16, which is why tools/apps.py's directory check rejects this
        region rather than mis-parsing it.)

        Inside a binary, a u16 table at +28 (0-terminated) lists that app's symbol-name
        offsets - the name sits at value+1 for '?'-prefixed symbols and value+2 otherwise.
        Every other printable run is a data constant, and the app's title is the first of
        them. Verified against the real region: "Storm alarm" and "Sunrise/Sunset", each
        matching a real SuuntoLink catalogue entry by name.

        Best-effort by design: any parse failure returns {} and the UI falls back to the
        generic slot label rather than showing a guess."""
        import re                                              # noqa: PLC0415
        try:
            sys.path.insert(0, str(TOOLS_DIR))
            import legacy_link                                  # noqa: PLC0415
            with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
                path = f.name
            try:
                with WATCH_LOCK:
                    info = legacy_link.run(["region-dump", hex(self.LEGACY_APPS_ADDR),
                                             path, str(self.LEGACY_APPS_SIZE)])
                if not info.get("ok"):
                    return {}
                data = Path(path).read_bytes()
            finally:
                Path(path).unlink(missing_ok=True)

            count = struct.unpack_from("<H", data, 0)[0]
            if count == 0 or count > 64:
                return {}
            table = struct.unpack_from(f"<{count + 1}I", data, 3)
            if table[0] != 3 + 4 * (count + 1):
                return {}                                        # not this format
            names = {}
            for i in range(count):
                blob = data[table[i]:table[i + 1]]
                if blob[:7] != b"IAMRULE":
                    continue
                sym, p = set(), 28
                while p + 2 <= len(blob):
                    v = struct.unpack_from("<H", blob, p)[0]
                    p += 2
                    if v == 0:
                        break
                    sym.update((v + 1, v + 2))
                consts = [(m.start(), m.group().decode("latin-1"))
                          for m in re.finditer(rb"[\x20-\x7e]{3,}", blob)
                          if m.start() not in sym and m.start() != 0]
                if consts:
                    names[i] = consts[0][1]
            return names
        except Exception:                                        # noqa: BLE001 - best effort
            return {}

    _FIELD_LABELS = None

    def _ambit1_label_displays(self, displays, app_names=None):
        """Attach a `label` to every display-row value.

        The page renders a row as `values.map(v => v.label)`, and the Ambit3 decoder already
        supplies those. The Ambit1 decoder emits only the numeric field id, so without this
        every data row rendered BLANK - caught on screen, not in the JSON, since the ids
        themselves were correct all along. Labels come from the SAME FIELD_TYPES catalog the
        field-types endpoint serves, so both devices name a field identically. Cached: it is
        a static dict, and this runs per display row."""
        if Handler._FIELD_LABELS is None:
            code, out, err = run_tool("custom_modes.py", ["--list-field-types"])
            info = self._parse_last_json_line(out) or {}
            Handler._FIELD_LABELS = {
                int(f["value"]): f.get("label", "")
                for f in (info.get("fieldTypes") or []) if "value" in f}
        labels = Handler._FIELD_LABELS
        for disp in displays:
            for field in disp.get("fields") or []:
                for v in field.get("values") or []:
                    t = v.get("type")
                    label = labels.get(t, "0x%x" % t if isinstance(t, int) else "")
                    # An app-slot row: name the app rather than saying "Suunto App Slot N".
                    if app_names and isinstance(label, str) and "Suunto App Slot" in label:
                        slot = label.rsplit(" ", 1)[-1]
                        if slot.isdigit() and (int(slot) - 1) in app_names:
                            label = app_names[int(slot) - 1]
                    v["label"] = label
        return displays

    def _handle_customodes_field_types(self):
        """GET /api/customodes/field-types - the real FIELD_TYPES catalog (95 entries),
        for a UI's own data-field picker when editing a display row's "type". No watch
        touched at all - custom_modes.py --list-field-types just dumps a static dict."""
        code, out, err = run_tool("custom_modes.py", ["--list-field-types"])
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "custom_modes.py --list-field-types "
                                   "produced no parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_customodes_row_menu(self):
        """GET /api/customodes/row-menu?activity=<id>&template=<n>&row=<TOP|CENTER|BOTTOM>

        What the row editor is allowed to offer, exactly as SuuntoLink would offer it: the
        values grouped into its own categories, in its own order, with its own labels. The
        catalogue is generated from SuuntoLink's own module (tools/gen_sportmode_rows.js ->
        assets/sportmode_rows.json), so this endpoint is a lookup, not a judgement.

        Values this project cannot write are dropped rather than shown and then failing -
        `row_bridge` maps 54 of SuuntoLink's 60 rows to a watch field id, and a row we cannot
        write is better absent from the menu than wrong in it.

        No watch is touched: it is a static file plus a lookup.
        """
        from urllib.parse import urlparse, parse_qs
        query = parse_qs(urlparse(self.path).query)

        def one(name, default=None):
            values = query.get(name)
            return values[0] if values else default

        sys.path.insert(0, str(TOOLS_DIR))
        import row_bridge                                    # noqa: PLC0415 - tools are lazy
        import custom_modes                                  # noqa: PLC0415

        catalogue = row_bridge.load_rows()
        try:
            activity = int(one("activity", "1"))
            template = int(one("template", "260"))
        except ValueError:
            self._send_json(400, {"ok": False,
                                  "error": "activity and template must be integers"})
            return
        row = (one("row", "TOP") or "TOP").upper()
        if row not in ("TOP", "CENTER", "BOTTOM"):
            self._send_json(400, {"ok": False, "error": f"unknown row {row!r}"})
            return

        per_type = catalogue["availability"].get(str(activity), {})
        index = per_type.get(row_bridge.TEMPLATE_TO_DISPLAY_TYPE.get(template, ""), {}).get(row)
        if index is None:
            self._send_json(200, {"ok": True, "categories": [], "multiValue": False,
                                  "maxValues": 1,
                                  "note": "this display type has no such row"})
            return

        categories = []
        for category_id, row_ids in catalogue["menus"][index]:
            values = []
            for rid in row_ids:
                entry = catalogue["rows"][str(rid)]
                field_id = row_bridge.row_to_field_id(entry["name"])
                if field_id is None:
                    continue                                 # not writable by us - omit it
                values.append({"fieldId": field_id, "label": entry["label"],
                               "name": entry["name"]})
            if values:
                categories.append({
                    "id": category_id,
                    "label": catalogue["categories"][str(category_id)]["label"],
                    "values": values,
                })

        multi = row_bridge.row_is_multi_value(template, row)
        self._send_json(200, {
            "ok": True,
            "categories": categories,
            "multiValue": multi,
            "maxValues": catalogue["limits"]["maxValuesPerMultiRow"] if multi else 1,
            "maxSuuntoApps": catalogue["limits"]["maxSuuntoApps"],
        })

    def _handle_geocode(self):
        """GET /api/geocode?q=... - find a place by name.

        Rate limited to Nominatim's stated one request per second, enforced here rather than
        trusted to the UI: exceeding it is how a free service gets an app blocked, and no
        amount of care in the QML could guarantee it once more than one picker exists.
        """
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        text = (query.get("q") or [""])[0].strip()
        if not text:
            self._send_json(400, {"ok": False, "error": "missing q"})
            return

        with _NOMINATIM_LOCK:
            wait = 1.0 - (time.time() - _NOMINATIM_LAST[0])
            if wait > 0:
                time.sleep(wait)
            _NOMINATIM_LAST[0] = time.time()

        url = NOMINATIM_URL + "?" + urllib.parse.urlencode({
            "q": text, "format": "jsonv2", "limit": 8,
        })
        request = urllib.request.Request(url, headers={
            "User-Agent": NOMINATIM_USER_AGENT,
            "Accept-Language": "en",
        })
        try:
            with urllib.request.urlopen(request, timeout=20) as reply:
                raw = json.loads(reply.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError, TimeoutError) as exc:
            # A search failing must never look like "no such place" - the map picker still
            # works by hand, and saying which it was is the difference between retrying and
            # giving up.
            self._send_json(502, {"ok": False, "error": f"place search failed: {exc}"})
            return

        results = [{
            "name": entry.get("display_name", ""),
            "lat": float(entry["lat"]),
            "lon": float(entry["lon"]),
        } for entry in raw if entry.get("lat") and entry.get("lon")]
        self._send_json(200, {"ok": True, "results": results})

    def _demo_state(self):
        """What Testing mode is currently pretending to be.

        garminRoot is handed to the Qt side rather than resolved there: the fixture lives
        next to this file, and the app should not have to guess where the backend keeps it.
        """
        return {
            "ok": True,
            "enabled": DEMO["enabled"],
            "variant": DEMO["variant"],
            "deviceName": self._demo_device_name(DEMO["variant"]),
            "garminRoot": str(DEMO_DIR / "garmin")
                          if (DEMO["enabled"] and DEMO["variant"] == "GarminEtrex") else "",
        }

    def _demo_device_name(self, variant):
        """The friendly name for a simulated device, from the same source the picker uses."""
        if variant == "GarminEtrex":
            return "Garmin eTrex 30"
        sys.path.insert(0, str(TOOLS_DIR))
        import row_bridge                                    # noqa: PLC0415
        row = row_bridge.load_rows().get("variants", {}).get(variant, {})
        return row.get("productName") or variant

    def _handle_demo_devices(self):
        """GET /api/demo/devices - what Testing mode can pretend to be.

        Names and capabilities both come from the generated table, so a device this project
        has never physically seen still appears with its own real limits.
        """
        sys.path.insert(0, str(TOOLS_DIR))
        import row_bridge                                    # noqa: PLC0415
        catalogue = row_bridge.load_rows()

        devices = []
        for code, row in catalogue.get("variants", {}).items():
            name = row.get("productName")
            if not name or not name.startswith(DEMO_DEVICE_PREFIXES):
                continue
            devices.append({
                "variant": code,
                "name": name,
                "maxSportModes": row.get("maxSportModes"),
                "maxDisplays": row.get("maxDisplays"),
                "maxMultisportModes": row.get("maxMultisportModes"),
                # Kailash has no CustomModes region at all, so it has no sport modes to show -
                # the app already detects that on real hardware and the demo must not pretend
                # otherwise.
                "hasSportModes": code != "Hoopoe",
                "kind": "suunto",
            })
        devices.sort(key=lambda d: d["name"])
        # The eTrex is not in SuuntoLink's table for obvious reasons - its record comes from
        # this project's own hardware notes (GARMIN_USB_IMPORT_SPEC.md, André's eTrex 30).
        # It is a mass-storage device with no sport modes at all, so none of the Suunto
        # ceilings apply to it.
        devices.append({
            "variant": "GarminEtrex",
            "name": "Garmin eTrex 30",
            "maxSportModes": 0, "maxDisplays": 0, "maxMultisportModes": 0,
            "hasSportModes": False,
            "kind": "garmin",
        })
        self._send_json(200, {"ok": True, "devices": devices})

    def _handle_demo(self, body):
        """POST /api/demo {"enabled": bool} - turn Testing mode on or off.

        Turning it OFF throws away the scratch copy of the demo region, so a later session
        starts from the shipped fixture again rather than from whatever was left behind.
        """
        enabled = bool(body.get("enabled"))
        variant = body.get("variant")
        if variant:
            DEMO["variant"] = variant
            # A different device means a different sample watch: drop the scratch region so
            # the next read starts from the fixture rather than the previous device's edits.
            if DEMO["custommodes"]:
                try:
                    os.unlink(DEMO["custommodes"])
                except OSError:
                    pass
                DEMO["custommodes"] = None
        if not enabled and DEMO["custommodes"]:
            try:
                os.unlink(DEMO["custommodes"])
            except OSError:
                pass                       # best effort - a stale scratch file harms nothing
            DEMO["custommodes"] = None
        DEMO["enabled"] = enabled
        self._send_json(200, self._demo_state())

    def _handle_customodes_capabilities(self):
        """GET /api/customodes/capabilities?variant=<codename>

        What THIS watch can do - how many sport modes, displays, apps and multisport modes it
        holds, plus SuuntoLink's own supports* flags. Generated for all 46 variants it knows
        (tools/gen_sportmode_rows.js), so a device this project has never seen still gets the
        right UI instead of the reference watch's numbers.

        Real request, 2026-08-11 (André, item 24): "make it adaptable so we don't have to
        workout this stuff device per device when a new device is added."

        Static-file lookup; no watch touched.
        """
        from urllib.parse import urlparse, parse_qs
        query = parse_qs(urlparse(self.path).query)
        # The fallback follows Testing mode when it is on, so a caller that asks without
        # naming a variant gets the simulated device's limits rather than the reference
        # watch's - the picker would otherwise look like it had done nothing.
        default = DEMO["variant"] if demo_ambit() else "Emu"
        variant = (query.get("variant") or [default])[0]

        sys.path.insert(0, str(TOOLS_DIR))
        import row_bridge                                    # noqa: PLC0415

        catalogue = row_bridge.load_rows()
        row = catalogue.get("variants", {}).get(variant)
        if row is None:
            # Unknown watch: say so rather than quietly handing back the Ambit3's numbers.
            self._send_json(200, {"ok": False, "variant": variant,
                                  "error": f"no capability record for variant {variant!r}"})
            return
        self._send_json(200, {"ok": True, "variant": variant, **row})

    def _handle_customodes_activities(self):
        """GET /api/customodes/activities

        The sports a mode can be, for the "Select activity" picker - all 84 from
        assets/activity_types.json, each flagged with whether it is one of the three that
        can hold several legs (Multisport, Triathlon, Adventure racing), plus the 2-6 leg
        bounds. All of it SuuntoLink's own, via tools/sport_mode_manage.py --activities.

        Static-file lookup; no watch touched.
        """
        code, out, err = run_tool("sport_mode_manage.py", ["--activities", "--json"])
        parsed = self._parse_last_json_line(out)
        if parsed is None:
            self._send_json(502, {"ok": False,
                                   "error": "sport_mode_manage.py --activities produced "
                                            "no JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200, parsed)

    def _sport_mode_manage(self, args, body):
        """Shared plumbing for the two endpoints below.

        Same rehearsal-first contract as every other write here: without confirm:true the
        tool is dry-run and reports what WOULD change, including the counts the UI shows.
        In Testing mode the same tool edits the demo region image through the same
        round-trip guard, so trying the app out behaves exactly like real hardware and the
        change persists for the session.
        """
        args = list(args) + ["--json"]
        if (body or {}).get("confirm"):
            args.append("--write")
        if demo_ambit():
            args += ["--from", demo_custom_modes_path(), "--variant", DEMO["variant"]]
        code, out, err = run_tool("sport_mode_manage.py", args)
        parsed = self._parse_last_json_line(out)
        if parsed is None:
            self._send_json(502, {"ok": False,
                                   "error": "sport_mode_manage.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        # A refusal (over the limit, a mode a multisport still uses, a bad leg count) is a
        # real answer the UI shows as-is, not a server fault - 200 with ok:false, so the
        # message reaches the user instead of an HTTP error page.
        self._send_json(200, parsed)

    def _handle_customodes_mode(self, body):
        """POST /api/customodes/mode - create or delete ONE single-sport mode.

        Body: {"action": "create"|"delete", "name": str, "activityId": int, "confirm": bool}

        Creating is three region writes and deleting is one; the tool handles both, along
        with the renumbering that deleting forces (SPORT_MODES legs address modes by
        position). It refuses to delete a mode a multisport combo still uses, which is what
        SuuntoLink does. See tools/sport_mode_manage.py and custom_modes_andre.md's
        2026-08-12 section for where every rule comes from.
        """
        action = (body or {}).get("action")
        name = (body or {}).get("name")
        if not name or action not in ("create", "delete"):
            self._send_json(400, {"ok": False,
                                   "error": 'need a name and action "create" or "delete"'})
            return
        if action == "delete":
            self._sport_mode_manage(["--delete", name], body)
            return
        activity = (body or {}).get("activityId")
        if not isinstance(activity, int):
            self._send_json(400, {"ok": False,
                                   "error": "creating a sport mode needs an activityId"})
            return
        self._sport_mode_manage(["--create", name, "--activity", str(activity)], body)

    def _handle_customodes_multisport(self, body):
        """POST /api/customodes/multisport - create, edit or delete a multisport combo.

        Body: {"action": "create"|"edit"|"delete", "name": str, "activityId": int,
               "legs": [str, ...], "rename": str, "confirm": bool}

        One region write in every case: a combo is purely a SPORT_MODES entry naming
        existing sport modes in order, with no mode and no displays of its own. `legs` is
        that order, by mode name, and repeats are allowed - that is how a triathlon gets
        two transitions.
        """
        action = (body or {}).get("action")
        name = (body or {}).get("name")
        legs = (body or {}).get("legs") or []
        activity = (body or {}).get("activityId")
        if not name or action not in ("create", "edit", "delete"):
            self._send_json(400, {"ok": False,
                                   "error": 'need a name and action "create", "edit" or '
                                            '"delete"'})
            return
        if action == "delete":
            self._sport_mode_manage(["--delete-multisport", name], body)
            return
        if not isinstance(legs, list) or not all(isinstance(x, str) for x in legs):
            self._send_json(400, {"ok": False, "error": "legs must be a list of mode names"})
            return
        flag = "--create-multisport" if action == "create" else "--edit-multisport"
        args = [flag, name]
        if isinstance(activity, int):
            args += ["--activity", str(activity)]
        if legs:
            args += ["--legs", ",".join(legs)]
        if action == "edit" and (body or {}).get("rename"):
            args += ["--rename", body["rename"]]
        self._sport_mode_manage(args, body)

    def _handle_customodes_rename(self, body):
        """POST /api/customodes/rename. Body: {"from": str, "to": str, "confirm": bool}.
        Same rehearsal-first pattern as every other write here - without confirm:true, a
        dry-run (real offsets found, nothing sent); with it, a real write confirmed by
        tools/custom_modes_rename_test.py's own re-read. Real, hardware-confirmed
        2026-08-08: renames both the mode's own name and its multisport-slot name in one
        write - see that tool's own docstring for why both are needed."""
        from_name = body.get("from")
        to_name = body.get("to")
        if not from_name or not to_name:
            self._send_json(400, {"error": "missing \"from\" or \"to\""})
            return
        confirm = bool(body.get("confirm", False))
        args = ["--from", from_name, "--to", to_name, "--json"]
        if confirm:
            args.append("--write")
        code, out, err = run_tool("custom_modes_rename_test.py", args)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "custom_modes_rename_test.py --json "
                                   "produced no parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_customodes_field(self, body):
        """POST /api/customodes/field. Body: {"mode": str, "fields": {name: value, ...},
        "confirm": bool}. Writes any of a mode's own flat SETTING_FIELDS values (Autolap,
        HrHigh/HrLow/HrLimitsUse, UseHw, etc. - see tools/custom_modes.py's own
        SETTING_FIELDS list for the complete real set), one or many at once via
        tools/custom_modes_field_write_test.py's own --set (repeatable). Same rehearsal-
        first pattern as every other write here."""
        mode = body.get("mode")
        fields = body.get("fields")
        if not mode or not fields:
            self._send_json(400, {"error": "missing \"mode\" or \"fields\""})
            return
        confirm = bool(body.get("confirm", False))
        args = ["--mode", mode]
        for name, value in fields.items():
            args += ["--set", f"{name}={value}"]
        args.append("--json")
        if confirm:
            args.append("--write")
        code, out, err = run_tool("custom_modes_field_write_test.py", args)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "custom_modes_field_write_test.py "
                                   "--json produced no parseable JSON", "raw_output": out,
                                   "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_customodes_interval_timer(self, body):
        """POST /api/customodes/interval-timer. Body: {"mode": str, "enabled": bool,
        "type": "time"|"distance", "high": int, "low": int, "repetitions": int,
        "confirm": bool}. Sets a mode's on-watch Interval Timer via tools/interval_timer.py -
        high/low are raw stored units (seconds for time, meters for distance), the same as the
        writer and the decoder expose. Same rehearsal-first (--json without --write) pattern as
        every other write here; only writes when confirm is true."""
        mode = body.get("mode")
        if not mode or "enabled" not in body:
            self._send_json(400, {"error": "missing \"mode\" or \"enabled\""})
            return
        args = ["--mode", mode]
        args.append("--enable" if body.get("enabled") else "--disable")
        if body.get("enabled"):
            args += ["--type", str(body.get("type", "time")),
                     "--high", str(int(body.get("high", 0))),
                     "--low", str(int(body.get("low", 0)))]
        if body.get("repetitions") is not None:
            args += ["--reps", str(int(body.get("repetitions")))]
        args.append("--json")
        if bool(body.get("confirm", False)):
            args.append("--write")
        code, out, err = run_tool("interval_timer.py", args)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "interval_timer.py --json produced no "
                                   "parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_customodes_display_field(self, body):
        """POST /api/customodes/display-field. Body: {"mode": str, "display": int,
        "field": int, "index": str (optional), "type": str (optional), "confirm": bool} -
        at least one of "index"/"type" required. Real, hardware-confirmed finding,
        2026-08-08 (custom_modes_andre.md): for the common Index=FT_TIME case seen on every
        real display, "type" - not "index" - is what actually selects the rendered content
        (e.g. type: "FT_HEART_RATE_CURR"). Both are exposed since the underlying tool
        supports either, but a UI built on this should default to changing "type" unless
        it has independent evidence a given display slot works differently."""
        mode = body.get("mode")
        display = body.get("display")
        field = body.get("field")
        index = body.get("index")
        new_type = body.get("type")
        if mode is None or display is None or field is None:
            self._send_json(400, {"error": "missing \"mode\", \"display\", or \"field\""})
            return
        if index is None and new_type is None:
            self._send_json(400, {"error": "give \"index\" and/or \"type\""})
            return
        confirm = bool(body.get("confirm", False))
        args = ["--mode", mode, "--display", str(display), "--field", str(field)]
        if index is not None:
            args += ["--to", str(index)]
        if new_type is not None:
            args += ["--type", str(new_type)]
        args.append("--json")
        if confirm:
            args.append("--write")
        code, out, err = run_tool("custom_modes_display_field_write_test.py", args)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "custom_modes_display_field_write_test.py "
                                   "--json produced no parseable JSON", "raw_output": out,
                                   "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_apps_read(self):
        """GET /api/apps - real apps currently installed on the watch (the Apps flash
        region's own directory, tools/apps.py's own already-reverse-engineered format -
        see that module's docstring). ruleIdx in the response is confirmed to be exactly
        the RuleIdx a sport mode's own RULE record references, and an app is shown by
        appending that rule's engine slot (51/52/53) as a display-field SHORTCUT the row
        cycles to (training_program_andre.md Finding 44, hardware-confirmed) - so a UI can
        label that cycling value with the real app name by matching this ruleIdx. Real,
        read-only 0x0b17 flash read, using apps.read_apps_region()'s own fast path (probes
        the region's real directory instead of a blind 200,000-byte read - see that
        function's own docstring)."""
        code, out, err = run_tool("apps.py", ["--json"], timeout=60)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "apps.py --json produced no "
                                   "parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_apps_logging_read(self):
        """GET /api/apps/logging - the per-app "log this app's output into recorded Moves"
        state (the EXERCISE_MODES_RULE.LogRule flag) for every Suunto App on every sport mode.
        Read-only 0x0b17 CustomModes read via tools/app_logging.py --json; app names come from
        the Apps region in the same call. Each entry: {mode, mode_name, slot, rule_idx, app,
        use_rule, log_rule}. use_rule=1 means the app is actually activated on that mode (the
        rows a user cares about); the UI shows those with a logging toggle."""
        code, out, err = run_tool("app_logging.py", ["--json"], timeout=60)
        rules = self._parse_last_json_line(out)
        if rules is None:
            self._send_json(502, {"ok": False, "error": "app_logging.py --json produced no "
                                   "parseable JSON (is the watch connected?)",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200, {"ok": True, "rules": rules})

    def _handle_apps_logging_write(self, body):
        """POST /api/apps/logging  Body: {"mode": int, "slot": int, "on": bool}. Flips ONE
        app's LogRule on the watch via tools/app_logging.py --write. That tool's own safety
        gate refuses the write unless it's exactly one single-byte LogRule change and nothing
        else moved, so a garbled encode never reaches flash. Success/refusal is read from the
        exit code (0 = written or already in the wanted state; nonzero = the tool aborted),
        not from JSON - the write path prints a human summary, and the UI re-reads state via
        GET afterwards. This is the desktop equivalent of the CLI toggle André asked for."""
        mode = body.get("mode")
        slot = body.get("slot")
        if mode is None or slot is None or "on" not in body:
            self._send_json(400, {"ok": False,
                                   "error": "missing \"mode\", \"slot\", or \"on\""})
            return
        args = ["--mode", str(int(mode)), "--slot", str(int(slot)),
                "--log", "on" if bool(body.get("on")) else "off", "--write"]
        code, out, err = run_tool("app_logging.py", args)
        if code != 0:
            self._send_json(502, {"ok": False, "error": "app_logging.py refused or failed the "
                                   "write", "raw_output": out, "stderr": err})
            return
        self._send_json(200, {"ok": True, "mode": int(mode), "slot": int(slot),
                              "on": bool(body.get("on"))})

    def _handle_apps_catalog_status(self):
        """GET /api/apps/catalog_status - whether a Suunto Apps catalog is present and how many
        entries it has, so the UI can show the "Import from SuuntoLink" prompt vs. the browser.
        No watch access, no --write - a local file check."""
        try:
            exists = (CATALOG_DIR / "catalog.json").exists() and (CATALOG_DIR / "catalog.bin").exists()
            count = len(load_catalog_entries()) if exists else 0
        except OSError:
            exists, count = False, 0
        self._send_json(200, {"ok": True, "hasCatalog": exists, "count": count})

    def _handle_apps_import(self, body):
        """POST /api/apps/import  Body: {"path": "<the user's SuuntoLink suunto-apps/index.json>"}.
        Extracts that real, user-supplied index.json into this app's compact catalog.json +
        catalog.bin (extract_apps_catalog.extract), exactly the split the bundled copy uses.
        This is how the catalog is meant to be supplied everywhere now (André, 2026-08-14): the
        App Zone catalog is Suunto's proprietary content and the service is dead, so nothing is
        shipped/hosted - each user imports their own licensed copy. Local file surgery only, no
        watch access."""
        path = body.get("path")
        if not path:
            self._send_json(400, {"error": "missing \"path\" to the SuuntoLink index.json"})
            return
        src = Path(path)
        if not src.exists():
            self._send_json(404, {"ok": False, "error": f"file not found: {path}"})
            return
        try:
            import extract_apps_catalog
            _, _, count, _ = extract_apps_catalog.extract(src, CATALOG_DIR)
        except Exception as exc:  # noqa: BLE001 - report, never mask
            self._send_json(502, {"ok": False, "error": f"import failed: {exc}"})
            return
        # Drop the metadata cache so the next search reads the freshly imported catalog.
        global _catalog_entries
        with _CATALOG_LOCK:
            _catalog_entries = None
        self._send_json(200, {"ok": True, "count": count})

    def _handle_apps_catalog(self):
        """GET /api/apps/catalog?q=&variant=&category=&limit= - real, 2026-08-09 ("2
        bigger. Let's ship the full catalog"). Metadata-only search over this app's own
        real, bundled copy of SuuntoLink's Suunto Apps catalog (data/suunto_apps/ -
        extract_apps_catalog.py's own doing, a real SuuntoLink installation asset,
        13,104 real apps). No watch access at all - a local file search, doesn't touch
        WATCH_LOCK. `variant` is a real device codename (Emu, Hoopoe is never present -
        Kailash has no Suunto App concept at all - see compatibleVariants' own real
        values) to filter to what the actually-connected watch can use."""
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        q = query.get("q", [""])[0]
        variant = query.get("variant", [None])[0]
        category = query.get("category", [None])[0]
        limit = int(query.get("limit", ["50"])[0])
        try:
            results = search_catalog(query=q, variant=variant,
                                      category_id=int(category) if category else None,
                                      limit=limit)
        except OSError:
            self._send_json(502, {"ok": False, "error": "catalog not found under "
                                   f"{CATALOG_DIR} - run tools/extract_apps_catalog.py first"})
            return
        self._send_json(200, {"ok": True, "results": results})

    def _handle_customodes_displays_ambit1(self, mode, edits, confirm):
        """The Ambit1 half of POST /api/customodes/displays.

        Supports `setRow`, `add`, `remove` and `setType`. setRow is a fixed-size, in-place patch; add
        and remove RESIZE the record, so the patcher corrects the four nested lengths
        (DISPLAYS / MODE / MODES / root) and re-derives every offset afterwards. `setType`
        is done as replace-with-a-clone plus the layout's default rows, which is
        what SuuntoLink itself does (verified in its own capture: a 3-row retyped to 1-row
        came back as rows [10], not the old values).

        Row addressing matches what the reader emitted: display indices count USER displays
        only, with the computed built-in tail excluded on both sides."""
        # Layout key -> the template id the watch stores, matching SportModesPage's own
        # displayTypes table (the Ambit1 uses the same ids as the Ambit3).
        LAYOUT_ID = {"3-row": 260, "2-row": 261, "1-row": 262, "graph": 257}
        # The row values SuuntoLink itself writes for a freshly-created / retyped display,
        # read straight out of its own USB capture (assets/pcap/2026-08-23-ambit1-suuntolink):
        #   260 rows [10,11,0] views [5]   261 rows [10,0] views [5]
        #   262 rows [10]                  257 rows [6,32,5]
        # These match SportModesPage's own displayTypes defaults exactly, so the UI's staged
        # preview and what lands on the watch agree.
        LAYOUT_DEFAULT_ROWS = {
            260: [10, 11, 5],
            261: [10, 5],
            262: [10],
            257: [6, 32, 5],
        }
        rows = []
        unsupported = []
        for e in edits:
            if not isinstance(e, dict):
                continue
            if e.get("op") in ("setRow", "add", "remove", "setType"):
                rows.append(e)
                continue
            unsupported.append(e.get("op"))
        if not rows:
            self._send_json(400, {
                "ok": False,
                "error": "this watch supports changing a display row's data, and adding or "
                         f"removing a display; {sorted(set(unsupported))} is not supported "
                         "here (it would change a layout's row count, which resizes the "
                         "record differently)"})
            return

        # mode name -> index, from the watch's own current order
        try:
            sys.path.insert(0, str(TOOLS_DIR))
            import legacy_link                                  # noqa: PLC0415
            env_pid = hex(SELECTED_PRODUCT_ID) if SELECTED_PRODUCT_ID is not None else None
            old_env = os.environ.get("AMBIT_PRODUCT_ID")
            if env_pid:
                os.environ["AMBIT_PRODUCT_ID"] = env_pid
            try:
                with WATCH_LOCK:
                    info = legacy_link.ambit1_sport_mode_read()
            finally:
                if env_pid:
                    if old_env is None:
                        os.environ.pop("AMBIT_PRODUCT_ID", None)
                    else:
                        os.environ["AMBIT_PRODUCT_ID"] = old_env
        except RuntimeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        if not info.get("ok"):
            self._send_json(502, info)
            return
        names = [m.get("name", "") for m in info.get("modes") or []]
        if mode not in names:
            self._send_json(404, {"ok": False, "error": f"no sport mode named {mode!r}"})
            return
        mode_idx = names.index(mode)
        ROW_INDEX = {"top": 0, "center": 1, "bottom": 2}

        lines = []
        for e in rows:
            op = e.get("op")
            if op == "setType":
                disp = e.get("display")
                layout = LAYOUT_ID.get(str(e.get("type", "")).strip().lower())
                if disp is None or layout is None:
                    unsupported.append(f"setType:{e.get('type')}")
                    continue
                lines.append(f"{mode_idx}|display-set-type|{int(disp)}:{layout}")
                # ...then the layout's default row values, so the result matches both
                # SuuntoLink's own behaviour and the preview the UI already staged.
                for ri, field in enumerate(LAYOUT_DEFAULT_ROWS.get(layout, [])):
                    lines.append(f"{mode_idx}|row|{int(disp)}:{ri}:0:{int(field)}")
                continue
            if op == "add":
                layout = LAYOUT_ID.get(str(e.get("type", "")).strip().lower())
                if layout is None:
                    unsupported.append(f"add:{e.get('type')}")
                    continue
                # A new display is cloned from a real one on the watch with this layout, so
                # the added record is always structurally valid - see the patcher's own
                # comment. If the watch has no display of that layout to clone, the patcher
                # rejects it rather than synthesising one.
                lines.append(f"{mode_idx}|display-add|{layout}")
                continue
            if op == "remove":
                disp = e.get("display")
                if disp is None:
                    continue
                lines.append(f"{mode_idx}|display-remove|{int(disp)}")
                continue
            disp = e.get("display")
            row = ROW_INDEX.get(str(e.get("row", "")).strip().lower())
            values = e.get("values") or []
            if disp is None or row is None:
                continue
            for vi, field in enumerate(values):
                lines.append(f"{mode_idx}|row|{int(disp)}:{row}:{vi}:{int(field)}")
        if not lines:
            self._send_json(400, {"ok": False, "error": "no usable row edits in the request"})
            return

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("\n".join(lines) + "\n")
            patch_path = f.name
        try:
            args = ["ambit1-sport-mode-patch", patch_path]
            if not confirm:
                args.append("--dry-run")
            env_pid = hex(SELECTED_PRODUCT_ID) if SELECTED_PRODUCT_ID is not None else None
            old_env = os.environ.get("AMBIT_PRODUCT_ID")
            if env_pid:
                os.environ["AMBIT_PRODUCT_ID"] = env_pid
            try:
                with WATCH_LOCK:
                    result = legacy_link.run(args)
            finally:
                if env_pid:
                    if old_env is None:
                        os.environ.pop("AMBIT_PRODUCT_ID", None)
                    else:
                        os.environ["AMBIT_PRODUCT_ID"] = old_env
        except RuntimeError as exc:
            self._send_json(502, {"ok": False, "error": str(exc)})
            return
        finally:
            Path(patch_path).unlink(missing_ok=True)

        result["unsupportedOps"] = sorted(set(o for o in unsupported if o))
        self._send_json(200 if result.get("ok") else 502, result)

    def _handle_customodes_displays(self, body):
        """POST /api/customodes/displays - structural display edits, applied as ONE write.

        Body: {"mode": "Running", "edits": [...], "confirm": bool}. `edits` is the staged
        list the UI has built up - add/remove a display, change its type, set a row's values
        - and they are applied in order and written once. That is deliberate: the watch has
        no "change one field" command for sport modes, so every save rewrites the whole
        ~7.5 KB region. Writing per click would mean a full region write per click; staging
        and saving once is also what SuuntoLink does.

        Without confirm:true this is a rehearsal - tools/custom_modes_edit.py is dry-run by
        default and reports what would change, same rehearsal-first pattern as routes and
        settings. The tool additionally refuses any real write unless it can first reproduce
        the watch's CURRENT region byte-for-byte (see its own docstring).
        """
        mode = (body or {}).get("mode")
        edits = (body or {}).get("edits")
        if not mode or not isinstance(edits, list) or not edits:
            self._send_json(400, {"ok": False,
                                   "error": "need a mode name and a non-empty edits list"})
            return
        # Ambit1: its own patcher and its own region format (76-byte settings, displays as
        # in-place TLV). custom_modes_edit.py is the SBEM/CustomModes tool and does not apply.
        if selected_is_legacy():
            self._handle_customodes_displays_ambit1(mode, edits,
                                                     bool((body or {}).get("confirm")))
            return

        args = ["--mode", mode, "--edits", json.dumps(edits), "--json"]
        if (body or {}).get("confirm"):
            args.append("--write")
        # Testing mode edits the demo region image instead of a watch, through the same tool
        # and the same round-trip guard - so an edit made while trying the app out behaves
        # exactly like one made on hardware, and persists for the session.
        if demo_ambit():
            args += ["--from", demo_custom_modes_path()]
        code, out, err = run_tool("custom_modes_edit.py", args)
        parsed = self._parse_last_json_line(out)
        if parsed is None:
            self._send_json(502, {"ok": False,
                                   "error": "custom_modes_edit.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200 if parsed.get("ok") else 502, parsed)

    def _handle_workout_compile(self, body):
        """POST /api/workout/compile. Body: {"workout": {...}}. Compiles the workout JSON into
        the native guidance binary via tools/guided_workout.py --compile-only (no watch needed),
        so the UI can show "compiled, N bytes" before an install."""
        workout = body.get("workout")
        if not workout:
            self._send_json(400, {"ok": False, "error": 'missing "workout"'})
            return
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(workout, f)
            path = f.name
        try:
            code, out, err = run_tool("guided_workout.py", [path, "--compile-only"], timeout=60)
        finally:
            os.unlink(path)
        info = self._parse_last_json_line(out)
        if info is None or not info.get("ok"):
            self._send_json(502, {"ok": False, "error": "couldn't compile the workout "
                                   "(community compiler / network?)", "raw_output": out,
                                   "stderr": err})
            return
        self._send_json(200, info)

    def _handle_workout_install(self, body):
        """POST /api/workout/install. Body: {"workout": {...}, "mode": "<mode name>"}. Installs
        the workout as a native guided workout into the named sport mode's WORKOUT menu via
        tools/guided_workout.py --append --write (Apps entry byte0=1 + guidance display 295, no
        rule - dormant until picked from [Next]->WORKOUT). Not slotted onto a display field."""
        workout = body.get("workout")
        mode = body.get("mode")
        if not workout or not mode:
            self._send_json(400, {"ok": False, "error": 'need "workout" and "mode"'})
            return
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(workout, f)
            path = f.name
        try:
            code, out, err = run_tool(
                "guided_workout.py",
                [path, "--mode", str(mode), "--append", "--json", "--write"], timeout=180)
        finally:
            os.unlink(path)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "guided_workout.py produced no "
                                   "parseable JSON - is the watch connected and on the time "
                                   "screen?", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_apps_install(self, body):
        """POST /api/apps/install. Body: {"mode": int, "display": int, "field": int,
        "ruleId": int, "confirm": bool}. Real, 2026-08-09, hardware-confirmed - installs one
        real catalog app (by its real ruleId) and makes it render on a sport mode's chosen
        display field, via tools/workout_install.py's own write path (build_apps_region()/
        install_app_into_mode()). That path appends the app's engine slot as a
        DISP_FIELD_SHORTCUT the row cycles to (Finding 44) and strips the catalog binary's
        leading IAMRULE magic so it isn't doubled (Finding 45) - the two fixes that took a
        self-installed app from "--" to actually rendering; nothing here has to handle
        either. `mode`/`display`/`field` are the same 0-based indices the /api/customodes
        response's own arrays use - a UI already holding that data knows a mode's own array
        position without a second lookup here.

        Real, deliberate difference from this project's usual rehearsal-first pattern:
        workout_install.py itself only opens a real connection when --write is given (see
        that file's own main(), and its comment on why this wasn't changed to match
        settings_write.py's own "always read for real" pattern - real flash-write code,
        not something to restructure without hardware to verify every flag combination
        against). So rather than ask that tool for a live dry-run it isn't built to give,
        confirm:false here never calls it at all - the preview (wouldBeRuleIdx) is built
        from /api/apps' own already-safe, already-read-only data instead (next_rule_idx()
        is exactly len(existing entries), confirmed in workout_install.py's own module
        docstring). confirm:true is the only path that ever touches the watch's flash."""
        mode = body.get("mode")
        display = body.get("display")
        field = body.get("field")
        rule_id = body.get("ruleId")
        if mode is None or display is None or field is None or rule_id is None:
            self._send_json(400, {"error": "missing \"mode\", \"display\", \"field\", "
                                   "or \"ruleId\""})
            return
        try:
            entry, binary = catalog_entry_binary(int(rule_id))
        except OSError:
            self._send_json(502, {"ok": False, "error": "catalog not found under "
                                   f"{CATALOG_DIR} - run tools/extract_apps_catalog.py first"})
            return
        if entry is None:
            self._send_json(404, {"ok": False, "error": f"no catalog entry with ruleId={rule_id}"})
            return

        if not bool(body.get("confirm", False)):
            code, out, err = run_tool("apps.py", ["--json"], timeout=60)
            info = self._parse_last_json_line(out)
            if info is None or not info.get("ok"):
                self._send_json(502, {"ok": False, "error": "couldn't read the watch's "
                                       "current Apps region for a preview", "raw_output": out,
                                       "stderr": err})
                return
            self._send_json(200, {
                "ok": True, "dryRun": True, "wouldBeRuleIdx": len(info["entries"]),
                "name": entry["name"], "ruleId": entry["ruleId"],
            })
            return

        compiled = {"name": entry["name"], "activityId": entry["activityId"],
                    "binary": list(binary)}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(compiled, f)
            compiled_path = f.name
        try:
            args = [compiled_path, "--mode", str(mode), "--display", str(display),
                     "--field", str(field), "--json", "--write"]
            # A real install writes two real flash regions (Apps + CustomModes) - the
            # default 180s run_tool() timeout already covers this, no override needed.
            code, out, err = run_tool("workout_install.py", args)
        finally:
            Path(compiled_path).unlink(missing_ok=True)
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "workout_install.py --json produced "
                                   "no parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_hrv_install(self, body):
        """POST /api/hrv/install - one-tap setup for the Health page's morning-HRV test:
        ensure an "HRV" sport mode exists and the community "5+5 HRV" Suunto App
        (ruleId 10069694) is wired onto its first display, so the user can record a guided
        HRV test. Orchestrates the existing create-mode + app-install tools, and runs INSIDE
        the backend so it serialises with the app's own watch access - the concurrent USB
        access that garbled an earlier CustomModes write (workout_install.py's read-back guard
        would now roll that back, but serialising avoids it in the first place).

        Body: {"confirm": bool}. confirm:false previews (does the mode exist? is the app
        already on it?); confirm:true creates the mode if missing and installs+wires the app.
        Idempotent: if the HRV mode already carries an app it reports alreadyInstalled and
        writes nothing, so re-tapping never duplicates the app."""
        HRV_RULE_ID = 10069694          # the "5+5 HRV" community app in SuuntoLink's catalog
        HRV_MODE_NAME = "HRV"
        HRV_MODE_ACTIVITY = 95          # Indoor training: HR belt on, GPS off (stationary test)
        confirm = bool((body or {}).get("confirm", False))

        def read_modes():
            code, out, err = run_tool("custom_modes.py", ["--json"], timeout=60)
            info = self._parse_last_json_line(out)
            return (info.get("exerciseModes") if info and info.get("ok") else None), out, err

        modes, out, err = read_modes()
        if modes is None:
            self._send_json(502, {"ok": False, "error": "couldn't read the watch's sport modes",
                                   "raw_output": out, "stderr": err})
            return
        hrv_idx = next((i for i, m in enumerate(modes) if m.get("name") == HRV_MODE_NAME), None)
        already = hrv_idx is not None and (modes[hrv_idx].get("appCount") or 0) > 0

        if not confirm:
            self._send_json(200, {"ok": True, "dryRun": True,
                                   "modeExists": hrv_idx is not None, "alreadyInstalled": already})
            return
        if already:
            self._send_json(200, {"ok": True, "alreadyInstalled": True, "hrvModeIndex": hrv_idx,
                                   "message": "The HRV app is already on your HRV mode."})
            return

        # Create the HRV mode if the watch doesn't have one yet, then find its index.
        if hrv_idx is None:
            code, out, err = run_tool("sport_mode_manage.py",
                                      ["--create", HRV_MODE_NAME, "--activity",
                                       str(HRV_MODE_ACTIVITY), "--write"], timeout=180)
            created = self._parse_last_json_line(out)
            modes, out2, err2 = read_modes()
            hrv_idx = next((i for i, m in enumerate(modes or [])
                            if m.get("name") == HRV_MODE_NAME), None)
            if hrv_idx is None:
                self._send_json(502, {"ok": False, "error": "created the HRV mode but could not "
                                       "find it afterwards", "raw_output": out, "stderr": err})
                return

        # Install + wire the app (workout_install.py, whose write now self-verifies + rolls back).
        try:
            entry, binary = catalog_entry_binary(HRV_RULE_ID)
        except OSError:
            self._send_json(502, {"ok": False, "error": "app catalog not found under "
                                   f"{CATALOG_DIR} - run tools/extract_apps_catalog.py first"})
            return
        if entry is None:
            self._send_json(404, {"ok": False, "error": "the 5+5 HRV app is not in the catalog"})
            return
        compiled = {"name": entry["name"], "activityId": entry["activityId"],
                    "binary": list(binary)}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(compiled, f)
            compiled_path = f.name
        try:
            args = [compiled_path, "--mode", str(hrv_idx), "--display", "0", "--field", "0",
                     "--json", "--write"]
            code, out, err = run_tool("workout_install.py", args)
        finally:
            Path(compiled_path).unlink(missing_ok=True)
        result = self._parse_last_json_line(out)
        if result is None:
            self._send_json(502, {"ok": False, "error": "the app install did not confirm - it "
                                   "may have been rolled back (a safe failure); try again",
                                   "raw_output": out, "stderr": err})
            return
        result["hrvModeIndex"] = hrv_idx
        self._send_json(200 if result.get("ok") else 502, result)

    # --- Training Program (tools/training_plan.py - see its docstring for the whole
    # design: workouts scheduled on calendar dates as date-gated Suunto Apps, the
    # from-scratch replacement for Movescount's own "Training programs") -----------------

    @staticmethod
    def _plan_id(name):
        slug = "".join(c if c.isalnum() else "-" for c in (name or "").lower()).strip("-")
        while "--" in slug:
            slug = slug.replace("--", "-")
        return slug or datetime.now(timezone.utc).strftime("plan-%Y%m%d-%H%M%S")

    def _handle_trainingprogram_list(self):
        """GET /api/trainingprogram - saved plans, newest-edited first. Pure local disk,
        no watch, no network."""
        plans = []
        if PLANS_DIR.is_dir():
            for path in PLANS_DIR.glob("*.json"):
                try:
                    with open(path) as f:
                        plan = json.load(f)
                except (OSError, json.JSONDecodeError):
                    continue  # an unreadable file shouldn't hide every other plan
                plans.append({"id": path.stem, "name": plan.get("name", path.stem),
                              "entries": plan.get("entries", []),
                              "updatedAt": plan.get("updatedAt", "")})
        plans.sort(key=lambda p: p["updatedAt"], reverse=True)
        self._send_json(200, {"ok": True, "plans": plans})

    def _handle_trainingprogram_save(self, body):
        """POST /api/trainingprogram - body {"plan": {name, entries}}. Same name = same
        file, so re-saving an edited plan updates it rather than multiplying copies."""
        plan = body.get("plan") or {}
        if not isinstance(plan.get("entries"), list):
            self._send_json(400, {"ok": False, "error": "plan needs an \"entries\" list"})
            return
        plan_id = self._plan_id(plan.get("name"))
        plan["updatedAt"] = datetime.now(timezone.utc).isoformat()
        PLANS_DIR.mkdir(parents=True, exist_ok=True)
        with open(PLANS_DIR / f"{plan_id}.json", "w") as f:
            json.dump(plan, f, indent=2)
        self._send_json(200, {"ok": True, "id": plan_id})

    def _handle_trainingprogram_delete(self, body):
        plan_id = body.get("id") or ""
        path = PLANS_DIR / f"{plan_id}.json"
        # resolve() guard: an id like "../foo" must never reach outside PLANS_DIR
        if not plan_id or PLANS_DIR.resolve() not in path.resolve().parents:
            self._send_json(400, {"ok": False, "error": "bad plan id"})
            return
        path.unlink(missing_ok=True)
        self._send_json(200, {"ok": True})

    def _handle_trainingprogram_sync_calendar(self, body):
        """POST /api/trainingprogram/sync-calendar. Body: {entries:[{date,mode,workout}], write}.
        Install the plan as native guided workouts in each entry's sport-mode WORKOUT menu,
        rotating by date (upcoming installed, past erased) via tools/training_calendar.py --sync -
        the current design (2026-08-21) that supersedes the date-gated App-Zone install. write:false
        is a real dry-run (compile + diff, no watch write). Same shell-the-tool shape as the
        standalone calendar GUI's own sync handler."""
        entries = body.get("entries")
        if not entries:
            self._send_json(400, {"ok": False, "error": 'need a non-empty "entries" list'})
            return
        missing = [i for i, e in enumerate(entries)
                   if not (e.get("date") and e.get("mode") and e.get("workout"))]
        if missing:
            self._send_json(400, {"ok": False,
                                   "error": f"entries {missing} are missing date/mode/workout"})
            return
        plan = {"name": body.get("name", "Calendar"), "entries": entries}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(plan, f)
            plan_path = f.name
        try:
            args = [plan_path, "--sync", "--json"]
            if body.get("write"):
                args.append("--write")
            code, out, err = run_tool("training_calendar.py", args, timeout=300)
        finally:
            try:
                os.unlink(plan_path)
            except OSError:
                pass
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False,
                                   "error": ("training_calendar produced no JSON: "
                                             + (err or out or "")).strip()[:200],
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_trainingprogram_install(self, body):
        """POST /api/trainingprogram/install. Body: {"plan": {...}, "mode": int,
        "display": int, "field": int, "confirm": bool}.

        confirm:false compiles only (live community compiler - internet, no watch) and
        returns the real packing: which dates land in which app and each compiled size.
        That IS the honest dry-run here: compilation is the step that can fail on content
        (workout too long for one app slot), and it touches no flash.

        confirm:true compiles then installs each app in sequence onto the SAME
        mode/display/field via workout_install.py - the row then cycles through the
        program's apps the same way SuuntoLink's own multi-shortcut rows work (Finding 44).
        Every hardware-proven fix in that tool (used-extent hash, no 0x0b04, Type/Shortcut
        invariant, single IAMRULE magic) applies unchanged. Stops at the first failed
        install and reports how far it got - the watch is left with N valid installed apps,
        not half of one."""
        plan = body.get("plan") or {}
        mode, display, field = body.get("mode"), body.get("display"), body.get("field")
        confirm = bool(body.get("confirm", False))
        if not isinstance(plan.get("entries"), list) or not plan["entries"]:
            self._send_json(400, {"ok": False, "error": "plan has no entries"})
            return
        if confirm and (mode is None or display is None or field is None):
            self._send_json(400, {"ok": False, "error": "missing \"mode\", \"display\", "
                                   "or \"field\""})
            return

        out_dir = Path(tempfile.mkdtemp(prefix="ambitapp-plan-"))
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(plan, f)
            plan_path = f.name
        try:
            # Compiles go to the live community compiler - one HTTP round-trip per app plus
            # the packing back-off's retries, so give it longer than run_tool's default.
            code, out, err = run_tool(
                "training_plan.py",
                [plan_path, "--compile", "--out-dir", str(out_dir), "--json"], timeout=600)
            info = self._parse_last_json_line(out)
            if info is None or not info.get("ok"):
                self._send_json(502, {"ok": False, "error": (info or {}).get(
                    "error", "training_plan.py produced no parseable JSON"),
                    "raw_output": out, "stderr": err})
                return
            apps = info["apps"]
            if not confirm:
                self._send_json(200, {"ok": True, "dryRun": True, "apps": apps})
                return

            installed = []
            for app in apps:
                code, out, err = run_tool(
                    "workout_install.py",
                    [app["path"], "--mode", str(mode), "--display", str(display),
                     "--field", str(field), "--json", "--write"])
                result = self._parse_last_json_line(out)
                if result is None or not result.get("ok"):
                    self._send_json(502, {
                        "ok": False, "installed": installed, "failedApp": app["name"],
                        "error": (result or {}).get("error",
                                                     "workout_install.py gave no JSON"),
                        "raw_output": out, "stderr": err})
                    return
                installed.append({"name": app["name"], "dates": app["dates"],
                                  "binaryLength": app["binaryLength"]})
            self._send_json(200, {"ok": True, "installed": installed})
        finally:
            Path(plan_path).unlink(missing_ok=True)
            for p in out_dir.glob("*.json"):
                p.unlink(missing_ok=True)
            out_dir.rmdir()

    # --- GPS Track Pod (2026-08-12, "just blind, as experimental") - see
    # tools/gps_track_pod.py's own module docstring for why this whole feature is marked
    # experimental and read-only: nobody on this project owns the hardware, so none of it
    # has ever been checked against a real device. WATCH_LOCK still applies (via run_tool())
    # even though this is a different physical device from "the watch" everywhere else -
    # no reason two USB operations should ever run concurrently through this backend. ---

    def _handle_gpstrackpod_status(self):
        """GET /api/gpstrackpod/status - device info + status, or a clean "not connected"
        rather than a stack trace if nothing is plugged in."""
        code, out, err = run_tool("gps_track_pod.py", ["--status", "--json"])
        result = self._parse_last_json_line(out)
        if result is None:
            self._send_json(502, {"ok": False,
                                   "error": "gps_track_pod.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200, result)

    def _handle_gpstrackpod_tracks(self):
        """GET /api/gpstrackpod/tracks - every track currently on the device."""
        code, out, err = run_tool("gps_track_pod.py", ["--list", "--json"])
        result = self._parse_last_json_line(out)
        if result is None:
            self._send_json(502, {"ok": False,
                                   "error": "gps_track_pod.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200, result)

    def _handle_gpstrackpod_retrieve(self, body):
        """POST /api/gpstrackpod/retrieve. Body: {"index": int} (-1 for every track).
        Writes GPX file(s) into GPSTRACKPOD_DIR and returns their paths - same
        write-to-a-real-folder-in-the-user's-home shape as backups/plans elsewhere."""
        index = body.get("index")
        if not isinstance(index, int):
            self._send_json(400, {"ok": False, "error": "missing \"index\" (int, -1 for all)"})
            return
        GPSTRACKPOD_DIR.mkdir(parents=True, exist_ok=True)
        code, out, err = run_tool(
            "gps_track_pod.py",
            ["--retrieve", str(index), "--out-dir", str(GPSTRACKPOD_DIR), "--json"])
        result = self._parse_last_json_line(out)
        if result is None:
            self._send_json(502, {"ok": False,
                                   "error": "gps_track_pod.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200 if result.get("ok") else 502, result)

    def _handle_gpstrackpod_logs(self):
        """POST /api/gpstrackpod/logs - the "Send logs" button. Runs a real diagnostic
        session (status, tracks, the device's own internal log if it offers one) through
        gpspod's own raw-USB-packet recorder and writes the result into GPSTRACKPOD_DIR.
        Nothing is sent anywhere automatically - this only writes a local file for the user
        to attach by hand, same shape as LogService::reportProblem() elsewhere in this app."""
        GPSTRACKPOD_DIR.mkdir(parents=True, exist_ok=True)
        log_path = GPSTRACKPOD_DIR / time.strftime("gpstrackpod-log-%Y%m%d-%H%M%S.json.gz")
        code, out, err = run_tool(
            "gps_track_pod.py", ["--send-logs", str(log_path), "--json"], timeout=300)
        result = self._parse_last_json_line(out)
        if result is None:
            self._send_json(502, {"ok": False,
                                   "error": "gps_track_pod.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200 if result.get("ok") else 502, result)

    # Suunto T6 + legacy merge (2026-08-14, "implement Suunto t6 ... only as experimental").
    # Same thin pass-through shape as the gpstrackpod handlers above: run the tool with --json,
    # forward its final JSON line. See tools/suunto_t6.py / tools/legacy_merge.py docstrings for
    # why this whole corner is built blind (nobody on this project owns a T6).

    def _handle_suuntot6_status(self):
        """GET /api/suuntot6/status - device info, or a clean "not present"."""
        code, out, err = run_tool("suunto_t6.py", ["--status", "--json"])
        result = self._parse_last_json_line(out)
        if result is None:
            self._send_json(502, {"ok": False, "error": "suunto_t6.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200, result)

    def _handle_suuntot6_logs(self):
        """GET /api/suuntot6/logs - training logs on the device."""
        code, out, err = run_tool("suunto_t6.py", ["--list", "--json"])
        result = self._parse_last_json_line(out)
        if result is None:
            self._send_json(502, {"ok": False, "error": "suunto_t6.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200 if result.get("ok") else 502, result)

    def _handle_suuntot6_retrieve(self, body):
        """POST /api/suuntot6/retrieve. Body: {"index": int, "format": "fit"|"xml"}. Writes the
        export (+ a JSON sample sidecar, for the merge) into SUUNTOT6_DIR."""
        index = body.get("index")
        fmt = body.get("format", "fit")
        if not isinstance(index, int):
            self._send_json(400, {"ok": False, "error": "missing \"index\" (int)"})
            return
        if fmt not in ("fit", "xml"):
            self._send_json(400, {"ok": False, "error": "format must be \"fit\" or \"xml\""})
            return
        SUUNTOT6_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SUUNTOT6_DIR / f"suunto_t6_{index}.{fmt}"
        args = ["--retrieve", str(index), "--out", str(out_path), "--format", fmt, "--json"]
        if fmt == "fit":
            args += ["--samples-out", str(SUUNTOT6_DIR / f"suunto_t6_{index}.json")]
        code, out, err = run_tool("suunto_t6.py", args)
        result = self._parse_last_json_line(out)
        if result is None:
            self._send_json(502, {"ok": False, "error": "suunto_t6.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200 if result.get("ok") else 502, result)

    # ── Suunto X6HR (experimental legacy wristop, serial/IR - see tools/suunto_x6hr.py) ──

    def _handle_suuntox6hr_status(self):
        """GET /api/suuntox6hr/status - serial number + units, or a clean "not present"."""
        code, out, err = run_tool("suunto_x6hr.py", ["--status", "--json"])
        result = self._parse_last_json_line(out)
        if result is None:
            self._send_json(502, {"ok": False, "error": "suunto_x6hr.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200, result)

    def _handle_suuntox6hr_logs(self):
        """GET /api/suuntox6hr/logs - chrono + hiking logs on the device."""
        code, out, err = run_tool("suunto_x6hr.py", ["--list", "--json"])
        result = self._parse_last_json_line(out)
        if result is None:
            self._send_json(502, {"ok": False, "error": "suunto_x6hr.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200 if result.get("ok") else 502, result)

    def _handle_suuntox6hr_retrieve(self, body):
        """POST /api/suuntox6hr/retrieve. Body: {"index": int, "format": "gpx"|"csv"|"json"}.
        Writes the export + a JSON sample sidecar (for the merge) into SUUNTOX6HR_DIR - the same
        sidecar shape suunto_t6.py emits, so legacy_merge consumes it identically."""
        index = body.get("index")
        fmt = body.get("format", "gpx")
        if not isinstance(index, int):
            self._send_json(400, {"ok": False, "error": "missing \"index\" (int)"})
            return
        if fmt not in ("gpx", "csv", "json"):
            self._send_json(400, {"ok": False, "error": "format must be gpx, csv or json"})
            return
        SUUNTOX6HR_DIR.mkdir(parents=True, exist_ok=True)
        out_path = SUUNTOX6HR_DIR / f"suunto_x6hr_{index}.{fmt}"
        args = ["--retrieve", str(index), "--out", str(out_path), "--format", fmt, "--json",
                "--samples-out", str(SUUNTOX6HR_DIR / f"suunto_x6hr_{index}.json")]
        code, out, err = run_tool("suunto_x6hr.py", args)
        result = self._parse_last_json_line(out)
        if result is None:
            self._send_json(502, {"ok": False, "error": "suunto_x6hr.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200 if result.get("ok") else 502, result)

    def _handle_legacywatch_status(self):
        """GET /api/legacywatch/status - detect whichever legacy Suunto wristop is connected:
        the T6 first (it announces its FTDI USB cable), then the X6HR (probed on a plain serial
        port). Returns {ok, device: "t6"|"x6hr", status} so one page can auto-detect and then
        pull that device's own logs. Neither present -> {ok:false, device:null}."""
        for device, script in (("t6", "suunto_t6.py"), ("x6hr", "suunto_x6hr.py")):
            code, out, err = run_tool(script, ["--status", "--json"])
            st = self._parse_last_json_line(out)
            if st and st.get("ok"):
                self._send_json(200, {"ok": True, "device": device, "status": st})
                return
        self._send_json(200, {"ok": False, "device": None,
                              "error": "No Suunto T6 or X6HR detected. Connect it with its "
                                       "PC-interface cable (T6: FTDI USB cradle; X6HR: the "
                                       "serial/IR pod via a USB-serial adapter)."})

    def _handle_legacymerge_sources(self):
        """GET /api/legacymerge/sources - the GPS Track Pod tracks (GPSTRACKPOD_DIR/*.gpx) and
        T6 exports (SUUNTOT6_DIR/*.json) already on disk, for the merge picker."""
        def _listing(directory, pattern):
            if not directory.exists():
                return []
            return [{"name": p.name, "path": str(p)}
                    for p in sorted(directory.glob(pattern), reverse=True)]
        # "t6" is really "any legacy wristop sidecar" - the X6HR emits the same {points} shape,
        # so its exports merge with a Pod track through the identical path.
        self._send_json(200, {"ok": True,
                              "pod": _listing(GPSTRACKPOD_DIR, "*.gpx"),
                              "t6": _listing(SUUNTOT6_DIR, "*.json")
                                    + _listing(SUUNTOX6HR_DIR, "*.json")})

    def _handle_legacymerge_devices(self):
        """GET /api/legacymerge/devices - what's plugged in RIGHT NOW, for the device-first
        merge. Someone with both a T6 and a GPS Track Pod plugs them at the same time (they
        use different USB transports, so they don't conflict); this reads both live so they
        can pick a T6 log and a Pod track directly instead of exporting to files first."""
        _, t6_out, _ = run_tool("suunto_t6.py", ["--status", "--json"])
        t6_status = self._parse_last_json_line(t6_out) or {}
        t6_present = bool(t6_status.get("present"))
        t6_logs = []
        if t6_present:
            _, out, _ = run_tool("suunto_t6.py", ["--list", "--json"])
            t6_logs = (self._parse_last_json_line(out) or {}).get("logs", [])

        _, pod_out, _ = run_tool("gps_track_pod.py", ["--status", "--json"])
        pod_status = self._parse_last_json_line(pod_out) or {}
        pod_present = bool(pod_status.get("ok"))
        pod_tracks = []
        if pod_present:
            _, out, _ = run_tool("gps_track_pod.py", ["--list", "--json"])
            pod_tracks = (self._parse_last_json_line(out) or {}).get("tracks", [])

        self._send_json(200, {"ok": True,
                              "t6": {"present": t6_present, "logs": t6_logs},
                              "pod": {"present": pod_present, "tracks": pod_tracks}})

    def _handle_legacymerge_live(self, body):
        """POST /api/legacymerge/live. Body: {"t6_index": int, "pod_index": int,
        "format": "gpx"|"fit"}. Retrieves the chosen T6 log and Pod track live off both
        devices, then merges them in one action - the device-first path (André, 2026-08-15:
        "if the gps pod is connected, just read and select directly the activity we want to
        merge"). Written into LEGACYMERGE_DIR."""
        t6_index = body.get("t6_index")
        pod_index = body.get("pod_index")
        fmt = body.get("format", "gpx")
        if not isinstance(t6_index, int) or not isinstance(pod_index, int):
            self._send_json(400, {"ok": False,
                                   "error": "need integer \"t6_index\" and \"pod_index\""})
            return
        if fmt not in ("gpx", "fit"):
            self._send_json(400, {"ok": False, "error": "format must be \"gpx\" or \"fit\""})
            return
        SUUNTOT6_DIR.mkdir(parents=True, exist_ok=True)
        GPSTRACKPOD_DIR.mkdir(parents=True, exist_ok=True)
        LEGACYMERGE_DIR.mkdir(parents=True, exist_ok=True)

        # 1) T6 log -> a JSON sample sidecar the merge reads.
        t6_json = SUUNTOT6_DIR / f"suunto_t6_{t6_index}.json"
        _, out, err = run_tool("suunto_t6.py", [
            "--retrieve", str(t6_index), "--out", str(SUUNTOT6_DIR / f"suunto_t6_{t6_index}.fit"),
            "--format", "fit", "--samples-out", str(t6_json), "--json"])
        r = self._parse_last_json_line(out)
        if r is None or not r.get("ok"):
            self._send_json(502, {"ok": False, "error": "reading the T6 log failed",
                                   "detail": r, "stderr": err})
            return

        # 2) Pod track -> GPX.
        pod_gpx = GPSTRACKPOD_DIR / f"gpstrackpod_{pod_index}.gpx"
        _, out, err = run_tool("gps_track_pod.py", [
            "--retrieve", str(pod_index), "--out", str(pod_gpx), "--json"])
        r = self._parse_last_json_line(out)
        if r is None or not r.get("ok"):
            self._send_json(502, {"ok": False, "error": "reading the GPS Track Pod failed",
                                   "detail": r, "stderr": err})
            return

        # 3) Merge the two.
        out_path = LEGACYMERGE_DIR / f"suunto_t6_{t6_index}+pod_{pod_index}.{fmt}"
        _, out, err = run_tool("legacy_merge.py", [
            "--pod-gpx", str(pod_gpx), "--t6-json", str(t6_json),
            "--out", str(out_path), "--format", fmt, "--json"])
        result = self._parse_last_json_line(out)
        if result is None:
            self._send_json(502, {"ok": False, "error": "legacy_merge.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200 if result.get("ok") else 502, result)

    def _handle_legacymerge_run(self, body):
        """POST /api/legacymerge/run. Body: {"pod_gpx": path, "t6_json": path,
        "format": "gpx"|"fit"}. Aligns the two on time (auto-align default) and writes a merged
        GPS+HR activity into LEGACYMERGE_DIR."""
        pod_gpx = body.get("pod_gpx")
        t6_json = body.get("t6_json")
        fmt = body.get("format", "gpx")
        if not pod_gpx or not t6_json:
            self._send_json(400, {"ok": False, "error": "need \"pod_gpx\" and \"t6_json\""})
            return
        if fmt not in ("gpx", "fit"):
            self._send_json(400, {"ok": False, "error": "format must be \"gpx\" or \"fit\""})
            return
        LEGACYMERGE_DIR.mkdir(parents=True, exist_ok=True)
        out_path = LEGACYMERGE_DIR / (Path(t6_json).stem + f"-merged.{fmt}")
        code, out, err = run_tool("legacy_merge.py", [
            "--pod-gpx", str(pod_gpx), "--t6-json", str(t6_json),
            "--out", str(out_path), "--format", fmt, "--json"])
        result = self._parse_last_json_line(out)
        if result is None:
            self._send_json(502, {"ok": False, "error": "legacy_merge.py produced no JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200 if result.get("ok") else 502, result)

    def _handle_firmware_download(self, body=None):
        """POST /api/firmware/download - downloads the official firmware image and returns
        its local path, which the Firmware page then hands to /api/firmware/flash. With no
        body it reads the connected (healthy) watch; for recovering a watch already stuck in
        the bootloader - which reports model "BSL" and can't identify itself - pass
        {"model": codename, "hw": hw_version} (from the watch registry) so the right image is
        fetched anyway. The image is a real SFI2ST firmware container, flashed by
        firmware_write.py - see FIRMWARE_FLASHER_DESIGN.md."""
        body = body or {}
        FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
        base = ["--json"]
        if body.get("model") and body.get("hw"):
            base += ["--model", body["model"], "--hw", body["hw"]]
        code, out, err = run_tool("firmware_check.py", base)
        if code != 0:
            self._send_json(502, {"ok": False, "raw_output": out, "stderr": err})
            return
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "firmware_check.py --json produced "
                                   "no parseable JSON", "raw_output": out})
            return

        version = info.get("latest_firmware_version") or "unknown"
        hw = info.get("hw_version") or "unknown"
        dest = FIRMWARE_DIR / f"{info.get('model', 'watch')}-fw_{version}-{hw}.bin"
        code, out, err = run_tool("firmware_check.py", base + ["--download", str(dest)])
        ok = code == 0 and dest.exists()
        self._send_json(200 if ok else 502, {
            "ok": ok, "path": str(dest) if ok else None,
            "size_bytes": dest.stat().st_size if ok else None,
            "raw_output": out, "stderr": err, **info})

    def _handle_firmware_known(self):
        """GET /api/firmware/known - the watches we've recorded (tools/watch_registry.py),
        for the recovery picker: a watch stuck in BSL can't name itself, so the user chooses
        which previously-connected watch to restore. See FIRMWARE_FLASHER_DESIGN.md."""
        code, out, err = run_tool("watch_registry.py", ["--json"])
        info = self._parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False, "error": "watch_registry.py --json produced "
                                   "no parseable JSON", "raw_output": out, "stderr": err})
            return
        self._send_json(200, info)

    def _stream_firmware_flash(self, body):
        """POST /api/firmware/flash - runs the REAL flasher and streams its --json progress
        as newline-delimited JSON (one event per line) so the Firmware page shows live
        progress across the ~10-minute flash. Body: {"file": path, "expect_model": codename}.
        Holds WATCH_LOCK for the whole flash (only one process can own the USB). This is the
        one irreversible write; the flasher itself carries the safety guards (model/battery
        checks, abort-before-commit, watchdog+restart) - see firmware_write.py."""
        file = (body or {}).get("file")
        model = (body or {}).get("expect_model")
        if not file or not model:
            self._send_json(400, {"error": "file and expect_model are required"})
            return
        if not Path(file).is_file():
            self._send_json(404, {"error": f"firmware file not found: {file}"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        args = [PYTHON, str(TOOLS_DIR / "firmware_write.py"), file,
                "--expect-model", model, "--commit", "--json"]
        with WATCH_LOCK:
            proc = subprocess.Popen(args, cwd=TOOLS_DIR, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
            try:
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:  # forward only real JSON events; skip the tools' own log lines
                        json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    try:
                        self.wfile.write((line + "\n").encode("utf-8"))
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        proc.kill()
                        break
                proc.wait()
            finally:
                if proc.poll() is None:
                    proc.kill()

    def _handle_backup_create(self, body=None):
        """Read-only against the watch (`nav` never writes), safe to call any time - the
        only actual disk write is the two .bin files themselves, not the watch.

        Body may carry an optional "dir" - the folder to write the backup into instead of the
        default ~/AmbitAppBackups (André, 2026-08-16: "save to a folder... your favourite cloud
        folder, so it can be synced"). Pointing it at a Dropbox/OneDrive/Drive sync folder is how
        the app does keyless cloud backup now, no OAuth."""
        body = body or {}
        target = BACKUP_DIR
        if body.get("dir"):
            target = Path(os.path.expanduser(str(body["dir"]))).resolve()
        target.mkdir(parents=True, exist_ok=True)
        label = time.strftime("%Y%m%d-%H%M%S")
        prefix = str(target / label)
        # Skipped entirely on a Kailash - see the branch below for what its regions actually
        # contain. This is a ~250 KB flash read over USB that comes back blank on that watch,
        # so it costs real seconds to produce three useless files.
        is_kailash = selected_is_kailash()
        if is_kailash:
            code, out, err = 0, "", ""
            routes_ok = False
        else:
            code, out, err = run_tool("write_nav.py", ["nav", "--save", prefix])
            routes_ok = code == 0 and Path(f"{prefix}-routes.bin").exists()
        # Ember rides along in the same backup (André, 2026-08-26: "afaik there is zero GUI
        # service to backup it somewhere" - this closes that gap the same keyless way, no new
        # mechanism). Best-effort and silent if Ember was never used (no log.json yet).
        #
        # Independent of the watch call above on purpose: someone using Ember without a
        # connected/supported watch (a Garmin owner, or no watch at all) must still get a
        # successful backup, not a 502 because `nav --save` had nothing to talk to.
        has_ember = False
        try:
            if EMBER_FILE.exists():
                Path(f"{prefix}-ember.json").write_text(EMBER_FILE.read_text())
                has_ember = True
        except Exception:
            pass
        # A Kailash's backup is a different thing entirely, and this is the whole reason it
        # gets its own branch (André, 2026-08-27: "try it" - so we did, against his watch).
        # `nav --save` above is meaningless here: its routes region came back 129,968 of
        # 130,000 bytes 0xFF with a header magic of 0x3008 against the 0x340c expected, and
        # waypoints 16,380 of 16,384 bytes 0xFF - both simply blank, which is consistent,
        # because this watch has no routes or POIs feature to fill them. CustomModes, Apps
        # and TrainingProgram are not declared or short-reply. The only region with real
        # bytes was GlonassSGEE, the GPS ephemeris, which expires and re-downloads itself.
        #
        # What IS irreplaceable on a Kailash is its DeviceHistory (visited cities/countries,
        # travel stats, the activity-mode logbook) and its TrackLog (the passive GPS track) -
        # and those are exactly what a firmware flash wipes
        # ([[ambit_app_kailash_desktop_home_fix]]). Neither is touched by `nav --save`, so a
        # Kailash backup that captured nothing is what this replaces.
        #
        # This is an ARCHIVE, not a restore point: there is no proven write path for either
        # region and this project does not invent one. Written as JSON (the tools' own
        # output, losslessly) plus a .gpx of the track so the data is usable outside this app
        # at all - which is the actual point of keeping it.
        has_kailash = False
        kailash_err = ""
        if is_kailash:
            hist_code, hist_out, hist_err = run_tool("kailash_history.py", ["--json"])
            hist = self._parse_last_json_line(hist_out)
            if hist and hist.get("ok"):
                Path(f"{prefix}-kailash-history.json").write_text(
                    json.dumps(hist, indent=2))
                has_kailash = True
            else:
                kailash_err = (hist_err or hist_out or "kailash_history.py produced no JSON")

            # ~1.3 MB flash read over USB - the same longer timeout /api/kailash/tracklog uses.
            trk_code, trk_out, trk_err = run_tool("kailash_tracklog.py", ["--json"], timeout=300)
            trk = self._parse_last_json_line(trk_out)
            if trk and trk.get("ok"):
                Path(f"{prefix}-kailash-tracklog.json").write_text(
                    json.dumps(trk, indent=2))
                has_kailash = True
                # Every correlated segment the tool already produced a GPX for, concatenated
                # one file per activity. Skipped silently when a segment has no track (a
                # session predating TrackLog's coverage) rather than writing an empty file.
                for i, act in enumerate(trk.get("activities") or []):
                    gpx = act.get("gpxText")
                    if gpx:
                        Path(f"{prefix}-kailash-track-{i + 1}.gpx").write_text(gpx)
            elif not kailash_err:
                kailash_err = (trk_err or trk_out or "kailash_tracklog.py produced no JSON")

        ok = routes_ok or has_ember or has_kailash
        self._send_json(200 if ok else 502, {
            "ok": ok, "prefix": prefix, "label": label, "hasEmber": has_ember,
            "hasRoutes": routes_ok, "hasKailash": has_kailash,
            "kailashError": kailash_err, "raw_output": out, "stderr": err})

    def _handle_restore(self, body):
        """Body: {"prefix": str, "confirm": bool}. Real hardware write when confirmed - same
        rehearsal-first pattern as everything else here, and the exact mechanism
        HANDOFF.md documents as "the backup that milestone 4 asked for and never had"."""
        prefix = body.get("prefix")
        if not prefix:
            self._send_json(400, {"error": "missing \"prefix\""})
            return
        has_routes_backup = (Path(f"{prefix}-routes.bin").exists()
                              and Path(f"{prefix}-waypoints.bin").exists())
        has_ember_backup = Path(f"{prefix}-ember.json").exists()
        if not (has_routes_backup or has_ember_backup):
            # A Kailash archive is deliberately one-way: this project has no proven write
            # path for DeviceHistory or TrackLog and does not invent one for a restore.
            # Say that, rather than the generic "no backup found" it would otherwise get.
            if (Path(f"{prefix}-kailash-history.json").exists()
                    or Path(f"{prefix}-kailash-tracklog.json").exists()):
                self._send_json(400, {"error": "This is a Kailash archive - travel history "
                                       "and GPS track, kept so a firmware flash cannot lose "
                                       "them. There is no write path back to the watch for "
                                       "either, so it cannot be restored."})
                return
            self._send_json(400, {"error": f"no backup found at prefix {prefix!r}"})
            return

        confirm = bool(body.get("confirm", False))
        # An Ember-only backup (no watch data alongside it) has nothing for write_nav.py to
        # restore - skip that call entirely rather than have it fail on files that were never
        # written in the first place.
        if has_routes_backup:
            args = ["restore", prefix]
            if confirm:
                args.append("--write")
            code, out, err = run_tool("write_nav.py", args)
            ok = code == 0
        else:
            code, out, err, ok = 0, "", "", True

        # Ember restores alongside the watch data if this backup has it - same rehearsal-first
        # confirm=false/true pattern, so a dry-run reports what WOULD happen without touching
        # anything. Full overwrite (not merged), matching what "restore a backup" means
        # everywhere else here: older backups made before Ember existed just have nothing to
        # restore, silently.
        ember_backup = Path(f"{prefix}-ember.json")
        ember_result = None
        if ember_backup.exists():
            try:
                data = json.loads(ember_backup.read_text())
                ember_result = {"entries": len(data.get("entries", [])), "fasts": len(data.get("fasts", []))}
                if confirm:
                    self._ember_save(data)
            except Exception as ex:
                ember_result = {"error": str(ex)}

        self._send_json(200 if ok else 502, {
            "ok": ok, "wrote": confirm and ok, "raw_output": out, "stderr": err,
            "ember": ember_result})


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"AmbitApp backend bridge running at http://{args.host}:{args.port}/ "
          f"(Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
