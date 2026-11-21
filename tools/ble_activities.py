#!/usr/bin/env python3
"""Recorded activities (ExerciseLog) over BLE, phone-driven, post-handshake.

Own file, this project's own "one file per format" convention. Unlike `ble_logs.py`
(this project's earlier, abandoned attempt at the real Suunto app's own phone-pull 0x1200
sequence - no known stop condition, never finished), `exercise_log.py`'s USB implementation
takes a much simpler route that turns out to need no BLE-specific work at all: it reads the
whole ExerciseLog region via plain 0x0b17 flash reads (`read_flash()`, the exact mechanism
already proven reliable over BLE for routes tonight - 146KB read clean, self-validating via
the region's own CRC) and decodes it locally. This file only replicates `exercise_log.py
main()`'s own watch-reading sequence (probe the real used size, read only that, walk the
entries) against a `link` instead of duplicating it inline - the actual sample/GPX/FIT
decode is 100% reused, unchanged.
"""

import struct
import sys

from exercise_log import (
    EXERCISE_LOG_BASE, EXERCISE_LOG_SIZE, WRAP_START_OFFSET, parse_master_header,
    read_entry_at, to_fit, to_gpx, walk_entries,
)
from write_nav import read_flash

CMD_LOCK_CHECK = 0x0b19
CMD_LOCK_SET = 0x0b1a


def sync_lock(link, on):
    """Takes (on) or releases (off) the watch's sync lock around a Bluetooth sync, as the
    Suunto app does (btsnoop 2026-08-09: 0x0b19/0x0b1a 1 at the start, 0 at the end) and the
    Android app does over Bluetooth (HomeScreen, syncDisplay). The watch shows "Syncing" while
    it is held; without it a "Sync now" pressed on the watch ended in "Sync failed" although
    the sync ran. Same steps as libambit_device_driver_lock_log(): set only a free lock, clear
    only a held one, nothing when the check fails. Best-effort: False instead of raising."""
    try:
        reply = link.command(CMD_LOCK_CHECK, b"")
        held = int.from_bytes(reply[:4], "little") if len(reply) >= 4 else None
        if on and held == 0:
            link.command(CMD_LOCK_SET, b"\x01\x00\x00\x00")
        elif not on and held == 1:
            link.command(CMD_LOCK_SET, b"\x00\x00\x00\x00")
        return held is not None
    except Exception:                                    # noqa: BLE001 - the sync goes on without it
        return False


WRAP_MARGIN = 0x10000          # as walk_entries() / openambit's pmem20.c


PIECE = 8192                   # read in pieces so progress can be told between them


def _spans(mem_start, mem_size, start, end):
    """The one or two plain address ranges from `start` to `end`, round the circular region's
    end to WRAP_START_OFFSET when end <= start."""
    stop = mem_start + mem_size
    if end > start:
        return [(start, end)]
    return [(start, stop), (mem_start + WRAP_START_OFFSET, end)]


def _read_logical(link, mem_start, mem_size, start, end, buf, tick=None):
    """Reads the log from absolute address `start` up to `end` into `buf` (indexed from
    mem_start); tick(n) after every piece of n bytes."""
    for a, b in _spans(mem_start, mem_size, start, end):
        while a < b:
            n = min(PIECE, b - a)
            buf[a - mem_start:a - mem_start + n] = read_flash(link, a, n, label="ExerciseLog")
            a += n
            if tick:
                tick(n)


