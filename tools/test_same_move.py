"""The same-move rule gives the same answer in Python and in the desktop's C++ (samemove.h), on the
shared cases the phone's jest test also runs (android/src/services/__tests__/sameMove.cases.json).
Both directions (a,b) and (b,a) must agree. The C++ part is skipped when no compiler is found."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from same_move import same_move  # noqa: E402

CASES = json.load(open(os.path.join(ROOT, "android/src/services/__tests__/sameMove.cases.json")))["cases"]

bad = 0
for c in CASES:
    for a, b in ((c["a"], c["b"]), (c["b"], c["a"])):
        if same_move(a, b) != c["same"]:
            print("python FAIL:", c["name"])
            bad += 1

cxx = shutil.which("g++") or shutil.which("clang++")
if cxx:
    lines = ['#include "samemove.h"', "#include <cstdio>", "int main(){int bad=0;"]
    for i, c in enumerate(CASES):
        def row(r):
            return "SameMoveRow{%dLL,%d,%r,%s,\"%s\"}" % (
                r["start"], r["durationS"], float(r["distanceM"]), "true" if r["watch"] else "false", r["device"])
        for a, b in ((c["a"], c["b"]), (c["b"], c["a"])):
            lines.append('if(sameMove(%s,%s)!=%s){std::printf("c++ FAIL: case %d\\n");bad++;}' % (
                row(a), row(b), "true" if c["same"] else "false", i))
    lines.append("return bad;}")
    with tempfile.TemporaryDirectory() as d:
        src, exe = os.path.join(d, "t.cpp"), os.path.join(d, "t")
        open(src, "w").write("\n".join(lines))
        subprocess.run([cxx, "-std=c++17", "-I", os.path.join(ROOT, "desktop/src/services"), src, "-o", exe], check=True)
        r = subprocess.run([exe], capture_output=True, text=True)
        if r.returncode:
            print(r.stdout)
            bad += r.returncode
else:
    print("(no C++ compiler: C++ check skipped)")

print("same-move: %d cases, %s" % (len(CASES), "OK" if not bad else "%d FAILED" % bad))
sys.exit(1 if bad else 0)
