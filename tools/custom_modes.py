#!/usr/bin/env python3
"""Decodes the Ambit3 CustomModes flash region (sport modes), a format this project only
reverse-engineered on 2026-08-05 - see docs/custom_modes_andre.md for the full derivation.

Unlike Waypoints/Routes/GpsSGEE (fixed-stride binary records) or DeviceSettings/POIs/
ActivityTracking (SBEM0102 [id][len][data] entries), CustomModes uses a third, distinct
on-device encoding: Suunto's own "BXml" binary-encoded tree format. Every tag is a 4-byte
header - [u16 LE tag_id][u16 LE length] - followed by that many content bytes, which may
themselves be further nested tags. The tag dictionary below (BXML_TAGS, FIELD_TYPES) is
extracted verbatim from Communist::Bluebird::BXmlTagMapping and BXmlIdMapping in the
decompiled assets/APK/movescountapp/ghidra/libkomposti-ng.so.c - not guessed.

    ./tools/custom_modes.py --from /tmp/dump_CustomModes.bin
    ./tools/custom_modes.py --save /tmp/before.bin   # live read + keep the raw bytes
"""

import argparse
import datetime
import json
import struct

CUSTOM_MODES_BASE = 0x002000
CUSTOM_MODES_SIZE = 12288  # confirmed live via 0x0b21, 2026-08-05

# BXmlTagMapping - the container/structure tags (libkomposti-ng.so.c ~846489-846913).
DEVICE_CUSTOM = 0x003
EXERCISE_MODES = 0x100
EXERCISE_MODES_MODE = 0x101
EXERCISE_MODES_SETTING = 0x102
EXERCISE_MODES_SETTING_NAME_LEN64 = 0x103
EXERCISE_MODES_DISPLAYS = 0x105
EXERCISE_MODES_DISPLAY = 0x106
EXERCISE_MODES_DISP_SETTING = 0x107
EXERCISE_MODES_DISP_FIELD = 0x108
EXERCISE_MODES_DISP_FIELD_SETTING = 0x109
EXERCISE_MODES_DISP_FIELD_SHORTCUT = 0x10A
EXERCISE_MODES_TYPE = 0x10B
EXERCISE_MODES_RULES = 0x10C
EXERCISE_MODES_RULE = 0x10D
# Not in BXmlTagMapping/BXmlIdMapping in libkomposti-ng.so.c at all - this app version predates
# Suunto Apps being wired into CustomModes. Observed live, 2026-08-05, appearing for the first
# time on a mode that just had a Suunto App assigned via SuuntoLink 4.1.15: an 8-byte leaf,
# two consecutive uint32 LE Unix timestamps, 2 seconds apart, both landing exactly on the real
# wall-clock moment of that sync. Name and exact semantics (e.g. created/modified) are inferred,
# not sourced - see docs/custom_modes_andre.md.
EXERCISE_MODES_APP_META = 0x1FF
SPORT_MODES = 0x200
SPORT_MODE = 0x210
SPORT_MODE_SETTING_NAME = 0x212
SPORT_MODE_ACTIVITY_ID = 0x213
SPORT_MODE_EXERCISE = 0x214
SPORT_MODE_SETTING_NAME_LEN64 = 0x215
# Not in BXmlTagMapping/BXmlIdMapping either - found 2026-08-07 the same way EXERCISE_MODES_
# APP_META was, by round-tripping a real live dump through decode()->encode() and comparing
# byte for byte against custom_modes_write.py's rebuild (see docs/V3_CHANGELOG.md). Present on
# EVERY SPORT_MODE slot on the reference watch: a 4-byte uint32 whose values (1,2,3,4,5,6,7,
# 9,10) are a persistent per-slot order/ID that survives deletion - the reference watch had
# slot 8 ("Alpine skiing") deleted via SuuntoLink, and the surviving values still skip 8
# rather than renumbering, ruling out "just flash position." Name and exact semantics
# inferred, not sourced.
SPORT_MODE_ORDER = 0x2FE
# Present only on the 3 slots whose EXERCISE_MODES_MODE has a Suunto App assigned (Cycling,
# Indoor training, Mountaineering) - a 4-byte uint32 Unix timestamp landing 2-6 seconds after
# that same mode's own EXERCISE_MODES_APP_META timestamps, every time, across all 3 - not a
# coincidence. Name and exact semantics inferred, not sourced.
SPORT_MODE_APP_META = 0x2FF

BXML_TAGS = {
    0x002: "DEVICE_DETAIL", 0x010: "FIRMWARE", 0x011: "FIRMWARE_DEVICE_INFO",
    0x020: "HW_DETAILS", 0x021: "HW_DETAILS_SUPPORTED_DEVICES", 0x022: "HW_DETAILS_DEVICE",
    DEVICE_CUSTOM: "DEVICE_CUSTOM",
    EXERCISE_MODES: "EXERCISE_MODES", EXERCISE_MODES_MODE: "EXERCISE_MODES_MODE",
    EXERCISE_MODES_SETTING: "EXERCISE_MODES_SETTING",
    EXERCISE_MODES_SETTING_NAME_LEN64: "EXERCISE_MODES_SETTING_NAME_LEN64",
    EXERCISE_MODES_DISPLAYS: "EXERCISE_MODES_DISPLAYS",
    EXERCISE_MODES_DISPLAY: "EXERCISE_MODES_DISPLAY",
    EXERCISE_MODES_DISP_SETTING: "EXERCISE_MODES_DISP_SETTING",
    EXERCISE_MODES_DISP_FIELD: "EXERCISE_MODES_DISP_FIELD",
    EXERCISE_MODES_DISP_FIELD_SETTING: "EXERCISE_MODES_DISP_FIELD_SETTING",
    EXERCISE_MODES_DISP_FIELD_SHORTCUT: "EXERCISE_MODES_DISP_FIELD_SHORTCUT",
    EXERCISE_MODES_TYPE: "EXERCISE_MODES_TYPE",
    EXERCISE_MODES_RULES: "EXERCISE_MODES_RULES", EXERCISE_MODES_RULE: "EXERCISE_MODES_RULE",
    EXERCISE_MODES_APP_META: "EXERCISE_MODES_APP_META",
    SPORT_MODES: "SPORT_MODES", SPORT_MODE: "SPORT_MODE",
    SPORT_MODE_SETTING_NAME: "SPORT_MODE_SETTING_NAME",
    SPORT_MODE_ACTIVITY_ID: "SPORT_MODE_ACTIVITY_ID", SPORT_MODE_EXERCISE: "SPORT_MODE_EXERCISE",
    SPORT_MODE_SETTING_NAME_LEN64: "SPORT_MODE_SETTING_NAME_LEN64",
    SPORT_MODE_ORDER: "SPORT_MODE_ORDER", SPORT_MODE_APP_META: "SPORT_MODE_APP_META",
}

