#!/usr/bin/env python3
"""Generates the Android launcher icons for Sommet.

The mark is "Summit sync" (shared 2026-08-29): a rounded-square device tile with a dashed
teal survey ring around a snow-capped amber summit. Geometry + palette live in
tools/packaging/sommet_icon.py (the single source of truth every platform renders from), so
the Android launcher can never drift from the desktop icon.

Per density it writes:
  ic_launcher.png          - legacy square launcher (pre-Android 8)
  ic_launcher_round.png    - legacy round launcher
  ic_launcher_foreground.png - adaptive-icon foreground (ring + summit on transparent; the
                               solid tile colour is @color/ic_launcher_background = #14181c)

    python3 android/scripts/generate_icons.py
"""

import os
import sys

# The artwork lives in tools/packaging/ at the repo root (two levels up from android/scripts).
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO, "tools", "packaging"))
from sommet_icon import render_tile, render_foreground  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(BASE, "android", "app", "src", "main", "res")

# density -> (legacy launcher px, adaptive foreground px). Foreground is the 108dp adaptive
# canvas at each density; legacy is the 48dp launcher. Sizes match what shipped before.
DENSITIES = {
    "mipmap-mdpi":    (48, 108),
    "mipmap-hdpi":    (72, 162),
    "mipmap-xhdpi":   (96, 216),
    "mipmap-xxhdpi":  (144, 324),
    "mipmap-xxxhdpi": (192, 432),
}


def main():
    for folder, (launcher, fg) in DENSITIES.items():
        out = os.path.join(RES, folder)
        os.makedirs(out, exist_ok=True)
        render_tile(launcher, shape="rounded").save(os.path.join(out, "ic_launcher.png"))
        render_tile(launcher, shape="circle").save(os.path.join(out, "ic_launcher_round.png"))
        render_foreground(fg).save(os.path.join(out, "ic_launcher_foreground.png"))
        print(f"{folder}: launcher {launcher}px, foreground {fg}px OK")
    print("Done. (adaptive background colour is @color/ic_launcher_background in colors.xml)")


if __name__ == "__main__":
    main()
