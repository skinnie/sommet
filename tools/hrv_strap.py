#!/usr/bin/env python3
"""Read HRV from a standard BLE heart-rate strap that reports R-R intervals, and compute HRV with
this project's own hrv.py. Built for André's COOSPO HW9 (an optical armband that streams raw R-R
over the standard Heart Rate service) but works for any strap that sets the RR-Interval flag:
Polar H10, most chest straps, etc. This is the "morning HRV" path - no watch, no Ambit 5+5: wear
the strap, sit/lie still ~2-5 min, run this.

Uses `bleak` (the project's existing BLE dependency, also used by ble_link.py) so it runs on
Linux (BlueZ), macOS (CoreBluetooth) and Windows (WinRT) - the same backend that runs the rest of
the desktop tools. Connect, subscribe to Heart Rate Measurement (0x2A37) notifications, collect
the R-R intervals, and feed them to hrv.py.

HR Measurement (0x2A37) layout (Bluetooth SIG):
  byte0 = flags: bit0 HR value format (0=uint8,1=uint16); bit3 (0x08) energy-expended present;
          bit4 (0x10) RR-Interval(s) present.
  HR value (1 or 2 bytes per bit0), [energy u16], then RR intervals as uint16 LE in 1/1024 s.
  rr_ms = round(raw * 1000 / 1024).

    ./tools/hrv_strap.py --mac E5:CD:59:2C:16:68 --seconds 120 --json
    ./tools/hrv_strap.py --name HW9 --seconds 60      # find by advertised-name substring
"""
import argparse, asyncio, json, sys
import hrv

HR_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEAS = "00002a37-0000-1000-8000-00805f9b34fb"


def parse_hr_measurement(data):
    """Return the R-R intervals (ms) in one HR Measurement notification (may be empty)."""
    if not data:
        return []
    flags = data[0]
    i = 1
    i += 2 if (flags & 0x01) else 1        # HR value: uint16 or uint8
    if flags & 0x08:                        # Energy Expended present -> uint16
        i += 2
    rr = []
    if flags & 0x10:                        # RR-Interval(s) present
        while i + 1 < len(data):
            raw = data[i] | (data[i + 1] << 8)
            rr.append(round(raw * 1000 / 1024))
            i += 2
    return rr


async def _resolve(mac, name, scan_timeout=15.0):
    """Scan and return a BLEDevice matching --mac, or a --name substring, or the HR service.
    Scanning first (rather than connecting to a bare address) is what BlueZ needs to find a strap
    that isn't already known, and it's the same on macOS/Windows."""
    from bleak import BleakScanner
    if mac:
        dev = await BleakScanner.find_device_by_address(mac, timeout=scan_timeout)
        return dev
    devices = await BleakScanner.discover(timeout=scan_timeout, return_adv=True)
    items = devices.values() if isinstance(devices, dict) else [(d, None) for d in devices]
    for dev, adv in items:
        nm = (getattr(dev, "name", None) or (adv.local_name if adv else None) or "")
        uuids = [u.lower() for u in (adv.service_uuids if adv else [])]
        if (name and name.lower() in nm.lower()) or (not name and HR_SERVICE in uuids):
            return dev
    return None


async def _capture(device, seconds):
    """Connect, subscribe to 0x2A37, collect R-R for `seconds`, return [rr_ms...]."""
    from bleak import BleakClient
    rr = []
    def cb(_sender, data):
        rr.extend(parse_hr_measurement(bytes(data)))
    async with BleakClient(device) as client:
        await client.start_notify(HR_MEAS, cb)
        await asyncio.sleep(seconds)
        try:
            await client.stop_notify(HR_MEAS)
        except Exception:
            pass
    return rr


async def _run(mac, name, seconds, as_json):
    device = await _resolve(mac, name)
    if not device:
        who = f"name~{name!r}" if name else f"mac {mac}" if mac else "HR strap"
        msg = f"no strap found ({who}) - turn it on / wear it so it advertises, then retry"
        if as_json:
            print(json.dumps({"ok": False, "error": msg})); return
        sys.exit(msg)
    if not as_json:
        print(f"reading R-R from {device.address} for {seconds}s - wear it and stay still…")
    try:
        rr = await _capture(device, seconds)
    except Exception as e:
        if as_json:
            print(json.dumps({"ok": False, "error": f"BLE read failed: {e}", "mac": device.address})); return
        sys.exit(f"BLE read failed: {e}")
    summary = hrv.hrv_summary(rr)
    result = {"ok": len(rr) >= 2, "mac": device.address, "seconds": seconds, "rr_ms": rr, **summary}
    if as_json:
        print(json.dumps(result))
    elif not result["ok"]:
        print(f"got only {len(rr)} beats - strap not reading (wet the sensor / wear tighter)")
    else:
        print(f"beats={summary['n_beats']}  RMSSD={summary['rmssd_ms']} ms  "
              f"HR={summary['mean_hr_bpm']} bpm  SDNN={summary['sdnn_ms']}  "
              f"pNN50={summary['pnn50_pct']}%")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--mac", help="strap BLE address, e.g. E5:CD:59:2C:16:68")
    g.add_argument("--name", help="find the strap by advertised-name substring (e.g. HW9)")
    ap.add_argument("--seconds", type=int, default=120, help="capture window (default 120)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    asyncio.run(_run(args.mac, args.name, args.seconds, args.json))


if __name__ == "__main__":
    main()
