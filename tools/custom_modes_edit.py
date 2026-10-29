#!/usr/bin/env python3
"""Structural edits to a sport mode's displays: add, remove, retype, and set a row's values.

DRY-RUN BY DEFAULT: without --write nothing is sent, only what would change is printed.

Why this exists separately from custom_modes_field_write_test.py: that one patches bytes in
place, which works for swapping one value of the same size but cannot add or remove
anything. Adding a display changes every length above it, so the only honest way is to
decode the region into a tree, edit the tree, and re-encode the whole thing - which is safe
only because tools/custom_modes_roundtrip.py proves the encoder reproduces 118/118 real
SuuntoLink region images byte-for-byte.

    ./tools/custom_modes_edit.py --mode Running                        # show its displays
    ./tools/custom_modes_edit.py --mode Running --add-display 3-row
    ./tools/custom_modes_edit.py --mode Running --remove-display 4
    ./tools/custom_modes_edit.py --mode Running --set-type 2 graph
    ./tools/custom_modes_edit.py --mode Running --set-row 2 Bottom 88,87
    ./tools/custom_modes_edit.py --mode Running --add-display 3-row --write

THE SAFETY RULE, enforced on every real write: the region read off the watch is decoded and
re-encoded FIRST, and if that does not come back byte-identical the write is refused. If we
cannot reproduce what is already on the watch, we have no business writing a modified
version of it - the failure would be silent and the region is the one place a bad write
makes a sport mode unusable.
"""

import argparse
import copy
import pathlib
import sys
import time

import ambit_format as F
import custom_modes
import custom_modes_write
import row_bridge

_CATALOGUE = row_bridge.load_rows()
from write_nav import Link, read_flash, read_memory_map, resolve_product_id, send_plan

# The four display types SuuntoLink offers, from its own ambit/sport_mode.js DisplayType
# (Data1Field / Data2Fields / Data3Fields / SimpleLineChart) matched to the template ids
# seen changing in `running2fromcreateandthen1to7` when André cycled a display through all
# four (saves 10/13/16). getNumRows() there gives the row count for each.
DISPLAY_TYPES = {
    "1-row": (262, 1, "PID_RUNNER_GPS_TEMPLATE_6"),
    "2-row": (261, 2, "PID_RUNNER_GPS_TEMPLATE_5"),
    "3-row": (260, 3, "PID_RUNNER_GPS_TEMPLATE_4"),
    "graph": (257, 3, "PID_RUNNER_GPS_TEMPLATE_1G_GRAPH"),
}
# A user-editable display always carries Type 10; every other value is a built-in the watch
# owns (compass, navigation, map...). Checked against all 11,494 displays in the captures -
# it agrees with custom_modes.system_tail_length()'s own answer on every one.
USER_DISPLAY_TYPE = 10
ROW_NAMES = ("Top", "Center", "Bottom")


# What SuuntoLink puts in a display's rows, by type. Taken from its own
# ambit/sport_mode.js createDisplay(): 1-row = Distance; 2-row = Distance + Duration;
# 3-row = Distance, Speed, Duration; graph = Altitude (+ its own two extra rows). The
# ValueFormat names map to watch field ids by correlating those defaults with the real
# type-changes in `running2fromcreateandthen1to7` (saves 10/13/16):
#   Distance -> 10 (FT_DISTANCE)   Speed -> 11 (FT_VELOCITY)
#   Duration ->  5 (FT_TIMER)      Altitude -> 6 (FT_ALTI)
# The bottom row stores its single value as Type 0 with a one-entry Shortcuts list, not as
# Type 5 - that is the shape SuuntoLink actually writes, confirmed in those same saves.
_DEFAULT_ROWS = {
    "1-row": [(10, [])],
    "2-row": [(10, []), (0, [5])],
    "3-row": [(10, []), (11, []), (0, [5])],
    # Byte-observed on the real 3-row -> graph switch (save 16): a graph replaces its rows
    # outright rather than carrying anything over.
    "graph": [(6, []), (32, []), (5, [])],
}


