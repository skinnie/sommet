#!/usr/bin/env python3
"""Ambit1/2 sport-mode edits (name, autolap, HR limits, pods), without a watch.

Ground truth: SuuntoLink editing André's Ambit2 (assets/pcap/ambit2_suuntolink_settings_
sportmodes.pcap) and his Ambit1 (assets/pcap/2026-08-23-ambit1-suuntolink/zzambit1full.pcap and
ambit1languages.pcap), git-ignored - skipped where absent. Each SuuntoLink save writes the
region's first 10240 bytes (+ the 0x0b18 tail [0x2000][0xFFFFFFFF]); between two consecutive
writes where SuuntoLink changed one of the settings Sommet edits, patch_region() of the first
must give the second byte for byte, except the Unix time SuuntoLink also puts in the mode's 0x01FF
record (left as read, like the Android writer). Checked here:
  - every captured write has that shape (extent, chunk size, commit tail);
  - each captured pods / autolap / HR-limits change, on both watches, replayed from the value
    the Sport Modes page would send;
  - the captured renames: Ambit1 (the name only), Ambit2 (the name and the mode's group name);
  - the Ambit1 76-byte layout decodes the dump read off the watch the way the format doc reads it;
  - sport_mode_edit(): what it writes, where, the backup and the read-back;
  - the bytes for a fixed synthetic region, the same FNV-1a as the Android writer's jest test.
Not seen on a real watch: André no longer has an Ambit1 or Ambit2 (routes/03).
"""
import json
import pathlib
import struct
import sys
import tempfile

import legacy_link
import legacy_sport_modes as LSM

ROOT = pathlib.Path(__file__).resolve().parent.parent
PCAP = ROOT / "assets" / "pcap"
AMBIT2 = PCAP / "ambit2_suuntolink_settings_sportmodes.pcap"
AMBIT1 = PCAP / "2026-08-23-ambit1-suuntolink" / "zzambit1full.pcap"
AMBIT1_LANG = PCAP / "2026-08-23-ambit1-suuntolink" / "ambit1languages.pcap"
AMBIT1_DUMP = PCAP / "2026-08-23-ambit1-suuntolink" / "BASELINE-sportmodes-20260823-120650.bin"


def _u16(b, o):
    return b[o] | (b[o + 1] << 8)


