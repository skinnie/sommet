#!/usr/bin/env python3
"""Full, read-only backup of an Ambit3 watch - everything the watch stores, in one run.

Why this exists: the pre-write backups this project made for firmware work only ever captured
the firmware-work regions (Apps, CustomModes, Routes, TrainingProgram, settings). They never
captured the recorded moves (ExerciseLog) or the 30-day step / activity / recovery history
(DeviceHistory). That is exactly why a forgotten, un-synced run was unrecoverable once a new
move overwrote the log head (2026-10-21). This tool captures all of it, so the last backup
always holds every recorded move and the recent wellness history.

What it saves, into backups/watch-YYYYmmdd-HHMMSS/ (or --out DIR):
  regions/*.bin     raw dumps of every data region the watch declares (0x0b21 memory map):
                    Apps, CustomModes, Routes, Waypoints, TrainingProgram, EventLog, ExerciseLog
  moves/*.gpx/.fit  each recorded move decoded (tools/exercise_log.py), + moves/index.json with
                    the per-move summary incl. training effect / recovery time / energy
  history.json      daily steps (30 days) + daily/weekly activity (kcal) + recovery ring
                    (tools/ambit_steps.py)
  settings.json     every readable device setting incl. activity_level (tools/settings_write.py)
  swim_styles.json  the swim styles the watch has learned (tools/swim_styles.py; --restore takes
                    this file back), on watches that learn them
  manifest.json     timestamp, model/fw/serial, the memory map, and the result of each step

Read-only: region reads (0x0b17), the memory map (0x0b21), object queries (ambit_steps) and
settings reads (settings_write, no --write). Nothing is ever written to the watch - this tool
cannot change or erase anything on it, by construction (it never calls a write command and never
passes --mark-synced / --write to the tools it shells out to).

The watch only keeps a window (ExerciseLog is a ring; steps = 30 days; activity/recovery = 24 h /
7 days), so a backup is a snapshot. Run it regularly - before new moves/days roll the old ones
out - which is what the companion schedule does. Safe to run on a timer: with no watch on the USB
bus it does nothing and exits 2; with --skip-unchanged it makes no new backup when every region is
byte-identical to the most recent one.

Usage:
  tools/backup_all.py                      # fast capture into a dated folder
  tools/backup_all.py --full               # read the whole ExerciseLog ring (deep, ~8 min)
  tools/backup_all.py --skip-unchanged     # no new backup if nothing changed since the last
  tools/backup_all.py --out DIR            # into DIR instead of a dated folder
  tools/backup_all.py --expect-model Emu   # abort unless the watch reports this model
Exit: 0 backup made (or nothing-changed skip), 2 no watch on USB, 1 error.
"""
import argparse
import contextlib
import datetime as _dt
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import exercise_log as EL  # noqa: E402
from device_info import read_device_info  # noqa: E402  (also sends the required session handshake)
from write_nav import Link, read_flash, read_memory_map  # noqa: E402
# Data regions worth saving, read in this order. ExerciseLog is handled specially (it is the big
# ring and gets decoded); the rest are small, fixed blobs dumped verbatim. Any the watch does not
# declare in its 0x0b21 memory map is simply skipped (e.g. the Kailash has no ExerciseLog/Routes).
PLAIN_REGIONS = ["Apps", "CustomModes", "Routes", "Waypoints", "TrainingProgram", "EventLog"]
EXERCISE_LOG_MARGIN = 256 * 1024  # fast mode reads the used extent plus this, to catch a move or
#                                   two just past the index (recently orphaned) without the full ring


def _sha(b):
    return hashlib.sha256(b).hexdigest()


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "move").strip()) or "move"


def _run_json_tool(script, extra_args, log):
    """Run a project tool that prints one JSON object on stdout (its own USB session), and return
    (parsed_or_None, raw_stdout). Robust to a few human log lines printed before the JSON."""
    cmd = [sys.executable, os.path.join(HERE, script), "--json", *extra_args]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if res.returncode != 0:
        log(f"  {script}: exit {res.returncode}; stderr: {res.stderr.strip()[:200]}")
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line), res.stdout
            except json.JSONDecodeError:
                continue
    return None, res.stdout


