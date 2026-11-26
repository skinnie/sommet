#!/usr/bin/env python3
"""Ambit1/2 display edits (add, remove, change layout, change a row), without a watch.

Ground truth: SuuntoLink editing André's Ambit2 (assets/pcap/ambit2_suuntolink_settings_
sportmodes.pcap) and his Ambit1 (assets/pcap/2026-08-23-ambit1-suuntolink/zzambit1full.pcap),
git-ignored - skipped where absent. Between two consecutive region writes where SuuntoLink
changed only one mode's displays, the change is read off the two display lists (not typed in
here), turned into the Sport Modes page's edits, and patch_displays() of the first write must
give the second byte for byte, except the display time SuuntoLink also puts in the mode's 0x01FF
record (left as read, like the settings time). Checked here:
  - every display in every captured write has the one record shape patch_displays writes, with
    the user screens (type 10) first;
  - the region after the used bytes is 0xFF in every captured write;
  - each captured add / remove / layout change / row change, on both watches, and the count of
    each, so a change the replay cannot explain fails instead of being skipped;
  - removing a display a capture added gives back the write before the add, on both watches;
  - the Ambit1's own screens by display type (what the desktop now lists) are the ones the old C
    reader found by its common-tail rule, on every captured Ambit1 region and both dumps;
  - the multisport group of the Ambit2 capture (read only);
  - sport_mode_displays_edit(): what it writes, the backup and the read-back;
  - the bytes for a fixed synthetic region, the same FNV-1a as the Android writer's jest test.
Not seen on a real watch: André no longer has an Ambit1 or Ambit2 (routes/03).
"""
import pathlib
import struct
import sys
import tempfile

import legacy_link
import legacy_sport_modes as LSM
from test_legacy_sport_mode_edit import AMBIT1, AMBIT1_DUMP, AMBIT1_LANG, AMBIT2, fnv1a, tlv, writes

LAYOUT_NAMES = {v: k for k, v in LSM.LAYOUT_IDS.items()}
ROW_LABELS = ("Top", "Center", "Bottom")


def modes_of(data):
    """{mode name: (0x0101 body offset, length)} in the region."""
    out = {}
    for tag, body, ln in LSM._children(data, 4, LSM.region_used(data)):
        if tag == LSM.TAG_MODES:
            for t, b, n in LSM._children(data, body, body + ln):
                if t == LSM.TAG_MODE:
                    kids = {k: (kb, kn) for k, kb, kn in LSM._children(data, b, b + n)}
                    if LSM.TAG_SETTINGS in kids:
                        s = kids[LSM.TAG_SETTINGS][0]
                        out[bytes(data[s:s + 16]).split(b"\0")[0].decode("latin-1")] = (b, n)
    return out


def displays_of(data, name):
    """(user displays parsed, all display records, 0x01FF body offset or None) of one mode."""
    b, n = modes_of(data)[name]
    kids = list(LSM._children(data, b, b + n))
    stamp = [kb for kt, kb, kn in kids if kt == 0x01FF and kn == 8]
    disp = [(kb, kn) for kt, kb, kn in kids if kt == LSM.TAG_DISPLAYS]
    if not disp:
        return None, [], stamp[0] if stamp else None
    db, dn = disp[0]
    records = [bytes(data[rb - 4:rb + rn]) for _t, rb, rn in LSM._children(data, db, db + dn)]
    parsed = [LSM.parse_display(r) for r in records]
    user = [p for p in parsed if p and p["type"] == LSM.USER_DISPLAY]
    return user, records, stamp[0] if stamp else None


def check_shapes(runs):
    """Every display parses to the one shape; user screens first; 0xFF after the used bytes."""
    seen = 0
    for data in runs:
        used = LSM.region_used(data)
        assert all(x == 0xFF for x in data[used:]), "non-0xFF byte after the used region"
        for name in modes_of(data):
            user, records, _ = displays_of(data, name)
            if user is None:
                continue                             # a mode SuuntoLink has not given displays yet
            parsed = [LSM.parse_display(r) for r in records]
            assert all(parsed), f"{name}: a display outside the captured shape"
            kinds = [p["type"] for p in parsed]
            assert kinds[:len(user)] == [LSM.USER_DISPLAY] * len(user), (name, kinds)
            assert LSM.USER_DISPLAY not in kinds[len(user):], (name, kinds)
            seen += len(records)
    return seen


