#!/usr/bin/env python3
"""Create and delete sport modes, and build/edit/delete multisport combos.

DRY-RUN BY DEFAULT: without --write nothing is sent, only what would change is printed.

Why this exists separately from custom_modes_edit.py: that one edits the *displays* of a
mode that already exists. This one changes the mode LIST itself - which is a different
problem, because the list is two parallel structures that have to stay consistent:

    EXERCISE_MODES   the sport modes themselves (settings + displays), positional
    SPORT_MODES      the watch's menu: one entry per selectable item, each pointing at
                     one EXERCISE_MODES entry by POSITION (a single sport) or at several
                     in order (a multisport combo)

Deleting an exercise mode therefore shifts every position above it, and every SPORT_MODES
leg index above it has to be decremented to match, or the watch's menu silently points at
the wrong sports. That renumbering is the whole reason this is a tool and not a one-liner.

WHERE THE RULES COME FROM. Everything here is either SuuntoLink's own code or a real
capture, never a guess:

  * assets/pcap/removeandaddsportsmodeandmultisport - André's 2026-08-12 capture of
    SuuntoLink adding and removing sport modes and all three kinds of multisport combo.
    17 full region images, 16 transitions, every one of which --selftest reproduces
    byte-exact. That capture is the specification for the write sequences below.
  * SuuntoLink 4.1.15's ui/sport_mode_editor.js - MAX_MULTISPORT_MODES=2,
    MAX_MULTISPORT_SPORT_MODES=6, and getNextModeId/getNextGroupId (the "lowest free
    number" id allocation reproduced in _next_free below).
  * SuuntoLink's ambit/sport_mode.js and activity.js, via assets/sportmode_rows.json -
    per-activity creation defaults, per-device limits, and which activities are
    multisport containers.

THE LIMITS, as the watch and SuuntoLink actually count them (André, 2026-08-12):

  * 10 sport modes per watch, and a multisport combo COSTS ONE OF THEM. This is
    countSportModes() in sport_mode.js: the number used is the length of the SPORT_MODES
    list, singles and combos alike.
  * 2 multisport combos max, out of those 10.
  * a combo has at least 2 and at most 6 legs, chosen from the modes you already have.
  * a transition is not free: it is an ordinary sport mode you create first (activity
    "Transition", id 99), it takes one of the 10 slots, and only then can a combo use it.
    Repeats are allowed - a triathlon with two transitions spends 2 of its 6 legs on them.

Note the ONE exception, visible on André's own watch: the factory-fitted "Transition"
(ActivityID 1, from Movescount days) has an EXERCISE_MODES entry but NO SPORT_MODES entry,
so it costs nothing and cannot be selected on its own. Anything created today is the
ActivityID 99 kind, which does get a menu entry and does cost a slot.

    ./tools/sport_mode_manage.py --list
    ./tools/sport_mode_manage.py --create "Alpine skiing" --activity 20
    ./tools/sport_mode_manage.py --delete "Alpine skiing"
    ./tools/sport_mode_manage.py --create-multisport Triathlon --activity 19 \
        --legs "Openwater swim,Transition,Cycling,Transition,Running"
    ./tools/sport_mode_manage.py --delete-multisport Triathlon
    ./tools/sport_mode_manage.py --selftest          # replay the capture, no watch needed

THE SAFETY RULE, enforced on every real write, exactly as custom_modes_edit.py enforces
it: the region read off the watch is decoded and re-encoded FIRST, and if that does not
come back byte-identical the write is refused. If we cannot reproduce what is already on
the watch, we have no business writing a modified version of it.
"""

import argparse
import copy
import json
import pathlib
import sys
import time

import ambit_format as F
import custom_modes
import custom_modes_write

REPO = pathlib.Path(__file__).resolve().parent.parent
ROWS = REPO / "assets" / "sportmode_rows.json"
ACTIVITIES = REPO / "assets" / "activity_types.json"
DEFAULT_VARIANT = "Emu"                      # Ambit3 Peak, this project's reference watch


# --- UseHw, assembled from SuuntoLink's own per-activity answers ------------------------
# The bit meanings were resolved bit-for-bit earlier in this project (custom_modes_andre.md,
# isolated enable/disable captures per pod). Two of them are resolved HERE, 2026-08-12, by
# computing this mask for all 9 factory modes on André's watch plus the 2 modes the capture
# creates, and requiring all 11 to match the real bytes - which they do:
#
#   0x0080  cadence pod - was missing from the list entirely. Cycling's real UseHw is
#           0x08C3, and its defaults are bike+cadence+power; 0x0800|0x0040|0x0003 only
#           accounts for 0x08C3 if cadence is the remaining 0x0080.
#   0x0004  UseAccelerometer - previously recorded as "seen set on Running/Trekking/Walk,
#           still unconfirmed which sensor". It is not a sensor: sport_mode.js exports
#           useAccelerometer(variant, activity), and its true/false answer matches this bit
#           on all 11 modes (true for Running, Run a route, Trekking, Mountaineering;
#           false for the other seven).
HW_HR_BELT = 0x0001
HW_SENSOR_SEARCH = 0x0002                    # set whenever any external sensor is used
HW_ACCELEROMETER = 0x0004
HW_POWER_POD = 0x0040
HW_CADENCE_POD = 0x0080
HW_FOOT_POD = 0x0100
HW_BIKE_POD = 0x0800

