#!/usr/bin/env python3
"""Ambit1/2 sport-mode create and delete (watch-protocol/10 L3), without a watch.

Ground truth: SuuntoLink's region writes in assets/pcap/2026-08-23-ambit1-suuntolink/
zzambit1full.pcap (Ambit1) and assets/pcap/ambit2_suuntolink_settings_sportmodes.pcap (Ambit2),
git-ignored - skipped where absent. SuuntoLink writes a create in two (Ambit1) or three (Ambit2)
saves; Sommet writes the end state in one, so create_mode() of the write before must equal the
last write of the create, byte for byte over all 10240 bytes, given the times SuuntoLink stamped.
Checked here:
  - the four captured creates: Ambit1 Yoga, Adventure Racing, Alpine Skiing; Ambit2 Adventure
    Racing (settings, the seven displays, the menu entry with the lowest free id);
  - the captured delete: Ambit2 Triathlon, which takes the Transition only it used;
  - deleting each created mode gives back the write before its create, on both watches;
  - SuuntoLink's refusals: 10/10 used, a multisport's leg, a mode with no menu entry, the last;
  - a delete in the middle of the list moves every leg above it down by one (as SuuntoLink's
    Ambit3 capture shows), legs still naming the same modes;
  - SuuntoLink's per-sport defaults on all four models only produce values the watches hold;
  - the bytes for a fixed synthetic region: the same FNV-1a as the Android jest test.
Not seen on a real watch: André no longer has an Ambit1 or Ambit2 (routes/03).
"""
import json
import pathlib
import struct
import sys
import tempfile

import legacy_link
import legacy_sport_modes as LSM
from test_legacy_sport_mode_edit import AMBIT1, AMBIT2, fnv1a, writes

def _mode_record(data, name):
    for at, ln, n, _blen in LSM._mode_list(data)[1]:
        if n == name:
            return at, ln
    raise AssertionError(name)


def stamps(data, name):
    """(settings time, display time, menu-entry time or None) SuuntoLink gave mode `name`."""
    at, ln = _mode_record(data, name)
    kids = {t: b for t, b, _n in LSM._children(data, at + 4, at + ln)}
    t1, t2 = struct.unpack_from("<II", data, kids[LSM.TAG_STAMP])
    t3 = None
    for g in LSM._group_list(data)[1]:
        if g["name"] == name:
            kids = {t: b for t, b, _n in LSM._children(data, g["at"] + 4, g["at"] + g["len"])}
            t3 = struct.unpack_from("<I", data, kids[LSM.TAG_GROUP_STAMP])[0]
    return (t1, t2, t3 if t3 is not None else t1)


def names(data):
    return [m[2] for m in LSM._mode_list(data)[1]]


def test_ambit1_creates():
    runs = writes(AMBIT1, 512)
    done = []
    for before, after, name, activity in ((0, 2, "Yoga", 10), (2, 4, "Adventure Racing", 61),
                                          (4, 6, "Alpine Skiing", 20)):
        got = LSM.create_mode(runs[before], name, activity, "Bluebird",
                              now=stamps(runs[after], name))
        assert got == runs[after], (name, [i for i in range(len(got)) if got[i] != runs[after][i]][:5])
        # Removing it gives the write before the create back.
        assert LSM.delete_mode(runs[after], name, "Bluebird") == runs[before], name
        done.append(name)
    # The capture ends with 10 modes: SuuntoLink's limit.
    assert LSM.usage(runs[6])["used"] == 10
    for bad in (lambda: LSM.create_mode(runs[6], "Eleventh", 3, "Bluebird"),
                lambda: LSM.create_mode(runs[0], "Running", 3, "Bluebird"),       # name taken
                lambda: LSM.create_mode(runs[0], "Seventeen chars!!", 3, "Bluebird"),
                lambda: LSM.create_mode(runs[0], "Nope", 7, "Bluebird"),          # not offered
                lambda: LSM.create_mode(runs[0], "Nope", 3, "Duck")):             # wrong model
        try:
            bad()
            raise AssertionError("accepted")
        except ValueError:
            pass
    return runs, done