def retype_display(disp, kind):
    """Change a display's type the way SuuntoLink does.

    Rows are addressed by ROLE, not by position - which is why the watch names them
    Top/Center/Bottom (sport_mode.js FieldId). On a real 1-row -> 2-row switch SuuntoLink
    kept the existing row and gave the NEW bottom row the Duration default; on 2-row ->
    3-row it kept the bottom row's Duration, moved the old value up, and gave the new middle
    row the Speed default. So: keep the bottom row if both types have one, keep as many of
    the upper rows as still exist, and fill anything new from the per-type defaults above.

    A graph is the exception and replaces its rows outright (save 16).
    """
    tpl, rows, sym = DISPLAY_TYPES[kind]
    disp["Template"], disp["TemplateName"] = tpl, sym
    defaults = _DEFAULT_ROWS[kind]

    if kind == "graph":
        # A graph replaces its rows outright rather than carrying anything over (save 16).
        chosen = [dict(Type=t, Shortcuts=list(sc)) for t, sc in defaults]
    else:
        old = disp["Fields"]
        # A 1-row display has no bottom row at all - sport_mode.js gives its single row the
        # id "Center", and 2-row is Center+Bottom, 3-row is Top+Center+Bottom. So the bottom
        # row only exists from two rows up.
        old_has_bottom = len(old) >= 2
        old_bottom = old[-1] if old_has_bottom else None
        old_upper = old[:-1] if old_has_bottom else list(old)

        new_has_bottom = rows >= 2
        upper_slots = rows - 1 if new_has_bottom else rows

        chosen = []
        for i in range(upper_slots):
            if i < len(old_upper):
                chosen.append(old_upper[i])          # kept, filling from the top in order
            else:
                t, sc = defaults[i] if i < len(defaults) else (0, [])
                chosen.append(dict(Type=t, Shortcuts=list(sc)))
        if new_has_bottom:
            if old_bottom is not None:
                chosen.append(old_bottom)            # the bottom row is anchored
            else:
                t, sc = defaults[rows - 1]
                chosen.append(dict(Type=t, Shortcuts=list(sc)))

    disp["Fields"] = [
        {"Index": i,
         "IndexName": ROW_NAMES[i] if i < 3 else f"Row{i}",
         "Type": src["Type"],
         "Shortcuts": list(src.get("Shortcuts") or [])}
        for i, src in enumerate(chosen)
    ]


def display_type_of(display):
    for name, (tpl, _rows, _sym) in DISPLAY_TYPES.items():
        if display["Template"] == tpl:
            return name
    return None


def user_displays(mode):
    """(index, display) for the displays the owner may edit, in order."""
    return [(i, d) for i, d in enumerate(mode["Displays"]) if d.get("Type") == USER_DISPLAY_TYPE]


def find_mode(decoded, name):
    for m in decoded["exercise_modes"]:
        if (m.get("Settings") or {}).get("Name") == name:
            return m
    known = [(m.get("Settings") or {}).get("Name") for m in decoded["exercise_modes"]]
    raise SystemExit(f"no sport mode called {name!r} - this watch has: "
                     + ", ".join(repr(k) for k in known if k))


def new_display(type_key):
    """A fresh display of `type_key`, pre-filled the way SuuntoLink pre-fills one.

    A new display is NOT born empty. Every display SuuntoLink added in
    `running2fromcreateandthen1to7` (saves 1, 4, 7, 19 - one per type) came out carrying the
    same per-template defaults `_DEFAULT_ROWS` already holds for retyping: 1-row Distance;
    2-row Distance + a Duration bottom; 3-row Distance, Speed and a Duration bottom; graph
    the 6/32/5 triple. An empty display is not a state the watch is ever given, so we do not
    invent one.

    Row count comes from the type (sport_mode.js getNumRows) and each row's Index is its
    position - the watch names them Top/Center/Bottom."""
    tpl, rows, _sym = DISPLAY_TYPES[type_key]
    defaults = _DEFAULT_ROWS[type_key]
    return {
        "Template": tpl,
        "TemplateName": DISPLAY_TYPES[type_key][2],
        "Type": USER_DISPLAY_TYPE,
        "Fields": [{"Index": i, "IndexName": ROW_NAMES[i] if i < 3 else f"Row{i}",
                    "Type": defaults[i][0], "Shortcuts": list(defaults[i][1])}
                   for i in range(rows)],
    }