# BXmlIdMapping - the leaf field-type / screen-template dictionary (~846916-848423).
# Reproduced in full since it is the only "what can this custom mode display" catalogue
# this project has; not all of it is wired into the decoder below yet (DISPLAYS/RULES).
FIELD_TYPES = {
    0x0001: "MT_MAIN", 0x0002: "MT_EXERCISE", 0xFFFE: "MT_NONE",
    0x0000: "FT_SHORTCUT", 0x0001: "FT_TIME", 0x0002: "FT_TIME_SEC", 0x0003: "FT_DATE",
    0x0004: "FT_WEEKDAY", 0x0005: "FT_TIMER", 0x0006: "FT_ALTI", 0x0007: "FT_BARO",
    0x0008: "FT_COMPASS", 0x0009: "FT_HEART_RATE_AVG", 0x000A: "FT_DISTANCE",
    0x000B: "FT_VELOCITY", 0x000C: "FT_AVGSPEED", 0x000D: "FT_TEMPERATURE",
    0x000E: "FT_BARO_GRAPH", 0x000F: "FT_ALTI_GRAPH", 0x0010: "FT_COMPASSCARDINAL",
    0x0011: "FT_TOTAL_CALORIES", 0x0012: "FT_REF_ALTI", 0x0013: "FT_ENERGY_SYMBOL",
    0x0014: "FT_HEART_RATE_PEAK", 0x0015: "FT_HEART_RATE_CURR", 0x0016: "FT_HEART_RATE_GRAPH",
    0x0017: "FT_CADENCE", 0x0018: "FT_NAVI_ARROW", 0x0019: "FT_NAVI_DISTANCE",
    0x001A: "FT_NAVI_DIRECTION", 0x001B: "FT_DUAL_TIME", 0x001C: "FT_PACE",
    0x001D: "FT_AVG_PACE", 0x001E: "FT_RECOVERY_TIME", 0x001F: "FT_TE",
    0x0020: "FT_EXERCISE_ALTI_GRAPH", 0x0033: "FT_RULE_ENGINE_0", 0x0034: "FT_RULE_ENGINE_1",
    0x0035: "FT_RULE_ENGINE_2", 0x0036: "FT_STOPWATCH_ABC", 0x0037: "FT_SW_ABC_LAP_TIME",
    0x0038: "FT_SW_ABC_LAP", 0x0039: "FT_SW_TIME", 0x003A: "FT_DEVELOPER_TITLE",
    0x003B: "FT_TEXT_ADJUST", 0x003C: "FT_CALIBRATED_DISTANCE", 0x003D: "FT_INTERVAL_VALUE",
    0x003E: "FT_INTERVAL_TITLE", 0x003F: "FT_SPLIT_LAP_DISTANCE", 0x0040: "FT_SWIM_STYLE_TITLE",
    0x0042: "FT_DRILL_TITLE", 0x0044: "FT_BATTERY_CHARGE", 0x0046: "FT_SPORT_LAP_STOPWATCH",
    0x0048: "FT_SPORT_LAP_AVGSPEED", 0x004A: "FT_BIKE_POWER_AVG", 0x004C: "FT_BIKE_POWER_10S",
    0x004E: "FT_BIKE_POWER_LAP", 0x0050: "FT_BIKE_POWER_LAP_MAX", 0x0052: "FT_SWIM_STYLE",
    0x0054: "FT_SWIM_STROKES", 0x0056: "FT_SWIM_PACE", 0x0058: "FT_SWIM_AVG_PACE",
    # Real, 2026-08-10, NOT from libkomposti (its 68 FT_ names are all already here -
    # these two are simply absent from it). Named from André's own side-by-side of a
    # real Openwater swim mode against SuuntoLink: the watch's display 1 decodes as
    # Top=0x47, Center=0x46, Bottom=shortcuts[0x58, 0x57], and SuuntoLink shows that
    # same screen as "current activity distance / current activity duration / average
    # swim pace + average stroke rate". 0x46 and 0x48 were already known as
    # FT_SPORT_LAP_STOPWATCH/AVGSPEED - which is what SuuntoLink labels "Current
    # activity duration"/"avg speed" in its Multisport category - so 0x47 sitting
    # between them as "current activity distance" fits both the observation and the
    # numbering. 0x57 likewise sits between FT_SWIM_PACE and FT_SWIM_AVG_PACE.
    # Rule-engine slots 3 and 4 do NOT continue the 0x33-0x35 run - they resume at 0x6B/0x6C,
    # and each slot has a companion field at 0x6D + slot used as a graph display's second
    # field. Both proven on Andre's real Running2 (5 installed apps, Rules 0..4 = the Apps
    # region order: Beers burned off / Downhills Counter / Real time EPOC / Sleep Monitor /
    # Current incline). Its graph display 1 is (0x34, 0x6E, 5) and graph display 3 is
    # (0x6B, 0x70, 5); Andre independently named those two screens as the Downhills Counter
    # (slot 1) and Sleep Monitor (slot 3) apps, so 0x6B = slot 3 and the companions are
    # 0x6D + slot (1 -> 0x6E, 3 -> 0x70). The unseen members 0x6D/0x6F/0x71 follow the same
    # rule but have not been observed on hardware yet.
    # 0x002C read straight off SuuntoLink's own UI: André opened Running2, whose display 0
    # bottom row we decode as Speed / Distance / Training Effect / 0x002C / app slot 1, and
    # SuuntoLink names that fourth value "Vertical speed" (2026-08-11). The same screen also
    # confirmed slot 1 is "Beers burned off", which is RuleIdx 0 in the Apps region - so the
    # rule-engine slot ordering above is right as well.
    0x002C: "FT_VERTICAL_SPEED",
    # All read off SuuntoLink by André, 2026-08-11 - observations, not inference.
    #
    # 0x21/0x22 come from his Cycling display 2 bottom row, which the watch stores as
    # [0x21, 0x22, 0x2C, 0x46] and SuuntoLink lists as [Ascent, Descent, Vertical speed,
    # Current activity duration]. The last two are independently known ids, so they act as
    # controls: both land exactly where the stored order puts them, which is what makes the
    # first two trustworthy rather than merely plausible. (That control matters - the same
    # question asked of a Pool swimming row showed SuuntoLink listing it in a DIFFERENT
    # order from storage, so position alone proves nothing without anchors.)
    0x0021: "FT_ASCENT", 0x0022: "FT_DESCENT",
    # Sole unknown on its row, so no ordering ambiguity is possible.
    0x002D: "FT_LAP_NUMBER",
    # His Running display 5 is a graph, and a graph stores (value, that value's graph,
    # bottom row) - one pick fills both fields. He picked Running performance.
    0x00AD: "FT_RUNNING_PERFORMANCE", 0x00B0: "FT_RUNNING_PERFORMANCE_GRAPH",
    # The power fields, resolved by reading SuuntoLink (André, 2026-08-11) rather than by the
    # interleave that was tried and withdrawn earlier the same day. His Cycling display 4 is
    # stored as top 0x4A / centre 0x0043 / bottom [0x004B, 0x4C, 0x004D] and SuuntoLink shows
    # it as average / Power / [Power 3s, Power 10s, Power 30s]. 0x4A and 0x4C are ids verified
    # against SuuntoLink's own binary and both land where storage puts them, anchoring the
    # other three.
    #
    # Worth recording what the withdrawn inference got right and wrong, because the shape
    # recurs: the 3s/30s guesses were correct, and plain Power was not. The pattern was real
    # but it did not extend as far as it appeared to - 0x0043 sits outside the run entirely,
    # and 0x0049 turned out to be Current activity avg pace, not a power field at all.
    0x0043: "FT_BIKE_POWER", 0x004B: "FT_BIKE_POWER_3S", 0x004D: "FT_BIKE_POWER_30S",
    # 0x002E was the sole value on Run a route display 4's bottom row - no ordering ambiguity
    # possible - and André read it as Lap time. 0x0049 then falls out by elimination: his
    # Running display 3 bottom stores [0x47, 0x0049, 0x002E] and SuuntoLink lists that row as
    # Lap time / Current activity distance / Current activity avg pace, so with 0x47 and
    # 0x002E both known, only one name is left for 0x0049.
    0x002E: "FT_LAP_TIME", 0x0049: "FT_SPORT_LAP_AVG_PACE",
    # Sole values on the top and centre rows of his Run a route display 4.
    0x002F: "FT_LAP_DISTANCE", 0x0030: "FT_LAP_AVG_PACE",
    # Sole value on Pool swimming display 1's top row AND display 2's top row; André read
    # both as Distance.
    0x0053: "FT_SWIM_TOTAL_DISTANCE",
    0x006B: "FT_RULE_ENGINE_3", 0x006C: "FT_RULE_ENGINE_4",
    0x006E: "FT_RULE_ENGINE_1_GRAPH", 0x0070: "FT_RULE_ENGINE_3_GRAPH",
    # 0x57 CONFIRMED 2026-08-11 by differential test, after a day spent uncertain. Reading a
    # multi-value row off SuuntoLink cannot settle these, because it demonstrably does not
    # list such a row in stored order - so instead of reading, we CHANGED one and watched.
    # André's Pool swimming display 2 bottom row stored [0x58, 0x0059, 0x57] against a UI
    # showing {average swim pace, average stroke rate, average SWOLF}; he unticked average
    # SWOLF in SuuntoLink and 0x0059 was the id that vanished. That names 0x0059 outright and
    # leaves only average stroke rate for 0x57, confirming the 2026-08-10 guess.
    #
    # This is the technique to reach for whenever a row's names are known but their order is
    # not: remove one value and see which id disappears. It needs no ordering assumption at
    # all.
    0x0047: "FT_SPORT_LAP_DISTANCE", 0x0057: "FT_SWIM_AVG_STROKE_RATE",
    0x0059: "FT_SWIM_AVG_SWOLF",
    # The last two unknowns in the table, settled the same way (André, 2026-08-11): he
    # unticked "interval stroke rate" on his Pool swimming display 1 bottom row and 0x0067 is
    # the id that vanished, leaving interval avg SWOLF for 0x0069. Note this is the OPPOSITE
    # of the assignment a positional reading had suggested - worth testing rather than
    # trusting.
    0x0067: "FT_SWIM_INT_STROKE_RATE", 0x0069: "FT_SWIM_INT_SWOLF",
    #
    # That same save also resolved why positional readings had been unreliable. The row was
    # stored as [int pace, 0x69, 0x67, rest time, style] but SuuntoLink displayed it as
    # [rest time, int pace, ...]; after the save it is stored as [rest time, int pace, 0x69,
    # style] - i.e. SuuntoLink WRITES a multi-value row in the order it shows it. The two
    # never disagreed in principle; that row simply had not been re-saved since an older
    # edit, so the stored order was stale. Reading a row off the UI by position is therefore
    # sound for any row SuuntoLink has written recently - but the differential test above
    # (remove one value, see which id disappears) needs no such assumption and is still the
    # method to reach for.
    0x005A: "FT_SWIM_LAP_DISTANCE", 0x005C: "FT_SWIM_LAP_RATE", 0x005E: "FT_SWIM_LAP_SWOLF",
    0x0060: "FT_SWIM_POOL_STROKES", 0x0062: "FT_SWIM_POOL_PACE", 0x0064: "FT_SWIM_INT_TIME",
    0x0066: "FT_SWIM_INT_STROKES", 0x0068: "FT_SWIM_INT_PACE", 0x006A: "FT_SWIM_REST_TIME",
    0x0100: "PID_RUNNER_GPS_TEMPLATE_1", 0x0101: "PID_RUNNER_GPS_TEMPLATE_1G_GRAPH",
    0x0102: "PID_RUNNER_GPS_TEMPLATE_2", 0x0103: "PID_RUNNER_GPS_TEMPLATE_3",
    0x0104: "PID_RUNNER_GPS_TEMPLATE_4", 0x0105: "PID_RUNNER_GPS_TEMPLATE_5",
    0x0106: "PID_RUNNER_GPS_TEMPLATE_6", 0x0107: "PID_RUNNER_GPS_TEMPLATE_7_WAYPOINT",
    0x0108: "PID_RUNNER_GPS_TEMPLATE_8_LOCATION",
    0x0109: "PID_RUNNER_GPS_TEMPLATE_9_WAYPOINT_NAME",
    0x0110: "PID_RUNNER_GPS_TEMPLATE_10_INPUT",
    0x0111: "PID_RUNNER_GPS_TEMPLATE_11_COMPASS_NAVIGATE",
    0x0112: "PID_RUNNER_GPS_TEMPLATE_12_SETTINGS",
    0x0113: "PID_RUNNER_GPS_TEMPLATE_13_TWOWAY_CHOICE",
    0x0114: "PID_RUNNER_GPS_TEMPLATE_14_ONEWAY_CHOICE",
    0x0115: "PID_RUNNER_GPS_TEMPLATE_15_ADJUST", 0x0116: "PID_RUNNER_GPS_TEMPLATE_16_LOADING",
    0x0117: "PID_RUNNER_GPS_TEMPLATE_17_POPUP", 0x0118: "PID_RUNNER_GPS_TEMPLATE_18_MEMORY",
    0x0119: "PID_RUNNER_GPS_TEMPLATE_19_MODE_TITLE",
    0x0120: "PID_RUNNER_GPS_TEMPLATE_20_CALIBRATION",
    0x0121: "PID_RUNNER_GPS_TEMPLATE_21_UTMMGRS_LOCATION",
    0x0122: "PID_RUNNER_GPS_TEMPLATE_22_NAVIGATION",
    0x0123: "PID_RUNNER_GPS_TEMPLATE_11_MILS_COMPASS_NAVIGATE",
    0x0150: "PID_RUNNER_GPS_TEMPLATE_50_MAP_DRAW", 0x0200: "PID_RUNNER_GPS_TEMPLATE_100_DEBUG",
}