def read_regions(link, out_dir, full, log, verbose):
    """Dump every declared plain region + the ExerciseLog. Returns (mem_map, el_base, el_size,
    el_data_fullsize, region_shas). el_data_fullsize is padded to the declared region size so the
    decoder's wrap math is correct; region_shas keys every saved region by its raw bytes' sha.
    `verbose` controls read_flash's own per-chunk progress printing (off keeps a timer's log clean)."""
    lab = (lambda s: s) if verbose else (lambda s: "")
    mm = read_memory_map(link)
    regions_dir = os.path.join(out_dir, "regions")
    os.makedirs(regions_dir, exist_ok=True)
    shas = {}

    for name in PLAIN_REGIONS:
        if name not in mm:
            log(f"  {name}: not declared by this watch - skipped")
            continue
        base, size = mm[name]
        if base == 0xFFFFFFFF or size == 0:
            log(f"  {name}: declared empty (base 0x{base:x} size {size}) - skipped")
            continue
        blob = bytes(read_flash(link, base, size, label=lab(name)))
        with open(os.path.join(regions_dir, f"{name}.bin"), "wb") as f:
            f.write(blob)
        shas[name] = _sha(blob)
        nonff = sum(1 for b in blob if b != 0xFF)
        log(f"  {name}: {len(blob)} B (non-0xFF {nonff}) -> regions/{name}.bin")

    # ExerciseLog: the big ring. Read the used extent (+margin) for speed by default; --full reads
    # the whole region (catches deeply-orphaned, un-indexed moves at the cost of ~8 min). Pad to the
    # declared size so walk_entries() wrap math holds, and save the raw bytes we actually read.
    el_base, el_size = mm.get("ExerciseLog", (EL.EXERCISE_LOG_BASE, EL.EXERCISE_LOG_SIZE))
    el_full = None
    if el_base == 0xFFFFFFFF or el_size == 0:
        log("  ExerciseLog: no such region on this watch - no moves to back up")
    else:
        if full:
            raw = bytes(read_flash(link, el_base, el_size, label=lab("ExerciseLog (full)")))
        else:
            probe = bytes(read_flash(link, el_base, 1024, label=lab("ExerciseLog (header)")))
            master = EL.parse_master_header(probe)
            needed = master["next_free_address"] - el_base + EXERCISE_LOG_MARGIN
            needed = max(1024, min(el_size, needed))
            raw = bytes(read_flash(link, el_base, needed, label=lab("ExerciseLog")))
        with open(os.path.join(regions_dir, "ExerciseLog.bin"), "wb") as f:
            f.write(raw)
        shas["ExerciseLog"] = _sha(raw)
        el_full = raw + b"\xff" * (el_size - len(raw)) if len(raw) < el_size else raw
        log(f"  ExerciseLog: {len(raw)} B read ({'full ring' if full else 'used extent+margin'}) "
            f"-> regions/ExerciseLog.bin")
    return mm, el_base, el_size, el_full, shas


