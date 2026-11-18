#!/usr/bin/env python3
"""sync/06: long stops inside one FIT, and when the move screen says "two rides in one file?".

Two made-up files, same shapes as the real ones (no real positions are kept in the repo):
  - the Edge 500 file of 2026-11-06: 535 s of riding, 16 h 17 min with no records, 310 s the
    next morning, 0.76 km in all -> one long stop, and the note shows;
  - a 600 km brevet (André, 2026-11-18: BRMs on an Edge 1040): 5 s smart recording, café stops of
    20 min, a 3 h and a 6.5 h sleep stop with hundreds of km on both sides -> the 6.5 h stop is
    listed, the note never shows, and moving time is far below total time.
Python (tools/activity_streams.py) and the Android twin (shared/activity_streams.js) must agree;
the note rule is shared/activity_view_logic.js twoRidesHint, run here through node.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import activity_streams as A  # noqa: E402

ROOT = os.path.dirname(HERE)
T0 = 1_100_000_000


def ride_fit(segments, step_s, speed_ms, total_s):
    """segments: [(start offset s, riding seconds)] -> FIT bytes with records every step_s."""
    msgs = [(0, 0, [(1, 0x84, 1), (2, 0x84, 1030)])]
    dist_cm = 0
    timer = 0
    for start, length in segments:
        for s in range(0, length + 1, step_s):
            if s:
                dist_cm += int(speed_ms * step_s * 100)
                timer += step_s
            msgs.append((1, 20, [(253, 0x86, T0 + start + s), (5, 0x86, dist_cm), (3, 0x02, 130),
                                 (6, 0x84, int(speed_ms * 1000))]))
    msgs.append((3, 18, [(253, 0x86, T0 + total_s), (2, 0x86, T0), (5, 0x00, 2), (6, 0x00, 0),
                         (7, 0x86, total_s * 1000), (8, 0x86, timer * 1000), (9, 0x86, dist_cm)]))
    return A._fit_file(msgs)


def two_in_one():
    # 15:50:13 -> 15:59:08, nothing until 08:16:00 the next day, -> 08:21:10
    return ride_fit([(0, 535), (535 + 58612, 310)], 1, 0.9, 535 + 58612 + 310)


def brevet():
    km_h = 25.0
    ms = km_h / 3.6
    seg_s = int(50_000 / ms)                       # 50 km between stops
    plan = []                                      # stop after each 50 km leg
    for leg in range(12):
        plan.append(seg_s)
    stops = {5: 3 * 3600, 8: int(6.5 * 3600)}       # sleep after 300 km and after 450 km
    segments, t = [], 0
    for i, length in enumerate(plan):
        segments.append((t, length))
        t += length + stops.get(i, 20 * 60)          # café stop otherwise
    total = segments[-1][0] + segments[-1][1]
    return ride_fit(segments, 5, ms, total)


def js_side(fit_bytes):
    if not shutil.which("node"):
        return None
    script = (
        "const st=require(process.argv[1]+'/shared/activity_streams.js');"
        "const L=require(process.argv[1]+'/shared/activity_view_logic.js');"
        "const b=new Uint8Array(require('fs').readFileSync(0));"
        "const o=st.streamsFromFit(b,500);"
        "process.stdout.write(JSON.stringify({gaps:o.summary.long_gaps,hint:L.twoRidesHint(o)}));"
    )
    out = subprocess.run(["node", "-e", script, ROOT], input=fit_bytes, capture_output=True, check=True)
    return json.loads(out.stdout)


def main():
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        print(("PASS " if good else "FAIL ") + name, got, "" if good else f"(want {want})")
        ok &= good

    f = two_in_one()
    py = A.streams_from_fit(f, 500)
    gaps = py["summary"]["long_gaps"]
    check("two in one: one long stop", len(gaps), 1)
    check("two in one: stop length", gaps[0]["len_s"], 58612)
    check("two in one: riding before / after", (gaps[0]["before_s"], gaps[0]["after_s"]), (535, 310))
    check("two in one: timer far below total", (py["summary"]["timer_s"], py["summary"]["elapsed_s"]),
          (845.0, 59457.0))
    js = js_side(f)
    if js is not None:
        check("two in one: Android decoder agrees", js["gaps"], gaps)
        check("two in one: note shows", js["hint"] is not None and js["hint"]["len_s"], 58612)

    f = brevet()
    py = A.streams_from_fit(f, 500)
    gaps = py["summary"]["long_gaps"]
    check("brevet: only the 6.5 h sleep stop is a long stop", [g["len_s"] for g in gaps], [int(6.5 * 3600)])
    check("brevet: hundreds of km on both sides",
          (round(gaps[0]["before_m"] / 1000), round(gaps[0]["after_m"] / 1000)), (450, 150))
    s = py["summary"]
    check("brevet: 600 km", round(s["dist_m"] / 1000), 600)
    check("brevet: moving time 24 h, total time over 36 h",
          (round(s["timer_s"] / 3600), s["elapsed_s"] > 36 * 3600), (24, True))
    js = js_side(f)
    if js is not None:
        check("brevet: Android decoder agrees", js["gaps"], gaps)
        check("brevet: note never shows", js["hint"], None)
    else:
        print("SKIP node not found: Android twin and note rule not run")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
