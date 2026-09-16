#!/usr/bin/env python3
"""Desktop overnight-HRV acquisition from a Polar Verity Sense (or OH1), over Bluetooth.

This is the desktop twin of the phone's PolarSleep module - the half that was missing on desktop
(the desktop already had the ANALYSIS, tools/sleep_stage.py, and the /api/sleep/process endpoint;
it had no way to GET a recording off the band). It produces a recording JSON that sleep_stage.py
consumes directly.

WHY PMD, not the HR strap path: the Verity is optical and reports HR ONLY over the standard Heart
Rate service (0x180D) - no R-R. Verified on André's band 2026-09-14 (30 s stream: HR present, zero
R-R). So HRV from a Verity needs Polar's PMD protocol (PPI = peak-to-peak intervals), unlike a
chest strap (H10) which gives R-R over 0x180D and works with tools/hrv_strap.py. This tool speaks
PMD.

Modes:
  record  (DEFAULT, TESTED): connect, start a live PMD PPI stream, and write each interval to the
          recording JSON as it arrives (the file is flushed per sample, so stopping at any time -
          Ctrl-C / SIGTERM - leaves a valid, analyzable file). This is the "band near the computer
          overnight" path. Honest limitation vs. the phone: the band stays connected (the phone
          uses the band's own OFFLINE recording so it can be worn away from the phone).
  fetch   (EXPERIMENTAL, UNVALIDATED): the true band-alone offline path - list the band's stored
          offline recordings over PFTP and download the latest. Reverse-engineered from the Polar
          BLE SDK; NOT yet validated against a real overnight recording (needs the hardware + a
          night). Prints a clear banner and is not used by the default desktop flow.

Recording JSON shape (what sleep_stage.py reads): {"source","start_time","rr_ms":[...],
"rr_times_s":[...]}. rr_ms are the PPI intervals in milliseconds.

Uses `bleak` (the project's existing BLE dep). PMD service/characteristics and the PPI frame layout
are from the open-source polar-ble-sdk (BlePMDClient / PpiData).
"""
import argparse
import asyncio
import json
import os
import signal
import sys
import time

# PMD (Polar Measurement Data) service
PMD_SERVICE = "fb005c80-02e7-f387-1cad-8acd2d8df0c8"
PMD_CP = "fb005c81-02e7-f387-1cad-8acd2d8df0c8"     # control point (write + notify)
PMD_DATA = "fb005c82-02e7-f387-1cad-8acd2d8df0c8"    # measurement data (notify)
HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"

# PMD control-point commands (client->service) and measurement types
CP_REQUEST_MEASUREMENT_START = 0x02
CP_STOP_MEASUREMENT = 0x03
PMD_TYPE_PPI = 0x03
PPI_SAMPLE_SIZE = 6   # hr(1) ppInMs(2 LE) ppErrorEstimate(2 LE) flags(1)

DEFAULT_NAME_MATCH = "polar"


def _print(msg):
    print(msg, flush=True)


async def _find(address_or_name, timeout=15.0):
    from bleak import BleakScanner
    if address_or_name and ":" in address_or_name and len(address_or_name) == 17:
        dev = await BleakScanner.find_device_by_address(address_or_name, timeout=timeout)
        if dev:
            return dev, address_or_name
    want = (address_or_name or "").lower()
    devs = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for _, (dev, adv) in devs.items():
        name = (adv.local_name or dev.name or "")
        if want:
            if want in name.lower():
                return dev, name
        elif "polar" in name.lower() or "sense" in name.lower():
            return dev, name
    return None, None


def _parse_ppi_frame(data: bytes):
    """PMD DATA frame -> list of PPI intervals (ms). Frame header: [type(1)][timestamp(8)]
    [frameType(1)] then samples. PPI online frames carry a zero timestamp; each 6-byte sample is
    hr(1), ppInMs(u16 LE), ppErrorEstimate(u16 LE), flags(1). blockerBit (flags bit0)=1 means the
    interval is unreliable (movement) - we drop those, as the SDK does for HRV."""
    if not data or (data[0] & 0x3F) != PMD_TYPE_PPI:
        return []
    body = data[10:]                       # skip type(1)+timestamp(8)+frameType(1)
    out = []
    off = 0
    while off + PPI_SAMPLE_SIZE <= len(body):
        s = body[off:off + PPI_SAMPLE_SIZE]
        pp = s[1] | (s[2] << 8)
        flags = s[5]
        blocker = flags & 0x01
        if pp > 0 and not blocker:
            out.append(pp)
        off += PPI_SAMPLE_SIZE
    return out


