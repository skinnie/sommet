#!/usr/bin/env python3
"""Linux only: turns Bluetooth privacy on while a watch pairs, then off again (André,
2026-10-26: "do it but document it well on the read me").

Why: with BlueZ's default `Privacy = off` the kernel gives the watch no identity when
pairing (net/bluetooth/smp.c adds SMP_DIST_ID_KEY only with HCI_PRIVACY). The Ambit3 then
files the pairing under MAC 00:00:00:00:00:00 - read off André's Peak over USB - and refuses
the computer once the link drops. Paired with privacy on, the watch keeps the computer's real
address, and reconnects work afterwards with privacy off again (verified on the Peak,
2026-10-26: reconnected without a code after a drop and a reboot).

Changing privacy needs root and a powered-off adapter, so one `pkexec` prompt runs a short
bash script as root: privacy on (unless it already is), then it waits on its stdin; a line or
EOF (Sommet quitting) or 30 minutes turns privacy off again. Each change switches Bluetooth
off for about a second, so a mouse or keyboard reconnects.

    ./tools/bt_privacy.py check     -> {"ok", "needed", "reason"}   (read-only)
    ./tools/bt_privacy.py window    turn it on, wait for Enter, turn it off (by hand)
    ./tools/bt_privacy.py always    -> {"ok", "always"}             (read-only)

Always on (André, 2026-11-22: the watch's offer to pair is shorter than typing a password): the
system's own setting, `Privacy = device` in /etc/bluetooth/main.conf, changed once through
set_always() with one password prompt. A pairing then needs no prompt at all. The price is in
the how-to: a Bluetooth mouse or keyboard paired before may need pairing again.
"""

import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import threading
import time

MAX_WINDOW_S = 1800         # the root script turns privacy off by itself after this

# The root script, its own file so the password prompt names it ("sommet-bluetooth-privacy")
# instead of /bin/bash. Runs under pkexec; prints ALREADY | ON | FAILED, then OFF at the end.
ROOT_SCRIPT = pathlib.Path(__file__).resolve().parent / "sommet-bluetooth-privacy"
ALWAYS_SCRIPT = pathlib.Path(__file__).resolve().parent / "sommet-bluetooth-privacy-always"
MAIN_CONF = pathlib.Path("/etc/bluetooth/main.conf")
# Where sommet-polkit-install puts root-owned copies, with polkit's description of them: run from
# there, the password window says why Sommet asks instead of showing a path (André, 2026-11-24).
INSTALLED_DIR = pathlib.Path("/usr/local/libexec/sommet")
POLICY_FILE = pathlib.Path("/usr/share/polkit-1/actions/io.github.skinnie.sommet.policy")


def script_path(bundled):
    """The installed copy of a root script when it is the same file, owned by root and
    described to polkit; otherwise the one in Sommet's package (which installs itself when run)."""
    installed = INSTALLED_DIR / bundled.name
    try:
        st = installed.stat()
        if (POLICY_FILE.exists() and st.st_uid == 0 and not st.st_mode & 0o022
                and installed.read_bytes() == bundled.read_bytes()):
            return installed
    except OSError:
        pass
    return bundled


def always_on(conf=None):
    """True when the system keeps Bluetooth privacy on (`Privacy = device` or `on`, not
    commented out, in main.conf). Read-only, no root."""
    import re                                                # noqa: PLC0415
    try:
        text = pathlib.Path(conf or MAIN_CONF).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return re.search(r"^[ \t]*Privacy[ \t]*=[ \t]*(device|on|network/on|limited-network)\b", text,
                     re.M | re.I) is not None


def set_always(on, prompt_timeout=180):
    """Turns the system's setting on or off: one password prompt (pkexec), Bluetooth restarts.
    Returns {"ok", "always", "error"?}. Blocks while the prompt is up."""
    if platform.system() != "Linux":
        return {"ok": False, "always": False, "error": "this is a Linux setting"}
    if not shutil.which("pkexec"):
        return {"ok": False, "always": always_on(), "error": "pkexec is not installed"}
    try:
        done = subprocess.run(["pkexec", str(script_path(ALWAYS_SCRIPT)), "on" if on else "off"],
                              capture_output=True, text=True, timeout=prompt_timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "always": always_on(), "error": "the password was not typed in time"}
    except OSError as exc:
        return {"ok": False, "always": always_on(), "error": f"could not start pkexec ({exc})"}
    word = (done.stdout or "").strip().splitlines()[-1:] or [""]
    if done.returncode == 126:
        return {"ok": False, "always": always_on(), "error": "cancelled"}
    if word[0] not in ("ON", "OFF"):
        return {"ok": False, "always": always_on(),
                "error": "the Bluetooth setting could not be changed"}
    return {"ok": True, "always": word[0] == "ON"}


def suunto_paired():
    """True when this computer already holds a Suunto watch pairing (a reconnect, not a
    fresh pairing). bt_bonds.py's own rule for what counts as Suunto."""
    import bt_bonds                                          # noqa: PLC0415
    try:
        return any(d["kind"] == "suunto" for d in bt_bonds.list_devices())
    except (OSError, subprocess.SubprocessError):
        return False


def check():
    """(needed, reason): whether a pairing about to start should get the privacy window."""
    if platform.system() != "Linux":
        return False, "not Linux"
    for tool in ("pkexec", "btmgmt", "bluetoothctl"):
        if not shutil.which(tool):
            return False, f"{tool} is not installed"
    if suunto_paired():
        return False, "a Suunto watch is already paired with this computer"
    if always_on():
        return False, "Bluetooth privacy is always on on this computer"
    return True, "fresh pairing on Linux"