# Real, 2026-08-09 ("on custom sports it shows the variable names, can't we have the normal
# names?") - a human-readable label per real FIELD_TYPES entry, for the desktop/Android
# field-type pickers. Curated by hand for every FT_* entry (real sports-watch terminology,
# not a mechanical guess) - two are hardware-confirmed real names from this project's own
# live testing, not just plausible-sounding ones: FT_VELOCITY really does show as "Speed"
# (docs/custom_modes_andre.md, Walk's own wrong field read back as this, matching what André
# reported seeing), and FT_HEART_RATE_CURR really does show as plain "Heart Rate" (same
# doc, the confirmed HR-display fix). FT_RULE_ENGINE_0/1/2's "Suunto App Slot N" wording
# matches this project's own real understanding of what RULEIDX selects (decode_rule()'s
# own docstring). The PID_RUNNER_GPS_TEMPLATE_* screen-template entries are deliberately
# NOT given invented descriptions here (this project has never confirmed what most of those
# numbers look like on a real screen, only that TEMPLATE_4 is the ordinary repeating data
# screen - see docs/custom_modes_andre.md) - field_type_label()'s own fallback just cleans up
# the real enum name's own formatting for those instead of guessing new meaning.
# Human labels. Where a field is one SuuntoLink also offers as a display row, the wording is
# ITS wording, taken from assets/sportmode_rows.json (generated from its own module and
# translations) rather than invented here - 23 of these were relabelled on 2026-08-11 for
# exactly that reason. The rule behind it is André's: SuuntoLink shows what the watch shows,
# so a user reading our screen and his watch should see the same words. Run
# `tools/row_bridge.py` after changing this table if you want to check the two still agree.
#
# FT_SHORTCUT is deliberately NOT SuuntoLink's "Empty": 0x00 doubles as the marker for a
# multi-value row, and _field_to_json() distinguishes the two by whether shortcuts are
# present, reporting "Empty" only when there are none.
FIELD_TYPE_LABELS = {
    "FT_SHORTCUT": "Shortcut", "FT_TIME": "Day time", "FT_TIME_SEC": "Time (with seconds)",
    "FT_DATE": "Date", "FT_WEEKDAY": "Weekday", "FT_TIMER": "Chrono", "FT_ALTI": "Altitude",
    "FT_BARO": "Sea level air pressure", "FT_COMPASS": "Compass Heading",
    "FT_HEART_RATE_AVG": "Avg heart rate", "FT_DISTANCE": "Distance",
    "FT_VELOCITY": "Speed", "FT_AVGSPEED": "Average speed", "FT_TEMPERATURE": "Temperature",
    "FT_BARO_GRAPH": "Barograph", "FT_ALTI_GRAPH": "Altitude Graph",
    "FT_COMPASSCARDINAL": "Compass Direction", "FT_TOTAL_CALORIES": "Calories",
    "FT_REF_ALTI": "Reference Altitude", "FT_ENERGY_SYMBOL": "Energy Level",
    "FT_HEART_RATE_PEAK": "Heart Rate (peak)", "FT_HEART_RATE_CURR": "Heart rate",
    "FT_HEART_RATE_GRAPH": "Heart Rate Graph", "FT_CADENCE": "Cadence",
    "FT_NAVI_ARROW": "Navigation Arrow", "FT_NAVI_DISTANCE": "Navigation Distance",
    "FT_NAVI_DIRECTION": "Navigation Direction", "FT_DUAL_TIME": "Dual time",
    "FT_PACE": "Pace", "FT_AVG_PACE": "Average pace", "FT_RECOVERY_TIME": "Recovery Time",
    "FT_TE": "Peak Training Effect", "FT_EXERCISE_ALTI_GRAPH": "Altitude Graph (exercise)",
    "FT_RULE_ENGINE_0": "Suunto App Slot 1", "FT_RULE_ENGINE_1": "Suunto App Slot 2",
    "FT_RULE_ENGINE_2": "Suunto App Slot 3",
    "FT_VERTICAL_SPEED": "Vertical speed", "FT_ASCENT": "Ascent", "FT_DESCENT": "Descent",
    "FT_LAP_NUMBER": "Lap number", "FT_LAP_TIME": "Lap time",
    "FT_LAP_DISTANCE": "Lap distance", "FT_LAP_AVG_PACE": "Lap avg pace",
    "FT_BIKE_POWER": "Power", "FT_BIKE_POWER_3S": "Power 3s",
    "FT_BIKE_POWER_30S": "Power 30s",
    "FT_SPORT_LAP_AVG_PACE": "Current activity avg pace",
    "FT_SWIM_TOTAL_DISTANCE": "Distance",
    "FT_RUNNING_PERFORMANCE": "Running performance",
    "FT_RUNNING_PERFORMANCE_GRAPH": "Running performance (graph)",
    "FT_RULE_ENGINE_3": "Suunto App Slot 4", "FT_RULE_ENGINE_4": "Suunto App Slot 5",
    "FT_RULE_ENGINE_1_GRAPH": "Suunto App Slot 2 (graph)",
    "FT_RULE_ENGINE_3_GRAPH": "Suunto App Slot 4 (graph)",
    "FT_STOPWATCH_ABC": "Stopwatch",
    "FT_SW_ABC_LAP_TIME": "Stopwatch Lap Time", "FT_SW_ABC_LAP": "Stopwatch Lap",
    "FT_SW_TIME": "Stopwatch Time", "FT_DEVELOPER_TITLE": "Developer Title",
    "FT_TEXT_ADJUST": "Text Adjust", "FT_CALIBRATED_DISTANCE": "Distance (calibrated)",
    "FT_INTERVAL_VALUE": "Interval Value", "FT_INTERVAL_TITLE": "Interval Title",
    "FT_SPLIT_LAP_DISTANCE": "Split/Lap Distance", "FT_SWIM_STYLE_TITLE": "Swim Style Title",
    "FT_DRILL_TITLE": "Drill Title", "FT_BATTERY_CHARGE": "Battery charge",
    # Read off SuuntoLink by André, 2026-08-11. Our own wording ("Lap Stopwatch", "Lap
    # Average Speed") was a guess from the FT_ symbol and is actively misleading - these are
    # the CURRENT-ACTIVITY fields of a multisport mode, not lap fields. The ids themselves
    # were never in doubt (both verified against SuuntoLink's own binary); only our labels
    # were wrong, which is exactly the kind of error a UI shows the user every day.
    "FT_SPORT_LAP_STOPWATCH": "Current activity duration",
    "FT_SPORT_LAP_AVGSPEED": "Current activity avg speed",
    "FT_BIKE_POWER_AVG": "Average power", "FT_BIKE_POWER_10S": "Power 10s",
    "FT_BIKE_POWER_LAP": "Lap power", "FT_BIKE_POWER_LAP_MAX": "Lap maximum power",
    "FT_SWIM_AVG_SWOLF": "Average SWOLF",
    "FT_SWIM_INT_STROKE_RATE": "Interval stroke rate",
    "FT_SWIM_INT_SWOLF": "Interval avg SWOLF",
    "FT_SWIM_STYLE": "Previous pool length style",   # SuuntoLink's own wording, André 2026-08-11 "FT_SWIM_STROKES": "Swim Strokes", "FT_SWIM_PACE": "Swim Pace",
    "FT_SWIM_AVG_PACE": "Average swim pace",
    "FT_SWIM_AVG_STROKE_RATE": "Average stroke rate",
    # SuuntoLink groups the FT_SPORT_LAP_* family under "Multisport" and words them
    # "Current activity ..." - matched here so our UI reads the same as the app the
    # owner already knows.
    "FT_SPORT_LAP_DISTANCE": "Current activity distance", "FT_SWIM_LAP_DISTANCE": "Lap distance",
    "FT_SWIM_LAP_RATE": "Lap stroke rate", "FT_SWIM_LAP_SWOLF": "Lap avg SWOLF",
    "FT_SWIM_POOL_STROKES": "Pool Swim Strokes", "FT_SWIM_POOL_PACE": "Pool Swim Pace",
    "FT_SWIM_INT_TIME": "Interval duration", "FT_SWIM_INT_STROKES": "Interval stroke count",
    "FT_SWIM_INT_PACE": "Interval pace", "FT_SWIM_REST_TIME": "Rest time",
    "MT_NONE": "(none)",
}


