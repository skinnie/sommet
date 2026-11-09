#!/usr/bin/env python3
"""Mark a workout as *synced* on the watch - the same per-move flag SuuntoLink writes over
cable so the official Suunto app / SuuntoLink treat the move as already downloaded and don't
duplicate it. Opt-in feature (Settings -> Experimental features, OFF by default): the upside
is no duplicated workouts; the downside is the move can no longer be re-retrieved from the
watch if the Suunto app later fails to keep it. Nothing here deletes anything - the watch
still reclaims log space by circular-buffer wraparound, independent of this flag. See the
`sommet-activity-sync-no-delete` finding.

Own file, this project's own "one file per format" convention (importing `Link`/constants
from `write_nav.py`, never growing it into a dispatcher).

USB (PROVEN, byte-exact vs the real SuuntoLink capture `assets/pcap/lars_ambit3peak_
13032017.pcap`): command 0x1201 (`ambit3_log_synced`), one SBEM0102 entry whose id is the
firmware generation's `log_synced_data_id` (0x8b on the Ambit3 GEN4 reference watch), value =
a NUL-terminated 20-byte LOCAL ISO8601 timestamp (the move header's own wall-clock time, not
the UTC-corrected one) + a single `synced=1` byte. Run `./tools/mark_synced.py --verify` to
byte-check the builder against that capture. The generation lookup mirrors openambit's own
`get_ambit3_fw_gen()`/`get_ambit3_driver_params()` tables - and, exactly like openambit, only
GEN4 has a known non-zero id: for every other generation (older Ambit3 fw, Vertical,
Traverse) the id is unknown, so this refuses to write rather than guess a wrong entry id.

BLE (UNVERIFIED): the real Suunto app never wrote this flag in any capture - it uses
`EventBoard` events instead (see `tools/ble_schema.py`). `mark_ble()` sends the *same*
0x1201/`log_synced_data_id` marker over the NSP transport by analogy with USB (the wire body
is identical; only the framing differs), using `write_nav.SBEM_WRITE_PREFIX` - the RE'd
phone-write header the app uses for its other BLE writes. This needs a live-watch check to
confirm it actually sets the shared synced flag; until then it is guarded the same way (GEN4
id only) and clearly logged as experimental.

    ./tools/mark_synced.py --verify          # self-test the builder vs the real capture
    ./tools/mark_synced.py --list            # show the fw-gen -> id table
"""

import argparse
import sys

from write_nav import Link, PRODUCT_IDS, SBEM_WRITE_PREFIX, codename_for_pid  # noqa: F401

SBEM0102_MAGIC = b"SBEM0102"
CMD_LOG_SYNCED = 0x1201
# libambit's own libambit_sbem0102_command_request() header, byte-for-byte the prefix the
# SuuntoLink USB capture sends before the magic (see set_time.py's own note). BLE writes use
# write_nav.SBEM_WRITE_PREFIX (...01 01) instead.
USB_SBEM_PREFIX = bytes([0, 0, 0, 0, 1, 0])
SYNCED_FIELD_LEN = 0x15   # 20-byte NUL-padded timestamp + 1 synced byte, per the capture
TIMESTAMP_LEN = 0x14      # openambit's sbem0102_synced.timestamp[0x14]

# openambit get_ambit3_fw_gen(): product_id -> ordered (fw_version_threshold, gen_label),
# first threshold <= device fw wins. Only the Ambit3 family, Vertical, Traverse/Alpha carry a
# generation split; product ids from write_nav.PRODUCT_IDS.
_GEN_TABLES = {
    0x001B: [((2, 4, 1), "GEN4"), ((2, 2, 16), "GEN3"), ((2, 0, 4), "GEN2"), ((0, 0, 0), "GEN1")],
    0x001C: [((2, 4, 1), "GEN4"), ((2, 2, 16), "GEN3"), ((2, 0, 4), "GEN2"), ((0, 0, 0), "GEN1")],
    0x001E: [((2, 4, 1), "GEN4"), ((2, 2, 16), "GEN3"), ((2, 0, 4), "GEN2"), ((0, 0, 0), "GEN1")],
    0x002C: [((1, 1, 22), "VERT_GEN3"), ((1, 0, 27), "VERT_GEN2"), ((1, 0, 0), "VERT_GEN1")],
    0x002B: [((2, 0, 18), "TRAVERSE_GEN2"), ((1, 0, 4), "TRAVERSE_GEN1")],
    0x002D: [((2, 0, 18), "TRAVERSE_GEN2"), ((1, 0, 4), "TRAVERSE_GEN1")],
}