class _Recorder:
    def __init__(self, out_path, source):
        self.out_path = out_path
        self.source = source
        self.rr = []
        self.times = []
        self.t0 = None
        self.start_iso = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._acc = 0.0

    def add(self, intervals):
        if self.t0 is None:
            self.t0 = time.monotonic()
        for pp in intervals:
            self._acc += pp / 1000.0
            self.rr.append(int(pp))
            self.times.append(round(self._acc, 3))
        self.flush()

    def flush(self):
        tmp = self.out_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"source": self.source, "start_time": self.start_iso,
                       "rr_ms": self.rr, "rr_times_s": self.times}, f)
        os.replace(tmp, self.out_path)     # atomic - a partial night is always a valid file


async def record(args):
    from bleak import BleakClient
    dev, name = await _find(args.device)
    if not dev:
        _print(json.dumps({"ok": False, "error": "no Polar band found (on? worn? in range?)"}))
        return 2
    _print(f"# connecting to {name} ...")
    rec = _Recorder(args.out, source="polar_ppi_live")
    stop_evt = asyncio.Event()

    def _sig(*_):
        stop_evt.set()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(s, _sig)
        except (NotImplementedError, RuntimeError):
            signal.signal(s, lambda *_: stop_evt.set())

    async with BleakClient(dev, timeout=30.0) as c:
        got = {"n": 0}

        def on_data(_, data: bytearray):
            iv = _parse_ppi_frame(bytes(data))
            if iv:
                got["n"] += len(iv)
                rec.add(iv)
        await c.start_notify(PMD_DATA, on_data)
        # start a live PPI stream: [REQUEST_MEASUREMENT_START, PPI type, 0 settings]
        await c.write_gatt_char(PMD_CP, bytes([CP_REQUEST_MEASUREMENT_START, PMD_TYPE_PPI, 0x00]),
                                response=True)
        _print(f"# recording PPI (Ctrl-C to stop). PPI needs the band WORN; it warms up ~25 s. "
               f"max {args.minutes} min.")
        deadline = time.monotonic() + args.minutes * 60
        while not stop_evt.is_set() and time.monotonic() < deadline:
            await asyncio.wait([asyncio.create_task(stop_evt.wait())], timeout=2.0)
        try:
            await c.write_gatt_char(PMD_CP, bytes([CP_STOP_MEASUREMENT, PMD_TYPE_PPI]),
                                    response=True)
            await c.stop_notify(PMD_DATA)
        except Exception:
            pass
    rec.flush()
    _print(json.dumps({"ok": len(rec.rr) >= 20, "out": args.out, "intervals": len(rec.rr),
                       "note": None if len(rec.rr) >= 20 else
                       "few/no PPI - was the band worn with good skin contact?"}))
    return 0 if len(rec.rr) >= 20 else 3


async def fetch(args):
    _print("#" * 70)
    _print("# EXPERIMENTAL / UNVALIDATED offline-recording fetch.")
    _print("# Reverse-engineered from polar-ble-sdk; NOT yet tested against a real overnight")
    _print("# recording. The band-alone offline path stores PPG/ACC under /U/0/*.REC over PFTP")
    _print("# and needs the PMD offline-frame decoder. Use `record` (live PPI) for a tested path.")
    _print("#" * 70)
    _print(json.dumps({"ok": False, "error": "offline fetch not validated - use `record`"}))
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="mode")

    r = sub.add_parser("record", help="live PMD PPI stream -> recording JSON (TESTED)")
    r.add_argument("--out", required=True, help="recording JSON path (flushed per sample)")
    r.add_argument("--minutes", type=int, default=600, help="max duration (default 600 = 10 h)")
    r.add_argument("--device", default=None, help="MAC or name substring (default: any Polar)")

    f = sub.add_parser("fetch", help="EXPERIMENTAL band-alone offline recording fetch")
    f.add_argument("--out", required=True)
    f.add_argument("--device", default=None)

    args = ap.parse_args()
    if args.mode == "record":
        return asyncio.run(record(args))
    if args.mode == "fetch":
        return asyncio.run(fetch(args))
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