def used_extent(data):
    """The number of real (used) bytes at the start of a CustomModes region image: the
    DEVICE_CUSTOM root BXML tag plus its 4-byte header. Everything after this is slack/0xFF
    padding within the fixed CUSTOM_MODES_REGION_SIZE region.

    This is the span SuuntoLink actually writes AND hashes (confirmed byte-exact against all 4
    real app-install captures in assets/ambit3 pcap/v2/, and against the watch's own reported
    0x0b21 CustomModes hash - docs/training_program_andre.md Finding 28). Writers must write only
    data[:used_extent(data)] so the closing hash covers exactly what the firmware re-hashes on
    a cold boot; writing the full padded region instead produces a hash the watch rejects with
    'err:62' on all sport modes after a restart."""
    root_id, root_len = struct.unpack_from("<HH", data, 0)
    if root_id != DEVICE_CUSTOM:
        raise ValueError(f"not a CustomModes image: root tag 0x{root_id:x} != DEVICE_CUSTOM")
    extent = 4 + root_len
    if extent > len(data):
        raise ValueError(f"declared extent {extent} exceeds image length {len(data)}")
    return extent


def check_field_type_shortcut_invariant(data):
    """A display field's Type is 0 if and only if it has at least one DISP_FIELD_SHORTCUT -
    zero exceptions across 31,976 real fields checked in every capture this project has
    (2026-08-12, docs/training_program_andre.md Finding 53's "connect to Moveslink" recovery). A
    field with a nonzero Type AND shortcuts, or Type=0 with none, never occurs on real
    hardware and the firmware rejects it - this was the real root cause of a real-hardware
    "connect to Moveslink" error, hit for the first time when this project's installer added
    a mode's FIRST-EVER shortcut to a field without also zeroing its old Type.

    Any writer touching CustomModes should call this on its own output before sending -
    raises on the first violation rather than letting a bad region reach the watch. Returns
    None (no violation) or (mode_index, mode_name, display_index, field_index, field_dict)."""
    decoded = decode(data)
    for mi, mode in enumerate(decoded.get("exercise_modes", [])):
        for di, disp in enumerate(mode.get("Displays", [])):
            for fi, f in enumerate(disp.get("Fields", disp.get("fields", []))):
                typ, sc = f.get("Type"), f.get("Shortcuts", [])
                if bool(sc) != (typ == 0):
                    return (mi, mode.get("Name"), di, fi, f)
    return None


# Value enumerations for display fields whose reading is a code rather than a measurement.
# Source: Suunto's own "Suunto Apps Developer Manual" (Apr 2, 2015),
# assets/manuals/SuuntoAppZoneDeveloperManual.pdf, the WATCH VARIABLES tables on pp.24-29.
# That manual documents the App SCRIPTING namespace (SUUNTO_SWIMMING_PREVIOUS_POOL_LENGTH_
# STYLE and friends), which is a different namespace from the display-field ids here - it
# never prints a field id. What it does give, and what is reproduced below, is the meaning of
# the VALUES, which is the same quantity either way.
FIELD_VALUE_ENUMS = {
    # SUUNTO_SWIMMING_PREVIOUS_POOL_LENGTH_STYLE, manual p.28.
    "FT_SWIM_STYLE": {0: "Other", 1: "Butterfly", 2: "Backstroke", 3: "Breaststroke",
                      4: "Freestyle", 5: "Drill"},
}


def field_value_label(name, value):
    """Human-readable reading for a coded field, or None if this field is a plain number.

    Only fields in FIELD_VALUE_ENUMS have one - everything else is a measurement whose value
    speaks for itself and must not be relabelled."""
    return FIELD_VALUE_ENUMS.get(name, {}).get(value)


def field_type_label(name):
    """Human-readable label for a real FIELD_TYPES name - the curated dict above for every
    FT_*/MT_NONE entry, or a real-but-honest fallback for the PID_RUNNER_GPS_TEMPLATE_*
    screen-template entries this project has never confirmed the visual meaning of: strips
    the common prefix and reformats the enum's own real suffix, without inventing new
    meaning. `name` not in FIELD_TYPES at all (shouldn't happen, defensive) falls back to
    itself unchanged."""
    if name in FIELD_TYPE_LABELS:
        return FIELD_TYPE_LABELS[name]
    if name.startswith("PID_RUNNER_GPS_TEMPLATE_"):
        rest = name[len("PID_RUNNER_GPS_TEMPLATE_"):]
        num, _, words = rest.partition("_")
        pretty = words.replace("_", " ").title()
        return f"Screen {num}" + (f" - {pretty}" if pretty else "")
    return name

# EXERCISE_MODES_SETTING_NAME_LEN64's content: name string (64 B on UTF8-capable devices,
# see build_settings_name_field() for the older 16/24-byte variants) then a fixed sequence of
# uint16 fields, then one full interval-timer slot, then five short repeats. Verified against
# every one of the 10 EXERCISE_MODES_MODE entries on the reference watch: sums to exactly 138
# bytes, matching every SETTING_NAME_LEN64 tag length observed live, 2026-08-05.
SETTING_FIELDS = [
    ("ActivityID", "H"), ("CustomModeIdLow", "H"), ("CustomModeIdHigh", "H"),
    ("UseHw", "H"), ("AltiBaroMode", "H"), ("GpsPowerMode", "H"),
    ("RecordingInterval", "H"), ("Autolap", "H"), ("HrHigh", "H"), ("HrLow", "H"),
    ("HrLimitsUse", "H"), ("AutoStart", "H"), ("AutoPause", "H"), ("AutoScrolling", "H"),
    ("IntTimerFlags", "H"), ("IntTimerCount", "H"),
]
INTERVAL_SLOT_FULL = [("Flags", "B"), ("Type", "B"), ("MaxLimit", "H"), ("MinLimit", "H"),
                       ("Padding", "H"), ("Len", "I")]
