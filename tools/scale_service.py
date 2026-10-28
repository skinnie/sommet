#!/usr/bin/env python3
"""Local Bluetooth scale: listens for an Etekcity FIT 8S broadcast, keeps the weigh-ins on disk.

No account, no cloud, no pairing: the scale advertises its reading while someone stands on it
(tools/etekcity_scale.py decodes it). This module is what the desktop backend runs in a thread:

    listener = ScaleListener(); listener.start()      # begin listening (needs bleak + Bluetooth)
    listener.status()                                  # {listening, address, lastWeighIn, error, ...}
    series(height_cm, sex, offset_pct)                 # one reading per day, body composition added

Weigh-ins are appended to ~/AmbitAppBackups/scale/weighins.jsonl, one JSON object per line
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

DATA_DIR = Path.home() / "AmbitAppBackups" / "scale"


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

    def __init__(self, data_dir=None):
        self.data_dir = data_dir
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.error = None
        self.last_seen = None        # time of the last packet from our scale
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

    def status(self):
        cfg = load_config(self.data_dir)
        w = self.last_weighin or (load_weighins(self.data_dir) or [None])[-1]
        return {"ok": True, "available": self.available(), "listening": self.listening(),
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
        """Bursts, each in its own event loop with its own connection to BlueZ, closed after the
        burst. BlueZ ties a scan to the connection that asked for it: a scan stopped while the
        watch link was connecting stayed on (17:24 and 17:30, 2026-10-28) until the backend
        quit and its connection closed. Closing it after every burst ends such a scan at once."""
        settle = etekcity_scale.Settle()
        while not self._stop.is_set():
            try:
                asyncio.run(self._burst(settle))
                self.error = None
            except Exception as e:       # noqa: BLE001 - shown in the UI; the next burst retries
                self.error = str(e) or e.__class__.__name__
            self._stop.wait(self.SCAN_OFF_S)

    def _on_adv(self, settle, adv):
        raw = (adv.manufacturer_data or {}).get(etekcity_scale.COMPANY_ID)
        d = etekcity_scale.decode(raw) if raw is not None else None
        if d is None:
            return
        want = (load_config(self.data_dir).get("address") or "").upper()
        if want and d["address"] != want:
            return
        if not want:                                       # first scale heard becomes THE scale
            save_config({"address": d["address"]}, self.data_dir)
        self.last_seen = time.time()
        res = settle.feed(d, self.last_seen)
        if res:
            self.record(res, self.last_seen)

    async def _burst(self, settle):
        from bleak import BleakScanner
        scanner = BleakScanner(lambda device, adv: self._on_adv(settle, adv))
        try:
            # BlueZ can leave a start or stop unanswered while the watch link is connecting
            # (2026-10-28: a burst hung for over an hour, its scan on all along). Never wait
            # long, and close the connection anyway: that ends the scan.
            await asyncio.wait_for(scanner.start(), self.BLUEZ_REPLY_S)
            began = time.time()
            while not self._stop.is_set() and self.keep_scanning(began, time.time()):
                await asyncio.sleep(0.25)
        finally:
            try:
                await asyncio.wait_for(scanner.stop(), self.BLUEZ_REPLY_S)
            except Exception:            # noqa: BLE001 - not started, or no reply: close anyway
                pass
            await close_bluez_connection()

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


def selftest():
    L0 = ScaleListener()
    assert L0.keep_scanning(100.0, 102.0) and not L0.keep_scanning(100.0, 103.5)   # a burst ends
    L0.last_seen = 110.0
    assert L0.keep_scanning(100.0, 125.0)                  # the scale is talking: stay on
    assert not L0.keep_scanning(100.0, 131.0)              # 20 s quiet: back to bursts
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
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest()
    else:
        print(__doc__)
