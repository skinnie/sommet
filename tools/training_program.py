#!/usr/bin/env python3
"""Writes planned moves to the Ambit3's TrainingProgram flash region - the NATIVE Movescount-era
"Today 1/2" planned-move card (Movescount-era user guide §3.39: TIME mode -> [Next]). DRY-RUN BY
DEFAULT: without --write nothing is emitted, only the exact bytes are logged.

HARDWARE-CONFIRMED 2026-09-03 on an Ambit3 Sport (Finch, fw 2.4.17): one item dated today ->
TIME mode -> [Next] displays the planned move. The format is no longer a structural guess: the
reader and its display gate were decompiled from the watch's own MSP430X firmware (Ghidra
TI_MSP430X; assets/Firmware/re-out/sfi2_code_recovery_notes.md, "MSP430X hunt (pass 7)"). The
one field that had defeated every earlier hardware test - header bytes 4..7 - is a fixed
firmware SIGNATURE (HEADER_SIGNATURE below); everything else (12-byte header shape, 40-byte
items, count, calendar base date) had been right since Finding 59. Gate, from the decompile:
the view is shown iff settings[0x1FE] != 0 and d >= -2, d = days(baseDate)+dayOffset[first
pending item >= today] - days(today); d == 0 -> "Today N/M", d < 0 -> "Training program
completed!", d > 0 -> "in N days" / weekday (<= 7 d) / dd.mm. Reload is immediate after the
region write (post-write hook) - no restart. Up to 60 items; base-date year 2013-2099.

    # Path (1) re-test: one planned move dated TODAY, then check the watch's reminder/day
    # screen (NOT the WORKOUT menu - that's the separate Workout-Planner/guidance path).
    ./tools/training_program.py --name "Long run" --duration 60 --intensity 3   # dry-run
    ./tools/training_program.py --name "Long run" --duration 60 --write         # real write
    ./tools/training_program.py --name "Long run" --date 2026-08-20 --write     # dated tomorrow

Shares the low-level watch transport (`Link`, `send_plan`, the memory-map check) with
`write_nav.py` by importing it, the same way `custom_modes.py`/`apps.py`/`exercise_log.py`
do - not by being folded into `write_nav.py` itself, which is specifically for the
navigation database (routes/waypoints/POIs).
"""

import argparse
import datetime
import json
import struct
import sys

import ambit_format as F
from ambit_pcap import FlashImage
from write_nav import CMD_DEVICE_INFO, Link, check_memory_map, read_memory_map, send_plan

# HEADER now READ BYTE-FOR-BYTE, 2026-08-19, out of the ACTUAL createBinary the Ambit3-Peak
# save path calls (TrainingProgramAreaConverter::createBinary == FUN_007f4770; EmuDevice::
# saveTrainingProgram at FUN_006fabf0 line ~664492 calls exactly this, NOT any Emu-specific
# packer). No "first 8 bytes hash/version" - the 12-byte header is fully determined:
#   off 0  u16  year   } base date = EARLIEST item's date, packed [u16 y][u8 m][u8 d] via the
#   off 2  u8   month  }  FUN_00531d20 JDN->Gregorian converter. It derives Y/M/D and packs
#   off 3  u8   day    }  them - it does NOT store a day-count (kills the days2000 theory).
#   off 4  4B   = SIGNATURE 3C 46 50 5A (CORRECTED 2026-09-03 from the MSP430X watch firmware:
#                 the desktop packer copies the prior region's bytes 4..7 - which on a
#                 Movescount-initialised watch were always this constant; on an erased region
#                 that copied 0xFF and the firmware REJECTED the header. See build_training_program)
#   off 8  u16  item count = (end-start)/40
#   off 10 u16  = 0xFFFF  (createBinary only overwrites the low u16 of a 0xFFFFFFFF-seeded field;
#                          there is NO u32-count "emu" variant - that was a misread of the PARSE
#                          side FUN_00770100, not the packer)
# The closing hash MODE is HASH_WRITTEN over the used extent - confirmed firmware-accepted
# (Finding 59: the watch's own 0x0b21 hash matches SHA256 of header+items after our write).
TRAINING_ITEM_SIZE = 40
# Header bytes 4..7: the watch firmware's signature constant (see build_training_program()).
HEADER_SIGNATURE = b"\x3c\x46\x50\x5a"