# Floor for the gap between the touch write and the commit write, in seconds. SuuntoLink's
# own gap measures 2, 3, 2 and 2 seconds across the four edits in
# running2fromcreateandthen1to7 - but it is elapsed work, not a wait: between the two writes
# it re-reads the settings (0x1100), the memory map (0x0b21, 33 times) and a slice of flash
# (0x0b17). So we do the same re-read and only pad up to this floor if we finished sooner.
STAMP_GAP_SECONDS = 2


def stamp_plan(pristine, edited, mode_name, now=None):
    """How SuuntoLink stages a save on a mode that carries APP_META, or None if it doesn't.

    CORRECTED 2026-08-11. This function first assumed EXERCISE_MODES_APP_META was *app*
    metadata present only on modes with Suunto Apps, because on André's watch only Running2
    had it and Running2 has 5 apps. That was a coincidence of which modes had been edited
    recently. He then edited Cycling and Pool swimming in SuuntoLink - both with zero apps -
    and both gained an APP_META. So SuuntoLink adds one to ANY mode it saves, and the tag's
    name is narrower than its behaviour: it is a per-mode last-edited stamp.

    Consequence: a mode that has none today gets one CREATED here, rather than skipped, since
    that is what SuuntoLink does and matching it is the standing decision. Observed shape when
    SuuntoLink creates one fresh (Cycling, 03:06:50/03:06:53): both stamps set, a few seconds
    apart, exactly like the update case.

    SuuntoLink saves in two full-region writes about two seconds apart
    (running2fromcreateandthen1to7, saves 3+4, 6+7, 9+10):

        write 1 - Timestamp1 = now, structure UNCHANGED   ("touch")
        write 2 - Timestamp2 = now, structure CHANGED     ("commit")

    Andre's read of it: a touch-then-commit shape is what you design for old flash that might
    lose power mid-write, and this format has to serve Ambit 1 and 2 as well. We reproduce it
    literally rather than collapsing it into one write, because we do not know what reads
    these stamps, and the only implementation known to work does it this way.

    Returns (touch_structure, commit_structure); both are complete decoded regions ready to
    encode."""
    target = find_mode(edited, mode_name)
    now = int(time.time() if now is None else now)
    if not isinstance(target.get("AppMeta"), dict):
        # No stamp yet: create one, the way SuuntoLink does for a mode it has not saved
        # before. Both writes then proceed exactly as for a mode that already had one.
        target["AppMeta"] = {"Timestamp1": now, "Timestamp2": now}
        find_mode(pristine, mode_name)["AppMeta"] = {"Timestamp1": now, "Timestamp2": now}

    touch = pristine
    find_mode(touch, mode_name)["AppMeta"]["Timestamp1"] = now
    # The commit write keeps the Timestamp1 the touch just wrote, and moves Timestamp2 on.
    target["AppMeta"]["Timestamp1"] = now
    target["AppMeta"]["Timestamp2"] = now + STAMP_GAP_SECONDS
    return touch, edited


def max_displays(variant):
    """SuuntoLink's own getMaxDisplays() answer. 8 for the Ambit3 family and Kailash, 4 for
    Traverse/Traverse Alpha (custom_modes._MAX_DISPLAYS_BY_VARIANT already carries this)."""
    return custom_modes.max_displays_for_variant(variant) \
        if hasattr(custom_modes, "max_displays_for_variant") else 8


