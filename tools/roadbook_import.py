#!/usr/bin/env python3
"""Import a brevet ROADBOOK into race controls with their official closing times.

French ACP / BRM roadbooks (and most randonneur ones) publish a control table like:

    C1 - FROIDCHAPELLE            236                     4,5      112,5     9:19     12:30
    C2 - MONTHERME                        D1              5,5      183,5     11:24    17:14
    C0 - ORCHIES                  236     D953            0,0      0,0       5:00     6:00

i.e. per control: label ("C<n> - PLACE"), then two km columns (partiel, total) and two clock
times (ouverture, fermeture). We pull the *total* km and the *fermeture* (closing) time for each
control — those are exactly the cutoffs the race timeline needs to compute per-control margins.

Only the "C<n> -" lines are controls; every other line is an intermediate locality (no times) and
is ignored. Non-French roadbooks that use "CP<n>"/"Control <n>" are matched too.

Input (JSON, file or stdin):
  {"text": "<roadbook table text>"}            # already-extracted text (works everywhere)
  {"pdf": "/path/to/roadbook.pdf"}             # extracted via `pdftotext -layout` if present
Output:
  {"ok": true, "controls": [{"label","name","km","open","close"}], "count": N}
  {"ok": false, "error": "..."}

Stdlib only. The optional PDF path shells out to the system `pdftotext` (poppler) when available;
the paste-text path has no external dependency and is the portable one (frozen binaries have no
pdftotext), so the UI leads with paste and treats PDF as a convenience.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

# A control line: "C1 - FROIDCHAPELLE", "CP3 - ...", "Control 3 - ...". Case-insensitive on the
# keyword; the number is the control ordinal.
_CTRL_RE = re.compile(r"^\s*(?:C|CP|CONTROL|CONTRÔLE|CTRL)\s*(\d+)\s*[-–:]\s*(.+)$", re.IGNORECASE)
_TIME_RE = re.compile(r"\b(\d{1,2}[:hH]\d{2})\b")
# a distance token: 112,5 / 112.5 / 112 (comma or dot decimal, European roadbooks use comma)
_KM_RE = re.compile(r"\b(\d{1,4}(?:[.,]\d+)?)\b")


def _norm_time(t: str) -> str:
    """'12h30' / '12:30' / '9:19' -> 'HH:MM' (zero-padded hour)."""
    t = t.replace("h", ":").replace("H", ":")
    hh, mm = t.split(":")
    return f"{int(hh):02d}:{mm}"


def _parse_control_line(num: int, rest: str) -> Optional[Dict[str, Any]]:
    """`rest` is everything after 'C<num> -'. Extract the place name, total km, and open/close.

    Layout (right-aligned columns): ... <partiel_km> <total_km> <open> <close>. The two clock
    times are the last two time tokens; the total km is the last distance token before them.
    Some controls (e.g. a start) omit one time; we keep whatever is present.
    """
    times = _TIME_RE.findall(rest)
    if not times:
        return None  # no cutoff on this line -> not a usable control row
    # Name = leading run of the text before the first column of digits/route codes. Everything up
    # to the first place we see 2+ spaces then a token, or the first distance/time — keep it simple:
    # take the text before the first multi-space gap, else before the first digit block.
    name = re.split(r"\s{2,}", rest.strip(), 1)[0].strip()
    name = re.sub(r"\s+\d.*$", "", name).strip() or rest.strip().split()[0]

    close = _norm_time(times[-1])
    open_ = _norm_time(times[-2]) if len(times) >= 2 else None

    # total km: the last distance token that appears before the first of the two trailing times.
    first_time_pos = rest.find(times[-2] if len(times) >= 2 else times[-1])
    head = rest[:first_time_pos] if first_time_pos > 0 else rest
    kms = _KM_RE.findall(head)
    # drop pure map/route numbers that got glued in: keep decimals if any exist, else the last int.
    decimals = [k for k in kms if ("," in k or "." in k)]
    km_tok = decimals[-1] if decimals else (kms[-1] if kms else None)
    if km_tok is None:
        return None
    km = float(km_tok.replace(",", "."))
    return {"label": f"C{num}", "name": name, "km": round(km, 1), "open": open_, "close": close}


def parse_roadbook(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for line in text.splitlines():
        m = _CTRL_RE.match(line)
        if not m:
            continue
        num = int(m.group(1))
        c = _parse_control_line(num, m.group(2))
        if c and (c["label"], c["km"]) not in seen:
            seen.add((c["label"], c["km"]))
            out.append(c)
    out.sort(key=lambda c: c["km"])
    return out


def _pdf_to_text(pdf_path: str) -> Optional[str]:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-layout", pdf_path, "-"], capture_output=True, text=True, timeout=30)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def import_roadbook(body: Dict[str, Any]) -> Dict[str, Any]:
    text = body.get("text")
    if not text and body.get("pdf"):
        text = _pdf_to_text(body["pdf"])
        if text is None:
            return {"ok": False, "error": "could not read PDF (need 'pdftotext'); paste the table text instead"}
    if not text:
        return {"ok": False, "error": "no roadbook text or readable PDF provided"}
    controls = parse_roadbook(text)
    if not controls:
        return {"ok": False, "error": "no control rows found (expected lines like 'C1 - PLACE ... 112,5 ... 12:30')"}
    return {"ok": True, "controls": controls, "count": len(controls)}


# --- self test (offline) --------------------------------------------------------------------

_SAMPLE = """\
Départ :
C0 - ORCHIES                      236      D953                          0,0         0,0         5:00      6:00
BEUVRY LA FORET                            CV RF                         4,0         4,0
C1 - FROIDCHAPELLE                236                                     4,5       112,5        9:19      12:30
CHIMAY                            241                                     4,0       125,5
C2 - MONTHERME                             D1                            5,5        183,5        11:24     17:14
C3 - DAMVILLERS                      D905                           4,5        285,0      14:32      0:00
C9 - ORCHIES                                                        3,5        600,5      0:49      21:03
"""


def _selftest():
    cs = parse_roadbook(_SAMPLE)
    print("=== roadbook_import selftest ===")
    for c in cs:
        print(f"  {c['label']:<4} {c['name']:<20} km {c['km']:<7} open {c['open']}  close {c['close']}")
    labels = [c["label"] for c in cs]
    assert labels == ["C0", "C1", "C2", "C3", "C9"], labels
    d = {c["label"]: c for c in cs}
    assert d["C1"]["km"] == 112.5 and d["C1"]["close"] == "12:30", d["C1"]
    assert d["C2"]["km"] == 183.5 and d["C2"]["open"] == "11:24" and d["C2"]["close"] == "17:14", d["C2"]
    assert d["C3"]["close"] == "00:00", d["C3"]        # midnight normalises to 00:00
    assert d["C9"]["km"] == 600.5 and d["C9"]["close"] == "21:03", d["C9"]
    assert d["C0"]["name"] == "ORCHIES", d["C0"]
    # intermediate localities (CHIMAY, BEUVRY) must NOT become controls
    assert "CHIMAY" not in [c["name"] for c in cs]
    print(f"\n✓ All roadbook_import selftest checks passed ({len(cs)} controls)")


def main(argv=None):
    p = argparse.ArgumentParser(description="Import a brevet roadbook into race controls")
    p.add_argument("input_file", nargs="?", help="JSON {text|pdf}")
    p.add_argument("--selftest", action="store_true")
    args = p.parse_args(argv)
    if args.selftest:
        _selftest()
        return
    try:
        body = json.load(open(args.input_file)) if args.input_file else json.load(sys.stdin)
        print(json.dumps(import_roadbook(body)))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
