#!/usr/bin/env python3
"""Local Bluetooth scale: listens for an Etekcity FIT 8S broadcast, keeps the weigh-ins on disk.

No account, no cloud, no pairing: the scale advertises its reading while someone stands on it
(tools/etekcity_scale.py decodes it). This module is what the desktop backend runs in a thread:

    listener = ScaleListener(); listener.start()      # begin listening (needs bleak + Bluetooth)
    listener.status()                                  # {listening, address, lastWeighIn, error, ...}
    series(height_cm, sex, offset_pct)                 # one reading per day, body composition added

Weigh-ins are appended to ~/SommetBackups/scale/weighins.jsonl, one JSON object per line
(t, weightKg, impedanceOhm or null, address). The chosen scale lives in scale.json next to it.
Body composition is NOT stored: it is computed on read (tools/body_composition.py), so a changed
height, sex or offset applies to the whole history.

    tools/scale_service.py selftest
"""
import asyncio
import datetime
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import body_composition  # noqa: E402
import etekcity_scale  # noqa: E402

DATA_DIR = Path.home() / "SommetBackups" / "scale"


def _paths(data_dir):
    d = Path(data_dir or DATA_DIR)
    return d / "weighins.jsonl", d / "scale.json"


def load_weighins(data_dir=None):
    path, _ = _paths(data_dir)
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                if isinstance(r.get("weightKg"), (int, float)) and isinstance(r.get("t"), (int, float)):
                    out.append(r)
            except ValueError:
                continue                       # one damaged line must not hide the rest
    return sorted(out, key=lambda r: r["t"])


def append_weighin(reading, data_dir=None):
    path, _ = _paths(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(reading) + "\n")


def load_config(data_dir=None):
    _, path = _paths(data_dir)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg, data_dir=None):
    _, path = _paths(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg), encoding="utf-8")


def forget(data_dir=None):
    """Forget the scale (not the weigh-ins already recorded)."""
    _, path = _paths(data_dir)
    if path.exists():
        path.unlink()


