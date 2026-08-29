#!/usr/bin/env python3
"""Generates the iOS AppIcon.appiconset for Sommet.

The mark is "Summit sync" (shared 2026-08-29); geometry + palette live in
tools/packaging/sommet_icon.py (the single source of truth every platform renders from).

iOS icons must be fully opaque with NO alpha channel and NO rounded corners of their own -
the system masks the superellipse - so these are rendered edge-to-edge on the #14181c tile
colour (shape="square", opaque=True).

There is no iOS Xcode project in this repo yet (it's a React Native app whose root is
android/; no ios/ has been generated). This writes a complete, standard .appiconset so the
icon is ready the moment `npx react-native` (or Xcode) scaffolds the iOS target - drop/replace
android/ios/<App>/Images.xcassets/AppIcon.appiconset with it.

    ./tools/packaging/make_ios_appicon.py
"""

import json
from pathlib import Path

from sommet_icon import render_tile

OUT = Path(__file__).resolve().parent.parent.parent / "android" / "ios" / "AppIcon.appiconset"

# (idiom, point-size string, scale) -> px is size*scale. The classic full ladder: valid on
# every Xcode version and complete for App Store submission.
ENTRIES = [
    ("iphone", "20x20", "2x"), ("iphone", "20x20", "3x"),
    ("iphone", "29x29", "2x"), ("iphone", "29x29", "3x"),
    ("iphone", "40x40", "2x"), ("iphone", "40x40", "3x"),
    ("iphone", "60x60", "2x"), ("iphone", "60x60", "3x"),
    ("ipad", "20x20", "1x"), ("ipad", "20x20", "2x"),
    ("ipad", "29x29", "1x"), ("ipad", "29x29", "2x"),
    ("ipad", "40x40", "1x"), ("ipad", "40x40", "2x"),
    ("ipad", "76x76", "1x"), ("ipad", "76x76", "2x"),
    ("ipad", "83.5x83.5", "2x"),
    ("ios-marketing", "1024x1024", "1x"),
]


def px_for(size_str, scale):
    base = float(size_str.split("x")[0])
    return int(round(base * int(scale.rstrip("x"))))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    images = []
    cache = {}
    for idiom, size_str, scale in ENTRIES:
        px = px_for(size_str, scale)
        fname = f"icon-{px}.png"
        if px not in cache:
            render_tile(px, shape="square", opaque=True).save(OUT / fname)
            cache[px] = fname
        images.append({"idiom": idiom, "size": size_str, "scale": scale, "filename": fname})

    contents = {"images": images, "info": {"version": 1, "author": "sommet"}}
    (OUT / "Contents.json").write_text(json.dumps(contents, indent=2) + "\n")
    print(f"wrote {len(cache)} PNGs + Contents.json to {OUT}")


if __name__ == "__main__":
    main()