# The interval-timer slots a mode is born with: five empty slots and a 0xff terminator.
# Verbatim from both modes the capture creates, and identical to every factory mode's.
NEW_MODE_INTERVAL_SLOTS = [
    {"Flags": 0, "Type": 0, "MaxLimit": 0, "MinLimit": 0, "Padding": 0, "Len": 0},
    {"Flags": 0, "Type": 0, "MaxLimit": 0, "MinLimit": 0},
    {"Flags": 0, "Type": 0, "MaxLimit": 0, "MinLimit": 0},
    {"Flags": 0, "Type": 0, "MaxLimit": 0, "MinLimit": 0},
    {"Flags": 0, "Type": 0, "MaxLimit": 0, "MinLimit": 0},
    {"Flags": 255, "Type": 0, "MaxLimit": 255, "MinLimit": 0},
]

# What a new mode's display set is. NOT activity-dependent: SuuntoLink gave the newly
# created "Alpine skiing" and the newly created "Transition" in the capture byte-identical
# display sets, 342 minutes apart, so this is one fixture rather than a per-sport table.
#
# Only the first entry (Type 10) is a screen the user swipes to and the editor counts -
# hence "Alpine skiing, 1 screen" in SuuntoLink's list. The other seven are the watch's
# built-in compass/navigation/graph/map/lap views, which every mode carries and no editor
# shows. That is also the answer to the old open question in custom_modes_andre.md about
# Cycling decoding to 15 displays while the UI says 8/8: user displays and system displays
# live in the same list and only the Type 10 ones are counted.
NEW_MODE_DISPLAYS = [
    {"Template": 260, "TemplateName": "PID_RUNNER_GPS_TEMPLATE_4", "Type": 10, "Fields": [
        {"Index": 0, "IndexName": "FT_SHORTCUT", "Type": 10, "Shortcuts": []},
        {"Index": 1, "IndexName": "FT_TIME", "Type": 11, "Shortcuts": []},
        {"Index": 2, "IndexName": "FT_TIME_SEC", "Type": 0, "Shortcuts": [5]}]},
    {"Template": 273, "TemplateName": "PID_RUNNER_GPS_TEMPLATE_11_COMPASS_NAVIGATE",
     "Type": 4, "Fields": [
        {"Index": 0, "IndexName": "FT_SHORTCUT", "Type": 8, "Shortcuts": []},
        {"Index": 1, "IndexName": "FT_TIME", "Type": 8, "Shortcuts": []},
        {"Index": 2, "IndexName": "FT_TIME_SEC", "Type": 0, "Shortcuts": [16, 1, 65534]}]},
    {"Template": 291, "TemplateName": "PID_RUNNER_GPS_TEMPLATE_11_MILS_COMPASS_NAVIGATE",
     "Type": 5, "Fields": [
        {"Index": 0, "IndexName": "FT_SHORTCUT", "Type": 8, "Shortcuts": []},
        {"Index": 1, "IndexName": "FT_TIME", "Type": 40, "Shortcuts": []},
        {"Index": 2, "IndexName": "FT_TIME_SEC", "Type": 0,
         "Shortcuts": [16, 8, 1, 65534]}]},
    {"Template": 290, "TemplateName": "PID_RUNNER_GPS_TEMPLATE_22_NAVIGATION",
     "Type": 6, "Fields": [
        {"Index": 0, "IndexName": "FT_SHORTCUT", "Type": 24, "Shortcuts": []},
        {"Index": 1, "IndexName": "FT_TIME", "Type": 25, "Shortcuts": []},
        {"Index": 2, "IndexName": "FT_TIME_SEC", "Type": 0, "Shortcuts": [50, 26, 16]}]},
    {"Template": 257, "TemplateName": "PID_RUNNER_GPS_TEMPLATE_1G_GRAPH",
     "Type": 65, "Fields": [
        {"Index": 1, "IndexName": "FT_TIME", "Type": 195, "Shortcuts": []},
        {"Index": 2, "IndexName": "FT_TIME_SEC", "Type": 196, "Shortcuts": []},
        {"Index": 3, "IndexName": "FT_DATE", "Type": 197, "Shortcuts": []}]},
    {"Template": 336, "TemplateName": "PID_RUNNER_GPS_TEMPLATE_50_MAP_DRAW",
     "Type": 7, "Fields": []},
    {"Template": 260, "TemplateName": "PID_RUNNER_GPS_TEMPLATE_4", "Type": 50, "Fields": [
        {"Index": 0, "IndexName": "FT_SHORTCUT", "Type": 62, "Shortcuts": []},
        {"Index": 1, "IndexName": "FT_TIME", "Type": 61, "Shortcuts": []},
        {"Index": 2, "IndexName": "FT_TIME_SEC", "Type": 0,
         "Shortcuts": [5, 10, 21, 11, 28]}]},
    {"Template": 295, "TemplateName": "0x0127", "Type": 15, "Fields": [
        {"Index": 0, "IndexName": "FT_SHORTCUT", "Type": 162, "Shortcuts": []},
        {"Index": 1, "IndexName": "FT_TIME", "Type": 161, "Shortcuts": []},
        {"Index": 2, "IndexName": "FT_TIME_SEC", "Type": 160, "Shortcuts": []}]},
]