def build_training_item(activity_id, duration_minutes, intensity, name,
                         day_offset=0, completed=False, move_id=0, distance=0):
    """One 40-byte TrainingProgram item. Layout REFINED 2026-08-09 (training_program_andre.md
    Finding 29) from a closer read of TrainingProgramAreaConverter::createBinary/parse in the
    decompiled backend - medium-high confidence, still not byte-verified against a real capture
    (none exists; Movescount is dead):

        off 0  u8   day_offset from the header's base date (parse multiplies it by 24h). This
                    is the real scheduling model - a move's date = base_date + day_offset days.
                    0 is VALID (the earliest/only move IS the base date). This corrects the
                    earlier "start_time byte, 0 is invalid" reading (Finding 24): what the real
                    client rejects with "no valid start time" is the JSON startTime that feeds
                    the HEADER base date, not this per-item byte.
        off 1  u8   completed (0/1)
        off 2  u16  activityId
        off 4  u32  moveId
        off 8  u32  distance (metres)
        off 12 u16  duration (MINUTES - createBinary divides JSON seconds by 60)
        off 14 u8   intensity (1-5)
        off 15 u8   padding (0)
        off 16 23B  activityName (UTF-8, null-padded/truncated - strncpy 0x17). NOTE: starts
                    at offset 16, not 15 as the earlier version had it.
        off 39 u8   padding (0)

    UPDATED 2026-08-22: charset changed iso-8859-15 -> utf-8. This struct itself was never
    verified against a real capture (see the ambit-app project memory's TrainingProgram
    finding - decompiled-code-derived, medium confidence), so "ISO-8859" here was always an
    assumption, not a confirmed byte value the way custom_modes.py's was. Changed anyway for
    consistency with what IS now confirmed on real hardware: every other watch NAME field in
    this project (custom_modes.py, apps.py, exercise_log.py) turned out to be UTF-8, proven
    by real mojibake on André's French Ambit3 Sport - same firmware, almost certainly the
    same string convention throughout, but flagging this one specifically as still unverified.
    """
    name_field = name.encode("utf-8", "replace")[:23]
    name_field = name_field.decode("utf-8", "ignore").encode("utf-8")
    name_field += b"\0" * (23 - len(name_field))
    item = struct.pack("<BBHIIHB", day_offset & 0xFF, int(completed), activity_id, move_id,
                        distance, duration_minutes, intensity & 0xFF)  # 15 bytes, off 0..14
    item += b"\0"          # off 15 padding
    item += name_field     # off 16..38
    item += b"\0" * (TRAINING_ITEM_SIZE - len(item))  # off 39 padding
    assert len(item) == TRAINING_ITEM_SIZE, len(item)
    return item