def edits_between(old, new):
    """The page's edits that turn the user display list `old` into `new`, or None."""
    if len(new) == len(old):
        out = []
        for i, (a, b) in enumerate(zip(old, new)):
            if a == b:
                continue
            if a["layout"] != b["layout"]:
                rows = [tuple((r, it, tuple(v))) for r, it, v in b["rows"]]
                if tuple(rows) != LSM.NEW_DISPLAY_ROWS[b["layout"]]:
                    return None
                out.append({"op": "setType", "display": i, "type": LAYOUT_NAMES[b["layout"]]})
                continue
            if [r[0] for r in a["rows"]] != [r[0] for r in b["rows"]]:
                return None
            for ra, rb in zip(a["rows"], b["rows"]):
                if ra != rb:
                    out.append({"op": "setRow", "display": i, "row": ROW_LABELS[rb[0]],
                                "values": rb[2] if rb[1] == 0 and rb[2] else [rb[1]]})
        return out
    if len(new) > len(old) and new[:len(old)] == old:
        return [{"op": "add", "type": LAYOUT_NAMES[d["layout"]]} for d in new[len(old):]]
    if len(new) == len(old) - 1:
        for i in range(len(old)):
            if old[:i] + old[i + 1:] == new:
                return [{"op": "remove", "display": i}]
    return None


def masked(before, after, stamp):
    """`after` with the mode's display time (second u32 of 0x01FF) as it was in `before`."""
    out = bytearray(after)
    if stamp is not None:
        out[stamp + 4:stamp + 8] = before[stamp + 4:stamp + 8]
    return bytes(out)


def replay(runs):
    """Replay every captured change to one mode's displays; returns (counts, unexplained)."""
    done, unexplained = {"add": 0, "remove": 0, "setType": 0, "setRow": 0}, []
    for k, (before, after) in enumerate(zip(runs, runs[1:])):
        if before == after:
            continue
        names_b, names_a = modes_of(before), modes_of(after)
        if list(names_b) != list(names_a):
            continue                                 # a mode created or deleted (L3)
        changed = [n for n in names_b if displays_of(before, n)[1] != displays_of(after, n)[1]]
        if not changed:
            continue                                 # settings, group or time writes
        name, = changed
        old, _records, stamp = displays_of(before, name)
        new, _records2, _ = displays_of(after, name)
        b, n = names_b[name]
        a, m = names_a[name]
        others_b = [kt for kt, _kb, _kn in LSM._children(before, b, b + n)]
        others_a = [kt for kt, _kb, _kn in LSM._children(after, a, a + m)]
        if old is None or others_b != others_a:
            continue                                 # a new mode's first displays, an app added
        edits = edits_between(old, new)
        if edits is None:
            unexplained.append(k)
            continue
        got = LSM.patch_displays(before, name, edits)
        assert got == masked(before, after, stamp), (k, name, edits)
        for e in edits:
            done[e["op"]] += 1
    return done, unexplained


def test_ambit2(runs):
    assert check_shapes(runs) > 10000
    done, unexplained = replay(runs)
    # Treino Forção: a graph removed; a 1-row, 2-row, 3-row and graph added; Top, Center and
    # the multi-value row changed.
    assert done == {"add": 4, "remove": 1, "setType": 0, "setRow": 3}, done
    assert not unexplained, unexplained
    # Removing what a capture added gives back the write before it (the add's own inverse).
    for k in (12, 15, 18, 21):
        user, _r, stamp = displays_of(runs[k + 1], "Treino Forção")
        got = LSM.patch_displays(runs[k + 1], "Treino Forção", [{"op": "remove", "display": len(user) - 1}])
        assert got == masked(runs[k + 1], runs[k], stamp), k


def test_ambit1(runs):
    assert check_shapes(runs) > 5000
    done, unexplained = replay(runs)
    # Running: 3-row -> 1-row and -> 2-row; a graph, a 3-row, then 2-row + 1-row + graph in one
    # save; Top, Center and the multi-value row changed.
    assert done == {"add": 5, "remove": 0, "setType": 2, "setRow": 3}, done
    assert not unexplained, unexplained
    for k, count in ((11, 1), (13, 1), (15, 3)):
        user, _r, stamp = displays_of(runs[k + 1], "Running")
        edits = [{"op": "remove", "display": len(user) - 1 - j} for j in range(count)]
        assert LSM.patch_displays(runs[k + 1], "Running", edits) == masked(runs[k + 1], runs[k], stamp), k


