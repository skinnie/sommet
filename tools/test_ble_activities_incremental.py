#!/usr/bin/env python3
"""Bluetooth reads only the new moves (tools/ble_activities.read_new_activities), and gets the
same moves the whole-log read does. Uses local ExerciseLog dumps read over USB (git-ignored
backups: the Traverse one has wrapped round its circular region); skipped where absent.
Checked for a spread of known counts: same headers, same samples, and how much was read."""
import contextlib
import io
import pathlib
import struct
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ble_activities as B           # noqa: E402
import exercise_log as EL            # noqa: E402

DUMPS = [  # (path, base, size)
    (HERE.parent / "backups" / "traverse-fw1.0.4-pristine" / "ExerciseLog.bin", 0x2B7CD0, 5276464),
    (HERE.parent / "backups" / "watch-20261118-100002" / "regions" / "ExerciseLog.bin",
     EL.EXERCISE_LOG_BASE, EL.EXERCISE_LOG_SIZE),
]


class FakeLink:
    """Answers 0x0b17 from a dump the way the watch does: [address][length] then the bytes."""
    def __init__(self, data, base):
        self.data, self.base, self.read = data, base, 0

    def command(self, cmd, payload, quiet=False):
        assert cmd == 0x0B17
        a, n = struct.unpack("<II", payload)
        self.read += n
        return payload + self.data[a - self.base:a - self.base + n]


def check(path, base, size):
    data = path.read_bytes()
    if len(data) < size:                       # a backup that kept only the used part
        data = data + b"\xff" * (size - len(data))
    full = list(EL.walk_entries(data, mem_start=base, mem_size=size))
    total = len(full)
    # A spread of known counts, not all of them (each decodes every new move: all 37 of the
    # Traverse's took minutes, too slow for check.sh).
    for known in sorted({1, total - 3, total - 1, total} - {0}) if total < 10 else (total - 5, total - 1, total):
        link = FakeLink(data, base)
        master, got = B.read_new_activities(link, known, mem_start=base, mem_size=size)
        want = full[known:]
        assert len(got) == len(want), (path.name, known, len(got), len(want))
        for (h1, s1), (h2, s2) in zip(got, want):
            assert h1 == h2 and s1 == s2, (path.parent.name, known)
    link, seen = FakeLink(data, base), []
    B.read_new_activities(link, total - 1, mem_start=base, mem_size=size,
                          progress=lambda d, t, m: seen.append((d, t, m)))
    assert seen[0][0] == 0 and seen[-1][0] == seen[-1][1] > 0 and seen[-1][2] == 1, seen[-1]
    return total, link.read


if __name__ == "__main__":
    ran = 0
    for path, base, size in DUMPS:
        if not path.exists():
            continue
        with contextlib.redirect_stdout(io.StringIO()):     # read_flash's progress lines
            total, last_read = check(path, base, size)
        print(f"  {path.parent.name}: {total} moves, each known count tried matches the whole-log "
              f"read; the newest move alone read {last_read // 1024} KB")
        ran += 1
    print("ble activities incremental: " + ("OK" if ran else "dumps absent, skipped"))
