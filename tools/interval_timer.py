#!/usr/bin/env python3
"""Set an exercise mode's Interval Timer (the on-watch repeated work/rest intervals shown
during an activity). A real, unblocked Ambit3 sport-mode feature - unlike the firmware-blocked
training program / planned moves.

The encoding was reverse-engineered byte-exact from real SuuntoLink captures (see
docs/explanation/custom-modes.md, "Resolves the interval-timer Type/Flags ambiguity", and this
file's own --selftest, which reproduces all four captures):

  * IntTimerFlags (a flat SETTING_FIELD) is the enable flag: 0 = off, 1 = on.
  * IntTimerCount (a flat SETTING_FIELD) is the repetition count (SuuntoLink's own default 99).
  * The type is stored in the interval-slot Type byte: 1 = Time (mm'ss, value in whole seconds),
    0 = Distance (km, value in meters - the same meters convention as Autolap).
  * "High" is the FULL slot's own Len (uint32); "Low" is the THIRD slot's MaxLimit (uint16).
    The full slot's Type and the next short slot's Type both carry the type byte; the low slot's
    Type stays 0. Confirmed for both Time and Distance captures.
  * The last short slot (IntervalSlots[5]) is NOT an interval slot at all - it holds the mode's
    backlight/display/quick-navigation advanced settings (custom_modes.py). This tool never
    touches it: it only rewrites the 40-byte span from IntTimerFlags through IntervalSlots[4].

Same real transport and discipline as custom_modes_field_write_test.py (write_nav.send_plan:
CMD_DATA_WRITE chunks + CMD_DATA_TAIL padded-region SHA256, commit=False - never CMD_NAV_COMMIT
for CustomModes, Finding 27), the mode's settings-block offset found live by walking the real
BXml tag tree (reused find_settings_base), and every offset computed from custom_modes.py's own
declared SETTING_FIELDS / INTERVAL_SLOT_* layout via struct.calcsize - no hardcoded magic.

    ./tools/interval_timer.py --mode Cycling --enable --type time --high 125 --low 390
    ./tools/interval_timer.py --mode Cycling --enable --type distance --high 5000 --low 3000 --reps 5
    ./tools/interval_timer.py --mode Cycling --disable
    ./tools/interval_timer.py --mode Cycling --enable --type time --high 125 --low 390 --write
    ./tools/interval_timer.py --selftest
"""
import argparse
import datetime
import json
import pathlib
import struct
import sys

import ambit_format as F
import custom_modes as CM
from ambit_pcap import FlashImage
from custom_modes_field_write_test import find_settings_base
from write_nav import Link, check_memory_map, read_flash, read_memory_map, send_plan

BACKUP_DIR = pathlib.Path.home() / "AmbitAppBackups" / "custom_modes"

TYPE_TIME = 1        # value in whole seconds, shown as mm'ss
TYPE_DISTANCE = 0    # value in meters, shown as km


def _size(fields):
    return sum(struct.calcsize("<" + fmt) for _, fmt in fields)


def _setting_offset(name):
    """Byte offset of a flat SETTING_FIELDS entry within the settings block (offset 0 = the
    start of the 64-byte Name), from custom_modes.py's own declared field order."""
    off = 64
    for n, fmt in CM.SETTING_FIELDS:
        if n == name:
            return off
        off += struct.calcsize("<" + fmt)
    raise KeyError(name)


NAME_LEN = 64
SETTINGS_SIZE = _size(CM.SETTING_FIELDS)                 # 32
FULL_SLOT_SIZE = _size(CM.INTERVAL_SLOT_FULL)            # 12
SHORT_SLOT_SIZE = _size(CM.INTERVAL_SLOT_SHORT)          # 6

FLAGS_OFF = _setting_offset("IntTimerFlags")            # 92
COUNT_OFF = _setting_offset("IntTimerCount")            # 94
FULL_SLOT_OFF = NAME_LEN + SETTINGS_SIZE                # 96
SLOT5_OFF = FULL_SLOT_OFF + FULL_SLOT_SIZE + 4 * SHORT_SLOT_SIZE  # 132

# The one contiguous span this tool ever rewrites: IntTimerFlags/IntTimerCount + the full slot
# + short slots 1-4. Stops exactly before IntervalSlots[5] (the advanced-settings slot).
SPAN_START = FLAGS_OFF          # 92
SPAN_END = SLOT5_OFF            # 132
SPAN_LEN = SPAN_END - SPAN_START  # 40

U16_MAX = 0xFFFF
U32_MAX = 0xFFFFFFFF