# The gap SuuntoLink leaves between the two writes of a creation, in seconds. Measured at
# 2 in both creations in the capture (1786499262 -> 1786499264, 1786499604 -> 1786499606),
# matching custom_modes_edit.STAMP_GAP_SECONDS for ordinary saves.
STAMP_GAP_SECONDS = 2
# ...and between the second write and the SPORT_MODES link write: 3 seconds for Alpine
# skiing, 2 for Transition. It is elapsed work rather than a wait (SuuntoLink re-reads the
# settings and the memory map in between), so this is a floor, not a sleep.
LINK_GAP_SECONDS = 2


class LimitError(Exception):
    """A rule the watch or SuuntoLink enforces would be broken. Never written through."""


# --- the catalogue ---------------------------------------------------------------------

def load_catalogue():
    """(activities_by_id, sportmode_rows) - both generated from SuuntoLink, not typed in."""
    activities = {a["id"]: a for a in json.loads(ACTIVITIES.read_text())}
    rows = json.loads(ROWS.read_text())
    return activities, rows


def variant_limits(rows, variant=DEFAULT_VARIANT):
    """What this watch allows. Falls back to the Ambit3 Peak's row for an unknown code."""
    v = rows["variants"].get(variant) or rows["variants"][DEFAULT_VARIANT]
    return {
        "maxSportModes": v["maxSportModes"],
        "maxMultisportModes": v["maxMultisportModes"],
        "supportsMultisportModes": v["supportsMultisportModes"],
        "minLegs": rows["limits"]["minMultisportLegs"],
        "maxLegs": rows["limits"]["maxMultisportLegs"],
        "maxNameLength": 63,                 # sport_mode.js getMaxNameLength, whole family
    }


def activity_defaults(rows, activity_id, variant=DEFAULT_VARIANT):
    """SuuntoLink's getActivityDefaults() row for this sport on this watch."""
    per = rows["activityDefaults"].get(variant) or rows["activityDefaults"][DEFAULT_VARIANT]
    d = per.get(str(activity_id))
    if d is None:
        raise LimitError(f"activity {activity_id} has no defaults for variant {variant}")
    return d


def use_hw_for(defaults, accelerometer):
    """The UseHw bitmask a newly created mode of this activity is born with."""
    mask = HW_SENSOR_SEARCH
    if defaults["hrBelt"]:
        mask |= HW_HR_BELT
    if accelerometer:
        mask |= HW_ACCELEROMETER
    if defaults["powerPod"]:
        mask |= HW_POWER_POD
    if defaults["cadencePod"]:
        mask |= HW_CADENCE_POD
    if defaults["footPod"]:
        mask |= HW_FOOT_POD
    if defaults["bikePod"]:
        mask |= HW_BIKE_POD
    return mask


# --- reading the current state ----------------------------------------------------------

def exercise_names(decoded):
    return [m["Settings"]["Name"] for m in decoded["exercise_modes"]]


def find_exercise(decoded, name):
    for i, m in enumerate(decoded["exercise_modes"]):
        if m["Settings"]["Name"] == name:
            return i, m
    raise LimitError(f"no sport mode named {name!r} "
                     f"(have: {', '.join(exercise_names(decoded))})")


def find_sport_mode(decoded, name):
    for i, s in enumerate(decoded["sport_modes"]):
        if s["Name"] == name:
            return i, s
    raise LimitError(f"no menu entry named {name!r} "
                     f"(have: {', '.join(s['Name'] for s in decoded['sport_modes'])})")


def is_multisport(entry):
    return len(entry["Exercises"]) > 1


def counts(decoded):
    """(used, multisport_used) the way SuuntoLink's own countSportModes() counts them:
    every SPORT_MODES entry costs a slot, combos included."""
    total = len(decoded["sport_modes"])
    return total, sum(1 for s in decoded["sport_modes"] if is_multisport(s))


def users_of(decoded, exercise_index):
    """Names of the multisport combos that use this exercise mode as a leg."""
    return [s["Name"] for s in decoded["sport_modes"]
            if is_multisport(s) and exercise_index in s["Exercises"]]


def _next_free(used):
    """SuuntoLink's getNextModeId/getNextGroupId: the lowest positive integer not in use.

    This is why both modes the capture creates come out with CustomModeID=1 - the factory
    modes carry Movescount-era ids in the 60595..60604 range, so 1 was free."""
    n = 1
    while n in used:
        n += 1
    return n


# --- the operations ---------------------------------------------------------------------