NOTICE = ("Type your password first, then choose Pair with app on the watch. Sommet needs it "
          "once, so the watch remembers this computer. Bluetooth may blink off for a second.")


def notify():
    """A desktop notification saying why the password prompt is coming (best-effort)."""
    if shutil.which("notify-send"):
        try:
            # Without the package's own library paths: inside the AppImage they made the
            # system's notify-send fail (P5Q, 2026-11-24: "symbol lookup error").
            env = {k: v for k, v in os.environ.items()
                   if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH",
                                "GI_TYPELIB_PATH", "GDK_PIXBUF_MODULE_FILE", "GTK_PATH",
                                "QT_PLUGIN_PATH", "XDG_DATA_DIRS")}
            subprocess.run(["notify-send", "-a", "Sommet", "-i", "bluetooth",
                            "Sommet: pairing your watch", NOTICE], timeout=5, env=env)
        except (OSError, subprocess.SubprocessError):
            pass


def _kill(proc):
    """Once the password is typed, pkexec runs the script as root and this process may not
    signal it (PermissionError). The script still ends on its own: stdin closed or 30 min."""
    try:
        proc.kill()
    except OSError:
        pass


class PrivacyWindow:
    """One pkexec'd root script. open() blocks until privacy is on (or the prompt is
    cancelled); close() turns it off again. Safe to close() twice."""

    def __init__(self, log=print):
        self.log = log
        self.proc = None
        self.state = "idle"     # on | already | declined | failed | unavailable | off

    def open(self, prompt_timeout=120):
        btmgmt = shutil.which("btmgmt")
        notify()
        try:
            self.proc = subprocess.Popen(
                ["pkexec", str(script_path(ROOT_SCRIPT)), btmgmt],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)
        except OSError as exc:
            self.state = "unavailable"
            self.log(f"Bluetooth privacy: could not start pkexec ({exc})")
            return self.state
        line = [None]
        reader = threading.Thread(target=lambda: line.__setitem__(0, self.proc.stdout.readline()),
                                  daemon=True)
        reader.start()
        reader.join(prompt_timeout)
        word = (line[0] or "").strip()
        if word in ("ON", "ALREADY", "FAILED"):
            self.state = {"ON": "on", "ALREADY": "already", "FAILED": "failed"}[word]
            if word == "ALREADY":                       # nothing to undo; the script has ended
                self.proc.wait(timeout=10)
                self.proc = None
        else:
            # Cancelled prompt (pkexec exits 126), no polkit agent (127), or no answer.
            code = self.proc.poll()
            if code is None and line[0] == "":
                # Its output closed (EOF), so it is exiting: wait for the code. Asking poll()
                # at once could beat the exit, and a cancelled prompt read as "unavailable"
                # (seen 2026-10-27 as test_bt_privacy failing about one run in three).
                try:
                    code = self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    code = None
            self.state = "declined" if code == 126 else "unavailable"
            if code is None:
                _kill(self.proc)
            self.proc = None
        self.log({"on": "Bluetooth privacy on for this pairing",
                  "already": "Bluetooth privacy was already on - leaving it as it is",
                  "failed": "Bluetooth privacy did not turn on - the watch may not reconnect "
                            "by itself after this pairing",
                  "declined": "Bluetooth privacy: password prompt cancelled - pairing without "
                              "it; the watch may not reconnect by itself (README: Linux "
                              "Bluetooth)",
                  "unavailable": "Bluetooth privacy: no password prompt available - pairing "
                                 "without it (README: Linux Bluetooth)"}[self.state])
        return self.state

    def close(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.stdin.write("\n")
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=40)
        except subprocess.TimeoutExpired:
            _kill(proc)
        if self.state == "on":
            self.log("Bluetooth privacy off again")
        self.state = "off"


def run_window(window, status, restart, on_done=lambda: None, poll_s=2.0, idle_s=1200):
    """Closes `window` at the right moment, from a background thread. `status()` = the
    daemon's status dict; `restart()` restarts the daemon after the adapter power-cycle.
    Closes when: the paired watch's link drops by itself after a session; the daemon stops;
    no session within `idle_s`; or MAX_WINDOW_S - 60 s overall."""
    def watch():
        began = time.monotonic()
        had_session = False
        while window.proc is not None:
            time.sleep(poll_s)
            s = status()
            if not s.get("running"):
                break
            had_session = had_session or bool(s.get("handshake_done"))
            if had_session and not s.get("linked_device"):
                break
            age = time.monotonic() - began
            if (not had_session and age > idle_s) or age > MAX_WINDOW_S - 60:
                break
        cycled = window.state in ("on", "failed")       # turning it off power-cycles the adapter
        was_running = bool(status().get("running"))
        window.close()
        if cycled and was_running:
            restart()
        on_done()
    t = threading.Thread(target=watch, daemon=True)
    t.start()
    return t


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    if a == ["check"]:
        needed, reason = check()
        print(json.dumps({"ok": True, "needed": needed, "reason": reason}))
        return 0
    if a == ["window"]:
        w = PrivacyWindow()
        if w.open() in ("on", "already", "failed"):
            input("Pair the watch now, then press Enter to turn privacy off again... ")
            w.close()
        return 0
    if a == ["always"]:
        print(json.dumps({"ok": True, "always": always_on()}))
        return 0
    print("usage: bt_privacy.py check | window | always")
    return 1


if __name__ == "__main__":
    sys.exit(main())