# openambit get_ambit3_driver_params(): gen_label -> log_synced_data_id. 0x00 means "openambit
# never mapped a real id for this generation" - we treat that as unsupported rather than
# sending entry 0x00 (which is not the synced field). Only GEN4 is a real, capture-confirmed
# value; the rest are 0x00 in openambit's own table and stay unsupported until captured.
_LOG_SYNCED_DATA_ID = {
    "GEN1": 0x00, "GEN2": 0x00, "GEN3": 0x00, "GEN4": 0x8B,
    "VERT_GEN1": 0x00, "VERT_GEN2": 0x00, "VERT_GEN3": 0x00,
    "TRAVERSE_GEN1": 0x00, "TRAVERSE_GEN2": 0x00,
}


def _fw_tuple(fw_version):
    """'2.5.11' -> (2, 5, 11). Accepts the device_info.py fw_version string."""
    parts = (fw_version or "0.0.0").split(".")
    return tuple(int(p) for p in (parts + ["0", "0", "0"])[:3])


def resolve_log_synced_id(product_id, fw_version):
    """(data_id, gen_label) for a watch, or (None, reason) when marking isn't supported.

    `product_id` is the USB product id (write_nav.PRODUCT_IDS key); `fw_version` the
    device_info.py string. Mirrors openambit's generation lookup exactly."""
    table = _GEN_TABLES.get(product_id)
    if table is None:
        codename = codename_for_pid(product_id) or f"0x{product_id:04x}" if product_id else "unknown"
        return None, f"no known log-synced generation for this device ({codename})"
    fw = _fw_tuple(fw_version)
    gen_label = None
    for threshold, label in table:
        if threshold <= fw:
            gen_label = label
            break
    if gen_label is None:
        return None, f"firmware {fw_version} predates every known generation for 0x{product_id:04x}"
    data_id = _LOG_SYNCED_DATA_ID.get(gen_label, 0x00)
    if not data_id:
        return None, (f"log-synced entry id not yet known for {gen_label} "
                      "(openambit never mapped it; needs a real SuuntoLink capture to confirm)")
    return data_id, gen_label


_GEN4_MIN = (2, 4, 1)


def gen4_supported_by_model(model, fw_version):
    """GEN4 support decided from the device_info model string + firmware, for transports with
    no USB product_id (BLE). Mirrors the Android MarkSynced.ts guard exactly: Ambit3 Peak/
    Sport/Run on GEN4 fw (>= 2.4.1) only. Vertical, Traverse/Alpha, Kailash, Ambit1/2 are all
    refused - openambit never mapped a real synced entry id for them, so guessing 0x8b would
    push a foreign entry to a watch that has no such field."""
    m = (model or "").lower()
    if "ambit3" not in m and "ambit 3" not in m:
        return False
    if "vertical" in m:
        return False
    return _fw_tuple(fw_version) >= _GEN4_MIN


def timestamp_from_header(header):
    """The move's LOCAL wall-clock ISO8601 string, exactly openambit's log_synced() format
    (`%Y-%m-%dT%H:%M:%S` from log_entry->header.date_time). Uses the header's own local
    fields, NOT the GPS/UTC-corrected timestamps used for GPX/FIT export."""
    return (f"{header['year']:04d}-{header['month']:02d}-{header['day']:02d}"
            f"T{header['hour']:02d}:{header['minute']:02d}:{header['msec'] // 1000:02d}")


def build_marker(timestamp, data_id, prefix=USB_SBEM_PREFIX):
    """The full 0x1201 payload for one move. `timestamp` is the local ISO8601 string."""
    field = timestamp.encode("ascii") + b"\x00"
    if len(field) > TIMESTAMP_LEN:
        raise ValueError(f"timestamp {timestamp!r} too long for the {TIMESTAMP_LEN}-byte field")
    field = field.ljust(TIMESTAMP_LEN, b"\x00") + b"\x01"   # pad to 20 + synced=1
    assert len(field) == SYNCED_FIELD_LEN
    return prefix + SBEM0102_MAGIC + bytes([data_id, len(field)]) + field


def _resolve_for_link(link):
    """(data_id, gen_label) or (None, reason) for an already-open USB Link."""
    from device_info import read_device_info                 # noqa: PLC0415
    info = read_device_info(link)
    return resolve_log_synced_id(getattr(link, "opened_product_id", None), info.get("fw_version"))


