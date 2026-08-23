#!/usr/bin/env python3
"""Regenerate the app's Material Symbols Rounded font subset, INCLUDING the 18 POI type glyphs.

The app ships a tiny static subset of Google's Material Symbols Rounded (desktop uses it via
`Icons.qml`; Android now uses it for POI type icons). The original subset was made ad-hoc with
no script, so its glyph list wasn't reproducible - this file fixes that AND adds the POI icons.

It: downloads the full variable font (cached under the gitignored `full-assets/`), pins it to a
static instance (wght 400, FILL 0, GRAD 0, opsz 24 - the flat filled look the app already
used), subsets it to the UNION of {the glyphs the current subset already has} + {the 18 POI
glyphs}, and writes the result to both the desktop and Android font paths. It then prints the
POI type -> glyph -> codepoint table, which is the single source for `Icons.qml`'s POI glyphs
and `PoiScreen.tsx`'s `POI_TYPE_GLYPHS`.

Both output paths are gitignored (`assets/` rule) - the font is a local build asset, same as
the existing one. Run this to regenerate it; the codepoints it prints are what the code uses.

    ./tools/subset_material_symbols.py
"""

import os
import sys
import urllib.request

from fontTools.ttLib import TTFont
from fontTools.subset import Subsetter, Options
from fontTools.varLib import instancer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
FULL_URL = ("https://raw.githubusercontent.com/google/material-design-icons/master/"
            "variablefont/MaterialSymbolsRounded%5BFILL%2CGRAD%2Copsz%2Cwght%5D.ttf")
CACHE = os.path.join(ROOT, "full-assets", "MaterialSymbolsRounded-full.ttf")  # gitignored
DESKTOP_TTF = os.path.join(ROOT, "desktop", "assets", "fonts", "MaterialSymbolsRounded.ttf")
ANDROID_TTF = os.path.join(ROOT, "android", "android", "app", "src", "main",
                           "assets", "fonts", "MaterialSymbolsRounded.ttf")

# POI type id (0-17, F.WAYPOINT_TYPES order) -> Material Symbol glyph name. Cave/Rock have no
# exact Material Symbol, so they use the nearest terrain glyphs (elevation / filter_hdr peaks),
# distinct from Mountain's landscape.
POI_GLYPHS = {
    0: "apartment", 1: "elevation", 2: "festival", 3: "directions_car", 4: "fork_right",
    5: "trip_origin", 6: "sports_score", 7: "restaurant", 8: "forest", 9: "explore",
    10: "hotel", 11: "grass", 12: "landscape", 13: "photo_camera", 14: "route",
    15: "filter_hdr", 16: "water_drop", 17: "place",
}

# One-off icons that aren't POI-indexed - name -> Material Symbol glyph name. Coach (2026-08-21,
# the v2 training-coach nav entry) needed a chat-bubble glyph, nothing existing in the subset
# fit ("forum" - a conversation bubble, matches the chat half of the Coach screen).
EXTRA_GLYPHS = {
    "coach": "forum",
    "apps": "apps",   # the 3x3 grid - the "Suunto Apps" / "Apps" menu icon
}


def existing_codepoints(path):
    cps = set()
    for table in TTFont(path)["cmap"].tables:
        cps.update(table.cmap.keys())
    return cps


def main():
    if not os.path.exists(CACHE):
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        print(f"downloading full Material Symbols Rounded -> {CACHE}")
        urllib.request.urlretrieve(FULL_URL, CACHE)

    full = TTFont(CACHE)
    name_to_cp = {}
    for cp, gname in full.getBestCmap().items():
        name_to_cp.setdefault(gname, cp)

    poi_cp, missing = {}, []
    for tid, gname in POI_GLYPHS.items():
        cp = name_to_cp.get(gname)
        (poi_cp.__setitem__(tid, cp) if cp is not None else missing.append((tid, gname)))
    if missing:
        print("ERROR - glyph names not in the font:", missing)
        return 1

    extra_cp, missing_extra = {}, []
    for key, gname in EXTRA_GLYPHS.items():
        cp = name_to_cp.get(gname)
        (extra_cp.__setitem__(key, cp) if cp is not None else missing_extra.append((key, gname)))
    if missing_extra:
        print("ERROR - glyph names not in the font:", missing_extra)
        return 1

    keep = existing_codepoints(DESKTOP_TTF) if os.path.exists(DESKTOP_TTF) else set()
    unicodes = sorted(keep | set(poi_cp.values()) | set(extra_cp.values()))

    static = instancer.instantiateVariableFont(
        full, {"wght": 400, "FILL": 0, "GRAD": 0, "opsz": 24}, inplace=False)
    opts = Options()
    opts.name_IDs = ["*"]
    ss = Subsetter(options=opts)
    ss.populate(unicodes=unicodes)
    ss.subset(static)

    for out in (DESKTOP_TTF, ANDROID_TTF):
        os.makedirs(os.path.dirname(out), exist_ok=True)
        static.save(out)

    print(f"\nwrote {os.path.getsize(DESKTOP_TTF)} B, {len(unicodes)} glyphs, to:")
    print(f"  {os.path.relpath(DESKTOP_TTF, ROOT)}\n  {os.path.relpath(ANDROID_TTF, ROOT)}")
    print("\nPOI type -> glyph -> codepoint (source for Icons.qml + PoiScreen POI_TYPE_GLYPHS):")
    for tid in sorted(POI_GLYPHS):
        print(f"  {tid:2d}  {POI_GLYPHS[tid]:16}  U+{poi_cp[tid]:04X}  \\u{poi_cp[tid]:04x}")
    if extra_cp:
        print("\nExtra one-off icons (source for Icons.qml):")
        for key in sorted(extra_cp):
            print(f"  {key:16}  {EXTRA_GLYPHS[key]:16}  U+{extra_cp[key]:04X}  \\u{extra_cp[key]:04x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
