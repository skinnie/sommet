"""Runs one tools/*.py for the three local browser tools - the Workout Builder (workout_gui.py),
the App Builder (apps_gui.py) and the Training Calendar (training_calendar_gui.py). One place for
the two things that broke "Install to Watch" there (2026-10-22):

1. Sommet's backend keeps an idle handle on the watch. With libusb-backed hidapi (Ubuntu and Linux
   Mint's python3-hid) that claim is exclusive, so a tool started from here could not open the
   watch while Sommet was running - a Linux Mint user with an Ambit3 Sport only ever got "couldn't
   read the watch's sport modes". A tool that talks to the watch now borrows it from the backend
   first (POST /api/watch/lend), runs pinned to the same watch the backend uses, and hands it back
   (POST /api/watch/return). No backend running: the watch is free, nothing to borrow. A backend
   older than this: no such route, so it runs as before.
2. In the packaged app sys.executable is the bundled helper, not Python, and cannot run a .py file
   by itself: it needs its "--tool" entry (desktop/backend/frozen_entry.py; the standalone
   builders take it through run_tool_entry_if_requested()). Without it, every tool run started a
   second copy of the app instead - the same bug the firmware flasher had.

Stdlib only, like the tools that import it.
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path



def _tools_dir():
    """tools/ itself. In the packaged app this module may be loaded from the bundle's archive
    (every tools/*.py is a hidden import there), so __file__ is not in tools/; the .py files
    are unpacked under sys._MEIPASS/tools (sommet_backend.spec, same rule as frozen_entry.py)."""
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", "")) / "tools"
        if bundled.is_dir():
            return bundled
    return Path(__file__).resolve().parent


TOOLS_DIR = _tools_dir()
BACKEND = "http://127.0.0.1:8766"
# The tools these pages run that open the watch over USB.
WATCH_TOOLS = {"custom_modes.py", "guided_workout.py", "workout_install.py", "training_calendar.py"}


def _post(path, payload, timeout):
    """(HTTP status, JSON body) from the local backend, or (None, None) when it isn't running."""
    req = urllib.request.Request(BACKEND + path, data=json.dumps(payload).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            return e.code, {}
    except (OSError, ValueError):
        return None, None


def tool_command(script, args):
    path = str(TOOLS_DIR / script)
    if getattr(sys, "frozen", False):
        return [sys.executable, "--tool", path, *args]
    return [sys.executable, path, *args]


def run_tool(script, args, timeout=180):
    """Runs tools/<script> with args. Returns (returncode, stdout, stderr); never raises for a
    nonzero exit. When the backend won't lend the watch (Testing mode is on, or it's busy), the
    tool isn't run and stdout carries one JSON line {"ok": false, "error": ..., "fromSommet":
    true} that the pages show as is."""
    env = os.environ.copy()
    token = None
    if script in WATCH_TOOLS:
        status, reply = _post("/api/watch/lend", {"seconds": timeout + 30, "tool": script}, timeout=75)
        if status == 200 and reply and reply.get("token"):
            token = reply["token"]
            if reply.get("productId"):
                env["AMBIT_PRODUCT_ID"] = reply["productId"]
            if reply.get("serial"):
                env["AMBIT_SERIAL"] = reply["serial"]
        elif status in (409, 503):
            error = (reply or {}).get("error") or "Sommet wouldn't hand over the watch just now."
            return 1, json.dumps({"ok": False, "error": error, "fromSommet": True}), ""
        # None (no backend) or anything else (an older backend without this route): run as before.
    try:
        proc = subprocess.run(tool_command(script, args), cwd=TOOLS_DIR, capture_output=True,
                              text=True, timeout=timeout, env=env)
        return proc.returncode, proc.stdout, proc.stderr
    finally:
        if token:
            _post("/api/watch/return", {"token": token}, timeout=10)


def sommet_error(info, fallback):
    """The message to show for a failed tool run: the backend's own (Testing mode, watch busy)
    when it refused the loan, else the page's usual hint."""
    if isinstance(info, dict) and info.get("fromSommet") and info.get("error"):
        return info["error"]
    return fallback


def run_tool_entry_if_requested():
    """`<frozen builder> --tool /path/to/tool.py args...` runs that tool and exits - how
    tool_command() runs tools from a packaged standalone builder. Call first thing in main()."""
    if len(sys.argv) >= 3 and sys.argv[1] == "--tool":
        import runpy
        tool = sys.argv[2]
        tools_dir = str(Path(tool).resolve().parent)
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        sys.argv = [tool] + sys.argv[3:]
        runpy.run_path(tool, run_name="__main__")
        sys.exit(0)