def c_reader_screens(data):
    """ambit_legacy_cli ambit1-sport-mode-read's rule: a mode's screens are its displays minus
    the longest run of layouts common to the end of every mode."""
    layouts = {}
    for name in modes_of(data):
        user, records, _ = displays_of(data, name)
        if user is not None:
            layouts[name] = [LSM.parse_display(r)["layout"] for r in records]
    tail = 0
    if len(layouts) > 1:
        shortest = min(len(v) for v in layouts.values())
        while tail < shortest and len({v[-tail - 1] for v in layouts.values()}) == 1:
            tail += 1
    return {name: len(v) - tail for name, v in layouts.items()}


def test_ambit1_screens_rule(runs):
    regions = list(runs) + writes(AMBIT1_LANG, 512)
    regions += [AMBIT1_DUMP.read_bytes(), AMBIT1_DUMP.with_name(
        AMBIT1_DUMP.name.replace("BASELINE", "AFTER").replace("120650", "135818")).read_bytes()]
    checked = 0
    for data in regions:
        for name, count in c_reader_screens(data).items():
            assert count == len(displays_of(data, name)[0]), name
            checked += 1
    assert checked > 800, checked


def test_ambit2_groups(runs):
    groups = LSM.sport_mode_groups(runs[31])
    multi = [g for g in groups if g["isMultisport"]]
    assert [(g["name"], g["activityId"], g["legNames"]) for g in multi] == [
        ("Triathlon", 19, ["Cycling", "Running", "Openwater swim"])], multi
    assert len(groups) == 10 and all(len(g["legs"]) == 1 for g in groups if not g["isMultisport"])


def test_refusals(runs):
    data = runs[31]
    for edits in ([{"op": "add", "type": "4-row"}],
                  [{"op": "remove", "display": 9}],
                  [{"op": "setRow", "display": 0, "row": "Top", "values": [1, 2]}],
                  [{"op": "setRow", "display": 0, "row": "Bottom", "values": [1, 2, 3, 4, 5, 6]}],
                  [{"op": "setRow", "display": 0, "row": "Top", "values": [0]}],
                  [{"op": "setRow", "display": 2, "row": "Center", "values": [5]}],   # 1-row
                  [{"op": "add", "type": "graph"}] * 3,                    # 6 + 3 > 8
                  [{"op": "move", "display": 0}]):
        try:
            LSM.patch_displays(data, "Treino Forção", edits)
            raise AssertionError(f"{edits} accepted")
        except ValueError:
            pass
    one = LSM.patch_displays(data, "Adventure Racing", [])
    assert one == data
    try:
        LSM.patch_displays(data, "Adventure Racing", [{"op": "remove", "display": 0}])
        raise AssertionError("the last display removed")
    except ValueError:
        pass


# ─── a synthetic region, the same one android/src/services/__tests__/LegacyDisplays.test.ts builds
def blob(size, name):
    b = bytearray(((i * 7 + 3) & 0xFF) for i in range(size))
    b[0:16] = name.encode("latin-1").ljust(16, b"\0")[:16]
    return bytes(b)


def synthetic(size):
    """Two modes, each with user screens and two built-in views (compass 273/4, map 336/7)."""
    builtin = (LSM.display_record(273, [(0, 8, []), (1, 8, []), (2, 0, [16, 1, 0xFFFE])], 4)
               + LSM.display_record(336, [], 7))
    run = (LSM.display_record(260, [(0, 70, []), (1, 28, []), (2, 0, [73, 1, 71])])
           + LSM.display_record(262, [(0, 1, [])]) + builtin)
    bike = LSM.display_record(257, [(0, 6, []), (1, 32, []), (2, 5, [])]) + builtin
    modes = tlv(0x010B, b"\x02\x00")
    modes += tlv(0x0101, tlv(0x0102, blob(size, "Running")) + tlv(0x01FF, bytes(8)) + tlv(0x0105, run))
    modes += tlv(0x0101, tlv(0x0102, blob(size, "Cycling")) + tlv(0x0105, bike))
    root = tlv(0x0100, modes)
    if size == 90:
        root += tlv(0x0200, tlv(0x0210, tlv(0x0212, b"Duathlon".ljust(24, b"\0")) + tlv(0x0213, b"\x02\x00")
                                + tlv(0x0214, b"\x00\x00") + tlv(0x0214, b"\x01\x00")))
    return tlv(0x0003, root).ljust(LSM.REGION_WRITE_BYTES, b"\xff")