def mark_usb(link, entries, quiet=False):
    """Mark each (header, samples) entry synced over an open USB Link. Returns
    {"ok", "marked", "skipped", "reason"}. Never raises for an unsupported device - it
    reports and marks nothing, so a caller can leave the feature on harmlessly."""
    data_id, gen = _resolve_for_link(link)
    if data_id is None:
        if not quiet:
            print(f"  mark-synced: skipped - {gen}")
        return {"ok": True, "marked": 0, "skipped": len(entries), "reason": gen}
    marked = 0
    for header, _samples in entries:
        payload = build_marker(timestamp_from_header(header), data_id)
        link.command(CMD_LOG_SYNCED, payload, quiet=quiet)
        marked += 1
    if not quiet:
        print(f"  mark-synced: {marked} move(s) marked ({gen}, entry 0x{data_id:02x})")
    return {"ok": True, "marked": marked, "skipped": 0, "reason": None}


def mark_ble(bridge, entries, quiet=False):
    """EXPERIMENTAL, unverified: mark each entry synced over the BLE bridge, by analogy with
    the proven USB marker (identical SBEM body, BLE write prefix). `bridge` is anything with a
    `.command(command, payload)` (desktop ble_bridge.BleBridge / ServerLink).

    Applies the SAME GEN4-only guard as every other path: reads the watch's own device_info
    over the bridge and refuses anything that isn't an Ambit3 Peak/Sport/Run on GEN4 fw. It
    does NOT assume GEN4 for an unknown device - a blind 0x8b push to a Kailash/Traverse would
    send a foreign entry id to a watch that has no such field."""
    from device_info import read_device_info                  # noqa: PLC0415
    try:
        info = read_device_info(bridge)
        model, fw = info.get("model"), info.get("fw_version")
    except Exception as exc:                                   # noqa: BLE001
        model, fw = None, None
        if not quiet:
            print(f"  mark-synced (BLE): could not read device_info ({exc})")
    if not gen4_supported_by_model(model, fw):
        reason = (f"unsupported/unknown BLE watch ({model or 'unknown'} fw {fw or '?'}) - "
                  "only Ambit3 Peak/Sport/Run on GEN4 fw (>= 2.4.1) has a known synced id")
        if not quiet:
            print(f"  mark-synced (BLE): skipped - {reason}")
        return {"ok": True, "marked": 0, "skipped": len(entries), "reason": reason}
    data_id = _LOG_SYNCED_DATA_ID["GEN4"]
    if not quiet:
        print(f"  mark-synced (BLE, EXPERIMENTAL/unverified): {model} GEN4, entry 0x{data_id:02x}")
    marked = 0
    for header, _samples in entries:
        payload = build_marker(timestamp_from_header(header), data_id, prefix=SBEM_WRITE_PREFIX)
        bridge.command(CMD_LOG_SYNCED, payload)
        marked += 1
    return {"ok": True, "marked": marked, "skipped": 0, "reason": "experimental-ble"}


def _verify():
    """Byte-check build_marker() against the real SuuntoLink capture's two log_synced pushes."""
    try:
        from ambit_pcap import messages                      # noqa: PLC0415
    except Exception as exc:                                  # noqa: BLE001
        print(f"cannot import ambit_pcap: {exc}")
        return 1
    cap = "assets/pcap/lars_ambit3peak_13032017.pcap"
    try:
        outs = [m.payload for m in messages(cap)
                if m.command == CMD_LOG_SYNCED and not m.incoming and m.payload]
    except Exception as exc:                                  # noqa: BLE001
        print(f"cannot read {cap}: {exc} (run from the repo root)")
        return 1
    if not outs:
        print(f"no outgoing 0x1201 in {cap}")
        return 1
    ok = True
    for want in outs:
        # entry id + timestamp reconstructed from the capture itself, then rebuilt
        idx = want.find(SBEM0102_MAGIC) + len(SBEM0102_MAGIC)
        data_id = want[idx]
        ts = want[idx + 2:idx + 2 + TIMESTAMP_LEN].split(b"\x00")[0].decode("ascii")
        got = build_marker(ts, data_id)
        same = got == want
        ok = ok and same
        print(f"  {'OK ' if same else 'BAD'}  id=0x{data_id:02x} {ts}")
        if not same:
            print(f"        want {want.hex()}\n        got  {got.hex()}")
    print("verify:", "byte-exact" if ok else "MISMATCH")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verify", action="store_true",
                    help="byte-check the builder against the real SuuntoLink capture")
    ap.add_argument("--list", action="store_true", help="print the fw-gen -> id table")
    args = ap.parse_args()

    if args.list:
        for pid, table in _GEN_TABLES.items():
            for _thr, gen in table:
                did = _LOG_SYNCED_DATA_ID.get(gen, 0)
                mark = f"0x{did:02x}" if did else "unsupported (id unknown)"
                print(f"  0x{pid:04x} {PRODUCT_IDS.get(pid, ''):28s} {gen:14s} {mark}")
        return 0
    if args.verify:
        return _verify()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    raise SystemExit(main())
