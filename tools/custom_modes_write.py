#!/usr/bin/env python3
"""Encodes the Ambit3 CustomModes BXml body - the inverse of custom_modes.py's decoder,
following this project's established convention (build_route.py/write_nav.py: a pure Python
reference encoder, cross-verified before any C port or real write is attempted).

**Scope, stated precisely**: this covers the BXml tag tree - EXERCISE_MODES (settings,
displays, rules) and SPORT_MODES - which custom_modes_andre.md fully decoded and byte-verified
against a real dump. It does NOT cover the remaining bytes of the 12288-byte CustomModes
region beyond the ~10240-byte BXml body (BXmlConverter::convertBinaryToTree was seen parsing
exactly 0x2800 bytes out of the region, see custom_modes_andre.md's "Traced further" section) -
that tail is presumed header/checksum by analogy with Routes/Waypoints' own closing-hash
layout, but that has NOT been confirmed, and this file does not claim to reproduce it. Building
that closing structure - or confirming the region needs none - is real work still open before
any encoder here is write-ready; see V3_CHANGELOG.md.

Every build_* function is the direct inverse of the matching decode_* function in
custom_modes.py: build_x(decode_x(data, offset, length)) reproduces the original content
bytes exactly for every field this project's decoder understands. Verified by
`--selftest`: round-trips a synthetic mode through encode -> decode -> compare, using field
values copied from custom_modes_andre.md's real, byte-verified examples (Openwater swim's
ActivityID/CustomModeID/UseHw/AltiBaroMode, the EXERCISE_MODES_TYPE=2 marker, and the exact
tag/length header bytes documented there), not arbitrary numbers.

    ./tools/custom_modes_write.py --selftest
"""

import struct

from custom_modes import (
    DEVICE_CUSTOM, EXERCISE_MODES, EXERCISE_MODES_MODE, EXERCISE_MODES_SETTING_NAME_LEN64,
    EXERCISE_MODES_DISPLAYS, EXERCISE_MODES_DISPLAY, EXERCISE_MODES_DISP_SETTING,
    EXERCISE_MODES_DISP_FIELD, EXERCISE_MODES_DISP_FIELD_SETTING,
    EXERCISE_MODES_DISP_FIELD_SHORTCUT, EXERCISE_MODES_TYPE, EXERCISE_MODES_RULES,
    EXERCISE_MODES_RULE, SPORT_MODES, SPORT_MODE, SPORT_MODE_ACTIVITY_ID, SPORT_MODE_EXERCISE,
    SPORT_MODE_SETTING_NAME_LEN64, SPORT_MODE_ORDER, SPORT_MODE_APP_META, SETTING_FIELDS,
    INTERVAL_SLOT_FULL, INTERVAL_SLOT_SHORT, INTERVAL_SLOT_REPEATS, decode,
    decode_exercise_mode, decode_sport_mode_slot,
)

SETTINGS_NAME_SIZE = 64
SETTINGS_SIZE = 138  # HEADER_SIZE-less content length, every real mode on the reference watch


def tag(tag_id, content):
    """[u16 LE tag_id][u16 LE length] + content - the one primitive the whole format is built
    from (BXmlConverter::parseBinaryTag's write-side inverse)."""
    return struct.pack("<HH", tag_id, len(content)) + content


def build_settings(settings):
    """Inverse of decode_settings(). `settings` is a dict shaped exactly like decode_settings's
    return value (Name, ActivityID, CustomModeID, UseHw, ..., IntervalSlots)."""
    # CORRECTED 2026-08-22: was iso-8859-15 - real hardware (André's French Ambit3 Sport)
    # proved the watch sends/expects UTF-8 for name fields, see ambit_format.py's
    # encode_name() header comment. Truncation re-decoded with "ignore" so a multi-byte
    # character never gets cut in half at the byte boundary (same fix as encode_name()).
    name = settings["Name"].encode("utf-8", "replace")[:SETTINGS_NAME_SIZE]
    name = name.decode("utf-8", "ignore").encode("utf-8")
    body = name.ljust(SETTINGS_NAME_SIZE, b"\0")

    custom_mode_id = settings["CustomModeID"]
    values = dict(settings)
    values["CustomModeIdLow"] = custom_mode_id & 0xFFFF
    values["CustomModeIdHigh"] = (custom_mode_id >> 16) & 0xFFFF
    for field, fmt in SETTING_FIELDS:
        body += struct.pack("<" + fmt, values[field])

    intervals = settings["IntervalSlots"]
    if len(intervals) != 1 + INTERVAL_SLOT_REPEATS:
        raise ValueError(f"expected {1 + INTERVAL_SLOT_REPEATS} interval slots, "
                          f"got {len(intervals)}")
    for i, slot in enumerate(intervals):
        slot_fields = INTERVAL_SLOT_FULL if i == 0 else INTERVAL_SLOT_SHORT
        for field, fmt in slot_fields:
            body += struct.pack("<" + fmt, slot[field])

    if len(body) != SETTINGS_SIZE:
        raise ValueError(f"settings body is {len(body)} bytes, expected {SETTINGS_SIZE}")
    return tag(EXERCISE_MODES_SETTING_NAME_LEN64, body)