EDITS = [
    (90, 0, [{"op": "setRow", "display": 0, "row": "Top", "values": [11]}]),
    (90, 0, [{"op": "setRow", "display": 0, "row": "Bottom", "values": [9, 31, 17, 34, 1]}]),
    (90, 1, [{"op": "add", "type": "2-row"}, {"op": "add", "type": "1-row"}]),
    (90, 0, [{"op": "remove", "display": 1}, {"op": "setType", "display": 0, "type": "graph"}]),
    (90, 1, [{"op": "setRow", "display": 1, "row": "Center", "values": [21]}]),
    (76, 0, [{"op": "setType", "display": 1, "type": "3-row"},
             {"op": "setRow", "display": 1, "row": "Bottom", "values": [5, 10]}]),
    (76, 1, [{"op": "add", "type": "3-row"}, {"op": "remove", "display": 0}]),
]


def synthetic_hash():
    regions = {90: synthetic(90), 76: synthetic(76)}
    h = 0x811C9DC5
    for size, mode, edits in EDITS:
        regions[size] = LSM.patch_displays(regions[size], mode, edits)
        h = fnv1a(regions[size], h)
    return h, regions


def test_synthetic():
    h, regions = synthetic_hash()
    assert h == 0x47A0085F, hex(h)
    r = regions[90]
    user, records, _ = displays_of(r, "Running")
    assert [d["layout"] for d in user] == [257] and len(records) == 3
    user, records, _ = displays_of(r, "Cycling")
    assert [d["layout"] for d in user] == [257, 261, 262] and len(records) == 5
    assert user[1]["rows"] == [[0, 10, []], [1, 0, [21]]]      # the 2-row's Center is multi-value
    assert LSM.sport_mode_groups(r) == [{"name": "Duathlon", "activityId": 2, "legs": [0, 1],
                                         "legNames": ["Running", "Cycling"], "isMultisport": True}]
    a1 = regions[76]
    assert [d["rows"] for d in displays_of(a1, "Cycling")[0]] == [[[0, 10, []], [1, 11, []], [2, 0, [5]]]]
    assert displays_of(a1, "Running")[0][1]["rows"][2] == [2, 0, [5, 10]]
    used = LSM.region_used(a1)
    assert set(a1[used:]) == {0xFF} and LSM.sport_mode_groups(a1) == []


def test_sport_mode_displays_edit():
    region = synthetic(90)
    state = {"flash": region, "writes": []}
    LSM.read_region_live = lambda length=0: state["flash"][:length]

    def flash_write(addr, data, extra):
        state["writes"].append((addr, len(data), extra))
        state["flash"] = data
    legacy_link.flash_write = flash_write
    add = [{"op": "add", "type": "1-row"}]
    with tempfile.TemporaryDirectory() as tmp:
        dry = legacy_link.sport_mode_displays_edit("Cycling", add, backup_dir=tmp)
        assert dry["ok"] and not dry["wrote"] and dry["bytes_changed"] > 0 and not state["writes"]
        out = legacy_link.sport_mode_displays_edit("Cycling", add, write=True, backup_dir=tmp)
        assert out["wrote"] and pathlib.Path(out["backup"]).read_bytes() == region
        assert state["writes"] == [(0x2000, 10240, 0xFFFFFFFF)]
        assert state["flash"] == LSM.patch_displays(region, "Cycling", add)
        # A refused edit sends nothing.
        try:
            legacy_link.sport_mode_displays_edit("Cycling", [{"op": "remove", "display": 7}],
                                                 write=True, backup_dir=tmp)
            raise AssertionError("a bad edit was written")
        except ValueError:
            pass
        assert len(state["writes"]) == 1
        # A watch that reads back something else is reported, with the copy named.
        legacy_link.flash_write = lambda addr, data, extra: state["writes"].append(1)
        try:
            legacy_link.sport_mode_displays_edit("Cycling", add, write=True, backup_dir=tmp)
            raise AssertionError("a lost write was reported as written")
        except RuntimeError as exc:
            assert "copy" in str(exc)


if __name__ == "__main__":
    if "--hash" in sys.argv:
        print(hex(synthetic_hash()[0]))
        sys.exit(0)
    test_synthetic()
    test_sport_mode_displays_edit()
    if not (AMBIT2.exists() and AMBIT1.exists()):
        print("legacy display edit: synthetic OK; captures absent, rest skipped")
        sys.exit(0)
    r2 = writes(AMBIT2, 1024)
    test_ambit2(r2)
    test_ambit2_groups(r2)
    test_refusals(r2)
    r1 = writes(AMBIT1, 512)
    test_ambit1(r1)
    test_ambit1_screens_rule(r1)
    print("legacy display edit: SuuntoLink's display adds, removal, layout changes and row changes "
          "replayed on Ambit1 and Ambit2 byte for byte (display time aside); groups, synthetic "
          "bytes and writer OK")