def _day(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d")


def series(height_cm=None, sex=None, offset_pct=0.0, data_dir=None):
    """One point per day, oldest first. If someone weighed in more than once that day, the latest
    weigh-in WITH impedance wins, else the latest. Body composition (marked estimated) is added when
    impedance, height and sex are all known."""
    byday = {}
    for r in load_weighins(data_dir):
        d = _day(r["t"])
        cur = byday.get(d)
        has = bool(r.get("impedanceOhm"))
        if cur is None or has or not cur.get("impedanceOhm"):
            byday[d] = r
    out = []
    for d in sorted(byday):
        r = byday[d]
        p = {"date": d, "weightKg": round(r["weightKg"], 2), "source": "scale", "t": r["t"]}
        if height_cm:
            p["bmi"] = round(r["weightKg"] / (height_cm / 100.0) ** 2, 1)
        imp = r.get("impedanceOhm")
        if imp:
            p["impedanceOhm"] = imp
            est = body_composition.estimate(r["weightKg"], imp, height_cm, sex, offset_pct or 0.0)
            if est:
                p.update(bodyFatPct=est["fat_pct"], bodyWaterPct=est["water_pct"],
                         fatFreeKg=est["fat_free_kg"], estimated=True)
        out.append(p)
    return out


class ScaleListener:
    """Listens for the scale in its own thread. start()/stop() are safe to call repeatedly.

    It scans in short bursts (SCAN_ON_S on, SCAN_OFF_S off) and stays on while the scale is
    talking. A nonstop scan stopped a Bluetooth mouse from reconnecting on a ThinkPad X230
    (Broadcom 4.0 chip, BlueZ 5.72), 2026-10-28. Passive scanning would be nicer but needs
    BlueZ's experimental advertisement monitor. A weigh-in broadcasts for well over SCAN_OFF_S
    (live weight, impedance, then ~50 final packets over 5 s), so a burst catches its start."""

    SCAN_ON_S = 3.0
    SCAN_OFF_S = 7.0
    HOLD_S = 20.0        # keep scanning this long after the last packet from the scale
    BLUEZ_REPLY_S = 5.0  # longest wait for BlueZ to answer a scan start or stop
    BACKOFF_MAX_S = 300.0  # longest wait between bursts while they keep going wrong
    START_WINDOW_S = 300.0  # it scans this long after it starts, then only when asked (want)
    WANT_MAX_S = 900.0

    def __init__(self, data_dir=None):
        self.data_dir = data_dir
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.error = None
        self.last_seen = None        # time of the last packet from our scale
        self._scanner_filter = None  # what the current scanner asked BlueZ to report
        self._wanted_until = 0.0     # monotonic time until which it scans (want())
        self.last_weighin = None     # the last stored reading

    # -- state -------------------------------------------------------------------------
    @staticmethod
    def available():
        try:
            import bleak  # noqa: F401
            return True
        except ImportError:
            return False

    def listening(self):
        return self._thread is not None and self._thread.is_alive()

    def want(self, seconds=None):
        """Scan for the next `seconds`. André's choice, 2026-11-23: the scale is listened for
        while the Weight page is open (the page asks again every minute) and for
        START_WINDOW_S after Sommet starts, not for as long as Sommet is open. Every scan
        makes the computer's Bluetooth announce what it hears to every program on the system
        (watch-protocol/08), so it scans when a weigh-in is likely and is silent otherwise."""
        try:
            seconds = float(seconds) if seconds else self.START_WINDOW_S
        except (TypeError, ValueError):
            seconds = self.START_WINDOW_S
        seconds = max(10.0, min(seconds, self.WANT_MAX_S))
        self._wanted_until = max(self._wanted_until, time.monotonic() + seconds)

    def wanted(self):
        return time.monotonic() < self._wanted_until

    def status(self):
        cfg = load_config(self.data_dir)
        w = self.last_weighin or (load_weighins(self.data_dir) or [None])[-1]
        return {"ok": True, "available": self.available(), "listening": self.listening(),
                "scanning": self.listening() and self.wanted(),
                "address": cfg.get("address"), "lastSeen": self.last_seen,
                "lastWeighIn": w, "error": self.error}

    # -- control -----------------------------------------------------------------------
    def start(self, address=None):
        """Begin listening. With an address, remember it as THE scale. Without one, an already
        remembered address is used; with neither, the first FIT 8S heard is remembered."""
        with self._lock:
            if address:
                cfg = load_config(self.data_dir)
                cfg["address"] = address.upper()
                save_config(cfg, self.data_dir)
            self.want(self.START_WINDOW_S)
            if self.listening():
                return
            if not self.available():
                self.error = "bleak is not installed (pip install bleak)"
                return
            self.error = None
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="scale-listener", daemon=True)
            self._thread.start()

    def stop(self):
        with self._lock:
            self._stop.set()
            t = self._thread
        if t:
            t.join(timeout=8)
        self._thread = None

    def _run(self):
        """One event loop, and so one connection to BlueZ, for as long as it listens.

        Until 2026-11-23 every burst had its own loop and its own connection, closed after the
        burst: about 360 new connections to the system's message bus an hour, measured on the
        X230 from the bus's own log (382 and 462 an hour while Sommet ran, 4 to 15 with it
        closed). That was not what silenced that laptop's Bluetooth that night (a Wine process
        that never read BlueZ's signals was), but it was waste on a shared bus. The bursts stay
        (they are what lets a Bluetooth mouse reconnect); the connection is now kept, and
        replaced only when a burst goes wrong."""
        try:
            asyncio.run(self._listen(etekcity_scale.Settle()))
        except Exception as e:           # noqa: BLE001 - shown in the UI
            self.error = str(e) or e.__class__.__name__

    def scan_filter(self):
        """Linux: what BlueZ is asked to report, or None for everything.

        An unfiltered scan makes BlueZ announce every advertiser in range to every program on
        the system's message bus: 67 signals for two 3 s scans on the X230, against 4 with a
        filter on one address (measured 2026-11-23). Those signals are what filled the bus
        that night (watch-protocol/08). So once the scale has been heard, BlueZ is asked for
        that one address only. "Heard" is the condition: the filter is used only when a real
        packet came from the very address the scale names in its payload (`bleAddress`), so a
        scale that advertises from another address is never filtered out."""
        if not sys.platform.startswith("linux"):
            return None
        cfg = load_config(self.data_dir)
        address = (cfg.get("address") or "").upper()
        if address and cfg.get("bleAddress") == address:
            return {"Pattern": address}
        return None

    def new_scanner(self, settle):
        from bleak import BleakScanner
        self._scanner_filter = self.scan_filter()
        extra = {"bluez": {"filters": dict(self._scanner_filter)}} if self._scanner_filter else {}
        return BleakScanner(lambda device, adv: self._on_adv(settle, adv, device), **extra)

    async def _listen(self, settle):
        scanner = None
        failures = 0
        try:
            while not self._stop.is_set():
                if not self.wanted():
                    # Nobody is about to weigh in: no scan, and no connection either (an
                    # open one still receives every signal BlueZ sends).
                    if scanner is not None:
                        scanner = None
                        await close_bluez_connection()
                    failures = 0
                    await self._pause(1.0)
                    continue
                if scanner is not None and self.scan_filter() != self._scanner_filter:
                    scanner = None           # the scale was just heard, or changed: new filter
                if scanner is None:
                    scanner = self.new_scanner(settle)
                if await self._burst(scanner):
                    failures = 0
                else:
                    # BlueZ ties a scan to the connection that asked for it: one whose start
                    # or stop got no answer stays on (17:24 and 17:30, 2026-10-28, until the
                    # backend quit). Closing the connection ends it at once. Then wait longer
                    # each time: a Bluetooth that answers nothing gets a new connection every
                    # few minutes, not every ten seconds.
                    scanner = None
                    await close_bluez_connection()
                    failures += 1
                await self._pause(self.off_seconds(failures))
        finally:
            await close_bluez_connection()

    def off_seconds(self, failures):
        """The wait between bursts: SCAN_OFF_S, doubled for every burst in a row that went
        wrong, up to BACKOFF_MAX_S."""
        return min(self.SCAN_OFF_S * 2 ** min(failures, 16), self.BACKOFF_MAX_S)

    async def _pause(self, seconds):
        until = time.monotonic() + seconds
        while not self._stop.is_set() and time.monotonic() < until:
            await asyncio.sleep(0.25)

    def _on_adv(self, settle, adv, device=None):
        raw = (adv.manufacturer_data or {}).get(etekcity_scale.COMPANY_ID)
        d = etekcity_scale.decode(raw) if raw is not None else None
        if d is None:
            return
        cfg = load_config(self.data_dir)
        want = (cfg.get("address") or "").upper()
        if want and d["address"] != want:
            return
        # The address the packet came from, as BlueZ names it: kept for scan_filter().
        came_from = (getattr(device, "address", "") or "").upper()
        if not want or (came_from and cfg.get("bleAddress") != came_from):
            cfg["address"] = d["address"]                  # first scale heard becomes THE scale
            if came_from:
                cfg["bleAddress"] = came_from
            save_config(cfg, self.data_dir)
        self.last_seen = time.time()
        res = settle.feed(d, self.last_seen)
        if res:
            self.record(res, self.last_seen)

    async def _burst(self, scanner):
        """One burst on the kept connection. True when BlueZ answered both the start and the
        stop; False means the scan may still be on and the connection has to go."""
        started = False
        try:
            # BlueZ can leave a start or stop unanswered while the watch link is connecting
            # (2026-10-28: a burst hung for over an hour, its scan on all along). Never wait
            # long.
            await asyncio.wait_for(scanner.start(), self.BLUEZ_REPLY_S)
            started = True
            self.error = None
            began = time.time()
            while not self._stop.is_set() and self.keep_scanning(began, time.time()):
                await asyncio.sleep(0.25)
        except asyncio.TimeoutError:
            self.error = "Bluetooth on this computer is not answering"
        except Exception as e:           # noqa: BLE001 - shown in the UI; a later burst retries
            self.error = str(e) or e.__class__.__name__
        try:
            await asyncio.wait_for(scanner.stop(), self.BLUEZ_REPLY_S)
        except Exception:                # noqa: BLE001 - not started, or no reply
            return False
        return started

    def keep_scanning(self, began, now):
        """Inside a burst: stay on for SCAN_ON_S, and for HOLD_S after the scale's last packet."""
        if now - began < self.SCAN_ON_S:
            return True
        return self.last_seen is not None and now - self.last_seen < self.HOLD_S

    def record(self, res, now=None):
        reading = {"t": int(now or time.time()), "weightKg": res["weight_kg"],
                   "impedanceOhm": res.get("impedance_ohm"), "address": res["address"]}
        append_weighin(reading, self.data_dir)
        self.last_weighin = reading
        return reading


