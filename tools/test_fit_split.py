#!/usr/bin/env python3
"""sync/06 "Split here": tools/fit_split.py and its Android twin shared/fit_split.js.

Made-up files only (no real positions in the repo):
  - two rides in one file, the 2026-11-06 Edge 500 shape (535 s, 16 h 17 min stopped, 310 s),
    with heart rate, altitude, a lap and an activity message on top of the session;
  - the same with two laps, the first ending before the stop;
  - a 600 km brevet with a 6.5 h sleep stop is NOT split through the Coach's note, but the cutter
    itself must refuse a stop shorter than 6 h.
Each half must be a valid FIT (CRC), decode with tools/activity_streams.py, hold no long stop,
and carry its own totals. Python and JavaScript must write the same bytes.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import activity_streams as A  # noqa: E402
import fit_split as F  # noqa: E402

ROOT = os.path.dirname(HERE)
T0 = 1_100_000_000
GAP = 58612


def two_in_one(two_laps=False):
    msgs = [(0, 0, [(1, 0x84, 1), (2, 0x84, 1036)])]
    dist = 0
    pts = [(s, s < 300) for s in range(0, 536)] + [(535 + GAP + s, True) for s in range(0, 311)]
    for t, moving in pts:
        if t and moving:
            dist += 90
        msgs.append((1, 20, [(253, 0x86, T0 + t), (5, 0x86, dist), (3, 0x02, 120 + t % 30),
                             (2, 0x84, int((100 + (t % 40)) * 5 + 2500)), (6, 0x84, 900)]))
        if two_laps and t == 300:
            msgs.append((2, 19, [(253, 0x86, T0 + 300), (2, 0x86, T0), (7, 0x86, 300000),
                                 (8, 0x86, 300000), (9, 0x86, dist), (11, 0x84, 9), (24, 0x00, 0)]))
    end = 535 + GAP + 310
    lap_start = 300 if two_laps else 0
    msgs.append((2, 19, [(253, 0x86, T0 + end), (2, 0x86, T0 + lap_start), (7, 0x86, (end - lap_start) * 1000),
                         (8, 0x86, 845000 - lap_start * 1000), (9, 0x86, dist), (11, 0x84, 31), (24, 0x00, 0)]))
    msgs.append((3, 18, [(253, 0x86, T0 + end), (2, 0x86, T0), (5, 0x00, 2), (6, 0x00, 0),
                         (7, 0x86, end * 1000), (8, 0x86, 845000), (9, 0x86, dist), (11, 0x84, 31),
                         (16, 0x02, 133), (17, 0x02, 154), (22, 0x84, 76)]))
    msgs.append((4, 34, [(253, 0x86, T0 + end), (0, 0x86, 845000), (1, 0x84, 1), (2, 0x00, 0)]))
    return A._fit_file(msgs)


def js_split(data, at_s):
    script = (
        "const F=require(process.argv[1]+'/shared/fit_split.js');"
        "const b=new Uint8Array(require('fs').readFileSync(0));"
        "let r; try { r=F.splitFit(b, process.argv[2]==='null'?null:Number(process.argv[2]))"
        ".map(p=>Buffer.from(p).toString('base64')); } catch(e) { r={error:e.message}; }"
        "process.stdout.write(JSON.stringify(r));")
    out = subprocess.run(["node", "-e", script, ROOT, json.dumps(at_s)], input=data,
                         capture_output=True, check=True)
    return json.loads(out.stdout)


def main():
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        print(("PASS " if good else "FAIL ") + name, got, "" if good else f"(want {want})")
        ok &= good

    node = shutil.which("node")
    for label, two_laps in (("one lap", False), ("two laps", True)):
        f = two_in_one(two_laps)
        check(f"{label}: one long stop found", F.long_stops(f), [(535, GAP)])
        p1, p2 = F.split_fit(f, 535)
        for n, p in ((1, p1), (2, p2)):
            check(f"{label}: half {n} CRC", F.fit_crc(p), 0)
            if p[0] >= 14:
                check(f"{label}: half {n} header CRC", F.fit_crc(p[:14]), 0)
        s1 = A.streams_from_fit(p1, 200)
        s2 = A.streams_from_fit(p2, 200)
        check(f"{label}: half 1 total / moving / distance",
              (s1["summary"]["elapsed_s"], s1["summary"]["timer_s"], s1["summary"]["dist_m"]), (535.0, 535.0, 269.1))   # records every second, so all of it counts
        check(f"{label}: half 2 total / moving / distance",
              (s2["summary"]["elapsed_s"], s2["summary"]["timer_s"], s2["summary"]["dist_m"]), (310.0, 310.0, 279.0))
        check(f"{label}: half 2 starts the next morning", s2["summary"].get("elapsed_s") and
              A.streams_from_fit(p2, 200)["streams"]["t"][0], 0)
        check(f"{label}: no long stop left", (s1["summary"]["long_gaps"], s2["summary"]["long_gaps"]), ([], []))
        check(f"{label}: half 2 distances start from 0", s2["streams"]["dist"][0], 0.0)
        check(f"{label}: records kept", (s1["records"], s2["records"]), (536, 311))
        check(f"{label}: calories shared by moving time",
              (s1["summary"]["kcal"], s2["summary"]["kcal"]), (round(31 * 535 / 845), round(31 * 310 / 845)))
        check(f"{label}: heart rate worked out again (half 2 max)", s2["summary"]["max_hr"], 149)
        if node:
            js = js_split(f, 535)
            check(f"{label}: Android twin writes the same bytes",
                  [__import__("base64").b64decode(x) for x in js] == [p1, p2], True)

    f = two_in_one()
    try:
        F.split_fit(f, 1234)
        check("wrong place: refused", "split", "refused")
    except ValueError:
        check("wrong place: refused", "refused", "refused")
    import test_long_gaps as G
    b = G.brevet()
    try:
        F.split_fit(b)                          # its longest stop is 6.5 h: that one is allowed...
        check("brevet: cutter finds the 6.5 h stop (the note never offers it)", True, True)
    except ValueError:
        check("brevet: cutter finds the 6.5 h stop (the note never offers it)", False, True)
    short = G.ride_fit([(0, 600), (600 + 5 * 3600, 600)], 1, 5.0, 600 + 5 * 3600 + 600)
    try:
        F.split_fit(short)
        check("a 5 h stop is never cut", "cut", "refused")
    except ValueError:
        check("a 5 h stop is never cut", "refused", "refused")
    if node:
        check("Android twin refuses it too", "error" in js_split(short, None), True)
    else:
        print("SKIP node not found: Android twin not run")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