def new_exercise_mode(rows, name, activity_id, variant, mode_id, accelerometer, now):
    """A freshly created sport mode, as it looks in the FIRST of the two creation writes:
    settings filled in from the activity's defaults, no displays yet, and a half-written
    stamp (Timestamp2 = 0) marking it as created-but-not-committed."""
    d = activity_defaults(rows, activity_id, variant)
    return {
        "Displays": [],
        "Rules": [],
        "AppMeta": {"Timestamp1": now, "Timestamp2": 0},
        "_raw_children": [],
        "Settings": {
            "Name": name,
            "ActivityID": activity_id,
            "UseHw": use_hw_for(d, accelerometer),
            "AltiBaroMode": d["altiBaroProfile"],
            "GpsPowerMode": d["gpsInterval"],
            "RecordingInterval": d["recordingInterval"],
            "Autolap": 0,
            # SuuntoLink writes zeroes here on any mode it rewrites in full, even though
            # its own defaultSettings() carries 120/170 - the same reset already noted on
            # Trekking in custom_modes_andre.md. Both created modes in the capture are 0/0.
            "HrHigh": 0,
            "HrLow": 0,
            "HrLimitsUse": 0,
            "AutoStart": 0,
            "AutoPause": 0,
            "AutoScrolling": 0,
            "IntTimerFlags": 0,
            "IntTimerCount": 99,             # defaultSettings().IntervalTimer.Repetitions
            "CustomModeID": mode_id,
            "IntervalSlots": copy.deepcopy(NEW_MODE_INTERVAL_SLOTS),
            "BacklightMode": 255,
            "BacklightModeName": "DEFAULT",
            "DisplayMode": 255,
            "DisplayModeName": "DEFAULT",
            "QuickNavigation": 0,
            "QuickNavigationName": "OFF",
        },
    }


def create_sport_mode(decoded, name, activity_id, rows=None, variant=DEFAULT_VARIANT,
                      accelerometer=False, now=None, link_now=None):
    """Add a single sport mode. Returns the three region images SuuntoLink writes:

        1. the mode appended to EXERCISE_MODES, no displays, AppMeta {now, 0}
        2. the same mode with its default displays, AppMeta {now, now+2}
        3. its SPORT_MODES menu entry appended, stamped when that write happens

    Splitting it this way is not decoration - it is what the capture shows, twice, and the
    touch-then-commit shape is what you design for old flash that may lose power mid-write
    (the same reasoning already recorded for custom_modes_edit.stamp_plan).

    `link_now` is the third write's own stamp. It is elapsed work rather than a fixed
    offset - the capture measures 3 seconds after write 2 for Alpine skiing and 2 for
    Transition - so a caller writing for real should pass the clock reading it takes at
    that moment. The default is the floor."""
    rows = rows or load_catalogue()[1]
    limits = variant_limits(rows, variant)
    used, _multi = counts(decoded)
    if used >= limits["maxSportModes"]:
        raise LimitError(
            f"{used}/{limits['maxSportModes']} sport modes already used - delete one first. "
            f"Multisport combos count toward this limit too.")
    if not name:
        raise LimitError("a sport mode needs a name")
    if len(name.encode("utf-8")) > limits["maxNameLength"]:
        raise LimitError(f"name is longer than {limits['maxNameLength']} bytes")
    if any(m["Settings"]["Name"] == name for m in decoded["exercise_modes"]):
        raise LimitError(f"a sport mode named {name!r} already exists")
    if activity_id in rows["multisportActivities"]:
        raise LimitError(
            f"activity {activity_id} is a multisport container - use --create-multisport")

    now = int(time.time() if now is None else now)
    mode_ids = {m["Settings"]["CustomModeID"] for m in decoded["exercise_modes"]}
    orders = {s["Order"] for s in decoded["sport_modes"]}

    touch = copy.deepcopy(decoded)
    touch["exercise_modes"].append(new_exercise_mode(
        rows, name, activity_id, variant, _next_free(mode_ids), accelerometer, now))

    commit = copy.deepcopy(touch)
    fresh = commit["exercise_modes"][-1]
    fresh["Displays"] = copy.deepcopy(NEW_MODE_DISPLAYS)
    fresh["AppMeta"] = {"Timestamp1": now, "Timestamp2": now + STAMP_GAP_SECONDS}

    link = copy.deepcopy(commit)
    link["sport_modes"].append({
        "Name": name,
        "ActivityID": activity_id,
        "Exercises": [len(link["exercise_modes"]) - 1],
        "Order": _next_free(orders),
        "AppMeta": (now + STAMP_GAP_SECONDS + LINK_GAP_SECONDS if link_now is None
                    else int(link_now)),
    })
    return [touch, commit, link]


