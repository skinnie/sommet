#!/usr/bin/env python3
"""Generates the Sommet desktop app's window / taskbar / bundle icon.

The mark is "Summit sync" (shared 2026-08-29): a rounded-square device tile with a dashed
teal survey ring around a snow-capped amber summit - see tools/packaging/sommet_icon.py for
the artwork and palette (the single source of truth all platforms render from). The same
mark is drawn in-app by desktop/qml/components/SommetMark.qml.

Emits the three OS icon slots into desktop/packaging/:
  icon.png  - Linux window/taskbar icon + Qt setWindowIcon (main.cpp)
  icon.icns - macOS .app bundle icon (CMake MACOSX_BUNDLE_ICON_FILE)
  icon.ico  - Windows .exe icon (packaging/sommet.rc)

Run once to (re)generate them - committed as normal binary assets.

    ./tools/packaging/make_desktop_app_icon.py
"""

from pathlib import Path

from PIL import Image

from sommet_icon import render_tile

OUT_DIR = Path(__file__).resolve().parent.parent.parent / "desktop" / "packaging"
# ICNS/ICO sizes. macOS wants power-of-two masters; Windows wants the classic ladder down to
# 16 so the taskbar/Explorer small icons stay crisp.
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    master = render_tile(1024, shape="rounded")
    master.save(OUT_DIR / "icon.png")

    # Pillow builds the .icns from the largest image, downscaling to each requested size.
    master.save(OUT_DIR / "icon.icns", format="ICNS",
                append_images=[render_tile(s, shape="rounded") for s in ICNS_SIZES[:-1]],
                sizes=[(s, s) for s in ICNS_SIZES])

    # Multi-resolution .ico, each size rendered fresh (not one blurry downscale) so the 16px
    # taskbar icon stays legible.
    ico_imgs = [render_tile(s, shape="rounded") for s in ICO_SIZES]
    ico_imgs[-1].save(OUT_DIR / "icon.ico", format="ICO",
                      sizes=[(s, s) for s in ICO_SIZES],
                      append_images=ico_imgs[:-1])

    print(f"wrote {OUT_DIR/'icon.png'}, icon.icns, icon.ico")


if __name__ == "__main__":
    main()