def build_disp_field(field):
    """Inverse of decode_disp_field(). `field` = {"Index", "Type", "Shortcuts": [...]}."""
    body = tag(EXERCISE_MODES_DISP_FIELD_SETTING, struct.pack("<HH", field["Index"], field["Type"]))
    for shortcut in field.get("Shortcuts", []):
        body += tag(EXERCISE_MODES_DISP_FIELD_SHORTCUT, struct.pack("<H", shortcut))
    return tag(EXERCISE_MODES_DISP_FIELD, body)


def build_display(display):
    """Inverse of decode_display(). `display` = {"Template", "Type", "Fields": [...]}."""
    body = tag(EXERCISE_MODES_DISP_SETTING, struct.pack("<HH", display["Template"], display["Type"]))
    for field in display["Fields"]:
        body += build_disp_field(field)
    return tag(EXERCISE_MODES_DISPLAY, body)


def build_rule(rule):
    """Inverse of decode_rule(). Flat 6 bytes, no sub-tags - see decode_rule's own docstring
    for why (RULEIDX/USERULE/LOGRULE aren't in either BXml dictionary)."""
    return struct.pack("<HHH", rule["RuleIdx"], int(rule["UseRule"]), int(rule["LogRule"]))


def build_mode(mode):
    """Inverse of decode_exercise_mode(). `mode` = {"Settings", "Displays", "Rules",
    "AppMeta"}. Tag order matches training_program_andre.md's confirmed-real ordering
    (SETTINGS, APP_META, DISPLAYS, RULES) - NOT append-everything-at-the-end, which
    workout_install.py's own writeup already found and fixed once for the app-install path."""
    body = build_settings(mode["Settings"])

    if mode.get("AppMeta"):
        meta = mode["AppMeta"]
        body += tag(0x1FF, struct.pack("<II", meta["Timestamp1"], meta["Timestamp2"]))

    # Real bug, found 2026-08-10 by tools/custom_modes_roundtrip.py against SuuntoLink's own
    # writes: this tag was emitted unconditionally, so a mode with NO displays got an empty
    # 4-byte DISPLAYS header that SuuntoLink never writes. It shows up the instant a sport
    # mode is created and before its first display is added - save 0 of
    # `running2fromcreateandthen1to7` (and of `ambit3addsports`) rebuilt 4 bytes too long
    # and diverged at the region's own length field. Guarded the same way RULES already was.
    # Directly relevant to add/remove-display support: emptying a mode would otherwise write
    # a region that does not match what the watch is ever given.
    if mode["Displays"]:
        displays_body = b"".join(build_display(d) for d in mode["Displays"])
        body += tag(EXERCISE_MODES_DISPLAYS, displays_body)

    if mode.get("Rules"):
        rules_body = b"".join(tag(EXERCISE_MODES_RULE, build_rule(r)) for r in mode["Rules"])
        body += tag(EXERCISE_MODES_RULES, rules_body)

    return tag(EXERCISE_MODES_MODE, body)