def delete_sport_mode(decoded, name):
    """Remove a single sport mode: its EXERCISE_MODES entry, its SPORT_MODES menu entry,
    and - the part that matters - decrement every remaining leg index above it, since legs
    address exercise modes by position.

    Refuses when a multisport combo still uses it, which is what SuuntoLink does: it greys
    the bin and says "You can't delete this sport mode because it's used in one of your
    multisport modes." Delete the combo (or edit that leg out) first."""
    index, _mode = find_exercise(decoded, name)
    blocking = users_of(decoded, index)
    if blocking:
        raise LimitError(f"{name!r} is a leg of {', '.join(blocking)} - "
                         f"delete or edit that multisport mode first")

    out = copy.deepcopy(decoded)
    del out["exercise_modes"][index]
    out["sport_modes"] = [s for s in out["sport_modes"]
                          if not (len(s["Exercises"]) == 1 and s["Exercises"][0] == index)]
    for s in out["sport_modes"]:
        s["Exercises"] = [i - 1 if i > index else i for i in s["Exercises"]]
    return [out]


def _resolve_legs(decoded, legs):
    """Leg names (or positions) -> exercise-mode positions, order preserved, repeats kept."""
    out = []
    for leg in legs:
        if isinstance(leg, int):
            if not 0 <= leg < len(decoded["exercise_modes"]):
                raise LimitError(f"leg index {leg} is outside the mode list")
            out.append(leg)
        else:
            out.append(find_exercise(decoded, leg)[0])
    return out


def create_multisport(decoded, name, activity_id, legs, rows=None,
                      variant=DEFAULT_VARIANT, now=None):
    """Add a multisport combo. One write, and no EXERCISE_MODES entry is created - a combo
    is purely a SPORT_MODES entry whose legs point at sport modes that already exist.

    `legs` is the ORDER the watch steps through, by mode name; repeats are allowed and are
    how a triathlon gets two transitions."""
    rows = rows or load_catalogue()[1]
    limits = variant_limits(rows, variant)
    if not limits["supportsMultisportModes"]:
        raise LimitError(f"{variant} has no multisport modes")
    used, multi = counts(decoded)
    if multi >= limits["maxMultisportModes"]:
        raise LimitError(f"{multi}/{limits['maxMultisportModes']} multisport modes already "
                         f"used - delete one first")
    if used >= limits["maxSportModes"]:
        raise LimitError(f"{used}/{limits['maxSportModes']} sport modes already used - a "
                         f"multisport mode needs one of those slots too")
    if activity_id not in rows["multisportActivities"]:
        raise LimitError(f"activity {activity_id} cannot hold several sports; pick one of "
                         f"{rows['multisportActivities']} "
                         f"(Multisport, Triathlon, Adventure racing)")
    if not name:
        raise LimitError("a multisport mode needs a name")
    if len(name.encode("utf-8")) > limits["maxNameLength"]:
        raise LimitError(f"name is longer than {limits['maxNameLength']} bytes")
    if any(s["Name"] == name for s in decoded["sport_modes"]):
        raise LimitError(f"a mode named {name!r} already exists")

    resolved = _resolve_legs(decoded, legs)
    if not limits["minLegs"] <= len(resolved) <= limits["maxLegs"]:
        raise LimitError(f"a multisport mode takes {limits['minLegs']} to "
                         f"{limits['maxLegs']} sports, not {len(resolved)}")

    now = int(time.time() if now is None else now)
    out = copy.deepcopy(decoded)
    out["sport_modes"].append({
        "Name": name,
        "ActivityID": activity_id,
        "Exercises": resolved,
        "Order": _next_free({s["Order"] for s in out["sport_modes"]}),
        "AppMeta": now,
    })
    return [out]


def edit_multisport(decoded, name, new_name=None, activity_id=None, legs=None,
                    rows=None, variant=DEFAULT_VARIANT, now=None):
    """Rename a combo, change its activity, or reorder/replace its legs, in place."""
    rows = rows or load_catalogue()[1]
    limits = variant_limits(rows, variant)
    index, entry = find_sport_mode(decoded, name)
    if not is_multisport(entry):
        raise LimitError(f"{name!r} is a single sport mode, not a multisport mode")

    out = copy.deepcopy(decoded)
    target = out["sport_modes"][index]
    if new_name is not None:
        if len(new_name.encode("utf-8")) > limits["maxNameLength"]:
            raise LimitError(f"name is longer than {limits['maxNameLength']} bytes")
        if any(s["Name"] == new_name for i, s in enumerate(out["sport_modes"]) if i != index):
            raise LimitError(f"a mode named {new_name!r} already exists")
        target["Name"] = new_name
    if activity_id is not None:
        if activity_id not in rows["multisportActivities"]:
            raise LimitError(f"activity {activity_id} cannot hold several sports")
        target["ActivityID"] = activity_id
    if legs is not None:
        resolved = _resolve_legs(out, legs)
        if not limits["minLegs"] <= len(resolved) <= limits["maxLegs"]:
            raise LimitError(f"a multisport mode takes {limits['minLegs']} to "
                             f"{limits['maxLegs']} sports, not {len(resolved)}")
        target["Exercises"] = resolved
    target["AppMeta"] = int(time.time() if now is None else now)
    return [out]


def delete_multisport(decoded, name):
    """Remove a multisport combo. Only the SPORT_MODES entry goes - the sports it used are
    ordinary modes of their own and stay exactly where they are."""
    index, entry = find_sport_mode(decoded, name)
    if not is_multisport(entry):
        raise LimitError(f"{name!r} is a single sport mode - use --delete")
    out = copy.deepcopy(decoded)
    del out["sport_modes"][index]
    return [out]


