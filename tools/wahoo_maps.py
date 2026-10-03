#!/usr/bin/env python3
"""Fresh OpenStreetMap maps for a Wahoo ELEMNT, built on this computer and installed over the USB
cable - no Wahoo servers (André, 2026-10-03: item 5 of the Wahoo plan, "osm based tiles interest
me, if we can integrate it is nice!").

    ./tools/wahoo_maps.py setup                          # one-time: install the map toolchain
    ./tools/wahoo_maps.py tiles  route.gpx [--margin-km 10]   # which tiles a route needs
    ./tools/wahoo_maps.py build  130/86 131/86           # make the tiles (OSM download + render)
    ./tools/wahoo_maps.py install 130/86 131/86          # put built tiles on the ELEMNT
    ./tools/wahoo_maps.py restore 130/86                 # give a tile back Wahoo's original
    ./tools/wahoo_maps.py status                         # the ELEMNT's tiles, which are Sommet's
    ./tools/wahoo_maps.py update 130/86 --progress F.json  # build + install (Sommet runs this
                                                         # detached and polls F.json)

How the ELEMNT keeps maps (seen 2026-10-03 on André's ELEMNT, BoltApp 1.77.10.1):
  * /sdcard/maps/tiles/8/<x>/<y>.map.lzma - one mapsforge map per zoom-8 slippy tile (about
    150 x 110 km here), lzma-compressed, next to an empty "<y>.map.lzma.<version>" marker (18).
  * It renders from a decompressed copy, /sdcard/maps/temp/z8x<x>y<y>.map, which is removed when
    a tile is replaced so the new one is used.
  * Versions are kept per Wahoo tile PACK in tilepack-versions.json, not per tile, so a replaced
    tile isn't re-downloaded unless Wahoo publishes a newer pack (theirs are from 2019).
The tiles are made by wahooMapsCreator (github.com/treee111/wahooMapsCreator, GPL-3.0, used as an
external program, not copied): Geofabrik extracts + land polygons -> osmium tag filter -> osmosis
mapsforge-map-writer with Wahoo's tag mapping -> lzma. Its data (several GB) and toolchain live in
SOMMET_WAHOOMC (default ../dev-toolchain/wahoomc next to the repo), not in the home folder.
Wahoo's original tile is moved aside on the ELEMNT (/sdcard/maps/sommet-orig/8/...) - `restore`
moves it back. USB only: a tile is 2-15 MB, which the Bluetooth file channel can't move in
reasonable time.
"""

import argparse
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wahoo_pages as P        # noqa: E402 - shared adb helpers

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLCHAIN = os.environ.get("SOMMET_WAHOOMC") or os.path.join(os.path.dirname(REPO), "dev-toolchain", "wahoomc")
JDK = os.path.join(os.path.dirname(REPO), "dev-toolchain", "jdk17", "bin")
WAHOOMC_VERSION = "4.4.0"
OSMOSIS_URL = "https://github.com/openstreetmap/osmosis/releases/download/0.49.2/osmosis-0.49.2.zip"
MAPWRITER = "mapsforge-map-writer-0.21.0-jar-with-dependencies.jar"
MAPWRITER_URL = ("https://search.maven.org/remotecontent?filepath=org/mapsforge/mapsforge-map-writer/"
                 "0.21.0/" + MAPWRITER)

SD_TILES = "/sdcard/maps/tiles/8"
SD_ORIG = "/sdcard/maps/sommet-orig/8"
SD_TEMP = "/sdcard/maps/temp"
DEFAULT_MARKER = 18
ZOOM = 8


def _log(msg):
    print("wahoo_maps: %s" % msg, file=sys.stderr, flush=True)


# ---- tiles ------------------------------------------------------------------------------------

def tile_of(lat, lon):
    n = 2 ** ZOOM
    x = int((lon + 180.0) / 360.0 * n)
    r = math.radians(max(-85.05, min(85.05, lat)))
    y = int((1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * n)
    return min(n - 1, max(0, x)), min(n - 1, max(0, y))


def tile_bounds(x, y):
    """(south, west, north, east) of a zoom-8 tile."""
    n = 2 ** ZOOM
    def lat(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))
    return lat(y + 1), x / n * 360.0 - 180.0, lat(y), (x + 1) / n * 360.0 - 180.0