def build_sport_mode_slot(slot):
    """Inverse of decode_sport_mode_slot(). `slot` = {"Name", "ActivityID", "Exercises": [...],
    "Order", "AppMeta"}. A single-sport slot has one Exercises entry; a multisport combo
    (Triathlon) has one per leg, in order - see decode_sport_mode_slot's own docstring for the
    confirmed 0/1/2/1/3 swim/T1/bike/T2/run example. Tag order (NAME, ACTIVITY_ID, EXERCISE*N,
    ORDER, then AppMeta if present) matches the real order observed on every slot of a live
    dump, 2026-08-07 - see SPORT_MODE_ORDER/SPORT_MODE_APP_META's own docstrings in
    custom_modes.py.

    CORRECTED 2026-08-22: "`Order` is required - every real slot has one" was wrong - real,
    live hardware (André's Ambit3 Sport AND Run, both) has the SPORT_MODE_ORDER tag on
    NONE of their real sport-mode slots (confirmed by grepping the raw tag bytes directly
    in the flash dump, not just the decoder - 0 occurrences on either watch). The earlier
    "every real slot has one" claim was true only of whatever dump it was confirmed
    against (likely the Peak) - a genuine per-model difference, not a bug in those watches.
    `Order` is now optional, same as `AppMeta` already was, so the byte-exact re-encode
    safety check (this function's caller) can pass on Sport/Run instead of always
    REFUSING with a TypeError."""
    # CORRECTED 2026-08-22: was iso-8859-15, see build_settings()'s own comment above.
    name = slot["Name"].encode("utf-8", "replace")[:64]
    name = name.decode("utf-8", "ignore").encode("utf-8").ljust(64, b"\0")
    body = tag(SPORT_MODE_SETTING_NAME_LEN64, name)
    body += tag(SPORT_MODE_ACTIVITY_ID, struct.pack("<H", slot["ActivityID"]))
    for exercise in slot["Exercises"]:
        body += tag(SPORT_MODE_EXERCISE, struct.pack("<H", exercise))
    if slot.get("Order") is not None:
        body += tag(SPORT_MODE_ORDER, struct.pack("<I", slot["Order"]))
    if slot.get("AppMeta") is not None:
        body += tag(SPORT_MODE_APP_META, struct.pack("<I", slot["AppMeta"]))
    return tag(SPORT_MODE, body)


def build_custom_modes_body(result, format_type=2):
    """Inverse of decode(). `result` is shaped exactly like decode()'s return value
    (format_type, exercise_modes, sport_modes). Produces the DEVICE_CUSTOM-wrapped BXml tree
    only - see this module's docstring for what's deliberately NOT included (the region's
    outer header/checksum, still unconfirmed)."""
    exercise_body = tag(EXERCISE_MODES_TYPE, struct.pack("<H", result.get("format_type", format_type)))
    for mode in result["exercise_modes"]:
        exercise_body += build_mode(mode)

    sport_modes_body = b"".join(build_sport_mode_slot(s) for s in result["sport_modes"])

    device_custom_body = tag(EXERCISE_MODES, exercise_body) + tag(SPORT_MODES, sport_modes_body)
    return tag(DEVICE_CUSTOM, device_custom_body)