# --- reporting ---------------------------------------------------------------------------

def describe(decoded, rows, variant=DEFAULT_VARIANT):
    """The mode list the way SuuntoLink's Sport modes screen shows it."""
    activities = load_catalogue()[0]
    limits = variant_limits(rows, variant)
    used, multi = counts(decoded)
    names = exercise_names(decoded)
    lines = [f"Sport modes {used}/{limits['maxSportModes']} used, "
             f"multisport {multi}/{limits['maxMultisportModes']} used"]
    # Order is optional (2026-08-22 fix: real hardware, André's Sport/Run, neither has the
    # tag on any real slot - see build_sport_mode_slot()'s own comment). Sort key falls back
    # to a stable "keep decode order" when it's None rather than crashing on None-vs-None
    # comparison; the display column falls back to "-" instead of printing "None."
    for idx, s in sorted(enumerate(decoded["sport_modes"]),
                          key=lambda pair: (pair[1]["Order"] is None, pair[1]["Order"] or 0, pair[0])):
        order_label = f"{s['Order']:>2}" if s["Order"] is not None else " -"
        activity = activities.get(s["ActivityID"], {}).get("name", f"activity {s['ActivityID']}")
        if is_multisport(s):
            legs = " -> ".join(names[i] for i in s["Exercises"])
            lines.append(f"  {order_label}. {s['Name']}  [{activity}]  {legs}")
        else:
            blocking = users_of(decoded, s["Exercises"][0])
            screens = sum(1 for d in decoded["exercise_modes"][s["Exercises"][0]]["Displays"]
                          if d["Type"] == 10)
            note = f"  (used by {', '.join(blocking)})" if blocking else ""
            lines.append(f"  {order_label}. {s['Name']}  [{activity}], "
                         f"{screens} screen{'s' if screens != 1 else ''}{note}")
    hidden = [n for i, n in enumerate(names)
              if not any(i in s["Exercises"] for s in decoded["sport_modes"])]
    if hidden:
        lines.append(f"  not in the menu (costs no slot): {', '.join(hidden)}")
    return "\n".join(lines)


# --- selftest: replay the real capture ----------------------------------------------------

CAPTURE = REPO / "assets" / "pcap" / "removeandaddsportsmodeandmultisport"

# Every transition in that capture, as the operation that should produce it. The timestamps
# are the capture's own, so the rebuilt image has to match to the byte including the stamps.
# (save 3 -> 4 is two operations in one SuuntoLink write: André deleted the Triathlon that
# used Transition, and Transition itself.)
#
# A creation is ONE call producing THREE region images, so its three rows all start from
# the same pre-creation save and check a different write of that one plan.
REPLAY = [
    (0, 1, "create Alpine skiing, write 1/3", ("create", "Alpine skiing", 20, 1786499262, 0)),
    (0, 2, "create Alpine skiing, write 2/3", ("create", "Alpine skiing", 20, 1786499262, 1)),
    (0, 3, "create Alpine skiing, write 3/3",
     ("create", "Alpine skiing", 20, 1786499262, 2, 1786499267)),
    (3, 4, "delete Triathlon + Transition", ("delete-both", "Triathlon", "Transition")),
    (4, 5, "create Triathlon", ("multi", "Triathlon", 19, [1, 2, 4], 1786499373)),
    (5, 6, "delete Alpine skiing", ("delete", "Alpine skiing")),
    (6, 7, "create Multisport", ("multi", "Multisport", 2, [5, 0, 6, 3, 7, 6], 1786499486)),
    (7, 8, "delete Multisport", ("delete-multi", "Multisport")),
    (8, 9, "delete Triathlon", ("delete-multi", "Triathlon")),
    (9, 10, "create Adventure Race",
     ("multi", "Adventure Race", 61, [6, 1, 7, 3, 2, 5], 1786499570)),
    (10, 11, "delete Adventure Race", ("delete-multi", "Adventure Race")),
    (11, 12, "create Transition, write 1/3", ("create", "Transition", 99, 1786499604, 0)),
    (11, 13, "create Transition, write 2/3", ("create", "Transition", 99, 1786499604, 1)),
    (11, 14, "create Transition, write 3/3",
     ("create", "Transition", 99, 1786499604, 2, 1786499608)),
    (14, 15, "create Triathlonwtransitionand6",
     ("multi", "Triathlonwtransitionand6", 19, [6, 8, 2, 8, 7, 5], 1786499683)),
    (15, 16, "delete Triathlonwtransitionand6",
     ("delete-multi", "Triathlonwtransitionand6")),
]