INTERVAL_SLOT_SHORT = [("Flags", "B"), ("Type", "B"), ("MaxLimit", "H"), ("MinLimit", "H")]
INTERVAL_SLOT_REPEATS = 5

# The *last* of the five short interval slots (index 5 of 6, absolute offset 132-137) is
# never a real interval-timer slot - real per-mode "advanced settings" are packed into its
# three sub-fields instead, confirmed byte-exact 2026-08-08 against real SuuntoLink captures
# *and* independently against SuuntoLink's own JS source enums (assets/WIndows apps/
# suuntolink_roaming/app-4.1.15/resources/app/ambit/{settings,sport_mode}.js) - see
# docs/custom_modes_andre.md's "2026-08-08" section for the full derivation. Kept as a
# reinterpretation of IntervalSlots[5] rather than a new struct field, so a raw round-trip
# through encode/decode still works unchanged.
BACKLIGHT_MODE_NAMES = {0: "NORMAL", 1: "OFF", 2: "NIGHT", 3: "TOGGLE", 255: "DEFAULT"}
DISPLAY_MODE_NAMES = {0: "LIGHT", 1: "DARK", 255: "DEFAULT"}
QUICK_NAVIGATION_NAMES = {0: "OFF", 1: "POI", 2: "ROUTE", 255: "DEFAULT"}


def read_tag(data, offset):
    """A single BXml tag header: [u16 LE tag_id][u16 LE length], content follows.
    Matches Communist::Bluebird::BXmlConverter::parseBinaryTag exactly (4 bytes, no more)."""
    if offset + 4 > len(data):
        return None
    tag_id, length = struct.unpack_from("<HH", data, offset)
    return tag_id, length


def decode_settings(data, offset, length):
    """Decodes one EXERCISE_MODES_SETTING_NAME_LEN64 content block (name + fixed fields).

    CORRECTED 2026-08-22: name decode was iso-8859-15 - live-caught on real hardware,
    André's French Ambit3 Sport: "Entraîn. salle"/"Course itinér." came back as visible
    mojibake ("EntraÃ®n. salle") under iso-8859-15, the classic UTF-8-misread-as-Latin-1
    signature. The watch sends UTF-8 - see ambit_format.py's encode_name() for the same fix
    on the write side."""
    name = data[offset:offset + 64].rstrip(b"\0").decode("utf-8", "replace")
    cursor = offset + 64
    out = {"Name": name}
    for field, fmt in SETTING_FIELDS:
        out[field] = struct.unpack_from("<" + fmt, data, cursor)[0]
        cursor += struct.calcsize(fmt)
    out["CustomModeID"] = out.pop("CustomModeIdLow") | (out.pop("CustomModeIdHigh") << 16)
    intervals = []
    for i in range(1 + INTERVAL_SLOT_REPEATS):
        slot_fields = INTERVAL_SLOT_FULL if i == 0 else INTERVAL_SLOT_SHORT
        slot = {}
        for field, fmt in slot_fields:
            slot[field] = struct.unpack_from("<" + fmt, data, cursor)[0]
            cursor += struct.calcsize(fmt)
        intervals.append(slot)
    out["IntervalSlots"] = intervals
    advanced = intervals[5]
    out["BacklightMode"] = advanced["Flags"]
    out["BacklightModeName"] = BACKLIGHT_MODE_NAMES.get(advanced["Flags"], f"0x{advanced['Flags']:02x}")
    out["DisplayMode"] = advanced["MaxLimit"]
    out["DisplayModeName"] = DISPLAY_MODE_NAMES.get(advanced["MaxLimit"], f"0x{advanced['MaxLimit']:02x}")
    out["QuickNavigation"] = advanced["MinLimit"]
    out["QuickNavigationName"] = QUICK_NAVIGATION_NAMES.get(advanced["MinLimit"], f"0x{advanced['MinLimit']:02x}")
    consumed = cursor - offset
    if consumed != length:
        out["_warning"] = f"consumed {consumed} of {length} declared bytes"
    return out


def decode_disp_field(data, offset, length):
    """One EXERCISE_MODES_DISP_FIELD: a DISP_FIELD_SETTING leaf (4 bytes: [u16 Index][u16
    Type], both addBinaryData type=1/uint16 - Communist::Bluebird::BXmlConverter::
    convertExerciseModesDisplayFieldBinaryToTree, byte-verified 2026-08-05) followed by zero
    or more DISP_FIELD_SHORTCUT leaves (2 bytes each: a single uint16). "Index" is the field
    catalogue value - resolved against FIELD_TYPES, and confirmed to match: every Index seen
    in the live dump (0,1,2,3,9,24,25...) is a valid FT_* entry. "Type" and the shortcut
    values are confirmed-present numeric fields whose exact semantics (a format/subtype
    selector, and alternate cycle-through values shown when a button scrolls this field) are
    not pinned down further - plausibly indices into the ~200 SUUNTO_* watch variables listed
    in SuuntoAppZoneDeveloperManual.pdf, but that mapping itself isn't in any decompiled asset
    this project has found, so it's reported here as a raw number rather than guessed."""
    end = offset + length
    cursor = offset
    field = {"Index": None, "IndexName": None, "Type": None, "Shortcuts": []}
    while cursor < end:
        tag = read_tag(data, cursor)
        if tag is None:
            break
        tag_id, tag_len = tag
        content = cursor + 4
        if tag_id == EXERCISE_MODES_DISP_FIELD_SETTING:
            idx, typ = struct.unpack_from("<HH", data, content)
            field["Index"] = idx
            field["IndexName"] = FIELD_TYPES.get(idx, f"0x{idx:04x}")
            field["Type"] = typ
        elif tag_id == EXERCISE_MODES_DISP_FIELD_SHORTCUT:
            field["Shortcuts"].append(struct.unpack_from("<H", data, content)[0])
        cursor = content + tag_len
    return field


def decode_display(data, offset, length):
    """One EXERCISE_MODES_DISPLAY (a single watch screen): a DISP_SETTING leaf (4 bytes:
    [u16 Template][u16 Type], both uint16 - convertExerciseModesDisplaySettingBinaryToTree)
    followed by 1-3 DISP_FIELD entries (the value slots shown on that screen). Template
    resolves against the same FIELD_TYPES dict (PID_RUNNER_GPS_TEMPLATE_* range) - confirmed
    to match every screen template seen live (T4, T5, T6, T11_COMPASS_NAVIGATE,
    T22_NAVIGATION, T1G_GRAPH, T50_MAP_DRAW, ...). Byte-verified against
    /tmp/dump_CustomModes.bin, 2026-08-05."""
    end = offset + length
    cursor = offset
    display = {"Template": None, "TemplateName": None, "Type": None, "Fields": []}
    while cursor < end:
        tag = read_tag(data, cursor)
        if tag is None:
            break
        tag_id, tag_len = tag
        content = cursor + 4
        if tag_id == EXERCISE_MODES_DISP_SETTING:
            tpl, typ = struct.unpack_from("<HH", data, content)
            display["Template"] = tpl
            display["TemplateName"] = FIELD_TYPES.get(tpl, f"0x{tpl:04x}")
            display["Type"] = typ
        elif tag_id == EXERCISE_MODES_DISP_FIELD:
            display["Fields"].append(decode_disp_field(data, content, tag_len))
        cursor = content + tag_len
    return display


def decode_rule(data, offset, length):
    """One EXERCISE_MODES_RULE: NOT further BXml sub-tags (RULEIDX/USERULE/LOGRULE have no
    entry in BXmlTagMapping or BXmlIdMapping - confirmed absent from both dictionaries in
    libkomposti-ng.so.c). Instead it's a flat 6-byte struct, three consecutive uint16 fields
    written back-to-back by three addBinaryData(type=1) calls, in this exact order -
    Communist::Bluebird::BXmlConverter::convertExerciseModesRuleBinaryToTree:
        RULEIDX (which rule-engine display slot, i.e. FT_RULE_ENGINE_0/1/2, this refers to)
        USERULE (whether a Suunto App is assigned/enabled for that slot)
        LOGRULE (whether that App's result gets logged as part of the recorded Move)
    This is the link between the separate "Suunto Apps" subsystem (its own flash region, own
    binary converter - see docs/custom_modes_andre.md) and a specific exercise mode: an app itself
    lives in the Apps region, but *which* app-slot an exercise mode uses, and whether it's
    logged, is recorded here in CustomModes.
    NOT byte-verified: this reference watch has no apps installed (its Apps flash region
    is entirely unwritten, 0xFF), and correspondingly none of its 10 EXERCISE_MODES_MODE
    entries contain an EXERCISE_MODES_RULES tag at all - there is nothing to decode on this
    watch. This function is derived from source alone and will raise if fed unexpected bytes
    rather than silently guess."""
    rule_idx, use_rule, log_rule = struct.unpack_from("<HHH", data, offset)
    out = {"RuleIdx": rule_idx, "UseRule": bool(use_rule), "LogRule": bool(log_rule)}
    if length != 6:
        out["_warning"] = f"expected 6 bytes, tag declared {length}"
    return out