def build_interval_span(enabled, type_byte, high, low, reps):
    """The exact 40 bytes for [IntTimerFlags .. end of IntervalSlots[4]] (relative to the
    settings-block base + SPAN_START). Byte-exact against every real SuuntoLink capture - see
    --selftest. When disabled, everything but IntTimerCount is zeroed, matching the captures."""
    if not enabled:
        type_byte, high, low = 0, 0, 0
    buf = bytearray(SPAN_LEN)

    def put(abs_off, fmt, value):
        struct.pack_into("<" + fmt, buf, abs_off - SPAN_START, value)

    put(FLAGS_OFF, "H", 1 if enabled else 0)
    put(COUNT_OFF, "H", reps)
    # Full slot (IntervalSlots[0]): Type + Len(=high). Flags/MaxLimit/MinLimit/Padding stay 0.
    put(FULL_SLOT_OFF + 1, "B", type_byte)                     # Type
    put(FULL_SLOT_OFF + 8, "I", high)                          # Len = high threshold
    # Short slot 1 (IntervalSlots[1]): only its Type carries the type byte.
    s1 = FULL_SLOT_OFF + FULL_SLOT_SIZE
    put(s1 + 1, "B", type_byte)                                # Type
    # Short slot 2 (IntervalSlots[2]): its MaxLimit holds the low threshold; Type stays 0.
    s2 = s1 + SHORT_SLOT_SIZE
    put(s2 + 2, "H", low)                                      # MaxLimit = low threshold
    # Short slots 3 and 4 stay all-zero.
    return bytes(buf)


def apply_span(region, base, span):
    """The CustomModes region with `span` written at the mode's interval-timer span. Only those
    40 bytes can change; everything else (including IntervalSlots[5]) is left byte-identical."""
    out = bytearray(region)
    out[base + SPAN_START:base + SPAN_END] = span
    return bytes(out)


# --- selftest: prove the encoder reproduces the real captures byte-exact ------------------
_SELFTEST_CAPTURES = [
    # (file, mode, enabled, type_byte, high, low, reps)
    ("assets/ambit3 pcap/v2/intervaltimerhigh02'05low06'30", "Cycling", True, TYPE_TIME, 125, 390, 99),
    ("assets/ambit3 pcap/v2/intervaltimerkmlow3high5", "Cycling", True, TYPE_DISTANCE, 5000, 3000, 99),
    ("assets/ambit3 pcap/v2/disableintervaltimerpace", "Cycling", False, 0, 0, 0, 99),
    ("assets/ambit3 pcap/v2/disableintervaltimerkm", "Cycling", False, 0, 0, 0, 99),
]


def _region_from_capture(path):
    img = FlashImage.from_pcap(path)
    return img.read(CM.CUSTOM_MODES_BASE, CM.CUSTOM_MODES_SIZE, fill=0x00)