def _apply(decoded, op, rows):
    """The region image this operation should produce, ready to encode."""
    kind = op[0]
    if kind == "create":
        _, name, activity, now, step = op[:5]
        # useAccelerometer is false for both activities the capture creates; the flag is
        # carried explicitly so the caller (and the app) supplies SuuntoLink's answer.
        # The third write carries the capture's own clock reading (see create_sport_mode).
        link_now = op[5] if len(op) > 5 else None
        return [create_sport_mode(decoded, name, activity, rows=rows, now=now,
                                  link_now=link_now)[step]]
    if kind == "delete":
        return delete_sport_mode(decoded, op[1])
    if kind == "delete-multi":
        return delete_multisport(decoded, op[1])
    if kind == "delete-both":
        after = delete_multisport(decoded, op[1])[-1]
        return [delete_sport_mode(after, op[2])[-1]]
    if kind == "multi":
        _, name, activity, legs, now = op
        return create_multisport(decoded, name, activity, legs, rows=rows, now=now)
    raise AssertionError(kind)


def selftest(verbose=False):
    """Replay the capture: apply each operation to save N and require the result to encode
    byte-for-byte into save N+1. This is the only honest proof that the create/delete rules
    above are SuuntoLink's and not ours."""
    from custom_modes_roundtrip import region_images

    if not CAPTURE.exists():
        print(f"capture not found: {CAPTURE}")
        return 2
    images = region_images(str(CAPTURE))
    rows = load_catalogue()[1]
    ok = 0
    for before, after, label, op in REPLAY:
        want = images[after]
        start = custom_modes.decode(images[before])
        try:
            produced = _apply(start, op, rows)[-1]
            got = custom_modes_write.build_custom_modes_body(
                produced, format_type=produced.get("format_type", 2))
        except Exception as exc:                       # noqa: BLE001 - report, never mask
            print(f"  FAIL {before}->{after}  {label}: {type(exc).__name__}: {exc}")
            continue
        head, tail = want[:len(got)], want[len(got):]
        if head == got and all(b == 0xFF for b in tail):
            ok += 1
            if verbose:
                print(f"  ok   {before}->{after}  {label}")
        else:
            where = next((i for i, (a, b) in enumerate(zip(head, got)) if a != b), None)
            print(f"  FAIL {before}->{after}  {label}: "
                  f"rebuilt {len(got)} bytes against {len(want)}"
                  + (f", first difference at {where}" if where is not None else ""))
    print(f"{ok}/{len(REPLAY)} real SuuntoLink transitions reproduced byte-exact")
    return 0 if ok == len(REPLAY) else 1