def show(mode):
    print(f"  {(mode.get('Settings') or {}).get('Name')!r}")
    for n, (idx, d) in enumerate(user_displays(mode)):
        kind = display_type_of(d) or f"template {d['Template']}"
        print(f"    display {n} ({kind})")
        for f in d["Fields"]:
            row = ROW_NAMES[f["Index"]] if f["Index"] < 3 else f"Row{f['Index']}"
            if f["Type"] == 0 and f.get("Shortcuts"):
                vals = ", ".join(_label(s) for s in f["Shortcuts"])
            elif f["Type"] == 0:
                vals = "(empty)"
            else:
                vals = _label(f["Type"])
            print(f"      {row:7} {vals}")
    built_in = len(mode["Displays"]) - len(user_displays(mode))
    print(f"    (+{built_in} built-in displays the watch owns)")


def _label(type_id):
    name = custom_modes.FIELD_TYPES.get(type_id, f"0x{type_id:04x}")
    return custom_modes.field_type_label(name)


def apply_edits(mode, args):
    """Mutates `mode` in place. Returns a list of human-readable descriptions."""
    done = []
    ui = user_displays(mode)

    if args.add_display:
        if len(ui) >= 8:
            raise SystemExit(f"this mode already has {len(ui)} displays and the watch's "
                             "own limit is 8 (SuuntoLink getMaxDisplays)")
        # A new display goes after the last user one, before the built-ins the watch owns.
        insert_at = (ui[-1][0] + 1) if ui else 0
        mode["Displays"].insert(insert_at, new_display(args.add_display))
        done.append(f"added a {args.add_display} display as display {len(ui)}")

    if args.remove_display is not None:
        if args.remove_display >= len(ui):
            raise SystemExit(f"display {args.remove_display} does not exist - this mode has "
                             f"{len(ui)} editable displays (0..{len(ui) - 1})")
        real = ui[args.remove_display][0]
        mode["Displays"].pop(real)
        done.append(f"removed display {args.remove_display}")

    if args.set_type:
        which, kind = args.set_type
        which = int(which)
        if which >= len(ui):
            raise SystemExit(f"display {which} does not exist ({len(ui)} editable)")
        if kind not in DISPLAY_TYPES:
            raise SystemExit(f"unknown display type {kind!r} - one of: "
                             + ", ".join(DISPLAY_TYPES))
        disp = ui[which][1]
        retype_display(disp, kind)
        done.append(f"display {which} is now {kind}")

    if args.set_row:
        which, row, values = args.set_row
        which = int(which)
        if which >= len(ui):
            raise SystemExit(f"display {which} does not exist ({len(ui)} editable)")
        disp = ui[which][1]
        row_idx = ROW_NAMES.index(row) if row in ROW_NAMES else int(row)
        if row_idx >= len(disp["Fields"]):
            raise SystemExit(f"display {which} has no {row} row (it has "
                             f"{len(disp['Fields'])} rows)")
        ids = [int(v, 0) for v in values.split(",") if v.strip()]
        if not ids:
            raise SystemExit("no values given - a row needs at least one")

        # Check the choice against SuuntoLink's own menu for this activity/display/row before
        # touching anything. Writing a value the watch does not offer for a given sport is
        # how a display ends up showing "--", and the menu is generated from SuuntoLink's own
        # module (assets/sportmode_rows.json) rather than assumed.
        row_name = ROW_NAMES[row_idx].upper()
        multi = row_bridge.row_is_multi_value(disp.get("Template"), row_name)
        if len(ids) > 1 and not multi:
            raise SystemExit(
                f"the {ROW_NAMES[row_idx]} row of this display holds one value, not "
                f"{len(ids)}. Only the bottom row of a 2-field or 3-field display cycles "
                f"between several values.")
        limit = _CATALOGUE["limits"]["maxValuesPerMultiRow"]
        if len(ids) > limit:
            raise SystemExit(f"{len(ids)} values given - a row holds at most {limit}")

        allowed = row_bridge.allowed_field_ids(
            _CATALOGUE, mode["Settings"].get("ActivityID"), disp.get("Template"), row_name)
        if allowed is not None:
            rejected = [i for i in ids if i not in allowed]
            if rejected:
                raise SystemExit(
                    "SuuntoLink does not offer "
                    + ", ".join(f"{_label(i)} ({i:#06x})" for i in rejected)
                    + f" on the {ROW_NAMES[row_idx]} row of a "
                    + f"{row_bridge.TEMPLATE_TO_DISPLAY_TYPE.get(disp.get('Template'), '?')} "
                    + f"display for this sport ({mode['Settings'].get('Name')}). "
                    + "Writing a value the watch does not support for a sport is how a "
                    + "display ends up showing '--'.")

        field = disp["Fields"][row_idx]
        if len(ids) == 1:
            # One value is stored as the row's own Type, with no shortcuts - the shape every
            # single-value row in every capture uses.
            field["Type"], field["Shortcuts"] = ids[0], []
        else:
            # Several values live in the Shortcuts list with Type 0. Confirmed on all 29
            # shortcut-carrying rows in the captures, and matching sport_mode.js, whose
            # Bottom row alone carries a nested Fields list.
            field["Type"], field["Shortcuts"] = 0, ids
        done.append(f"display {which} {row} row set to " + ", ".join(_label(i) for i in ids))

    return done