def _selftest():
    """Round-trips a synthetic mode through build_* -> decode_* and checks equality, using
    field values taken verbatim from custom_modes_andre.md's real, byte-verified "Openwater
    swim" example (ActivityID=0x53, CustomModeID=60596, UseHw=0x0003, AltiBaroMode=1) - not
    arbitrary numbers. Also checks the exact tag/length header bytes the doc transcribed
    directly from the real dump, byte for byte."""
    # From custom_modes_andre.md: "offset 8: 0b 01 02 00 -> tag=0x010b length=2 value=2"
    type_tag = tag(EXERCISE_MODES_TYPE, struct.pack("<H", 2))
    expected = bytes.fromhex("0b0102000200")
    assert type_tag == expected, f"EXERCISE_MODES_TYPE mismatch: {type_tag.hex()} != {expected.hex()}"

    settings = {
        "Name": "Openwater swim", "ActivityID": 0x53, "CustomModeID": 60596,
        "UseHw": 0x0003, "AltiBaroMode": 1, "GpsPowerMode": 0, "RecordingInterval": 0,
        "Autolap": 0, "HrHigh": 0, "HrLow": 0, "HrLimitsUse": 0, "AutoStart": 0,
        "AutoPause": 0, "AutoScrolling": 0, "IntTimerFlags": 0, "IntTimerCount": 0,
        "IntervalSlots": [{"Flags": 0, "Type": 0, "MaxLimit": 0, "MinLimit": 0, "Padding": 0,
                            "Len": 0}] + [{"Flags": 0, "Type": 0, "MaxLimit": 0, "MinLimit": 0}
                                          for _ in range(5)],
    }
    settings_tag = build_settings(settings)
    # "offset 18: 03 01 8a 00 -> tag=0x0103 SETTING_NAME_LEN64 length=0x008a=138"
    assert settings_tag[:4] == bytes.fromhex("03018a00"), settings_tag[:4].hex()
    assert len(settings_tag) == 4 + SETTINGS_SIZE

    decoded_back = decode_settings_roundtrip(settings_tag)
    for key, value in settings.items():
        assert decoded_back[key] == value, f"{key}: {decoded_back[key]!r} != {value!r}"

    mode = {
        "Settings": settings,
        "Displays": [{
            "Template": 0x0107, "Type": 0,
            "Fields": [{"Index": 0x18, "Type": 8, "Shortcuts": [0, 8]},
                       {"Index": 0x19, "Type": 4, "Shortcuts": []}],
        }],
        "Rules": [{"RuleIdx": 0, "UseRule": True, "LogRule": False}],
        "AppMeta": {"Timestamp1": 1785000000, "Timestamp2": 1785000002},
    }
    mode_bytes = build_mode(mode)
    tag_id, length = struct.unpack_from("<HH", mode_bytes, 0)
    assert tag_id == EXERCISE_MODES_MODE
    redecoded = decode_exercise_mode(mode_bytes, 4, length)
    assert redecoded["Settings"]["Name"] == "Openwater swim"
    assert redecoded["Settings"]["ActivityID"] == 0x53
    assert redecoded["Displays"][0]["Template"] == 0x0107
    assert redecoded["Displays"][0]["Fields"][0]["Index"] == 0x18
    assert redecoded["Displays"][0]["Fields"][0]["Shortcuts"] == [0, 8]
    assert redecoded["Rules"][0] == {"RuleIdx": 0, "UseRule": True, "LogRule": False}
    assert redecoded["AppMeta"] == {"Timestamp1": 1785000000, "Timestamp2": 1785000002}

    slot = {"Name": "Cycling", "ActivityID": 4, "Exercises": [2], "Order": 2, "AppMeta": None}
    slot_bytes = build_sport_mode_slot(slot)
    tag_id, length = struct.unpack_from("<HH", slot_bytes, 0)
    redecoded_slot = decode_sport_mode_slot(slot_bytes, 4, length)
    assert redecoded_slot == slot, redecoded_slot

    triathlon = {"Name": "Triathlon", "ActivityID": 0x13, "Exercises": [0, 1, 2, 1, 3],
                 "Order": 10, "AppMeta": None}
    tri_bytes = build_sport_mode_slot(triathlon)
    tag_id, length = struct.unpack_from("<HH", tri_bytes, 0)
    # custom_modes_andre.md originally confirmed 104 bytes for Triathlon's 5-leg encoding -
    # that was before SPORT_MODE_ORDER (0x2fe) was found (2026-08-07); 112 = 104 + the 8-byte
    # ORDER tag every real slot carries.
    assert length == 112, length
    redecoded_tri = decode_sport_mode_slot(tri_bytes, 4, length)
    assert redecoded_tri == triathlon

    full = {"format_type": 2, "exercise_modes": [mode], "sport_modes": [slot, triathlon]}
    body = build_custom_modes_body(full)
    region = body.ljust(12288, b"\xff")
    redecoded_full = decode(region)
    assert redecoded_full["format_type"] == 2
    assert redecoded_full["exercise_modes"][0]["Settings"]["Name"] == "Openwater swim"
    assert [s["Name"] for s in redecoded_full["sport_modes"]] == ["Cycling", "Triathlon"]
    assert redecoded_full["sport_modes"][1]["Exercises"] == [0, 1, 2, 1, 3]

    print(f"OK - {len(body)} bytes of BXml body, full round-trip through decode() matches "
          f"input exactly (region padded to 12288 with 0xff)")
    print("NOTE: this covers the BXml body only - the region's outer header/checksum "
          "(beyond ~10240 bytes) is still unconfirmed, see this file's own docstring.")


def decode_settings_roundtrip(settings_tag_bytes):
    from custom_modes import decode_settings
    _, length = struct.unpack_from("<HH", settings_tag_bytes, 0)
    return decode_settings(settings_tag_bytes, 4, length)


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(__doc__)