def build_training_item_emu(activity_id, intensity, name, day_offset=0):
    """REFUTED 2026-08-19 — DO NOT USE; kept only as a record of a wrong turn. This assumed the
    Emu packer stored only date+activityId+intensity+name (leaving moveId/distance/duration as
    0xFF). Parsing the descriptor table at VA 0x9bc508 out of SDSApplicationServer.exe proved
    otherwise: FUN_00725ac0 reads Activity.ID->off2, ID->off4, Distance->off8, Duration->off12
    from that table, i.e. EXACTLY the original build_training_item() layout. Use
    build_training_item(); it is now byte-confirmed correct.

    (original, wrong, docstring:) One 40-byte item in the EMU-SPECIFIC layout (2026-08-19, from
    `EmuDevice::handleMCServiceTrainingPrograms` -> FUN_00770100 in the SuuntoLink decompile
    `assets/WIndows apps/Suuntolink/.../SDSApplicationServer.exe.c`, the Ambit3-Peak's OWN
    training-program packer, NOT the generic TrainingProgramAreaConverter build_training_item()
    uses).

    Enumerating every plan field the Emu packer reads proves it stores ONLY: date (day_offset),
    Activity.ID, Activity.LocalizedName (name), and Intensity. It NEVER reads Duration, Distance,
    moveId, Completed or DailyOrdinal - those bytes stay at the 0xFF the region is memset to.
    Every prior hardware test wrote zeros/values there instead, which likely malformed the record
    and made the firmware drop it. So this layout leaves off4..off13 as 0xFF:

        off 0     u8    day_offset from header base date
        off 1     u8    0x00  (Emu clears this byte)
        off 2-3   u16   activityId          (best-confidence position; the exact byte FUN_00725ac0
                                             writes is via a descriptor table not fully decoded)
        off 4-13  10B   0xFF  (moveId/distance/duration UNSET - the novel part)
        off 14    u8    intensity (1-5)
        off 15    u8    0xFF
        off 16-38 23B   activityName (ISO-8859, null-padded)  strncpy 0x17
        off 39    u8    0x00
    """
    item = bytearray(b"\xff" * TRAINING_ITEM_SIZE)
    item[0] = day_offset & 0xFF
    item[1] = 0x00
    struct.pack_into("<H", item, 2, activity_id)
    item[14] = intensity & 0xFF
    item[15] = 0xFF
    # CORRECTED 2026-08-22: was iso-8859-15, see build_training_item()'s own comment above.
    name_field = name.encode("utf-8", "replace")[:23]
    name_field = name_field.decode("utf-8", "ignore").encode("utf-8")
    item[16:16 + len(name_field)] = name_field
    item[16 + len(name_field):39] = b"\0" * (39 - (16 + len(name_field)))
    item[39] = 0x00
    assert len(item) == TRAINING_ITEM_SIZE, len(item)
    return bytes(item)


SUUNTO_EPOCH = datetime.date(2000, 1, 1)  # SUUNTO_DAYS_AFTER_1_1_2000 (Finding 58's App date-gate)


def pack_base_date(base_date, date_format):
    """The 4-byte off0 header field. SETTLED 2026-08-19: it is calendar [u16 y][u8 m][u8 d].
    createBinary (FUN_007f4770) runs the earliest start time through FUN_00531d20 THREE times to
    pull the year, month and day components out, then packs them as [u16 year][u8 month][u8 day]
    - the JDN converter is used to DERIVE the calendar fields, not to store a day-count. So the
    stored value is NOT days-since-2000. 'days2000' is kept only as a refuted alternative for the
    record; do not expect it to surface a move.

        ymd       [u16 year][u8 month][u8 day]        - createBinary-exact (default; use this)
        days2000  [u32 days since 2000-01-01]         - REFUTED by createBinary; record only
    """
    if date_format == "ymd":
        return struct.pack("<HBB", base_date.year, base_date.month, base_date.day)
    if date_format == "days2000":
        return struct.pack("<I", (base_date - SUUNTO_EPOCH).days)
    raise ValueError(date_format)