def read_new_activities(link, known_count, mem_start=EXERCISE_LOG_BASE,
                        mem_size=EXERCISE_LOG_SIZE, probe=None, progress=None):
    """Only the moves after the first `known_count`, read entry by entry (Bluetooth reads
    about 1 KB a second: the whole log was ~430 KB on André's Peak, 2026-11-21, so every sync
    took minutes before a new move showed). The newest entry is the master header's
    last_entry; each entry's header holds its previous one, so the new ones are found walking
    back, and each is read from its own address to the next one's (next_free_address for the
    newest). Returns (master, entries) like read_activities(), or None when the chain does not
    add up (the caller then reads the whole log). progress(done_bytes, total_bytes, moves)
    is called as the new moves come in."""
    if probe is None:
        probe = read_flash(link, mem_start, 1024, label="ExerciseLog (header)")
    master = parse_master_header(probe)
    new = master["entries"] - known_count
    if new <= 0:
        return master, []
    if known_count <= 0:
        return None
    stop = mem_start + mem_size
    addrs, addr = [], master["last_entry"]
    for _ in range(new):
        if not mem_start <= addr < stop:
            return None
        head = read_flash(link, addr, min(12, stop - addr), label="ExerciseLog (entry)")
        if len(head) < 12 or head[:4] != b"PMEM":
            return None
        addrs.append((addr, int.from_bytes(head[4:8], "little")))
        addr = int.from_bytes(head[8:12], "little")
    addrs.reverse()
    # Each entry's next must be the following one, and the newest's next_free_address.
    for (a, nxt), following in zip(addrs, [x for x, _ in addrs[1:]] + [None]):
        if following is not None and nxt != following:
            return None
    buf = bytearray(b"\xff" * (mem_size + WRAP_MARGIN))
    ends = [x for x, _ in addrs[1:]] + [master["next_free_address"]]
    total = sum(b - a for (s, _), e in zip(addrs, ends) for a, b in _spans(mem_start, mem_size, s, e))
    done = [0]

    def tick(n):
        done[0] += n
        if progress:
            progress(done[0], total, len(addrs))
    if progress:
        progress(0, total, len(addrs))
    wrapped = False
    for (a, _), e in zip(addrs, ends):
        _read_logical(link, mem_start, mem_size, a, e, buf, tick)
        wrapped |= e <= a
    if wrapped:
        margin = min(WRAP_MARGIN, mem_size - WRAP_START_OFFSET)
        buf[mem_size:mem_size + margin] = buf[WRAP_START_OFFSET:WRAP_START_OFFSET + margin]
    data = bytes(buf)
    entries = []
    for a, _ in addrs:
        header, samples, _n, _p = read_entry_at(data, mem_size, a, mem_start)
        entries.append((header, samples))
    return master, entries


def read_activities(link, known_count=0, progress=None):
    """Returns (master, entries) - same shape `exercise_log.py main()`'s own watch-reading
    branch produces. `entries` is a list of (header, samples) tuples; pass each to
    `to_gpx()`/`to_fit()` for export, same as the USB CLI does."""
    probe = read_flash(link, EXERCISE_LOG_BASE, 1024, label="ExerciseLog (header)")
    try:
        quick = read_new_activities(link, known_count, probe=probe, progress=progress)
    except (ValueError, IndexError, struct.error, RuntimeError):
        quick = None
    if quick is not None:
        return quick
    probe_master = parse_master_header(probe)
    needed = probe_master["next_free_address"] - EXERCISE_LOG_BASE + 8192
    needed = max(1024, min(EXERCISE_LOG_SIZE, needed))
    data = read_flash(link, EXERCISE_LOG_BASE, needed, label="ExerciseLog")
    try:
        entries = list(walk_entries(data, skip_count=known_count))
    except (IndexError, struct.error):
        data = read_flash(link, EXERCISE_LOG_BASE, EXERCISE_LOG_SIZE, label="ExerciseLog")
        entries = list(walk_entries(data, skip_count=known_count))
    master = parse_master_header(data)
    return master, entries


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--known-count", type=int, default=0, metavar="N")
    args = ap.parse_args()

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent
                           / "desktop" / "backend"))
    import ble_bridge                                        # noqa: PLC0415

    bridge = ble_bridge.BleBridge()
    status = bridge.status()
    if not status.get("handshake_done"):
        print("no BLE connection with a completed handshake - connect first")
        return 1
    bridge.set_dry_run(False)
    master, entries = read_activities(bridge, args.known_count)
    print(f"master: entries={master['entries']}")
    for header, samples in entries:
        print(f"  {header['activity_name']!r} {header['year']:04d}-{header['month']:02d}-"
              f"{header['day']:02d}, {len(samples)} sample(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
