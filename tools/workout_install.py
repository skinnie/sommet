#!/usr/bin/env python3
"""Installs a compiled Suunto App (workout.py's --compile output, or any entry from
SuuntoLink's bundled catalog) onto the watch: appends it into the Apps flash region, and wires
it into one exercise mode's display so it actually shows up - the writer half of
`docs/training_program_andre.md`'s Finding 13/15 GUI -> generator -> compiler -> *install* pipeline.

DRY-RUN BY DEFAULT, same convention as every other writer in this project: without --write
nothing is emitted, only the exact bytes are logged.

**What's verified and what isn't.** The tag-level encoding is verified byte-exact against a real
SuuntoLink install: the tag order inside an exercise mode is `SETTINGS, APP_META, DISPLAYS,
RULES`. The app-placement mechanism was CORRECTED 2026-08-09 (docs/training_program_andre.md Finding
44) after diffing a real, rendering SuuntoLink Couch-to-5K install against the clean pre-state:
SuuntoLink makes an app render by **appending the app's rule-engine slot (51/52/53 =
FT_RULE_ENGINE_0/1/2) as a `DISP_FIELD_SHORTCUT` on a display field** - so that row cycles
between its normal value(s) and the app - NOT by setting a field's `Type` to 51 (the old approach
here, which only ever showed "--"). The slot number = the app's 0-based position in the mode's
RULES list. `RuleIdx` is the app's own index in the Apps region. Installing an app does NOT touch
`HrHigh`/`HrLow`/`IntTimerCount` (the old "reset them" behaviour was a misreading and is removed).
`install_app_into_mode()` now reproduces a real SuuntoLink install byte-for-byte (only the
time-based APP_META timestamps differ).

**2026-08-08 (docs/training_program_andre.md Finding 25): the Apps-region format is now SOLVED**,
from 4 real USBPcap captures of SuuntoLink actually installing apps (`assets/ambit3 pcap/v2/`)
plus a real live 11-entry region - see `apps.py`'s module docstring for the full derivation.
The whole region is a self-describing directory (`[u16 num_entries][u16 unknown2][u32
entry_offset]*N[u32 total_length]`, then fixed 32-byte `[header 3B][name 29B]` blocks + magic +
binary per entry) that gets **entirely rewritten** on every single install, not appended to -
this project's own writer used to append one raw entry with a guessed 12-byte header and no
directory at all, which is almost certainly the real cause of the "app error" chased across
Findings 16-19 (a structurally wrong format, not a wrong constant in an otherwise-right one).
Rewritten below to build the real directory + all existing entries + the new one, every time.
One field remains genuinely unknown: the per-entry `marker` byte - consistent for a given app
across every real capture checked, but its rule isn't determined, so new entries here use `0`
as a placeholder rather than a guess with false confidence.

**Apps region hash mode now confirmed too**: computed SHA256 over just the real written bytes
of the `appstopscreensunrisunset` capture (4300 bytes) matches the real captured `0x0b18` tail
hash exactly - `HASH_WRITTEN` (hash of only the written bytes, not the whole padded region) was
already what `ambit_format.py` assumed, now independently confirmed rather than reasoned by
analogy.

**Still NOT verified**: whether `CustomModes` needs a `CMD_NAV_COMMIT` afterward - still a
reasoned inference by analogy with Routes/Waypoints, not confirmed against these Apps captures
(that's a CustomModes question, not an Apps one). Use `--backup-to` before a real `--write` and
keep `--restore` in mind.

    ./tools/workout_install.py compiled.json --mode 2 --display 0 --field 0 --write
    ./tools/workout_install.py --restore backup_CustomModes.bin --write

Offline planning against a real capture, no watch needed (`Link` opens no connection at all
in dry-run, so reading the watch specifically requires --write; these let you dry-run the
*planning* logic against a previously-saved dump instead):

    ./tools/workout_install.py compiled.json --mode 2 --display 0 --field 0 \\
        --from-apps dump_Apps.bin --from-custom-modes dump_CustomModes.bin
"""

import argparse
import json
import struct
import sys
import time

import apps
import ambit_format as F
import custom_modes as cm
from ambit_pcap import FlashImage
from build_route import emit_packs
from write_nav import (CMD_DEVICE_INFO, Link, check_memory_map, read_flash,
                        read_memory_map, send_plan)