def decode_exercise_mode(data, offset, length):
    """One EXERCISE_MODES_MODE entry: SETTING_NAME_LEN64 (settings), DISPLAYS (screens) and
    RULES (Suunto App rule-engine slot assignments, usually absent) sub-tags, all decoded."""
    end = offset + length
    cursor = offset
    mode = {"Displays": [], "Rules": [], "AppMeta": None, "_raw_children": []}
    while cursor < end:
        tag = read_tag(data, cursor)
        if tag is None:
            break
        tag_id, tag_len = tag
        content = cursor + 4
        if tag_id == EXERCISE_MODES_SETTING_NAME_LEN64:
            mode["Settings"] = decode_settings(data, content, tag_len)
        elif tag_id == EXERCISE_MODES_APP_META and tag_len == 8:
            t1, t2 = struct.unpack_from("<II", data, content)
            mode["AppMeta"] = {"Timestamp1": t1, "Timestamp2": t2}
        elif tag_id == EXERCISE_MODES_DISPLAYS:
            sub_cursor = content
            sub_end = content + tag_len
            while sub_cursor < sub_end:
                sub_tag = read_tag(data, sub_cursor)
                if sub_tag is None:
                    break
                sub_id, sub_len = sub_tag
                sub_content = sub_cursor + 4
                if sub_id == EXERCISE_MODES_DISPLAY:
                    mode["Displays"].append(decode_display(data, sub_content, sub_len))
                sub_cursor = sub_content + sub_len
        elif tag_id == EXERCISE_MODES_RULES:
            sub_cursor = content
            sub_end = content + tag_len
            while sub_cursor < sub_end:
                sub_tag = read_tag(data, sub_cursor)
                if sub_tag is None:
                    break
                sub_id, sub_len = sub_tag
                sub_content = sub_cursor + 4
                if sub_id == EXERCISE_MODES_RULE:
                    mode["Rules"].append(decode_rule(data, sub_content, sub_len))
                sub_cursor = sub_content + sub_len
        else:
            mode["_raw_children"].append(
                (BXML_TAGS.get(tag_id, f"0x{tag_id:03x}"), tag_len))
        cursor = content + tag_len
    return mode


def decode_sport_mode_slot(data, offset, length):
    """One SPORT_MODE (multisport) slot: a 64-byte name, an ActivityID, and one *or more*
    SPORT_MODE_EXERCISE tags. A single-sport slot has exactly one Exercise tag, pointing at
    its own EXERCISE_MODES_MODE flash-storage index. A real multisport combo (e.g. the
    default "Triathlon") has one Exercise tag per leg, in order - confirmed on hardware
    2026-08-05: Triathlon decodes to five tags, 0/1/2/1/3, i.e. Openwater swim -> Transition
    -> Cycling -> Transition -> Running, the standard swim/T1/bike/T2/run structure. Earlier
    versions of this function kept only the last Exercise tag, silently discarding the
    sequence for any multisport entry - fixed by collecting all of them."""
    end = offset + length
    cursor = offset
    slot = {"Name": "", "ActivityID": None, "Exercises": [], "Order": None, "AppMeta": None}
    while cursor < end:
        tag = read_tag(data, cursor)
        if tag is None:
            break
        tag_id, tag_len = tag
        content = cursor + 4
        if tag_id == SPORT_MODE_SETTING_NAME_LEN64:
            # CORRECTED 2026-08-22: same iso-8859-15 -> utf-8 fix as decode_settings() above.
            slot["Name"] = data[content:content + tag_len].rstrip(b"\0").decode(
                "utf-8", "replace")
        elif tag_id == SPORT_MODE_ACTIVITY_ID:
            slot["ActivityID"] = struct.unpack_from("<H", data, content)[0]
        elif tag_id == SPORT_MODE_EXERCISE:
            slot["Exercises"].append(struct.unpack_from("<H", data, content)[0])
        elif tag_id == SPORT_MODE_ORDER and tag_len == 4:
            slot["Order"] = struct.unpack_from("<I", data, content)[0]
        elif tag_id == SPORT_MODE_APP_META and tag_len == 4:
            slot["AppMeta"] = struct.unpack_from("<I", data, content)[0]
        cursor = content + tag_len
    return slot


def decode(data):
    """Decodes a raw CustomModes flash dump (12288 bytes). Returns a dict with the
    format-type marker, every configured exercise mode, and the SPORT_MODES multisport
    slots (populated ones only, by default)."""
    root = read_tag(data, 0)
    if root is None or root[0] != DEVICE_CUSTOM:
        raise ValueError(f"expected DEVICE_CUSTOM at offset 0, got {root}")
    _, root_len = root
    cursor = 4
    end = 4 + root_len
    result = {"exercise_modes": [], "sport_modes": []}
    while cursor < end:
        tag = read_tag(data, cursor)
        if tag is None:
            break
        tag_id, length = tag
        content = cursor + 4

        if tag_id == EXERCISE_MODES:
            sub_end = content + length
            sub_cursor = content
            while sub_cursor < sub_end:
                sub_tag = read_tag(data, sub_cursor)
                if sub_tag is None:
                    break
                sub_id, sub_len = sub_tag
                sub_content = sub_cursor + 4
                if sub_id == EXERCISE_MODES_TYPE:
                    result["format_type"] = struct.unpack_from("<H", data, sub_content)[0]
                elif sub_id == EXERCISE_MODES_MODE:
                    result["exercise_modes"].append(
                        decode_exercise_mode(data, sub_content, sub_len))
                sub_cursor = sub_content + sub_len

        elif tag_id == SPORT_MODES:
            sub_end = content + length
            sub_cursor = content
            while sub_cursor < sub_end:
                sub_tag = read_tag(data, sub_cursor)
                if sub_tag is None:
                    break
                sub_id, sub_len = sub_tag
                sub_content = sub_cursor + 4
                if sub_id == SPORT_MODE:
                    result["sport_modes"].append(
                        decode_sport_mode_slot(data, sub_content, sub_len))
                sub_cursor = sub_content + sub_len

        cursor = content + length
    return result


# Real, 2026-08-09 ("I see screen XX, but afaik suunto ambit can only have a max number
# of screens that is way lower than this, please identify the correct screen number that
# the user would see"). Confirmed live against this reference watch's own real Displays
# data (custom_modes.py --json), cross-checked against SuuntoLink's own real "Displays"
# screenshot counts (assets/ambit3 pcap/v2/screens sports modes/sportsmodes.JPG - "Cycling
# 8 screens", "Walk 1 screens", etc.): every one of the watch's 7 real sport modes has a
# real, fixed run of *built-in* screens (Compass, Navigation, Map, an Interval Timer
# summary, and one still-unconfirmed 0x0127) appended after the user's own configurable
# ones - "Screen 9/10/11..." was these built-in screens, not real user screens, which is
# why the numbering looked wrong. Two real shapes confirmed so far:
#   - GPS-capable activities (Cycling/Running/Run a route/Trekking/Indoor training/Walk -
#     6/6 tested): a byte-identical 7-entry tail (Compass, Compass-in-mils, Navigation, a
#     2nd Graph, Map, Interval Timer, 0x0127). Excluding it and renumbering from 1 matches
#     SuuntoLink's own reported count exactly for every one of the 6.
#   - Pool swimming (the one non-GPS activity tested): a shorter, real 3-entry tail
#     (Interval Timer, an unconfirmed swim-specific 0x0124, then the same 0x0127) - also
#     matches its own real reported count (3) exactly once excluded.
# The very last entry being the unconfirmed 0x0127 is the one invariant confirmed across
# all 7 tested modes regardless of activity type - a real, if not yet fully explained,
# anchor. A mode whose own tail doesn't match either known shape falls back to 0 excluded
# (every entry shown, numbered from 1) rather than guessing at a boundary with no real
# evidence for it.
_GPS_SYSTEM_TAIL = (
    "PID_RUNNER_GPS_TEMPLATE_11_COMPASS_NAVIGATE",
    "PID_RUNNER_GPS_TEMPLATE_11_MILS_COMPASS_NAVIGATE",
    "PID_RUNNER_GPS_TEMPLATE_22_NAVIGATION",
    "PID_RUNNER_GPS_TEMPLATE_1G_GRAPH",
    "PID_RUNNER_GPS_TEMPLATE_50_MAP_DRAW",
    "PID_RUNNER_GPS_TEMPLATE_4",
    "0x0127",
)
_NON_GPS_SYSTEM_TAIL = (
    "PID_RUNNER_GPS_TEMPLATE_4",
    "0x0124",
    "0x0127",
)