async def close_bluez_connection():
    """Linux: close bleak's D-Bus connection for this event loop, so BlueZ drops any scan it
    still holds for it. Other systems: nothing to do."""
    if not sys.platform.startswith("linux"):
        return
    try:
        from bleak.backends.bluezdbus import manager as bluez_manager
        mgr = bluez_manager._global_instances.pop(asyncio.get_running_loop(), None)
        bus = getattr(mgr, "_bus", None)
        if bus is not None:
            bus.disconnect()
            await asyncio.wait_for(bus.wait_for_disconnect(), 2)
    except Exception:                    # noqa: BLE001 - a newer bleak may differ; bursts still end
        pass


def find_scales(seconds=15):
    """One-off scan for the wizard: every FIT 8S heard in `seconds`, strongest first. Blocking."""
    from bleak import BleakScanner
    found = {}

    def on_adv(device, adv):
        raw = (adv.manufacturer_data or {}).get(etekcity_scale.COMPANY_ID)
        d = etekcity_scale.decode(raw) if raw is not None else None
        if d:
            found[d["address"]] = {"address": d["address"], "weightKg": d["weight_kg"],
                                   "rssi": adv.rssi}

    async def go():
        async with BleakScanner(on_adv):
            await asyncio.sleep(seconds)
    asyncio.run(go())
    return sorted(found.values(), key=lambda f: -(f["rssi"] or -999))