def build_apps_region(existing_entries, compiled, entry_type=0):
    """Builds a full Apps-region write: the real directory format (apps.py's module
    docstring, Finding 25) - `[u16 num_entries][u16 count^0x02][u32 entry_offset]*N
    [u32 total_length]`, then one `[3-byte header][29-byte name]` block + magic + binary per
    entry, back to back. Confirmed the whole region is rewritten (directory + every existing
    entry) on every real install, not appended to - `existing_entries` should be
    `apps.decode(current_apps_bytes)` of what's there now, and the new one is always placed
    last (matching every real capture checked: new entries append to the end of the list, not
    inserted).

    Both formerly-unknown fields are now SOLVED (2026-08-09, Finding 29, via openambit's
    serialize_app_data prior art + verification against all 26 real entries this project has):
    - the header's second u16 is `num_entries ^ 0x02`, not an opaque value.
    - the per-entry `marker` byte is `apps.entry_checksum(binary)` (XOR of MAGIC+binary ^ len),
      no longer a guess left at 0.
    `activityId` is read from `compiled` (falls back to 0) - confirmed to match the app's real
    catalog activityId in every sample.

    Real edge case guarded against here that a real client apparently doesn't guard against
    (found live, Finding 25): a name over apps.NAME_LEN (29) bytes wouldn't leave room for its
    own null terminator and would run into the next field - truncated here instead."""
    binary = bytes(compiled["binary"])
    # The on-watch entry layout is [header][name][IAMRULE magic][bytecode]; `binary` here must be
    # the bytecode WITHOUT the magic, since we prepend apps.MAGIC below. But some sources already
    # carry the 8-byte magic inside their `binary` (notably SuuntoLink's own catalog index.json -
    # docs/training_program_andre.md Finding 45): passing that straight through produced a DOUBLE magic
    # and an 8-byte-shifted, corrupt bytecode that installed cleanly but always rendered "--".
    # Strip a leading magic defensively so every source lands byte-identical to a real install.
    if binary[:len(apps.MAGIC)] == apps.MAGIC:
        binary = binary[len(apps.MAGIC):]
    activity_id = compiled.get("activityId", 0) & 0xFF
    marker = apps.entry_checksum(binary)
    # CORRECTED 2026-08-22: was iso-8859-15 - real hardware (André's French Ambit3 Sport)
    # proved the watch sends/expects UTF-8 for name fields, see ambit_format.py's
    # encode_name() header comment and apps.py's own decode fix.
    name = compiled.get("name", "App").encode("utf-8", "replace")[:apps.NAME_LEN - 1]
    name = name.decode("utf-8", "ignore").encode("utf-8")
    name_field = name + b"\0" * (apps.NAME_LEN - len(name))
    # Entry header byte 0 = the rule TYPE, from BinaryAreaAppsConverter::typeMapping in the
    # Movescount Android app's libkomposti (init(): map["generic"]=0, map["guidance"]=1).
    # apps.py called this "reserved=0" because every entry it ever decoded was a generic App.
    # 1 = a native guidance WORKOUT (the [Next]-3s WORKOUT menu), the whole point of --as-workout.
    new_entry_bytes = (bytes([entry_type & 0xFF, activity_id, marker]) + name_field
                       + apps.MAGIC + binary)

    all_bytes = [e["_raw_block"] for e in existing_entries] + [new_entry_bytes]
    num_entries = len(all_bytes)
    table_len = 4 + 4 * (num_entries + 1)

    offsets = []
    cursor = table_len
    for block in all_bytes:
        offsets.append(cursor)
        cursor += len(block)
    total_length = cursor

    header = struct.pack("<HH", num_entries, num_entries ^ 0x02) + struct.pack(
        f"<{num_entries + 1}I", *offsets, total_length)
    return header + b"".join(all_bytes)


def apps_entries_with_raw_blocks(apps_dump):
    """apps.decode() plus each entry's own raw bytes (header+name+magic+binary), needed to
    reassemble existing entries verbatim into a fresh build_apps_region() call without
    re-deriving marker/activityId by hand."""
    entries = apps.decode(apps_dump)
    for e in entries:
        start = e["entry_offset"]
        end = e["magic_offset"] + len(apps.MAGIC) + len(e["binary"])
        e["_raw_block"] = apps_dump[start:end]
    return entries


def _read_tag(data, offset):
    return struct.unpack_from("<HH", data, offset)


def _walk_children(data, content, end):
    """Yields (tag_id, tag_content_offset, tag_len, tag_offset) for each direct child."""
    cursor = content
    while cursor < end:
        tag_id, length = _read_tag(data, cursor)
        yield tag_id, cursor + 4, length, cursor
        cursor = cursor + 4 + length


