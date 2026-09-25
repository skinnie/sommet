#!/usr/bin/env python3
"""Magene C406 Pro data screens ("pages") - the on-device layout of data fields per screen.
Decoded from the OneLap 1.9.3 APK (not captures): ProDecodePageStrategy (layout),
BikeComputerPageProViewModel / ProChangeItemStrategy (how a layout is rebuilt), ProPageStrategy
(the field codes the app offers, grouped as its picker groups them), ProPageLimitStrategy +
BikeComputerPageProActivity (limits) and the app's own English string arrays (field names).

Block (read with `40 42`, written with `40 43 <block>`, ack `40 43 <status>`: 0 ok, 2 busy):

    <pageCount u8> <totalLen u8> then pageCount x ( <n u8> <n field codes> )

totalLen = the byte length of all the page entries. A field code is one byte: the high nibble is
the group (0x1_ speed, 0x2_ cadence, 0x3_ heart rate, 0x4_-0x6_ power, 0x7_ distance, 0x8_ slope,
0x9_ elevation, 0xA_ gain/loss, 0xB_ time, 0xC_ shifting, 0xF_ calories/others), the low nibble
the variant; 0xFF = an empty slot. The app keeps 2..8 fields per page and at most 30 pages.

This file only encodes/decodes/validates; tools/magene_device.py does the BLE read/write.
"""

import json
import sys

MAX_PAGES = 30
MIN_FIELDS, MAX_FIELDS = 2, 8
EMPTY = 0xFF

# (group, [(code, name)]) in the OneLap picker's order. The three 0xA9/0xAB/0xAD codes are offered
# by the app but have no name in its own resources (it shows them blank); kept, labelled by code.
GROUPS = [
    ("Speed", [
        (0x10, "Speed"),
        (0x11, "Avg Speed"),
        (0x12, "Max Speed"),
        (0x13, "[lap] Avg Speed"),
        (0x14, "[lap] Max Speed"),
        (0x1B, "PRE Avg SPD"),
        (0x1C, "PRE Max SPD"),
        (0x1A, "BEST SPD"),
    ]),
    ("Cadence", [
        (0x20, "Current CAD"),
        (0x21, "Avg CAD"),
        (0x22, "Max CAD"),
        (0x23, "[lap] Avg CAD"),
        (0x24, "[lap] Max CAD"),
        (0x2B, "PRE Avg CAD"),
        (0x2C, "PRE Max CAD"),
    ]),
    ("Heart rate", [
        (0x30, "Current HR"),
        (0x31, "Avg HR"),
        (0x32, "Max HR"),
        (0x33, "[lap] Avg HR"),
        (0x34, "[lap] Max HR"),
        (0x3B, "PRE AVG HR"),
        (0x3D, "Heart Rate Zones"),
        (0x3C, "LTHR%"),
        (0x3A, "MHR%"),
    ]),
    ("Power", [
        (0x40, "Current Power"),
        (0x41, "Avg Power"),
        (0x42, "Max Power"),
        (0x43, "[lap] Avg Power"),
        (0x44, "[lap] Max Power"),
        (0x45, "Avg Power (last lap)"),
        (0x46, "Max Power (last lap)"),
        (0x47, "Estimated power"),
        (0x50, "3S POWER"),
        (0x51, "5S POWER"),
        (0x52, "10S POWER"),
        (0x53, "15S POWER"),
        (0x54, "3S AP MAX"),
        (0x55, "5S AP MAX"),
        (0x56, "10S AP MAX"),
        (0x57, "15S AP MAX"),
        (0x60, "Normalized Power®"),
        (0x61, "Training Stress®"),
        (0x62, "Intensity Factor®"),
        (0x63, "Variability Index"),
        (0x6A, "FTP%"),
        (0x5A, "Power kJ"),
        (0x64, "BALANCE"),
        (0x5B, "Avg Left/Right Balance"),
        (0x5C, "3s Left/Right Balance"),
        (0x5D, "5s Left/Right Balance"),
        (0x5E, "15s Left/Right Balance"),
        (0x65, "Torque effectiveness"),
        (0x66, "Pedal Smoothness"),
        (0x6B, "POWER ZONE"),
        (0x6C, "W/KG"),
        (0x6F, "3s Power-to-Weight Ratio"),
        (0x6D, "LAP W/KG"),
        (0x6E, "PRE W/KG"),
    ]),
    ("Distance", [
        (0x71, "Total Distance"),
        (0x72, "[lap] Total Distance"),
        (0x73, "PRE DIST"),
    ]),
    ("Slope", [
        (0x80, "Current Slope"),
        (0x81, "Avg Slope"),
        (0x82, "Max Slope"),
        (0x83, "[lap] Avg Slope"),
        (0x84, "[lap] Max Slope"),
    ]),
    ("Elevation", [
        (0x90, "Current Elev."),
        (0x91, "Avg Elev."),
        (0x92, "Max Elev."),
        (0x93, "[lap] Avg Elev."),
        (0x94, "[lap] Max Elev."),
    ]),
    ("Elevation gain & loss", [
        (0xA0, "ELE ASCENT"),
        (0xA5, "ELE DESCENT"),
        (0xA1, "LAP ASCENT"),
        (0xA6, "LAP DESCENT"),
        (0xAA, "VAM"),
        (0xAE, "PRE GAIN"),
        (0xAC, "Vertical descent speed VDM"),
        (0xAF, "PRE LOSS"),
        (0xA9, "Ascent/descent 0xA9"),
        (0xAB, "Ascent/descent 0xAB"),
        (0xAD, "Ascent/descent 0xAD"),
    ]),
    ("Time", [
        (0xB1, "Moving Time"),
        (0xB2, "[lap] Time"),
        (0xB0, "Total"),
        (0xB3, "Clock"),
        (0xB4, "Sunset Time"),
        (0xB5, "Sunrise Time"),
        (0xBA, "Previous lap time"),
        (0xBB, "Best Time"),
    ]),
    ("Calories", [
        (0xF2, "Calories"),
        (0xF3, "[lap] Calories"),
        (0xFA, "CAL/H"),
    ]),
    ("Others", [
        (0xF0, "Total laps"),
        (0xF1, "Current Temp"),
    ]),
    ("Electronic shifting", [
        (0xC0, "Gear"),
        (0xC1, "Front Gear"),
        (0xC2, "Rear Gear"),
        (0xC3, "Front Power"),
        (0xC4, "Rear Power"),
    ]),
]
FIELDS = {code: name for _, items in GROUPS for code, name in items}
FIELDS[EMPTY] = "Empty"