def _selftest_one_connection():
    """The listener keeps one connection to BlueZ across bursts (2026-11-23), drops it only
    after a burst BlueZ did not answer, and then waits longer each time."""
    global close_bluez_connection
    L = ScaleListener()
    assert [L.off_seconds(n) for n in (0, 1, 2, 3)] == [7.0, 14.0, 28.0, 56.0]
    assert L.off_seconds(6) == L.off_seconds(60) == 300.0

    class Scanner:
        def __init__(self, log, bursts, fail_stop=False):
            self.log, self.bursts, self.fail_stop = log, bursts, fail_stop

        async def start(self):
            self.log.append("start")

        async def stop(self):
            self.bursts.append(1)
            if self.fail_stop:
                raise RuntimeError("org.bluez.Error.InProgress")
            self.log.append("stop")

    def run_listen(listener):
        global close_bluez_connection
        saved = close_bluez_connection

        async def closed():
            return None
        close_bluez_connection = closed
        try:
            asyncio.run(listener._listen(None))
        finally:
            close_bluez_connection = saved

    def run(fail_first, stop_after):
        global close_bluez_connection
        log, bursts, waits = [], [], []
        made = []
        L = ScaleListener()
        L.SCAN_ON_S = 0.0
        L.want(60)

        def new_scanner(_settle):
            made.append(Scanner(log, bursts, fail_stop=len(made) < fail_first))
            log.append("scanner")
            return made[-1]

        async def pause(seconds):
            waits.append(seconds)
            if len(bursts) >= stop_after:
                L._stop.set()

        async def closed():
            log.append("close")
        L.new_scanner, L._pause = new_scanner, pause
        saved = close_bluez_connection
        close_bluez_connection = closed
        try:
            asyncio.run(L._listen(None))
        finally:
            close_bluez_connection = saved
        return log, waits

    log, waits = run(fail_first=0, stop_after=4)
    assert log == ["scanner"] + ["start", "stop"] * 4 + ["close"], log   # one connection, 4 bursts
    assert waits == [7.0] * 4, waits
    L = ScaleListener()
    L.SCAN_ON_S = 0.0
    L.want(60)
    wanted = [None]                             # what scan_filter() answers: nothing yet
    made, pauses = [], []
    L.scan_filter = lambda: wanted[0]

    def new_scanner(_settle):
        L._scanner_filter = L.scan_filter()     # as the real new_scanner does
        made.append(L._scanner_filter)
        return Scanner([], [])

    async def pause(_seconds):
        pauses.append(1)
        if len(pauses) == 2:
            wanted[0] = {"Pattern": "C0:FF:EE:00:00:01"}    # the scale was just heard
        if len(pauses) >= 4:
            L._stop.set()
    L.new_scanner, L._pause = new_scanner, pause
    run_listen(L)
    # four bursts: the filter changed after the second, so one new scanner, and only one
    assert made == [None, {"Pattern": "C0:FF:EE:00:00:01"}], made
    # It scans only while wanted: nothing before want(), bursts during, and when the time is
    # up the connection is closed and no scanner is made until it is wanted again.
    L = ScaleListener()
    L.SCAN_ON_S = 0.0
    log, ticks = [], []
    assert not L.wanted() and L.status()["scanning"] is False
    L.new_scanner = lambda _s: log.append("scanner") or Scanner(log, [])

    async def pause(_seconds):
        ticks.append(1)
        if len(ticks) == 2:
            L.want(60)                          # the Weight page opens
        elif len(ticks) == 4:
            L._wanted_until = 0.0               # ... and its time runs out
        elif len(ticks) >= 6:
            L._stop.set()
    L._pause = pause

    async def closed():
        log.append("close")
    saved = close_bluez_connection
    close_bluez_connection = closed
    try:
        asyncio.run(L._listen(None))
    finally:
        close_bluez_connection = saved
    assert log == ["scanner", "start", "stop", "start", "stop", "close", "close"], log
    L2 = ScaleListener()
    L2.want(5)
    assert L2.wanted() and L2._wanted_until - time.monotonic() > 9      # never under 10 s
    L2.want(10 ** 6)
    assert L2._wanted_until - time.monotonic() <= L2.WANT_MAX_S + 1     # nor over 15 min
    log, waits = run(fail_first=2, stop_after=4)
    assert log == ["scanner", "start", "close", "scanner", "start", "close",
                   "scanner", "start", "stop", "start", "stop", "close"], log
    assert waits == [14.0, 28.0, 7.0, 7.0], waits            # longer after each miss, then back