def _find_mode(data, mode_index):
    root_id, root_len = _read_tag(data, 0)
    if root_id != cm.DEVICE_CUSTOM:
        raise ValueError(f"expected DEVICE_CUSTOM at offset 0, got 0x{root_id:x}")
    for tag_id, content, length, offset in _walk_children(data, 4, 4 + root_len):
        if tag_id == cm.EXERCISE_MODES:
            em_content, em_len, em_offset = content, length, offset
            idx = 0
            for m_id, m_content, m_len, m_offset in _walk_children(data, content, content + length):
                if m_id == cm.EXERCISE_MODES_MODE:
                    if idx == mode_index:
                        return {"em_offset": em_offset, "em_content": em_content,
                                "em_len": em_len, "mode_offset": m_offset,
                                "mode_content": m_content, "mode_len": m_len}
                    idx += 1
    raise ValueError(f"mode index {mode_index} not found")


SPORT_MODE_APP_LIMIT = 5  # The real, manual-documented limit (3.35 Suunto Apps: "up to five
                          # Suunto Apps to each sport mode") - per MODE, not a whole-watch total.
                          # RULE_ENGINE_SLOTS (a small global slot-count model) is retired
                          # entirely as of docs/training_program_andre.md Finding 23: it was based on
                          # custom_modes.py's FIELD_TYPES dictionary and Finding 17's now-explained
                          # "RuleIdx=3 -> app error" test, neither of which was ever a real cap.


def next_rule_idx(current_apps_bytes):
    """RuleIdx = the new entry's own 0-based position in the Apps region's physical entry
    listing - NOT a small enumerated global slot. Confirmed 2026-08-08 (Finding 23) against a
    real 11-entry Apps region cross-referenced with CustomModes: all 6 real RuleIdx assignments
    in use (0, 1, 7, 8, 9, 10) matched their app's own index in apps.decode()'s entry list
    exactly, no exceptions - e.g. the app at apps.decode()[7] is the one wired with RuleIdx=7.
    This supersedes the earlier "lowest free global slot" model entirely - RuleIdx grows with
    the whole region's install history, it doesn't reset or get reused when a low index frees
    up (that had only ever been tested with 3 entries, too few to see the real pattern)."""
    return len(apps.decode(current_apps_bytes))


def check_mode_app_limit(decoded, mode_index):
    """The real limit is per sport mode (SPORT_MODE_APP_LIMIT), not a global count. Raises
    before install_app_into_mode() would silently add a 6th app past what the manual documents
    as supported."""
    existing = len(decoded["exercise_modes"][mode_index]["Rules"])
    if existing >= SPORT_MODE_APP_LIMIT:
        raise RuntimeError(
            f"mode[{mode_index}] already has {existing} Suunto Apps assigned - the manual's "
            f"documented limit is {SPORT_MODE_APP_LIMIT} apps per sport mode")


# The three rule-engine slots an app can be placed onto: FT_RULE_ENGINE_0/1/2 (custom_modes.py's
# FIELD_TYPE_LABELS "Suunto App Slot 1/2/3"). Corrected 2026-08-09 (Finding 44): these values are
# added to a display field's SHORTCUT list, not written into a field's Type. A mode can have up to
# SPORT_MODE_APP_LIMIT (5) apps *assigned* (RULES tags) but only 3 can be *placed* on a field,
# because only 3 engine slots exist. The slot an app feeds = its 0-based position in the mode's
# RULES list, so the Nth app placed uses APP_SLOT_TYPES[N].
APP_SLOT_TYPES = (51, 52, 53)  # FT_RULE_ENGINE_0, _1, _2 in that fixed order


def _tag(tag_id, content):
    return struct.pack("<HH", tag_id, len(content)) + content


def _grow_region(data, insert_at, blob):
    """Insert `blob` at insert_at, keeping the region's total size constant by consuming trailing
    0xFF padding. CustomModes is a fixed-size flash buffer of which only the used extent is ever
    written, so growth must come out of the pad, never off the end."""
    original_size = len(data)
    tail = bytes(data[original_size - len(blob):])
    if tail.count(0xFF) != len(tail):
        raise RuntimeError(
            f"not enough 0xFF padding left in CustomModes to grow by {len(blob)} bytes - "
            "refusing to silently discard real trailing data")
    data[insert_at:insert_at] = blob
    del data[original_size:]


def _bump(data, len_offset, delta):
    old = struct.unpack_from("<H", data, len_offset)[0]
    struct.pack_into("<H", data, len_offset, old + delta)


