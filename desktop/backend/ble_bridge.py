#!/usr/bin/env python3
"""Desktop-app side of the Bluetooth bridge - talks to `tools/ble_server.py listen`, the
process that actually holds the watch's BLE (GATT server) connection open.

WHY THIS EXISTS. `server.py`'s other endpoints work by running a CLI tool
(`write_nav.py`, `sgee.py`, ...) as a fresh subprocess per HTTP request, each one opening
its own USB connection and exiting - fine for a cable that's just always there. BLE can't
work that way: the watch's advertising window is short, the GATT server + D-Bus event loop
have to stay running across many commands, and only one process can host that server at a
time. So `ble_server.py` stays a single long-lived daemon (started once, kept running across
many requests) instead of a per-call subprocess, and this module is the thin client that
talks to it over the Unix control socket `ble_server.py`'s `ControlSocketServer` exposes.
Same shape as the Android side of this project: `AmbitBleModule` owns one persistent
connection, and every other call (`AmbitBleDeviceProvider`, in-app `getLogs`/etc.) just
reaches into it - a Unix socket standing in here for that shared in-process state.

Linux: `ble_server.py` (BlueZ over D-Bus). Windows: `ble_server_win.py` (Windows' own GATT
server through WinRT, 2026-11-21), same requests over TCP instead of a Unix socket. macOS has
no daemon yet (CoreBluetooth would be its own back end behind this same client interface).

DRY-RUN BY DEFAULT, matching every other watch-writing path in this project
(`write_nav.py`, `sgee.py`, `server.py`'s own WATCH_LOCK'd endpoints): connecting only reads
(`command()` calls made read-only unless `confirm: true` is threaded through, same contract
`server.py` already uses for USB). `set_dry_run(False)` is the explicit, one-place opt-in.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# Frozen (the packaged download): tools/ is unpacked under sys._MEIPASS and PYTHON is the
# backend's own executable, which runs a script only behind its "--tool" sentinel
# (frozen_entry.py) - the same rule as server.py's run_tool(). Before 2026-11-21 this file
# ran "<exe> -u <repo>/tools/ble_server.py", which in a download is neither a tool call nor a
# real path.
FROZEN = getattr(sys, "frozen", False)
if FROZEN:
    TOOLS_DIR = Path(sys._MEIPASS) / "tools"  # type: ignore[attr-defined]
else:
    TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
PYTHON = sys.executable

# Windows has its own daemon (tools/ble_server_win.py, Windows' Bluetooth through WinRT) and no
# Unix sockets in its Python: the control socket is TCP on 127.0.0.1, its port and a token in
# PORT_FILE (same requests otherwise).
WINDOWS = os.name == "nt"
DAEMON_SCRIPT = "ble_server_win.py" if WINDOWS else "ble_server.py"
PORT_FILE = Path.home() / ".cache" / "Sommet" / "ble.port"

LOG_ROLL_BYTES = 5 * 1024 * 1024

# Must match tools/ble_server.py's SOCKET_PATH exactly - not re-derived, so the two can never
# drift apart.
SOCKET_PATH = Path.home() / ".cache" / "Sommet" / "ble.sock"

# How long to wait for the daemon subprocess to bind its control socket after being spawned.
# Local process startup (Python interpreter + dbus/GLib imports), not a BLE operation - a few
# seconds is generous, not a value tuned against the watch's own timing.
_STARTUP_TIMEOUT_S = 10.0


def _detached():
    """Popen options that let the daemon outlive whatever started it. Real bug, hit live
    2026-08-11 testing a fresh pairing: in the SAME process group as the shell that launched it,
    the daemon died with that shell mid-pairing. POSIX: its own session. Windows: its own
    process group, and no console window flashing up."""
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def _daemon_endpoint_exists():
    return (PORT_FILE if WINDOWS else SOCKET_PATH).exists()


def _open_control():
    """A connected socket to the daemon, and the token every request carries (Windows)."""
    if WINDOWS:
        try:
            info = json.loads(PORT_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BleBridgeError(f"BLE daemon is not running ({exc})") from exc
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        return sock, ("127.0.0.1", int(info["port"])), info.get("token", "")
    return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM), str(SOCKET_PATH), None


# ---- Linux, packaged download: the daemon runs on the computer's own Python ------------------
#
# tools/ble_server.py talks to BlueZ over D-Bus with the `dbus` and `gi` modules. They are not
# pip packages that freeze well: they belong to the system (python3-dbus, python3-gi, present on
# the usual desktop installs) and are tied to its D-Bus and GLib. The packaged backend never
# held them, so in the Linux download the daemon died on "No module named 'dbus'" right after
# the password prompt, and Bluetooth only ever worked from a source checkout (P5Q, Mint,
# 2026-11-22: "it asks me sudo password and then it fails"). So the packaged backend starts
# the daemon with the system's Python, which has them, on the tools/ it ships. The daemon needs
# nothing else: the standard library and its own tools/ modules.
LINUX_FROZEN = FROZEN and sys.platform.startswith("linux")
DBUS_MODULES_HINT = (
    "Bluetooth on Linux needs two system packages that are not installed here: python3-dbus "
    "and python3-gi. On Debian, Ubuntu or Mint: sudo apt install python3-dbus python3-gi. "
    "On Fedora: sudo dnf install python3-dbus python3-gobject. Then try again. The cable "
    "works without them.")
_SYSTEM_PYTHON = []          # [path or None], filled on first use

# Linux: the system's Bluetooth service (BlueZ) can be up and still answer nothing. Seen on the
# X230, 2026-11-23: bluetoothd held its bus name and took every request, and no reply ever
# came back (the system message bus had stopped reading from it, an hour before anyone
# paired). Sommet then asked for the password twice, switched the adapter off and on four
# times, started a daemon that died in find_adapter, ran a 90 s Magene read every 90 s for an
# hour, and told the user the watch could not be found. One short Ping answers the question
# first.
BLUETOOTH_SILENT_HINT = (
    "Bluetooth on this computer is not answering: its system service is running but stuck, so "
    "no app can use Bluetooth right now. Restart Bluetooth (in a terminal: sudo systemctl "
    "restart bluetooth) or restart the computer, then try again. The cable works meanwhile.")
_BLUEZ_PING = {"at": None, "silent": False}
_BLUEZ_PING_LOCK = threading.Lock()
_NO_REPLY_WORDS = ("timed out", "did not receive a reply", "timeout was reached", "noreply")


def _bluez_ping(timeout):
    """True = BlueZ took a Ping and did not answer within `timeout` s. False = it answered, is
    not there at all (another error, with other words for the user), or nothing here can ask."""
    env = _system_env()
    asks = (
        ["busctl", "--system", "--auto-start=no", f"--timeout={timeout:g}", "call", "org.bluez",
         "/org/bluez", "org.freedesktop.DBus.Peer", "Ping"],
        ["dbus-send", "--system", "--dest=org.bluez", "--print-reply",
         f"--reply-timeout={int(timeout * 1000)}", "/org/bluez", "org.freedesktop.DBus.Peer.Ping"],
    )
    for argv in asks:
        if shutil.which(argv[0], path=env["PATH"]) is None:
            continue
        try:
            done = subprocess.run(argv, env=env, timeout=timeout + 4, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True)
        except (OSError, subprocess.SubprocessError):
            return False                # the asking itself went wrong: not a finding
        if done.returncode == 0:
            return False
        words = (done.stderr + done.stdout).lower()
        return any(w in words for w in _NO_REPLY_WORDS)
    return False


def bluetooth_silent(max_age=15.0, timeout=3.0):
    """Linux only: True when the system's Bluetooth service takes a request and never answers.
    The last answer is reused for `max_age` seconds, so a page that polls costs one Ping per
    `max_age`, and while Bluetooth is stuck nothing else is sent to it."""
    if not sys.platform.startswith("linux"):
        return False
    with _BLUEZ_PING_LOCK:
        now = time.monotonic()
        if _BLUEZ_PING["at"] is None or now - _BLUEZ_PING["at"] > max_age:
            _BLUEZ_PING["silent"] = _bluez_ping(timeout)
            _BLUEZ_PING["at"] = time.monotonic()
        return _BLUEZ_PING["silent"]


def _system_env():
    """The environment for the system's Python: none of the packaged app's library paths
    (LD_LIBRARY_PATH points into the download, PYTHONHOME into the backend), which would make
    it load the wrong GLib or no standard library at all."""
    keep = ("HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "TZ",
            "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    return env


def system_python_with_dbus():
    """The system's Python 3 that can run the daemon (it imports dbus and GLib), or None."""
    if _SYSTEM_PYTHON:
        return _SYSTEM_PYTHON[0]
    found = None
    probe = "import dbus, dbus.mainloop.glib, dbus.service; from gi.repository import GLib"
    for candidate in ("/usr/bin/python3", shutil.which("python3", path=_system_env()["PATH"])):
        if not candidate or not os.path.exists(candidate):
            continue
        try:
            ok = subprocess.run([candidate, "-c", probe], env=_system_env(), timeout=20,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            found = candidate
            break
    _SYSTEM_PYTHON.append(found)
    return found


def daemon_command(script):
    """(argv before "listen", environment) to start the daemon script, for this kind of install.
    Raises BleBridgeError with words for the user when this computer cannot run it."""
    extra = {"PYTHONUNBUFFERED": "1",
             # the Windows service stops when this backend has gone
             "SOMMET_PARENT_PID": str(os.getpid())}
    if LINUX_FROZEN:
        python = system_python_with_dbus()
        if python is None:
            raise BleBridgeError(DBUS_MODULES_HINT)
        return [python, "-u", script], {**_system_env(), **extra}
    if FROZEN:
        return [PYTHON, "--tool", script], {**os.environ, **extra}
    return [PYTHON, "-u", script], {**os.environ, **extra}


class BleBridgeError(RuntimeError):
    """Raised for anything the socket protocol itself reports as a failure - a malformed
    request, a command timeout relayed from ServerLink, or the daemon not being reachable at
    all. Callers (server.py's HTTP handlers) catch this the same way they already catch
    subprocess failures from run_tool()."""


class BleBridge:
    """Owns the `ble_server.py listen` subprocess and the socket connection to it.

    One instance per backend process (see the module-level `bridge` singleton below) -
    there is exactly one BLE connection to the watch at a time, same real constraint
    PROJECT_RULES.md notes for pairing itself ("these watches can only be paired with one
    device/app at a time").
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._proc = None
        self._log_path = Path.home() / ".cache" / "Sommet" / "ble_daemon.log"
        # Real bug, found live 2026-08-11 wiring routes/settings: write_nav.py's own
        # functions (read_pois(), send_plan(), main()'s own summary print) read
        # `link.dry_run`/`link.sent` as PLAIN ATTRIBUTES - the real shape `write_nav.Link`
        # and `ble_server.ServerLink` both have - not through a method call. This class
        # only exposed `set_dry_run()`, so any of those functions crashed with
        # AttributeError the first time one touched `link.dry_run` directly. `dry_run`
        # mirrors the daemon's own state locally (set_dry_run() keeps it in sync); `sent`
        # is a real per-command log matching Link's own (command, payload, raw-bytes)
        # triples, close enough for the byte-count summaries that read it - the actual
        # wire bytes were already sent by the daemon, this is bookkeeping, not the
        # transport itself.
        self.dry_run = True
        # About 1 KB a second: readers that can read less do (write_nav.read_nav_flash reads
        # only the used part of the routes database instead of its whole 146 KB).
        self.slow_link = True
        self.sent = []

    def is_running(self):
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def start(self, forget=False, verbose=True):
        """Spawns the daemon if it isn't already running. Idempotent - a second call while
        one is already up is a no-op, matching `connect()`'s use as "make sure we're
        listening" rather than "start a new session every tap".

        Real bug, hit live and repeatedly, 2026-08-11: the old check here only looked at
        `self._proc` - a daemon started by ANY OTHER means (a previous backend process
        that never got torn down cleanly, a manual test run from a terminal, a leftover
        from an earlier session) was invisible to it, so `start()` would happily spawn a
        SECOND daemon on top of a live one. Two daemons both trying to register the same
        GATT application and race for the same watch connection is exactly the kind of
        split-brain state that made reads intermittently fail/hang with no obvious cause -
        confirmed live: `pgrep` found two `ble_server.py listen` processes at once after
        nothing more than a normal app restart. Now checks the control socket itself
        first (same probe `status()` uses) and adopts an already-live daemon instead of
        racing it with a new one - `forget`/`verbose` are simply not applied in that case,
        since a running daemon can't retroactively change how it forgot bonds at
        startup; if a caller genuinely needs a `--forget` session, `stop()` first."""
        try:
            self._request({"op": "status"}, timeout=2.0)
            return                      # a daemon is already answering - adopt it, don't race it
        except BleBridgeError:
            pass                        # nothing there yet - fall through to actually starting one
        if bluetooth_silent():
            raise BleBridgeError(BLUETOOTH_SILENT_HINT)
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            # Keep the log from growing for ever (it reached 130 MB): past 5 MB the old one
            # becomes ble_daemon.log.1, replacing any older .1.
            try:
                if self._log_path.stat().st_size > LOG_ROLL_BYTES:
                    self._log_path.replace(self._log_path.with_suffix(".log.1"))
            except OSError:
                pass
            # "-u": unbuffered. Real bug caught while testing this on hardware, 2026-08-11 -
            # a Python child process with stdout redirected to a real file (not a tty) is
            # fully block-buffered by default, so `ble_server.py`'s own progress prints
            # (bond forgetting, "watch found", "watch subscribed") sat in the child's
            # buffer instead of reaching this log file, making a live session look stalled
            # when it wasn't. Nothing here needs it fast; it needs it AT ALL while running.
            script = str(TOOLS_DIR / DAEMON_SCRIPT)
            command, env = daemon_command(script)      # raises with words when it cannot run
            args = command + ["listen"]
            if forget:
                args.append("--forget")
            if verbose:
                args.append("--verbose")
            log_file = open(self._log_path, "a", encoding="utf-8")  # noqa: SIM115 - lives as long as _proc
            self._proc = subprocess.Popen(
                args, cwd=str(TOOLS_DIR), stdout=log_file, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, env=env,
                **_detached())
        self._wait_for_socket()

    def stop(self):
        """Tears the daemon down. Does not touch the watch's own bond - same "always unpair,
        don't replace" guidance in PROJECT_RULES.md is a watch-menu action, not something
        this does implicitly on every disconnect."""
        with self._lock:
            proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _wait_for_socket(self):
        deadline = time.monotonic() + _STARTUP_TIMEOUT_S
        while time.monotonic() < deadline:
            if _daemon_endpoint_exists():
                try:
                    self._request({"op": "status"}, timeout=2.0)
                    return
                except BleBridgeError:
                    pass                                    # bound but not accepting yet
            if not self.is_running():
                raise BleBridgeError(
                    "ble_server.py exited before opening its control socket - "
                    f"see {self._log_path}")
            time.sleep(0.2)
        raise BleBridgeError(
            f"ble_server.py did not open its control socket within {_STARTUP_TIMEOUT_S}s")

    def _request(self, payload, timeout=20.0):
        if not _daemon_endpoint_exists():
            raise BleBridgeError("BLE daemon is not running (connect first)")
        sock, address, token = _open_control()
        if token is not None:
            payload = {**payload, "token": token}
        sock.settimeout(timeout)
        try:
            sock.connect(address)
            sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    raise BleBridgeError("BLE daemon closed the connection")
                buf += chunk
        except OSError as exc:
            raise BleBridgeError(f"could not reach BLE daemon: {exc}") from exc
        finally:
            sock.close()
        response = json.loads(buf.decode("utf-8"))
        if not response.get("ok"):
            raise BleBridgeError(response.get("error", "unknown BLE daemon error"))
        return response

    def status(self):
        """Read-only - safe to poll the way DeviceService's own heartbeat polls USB. Reports
        not-running rather than raising when no daemon answers, so a caller can treat this
        like any other "is the watch there" check.

        Deliberately does NOT gate on `is_running()`/`self._proc` first - that only knows
        about a daemon *this* BleBridge instance itself spawned. A real backend restart (or,
        while testing, a daemon started by hand outside the app) leaves a perfectly live
        daemon with no tracked subprocess handle here at all; asking the socket directly is
        the only check that reflects what's actually running, not just what this object
        remembers starting."""
        try:
            response = self._request({"op": "status"}, timeout=3.0)
        except BleBridgeError:
            return {"running": False, "subscribed": False}
        response["running"] = True
        return response

    def set_dry_run(self, value):
        result = self._request({"op": "set_dry_run", "value": bool(value)})["dry_run"]
        self.dry_run = result
        return result

    def forget(self):
        """Real request, 2026-08-13 ("we need to add a button to forget the watch"). Reaches
        the live daemon over the control socket - see ControlSocketServer's own "forget" op
        docstring for why this can't be done as stop()+start(forget=True) instead: those two
        only work on a daemon THIS process spawned, and `is_running()`'s own docstring
        already covers why that's not a safe assumption to make."""
        # Real, 2026-08-13: a generous timeout, not the usual few seconds - the underlying
        # GetManagedObjects()/RemoveDevice() D-Bus calls this makes turned out genuinely
        # slow on a BLE-noisy machine (dozens of nearby devices - see ControlSocketServer's
        # own "forget" op comment), confirmed live, not a hang: BlueZ itself is busy
        # servicing that same noise. Must exceed the daemon's own internal wait
        # (ble_server.py's ControlSocketServer, 20s) or this gives up first and reports a
        # false timeout while the daemon is still working.
        response = self._request({"op": "forget"}, timeout=30.0)
        return response.get("removed", 0)

    def submit_passkey(self, passkey):
        """A fresh pairing needs a human to read a 6-digit passkey off the watch's own
        screen and report it back - see ble_server.py's Agent docstring for why this can't
        be automated (LE Legacy Passkey Entry, watch = Display Only). Call once status()
        shows a non-null "pending_passkey_device"."""
        return self._request({"op": "submit_passkey", "passkey": int(passkey)})["ok"]

    def command(self, cmd, payload=b"", expect_reply=True, quiet=False, flags=None,
                timeout=20.0):
        """`Link.command()`'s full real signature (matching both `write_nav.Link` and
        `ble_server.ServerLink`) - existing call sites (`read_flash()`, `send_plan()`,
        `write_one()`, ...) need nothing new beyond swapping which object they call it on.
        `quiet` and `flags` are accepted for signature compatibility but not forwarded -
        real bug, found live 2026-08-11: `read_flash()` passing `quiet=True` raised
        `TypeError: unexpected keyword argument` here because this method only ever
        declared the two or three parameters this file's OWN early call sites happened to
        use, not the shape callers coming from the wider `write_nav.py`/`settings_write.py`
        codebase actually rely on. The daemon's own `ControlSocketServer` already always
        runs `quiet=True` server-side regardless (nothing here would change its behavior);
        `flags` isn't yet exposed over the control socket at all - every real command this
        project has needed so far uses the driver-path default (`ServerLink.DRIVER_FLAGS`)
        the daemon applies on its own."""
        response = self._request({
            "cmd": cmd, "op": "command", "payload_hex": payload.hex(),
            "expect_reply": expect_reply, "timeout": timeout,
        }, timeout=timeout + 5.0)
        reply = bytes.fromhex(response["payload_hex"])
        self.sent.append((cmd, payload, reply))
        return reply


# One bridge per backend process - see BleBridge's own docstring for why a second is never
# needed. server.py imports this, not the class, for every BLE endpoint.
bridge = BleBridge()