def writes(capture, chunk):
    """SuuntoLink's sport-mode region writes in `capture`, each checked for SuuntoLink's shape."""
    import ambit_pcap
    out, cur = [], None
    for m in ambit_pcap.messages(capture):
        if m.incoming:
            continue
        if m.command == 0x0B16:
            addr, ln, _seq = struct.unpack("<IHH", m.payload[:8])
            if LSM.REGION_ADDR <= addr < LSM.REGION_ADDR + 0x4000:
                if cur is None:
                    cur = {"data": bytearray(LSM.REGION_WRITE_BYTES), "chunks": []}
                cur["chunks"].append((addr, ln))
                cur["data"][addr - LSM.REGION_ADDR:addr - LSM.REGION_ADDR + ln] = m.payload[8:8 + ln]
        elif m.command == 0x0B18 and cur is not None:
            tail = struct.unpack("<II", m.payload[:8])
            assert tail == (LSM.REGION_ADDR, LSM.COMMIT_EXTRA), tail
            want = [(LSM.REGION_ADDR + i * chunk, chunk) for i in range(LSM.REGION_WRITE_BYTES // chunk)]
            assert cur["chunks"] == want, cur["chunks"][:3]
            out.append(bytes(cur["data"]))
            cur = None
    return out


def _mode_of(data, off):
    """(blob offset, blob length, settings-stamp offset or None) of the mode holding `off`."""
    for tag, body, ln in LSM._children(data, 4, LSM.region_used(data)):
        if tag != LSM.TAG_MODES:
            continue
        for t, b, n in LSM._children(data, body, body + ln):
            if t == LSM.TAG_MODE and b <= off < b + n:
                kids = list(LSM._children(data, b, b + n))
                blob = [(kb, kn) for kt, kb, kn in kids if kt == LSM.TAG_SETTINGS][0]
                stamp = [kb for kt, kb, kn in kids if kt == 0x01FF and kn == 8]
                return blob[0], blob[1], (stamp[0] if stamp else None)
    return None


def _stamped(before, after, stamp):
    """`after` with the mode's settings time (first u32 of 0x01FF) as it was in `before`."""
    out = bytearray(after)
    if stamp is not None:
        out[stamp:stamp + 4] = before[stamp:stamp + 4]
    return bytes(out)


def replay(runs):
    """Replay every captured single-setting change Sommet edits; returns counts per kind."""
    done = {"pods": 0, "autolap": 0, "hr": 0}
    for before, after in zip(runs, runs[1:]):
        if LSM.region_used(before) != LSM.region_used(after):
            continue                                 # a mode/display/group added or removed
        diff = [i for i in range(len(before)) if before[i] != after[i]]
        if not diff:
            continue
        where = _mode_of(before, diff[0])
        if where is None:
            continue
        blob, blen, stamp = where
        inside = {(i - blob) // 2 * 2 for i in diff if blob <= i < blob + blen}
        outside = [i for i in diff if not blob <= i < blob + blen]
        if not inside or any(stamp is None or not stamp <= i < stamp + 4 for i in outside):
            continue                                 # not a settings write of one mode
        lay = LSM.LAYOUTS[blen]
        mode = [o for o, _ in LSM.named_blobs(before)].index(blob)
        get = lambda d, f: _u16(d, blob + lay[f])    # noqa: E731
        if inside == {lay["pods"]}:
            old, new = get(before, "pods"), get(after, "pods")
            # The page toggles bit 0 for the HR belt (0x0001); the other pods are one bit each.
            sent = old ^ 0x0001 if (old ^ new) & 0x0001 else new
            patch, kind = {"useHw": sent}, "pods"
        elif inside == {lay["autolap"]}:
            patch, kind = {"autolapM": get(after, "autolap")}, "autolap"
        elif inside <= {lay["hr_max"], lay["hr_min"], lay["use_hr"]} - {None}:
            on = bool(get(after, "use_hr")) if lay["use_hr"] is not None else \
                bool(get(after, "hr_max") or get(after, "hr_min"))
            src = after if on else before            # off: the page still sends the limits shown
            patch = {"heartrateMax": get(src, "hr_max"), "heartrateMin": get(src, "hr_min"),
                     "useHrLimits": on}
            kind = "hr"
        else:
            continue                                 # a setting Sommet does not write
        got = LSM.patch_region(before, mode, patch)
        assert got == _stamped(before, after, stamp), (kind, patch)
        done[kind] += 1
    return done


def test_ambit2_settings():
    runs = writes(AMBIT2, 1024)
    assert len(runs) == 159
    done = replay(runs)
    # HR belt off/on, foot/bike/power/cadence on/off; HR limits on and off; four autolaps.
    assert done == {"pods": 10, "autolap": 4, "hr": 2}, done
    return runs


def test_ambit1_settings():
    runs = writes(AMBIT1, 512)
    assert len(runs) == 79
    done = replay(runs)
    assert done["pods"] == 10 and done["autolap"] == 2 and done["hr"] == 2, done
    return runs


def test_ambit2_rename(runs):
    # Save 2 of the capture: "Indoor training" renamed "Treino Forção". SuuntoLink wrote the
    # mode (run 6) and then its group (run 8); Sommet writes both names in one write.
    before = runs[5]
    got = LSM.patch_region(before, "Indoor training", {"name": "Treino Forção"})
    blob = LSM.named_blobs(before)[5][0]
    group = [g for g in LSM._groups(before) if g[2] == [5]][0][0]
    after_blob = LSM.named_blobs(runs[6])[5][0]
    after_group = [g for g in LSM._groups(runs[8]) if g[2] == [5]][0][0]
    assert got[blob:blob + 16] == runs[6][after_blob:after_blob + 16]
    assert got[group:group + 24] == runs[8][after_group:after_group + 24]
    changed = {i for i in range(len(before)) if got[i] != before[i]}
    assert changed and changed <= set(range(blob, blob + 16)) | set(range(group, group + 24))
    # The multisport group (Triathlon) and the other modes' groups are left alone.
    assert [bytes(got[o:o + 24]).split(b"\0")[0] for o, _n, m in LSM._groups(got)].count(b"Treino For\xe7\xe3o") == 1


def test_ambit1_rename(runs):
    # Between the two Ambit1 captures, "Adventure Racing" became "Corrida de Acção" (typed by
    # André in SuuntoLink): the name and the settings time changed, nothing else.
    before, after = runs[-1], writes(AMBIT1_LANG, 512)[0]
    got = LSM.patch_region(before, "Adventure Racing", {"name": "Corrida de Acção"})
    blob, _blen, stamp = _mode_of(before, LSM.named_blobs(before)[8][0])
    assert got == _stamped(before, after, stamp)
    assert not LSM._groups(before)                   # the Ambit1 region has no groups


def test_ambit1_decode():
    # docs/ambit1_sport_mode_format.md section 5, the baseline read off the watch.
    modes = {m["name"]: m for m in LSM.read_app_modes(AMBIT1_DUMP)}
    run = modes["Running"]
    assert run["autolapM"] == 1000 and run["useHw"] == 0x0107 and run["gpsInterval"] == 1
    assert run["recordingInterval"] == 1 and run["hrBelt"] and run["footPod"]
    assert modes["Indoor training"]["gpsInterval"] == 0
    assert modes["Trekking"]["gpsInterval"] == 60 and modes["Trekking"]["recordingInterval"] == 10
    assert modes["Cycling"]["useHw"] == 0x08C3 and modes["Cycling"]["bikePod"]


# ─── a synthetic region, the same one android/src/services/__tests__/LegacySportModes.test.ts builds
def tlv(tag, body):
    return struct.pack("<HH", tag, len(body)) + body


def blob(size, name):
    b = bytearray(((i * 7 + 3) & 0xFF) for i in range(size))
    b[0:16] = name.encode("latin-1").ljust(16, b"\0")[:16]
    return bytes(b)


def group(name, members):
    body = tlv(0x0212, name.encode("latin-1").ljust(24, b"\0"))
    for m in members:
        body += tlv(0x0214, struct.pack("<H", m))
    return tlv(0x0210, body)


def synthetic(size):
    modes = tlv(0x010B, b"\x02\x00")
    modes += tlv(0x0101, tlv(0x0102, blob(size, "Running")) + tlv(0x01FF, bytes(8)))
    modes += tlv(0x0101, tlv(0x0102, blob(size, "Cycling")))
    root = tlv(0x0100, modes)
    if size == 90:
        root += tlv(0x0200, group("Running", [0]) + group("Cycling", [1]) + group("Duathlon", [0, 1]))
    return tlv(0x0003, root).ljust(1024, b"\xff")


EDITS = [
    (90, 0, {"autolapM": 1500}),
    (90, 0, {"useHw": 0x0000}),
    (90, 1, {"heartrateMax": 180, "heartrateMin": 60, "useHrLimits": True}),
    (90, 1, {"heartrateMax": 170, "heartrateMin": 50, "useHrLimits": False}),
    (90, 0, {"name": "Corrida Ação"}),
    (90, 1, {"name": "Bike 16 chars!!!", "useHw": 0x0801}),
    (76, 1, {"autolapM": 2000, "useHw": 0x0106, "heartrateMax": 200, "heartrateMin": 40,
             "useHrLimits": True, "name": "Trilho"}),
    (76, 0, {"useHrLimits": False, "heartrateMax": 150, "heartrateMin": 90}),
]


def fnv1a(data, h=0x811C9DC5):
    for x in data:
        h = ((h ^ x) * 0x01000193) & 0xFFFFFFFF
    return h


def synthetic_hash():
    regions = {90: synthetic(90), 76: synthetic(76)}
    h = 0x811C9DC5
    for size, mode, patch in EDITS:
        regions[size] = LSM.patch_region(regions[size], mode, patch)
        h = fnv1a(regions[size], h)
    return h, regions


def test_synthetic():
    h, regions = synthetic_hash()
    assert h == 0x3CEF0ABF, hex(h)
    r = regions[90]
    groups = [bytes(r[o:o + n]).split(b"\0")[0] for o, n, _m in LSM._groups(r)]
    assert groups == ["Corrida Ação".encode("latin-1"), b"Bike 16 chars!!!", b"Duathlon"]
    m = [LSM.decode_settings(r[o:o + n]) for o, n in LSM.named_blobs(r)]
    assert m[0]["hrbelt_and_pods"] & 0x0003 == 0
    assert (m[1]["heartrate_max"], m[1]["heartrate_min"], m[1]["use_heartrate_limits"]) == (0, 0, 0)
    a1 = [LSM.decode_settings(regions[76][o:o + n]) for o, n in LSM.named_blobs(regions[76])]
    assert a1[1]["activity_name"] == "Trilho" and a1[1]["autolap"] == 2000
    assert (a1[0]["heartrate_max"], a1[0]["heartrate_min"]) == (0, 0)
    for bad in ("Nope", 7):
        try:
            LSM.patch_region(r, bad, {"autolapM": 1})
            raise AssertionError(f"{bad!r} accepted")
        except ValueError:
            pass


# (size, pods on the watch, pods the page sends, pods written). Bit 1 is set whenever the belt or
# a pod is used (every pods value in both captures); it follows that rule when the belt or a pod
# is switched (André 2026-11-26, Q14 a) and is left as read otherwise.
PODS_RULE = [
    (90, 0x0803, 0x0802, 0x0802),   # belt off, bike pod left: bit 1 stays (was cleared before Q14)
    (76, 0x0003, 0x0002, 0x0000),   # belt off, nothing left: bit 1 goes (was kept before Q14)
    (90, 0x0000, 0x0001, 0x0003),   # belt on alone: SuuntoLink's Ambit2 0x0000 -> 0x0003
    (76, 0x0107, 0x0106, 0x0106),   # SuuntoLink's Ambit1 belt off with the foot pod on
    (90, 0x0003, 0x0043, 0x0043),   # power pod on next to the belt
    (90, 0x0101, 0x0105, 0x0105),   # only the 0x0004 bit changed: bit 1 left as read
]


def test_pods_rule():
    for size, start, sent, want in PODS_RULE:
        r = bytearray(synthetic(size))
        off = LSM.named_blobs(r)[0][0] + LSM.LAYOUTS[size]["pods"]
        struct.pack_into("<H", r, off, start)
        got = struct.unpack_from("<H", LSM.patch_region(bytes(r), 0, {"useHw": sent}), off)[0]
        assert got == want, (size, hex(start), hex(sent), hex(got))


def test_rename_clash_and_ambit1_hr():
    """Review before 0.2.68: a rename to a name another mode or multisport has is refused (every
    later edit finds a mode by name); on the Ambit1, HR limits "on" with 0/0 is refused (it has no
    on/off field, so nothing would change)."""
    for size, mode, name in ((90, 1, "Running"), (90, 1, "Duathlon"), (76, 1, "Running")):
        try:
            LSM.patch_region(synthetic(size), mode, {"name": name})
            raise AssertionError(f"renamed to {name!r} on the {size}-byte layout")
        except ValueError as exc:
            assert "already has" in str(exc), exc
    assert LSM.patch_region(synthetic(90), 0, {"name": "Running"}) == synthetic(90)
    LSM.patch_region(synthetic(90), 1, {"name": "Bike"})
    off = synthetic(76)
    off = LSM.patch_region(off, 0, {"useHrLimits": False, "heartrateMax": 150, "heartrateMin": 90})
    try:
        LSM.patch_region(off, 0, {"useHrLimits": True})
        raise AssertionError("Ambit1 HR limits turned on with 0/0")
    except ValueError as exc:
        assert "Ambit1" in str(exc), exc
    on = LSM.patch_region(off, 0, {"useHrLimits": True, "heartrateMax": 180, "heartrateMin": 0})
    blob = LSM.named_blobs(on)[0][0]
    assert _u16(on, blob + 28) == 180


def test_sport_mode_edit():
    region = synthetic(90).ljust(LSM.REGION_WRITE_BYTES, b"\xff")
    state = {"flash": region, "writes": []}
    LSM.read_region_live = lambda length=0: state["flash"][:length]

    def flash_write(addr, data, extra):
        state["writes"].append((addr, len(data), extra))
        state["flash"] = data
    legacy_link.flash_write = flash_write
    with tempfile.TemporaryDirectory() as tmp:
        dry = legacy_link.sport_mode_edit("Cycling", {"autolapM": 500}, backup_dir=tmp)
        assert dry["ok"] and not dry["wrote"] and dry["bytes_changed"] == 2 and not state["writes"]
        same = legacy_link.sport_mode_edit("Cycling", {"name": "Cycling"}, write=True, backup_dir=tmp)
        assert not same["wrote"] and not state["writes"]          # nothing to change: no write
        out = legacy_link.sport_mode_edit("Cycling", {"autolapM": 500}, write=True, backup_dir=tmp)
        assert out["wrote"] and pathlib.Path(out["backup"]).read_bytes() == region
        assert state["writes"] == [(0x2000, 10240, 0xFFFFFFFF)]
        # A short read is refused before anything is sent.
        LSM.read_region_live = lambda length=0: state["flash"][:4096]
        try:
            legacy_link.sport_mode_edit("Cycling", {"autolapM": 600}, write=True, backup_dir=tmp)
            raise AssertionError("a short read was written over")
        except RuntimeError:
            pass
        assert len(state["writes"]) == 1
        # A write the watch does not keep: the region as read is written back once.
        LSM.read_region_live = lambda length=0: state["flash"][:length]
        before = state["flash"]
        state["writes"].clear()

        def lost_write(addr, data, extra):
            state["writes"].append((addr, len(data), extra))
            if len(state["writes"]) > 1:
                state["flash"] = data
        legacy_link.flash_write = lost_write
        try:
            legacy_link.sport_mode_edit("Cycling", {"autolapM": 700}, write=True, backup_dir=tmp)
            raise AssertionError("a lost write was reported as written")
        except RuntimeError as exc:
            assert "written back" in str(exc), exc
        assert len(state["writes"]) == 2 and state["flash"] == before


if __name__ == "__main__":
    if "--hash" in sys.argv:
        print(hex(synthetic_hash()[0]))
        sys.exit(0)
    test_synthetic()
    test_rename_clash_and_ambit1_hr()
    test_pods_rule()
    test_sport_mode_edit()
    if not (AMBIT2.exists() and AMBIT1.exists() and AMBIT1_LANG.exists()):
        print("legacy sport-mode edit: synthetic OK; captures absent, rest skipped")
        sys.exit(0)
    r2 = test_ambit2_settings()
    test_ambit2_rename(r2)
    r1 = test_ambit1_settings()
    test_ambit1_rename(r1)
    test_ambit1_decode()
    print("legacy sport-mode edit: SuuntoLink's pods/autolap/HR/rename writes replayed on Ambit1 "
          "and Ambit2 byte for byte (settings time aside); synthetic bytes and writer OK")