def _selftest_filter(tmp):
    """BlueZ is asked for the scale's address only once a packet really came from it."""
    import types
    packet = types.SimpleNamespace(manufacturer_data={
        etekcity_scale.COMPANY_ID: bytes.fromhex("01010000eeffc0c0a90172230100000000020300")})
    scale, other = "C0:FF:EE:00:00:01", "5A:11:22:33:44:55"
    saved = sys.platform
    try:
        sys.platform = "linux"
        L = ScaleListener(tmp)
        assert L.scan_filter() is None                       # no scale yet: everything
        save_config({"address": scale}, tmp)
        assert L.scan_filter() is None                       # chosen, never heard: everything
        L._on_adv(etekcity_scale.Settle(), packet, types.SimpleNamespace(address=other))
        assert load_config(tmp)["bleAddress"] == other
        assert L.scan_filter() is None                       # it advertises from another address
        L._on_adv(etekcity_scale.Settle(), packet, types.SimpleNamespace(address=scale.lower()))
        assert load_config(tmp) == {"address": scale, "bleAddress": scale}
        assert L.scan_filter() == {"Pattern": scale}
        save_config({"address": "AA:BB:CC:00:00:02", "bleAddress": scale}, tmp)
        assert L.scan_filter() is None                       # another scale chosen: start over
        forget(tmp)
        L._on_adv(etekcity_scale.Settle(), packet, types.SimpleNamespace(address=scale))
        assert L.scan_filter() == {"Pattern": scale}         # the first scale heard, at once
        sys.platform = "win32"
        assert L.scan_filter() is None                       # BlueZ's filter: Linux only
    finally:
        sys.platform = saved
        forget(tmp)