def add_rule_to_mode(data, mode_index, rule_idx, log_rule=False):
    """Append an EXERCISE_MODES_RULE {RuleIdx, UseRule=1, LogRule} to the mode - creating the
    mode's EXERCISE_MODES_RULES container if it has none yet. RuleIdx is the app's 0-based
    position in the Apps region (see next_rule_idx).

    log_rule=True revives the Movescount behaviour where the app's per-sample output is
    recorded into the Move (it surfaces as ruleoutput1..5 in the log - see app_logging.py).
    Default False, matching every real SuuntoLink install capture."""
    rule = _tag(cm.EXERCISE_MODES_RULE, struct.pack("<HHH", rule_idx, 1, 1 if log_rule else 0))
    loc = _find_mode(data, mode_index)
    mc, ml = loc["mode_content"], loc["mode_len"]
    existing = [c for c in _walk_children(data, mc, mc + ml) if c[0] == cm.EXERCISE_MODES_RULES]
    if existing:
        _, r_content, r_len, r_offset = existing[0]
        _grow_region(data, r_content + r_len, rule)
        delta = len(rule)
        _bump(data, r_offset + 2, delta)          # RULES container grows
    else:
        rules = _tag(cm.EXERCISE_MODES_RULES, rule)
        _grow_region(data, mc + ml, rules)         # RULES sits at the end of the mode
        delta = len(rules)
    _bump(data, loc["mode_offset"] + 2, delta)
    _bump(data, loc["em_offset"] + 2, delta)
    _bump(data, 2, delta)                          # DEVICE_CUSTOM root


def set_app_meta(data, mode_index):
    """Stamp the mode's EXERCISE_MODES_APP_META timestamps (SuuntoLink updates them on every app
    change), creating the tag after SETTINGS only if the mode has none - matching how a real
    SuuntoLink install touches an already-stamped mode in place rather than duplicating it."""
    t1 = int(time.time())
    loc = _find_mode(data, mode_index)
    mc, ml = loc["mode_content"], loc["mode_len"]
    existing = [c for c in _walk_children(data, mc, mc + ml) if c[0] == cm.EXERCISE_MODES_APP_META]
    if existing:
        struct.pack_into("<II", data, existing[0][1], t1, t1 + 2)  # update in place, no resize
        return
    settings = next(c for c in _walk_children(data, mc, mc + ml)
                     if c[0] == cm.EXERCISE_MODES_SETTING_NAME_LEN64)
    _, s_content, s_len, _ = settings
    app_meta = _tag(cm.EXERCISE_MODES_APP_META, struct.pack("<II", t1, t1 + 2))
    _grow_region(data, s_content + s_len, app_meta)
    delta = len(app_meta)
    _bump(data, loc["mode_offset"] + 2, delta)
    _bump(data, loc["em_offset"] + 2, delta)
    _bump(data, 2, delta)


def add_app_shortcut_to_field(data, mode_index, display_index, field_index, shortcut_value):
    """THE mechanism SuuntoLink uses to make an app render (docs/training_program_andre.md Finding 44):
    APPEND the app's rule-engine slot (51/52/53 = FT_RULE_ENGINE_0/1/2) as an
    EXERCISE_MODES_DISP_FIELD_SHORTCUT to the chosen display field, so that row cycles between its
    normal value(s) and the app on button presses. This does NOT touch the field's Type if it
    already has a shortcut - the old "set the field Type to 51" approach never rendered
    ("--").

    Finding 53 addendum (2026-08-12): if the field has ZERO existing shortcuts, its Type MUST
    be zeroed at the same time. Confirmed against 6 independent real SuuntoLink transitions in
    running2fromcreateandthen1to7 (e.g. Type 8->0, 197->0, 160->0, 40->0, always exactly when
    Shortcuts goes from [] to non-empty; never seen with the old Type left in place). Every
    real field this project has ever read either has Type=0 with a Shortcuts list, or a
    nonzero Type with none - "nonzero Type + Shortcuts" never occurs. This project's installer
    had never exercised the empty-shortcuts case before - Findings 44-46's Fixed100/Temperature
    tests both happened to land on fields that already had a shortcut - and produced exactly
    that never-real combination, which the firmware rejected with "connect to Moveslink" on a
    real Running/display0/field1 install (that field's Type was 28, no prior shortcuts)."""
    loc = _find_mode(data, mode_index)
    mc, ml = loc["mode_content"], loc["mode_len"]
    displays = next(c for c in _walk_children(data, mc, mc + ml)
                     if c[0] == cm.EXERCISE_MODES_DISPLAYS)
    _, disp_content, disp_len, displays_offset = displays
    target = None
    d_idx = 0
    for d_id, d_content, d_len, d_offset in _walk_children(data, disp_content,
                                                            disp_content + disp_len):
        if d_id != cm.EXERCISE_MODES_DISPLAY:
            continue
        if d_idx == display_index:
            f_idx = 0
            for f_id, f_content, f_len, f_offset in _walk_children(data, d_content,
                                                                    d_content + d_len):
                if f_id != cm.EXERCISE_MODES_DISP_FIELD:
                    continue
                if f_idx == field_index:
                    target = (f_offset, f_content, f_len, d_offset)
                    break
                f_idx += 1
            break
        d_idx += 1
    if target is None:
        raise ValueError(f"display {display_index} field {field_index} not found in this mode")
    f_offset, f_content, f_len, d_offset = target

    has_shortcut = False
    setting_content = None
    for c_id, c_content, c_len, c_offset in _walk_children(data, f_content, f_content + f_len):
        if c_id == cm.EXERCISE_MODES_DISP_FIELD_SHORTCUT:
            has_shortcut = True
        elif c_id == cm.EXERCISE_MODES_DISP_FIELD_SETTING:
            setting_content = c_content
    if not has_shortcut:
        # First shortcut on this field - zero its Type in place (Finding 53 addendum above).
        # In-place overwrite, same length, so it never affects any offset used below.
        struct.pack_into("<H", data, setting_content + 2, 0)

    sc_tag = _tag(cm.EXERCISE_MODES_DISP_FIELD_SHORTCUT, struct.pack("<H", shortcut_value))
    _grow_region(data, f_content + f_len, sc_tag)   # append shortcut at end of this field
    delta = len(sc_tag)
    # every len-offset below sits before the insertion point, so none shifts.
    _bump(data, f_offset + 2, delta)          # DISP_FIELD
    _bump(data, d_offset + 2, delta)          # DISPLAY
    _bump(data, displays_offset + 2, delta)   # DISPLAYS
    _bump(data, loc["mode_offset"] + 2, delta)
    _bump(data, loc["em_offset"] + 2, delta)
    _bump(data, 2, delta)                      # DEVICE_CUSTOM root