def test_ambit2_create_and_delete():
    runs = writes(AMBIT2, 1024)
    # Writes 2-5: Adventure Racing's settings, its displays, its menu entry (id 9 = the lowest
    # free; ids 1-8 and 10 taken), the mode id 1 (the factory ones are 0x2E1B...).
    got = LSM.create_mode(runs[2], "Adventure Racing", 61, "Duck",
                          now=stamps(runs[5], "Adventure Racing"))
    assert got == runs[5], [i for i in range(len(got)) if got[i] != runs[5][i]][:5]
    assert LSM.delete_mode(runs[5], "Adventure Racing", "Duck") == runs[2]
    # Write 0->1: SuuntoLink deletes the Triathlon; the Transition it alone used goes with it.
    assert LSM.delete_multisport(runs[0], "Triathlon", "Duck") == runs[1]
    assert LSM.usage(runs[0]) == {"used": 9, "max": 10, "multisport": 1}
    assert LSM.usage(runs[5]) == {"used": 10, "max": 10, "multisport": 1}
    refusals = {
        "leg": lambda: LSM.delete_mode(runs[0], "Cycling", "Duck"),        # Triathlon's leg
        "hidden": lambda: LSM.delete_mode(runs[0], "Transition", "Duck"),  # no menu entry
        "full": lambda: LSM.create_mode(runs[5], "Eleventh", 3, "Duck"),
        "single as multi": lambda: LSM.delete_multisport(runs[0], "Cycling", "Duck"),
        "model": lambda: LSM.create_mode(runs[2], "Nope", 3, "Bluebird"),
    }
    for what, call in refusals.items():
        try:
            call()
            raise AssertionError(f"{what} accepted")
        except ValueError:
            pass
    m = LSM.menu(runs[0], "Duck")
    assert [x["name"] for x in m["multisports"]] == ["Triathlon"] and m["multisports"][0]["deletable"]
    flags = {x["name"]: x["deletable"] for x in m["modes"]}
    assert not flags["Cycling"] and not flags["Transition"] and flags["Mountaineering"]
    return runs


def test_middle_delete(runs):
    # Write 2 has the Triathlon (legs Cycling, Running, Openwater swim = places 1, 0, 4).
    # Deleting "Run a route" (place 2) moves every leg above it down; the names stay.
    before = runs[2]
    got = LSM.delete_mode(before, "Run a route", "Duck")
    groups = {g["name"]: g["legNames"] for g in LSM.sport_mode_groups(got)}
    want = {g["name"]: g["legNames"] for g in LSM.sport_mode_groups(before) if g["name"] != "Run a route"}
    assert groups == want, groups
    assert names(got) == [n for n in names(before) if n != "Run a route"]
    # Every other mode record and entry is byte-identical, only its legs renumbered.
    for n in names(got):
        if n:
            a, la = _mode_record(before, n)
            b, lb = _mode_record(got, n)
            assert before[a:a + la] == got[b:b + lb], n
    assert set(got[LSM.region_used(got):]) == {0xFF}
    # A deleted mode is gone from the menu entries' ids too; the rest keep theirs.
    ids_before = {g["name"]: g["id"] for g in LSM._group_list(before)[1]}
    ids_after = {g["name"]: g["id"] for g in LSM._group_list(got)[1]}
    assert ids_after == {k: v for k, v in ids_before.items() if k != "Run a route"}


def test_defaults_values():
    """Every sport SuuntoLink offers, on every model: only values these watches hold."""
    rows = json.loads(LSM.ROWS_JSON.read_text())
    seen = {"gps": {0, 1, 60}, "rec": {1, 10}, "alti": {0, 1, 2}}
    for variant in LSM.LEGACY_VARIANTS:
        for act in sorted(LSM.SELECTABLE_ACTIVITIES):
            f = LSM.new_mode_fields(variant, act, rows)
            for k, ok in seen.items():
                assert f[k] in ok, (variant, act, k, f[k])
            pods = f["pods"] & ~0x0004
            assert bool(pods & LSM.SENSOR_SEARCH) == bool(pods & ~LSM.SENSOR_SEARCH), (variant, act)
            if variant in ("Colibri", "Greentit"):
                assert f["alti"] == 0
            if variant == "Greentit":
                assert not f["pods"] & 0x08C0
    # What the four captured creates hold, from SuuntoLink's table.
    assert LSM.new_mode_fields("Bluebird", 10, rows) == {"pods": 3, "alti": 1, "gps": 0, "rec": 10}
    assert LSM.new_mode_fields("Duck", 61, rows) == {"pods": 3, "alti": 0, "gps": 1, "rec": 10}
    # A swim on the Ambit2: belt and pods locked off, alti-baro hidden (0); not on the Ambit1.
    assert LSM.new_mode_fields("Duck", 6, rows) == {"pods": 0, "alti": 0, "gps": 0, "rec": 1}
    assert LSM.new_mode_fields("Bluebird", 6, rows) == {"pods": 0, "alti": 1, "gps": 0, "rec": 1}
    # Running: belt + foot pod + sensor search + accelerometer, the factory Running's 0x0107.
    assert LSM.new_mode_fields("Duck", 3, rows)["pods"] == 0x0107


# ─── a synthetic region, the same one android/src/services/__tests__/LegacySportModeManage.test.ts
def tlv(tag, body):
    return struct.pack("<HH", tag, len(body)) + body