def selftest():
    L0 = ScaleListener()
    assert L0.keep_scanning(100.0, 102.0) and not L0.keep_scanning(100.0, 103.5)   # a burst ends
    L0.last_seen = 110.0
    assert L0.keep_scanning(100.0, 125.0)                  # the scale is talking: stay on
    assert not L0.keep_scanning(100.0, 131.0)              # 20 s quiet: back to bursts
    _selftest_one_connection()
    with tempfile.TemporaryDirectory() as tmp:
        assert load_weighins(tmp) == [] and series(data_dir=tmp) == []
        day = time.mktime((2026, 10, 16, 8, 0, 0, 0, 0, -1))
        L = ScaleListener(tmp)
        L.record({"weight_kg": 74.47, "impedance_ohm": None, "address": "AA"}, day)
        L.record({"weight_kg": 74.61, "impedance_ohm": 500, "address": "AA"}, day + 60)
        L.record({"weight_kg": 74.52, "impedance_ohm": None, "address": "AA"}, day + 120)   # socks
        L.record({"weight_kg": 73.5, "impedance_ohm": None, "address": "AA"}, day + 86400)
        s = series(180, "M", 0.0, tmp)
        assert [p["date"] for p in s] == ["2026-10-16", "2026-10-17"], s
        assert s[0]["weightKg"] == 74.61 and s[0]["impedanceOhm"] == 500, "weigh-in with impedance wins the day"
        assert s[0]["estimated"] is True and 17 < s[0]["bodyFatPct"] < 20, s[0]
        assert "bodyFatPct" not in s[1], "no impedance, no body composition"
        assert s[0]["bmi"] == 23.0 and s[1]["bmi"] == 22.7, (s[0]["bmi"], s[1]["bmi"])
        # height/sex unknown: weight and impedance still shown, no made-up body fat
        assert "bodyFatPct" not in series(None, None, 0.0, tmp)[0]
        # the offset applies on read, to the whole history
        assert abs(series(180, "M", -2.0, tmp)[0]["bodyFatPct"] - (s[0]["bodyFatPct"] - 2.0)) < 0.11
        # a damaged line does not hide the others
        with open(_paths(tmp)[0], "a") as f:
            f.write("{not json\n")
        assert len(load_weighins(tmp)) == 4
        # config
        assert load_config(tmp) == {}
        save_config({"address": "AA"}, tmp)
        assert L.status()["address"] == "AA" and L.status()["lastWeighIn"]["weightKg"] == 73.5
        forget(tmp)
        assert load_config(tmp) == {} and len(load_weighins(tmp)) == 4, "forget keeps the history"
        _selftest_filter(tmp)
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest()
    else:
        print(__doc__)