def apply_batch(mode, edits):
    """Apply a list of {"op": ...} edits in order. Same code path as the single-edit flags,
    so there is one implementation of each operation, not two."""
    import types as _types
    done = []
    for e in edits:
        op = e.get("op")
        args = _types.SimpleNamespace(add_display=None, remove_display=None,
                                       set_type=None, set_row=None)
        if op == "add":
            args.add_display = e["type"]
        elif op == "remove":
            args.remove_display = int(e["display"])
        elif op == "setType":
            args.set_type = (str(e["display"]), e["type"])
        elif op == "setRow":
            args.set_row = (str(e["display"]), e["row"],
                            ",".join(str(v) for v in e["values"]))
        else:
            raise SystemExit(f"unknown edit op {op!r}")
        done += apply_edits(mode, args)
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--device", metavar="NAME", help="which watch, when more than one is connected")
    ap.add_argument("--mode", required=True, help="sport mode name, e.g. Running")
    ap.add_argument("--add-display", metavar="TYPE", choices=sorted(DISPLAY_TYPES),
                     help="add a display: " + ", ".join(sorted(DISPLAY_TYPES)))
    ap.add_argument("--remove-display", metavar="N", type=int, help="remove editable display N")
    ap.add_argument("--set-type", nargs=2, metavar=("N", "TYPE"), help="retype display N")
    ap.add_argument("--set-row", nargs=3, metavar=("N", "ROW", "IDS"),
                     help="set display N's ROW (Top/Center/Bottom) to a comma-separated "
                          "list of field type ids, at most 5")
    ap.add_argument("--edits", metavar="JSON",
                     help="a JSON list of edits applied in order and written ONCE, e.g. "
                          "'[{\"op\":\"add\",\"type\":\"3-row\"},"
                          "{\"op\":\"setRow\",\"display\":0,\"row\":\"Bottom\","
                          "\"values\":[88,87]}]'. This is what the desktop app sends: the "
                          "UI stages edits and saves once, the way SuuntoLink does, so a "
                          "session of changes costs one full-region write instead of one "
                          "per click.")
    ap.add_argument("--json", action="store_true", help="machine-readable result")
    ap.add_argument("--write", action="store_true", help="actually write; without this "
                                                          "nothing is sent")
    ap.add_argument("--no-stamp", action="store_true",
                     help="do not touch APP_META. Only affects modes that have Suunto Apps "
                          "installed - app-less modes carry no APP_META and are unaffected "
                          "either way. Default is to stamp it exactly as SuuntoLink does.")
    ap.add_argument("--verbose", action="store_true")
    # Edit a region IMAGE instead of a watch. Added 2026-08-11 for the app's Testing mode,
    # which answers from a real captured region so the whole editor can be exercised with no
    # watch attached - and, just as usefully, so this tool can be tested offline. With
    # --write the edited image is written back to the same file, which is what makes an edit
    # in Testing mode persist the way an edit on a watch does.
    ap.add_argument("--from", dest="from_file", metavar="FILE",
                     help="edit this CustomModes image rather than a connected watch")
    args = ap.parse_args()

    link = None
    if args.from_file:
        region = pathlib.Path(args.from_file).read_bytes()
        # The ceiling is the REGION's size, not the file's: an image holds only the bytes
        # actually used, so using its length would refuse any edit that grows the mode list.
        base, size = F.CUSTOM_MODES_BASE, custom_modes.CUSTOM_MODES_SIZE
    else:
        product_id = resolve_product_id(args.device) if args.device else None
        link = Link(dry_run=False, verbose=args.verbose, product_id=product_id)
        link.open()
        found = read_memory_map(link)
        if "CustomModes" not in found:
            raise SystemExit("this watch declares no CustomModes region (Kailash has none) - "
                              "sport modes are not editable on it")
        base, size = found["CustomModes"]
        region = read_flash(link, base, size, label="CustomModes")

    decoded = custom_modes.decode(region)

    # --- the safety rule ---------------------------------------------------------------
    rebuilt = custom_modes_write.build_custom_modes_body(
        decoded, format_type=decoded.get("format_type", 2))
    # Only the body has to match. What lies past it deliberately does NOT: flash is not
    # erased before a write, so any write that SHRINKS the mode list (removing a display)
    # leaves the tail of the longer previous version sitting behind the new end. Observed
    # on hardware - after adding two displays to Running and removing them again, the first
    # 8428 bytes were byte-identical to the pre-test backup and 53 stale bytes trailed it.
    # Those bytes are unreachable: the parser walks the tag tree and stops at its end, which
    # is exactly why the watch and SuuntoLink both read the region back correctly. Requiring
    # 0xFF there would make the tool refuse to write to any watch we had ever shrunk a mode
    # on - a false alarm about our own previous, correct write.
    if region[:len(rebuilt)] != rebuilt:
        raise SystemExit(
            "REFUSING TO WRITE: this watch's own CustomModes region does not survive a "
            "decode/re-encode unchanged, so a modified version cannot be trusted either. "
            "Run tools/custom_modes_roundtrip.py and fix the encoder before editing. "
            f"(region {len(region)} bytes, rebuilt {len(rebuilt)})")
    stale = sum(1 for b in region[len(rebuilt):] if b != 0xFF)
    if stale and args.verbose:
        print(f"  ({stale} stale bytes past the end of the mode list, left by an earlier "
              f"longer version - unreachable, the watch stops at the tag tree's end)")

    mode = find_mode(decoded, args.mode)
    if not any([args.add_display, args.remove_display is not None, args.set_type,
                args.set_row, args.edits]):
        show(mode)
        return 0

    # The pre-edit structure, kept for the APP_META touch write below (see stamp_plan).
    pristine = copy.deepcopy(decoded)

    if not args.json:
        print("before:")
        show(mode)
    if args.edits:
        import json as _json
        changes = apply_batch(mode, _json.loads(args.edits))
    else:
        changes = apply_edits(mode, args)
    fmt = decoded.get("format_type", 2)
    staged = None if args.no_stamp else stamp_plan(pristine, decoded, args.mode)
    if staged:
        touch_body = custom_modes_write.build_custom_modes_body(staged[0], format_type=fmt)
        changes.append("stamped APP_META the way SuuntoLink does (touch write, then commit)")

    if not args.json:
        print("\nafter:")
        show(mode)
        print("\n" + "\n".join("  * " + c for c in changes))

    body = custom_modes_write.build_custom_modes_body(decoded, format_type=fmt)
    if len(body) > size:
        raise SystemExit(f"the edited region is {len(body)} bytes and this watch declares "
                          f"only {size} - refusing to write past the end of it")

    if not args.write:
        if args.json:
            import json as _json
            print(_json.dumps({"ok": True, "wrote": False, "changes": changes,
                               "bytes": len(body), "wasBytes": len(rebuilt)}))
        else:
            print(f"\ndry-run: {len(body)} bytes would be written to CustomModes "
                  f"(was {len(rebuilt)}) - pass --write to send it")
        return 0

    if args.from_file:
        # Same bytes the watch would have received, written where they came from. The
        # round-trip guard above has already proven the encoder reproduces this region.
        pathlib.Path(args.from_file).write_bytes(body)
        if args.json:
            import json as _json
            print(_json.dumps({"ok": True, "wrote": True, "changes": changes,
                               "bytes": len(body)}))
        else:
            print(f"\nwritten to {args.from_file} ({len(body)} bytes)")
        return 0

    from ambit_pcap import FlashImage

    def send(payload):
        flash = FlashImage()
        flash.write(base, payload)
        # No 0x0b04: SuuntoLink never sends it for CustomModes, and it empties the POI lists.
        send_plan(link, flash, [("CustomModes", base, payload), ("tail", base, None)],
                  commit=False)

    if staged:
        # Touch write, then the commit write. The ~2s between SuuntoLink's two writes is not
        # a sleep - between them it re-reads the settings, the memory map (33 times) and a
        # slice of flash, and the gap is just how long that took. So we do the same re-read
        # rather than sleeping: whatever that gap is really for, pacing off real traffic
        # tracks it on hardware we cannot test (the Ambit 1 and 2 run this same format) where
        # a fixed sleep would not. We keep the measured gap only as a floor.
        #
        # What the gap is FOR remains unknown. A watch-side reload was considered and has no
        # evidence: the watch only serves USB from watch mode, so nothing on its display can
        # be observed mid-write, and the "returns to the sports screen" behaviour Andre
        # described is SuuntoLink's own window, not the watch's.
        if not args.json:
            print(f"\n  touch write ({len(touch_body)} bytes), then re-read the watch the "
                  f"way SuuntoLink does, then the commit write")
        send(touch_body)
        started = time.monotonic()
        read_memory_map(link)
        remaining = STAMP_GAP_SECONDS - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)
    send(body)

    # Confirm by reading the region back. The first read can disagree with what we sent even
    # though the write landed - seen once on real hardware, where an immediate re-read
    # mismatched but a second, independent read came back byte-identical to `body`. So a
    # single disagreement is not evidence of a bad write; read again before saying so, and
    # report WHERE it differs rather than just that it did, since "did not confirm" on a
    # flash region is alarming enough to deserve detail.
    confirmed = False
    detail = ""
    for attempt in range(2):
        if attempt:
            time.sleep(1.0)
        after = read_flash(link, base, size, label="CustomModes")
        if after[:len(body)] == body:
            confirmed = True
            break
        first = next((i for i, (a, b) in enumerate(zip(after, body)) if a != b), None)
        detail = (f"read back {len(after)} bytes for {len(body)} sent"
                  + (f"; first difference at offset {first}"
                     f" (watch {after[first]:#04x}, sent {body[first]:#04x})"
                     if first is not None else "; the reply is shorter than what was sent"))

    if args.json:
        import json as _json
        print(_json.dumps({"ok": confirmed, "wrote": confirmed, "changes": changes,
                           "bytes": len(body),
                           "error": None if confirmed else
                                    "the region read back does not match what was sent: "
                                    + detail}))
        return 0 if confirmed else 1
    if confirmed:
        print(f"\nwritten and confirmed by re-read ({len(body)} bytes)")
        return 0
    print("\nWRITE DID NOT CONFIRM: the region read back does not match what was sent")
    print(f"  {detail}")
    print("  Read the region with custom_modes.py before writing again - the watch may hold "
          "a partially written mode list.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