def synthetic(blen):
    def mode(name, act):
        b = bytearray(blen)
        b[0:16] = name.encode("latin-1").ljust(16, b"\0")
        struct.pack_into("<H", b, 16, act)
        if blen == 90:
            struct.pack_into("<I", b, 18, 0x2E1B + act)
        return tlv(0x0101, tlv(0x0102, bytes(b)) + tlv(0x0105, LSM.NEW_MODE_DISPLAYS[:54]))

    def group(name, act, legs, gid):
        body = tlv(0x0212, name.encode("latin-1").ljust(24, b"\0")) + tlv(0x0213, struct.pack("<H", act))
        body += b"".join(tlv(0x0214, struct.pack("<H", x)) for x in legs)
        return tlv(0x0210, body + tlv(0x02FE, struct.pack("<I", gid)))

    modes = tlv(0x010B, b"\x02\x00") + mode("Running", 3) + mode("Transition", 1) + mode("Cycling", 4)
    root = tlv(0x0100, modes)
    if blen == 90:
        root += tlv(0x0200, group("Running", 3, [0], 2) + group("Duathlon", 2, [0, 1, 2, 1, 0], 1)
                    + group("Cycling", 4, [2], 3))
    return tlv(0x0003, root).ljust(10240, b"\xff")


def synthetic_hash():
    h = 0x811C9DC5
    a2 = synthetic(90)
    steps = [LSM.create_mode(a2, "Trilho", 82, "Duck", now=(1000, 1001, 1002))]
    steps.append(LSM.create_mode(steps[-1], "Natação", 6, "Colibri", now=2000))
    steps.append(LSM.delete_multisport(steps[-1], "Duathlon", "Duck"))
    steps.append(LSM.delete_mode(steps[-1], "Running", "Duck"))
    a1 = synthetic(76)
    steps.append(LSM.create_mode(a1, "Ski", 20, "Bluebird", now=3000))
    steps.append(LSM.delete_mode(steps[-1], "Transition", "Bluebird"))
    for s in steps:
        h = fnv1a(s, h)
    return h, steps


def test_synthetic():
    h, steps = synthetic_hash()
    assert h == 0x3D11ACA4, hex(h)
    # After the Duathlon went, its Transition went with it and the legs moved down.
    groups = {g["name"]: g["legNames"] for g in LSM.sport_mode_groups(steps[2])}
    assert groups == {"Running": ["Running"], "Cycling": ["Cycling"], "Trilho": ["Trilho"],
                      "Natação": ["Natação"]}, groups
    assert names(steps[3]) == ["Cycling", "Trilho", "Natação"]
    assert names(steps[5]) == ["Running", "Cycling", "Ski"]
    # Colibri: no alti-baro choice; a swim on the Ambit2 family has no belt or pods.
    nat = [LSM.decode_settings(steps[1][o:o + n]) for o, n in LSM.named_blobs(steps[1])][-1]
    assert nat["hrbelt_and_pods"] == 0 and nat["alti_baro_mode"] == 0 and nat["sport_mode_id"] == 2


def test_cli_write():
    region = synthetic(90)
    state = {"flash": region, "writes": []}
    LSM.read_region_live = lambda length=0: state["flash"][:length]

    def flash_write(addr, data, extra):
        state["writes"].append((addr, len(data), extra))
        state["flash"] = data
    legacy_link.flash_write = flash_write
    with tempfile.TemporaryDirectory() as tmp:
        dry = legacy_link.sport_mode_create("Trilho", 82, "Duck", backup_dir=tmp)
        assert dry["ok"] and not dry["wrote"] and not state["writes"]
        out = legacy_link.sport_mode_create("Trilho", 82, "Duck", write=True, backup_dir=tmp)
        assert out["wrote"] and pathlib.Path(out["backup"]).read_bytes() == region
        assert state["writes"] == [(0x2000, 10240, 0xFFFFFFFF)]
        assert "Trilho" in names(state["flash"])
        out = legacy_link.sport_mode_delete("Trilho", "Duck", write=True, backup_dir=tmp)
        assert out["wrote"] and names(state["flash"]) == names(region)
        out = legacy_link.sport_mode_delete("Duathlon", "Duck", multisport=True, write=True,
                                            backup_dir=tmp)
        assert out["wrote"] and "Transition" not in names(state["flash"])
        assert len(state["writes"]) == 3


if __name__ == "__main__":
    if "--hash" in sys.argv:
        print(hex(synthetic_hash()[0]))
        sys.exit(0)
    test_defaults_values()
    test_synthetic()
    test_cli_write()
    if not (AMBIT2.exists() and AMBIT1.exists()):
        print("legacy sport-mode create/delete: synthetic OK; captures absent, rest skipped")
        sys.exit(0)
    test_ambit1_creates()
    r2 = test_ambit2_create_and_delete()
    test_middle_delete(r2)
    print("legacy sport-mode create/delete: SuuntoLink's 4 creates and its Triathlon delete "
          "replayed byte for byte, created modes removed back to the write before, refusals, "
          "middle delete renumbering and synthetic bytes OK")