def decode_moves(el_data, el_base, el_size, out_dir, full, link, log):
    """Walk the ExerciseLog and write one GPX + FIT per move, plus moves/index.json. Returns the
    list of move summaries. One unparseable/empty move never aborts the rest. If a fast read is
    too short for a wrapped log, falls back to a full re-read (link still open)."""
    if el_data is None:
        return []
    try:
        entries = list(EL.walk_entries(el_data, mem_start=el_base, mem_size=el_size))
    except (IndexError, EL.struct.error, ValueError) as exc:
        if not full and link is not None:
            log(f"  fast read parsed incompletely ({exc}) - re-reading the full ring")
            raw = bytes(read_flash(link, el_base, el_size, label="ExerciseLog (full)"))
            with open(os.path.join(out_dir, "regions", "ExerciseLog.bin"), "wb") as f:
                f.write(raw)
            entries = list(EL.walk_entries(raw, mem_start=el_base, mem_size=el_size))
        else:
            log(f"  could not walk ExerciseLog ({exc}) - saved the raw dump, skipped decode")
            return []

    moves_dir = os.path.join(out_dir, "moves")
    os.makedirs(moves_dir, exist_ok=True)
    summaries = []
    for n, (header, samples) in enumerate(entries, 1):
        date = f"{header['year']:04d}-{header['month']:02d}-{header['day']:02d}"
        stem = f"move{n}_{date}_{header['hour']:02d}{header['minute']:02d}_{_safe(header.get('activity_name'))}"
        try:
            with open(os.path.join(moves_dir, stem + ".gpx"), "w") as f:
                f.write(EL.to_gpx(header, samples))
        except Exception as exc:  # a GPS-less/empty move should not sink the backup
            log(f"  move {n} ({date}): GPX failed ({type(exc).__name__}: {exc})")
        try:
            with open(os.path.join(moves_dir, stem + ".fit"), "wb") as f:
                f.write(EL.to_fit(header, samples))
        except Exception as exc:
            log(f"  move {n} ({date}): FIT skipped ({type(exc).__name__}: {exc})")
        summaries.append({
            "n": n, "date": date, "time": f"{header['hour']:02d}:{header['minute']:02d}",
            "activity": header.get("activity_name"), "activity_type": header.get("activity_type"),
            "duration_s": header.get("duration_ms", 0) // 1000, "distance_m": header.get("distance"),
            "samples": header.get("samples_count"),
            "peak_training_effect": header.get("peak_training_effect"),
            "recovery_time_s": (header.get("recovery_time_ms") or 0) // 1000,
            "energy_kcal": header.get("energy_consumption"),
            "ascent_m": header.get("ascent"), "descent_m": header.get("descent"),
            "cadence_avg": header.get("cadence_avg"), "cadence_max": header.get("cadence_max"),
            "file": stem,
        })
        log(f"  move {n}: {date} {header['hour']:02d}:{header['minute']:02d} "
            f"{header.get('activity_name')!r} {header.get('distance')}m "
            f"{header.get('duration_ms', 0)//1000}s -> moves/{stem}.*")
    with open(os.path.join(moves_dir, "index.json"), "w") as f:
        json.dump({"count": len(summaries), "moves": summaries}, f, indent=2)
    return summaries


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", metavar="DIR", help="save here instead of a dated backups/watch-* folder")
    ap.add_argument("--full", action="store_true",
                    help="read the entire ExerciseLog ring, not just the used extent (deep, ~8 min)")
    ap.add_argument("--skip-unchanged", action="store_true",
                    help="make no new backup if every region is byte-identical to the latest one")
    ap.add_argument("--expect-model", metavar="MODEL",
                    help="abort unless the connected watch reports this model (e.g. Emu)")
    ap.add_argument("--quiet", action="store_true", help="only print the final result line")
    args = ap.parse_args()

    # read_flash() and the link commands print per-chunk progress to stdout with no off switch, so
    # in --quiet mode redirect stdout to the void for the whole run and emit only the result line to
    # the real stdout. Early-exit notices already go to stderr, so they stay visible either way.
    real_out = sys.stdout
    sink = open(os.devnull, "w") if args.quiet else None
    try:
        with (contextlib.redirect_stdout(sink) if sink else contextlib.nullcontext()):
            return _run(args, real_out)
    finally:
        if sink:
            sink.close()


