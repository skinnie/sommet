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

LINUX ONLY, for now. `ble_server.py` hosts the GATT server via BlueZ's D-Bus API, which has
no equivalent on macOS or Windows - each of those needs its own peripheral-mode backend
(CoreBluetooth on macOS, WinRT's GATT server APIs on Windows) behind this same client
interface. Nothing here is Linux-specific by construction; only `ble_server.py` itself is.

DRY-RUN BY DEFAULT, matching every other watch-writing path in this project
(`write_nav.py`, `sgee.py`, `server.py`'s own WATCH_LOCK'd endpoints): connecting only reads
(`command()` calls made read-only unless `confirm: true` is threaded through, same contract
`server.py` already uses for USB). `set_dry_run(False)` is the explicit, one-place opt-in.
"""

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
PYTHON = sys.executable

LOG_ROLL_BYTES = 5 * 1024 * 1024

# Must match tools/ble_server.py's SOCKET_PATH exactly - not re-derived, so the two can never
# drift apart.
SOCKET_PATH = Path.home() / ".cache" / "Sommet" / "ble.sock"

# How long to wait for the daemon subprocess to bind its control socket after being spawned.
# Local process startup (Python interpreter + dbus/GLib imports), not a BLE operation - a few
# seconds is generous, not a value tuned against the watch's own timing.
_STARTUP_TIMEOUT_S = 10.0


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
            args = [PYTHON, "-u", str(TOOLS_DIR / "ble_server.py"), "listen"]
            if forget:
                args.append("--forget")
            if verbose:
                args.append("--verbose")
            log_file = open(self._log_path, "a", encoding="utf-8")  # noqa: SIM115 - lives as long as _proc
            self._proc = subprocess.Popen(
                args, cwd=str(TOOLS_DIR), stdout=log_file, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                # Fully detach into its own session - real bug, hit live 2026-08-11 testing
                # a fresh pairing: without this, the daemon is still in the SAME process
                # group as whatever shell launched it (a terminal, or - while testing this -
                # a backgrounded `&` job), and that shell exiting/being reaped can take the
                # daemon down with it mid-pairing, well before any BLE timeout would. The
                # whole point of this being a persistent daemon (see this module's own
                # docstring) is that it outlives the process that started it.
                start_new_session=True)
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
            if SOCKET_PATH.exists():
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
        if not SOCKET_PATH.exists():
            raise BleBridgeError("BLE daemon is not running (connect first)")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(SOCKET_PATH))
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
