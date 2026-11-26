#!/usr/bin/env python3
"""Android's (and iOS's) move download with many moves on the watch (GitHub #26, 2026-11-26).

Builds the app's own Ambit3 driver with gcc on this machine (android/android/app/src/main/cpp/
host_tests/move_read_harness.c includes libambit/device_driver_ambit3.c) and runs its log read
against a fake watch: the move list (0x1200) in pages, flash pieces (0x0b17) from a saved
ExerciseLog dump, damaged replies injected where asked. Checks:

- the move list arrives whole over many pages; a page lost once or three times is asked again;
  a page that never comes ends the read with an error, not a crash (it was a double free);
- a move the phone already has is not downloaded again, even when the watch calls it not synced
  (every move on a watch only Sommet syncs: it was downloaded again at every sync);
- with the dumps (git-ignored backups, skipped where absent): every move is read and the newest
  ones match the desktop's own reader (tools/exercise_log.py) field for field; with known moves
  only the new ones are read, and only their bytes; a piece lost one to three times is asked
  again; a piece that never comes loses that move only, the others still arrive;
- with SuuntoLink's capture of a Peak holding 71 moves (assets, skipped where absent): its twelve
  real pages are read whole, also with a page lost twice.

Where the dumps and captures are: SOMMET_BACKUPS_DIR / SOMMET_ASSETS_DIR, else ../backups and
../assets next to tools/. Needs gcc (skipped without it).

    python3 tools/test_android_move_read.py
"""
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import exercise_log as EL            # noqa: E402

CPP = HERE.parent / "android" / "android" / "app" / "src" / "main" / "cpp"
SHARED = HERE.parent / "shared" / "libambit"
BACKUPS = pathlib.Path(os.environ.get("SOMMET_BACKUPS_DIR") or HERE.parent / "backups")
ASSETS = pathlib.Path(os.environ.get("SOMMET_ASSETS_DIR") or HERE.parent / "assets")
LEFT_OUT = {"protocol.c", "protocol_ble.c", "libambit_android.c", "libambit_ios.c",
            "firmware_flash_android.c", "hid_stub.c", "device_driver_ambit3.c"}

# (dump, base, size, product id, firmware): the Traverse's has wrapped round its region.
DUMPS = [
    ("traverse-fw1.0.4-pristine/ExerciseLog.bin", 0x2B7CD0, 5276464, 0x2B, "1.0.4"),
    ("watch-20261118-100002/regions/ExerciseLog.bin", EL.EXERCISE_LOG_BASE, EL.EXERCISE_LOG_SIZE,
     0x1B, "2.4.17"),
]


def build(out):
    """The harness and the app's libambit, compiled file by file in parallel (one gcc for all
    took 11 s on the X230), with AddressSanitizer where the compiler has it."""
    if not shutil.which("gcc"):
        return None
    from concurrent.futures import ThreadPoolExecutor             # noqa: PLC0415
    srcs = sorted(p for p in (CPP / "libambit").glob("*.c") if p.name not in LEFT_OUT)
    srcs += [SHARED / "device_driver_ambit3_navigation.c", SHARED / "device_driver_ambit3_sport_modes.c",
             CPP / "host_tests" / "move_read_harness.c"]
    # What gcc 14 (the build robot's) refuses must fail here too: gcc 13 only warned about a
    # wrongly typed callback (2026-11-26). No -w: it would silence these -Werror= too; the
    # compiler's output is captured and shown only when a file fails.
    base = ["gcc", "-std=gnu11", "-g", "-O0", "-Werror=incompatible-pointer-types",
            "-Werror=implicit-function-declaration", "-Werror=int-conversion", "-Werror=implicit-int",
            "-include", "endian.h",
            "-I", str(CPP / "libambit"), "-I", str(CPP / "libambit" / "hidapi"), "-I", str(SHARED)]
    for extra in (["-fsanitize=address", "-fno-omit-frame-pointer"], []):
        objs = [out.parent / f"{i}-{p.stem}.o" for i, p in enumerate(srcs)]
        with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
            done = list(pool.map(lambda so: subprocess.run(base + extra + ["-c", str(so[0]), "-o", str(so[1])],
                                                           capture_output=True, text=True), zip(srcs, objs)))
        failed = [d for d in done if d.returncode != 0]
        if not failed:
            link = subprocess.run(["gcc", *extra, *map(str, objs), "-lm", "-o", str(out)],
                                  capture_output=True, text=True)
            if link.returncode == 0:
                return out
            failed = [link]
        if not extra:
            raise RuntimeError("the harness did not compile:\n" + failed[0].stderr[-2000:])
    return None