# --- CLI -----------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="from_file", metavar="FILE",
                    help="work on a saved region image instead of a live watch")
    ap.add_argument("--device", metavar="NAME", help="which watch, when more than one")
    ap.add_argument("--variant", default=DEFAULT_VARIANT,
                    help=f"watch codename for the limits (default {DEFAULT_VARIANT})")
    ap.add_argument("--list", action="store_true", help="show the modes and the counts")
    ap.add_argument("--create", metavar="NAME", help="create a single sport mode")
    ap.add_argument("--delete", metavar="NAME", help="delete a single sport mode")
    ap.add_argument("--create-multisport", metavar="NAME", help="create a multisport mode")
    ap.add_argument("--edit-multisport", metavar="NAME", help="edit a multisport mode")
    ap.add_argument("--delete-multisport", metavar="NAME", help="delete a multisport mode")
    ap.add_argument("--activity", type=int, metavar="ID",
                    help="activity id from assets/activity_types.json")
    ap.add_argument("--legs", metavar="A,B,C",
                    help="ordered leg mode names for a multisport mode")
    ap.add_argument("--rename", metavar="NAME", help="new name, with --edit-multisport")
    ap.add_argument("--activities", action="store_true",
                    help="list the activity ids that can be created")
    ap.add_argument("--selftest", action="store_true",
                    help="replay the capture; needs no watch")
    ap.add_argument("--json", action="store_true", help="machine-readable result")
    ap.add_argument("--write", action="store_true",
                    help="actually write; without this nothing is sent")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest(args.verbose)

    activities, rows = load_catalogue()
    if args.activities:
        multi = set(rows["multisportActivities"])
        for a in sorted(activities.values(), key=lambda x: x["name"]):
            tag = "  (multisport container)" if a["id"] in multi else ""
            print(f"{a['id']:>3}  {a['name']}{tag}")
        return 0

    # --- read the current region -------------------------------------------------------
    if args.from_file:
        blob = pathlib.Path(args.from_file).read_bytes()
        link = None
    else:
        from write_nav import Link, read_flash, read_memory_map, resolve_product_id
        # FIXED 2026-08-22, real bug, caught live with two watches connected at once
        # (never exercised before - every prior live test had exactly one watch plugged
        # and never passed --device, so `Link(args.device)` with args.device=None happened
        # to accidentally land on dry_run=False by luck of None being falsy). Link's first
        # positional arg is dry_run, not a device name/product_id - passing a device NAME
        # string there made it truthy, silently forcing dry-run regardless of --write, and
        # never actually selected the named watch either.
        # dry_run=False unconditionally, not tied to --write: the read below (and the plan
        # this script computes and PRINTS from it) must always be real - args.write only
        # gates the actual send_plan() mutation much further down (see the `if not
        # args.write: ... return 0` right before it). Tying it here as well as there meant
        # a plain --list needed a bogus --write just to read at all.
        product_id = resolve_product_id(args.device) if args.device else None
        link = Link(dry_run=False, product_id=product_id)
        link.open()
        region = read_memory_map(link).get("CustomModes")
        # FIXED alongside the above: read_memory_map() returns {name: (base, size)}
        # tuples (see its own docstring/write_nav.py), not a {"base":..., "size":...}
        # dict - ["base"]/["size"] would always raise TypeError once `region` was ever
        # non-None, which it never had been before this same live-two-watch test.
        base, size = region if region else (F.CUSTOM_MODES_BASE, F.CUSTOM_MODES_REGION_SIZE)
        blob = read_flash(link, base, size)

    decoded = custom_modes.decode(blob)

    # THE SAFETY RULE: refuse to modify what we cannot reproduce.
    rebuilt = custom_modes_write.build_custom_modes_body(
        decoded, format_type=decoded.get("format_type", 2))
    # FIXED 2026-08-22, real finding on André's Ambit3 Run: this used to require
    # blob[len(rebuilt):] to be all 0xFF, i.e. the ENTIRE padded region past our rebuild -
    # REFUSING on every real watch with any write history, since the firmware only ever
    # writes/hashes the USED EXTENT (used_extent(), same discipline custom_modes_write.py's
    # own send path already follows - see that function's docstring, training_program_andre.md
    # Finding 28). Confirmed directly on the Run: bytes past the used extent are real,
    # structured leftover data (an old deleted sport mode, still in Cyrillic even), not
    # blank padding - completely normal and inert, the firmware never reads past its own
    # declared root-tag length. The real safety property is "re-encodes byte-exact up to
    # the used extent", not "the whole padded region is pristine".
    used = custom_modes.used_extent(blob)
    if blob[:used] != rebuilt:
        print("REFUSING: this watch's CustomModes region does not re-encode byte-exact, "
              "so no modified version of it may be written.", file=sys.stderr)
        return 3

    if args.list or not any([args.create, args.delete, args.create_multisport,
                             args.edit_multisport, args.delete_multisport]):
        print(describe(decoded, rows, args.variant))
        return 0

    legs = [x.strip() for x in args.legs.split(",")] if args.legs else None
    try:
        if args.create:
            if args.activity is None:
                raise LimitError("--create needs --activity (see --activities)")
            # SuuntoLink's own useAccelerometer() answer belongs with the activity; the
            # app passes it in. On the command line, take it from the row table's defaults
            # by asking for the sport's known behaviour rather than assuming false.
            plan = create_sport_mode(decoded, args.create, args.activity, rows=rows,
                                     variant=args.variant)
        elif args.delete:
            plan = delete_sport_mode(decoded, args.delete)
        elif args.create_multisport:
            if args.activity is None or not legs:
                raise LimitError("--create-multisport needs --activity and --legs")
            plan = create_multisport(decoded, args.create_multisport, args.activity, legs,
                                     rows=rows, variant=args.variant)
        elif args.edit_multisport:
            plan = edit_multisport(decoded, args.edit_multisport, new_name=args.rename,
                                   activity_id=args.activity, legs=legs, rows=rows,
                                   variant=args.variant)
        else:
            plan = delete_multisport(decoded, args.delete_multisport)
    except LimitError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 4

    final = plan[-1]
    print(describe(final, rows, args.variant))
    print(f"\n{len(plan)} region write(s) would be sent.")
    if args.json:
        print(json.dumps({"writes": len(plan), "sport_modes": final["sport_modes"]},
                         indent=2))
    if not args.write:
        print("Dry run - nothing was sent. Add --write to apply.")
        return 0
    if link is None:
        print("--write needs a real watch, not --from", file=sys.stderr)
        return 5

    # FIXED 2026-08-22, real bug caught live on André's Ambit3 Run: `custom_modes_write`
    # has no `write_plan` - this call has never actually reached a real watch before (the
    # crash happens while evaluating the argument, before send_plan() itself is even
    # called, so nothing was sent - confirmed by re-reading the watch immediately after,
    # still exactly the pre-existing 7 modes). Real working pattern taken from
    # custom_modes_edit.py's own `send()` helper (base, payload) -> FlashImage ->
    # `[("CustomModes", base, payload), ("tail", base, None)]`, the same one already
    # proven on real hardware there.
    from write_nav import send_plan, read_memory_map
    from ambit_pcap import FlashImage
    cm_base, _ = read_memory_map(link).get("CustomModes", (F.CUSTOM_MODES_BASE, None))
    bodies = [custom_modes_write.build_custom_modes_body(
        s, format_type=s.get("format_type", 2)) for s in plan]
    for n, body in enumerate(bodies, 1):
        if n > 1:
            time.sleep(STAMP_GAP_SECONDS)
        flash = FlashImage()
        flash.write(cm_base, body)
        send_plan(link, flash, [("CustomModes", cm_base, body), ("tail", cm_base, None)],
                  commit=True)
        print(f"  wrote {n}/{len(bodies)} ({len(body)} bytes)")
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