def decode_pages(block):
    """block = the bytes after `40 42 <status>` -> [[code, ...], ...]. Raises ValueError when the
    block isn't in the Pro layout (a different unit / format) - writes are refused then."""
    if len(block) < 2:
        raise ValueError("pages block too short")
    count, total = block[0], block[1]
    body = block[2:2 + total]
    if len(body) != total:
        raise ValueError(f"pages block says {total} bytes, has {len(block) - 2}")
    pages, i = [], 0
    while i < len(body):
        n = body[i]
        page = list(body[i + 1:i + 1 + n])
        if len(page) != n:
            raise ValueError("truncated page")
        pages.append(page)
        i += 1 + n
    if len(pages) != count:
        raise ValueError(f"pages block says {count} pages, has {len(pages)}")
    return pages


def validate(pages):
    if not 1 <= len(pages) <= MAX_PAGES:
        raise ValueError(f"1..{MAX_PAGES} pages")
    for k, page in enumerate(pages, 1):
        if not MIN_FIELDS <= len(page) <= MAX_FIELDS:
            raise ValueError(f"page {k}: {MIN_FIELDS}..{MAX_FIELDS} fields")
        for code in page:
            if int(code) not in FIELDS:
                raise ValueError(f"page {k}: unknown field code 0x{int(code):02X}")


def encode_pages(pages):
    validate(pages)
    body = bytearray()
    for page in pages:
        body.append(len(page))
        body.extend(int(c) for c in page)
    if len(body) > 255:
        raise ValueError("layout too large for the device (255-byte limit)")
    return bytes([len(pages), len(body)]) + bytes(body)


def describe(pages):
    return [[{"code": c, "name": FIELDS.get(c, f"0x{c:02X}")} for c in page] for page in pages]


def main():
    # ./tools/magene_pages.py fields                 -> the field catalogue (groups) as JSON
    # ./tools/magene_pages.py decode <hex block>     -> pages
    # ./tools/magene_pages.py encode '<pages JSON>'  -> hex block
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fields"
    if cmd == "fields":
        print(json.dumps([{"group": g, "fields": [{"code": c, "name": n} for c, n in items]}
                          for g, items in GROUPS] + [{"group": "Empty", "fields": [{"code": EMPTY, "name": "Empty"}]}]))
    elif cmd == "decode":
        print(json.dumps(describe(decode_pages(bytes.fromhex(sys.argv[2])))))
    elif cmd == "encode":
        print(encode_pages(json.loads(sys.argv[2])).hex())
    return 0


if __name__ == "__main__":
    sys.exit(main())