def _run(args, real_out):
    def log(msg):
        if not args.quiet:
            print(msg)

    backups_root = os.path.join(ROOT, "backups")
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.out or os.path.join(backups_root, f"watch-{stamp}")

    # Connect. No watch on the bus is the normal "nothing to do" case for a scheduled run -> exit 2.
    link = Link(dry_run=False, verbose=False)
    try:
        link.open()
    except Exception as exc:
        print(f"no watch on USB ({exc}) - nothing backed up", file=sys.stderr)
        return 2
    try:
        info = read_device_info(link)  # sends CMD_DEVICE_INFO 02 48 03 00 - the session handshake
        model, serial, fw = info["model"], info["serial"], info["fw_version"]
        if args.expect_model and model and model != args.expect_model:
            print(f"watch is {model!r}, expected {args.expect_model!r} - aborting", file=sys.stderr)
            return 1
        log(f"watch: model={model} fw={fw} serial={serial}")

        os.makedirs(out_dir, exist_ok=True)
        log(f"=== regions -> {out_dir} ===")
        mm, el_base, el_size, el_full, shas = read_regions(link, out_dir, args.full, log,
                                                            verbose=not args.quiet)

        log("=== moves ===")
        moves = decode_moves(el_full, el_base, el_size, out_dir, args.full, link, log)
    finally:
        try:
            link.device.close()
        except Exception:
            pass

    # The two tools that use object/settings queries open their own USB session (the link above is
    # now closed), exactly as they do standalone.
    log("=== history (steps / activity / recovery) ===")
    history, history_raw = _run_json_tool("ambit_steps.py", [], log)
    with open(os.path.join(out_dir, "history.json"), "w") as f:
        json.dump(history if history is not None else {"ok": False, "raw": history_raw}, f, indent=2)
    if history:
        days = [d for d in history.get("days", []) if d.get("steps") is not None]
        log(f"  steps: {len(days)} day(s) with a count; "
            f"activity/recovery rings {'present' if history.get('activity') or history.get('recovery') else 'absent'}")

    log("=== settings ===")
    settings, settings_raw = _run_json_tool("settings_write.py", [], log)
    with open(os.path.join(out_dir, "settings.json"), "w") as f:
        f.write(json.dumps(settings, indent=2) if settings is not None else settings_raw)
    if settings:
        s = settings.get("settings", settings)
        log(f"  {len(s)} setting(s) captured")

    log("=== swim styles ===")
    swim, _swim_raw = _run_json_tool("swim_styles.py",
                                     ["--save", os.path.join(out_dir, "swim_styles.json")], log)
    if swim and swim.get("supported"):
        styles = swim.get("styles") or []
        log(f"  {sum(1 for r in styles if any(r.get('vectors') or []))} taught stroke(s) captured")
        # Part of the "anything changed?" check below: re-teaching a stroke changes no region.
        shas["SwimStyles"] = hashlib.sha256(json.dumps(styles, sort_keys=True).encode()).hexdigest()

    manifest = {
        "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
        "model": model, "fw": fw, "serial": serial,
        "memory_map": {k: {"base": v[0], "size": v[1]} for k, v in mm.items()},
        "region_sha256": shas,
        "moves": len(moves),
        "history_ok": bool(history),
        "settings_ok": bool(settings),
        "swim_styles_ok": bool(swim and swim.get("supported")),
        "exercise_log_read": "full ring" if args.full else "used extent + margin",
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    # --skip-unchanged: drop this backup if the data regions are byte-identical to the previous
    # one (watch plugged in but nothing new recorded), so a frequent timer does not pile copies.
    # EventLog is excluded from the comparison: it logs each power/connect event, so it changes
    # every session and would otherwise defeat the check.
    def _dedup_view(d):
        return {k: v for k, v in (d or {}).items() if k != "EventLog"}

    if args.skip_unchanged:
        others = sorted((os.path.join(backups_root, d) for d in os.listdir(backups_root)
                         if d.startswith("watch-")
                         and os.path.abspath(os.path.join(backups_root, d)) != os.path.abspath(out_dir)),
                        key=os.path.getmtime)
        if others:
            prev_mani = os.path.join(others[-1], "manifest.json")
            prev_shas = {}
            if os.path.exists(prev_mani):
                try:
                    prev_shas = json.load(open(prev_mani)).get("region_sha256", {})
                except Exception:
                    prev_shas = {}
            if shas and _dedup_view(prev_shas) == _dedup_view(shas):
                import shutil
                shutil.rmtree(out_dir, ignore_errors=True)
                print(f"unchanged since {os.path.basename(others[-1])} - no new backup", file=sys.stderr)
                return 0

    print(f"backup complete: {out_dir}  ({len(moves)} move(s), "
          f"history {'ok' if history else 'FAILED'}, settings {'ok' if settings else 'FAILED'})",
          file=real_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