def run(harness, *args):
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0:abort_on_error=0")
    p = subprocess.run([str(harness), *map(str, args)], capture_output=True, text=True,
                       timeout=300, env=env)
    assert "ERROR: AddressSanitizer" not in p.stderr, p.stderr[-3000:]
    assert p.returncode == 0, (p.returncode, p.stderr[-2000:])
    pushed, result = [], None
    for line in p.stdout.splitlines():
        f = line.split()
        if f[:2] == ["R", "pushed"]:
            pushed.append({"when": tuple(int(x) for x in f[2:8]), "duration_ms": int(f[9]),
                           "distance": int(f[11]), "samples": int(f[13]), "timesum": int(f[15])})
        elif f[:2] == ["R", "result"]:
            result = {f[i]: int(f[i + 1]) for i in range(3, len(f) - 1, 2)}
            result["ret"] = int(f[2])
    assert result is not None, p.stdout[-2000:]
    return pushed, result


def write_listing(path, moves, known=0, synced=0):
    """`known`: how many of the oldest moves the phone already has."""
    with open(path, "w") as f:
        for i, m in enumerate(moves):
            f.write(f"{m['a1']:x} {m['e1']:x} {m['a2']:x} {m['e2']:x} {synced} "
                    f"{1 if i < known else 0} {m['when']}\n")


# ---- made-up moves: the move list alone (runs everywhere, no dump needed) -----------------
def check_pages(harness, tmp):
    listing = tmp / "made-up.txt"
    moves = [{"a1": 0x100100 + i * 0x1000, "e1": 0x100900 + i * 0x1000, "a2": 0, "e2": 0,
              "when": f"2026-01-{i + 1:02d}T10:00:00"} for i in range(20)]
    write_listing(listing, moves, known=20, synced=0)
    args = ["--listing", listing, "--per-page", 6]
    _p, r = run(harness, *args)
    # 20 moves in 4 pages; all known, though "not synced": none read (one piece: the log's head)
    assert (r["walked"], r["page_requests"], r["piece_reads"]) == (20, 4, 1), r
    for times, requests in ((1, 5), (3, 7)):
        _p, r = run(harness, *args, "--fail-page", f"2:{times}")
        assert (r["walked"], r["page_requests"]) == (20, requests), (times, r)
    _p, r = run(harness, *args, "--fail-page", "2:99")
    assert r["ret"] == -1 and r["walked"] == 0 and r["page_requests"] == 2 + 4, r
    _p, r = run(harness, *args, "--fail-page", "0:99")
    assert r["ret"] == -1 and r["page_requests"] == 4, r
    return "move list: 20 made-up moves in 4 pages, lost pages asked again, a missing one ends in an error"


# ---- the dumps --------------------------------------------------------------------------
def chain(data, base, size):
    """Every move of a dump as the watch's 0x1200 list gives it: its start, its end (the next
    move's start, or the region's next free address for the newest), and, for a move that runs
    past the region's end, the second part from base+0x12 (SuuntoLink capture
    lars_Ambit3Peak_2017-03: a1=..., e1=the region's end, a2=base+0x12)."""
    master = EL.parse_master_header(data)
    out, current, nxt = [], base, master["first_entry"]
    while current != nxt:
        off = nxt - base
        assert data[off:off + 4] == b"PMEM", hex(nxt)
        following = struct.unpack_from("<I", data, off + 4)[0]
        spec_len = struct.unpack_from("<H", data, off + 12)[0]
        hoff = off + 14 + spec_len
        hlen = struct.unpack_from("<H", data, hoff)[0]
        h = EL.parse_log_header(data, hoff + 2, hlen)
        end = master["next_free_address"] if following == nxt else following
        if end > nxt:
            a1, e1, a2, e2 = nxt, end, 0, 0
        else:
            a1, e1, a2, e2 = nxt, base + size, base + EL.WRAP_START_OFFSET, end
        out.append({"a1": a1, "e1": e1, "a2": a2, "e2": e2, "header": h,
                    "when": f"{h['year']:04d}-{h['month']:02d}-{h['day']:02d}T"
                            f"{h['hour']:02d}:{h['minute']:02d}:{h['msec'] // 1000:02d}"})
        current, nxt = nxt, following
    return out


def desktop(data, base, size, skip):
    want = []
    for h, samples in EL.walk_entries(data, mem_start=base, mem_size=size, skip_count=skip):
        want.append({"when": (h["year"], h["month"], h["day"], h["hour"], h["minute"], h["msec"]),
                     "duration_ms": h["duration_ms"], "distance": h["distance"],
                     "samples": len(samples), "timesum": sum(s.get("time", 0) for s in samples) & 0xFFFFFFFF})
    return want


def part_length(m):
    return (m["e1"] - m["a1"]) + (m["e2"] - m["a2"])