def install_app_into_mode(custom_modes_bytes, mode_index, display_index, field_index, rule_idx,
                          as_workout=False, log_rule=False):
    """Returns new CustomModes region bytes with the app wired into
    (mode_index, display_index, field_index) the way SuuntoLink really does it (Finding 44):
    add the RULE, stamp APP_META, and APPEND the app's engine slot as a DISP_FIELD_SHORTCUT on
    the target field. It does NOT overwrite any field's Type and does NOT touch
    HrHigh/HrLow/IntTimerCount - those were bogus side effects of the old, non-rendering approach.

    as_workout=True (Finding 39 experiment): add the RULE + APP_META but do NOT place a shortcut
    on any field - testing whether a present-but-unplaced guidance rule shows in the browsable
    WORKOUT options menu. display_index/field_index are ignored in this mode."""
    data = bytearray(custom_modes_bytes)

    # The engine slot this app feeds = its 0-based position in the mode's RULES list, i.e. how
    # many rules the mode already has. SuuntoLink's Couch-to-5K (Walk's first app) -> slot 51.
    decoded = cm.decode(bytes(data))
    n_existing = len(decoded["exercise_modes"][mode_index]["Rules"])

    add_rule_to_mode(data, mode_index, rule_idx, log_rule=log_rule)
    set_app_meta(data, mode_index)

    # Place the engine-slot shortcut on a data field when a field is given. For a guidance
    # WORKOUT (as_workout, byte0=1) this is ALSO wanted: the guidance entry makes it appear in
    # the [Next]-3s WORKOUT menu, but our community-compiled binary renders its step output via
    # prefix/postfix on a display field (Finding 61's "Warm --s"), not the native guidance
    # display - so without a field placement there is nothing to SEE while the workout runs.
    # as_workout with no field => menu only, unwired (the original Finding 39 experiment).
    place_field = (display_index is not None and field_index is not None)
    if not as_workout or place_field:
        if n_existing >= len(APP_SLOT_TYPES):
            raise RuntimeError(
                f"mode already has {n_existing} apps placed on fields - only "
                f"{len(APP_SLOT_TYPES)} engine slots ({APP_SLOT_TYPES}) exist to place onto")
        shortcut_value = APP_SLOT_TYPES[n_existing]   # 51/52/53 = FT_RULE_ENGINE_0/1/2
        add_app_shortcut_to_field(data, mode_index, display_index, field_index, shortcut_value)
    return bytes(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("compiled", nargs="?", help="a workout.py --compile --out JSON file")
    ap.add_argument("--mode", type=int, help="EXERCISE_MODES_MODE index to install into"
                     " (see custom_modes.py's printed order)")
    ap.add_argument("--display", type=int, help="which of that mode's displays (0-based)")
    ap.add_argument("--field", type=int, help="which field on that display (0-based, 0=top)")
    ap.add_argument("--write", action="store_true",
                     help="actually emits; without this option nothing is sent")
    ap.add_argument("--backup-to", metavar="FILE",
                     help="save the current CustomModes region here before writing")
    ap.add_argument("--restore", metavar="FILE",
                     help="write a raw CustomModes dump back verbatim (e.g. a --backup-to"
                          " file) instead of installing an app")
    ap.add_argument("--from-apps", metavar="FILE",
                     help="use this raw Apps dump instead of reading the watch (offline"
                          " testing - a real connection can't be read from without --write)")
    ap.add_argument("--from-custom-modes", metavar="FILE",
                     help="use this raw CustomModes dump instead of reading the watch")
    ap.add_argument("--apps-only", action="store_true",
                     help="append to the Apps region only - skip CustomModes entirely (e.g."
                          " when it's already correctly wired from a previous run)")
    ap.add_argument("--as-workout", action="store_true",
                     help="EXPERIMENT (Finding 39): add the rule to the mode's RULES list but"
                          " do NOT pin it to a display field - testing whether an unwired"
                          " guidance rule appears in the browsable WORKOUT options menu."
                          " --display/--field not required with this.")
    ap.add_argument("--no-log", action="store_true",
                     help="do NOT record this app's output into the Move (LogRule=0). By"
                          " default an installed app IS logged (LogRule=1) so its output can"
                          " appear as a graph in analysis - the standing rule that any app"
                          " activated in a sport mode gets logged. Pass this to opt a specific"
                          " app out. See app_logging.py to toggle it on an already-installed app.")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", action="store_true",
                     help="print one final JSON line summarizing the result - for"
                          " backend/server.py. Human-readable progress lines still print"
                          " before it (server.py's own \"last JSON-parseable line\""
                          " convention, same as every other tool here)")
    args = ap.parse_args()

    if not args.restore and args.compiled is None:
        ap.error("either --restore FILE, or a compiled JSON")
    if not args.restore and not args.apps_only and not args.as_workout and (
            args.mode is None or args.display is None or args.field is None):
        ap.error("--mode/--display/--field are required unless --apps-only or --restore")
    if args.as_workout and args.mode is None:
        ap.error("--as-workout needs --mode (the sport mode to offer the workout in)")

    # Real, 2026-08-09: considered changing this to match settings_write.py's own pattern
    # (Link opened for real unconditionally, only the actual write call gated on --write) so
    # a backend caller could get a real live-watch rehearsal without --write. Backed out -
    # this file's own CMD_DEVICE_INFO/memory-map check and its --restore/--from-apps/
    # --from-custom-modes offline paths all currently depend on dry_run's existing "no real
    # device touched at all" meaning in ways that would need real hardware to verify safely
    # for every flag combination, not something to change blind on real flash-write code.
    # Left as the original author had it - see backend/server.py's own _handle_apps_install
    # for how the rehearsal problem is solved instead (never calling this tool at all until
    # confirm:true, building the preview from already-safe read-only endpoints instead).
    link = Link(dry_run=not args.write, verbose=args.verbose)
    if args.write:
        print("!! REAL WRITE requested")
        link.open()
    else:
        print("dry-run mode: not a byte will be emitted")

    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    check_memory_map(read_memory_map(link))

    # Poka-yoke (2026-09-02): this tool installs DISPLAY-FIELD apps. It also has an
    # --as-workout mode that flags the Apps entry as a workout (byte0=1) - but on Ambit3 that
    # is NOT how a workout reaches the [Next]-3s -> WORKOUT menu: a whole session proved such
    # an entry installs cleanly yet never lists. The working path is guided_workout.py (adds
    # the guidance display the firmware actually scans). Refuse the trap combination.
    if args.write and args.as_workout:
        pid = getattr(link, "opened_product_id", None)
        from write_nav import is_ambit3
        if is_ambit3(pid):
            sys.exit(
                "--as-workout on an Ambit3 does not produce a WORKOUT-menu entry (hardware-"
                "confirmed: it installs but never lists). Use guided_workout.py instead - it "
                "adds the guidance display the firmware scans. See ambit_finch_movescount_"
                "workout_capture memory / docs Finding 39.")

    if args.restore:
        with open(args.restore, "rb") as f:
            new_custom_modes = f.read()
        if len(new_custom_modes) != F.CUSTOM_MODES_REGION_SIZE:
            sys.exit(f"'{args.restore}' is {len(new_custom_modes)} bytes, expected "
                     f"{F.CUSTOM_MODES_REGION_SIZE}")
        # Write ONLY the used BXML extent, not the full padded region, and no 0x0b04
        # (Finding 28): this is exactly what real SuuntoLink does and what the watch's cold-
        # boot hash validation expects. Writing the full region produced a wrong hash and
        # 'err:62' on every mode after a restart.
        extent = cm.used_extent(new_custom_modes)
        payload = new_custom_modes[:extent]
        flash = FlashImage()
        flash.write(F.CUSTOM_MODES_BASE, payload)
        layout = [("CustomModes", F.CUSTOM_MODES_BASE, payload),
                  ("tail", F.CUSTOM_MODES_BASE, None)]
        send_plan(link, flash, layout, commit=False)
        if args.write:
            # Read back and confirm the region landed byte-for-byte (see the app-install path's
            # own note on the 2026-08-25 garbled write). --restore is idempotent, so on a
            # mismatch we just report and the caller re-runs - re-writing the same region is the
            # recovery.
            readback = read_flash(link, F.CUSTOM_MODES_BASE, len(payload),
                                  label="CustomModes (verify)")
            if readback != payload:
                n = min(len(readback), len(payload))
                first = next((i for i in range(n) if readback[i] != payload[i]), n)
                sys.exit(f"CustomModes read-back MISMATCH at 0x{first:x} - the write did not land. "
                         "Nothing else changed; re-run the same --restore to retry.")
            print("CustomModes verified: read-back matches.")
        print(f"\n{'wrote' if args.write else 'would write'} {extent} used bytes"
              f" (of {len(new_custom_modes)}) to CustomModes (restore)")
        return 0

    with open(args.compiled) as f:
        compiled = json.load(f)

    if args.from_apps:
        with open(args.from_apps, "rb") as f:
            current_apps = f.read()
    elif not link.dry_run:
        # Real, 2026-08-09: apps.read_apps_region()'s own probe-first fast path (the
        # region's real directory has an exact total_length boundary - see that
        # function's own docstring), not a blind full-200,000-byte read.
        current_apps = apps.read_apps_region(link)
    else:
        ap.error("reading the watch needs --write (Link opens no connection in dry-run) - "
                 "pass --from-apps for an offline plan against a real capture instead")

    current_custom_modes = None
    if not args.apps_only:
        if args.from_custom_modes:
            with open(args.from_custom_modes, "rb") as f:
                current_custom_modes = f.read()
        elif not link.dry_run:
            current_custom_modes = read_flash(
                link, F.CUSTOM_MODES_BASE, F.CUSTOM_MODES_REGION_SIZE, label="CustomModes")
        else:
            ap.error("reading the watch needs --write (Link opens no connection in dry-run) - "
                     "pass --from-custom-modes for an offline plan against a real capture"
                     " instead")

        if args.backup_to:
            with open(args.backup_to, "wb") as f:
                f.write(current_custom_modes)
            print(f"backed up current CustomModes to {args.backup_to}")

    existing_apps_entries = apps_entries_with_raw_blocks(current_apps)
    new_region = build_apps_region(existing_apps_entries, compiled,
                                   entry_type=1 if args.as_workout else 0)
    if len(new_region) > F.APPS_REGION_SIZE:
        sys.exit(f"refusing to write: the rebuilt Apps region would be {len(new_region)} "
                 f"bytes, past the end of the {F.APPS_REGION_SIZE}-byte Apps region")
    print(f"Apps region: {len(existing_apps_entries)} existing entr"
          f"{'y' if len(existing_apps_entries) == 1 else 'ies'} + 1 new "
          f"(name={compiled.get('name')!r}) = {len(new_region)} bytes total")

    flash = FlashImage()
    flash.write(F.APPS_BASE, new_region)
    apps_layout = [("Apps region", F.APPS_BASE, new_region),
                   ("tail", F.APPS_BASE, None)]

    rule_idx = mode_name = None
    if args.apps_only:
        print("--apps-only: leaving CustomModes untouched")
        send_plan(link, flash, apps_layout, commit=False)
    else:
        decoded_modes = cm.decode(current_custom_modes)
        if not args.as_workout:
            check_mode_app_limit(decoded_modes, args.mode)
        rule_idx = next_rule_idx(current_apps)
        mode_name = decoded_modes["exercise_modes"][args.mode]["Settings"]["Name"]
        wiring = "UNWIRED (as-workout experiment)" if args.as_workout \
            else f"display[{args.display}] field[{args.field}]"
        print(f"CustomModes: mode[{args.mode}]={mode_name!r} {wiring} -> RuleIdx={rule_idx}")

        new_custom_modes = install_app_into_mode(
            current_custom_modes, args.mode, args.display, args.field, rule_idx,
            as_workout=args.as_workout, log_rule=not args.no_log)

        # Refuse to send anything violating the Type/Shortcut invariant (Finding 53's
        # "connect to Moveslink" root cause) - checked here, on the actual bytes about to be
        # written, so any future bug in this class is caught before it ever reaches the watch
        # rather than after a real-hardware failure.
        violation = cm.check_field_type_shortcut_invariant(new_custom_modes)
        if violation:
            mi, mname, di, fi, f = violation
            sys.exit(f"refusing to write: mode[{mi}]={mname!r} display[{di}] field[{fi}] "
                     f"would have Type={f.get('Type')} with Shortcuts={f.get('Shortcuts')} - "
                     "violates the real Type/Shortcut invariant (Finding 53), would risk "
                     "'connect to Moveslink' on the watch")

        # Write ONLY the used BXML extent (Finding 28) - see custom_modes.used_extent(). The
        # full-region write was the confirmed cause of 'err:62' on every mode after a restart.
        cm_extent = cm.used_extent(new_custom_modes)
        cm_payload = new_custom_modes[:cm_extent]
        flash2 = FlashImage()
        flash2.write(F.CUSTOM_MODES_BASE, cm_payload)
        cm_layout = [("CustomModes", F.CUSTOM_MODES_BASE, cm_payload),
                     ("tail", F.CUSTOM_MODES_BASE, None)]

        # write the app itself first, then wire it in - so a failure partway through never
        # leaves CustomModes pointing at an app that isn't actually there.
        # commit=False for BOTH (no CMD_NAV_COMMIT / 0x0b04): confirmed 2026-08-09
        # (docs/training_program_andre.md Finding 27) against all 4 real SuuntoLink app-install
        # captures in assets/ambit3 pcap/v2/ - not one of them EVER sends 0x0b04 for the
        # Apps or CustomModes regions. 0x0b04 is specifically the *navigation database*
        # commit (routes/waypoints); firing it after an Apps/CustomModes write was this
        # project's own addition, and is the single command we sent that real SuuntoLink
        # never does. The real per-region finalization is the 0x0b18 tail itself (a SHA256
        # of the written span, verified byte-exact against every real tail). Sending the
        # spurious nav-commit is the strongest suspect for the "connect to Moveslink" /
        # needs-restart / clock-reset state seen on real hardware after installs.
        send_plan(link, flash, apps_layout, commit=False)
        send_plan(link, flash2, cm_layout, commit=False)

        # Read CustomModes back and confirm the watch actually holds the bytes we built.
        # install_app_into_mode()'s output is verified to decode clean, so a mismatch here is a
        # transport/concurrent-access glitch, not a builder bug - seen once (2026-08-25): a
        # CustomModes write came back 54 bytes shifted with 0x00 runs, scrambling every mode
        # after the target. Catch it and roll back to the pre-write region (current_custom_modes,
        # already in memory) using the proven single-region write path, so the watch is NEVER
        # left corrupted. The Apps write is additive/self-describing and re-run-safe, so the app
        # entry can stay; only CustomModes is restored.
        if args.write:
            readback = read_flash(link, F.CUSTOM_MODES_BASE, len(cm_payload),
                                  label="CustomModes (verify)")
            if readback != cm_payload:
                n = min(len(readback), len(cm_payload))
                first = next((i for i in range(n) if readback[i] != cm_payload[i]), n)
                print(f"!! CustomModes read-back MISMATCH at 0x{first:x} - the write did not land "
                      f"correctly. Rolling back to the pre-write region.")
                old_payload = current_custom_modes[:cm.used_extent(current_custom_modes)]
                rb = FlashImage()
                rb.write(F.CUSTOM_MODES_BASE, old_payload)
                send_plan(link, rb, [("CustomModes", F.CUSTOM_MODES_BASE, old_payload),
                                     ("tail", F.CUSTOM_MODES_BASE, None)], commit=False)
                check = read_flash(link, F.CUSTOM_MODES_BASE, len(old_payload),
                                   label="CustomModes (rollback verify)")
                if check == old_payload:
                    sys.exit("CustomModes write was corrupted; rolled back to the pre-write "
                             "region and verified. Nothing bad left on the watch - re-run to retry.")
                sys.exit("CustomModes write was corrupted AND the rollback read-back did not "
                         "match either - restore manually from your --backup-to file.")
            print("CustomModes verified: read-back matches the built region byte-for-byte.")

    total = sum(len(payload) for _, payload, _ in link.sent)
    print(f"\n{len(link.sent)} messages, {total} payload bytes"
          + ("" if args.write else " — nothing was emitted"))

    if args.json:
        print(json.dumps({
            "ok": True, "written": bool(args.write), "ruleIdx": rule_idx,
            "modeName": mode_name, "name": compiled.get("name"),
            "messages": len(link.sent), "payloadBytes": total,
        }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