def gpx_points(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    pts = [(float(a), float(b)) for a, b in
           re.findall(r'<(?:trkpt|rtept|wpt)[^>]*?lat="([-\d.]+)"[^>]*?lon="([-\d.]+)"', text)]
    pts += [(float(b), float(a)) for a, b in
            re.findall(r'<(?:trkpt|rtept|wpt)[^>]*?lon="([-\d.]+)"[^>]*?lat="([-\d.]+)"', text)]
    return pts


def tiles_for_points(points, margin_km=10.0):
    """Zoom-8 tiles touched by the points or within margin_km of them."""
    out = set()
    for lat, lon in points:
        dlat = margin_km / 111.0
        dlon = margin_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
        for la in (lat - dlat, lat, lat + dlat):
            for lo in (lon - dlon, lon, lon + dlon):
                out.add(tile_of(la, lo))
    return sorted(out)


def parse_tiles(args):
    out = []
    for a in args:
        for part in a.split(","):
            m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", part)
            if not m:
                raise ValueError("tiles are written x/y, e.g. 130/86 (got %r)" % part)
            x, y = int(m.group(1)), int(m.group(2))
            if not (0 <= x < 256 and 0 <= y < 256):
                raise ValueError("tile %s out of range" % part)
            out.append((x, y))
    return out


# ---- toolchain --------------------------------------------------------------------------------

def _env():
    env = dict(os.environ)
    env["HOME"] = os.path.join(TOOLCHAIN, "home")     # wahoomc keeps its data + plugin under HOME
    env["PATH"] = os.pathsep.join([os.path.join(TOOLCHAIN, "bin"), JDK, env.get("PATH", "")])
    # Java ignores $HOME (it asks the system account), so point osmosis at the plugin folder.
    env["OSMOSIS_OPTS"] = "-Duser.home=" + os.path.join(TOOLCHAIN, "home")
    return env


def toolchain_ready():
    need = [os.path.join(TOOLCHAIN, "venv", "bin", "python"), os.path.join(TOOLCHAIN, "bin", "python"),
            os.path.join(TOOLCHAIN, "bin", "osmium"),
            os.path.join(TOOLCHAIN, "bin", "osmosis"),
            os.path.join(TOOLCHAIN, "home", ".openstreetmap", "osmosis", "plugins", MAPWRITER)]
    missing = [p for p in need if not os.path.exists(p)]
    for prog in ("ogr2ogr", "lzma"):
        if not shutil.which(prog):
            missing.append(prog)
    if not (shutil.which("java") or os.path.exists(os.path.join(JDK, "java"))):
        missing.append("java")
    return missing


def setup():
    """Install wahooMapsCreator's toolchain into TOOLCHAIN without root: a venv (with the system
    GDAL bindings), osmium-tool unpacked from the distribution's package, osmosis's release zip,
    the mapsforge writer plugin. ogr2ogr (gdal-bin), lzma and java must already be installed."""
    os.makedirs(os.path.join(TOOLCHAIN, "bin"), exist_ok=True)
    plugins = os.path.join(TOOLCHAIN, "home", ".openstreetmap", "osmosis", "plugins")
    os.makedirs(plugins, exist_ok=True)
    venv = os.path.join(TOOLCHAIN, "venv")
    if not os.path.exists(os.path.join(venv, "bin", "python")):
        _log("creating the Python environment")
        subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", venv], check=True)
    subprocess.run([os.path.join(venv, "bin", "pip"), "install", "-q", "wahoomc==" + WAHOOMC_VERSION,
                    "geojson", "shapely", "requests"], check=True)
    osmium = os.path.join(TOOLCHAIN, "bin", "osmium")
    if not os.path.exists(osmium):
        _log("unpacking osmium-tool")
        debs = os.path.join(TOOLCHAIN, "debs")
        os.makedirs(debs, exist_ok=True)
        subprocess.run(["apt-get", "download", "osmium-tool"], cwd=debs, check=True)
        deb = sorted(glob.glob(os.path.join(debs, "osmium-tool_*.deb")))[-1]
        subprocess.run(["dpkg", "-x", deb, os.path.join(TOOLCHAIN, "root")], check=True)
        os.symlink(os.path.join(TOOLCHAIN, "root", "usr", "bin", "osmium"), osmium)
    # wahooMapsCreator runs its helper scripts with a bare "python".
    py = os.path.join(TOOLCHAIN, "bin", "python")
    if not os.path.lexists(py):
        os.symlink(os.path.join(venv, "bin", "python"), py)
    osmosis = os.path.join(TOOLCHAIN, "bin", "osmosis")
    if not os.path.exists(osmosis):
        _log("downloading osmosis")
        z = os.path.join(TOOLCHAIN, "osmosis.zip")
        urllib.request.urlretrieve(OSMOSIS_URL, z)
        subprocess.run(["unzip", "-q", "-o", z, "-d", TOOLCHAIN], check=True)
        os.symlink(os.path.join(TOOLCHAIN, "osmosis-0.49.2", "bin", "osmosis"), osmosis)
    jar = os.path.join(plugins, MAPWRITER)
    if not os.path.exists(jar):
        _log("downloading the mapsforge map writer")
        urllib.request.urlretrieve(MAPWRITER_URL, jar)
    missing = toolchain_ready()
    if missing:
        raise RuntimeError("still missing: " + ", ".join(missing))
    return {"toolchain": TOOLCHAIN}


def _built_path(x, y):
    return os.path.join(TOOLCHAIN, "home", "wahooMapsCreatorData", "_tiles", str(x), "%d.map.lzma" % y)


def build(tiles, force=False):
    missing = toolchain_ready()
    if missing:
        raise RuntimeError("the map toolchain isn't installed (%s) - run: wahoo_maps.py setup"
                           % ", ".join(missing))
    cmd = [os.path.join(TOOLCHAIN, "venv", "bin", "python"), "-m", "wahoomc", "cli",
           "-xy", ",".join("%d/%d" % t for t in tiles), "-md", "30"]
    if force:
        cmd.append("-fp")
    _log("building %d tile(s): %s" % (len(tiles), " ".join(cmd[-3:])))
    started = time.time()
    proc = subprocess.run(cmd, env=_env(), cwd=TOOLCHAIN, stdin=subprocess.DEVNULL,
                          stdout=sys.stderr, stderr=sys.stderr)
    out = []
    for x, y in tiles:
        p = _built_path(x, y)
        if not os.path.exists(p) or (force and os.path.getmtime(p) < started - 5):
            raise RuntimeError("wahooMapsCreator didn't produce tile %d/%d (exit %d)" % (x, y, proc.returncode))
        out.append({"tile": "%d/%d" % (x, y), "file": p, "bytes": os.path.getsize(p),
                    "built": int(os.path.getmtime(p))})
    return out


# ---- device -----------------------------------------------------------------------------------

def _sh(serial, cmd, timeout=60):
    return P._adb("shell", cmd, serial=serial, timeout=timeout)[1]


def _marker_version(serial, x, y):
    names = _sh(serial, "ls %s/%d/" % (SD_TILES, x)).split()
    for n in names:
        m = re.fullmatch(r"%d\.map\.lzma\.(\d+)" % y, n.strip())
        if m:
            return int(m.group(1))
    return None


def status(serial):
    # The ELEMNT's ls prints no folder headers for several folders, so echo them.
    listing = _sh(serial, 'for d in %s/* %s/*; do [ -d "$d" ] && echo "$d:" && ls -l "$d"; done'
                  % (SD_TILES, SD_ORIG), timeout=120)
    tiles, orig, cur = {}, set(), None
    for line in listing.splitlines():
        line = line.strip()
        if line.endswith(":") and line.startswith("/"):
            cur = line[:-1].rstrip("/")
            continue
        m = re.search(r"\s(\d+)\s+(\d{4}-\d\d-\d\d \d\d:\d\d)\s+(\d+)\.map\.lzma$", line)
        if not m or cur is None:
            continue
        x, y = int(cur.rsplit("/", 1)[1]), int(m.group(3))
        if cur.startswith(SD_ORIG):
            orig.add((x, y))
        else:
            tiles[(x, y)] = {"tile": "%d/%d" % (x, y), "bytes": int(m.group(1)), "date": m.group(2)}
    for k, v in tiles.items():
        v["sommet"] = k in orig
        s, w, n, e = tile_bounds(*k)
        v["bounds"] = [round(s, 3), round(w, 3), round(n, 3), round(e, 3)]
    return sorted(tiles.values(), key=lambda t: t["tile"])


def install(serial, tiles):
    done = []
    for x, y in tiles:
        local = _built_path(x, y)
        if not os.path.exists(local):
            raise RuntimeError("tile %d/%d isn't built yet - build it first" % (x, y))
        ver = _marker_version(serial, x, y) or DEFAULT_MARKER
        tmp = "/sdcard/maps/sommet-upload.map.lzma"
        _log("copying tile %d/%d (%.1f MB)" % (x, y, os.path.getsize(local) / 1e6))
        if P._adb("push", local, tmp, serial=serial, timeout=900)[0] != 0:
            raise RuntimeError("adb push failed for tile %d/%d" % (x, y))
        tile = "%s/%d/%d.map.lzma" % (SD_TILES, x, y)
        orig = "%s/%d/%d.map.lzma" % (SD_ORIG, x, y)
        # Keep Wahoo's own tile once (the first time only), then swap in place (same filesystem:
        # renames are instant), clear the old marker and the decompressed render copy.
        _sh(serial, "mkdir -p %s/%d %s/%d; if [ -f %s ] && [ ! -f %s ]; then mv %s %s; fi; "
                    "rm -f %s.*; mv %s %s; touch %s.%d; rm -f %s/z8x%dy%d.map"
            % (SD_TILES, x, SD_ORIG, x, tile, orig, tile, orig, tile, tmp, tile, tile, ver, SD_TEMP, x, y))
        size = _sh(serial, "ls -l %s" % tile).split()
        if len(size) < 4 or int(size[3]) != os.path.getsize(local):
            raise RuntimeError("tile %d/%d didn't land on the ELEMNT intact" % (x, y))
        done.append({"tile": "%d/%d" % (x, y), "bytes": os.path.getsize(local), "marker": ver})
    return done


def restore(serial, tiles):
    done = []
    for x, y in tiles:
        tile = "%s/%d/%d.map.lzma" % (SD_TILES, x, y)
        orig = "%s/%d/%d.map.lzma" % (SD_ORIG, x, y)
        if "No such file" in _sh(serial, "ls %s" % orig):
            raise RuntimeError("no Wahoo original kept for tile %d/%d" % (x, y))
        _sh(serial, "mv %s %s; rm -f %s/z8x%dy%d.map" % (orig, tile, SD_TEMP, x, y))
        done.append("%d/%d" % (x, y))
    return done


def _progress(path, **state):
    if not path:
        return
    state["time"] = int(time.time())
    with open(path + ".tmp", "w") as fh:
        json.dump(state, fh)
    os.replace(path + ".tmp", path)


def update(tiles, progress=None, force=False):
    """Build the tiles, then install them - the one long job the app starts and polls."""
    names = ["%d/%d" % t for t in tiles]
    try:
        _serial()                              # fail fast, before an hour of rendering
        _progress(progress, stage="building", tiles=names)
        built = build(tiles, force)
        _progress(progress, stage="installing", tiles=names)
        installed = install(_serial(), tiles)
        _progress(progress, stage="done", tiles=names, installed=installed)
        return {"built": built, "installed": installed}
    except Exception as e:                     # noqa: BLE001 - reported through the progress file
        _progress(progress, stage="error", tiles=names, error=str(e))
        raise


def _serial():
    s = P.find_serial()
    if not s:
        raise RuntimeError("No Wahoo ELEMNT on adb - maps go over the USB cable (press power twice "
                           "and re-plug it)")
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["setup", "tiles", "build", "install", "update", "restore", "status",
                                       "check"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--margin-km", type=float, default=10.0)
    ap.add_argument("--force", action="store_true", help="build: re-render even if up to date")
    ap.add_argument("--progress", help="update: JSON file to keep the job's stage in")
    a = ap.parse_intermixed_args()
    try:
        if a.command == "setup":
            out = setup()
        elif a.command == "check":
            missing = toolchain_ready()
            out = {"ready": not missing, "missing": missing, "toolchain": TOOLCHAIN}
        elif a.command == "tiles":
            pts = gpx_points(a.args[0])
            if not pts:
                raise ValueError("no points in %s" % a.args[0])
            out = {"tiles": ["%d/%d" % t for t in tiles_for_points(pts, a.margin_km)]}
        elif a.command == "build":
            out = {"built": build(parse_tiles(a.args), a.force)}
        elif a.command == "update":
            out = update(parse_tiles(a.args), a.progress, a.force)
        elif a.command == "install":
            out = {"installed": install(_serial(), parse_tiles(a.args))}
        elif a.command == "restore":
            out = {"restored": restore(_serial(), parse_tiles(a.args))}
        else:
            out = {"tiles": status(_serial())}
        out["ok"] = True
    except Exception as e:                     # noqa: BLE001 - one JSON error line for the UI
        out = {"ok": False, "error": str(e)}
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