def check_dump(harness, tmp, rel, base, size, product, fw):
    path = BACKUPS / rel
    data = path.read_bytes()
    if len(data) < size:                                # a backup that kept only the used part
        data += b"\xff" * (size - len(data))
    dump = tmp / "dump.bin"
    dump.write_bytes(data)
    moves = chain(data, base, size)
    total = len(moves)
    assert total > 0
    listing = tmp / "listing.txt"
    common = ["--dump", dump, "--base", hex(base), "--size", hex(size), "--product", hex(product),
              "--fw", fw, "--listing", listing]

    # Nothing known yet: every move comes, in order, each one whole.
    write_listing(listing, moves, known=0)
    pushed, r = run(harness, *common)
    assert len(pushed) == total and r["walked"] == total, (rel, len(pushed), total, r)
    for got, m in zip(pushed, moves):
        assert got["samples"] == m["header"]["samples_count"] > 0, (rel, m["when"], got)

    # Known moves are not read again, "not synced" or not; the new ones match the desktop.
    new = min(3, total)
    write_listing(listing, moves, known=total - new)
    pushed, r = run(harness, *common)
    want = desktop(data, base, size, total - new)
    # Same moves, same fields. The sum of sample times is left out of this comparison: the two
    # decoders' time correction differs by a few ms on some samples (28 ms over 3,760 samples on
    # the Traverse dump), which is about decoding, not what was downloaded.
    key = ("when", "duration_ms", "distance", "samples")
    assert [{k: g[k] for k in key} for g in pushed] == [{k: w[k] for k in key} for w in want], \
        (rel, pushed, want)
    want = pushed                         # every field, sample times too, for the runs below
    budget = sum(part_length(m) for m in moves[total - new:]) + 1024 * (2 * new + 1)
    assert r["piece_bytes"] <= budget, (rel, r, budget)

    # A piece lost one to three times is asked again; one that never comes loses that move only.
    victim = moves[total - 2]
    piece = victim["a1"] + 2 * 1024 if victim["e1"] - victim["a1"] > 3 * 1024 else victim["a1"]
    for times in (1, 2, 3):
        got, r = run(harness, *common, "--fail-piece", f"{piece:x}:{times}")
        assert got == want, (rel, times)    # the asked-again pieces gave the same bytes
    got, r = run(harness, *common, "--fail-piece", f"{piece:x}:99")
    vh = victim["header"]
    lost = (vh["year"], vh["month"], vh["day"], vh["hour"], vh["minute"], vh["msec"])
    assert got == [w for w in want if w["when"] != lost], (rel, got)
    assert r["walked"] == total, r

    # The same list in pages of two, one page lost twice: still whole.
    got, r = run(harness, *common, "--per-page", 2, "--fail-page", "1:2")
    assert got == want and r["walked"] == total, (rel, r)
    wrapped = sum(1 for m in moves if m["a2"])
    return (f"{path.parent.name if path.parent.name != 'regions' else path.parent.parent.name}: "
            f"{total} moves ({wrapped} past the region's end) read whole; the newest {new} match "
            f"the desktop's reader; lost pieces and pages asked again")


# ---- SuuntoLink's capture of a Peak with 71 moves: its real pages --------------------------
def check_capture(harness, tmp):
    import ambit_pcap                                  # noqa: PLC0415
    cap = ASSETS / "pcap" / "lars_Ambit3Peak_2017-03.pcap"
    msgs = [m for m in ambit_pcap.messages(str(cap)) if m.command == 0x1200]
    pages, pending = [], None
    for m in msgs:
        if not m.incoming:
            pending = m
        elif pending is not None:
            pages.append((pending.payload[:4], m.payload))
            pending = None
    f = tmp / "pages.txt"
    f.write_text("".join(f"{c.hex()} {p.hex()}\n" for c, p in pages))
    common = ["--pages", f, "--all-known", "--product", "0x1b", "--fw", "2.4.17"]
    _p, r = run(harness, *common)
    assert r["walked"] == 71 and r["page_requests"] == len(pages) == 12, r
    _p, r = run(harness, *common, "--fail-page", "5:2")
    assert r["walked"] == 71 and r["page_requests"] == 14, r
    _p, r = run(harness, *common, "--fail-page", "5:99")
    assert r["ret"] == -1, r
    return "SuuntoLink capture: 71 moves in its 12 real pages read whole, also with a page lost twice"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="android-move-read-") as d:
        tmp = pathlib.Path(d)
        harness = build(tmp / "harness")
        if harness is None:
            print("android move read: gcc absent, skipped")
            sys.exit(0)
        print("  " + check_pages(harness, tmp))
        for rel, base, size, product, fw in DUMPS:
            if (BACKUPS / rel).exists():
                print("  " + check_dump(harness, tmp, rel, base, size, product, fw))
            else:
                print(f"  {rel}: dump absent, skipped")
        cap = ASSETS / "pcap" / "lars_Ambit3Peak_2017-03.pcap"
        if cap.exists():
            print("  " + check_capture(harness, tmp))
        else:
            print("  SuuntoLink capture absent, skipped")
    print("android move read: OK")