def build_training_program(items, base_date, date_format="ymd", emu=False):
    """items: a list of build_training_item() results. See the EXPERIMENTAL notice above.

    HEADER (12 bytes) - base-date packing DECODED (Finding 59, 2026-08-13, from
    TrainingProgramAreaConverter::createBinary's FUN_00531d20 JDN->Gregorian converter),
    which closed this format's last unknown:

        off 0  u16  year   (little-endian)
        off 2  u8   month  (1-12)
        off 3  u8   day    (1-31)
        off 4  4B   = SIGNATURE 3C 46 50 5A - REQUIRED. The watch firmware validates these
                    bytes (strictly increasing, last < 0x65) before parsing anything; 0xFFFFFFFF
                    (what every hardware test before 2026-09-03 wrote) is REJECTED and the whole
                    program is treated as empty. Decompiled from the MSP430X watch firmware, see
                    build_training_program()'s inline comment.
        off 8  u16  item count
        off 10 u16  = 0xFFFF

    emu=True is REFUTED (2026-08-19): the "u32 count, off10=0x0000" idea came from FUN_00770100,
    which is the PARSE side (handleMCServiceTrainingPrograms). The PACKER the Ambit3-Peak save
    path actually calls is the generic createBinary (FUN_007f4770), which writes a u16 count and
    leaves off 10 = 0xFFFF. Kept selectable only to reproduce the refuted trial.

    `base_date` (a datetime.date) is the reference date every item's day_offset counts from -
    the earliest move's date. Earlier writes packed seconds/hours-since-epoch here, producing a
    garbage date, which is why nothing surfaced (Finding 59). For the Path (1) re-test we pack a
    real calendar date so the watch can match "today"."""
    # Header bytes 4..7 are a fixed SIGNATURE the watch firmware validates before it will
    # parse the region at all (MSP430X watch fw 2.4.17, FUN_00059a3a via the TrainingProgram
    # reload FUN_0004c250 -> FUN_00039262; decompiled 2026-09-03, assets/Firmware/re-out/
    # sfi2_code_recovery_notes.md "MSP430X hunt (pass 7)"): four strictly increasing bytes with
    # the last < 0x65, firmware constant 3C 46 50 5A. An invalid signature makes the firmware
    # substitute an EMPTY program {2001-01-01, sig, count 0}, which hides the TIME-mode "Today"
    # view and silences the daily pop-up - exactly the symptom of every prior hardware test,
    # all of which wrote 0xFFFFFFFF here. The desktop packer's "copy the prior binary's bytes
    # 4..7 verbatim" (Finding 59) only ever propagated this constant on a Movescount-initialised
    # watch; on an erased region it propagated 0xFF and the header was rejected.
    tail = struct.pack("<4sI", HEADER_SIGNATURE, len(items)) if emu \
        else struct.pack("<4sHH", HEADER_SIGNATURE, len(items), 0xFFFF)
    header = pack_base_date(base_date, date_format) + tail
    assert len(header) == 12, len(header)
    blob = header + b"".join(items)
    flash = FlashImage()
    flash.write(F.TRAINING_PROGRAM_BASE, blob)
    layout = [("TrainingProgram data", F.TRAINING_PROGRAM_BASE, blob),
              ("tail", F.TRAINING_PROGRAM_BASE, None)]
    return flash, layout


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--name", default="Test", help="the workout's name (up to 23 characters)")
    ap.add_argument("--duration", type=int, default=30, help="planned duration in minutes")
    ap.add_argument("--intensity", type=int, default=3, help="planned intensity, 0-255")
    ap.add_argument("--activity-id", type=int, default=3,
                     help="ActivityID (default 3 = Running)")
    ap.add_argument("--move-id", type=lambda x: int(x, 0), default=0,
                     help="item moveId (u32, off4 = Plan.ID). THE NEW EXPERIMENT (unblocked "
                          "2026-08-19): Finding 60 said planned moves and guided workouts are one"
                          " mechanism and the prior blocker was 'the schedule points at a workout"
                          " that does not exist' - impossible to fix until guided_workout.py could"
                          " install a REAL native guidance workout (WORKOUT-menu entry). Now it"
                          " can. So install one first, note its ruleId + activityId, then set"
                          " --move-id=<ruleId> --activity-id=<activityId> here so the planned move"
                          " points at a workout that IS present. 0 = the old (workout-absent) tests.")
    ap.add_argument("--distance", type=int, default=0,
                     help="item planned distance in METRES (the day screen shows activity/"
                          "duration/distance). 0 = the earlier tests.")
    ap.add_argument("--day-offset", type=int, default=0,
                     help="days from the header base date (0 = the base/earliest move itself);"
                          " see build_training_item()'s docstring (Finding 29)")
    ap.add_argument("--date", type=datetime.date.fromisoformat, default=datetime.date.today(),
                     help="header base date, YYYY-MM-DD (default: today). The move's real date"
                          " is this + --day-offset days. Path (1) re-test wants it to land on"
                          " today so the watch fires a training-day reminder.")
    ap.add_argument("--date-format", choices=("ymd", "days2000"), default="ymd",
                     help="how the 4-byte header base date is encoded (see pack_base_date):"
                          " 'ymd' = Finding 59's calendar decode, 'days2000' = u32 days since"
                          " 2000-01-01. Discriminates whether the date encoding is the blocker.")
    ap.add_argument("--emu", action="store_true",
                     help="use the Ambit3-Peak EMU-SPECIFIC record layout decoded from the "
                          "SuuntoLink decompile (EmuDevice::handleMCServiceTrainingPrograms / "
                          "FUN_00770100): stores ONLY date+activityId+intensity+name, leaving "
                          "moveId/distance/duration/completed as 0xFF. This is the shape the "
                          "Ambit3 Peak's own firmware was fed; every prior test used the fuller "
                          "generic layout. Ignores --duration/--distance/--move-id.")
    ap.add_argument("--clear", action="store_true",
                     help="restore the region to its pristine empty state (all-0xFF, byte-"
                          "identical to a never-written region) instead of writing a move -"
                          " use with --write to undo a re-test afterwards")
    ap.add_argument("--plan", metavar="FILE",
                     help="write a WHOLE program from a JSON file instead of the single-item"
                          " flags: {\"items\": [{\"date\": \"YYYY-MM-DD\", \"activityId\": 3,"
                          " \"durationMinutes\": 45, \"intensity\": 3, \"name\": \"Long run\","
                          " \"distance\": 0, \"moveId\": 0}, ...]}. The header base date is the"
                          " EARLIEST item's date; each item's dayOffset is derived from it (must"
                          " fit in 0..255 days); at most 60 items (the firmware's cap). Items"
                          " are sorted by date. This is what the desktop backend calls.")
    ap.add_argument("--json", action="store_true",
                     help="print one final JSON line {ok, written, count, baseDate, dates,"
                          " bytes} - for desktop/backend/server.py (its usual last-JSON-line"
                          " convention). Human-readable lines still print before it.")
    ap.add_argument("--write", action="store_true",
                     help="actually emits; without this option nothing is sent")
    ap.add_argument("--verbose", action="store_true", help="logs every 64-byte report")
    args = ap.parse_args()

    def emit_json(ok, **extra):
        if args.json:
            print(json.dumps({"ok": ok, "written": bool(args.write) and ok, **extra}))

    plan_items = None
    if args.plan:
        try:
            with open(args.plan) as f:
                plan = json.load(f)
            raw = plan["items"] if isinstance(plan, dict) else plan
            if not isinstance(raw, list) or not raw:
                raise ValueError("plan has no items")
            if len(raw) > 60:
                raise ValueError(f"{len(raw)} items - the watch stores at most 60 planned moves")
            parsed = []
            for i, it in enumerate(raw):
                d = datetime.date.fromisoformat(str(it["date"]))
                if not 2013 <= d.year <= 2099:
                    raise ValueError(f"item {i}: year {d.year} outside the firmware's 2013-2099")
                parsed.append((d, it))
            parsed.sort(key=lambda t: t[0])
            base = parsed[0][0]
            if (parsed[-1][0] - base).days > 255:
                raise ValueError("plan spans more than 255 days (dayOffset is one byte)")
            plan_items = [(d, it) for d, it in parsed]
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as e:
            print(f"bad --plan: {e}")
            emit_json(False, error=str(e))
            return 2
        args.date = plan_items[0][0]

    link = Link(dry_run=not args.write, verbose=args.verbose)
    if args.write:
        print("!! REAL WRITE requested - native planned moves (TrainingProgram region;"
              " hardware-confirmed 2026-09-03, see module docstring)")
        link.open()
    else:
        print("dry-run mode: not a byte will be emitted")

    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    check_memory_map(read_memory_map(link))

    if plan_items is not None:
        items = []
        dates = []
        for d, it in plan_items:
            items.append(build_training_item(
                int(it.get("activityId", 3)), int(it.get("durationMinutes", 0)),
                int(it.get("intensity", 3)), str(it.get("name", "Move")),
                day_offset=(d - args.date).days, completed=bool(it.get("completed", False)),
                move_id=int(it.get("moveId", 0)), distance=int(it.get("distance", 0))))
            dates.append(d.isoformat())
        flash, layout = build_training_program(items, base_date=args.date)
        blob = layout[0][2]
        print(f"  plan: {len(items)} planned move(s), base date {args.date.isoformat()}, "
              f"{len(blob)} bytes")
        for (d, it), item in zip(plan_items, items):
            print(f"    {d.isoformat()}  act={it.get('activityId', 3):<3} "
                  f"{int(it.get('durationMinutes', 0)):>4} min  int={it.get('intensity', 3)}  "
                  f"{str(it.get('name', 'Move'))[:23]!r}")
        send_plan(link, flash, layout, commit=False)
        total = sum(len(payload) for _, payload, _ in link.sent)
        print(f"\n{len(link.sent)} messages, {total} payload bytes"
              + ("" if args.write else " — nothing was emitted"))
        emit_json(True, count=len(items), baseDate=args.date.isoformat(), dates=dates,
                  bytes=len(blob))
        return 0

    if args.clear:
        blob = b"\xff" * F.TRAINING_PROGRAM_REGION_SIZE
        flash = FlashImage()
        flash.write(F.TRAINING_PROGRAM_BASE, blob)
        layout = [("TrainingProgram (cleared to empty)", F.TRAINING_PROGRAM_BASE, blob),
                  ("tail", F.TRAINING_PROGRAM_BASE, None)]
        print(f"  CLEAR: restoring {len(blob)} bytes of 0xFF (pristine empty region)")
    else:
        if args.emu:
            item = build_training_item_emu(args.activity_id, args.intensity, args.name,
                                            day_offset=args.day_offset)
        else:
            item = build_training_item(args.activity_id, args.duration, args.intensity, args.name,
                                        day_offset=args.day_offset, move_id=args.move_id,
                                        distance=args.distance)
        flash, layout = build_training_program([item], base_date=args.date,
                                               date_format=args.date_format, emu=args.emu)
        move_date = args.date + datetime.timedelta(days=args.day_offset)
        print(f"  layout: {'EMU (date+activity+intensity+name, rest 0xFF)' if args.emu else 'generic'}")
        print(f"  header base date: {args.date.isoformat()} "
              f"(format={args.date_format}, packed "
              f"{pack_base_date(args.date, args.date_format).hex(' ')})")
        extra = "" if args.emu else (f" duration={args.duration}min moveId={args.move_id} "
                                     f"distance={args.distance}m")
        print(f"  item: name={args.name!r} activityId={args.activity_id} "
              f"intensity={args.intensity}{extra} -> move date {move_date.isoformat()}")
        print(f"  item bytes: {item.hex(' ')}")
    send_plan(link, flash, layout, commit=False)

    total = sum(len(payload) for _, payload, _ in link.sent)
    reports = sum(len(r) for _, _, r in link.sent)
    print(f"\n{len(link.sent)} messages, {total} payload bytes, "
          f"{reports} reports of 64 bytes"
          + ("" if args.write else " — nothing was emitted"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