def selftest():
    root = pathlib.Path(__file__).resolve().parent.parent
    failures = []
    for rel, mode, enabled, tb, high, low, reps in _SELFTEST_CAPTURES:
        path = root / rel
        if not path.exists():
            failures.append(f"{rel}: capture file missing (local corpus)")
            continue
        region = _region_from_capture(str(path))
        base = find_settings_base(region, mode)
        if base is None:
            failures.append(f"{rel}: mode {mode!r} not found")
            continue
        want = region[base + SPAN_START:base + SPAN_END]
        got = build_interval_span(enabled, tb, high, low, reps)
        if got != want:
            failures.append(f"{rel}: span mismatch\n    want {want.hex()}\n    got  {got.hex()}")
            continue
        # And prove the patch touches ONLY the span: re-applying the capture's own params must
        # reproduce the whole region byte-for-byte.
        rebuilt = apply_span(region, base, got)
        if rebuilt != region:
            diffs = sum(1 for a, b in zip(rebuilt, region) if a != b)
            failures.append(f"{rel}: apply_span changed {diffs} byte(s) outside the span")
    if failures:
        print("SELFTEST FAILED:")
        for f_ in failures:
            print("  " + f_)
        return 1
    print(f"SELFTEST OK: {len(_SELFTEST_CAPTURES)} captures reproduced byte-exact "
          "(enable time, enable distance, disable pace, disable km).")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mode", metavar="NAME",
                    help="the exercise mode's current real name, exactly as custom_modes.py shows")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--enable", dest="enable", action="store_true", help="turn the interval timer on")
    grp.add_argument("--disable", dest="enable", action="store_false", help="turn it off")
    ap.set_defaults(enable=None)
    ap.add_argument("--type", choices=["time", "distance"],
                    help="time = mm'ss (value in seconds); distance = km (value in meters)")
    ap.add_argument("--high", type=int, help="high threshold: seconds (time) or meters (distance)")
    ap.add_argument("--low", type=int, help="low threshold: seconds (time) or meters (distance)")
    ap.add_argument("--reps", type=int, default=99, help="repetitions (SuuntoLink default 99)")
    ap.add_argument("--write", action="store_true",
                    help="actually write; without this, only reads and reports what would change")
    ap.add_argument("--json", action="store_true", help="print one JSON line (for the backend)")
    ap.add_argument("--selftest", action="store_true",
                    help="prove the encoder reproduces the real captures byte-exact; no watch")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    quiet = args.json

    def out(msg):
        if not quiet:
            print(msg)

    def finish(payload, code):
        if quiet:
            print(json.dumps(payload))
        return code

    if not args.mode or args.enable is None:
        msg = "need --mode and one of --enable/--disable"
        out(f"ABORT: {msg}")
        return finish({"ok": False, "error": msg}, 1)

    enabled = args.enable
    type_byte = TYPE_TIME if args.type == "time" else TYPE_DISTANCE
    high = args.high or 0
    low = args.low or 0
    reps = args.reps

    if enabled:
        if args.type is None or args.high is None or args.low is None:
            msg = "enabling needs --type, --high and --low"
            out(f"ABORT: {msg}")
            return finish({"ok": False, "error": msg}, 1)
    # Bounds: Len(high) is u32, MaxLimit(low) and IntTimerCount(reps) are u16.
    for name, value, hi in (("high", high, U32_MAX), ("low", low, U16_MAX), ("reps", reps, U16_MAX)):
        if not (0 <= value <= hi):
            msg = f"{name}={value} out of range (0..{hi})"
            out(f"ABORT: {msg}")
            return finish({"ok": False, "error": msg}, 1)

    span = build_interval_span(enabled, type_byte, high, low, reps)

    link = Link(dry_run=False, verbose=not quiet)
    link.open()

    out("Checking memory map before touching anything...")
    found = read_memory_map(link)
    if not check_memory_map(found):
        msg = "memory map does not match expectations, refusing to write."
        out(f"ABORT: {msg}")
        return finish({"ok": False, "error": msg}, 1)

    out("Reading CustomModes...")
    fresh = read_flash(link, F.CUSTOM_MODES_BASE, F.CUSTOM_MODES_REGION_SIZE, label="CustomModes")

    base = find_settings_base(fresh, args.mode)
    if base is None:
        msg = f"{args.mode!r} not found as a real exercise mode in this reply."
        out(f"ABORT: {msg}")
        return finish({"ok": False, "error": msg}, 1)
    out(f"Found {args.mode!r} settings block at region offset {base} (0x{base:x})")

    # Bounds-check the computed span against the region before touching a byte.
    if base + SPAN_END > len(fresh):
        msg = f"span end {base + SPAN_END} past region size {len(fresh)} - refusing to write."
        out(f"ABORT: {msg}")
        return finish({"ok": False, "error": msg}, 1)

    modified = apply_span(fresh, base, span)
    changed = sum(1 for a, b in zip(fresh, modified) if a != b)
    summary = {"enabled": enabled, "type": args.type if enabled else None,
               "high": high if enabled else None, "low": low if enabled else None, "reps": reps}
    out(f"Interval timer -> {summary}")
    out(f"Would change {changed} byte(s) (only within the 40-byte interval span).")

    if not args.write:
        out("Read-only (pass --write to actually send this).")
        return finish({"ok": True, "dryRun": True, "mode": args.mode,
                       "intervalTimer": summary, "bytesChanged": changed}, 0)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    backup_path = BACKUP_DIR / f"before_intervaltimer_{stamp}.bin"
    backup_path.write_bytes(fresh)
    out(f"Backup written to {backup_path}")

    # Only the used extent, hashed as written, and no 0x0b04: a padded region gets a hash the
    # watch rejects after a restart (err:62), and 0x0b04 empties its POI lists.
    payload = bytes(modified[:CM.used_extent(modified)])
    flash = FlashImage()
    flash.write(F.CUSTOM_MODES_BASE, payload)
    layout = [(f"CustomModes ({args.mode} interval timer)", F.CUSTOM_MODES_BASE, payload),
              ("tail", F.CUSTOM_MODES_BASE, None)]

    out(f"Writing the used extent ({len(payload)} bytes) + CMD_DATA_TAIL (its SHA256)...")
    send_plan(link, flash, layout, commit=False)
    out("  send_plan returned without raising - no protocol-level rejection seen")

    out("Reading CustomModes back to verify...")
    after = read_flash(link, F.CUSTOM_MODES_BASE, len(payload), label="CustomModes")
    if bytes(after) == payload:
        out("\nSUCCESS: region read back byte-for-byte matching the intended edit.")
        return finish({"ok": True, "dryRun": False, "mode": args.mode,
                       "intervalTimer": summary, "bytesChanged": changed}, 0)
    diffs = sum(1 for a, b in zip(after, payload) if a != b)
    mismatch_path = BACKUP_DIR / f"after_intervaltimer_{stamp}.bin"
    mismatch_path.write_bytes(after)
    out(f"\nMISMATCH: {diffs} bytes differ from what was intended. Saved to {mismatch_path}.")
    return finish({"ok": False, "error": f"{diffs} bytes differ after write",
                   "mismatchPath": str(mismatch_path), "backupPath": str(backup_path)}, 1)


if __name__ == "__main__":
    sys.exit(main())