# Real, confirmed from SuuntoLink's own real JS source (getMaxDisplays() in
# suunto_apps.js/sport_mode.js - a real, live client-side product config, not guessed):
# Traverse/Traverse Alpha cap at 4 real display screens per mode; every other real variant
# in this watch family (Ambit/Ambit2/Ambit3, everything this project's own _modelNames
# table - see android/.../AmbitUsbModule.kt and desktop/.../HomeViewModel.qml - knows
# about) caps at 8. Keyed on the real engineering codename (DeviceService.model / the
# 0x0000 identity reply's own value), not a commercial name.
_MAX_DISPLAYS_BY_VARIANT = {"Jabiru": 4, "Loon": 4}
_DEFAULT_MAX_DISPLAYS = 8


def max_displays_for_variant(variant):
    return _MAX_DISPLAYS_BY_VARIANT.get(variant, _DEFAULT_MAX_DISPLAYS)


def system_tail_length(display_templates):
    """How many trailing entries in a real mode's own Displays list are built-in/system
    screens (not real user-configurable "Screen N" ones) - see this module's own
    _GPS_SYSTEM_TAIL/_NON_GPS_SYSTEM_TAIL comment for exactly what's confirmed.

    SUPERSEDED 2026-08-12 by user_display_count() below, which asks the display's own Type
    instead of pattern-matching its template names. Kept because it is a real, verified
    description of the two tails this project has seen, and because removing a published
    helper is not worth it - but nothing in this module calls it any more."""
    if tuple(display_templates[-len(_GPS_SYSTEM_TAIL):]) == _GPS_SYSTEM_TAIL:
        return len(_GPS_SYSTEM_TAIL)
    if tuple(display_templates[-len(_NON_GPS_SYSTEM_TAIL):]) == _NON_GPS_SYSTEM_TAIL:
        return len(_NON_GPS_SYSTEM_TAIL)
    return 0


# The display Type that means "a screen the wearer swipes to and the editor counts". Every
# other Type in this format is one of the watch's own built-in views - compass, navigation,
# graph, map, lap - which every mode carries and no editor shows.
USER_DISPLAY_TYPE = 10


def user_display_count(displays):
    """How many of a mode's displays are real user screens.

    Established 2026-08-12 from assets/pcap/removeandaddsportsmodeandmultisport, where both
    modes SuuntoLink creates are born with 8 displays of which exactly one is Type 10 - and
    SuuntoLink calls that mode "1 screen". Checked across every CustomModes image in
    assets/pcap: 1329 modes, 9 distinct display Types (4, 5, 6, 7, 10, 15, 50, 65, 70), and
    the Type 10 ones are a contiguous prefix in all 1329 - never interleaved.

    THIS REPLACES the template-name tail match system_tail_length() used to do, for a real
    reason rather than tidiness. Where that rule recognised the tail (1325 of those 1329
    modes) the two agree exactly - zero disagreements - but on the other 4 it recognised
    nothing and reported every built-in view as a user screen. That is a real bug seen in
    the desktop app on 2026-08-12: the demo watch's Cycling showed "11 display(s)" where
    SuuntoLink shows 5, because its display list ends in a tail neither pattern matched.
    Asking the format instead of pattern-matching it cannot fail that way."""
    return sum(1 for d in displays if d.get("Type") == USER_DISPLAY_TYPE)


# Real, 2026-08-10 (André, item 11: "on ambit 3, open water swim, screen 1 I see 1 0x0047,
# 2 lap stopwatch, 3 shortcut, for the same sport on suunto link I see ... 3 I have two
# screens/datas average swim pace and average stroke rate").
#
# A row is NOT one value. Each DISP_FIELD carries a base Type plus a `Shortcuts` list, and
# when the base Type is 0 the row's real content IS that list - up to 5 values the wearer
# steps through with a button press (automatically only when the mode's own autoscroll is
# on). Confirmed two ways: across all 111 CustomModes saves in
# `assets/pcap/running2fromcreateandthen1to7` every shortcut-carrying row has base Type 0
# (29/29), and SuuntoLink's own sport_mode.js `defaultDisplay()` models exactly this - Top
# and Center carry a single value while Bottom carries a nested `Fields` LIST.
#
# This function used to emit only the base Type, so a row like Openwater swim's
# `Shortcuts=[88, 87]` (FT_SWIM_AVG_PACE + average stroke rate) rendered as one meaningless
# value and the extra content vanished. `values` below is what the row actually shows, in
# order, whichever form it takes.
_ROW_LABELS = ("Top", "Center", "Bottom")   # sport_mode.js FieldId


def _field_to_json(f):
    def label(t):
        return field_type_label(FIELD_TYPES.get(t, f"0x{t:04x}"))
    shortcuts = list(f.get("Shortcuts") or [])
    base = f["Type"]

    # Type 0 (FT_SHORTCUT) means two different things and the difference is whether the row
    # carries shortcuts. WITH them it is the marker for a multi-value row and the values are
    # the shortcuts. WITHOUT them the row is genuinely EMPTY - confirmed 2026-08-11 by André
    # setting a row to Empty in SuuntoLink and watching it become Type=0/Shortcuts=[].
    # Labelling both "Shortcut" showed an empty row as if it displayed something called a
    # shortcut, which is the raw enum name leaking into the UI (André: "on our app it shows
    # shortcut, can we change to empty").
    def row_values():
        if base == 0 and shortcuts:
            return [{"type": t, "label": label(t)} for t in shortcuts]
        if base == 0:
            return [{"type": 0, "label": "Empty"}]
        return [{"type": base, "label": label(base)}]

    values = row_values()
    idx = f.get("Index")
    return {
        "indexName": f["IndexName"],
        "rowLabel": _ROW_LABELS[idx] if isinstance(idx, int) and idx < len(_ROW_LABELS) else None,
        "type": base,
        "typeLabel": "Empty" if (base == 0 and not shortcuts) else label(base),
        "shortcuts": shortcuts,
        # What the row really displays: the shortcut list when the base type is 0, else the
        # single base value. A UI should render this and ignore `type` unless it needs the
        # raw encoding.
        "values": values,
        "isMultiValue": len(values) > 1,
    }


def _interval_timer_json(settings):
    """The mode's Interval Timer as a UI needs it: {enabled, type, high, low, repetitions}.
    Decoded from IntTimerFlags/IntTimerCount and the interval slots - the full slot's own Type
    (1=Time/mm'ss, 0=Distance/km) and Len (the "high" threshold), and the third slot's MaxLimit
    (the "low" threshold). Time values are whole seconds, distance values are meters. See
    tools/interval_timer.py (the writer) for the byte-exact derivation from real SuuntoLink
    captures - this only surfaces what decode_settings() already parsed."""
    slots = settings.get("IntervalSlots") or []
    full = slots[0] if len(slots) > 0 else {}
    low_slot = slots[2] if len(slots) > 2 else {}
    return {
        "enabled": settings.get("IntTimerFlags") == 1,
        "type": "time" if full.get("Type") == 1 else "distance",
        "high": full.get("Len", 0),
        "low": low_slot.get("MaxLimit", 0),
        "repetitions": settings.get("IntTimerCount", 0),
    }


def _displays_to_json(displays):
    """One mode's own real Displays list, JSON-shaped - `index` stays the raw 0-based
    position (writeDisplayField's own addressing needs this unchanged), `screenNumber` is
    the real 1-based number the user would actually see on the watch (None for a built-in
    screen - see system_tail_length()'s own module-level comment), `isBuiltIn` flags which
    is which."""
    out = []
    for i, disp in enumerate(displays):
        # The display's own Type, not a guess from its template name - see
        # user_display_count() for why that changed and what it fixes.
        is_built_in = disp.get("Type") != USER_DISPLAY_TYPE
        out.append({
            "index": i,
            "screenNumber": None if is_built_in else i + 1,
            "isBuiltIn": is_built_in,
            # The raw display Type, so a UI can count user screens the same way this does
            # rather than re-deriving the rule.
            "type": disp.get("Type"),
            "template": disp["TemplateName"],
            # The numeric template id as well as its name. A UI needs the number to ask
            # which values may go on this display's rows (SuuntoLink keys that by display
            # type), and deriving it back from the name would be a parse where a field will
            # do.
            "templateId": disp["Template"],
            "templateLabel": field_type_label(disp["TemplateName"]),
            "fields": [_field_to_json(f) for f in disp["Fields"]],
        })
    return out


