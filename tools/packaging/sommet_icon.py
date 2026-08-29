#!/usr/bin/env python3
"""Single source of truth for the Sommet app-icon artwork.

The mark ("Summit sync", shared 2026-08-29) is a rounded-square device tile carrying a
dashed teal survey ring around a snow-capped amber summit - Sommet is French for "summit",
the dashed ring is the GPS track/sync the app plants around a route, the white cap is the
peak. Every platform icon (desktop PNG/ICO/ICNS, Android launcher + adaptive foreground,
iOS AppIcon set) is rendered from the geometry below so they never drift apart.

Coordinates live in a 240-unit reference box (the size the artwork was drawn in); the
renderer scales that box to any target size and supersamples for clean dashed/round edges.
Import `render_tile` / `render_foreground` from here - do not re-hardcode the geometry.
"""

import math

from PIL import Image, ImageDraw

# --- palette (straight from the shared summit_sync_snowcap artwork) ---------------------
BG    = (20, 24, 28, 255)     # #14181c - device-black tile
RING  = (29, 158, 117, 255)   # #1d9e75 - dashed survey / sync ring
AMBER = (239, 159, 39, 255)   # #ef9f27 - the summit body
SNOW  = (242, 242, 238, 255)  # #f2f2ee - the snow cap

# --- geometry in the 240-unit reference box --------------------------------------------
REF = 240.0
CORNER = 54.0                              # rounded-tile corner radius
RING_CX, RING_CY, RING_R = 120.0, 122.0, 78.0
RING_W = 7.0                               # stroke width
DASH, GAP = 11.0, 13.0                     # stroke-dasharray
AMBER_TRI = [(76, 166), (120, 76), (164, 166)]
SNOW_CAP  = [(120, 76), (143, 123), (128, 116), (120, 124), (111, 116), (97, 123)]

# Visual outer diameter of the artwork = the ring plus its stroke. Used to fit the content
# inside a target fraction of a canvas (Android adaptive safe zone, etc.).
CONTENT_DIAM = 2 * (RING_R + RING_W / 2.0)  # 163


def _draw_dashed_ring(d, sc, cx, cy):
    """Round-capped dashed ring, stamped as overlapping dots so the caps are truly round
    (PIL's arc gives butt caps only)."""
    r = RING_R
    circumference = 2 * math.pi * r
    period = DASH + GAP
    stamp = (RING_W / 2.0) * sc
    step = 0.35                              # reference units between stamps -> heavy overlap
    s = 0.0
    while s < circumference:
        if (s % period) < DASH:
            ang = s / r
            px = (cx + r * math.cos(ang)) * sc
            py = (cy + r * math.sin(ang)) * sc
            d.ellipse([px - stamp, py - stamp, px + stamp, py + stamp], fill=RING)
        s += step


def _draw_content(d, sc, cx=RING_CX, cy=RING_CY):
    """Ring + amber summit + snow cap, in reference units scaled by `sc`, centred so the
    ring sits at (cx, cy). Ordering matches the artwork: ring behind, amber, then cap."""
    dx, dy = cx - RING_CX, cy - RING_CY
    _draw_dashed_ring(d, sc, RING_CX + dx, RING_CY + dy)
    d.polygon([((x + dx) * sc, (y + dy) * sc) for x, y in AMBER_TRI], fill=AMBER)
    d.polygon([((x + dx) * sc, (y + dy) * sc) for x, y in SNOW_CAP], fill=SNOW)


def render_tile(size, ss=4, shape="rounded", opaque=False):
    """The full icon tile at `size` px.

    shape: "rounded" (rounded square, the canonical look), "circle" (round launcher),
           "square" (edge-to-edge, for iOS which masks its own corners).
    opaque: drop the alpha channel (iOS forbids transparency).
    """
    n = size * ss
    sc = n / REF
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if shape == "circle":
        d.ellipse([0, 0, n - 1, n - 1], fill=BG)
    elif shape == "square":
        d.rectangle([0, 0, n - 1, n - 1], fill=BG)
    else:
        d.rounded_rectangle([0, 0, n - 1, n - 1], radius=CORNER * sc, fill=BG)
    _draw_content(d, sc)
    img = img.resize((size, size), Image.LANCZOS)
    if opaque:
        flat = Image.new("RGB", (size, size), BG[:3])
        flat.paste(img, mask=img.split()[3])
        return flat
    return img


def render_foreground(size, ss=4, safe_frac=0.64):
    """Transparent Android adaptive foreground: ring + summit only (the solid tile colour
    is the background layer). Content is scaled so the ring's outer diameter spans
    `safe_frac` of the canvas, keeping it inside the 66%% adaptive-icon safe circle."""
    n = size * ss
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # scale so CONTENT_DIAM reference-units -> safe_frac * n pixels; centre the ring.
    sc = (safe_frac * n) / CONTENT_DIAM
    center = (n / 2.0) / sc                  # canvas centre expressed in reference units
    _draw_content(d, sc, cx=center, cy=center)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    # quick visual proof sheet
    from pathlib import Path
    out = Path(__file__).resolve().parent / "_icon_preview.png"
    sheet = Image.new("RGBA", (240 * 3 + 40, 260), (255, 255, 255, 255))
    sheet.paste(render_tile(240, shape="rounded"), (0, 10))
    sheet.paste(render_tile(240, shape="circle"), (250, 10))
    fg = Image.new("RGBA", (240, 240), BG)
    fg.alpha_composite(render_foreground(240))
    sheet.paste(fg, (500, 10))
    sheet.save(out)
    print("wrote", out)
