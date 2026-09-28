#!/usr/bin/env python3
"""Keep SuuntoLink away from the watch's USB (André, 2026-09-28: "suunto link warning should be
present in every firmware flash or even when opening our app").

Why: SuuntoLink (Suunto's own desktop app, macOS/Windows) grabs any Ambit it sees on USB. Worse,
on a Mac it registers launch agents (com.suunto.suuntolink.launcher.*, 17 of them on André's
machine) that auto-start it whenever a watch enumerates - including when the watch re-enumerates
into its bootloader mid-flash. That is what killed a real firmware flash at 23.7% (see the old
issue #14 notes). Sommet can't share the USB with it, so:

  status()   -> is it installed / running / set to auto-launch?  (banner on app open)
  quit_app() -> close it (graceful first, then force)            ("Quit SuuntoLink" button)
  Watchdog   -> during a firmware flash, close it every time it reappears.

Stdlib only (ships inside the frozen backend like every other tools/ module). Linux has no
SuuntoLink; the only thing checked there is a Wine-run Suuntolink.exe.

    ./tools/suuntolink_guard.py            # status as JSON
    ./tools/suuntolink_guard.py --quit
"""
import glob
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

MAC_APP_PATHS = ["/Applications/Suuntolink.app", str(pathlib.Path.home() / "Applications" / "Suuntolink.app")]
MAC_AGENT_GLOBS = [str(pathlib.Path.home() / "Library" / "LaunchAgents" / "com.suunto.*.plist"),
                   "/Library/LaunchAgents/com.suunto.*.plist"]
WIN_EXE = r"%LOCALAPPDATA%\Suuntolink\Suuntolink.exe"


def _run(cmd, timeout=5):
    """(returncode, stdout). Never raises - a missing tool just reads as 'not found'."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def _mac_pids():
    # Exact process name of the Electron main process; its helpers are "Suuntolink Helper ...".
    rc, out = _run(["pgrep", "-x", "Suuntolink"])
    return [int(x) for x in out.split()] if rc == 0 else []


def _win_pids():
    rc, out = _run(["tasklist", "/FI", "IMAGENAME eq Suuntolink.exe", "/FO", "CSV", "/NH"])
    pids = []
    for line in out.splitlines():
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) > 1 and cols[0].lower() == "suuntolink.exe" and cols[1].isdigit():
            pids.append(int(cols[1]))
    return pids


def _linux_pids():
    """A Wine-run Suuntolink.exe, if any. Matches the executable name only - never our own
    tools (suuntolink_guard.py, suuntolink_catalog.py)."""
    pids = []
    for d in glob.glob("/proc/[0-9]*"):
        try:
            args = pathlib.Path(d, "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if any(os.path.basename(a.decode(errors="ignore")).lower() == "suuntolink.exe" for a in args if a):
            pids.append(int(os.path.basename(d)))
    return pids


def _win_autostart():
    try:
        import winreg  # noqa: PLC0415 - Windows only
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
        names, i = [], 0
        while True:
            try:
                name, value, _ = winreg.EnumValue(key, i)
            except OSError:
                break
            if "suunto" in (name + str(value)).lower():
                names.append(name)
            i += 1
        return names
    except (ImportError, OSError):
        return []


def status():
    """{platform, installed, running, pids, installPath, autoLaunch: [...]}"""
    if sys.platform == "darwin":
        path = next((p for p in MAC_APP_PATHS if os.path.isdir(p)), None)
        agents = sorted(os.path.basename(p)[:-len(".plist")] for g in MAC_AGENT_GLOBS for p in glob.glob(g))
        pids = _mac_pids()
        return {"platform": "mac", "installed": bool(path or agents or pids), "running": bool(pids),
                "pids": pids, "installPath": path, "autoLaunch": agents}
    if os.name == "nt":
        exe = os.path.expandvars(WIN_EXE)
        pids = _win_pids()
        return {"platform": "windows", "installed": os.path.isfile(exe) or bool(pids), "running": bool(pids),
                "pids": pids, "installPath": exe if os.path.isfile(exe) else None, "autoLaunch": _win_autostart()}
    pids = _linux_pids()
    return {"platform": "linux", "installed": bool(pids), "running": bool(pids), "pids": pids,
            "installPath": None, "autoLaunch": []}


def quit_app(wait_s=4.0):
    """Close SuuntoLink: politely first (so it saves its state), then by force.
    Returns {wasRunning, stillRunning}."""
    before = status()
    if not before["running"]:
        return {"wasRunning": False, "stillRunning": False}
    if sys.platform == "darwin":
        _run(["osascript", "-e", 'tell application "Suuntolink" to quit'])
    elif os.name == "nt":
        _run(["taskkill", "/IM", "Suuntolink.exe", "/T"])
    deadline = time.time() + wait_s
    while time.time() < deadline and status()["running"]:
        time.sleep(0.25)
    if status()["running"]:
        if sys.platform == "darwin":
            _run(["pkill", "-9", "-f", "/Suuntolink.app/Contents/"])   # main process + helpers
        elif os.name == "nt":
            _run(["taskkill", "/IM", "Suuntolink.exe", "/T", "/F"])
        else:
            for pid in before["pids"]:
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
        time.sleep(0.5)
    return {"wasRunning": True, "stillRunning": status()["running"]}


class Watchdog(threading.Thread):
    """Closes SuuntoLink every time it (re)appears, until stop(). on_event(dict) is called
    from this thread for each closure, so the caller can report it. Polls every 0.3 s:
    SuuntoLink is an Electron app and takes seconds to start before it opens the USB."""

    def __init__(self, on_event=None, interval=0.3):
        super().__init__(daemon=True)
        self._halt = threading.Event()
        self.on_event = on_event
        self.interval = interval
        self.closed = 0

    def run(self):
        while not self._halt.is_set():
            if status()["running"]:
                r = quit_app(wait_s=1.0)
                self.closed += 1
                if self.on_event:
                    self.on_event({"phase": "suuntolink",
                                   "message": "SuuntoLink started and was closed so it can't take the watch's USB"
                                              + ("" if not r["stillRunning"] else " - but it is STILL running"),
                                   "stillRunning": r["stillRunning"]})
            self._halt.wait(self.interval)

    def stop(self):
        self._halt.set()


if __name__ == "__main__":
    print(json.dumps(quit_app() if "--quit" in sys.argv else status()))