def to_json(result):
    """A JSON-friendly view of decode()'s own result dict, for backend/server.py - added
    2026-08-08 alongside the real CustomModes write tools (custom_modes_rename_test.py/
    custom_modes_field_write_test.py/custom_modes_display_field_write_test.py). Every field
    name here matches what those write tools themselves expect (SETTING_FIELDS' own real
    names for --set, FIELD_TYPES' own real names for --to/--type), so a UI built against
    this can round-trip without a second name-mapping layer."""
    modes = []
    for mode in result["exercise_modes"]:
        s = mode.get("Settings", {})
        modes.append({
            "name": s.get("Name"),
            "activityId": s.get("ActivityID"),
            # How many Suunto Apps this mode has installed. A UI needs it to enforce the
            # per-mode ceiling SuuntoLink's own getMaxSuuntoApps() reports (5 for this watch
            # family, confirmed from its module and independently by André's Running2 having
            # exactly 5 rules referenced as field ids 51/52/53/107/108).
            "appCount": len(mode.get("Rules") or []),
            "customModeId": s.get("CustomModeID"),
            "useHw": s.get("UseHw"),
            "altiBaroMode": s.get("AltiBaroMode"),
            "recordingInterval": s.get("RecordingInterval"),
            "autolap": s.get("Autolap"),
            "hrHigh": s.get("HrHigh"),
            "hrLow": s.get("HrLow"),
            "hrLimitsUse": s.get("HrLimitsUse"),
            "autoStart": s.get("AutoStart"),
            "autoPause": s.get("AutoPause"),
            "autoScrolling": s.get("AutoScrolling"),
            "intTimerFlags": s.get("IntTimerFlags"),
            "intTimerCount": s.get("IntTimerCount"),
            "intervalTimer": _interval_timer_json(s),
            "backlightMode": s.get("BacklightModeName"),
            "displayMode": s.get("DisplayModeName"),
            "quickNavigation": s.get("QuickNavigationName"),
            "displays": _displays_to_json(mode["Displays"]),
            "rules": mode["Rules"],
        })

    # A SPORT_MODE slot is either a pointer to one exercise mode (an ordinary sport mode) or
    # an ordered list of several - a MULTISPORT mode. André's own watch has "Triathlon"
    # (activityId 19) with legs [0, 1, 2, 1, 3]: Openwater swim, Transition, Cycling,
    # Transition, Running. That is why the Triathlon never appeared in a UI listing exercise
    # modes: it has no exercise mode of its own, only legs pointing at other modes'.
    #
    # `legNames` resolves those indices here rather than making every caller do it, and
    # `usedByMultisport` marks the exercise modes a multisport leg points at - SuuntoLink
    # refuses to delete one of those ("You can't delete this sport mode because it's used in
    # one of your multisport modes"), and a UI needs to know which without re-deriving it.
    exercise_names = [m.get("Settings", {}).get("Name") for m in result["exercise_modes"]]
    used_by_multisport = set()
    # `inWatchMenu` marks the exercise modes that have a SPORT_MODES entry of their own, i.e.
    # the ones you can actually select on the watch. Not every mode does: the factory-fitted
    # "Transition" (ActivityID 1, from Movescount days) has none, which is why it costs none
    # of the watch's 10 slots and cannot be started on its own. Established 2026-08-12 from
    # assets/pcap/removeandaddsportsmodeandmultisport - see docs/custom_modes_andre.md. A UI needs
    # this to explain why its list can be longer than the "N/10 used" beside it.
    in_menu = set()
    sport_modes = []
    for slot in result["sport_modes"]:
        if not slot["Name"]:
            continue
        legs = list(slot["Exercises"] or [])
        if len(legs) > 1:
            used_by_multisport.update(legs)
        else:
            in_menu.update(legs)
        sport_modes.append({
            "name": slot["Name"],
            "activityId": slot["ActivityID"],
            "order": slot.get("Order"),
            "legs": legs,
            "legNames": [exercise_names[i] if 0 <= i < len(exercise_names) else None
                         for i in legs],
            "isMultisport": len(legs) > 1,
        })

    for index, mode in enumerate(modes):
        mode["usedByMultisport"] = index in used_by_multisport
        mode["inWatchMenu"] = index in in_menu

    return {"ok": True, "formatType": result.get("format_type"),
            "exerciseModes": modes, "sportModes": sport_modes}


def show(result, verbose=False):
    print(f"format_type={result.get('format_type')}")
    print(f"\n{len(result['exercise_modes'])} exercise mode(s):")
    for mode in result["exercise_modes"]:
        s = mode.get("Settings", {})
        print(f"  {s.get('Name', '?')!r:20} ActivityID=0x{s.get('ActivityID', 0):02x}"
              f"  CustomModeID={s.get('CustomModeID')}"
              f"  UseHw=0x{s.get('UseHw', 0):04x}"
              f"  AltiBaroMode={s.get('AltiBaroMode')}"
              f"  RecordingInterval={s.get('RecordingInterval')}"
              f"  Autolap={s.get('Autolap')}")
        print(f"      BacklightMode={s.get('BacklightModeName')}"
              f"  Display={s.get('DisplayModeName')}"
              f"  QuickNavigation={s.get('QuickNavigationName')}")
        print(f"      {len(mode['Displays'])} display(s)"
              + (f", {len(mode['Rules'])} rule(s)" if mode["Rules"] else ""))
        if mode["AppMeta"]:
            t1 = datetime.datetime.fromtimestamp(mode["AppMeta"]["Timestamp1"], datetime.UTC)
            print(f"      AppMeta: timestamps={mode['AppMeta']['Timestamp1']}"
                  f",{mode['AppMeta']['Timestamp2']} (~{t1} UTC)")
        if verbose:
            for i, disp in enumerate(mode["Displays"]):
                fields = "; ".join(
                    f"{f['IndexName']}(Type={f['Type']}"
                    + (f", Shortcuts={f['Shortcuts']}" if f["Shortcuts"] else "") + ")"
                    for f in disp["Fields"])
                print(f"        [{i}] {disp['TemplateName']} (Type={disp['Type']}): {fields}")
            for rule in mode["Rules"]:
                print(f"        RULE RuleIdx={rule['RuleIdx']} UseRule={rule['UseRule']}"
                      f" LogRule={rule['LogRule']}")
        if mode["_raw_children"]:
            children = ", ".join(f"{n}[{l}]" for n, l in mode["_raw_children"])
            print(f"      (not yet decoded: {children})")
        if "_warning" in s:
            print(f"      WARNING: {s['_warning']}")

    mode_names = [m.get("Settings", {}).get("Name", "?") for m in result["exercise_modes"]]

    populated = [s for s in result["sport_modes"] if s["Name"]]
    print(f"\n{len(populated)}/{len(result['sport_modes'])} SPORT_MODES (multisport) "
          "slot(s) populated:")
    for slot in populated:
        legs = slot["Exercises"]
        leg_names = " -> ".join(
            mode_names[i] if 0 <= i < len(mode_names) else f"?({i})" for i in legs)
        print(f"  {slot['Name']!r:20} ActivityID=0x{slot['ActivityID']:02x}"
              f"  legs={legs}  ({leg_names})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="from_file", metavar="FILE",
                     help="decode a raw CustomModes dump (12288 bytes) instead of the watch")
    ap.add_argument("--displays", action="store_true",
                     help="also print each exercise mode's decoded screens/fields and rules")
    ap.add_argument("--save", metavar="FILE",
                     help="also save the raw region bytes here (only meaningful when reading"
                          " live from the watch) - for capturing a clean before/after pair")
    ap.add_argument("--json", action="store_true",
                     help="print one JSON line instead of human-readable output - for "
                          "ambitapp-v2/backend/server.py, not meant for a person to read")
    ap.add_argument("--list-field-types", action="store_true",
                     help="print the real FIELD_TYPES catalog as one JSON line and exit - "
                          "no watch touched, for a UI's own data-field picker")
    args = ap.parse_args()

    if args.list_field_types:
        print(json.dumps({"ok": True, "fieldTypes": [
            {"value": k, "name": v, "label": field_type_label(v)}
            for k, v in sorted(FIELD_TYPES.items())]}))
        return 0

    if args.from_file:
        with open(args.from_file, "rb") as f:
            data = f.read()
    else:
        from write_nav import Link, read_flash
        link = Link(dry_run=False, verbose=not args.json)
        if not args.json:
            print("read-only: 0x0b17 reads flash, nothing is written")
        link.open()
        data = read_flash(link, CUSTOM_MODES_BASE, CUSTOM_MODES_SIZE, label="CustomModes")
        if args.save:
            with open(args.save, "wb") as f:
                f.write(data)
            if not args.json:
                print(f"\nsaved raw dump to {args.save}")

    result = decode(data)
    if args.json:
        print(json.dumps(to_json(result)))
    else:
        show(result, verbose=args.displays)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
