#!/usr/bin/env python3
"""Ambit1 Suunto Apps (watch-protocol/10 L1), without a watch.

Ground truth: SuuntoLink putting two apps on André's Ambit1 in
assets/pcap/2026-08-23-ambit1-suuntolink/zzambit1full.pcap and rewriting them in
ambit1languages.pcap (git-ignored, skipped where absent). legacy_apps.py says what each byte is.
Checked here:
  - every captured Apps-store write has SuuntoLink's shape (0x27100, 39 x 512 + 32 bytes, the
    0x0b18 tail [0x27100][0xFFFFFFFF]) and parses; build_store() of its apps gives it back;
  - the apps in it are SuuntoLink's catalogue bytes (Sunrise/Sunset 10000003, Storm Alarm
    10000002), when the catalogue is here (data/suunto_apps, git-ignored);
  - install 1 (Sunrise/Sunset on Alpine Skiing, store empty) and install 2 (Storm Alarm on Cycling,
    its row saved in SuuntoLink's menu order, Alpine Skiing renumbered) replayed byte for byte:
    the sport-mode write (the mode's display time in 0x01FF aside, left as read as by every
    display edit) and the store;
  - the refusals: the Ambit2 family, a second app on a mode, one app on two modes, any row but a
    3-field display's bottom row, a full row, a value outside SuuntoLink's menu, a store order no
    capture settles, an empty store;
  - legacy_link.app_install: what it writes, in which order, the copies, and the sport modes put
    back when the store write fails.
Not seen on a real watch: André no longer has an Ambit1 (routes/03).
"""
import pathlib
import struct
import sys
import tempfile

import legacy_apps as LA
import legacy_link
import legacy_sport_modes as LSM
from test_legacy_sport_mode_edit import AMBIT1, AMBIT1_LANG, writes
import test_legacy_display_edit
import test_legacy_sport_mode_manage as M

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOG = ROOT / "data" / "suunto_apps"
SUNSET, STORM = 10000003, 10000002          # SuuntoLink's catalogue numbers of the two apps


def store_writes(capture):
    """[(message index, the 20000 store bytes)] SuuntoLink wrote, each checked for its shape."""
    import ambit_pcap
    out, cur = [], None
    for i, m in enumerate(ambit_pcap.messages(capture)):
        if m.incoming:
            continue
        if m.command == 0x0B16:
            addr, ln, _seq = struct.unpack("<IHH", m.payload[:8])
            if LA.STORE_ADDR <= addr < LA.STORE_ADDR + LA.STORE_SIZE:
                if cur is None:
                    cur = {"data": bytearray(b"\xff" * LA.STORE_SIZE), "chunks": [], "at": i}
                cur["chunks"].append((addr, ln))
                cur["data"][addr - LA.STORE_ADDR:addr - LA.STORE_ADDR + ln] = m.payload[8:8 + ln]
        elif m.command == 0x0B18 and cur is not None:
            assert struct.unpack("<II", m.payload[:8]) == (LA.STORE_ADDR, LA.COMMIT_EXTRA)
            want = [(LA.STORE_ADDR + k * 512, 512) for k in range(39)] + [(LA.STORE_ADDR + 39 * 512, 32)]
            assert cur["chunks"] == want, cur["chunks"][:3]
            out.append((cur["at"], bytes(cur["data"])))
            cur = None
    return out


def same_but_stamp(got, want, mode):
    """got == want, the display time (second u32 of the mode's 0x01FF) aside."""
    def blank(data):
        data = bytearray(data)
        for at, ln, name, _b in LSM._mode_list(data)[1]:
            if name == mode:
                for tag, body, n in LSM._children(data, at + 4, at + ln):
                    if tag == LSM.TAG_STAMP and n == 8:
                        data[body + 4:body + 8] = b"\0\0\0\0"
        return bytes(data)
    return blank(got) == blank(want)


def check_refused(what, call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError(f"{what} accepted")


def test_captures():
    runs = writes(AMBIT1, 512)
    stores = store_writes(AMBIT1)
    assert [len(LA.parse_store(s)) for _i, s in stores] == [1, 1, 2]
    for _i, s in stores + store_writes(AMBIT1_LANG):
        assert LA.build_store(LA.parse_store(s)) == s
    store1, store2 = stores[0][1], stores[2][1]
    sunset = LA.parse_store(store1)[0]
    storm = LA.parse_store(store2)[0]
    assert LA.parse_store(store2) == [storm, sunset]
    ids = {sunset: SUNSET, storm: STORM}
    if (CATALOG / "catalog.json").exists():
        rule_id_of, name_of = LA.catalog_lookup(CATALOG)
        assert (rule_id_of(sunset), rule_id_of(storm)) == (SUNSET, STORM)
        assert (name_of(sunset), name_of(storm)) == ("Sunrise/Sunset", "Storm Alarm")
    never = b"\xff" * LA.STORE_SIZE
    # Install 1 (sport-mode writes 75 -> 76, then the first store): Sunrise/Sunset on Alpine
    # Skiing's first screen, its Bottom row.
    region, store = LA.install(runs[75], never, "Alpine Skiing", 0, 2, sunset, ids.get)
    assert same_but_stamp(region, runs[76], "Alpine Skiing") and store == store1
    # Install 2 (writes 77 -> 78, then the two-app store): Storm Alarm on Cycling, which comes
    # first; Alpine Skiing's link moves to place 1.
    region, store = LA.install(runs[77], store1, "Cycling", 0, 2, storm, ids.get)
    assert same_but_stamp(region, runs[78], "Cycling") and store == store2
    assert {p: refs for p, (_h, refs) in LA.mode_links(region).items()} == {0: [0], 9: [1]}
    listed = LA.installed(runs[78], store2, ids.get)
    assert [(x["mode"], x["ruleId"]) for x in listed] == [("Cycling", STORM), ("Alpine Skiing", SUNSET)]
    other = b"IAMRULE\0" + b"\x01" * 40
    refusals = {
        "second app on a mode": lambda: LA.install(runs[76], store1, "Alpine Skiing", 0, 2, storm, ids.get),
        "one app on two modes": lambda: LA.install(runs[77], store1, "Cycling", 0, 2, sunset, ids.get),
        "one-value row": lambda: LA.install(runs[75], never, "Alpine Skiing", 0, 0, sunset),
        "a value outside the menu": lambda: LA.install(LSM.patch_displays(runs[75], "Alpine Skiing", [
            {"op": "setRow", "display": 0, "row": "Bottom", "values": [5, 107]}]),
            never, "Alpine Skiing", 0, 2, sunset),
        "not an app": lambda: LA.install(runs[75], never, "Alpine Skiing", 0, 2, b"\0" * 40),
        "no screen 9": lambda: LA.install(runs[75], never, "Alpine Skiing", 9, 2, sunset),
        # A higher catalogue number than Sunrise/Sunset: by mode it goes first, by number second.
        "order not captured": lambda: LA.install(runs[77], store1, "Cycling", 0, 2, other,
                                                 {**ids, other: 10000004}.get),
        "unknown app next to another": lambda: LA.install(runs[77], store1, "Cycling", 0, 2, other, ids.get),
        "Ambit2": lambda: LA.install(test_legacy_display_edit.synthetic(90), never, 0, 0, 2, sunset),
    }
    for what, call in refusals.items():
        check_refused(what, call)
    return runs, store1, store2


def test_synthetic():
    """No capture needed: the synthetic Ambit1 region of the create/delete test (Running,
    Transition, Cycling, each with SuuntoLink's new-mode 3-field screen), plus a 1-field screen."""
    region = LSM.patch_displays(M.synthetic(76), 0, [{"op": "add", "type": "1-row"}])
    never = b"\xff" * LA.STORE_SIZE
    app = b"IAMRULE\0" + bytes(range(200))
    new, store = LA.install(region, never, 0, 0, 2, app)
    assert LA.parse_store(store) == [app] and store[:3] == b"\x01\x00\x03"
    assert struct.unpack_from("<2I", store, 3) == (11, 11 + len(app) + 1)
    assert list(LA.mode_links(new)) == [0] and LA.mode_links(new)[0][1] == [0]
    assert LSM.region_used(new) == LSM.region_used(region) + 20
    assert LA._user_displays(new, 0)[1][0][2]["rows"][2] == [2, 0, [5, LA.APP_SLOT]]
    check_refused("1-field screen", lambda: LA.install(region, never, 0, 1, 0, app))
    check_refused("Center row", lambda: LA.install(region, never, 0, 0, 1, app))
    check_refused("empty store", lambda: LA.build_store([]))
    check_refused("full store", lambda: LA.build_store([app * 100]))
    # A store holding an app no mode shows is not rebuilt.
    check_refused("orphan", lambda: LA.install(region, store, 2, 0, 2, b"IAMRULE\0" + b"\x02" * 9))
    return new, store


def test_cli(region, store1, store2, bare):
    """legacy_link's two-region write: order, extents, copies, read-back, the put-back."""
    state = {"region": region, "store": store1, "writes": [], "fail_store": False}
    LSM.read_region_live = lambda length=0: state["region"][:length]
    legacy_link._apps_store_read = lambda: state["store"]

    def flash_write(addr, data, extra):
        state["writes"].append((addr, len(data), extra))
        if addr == LA.STORE_ADDR:
            if state["fail_store"]:
                raise RuntimeError("0x0b16 at 0x027300 rc=-1")
            state["store"] = data
        else:
            state["region"] = data
    legacy_link.flash_write = flash_write
    storm = LA.parse_store(store2)[0]
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "app.bin"
        path.write_bytes(storm)
        # Without the catalogue the store order cannot be checked against a catalogue number.
        try:
            legacy_link.app_install("Cycling", 0, 2, path, write=True, backup_dir=tmp)
            raise AssertionError("installed next to an app with no catalogue number")
        except ValueError:
            pass
        saved = LA.catalog_lookup
        LA.catalog_lookup = lambda d: ({LA.parse_store(store1)[0]: SUNSET, storm: STORM}.get, None)
        try:
            dry = legacy_link.app_install("Cycling", 0, 2, path, catalog_dir=tmp, backup_dir=tmp)
            assert dry["ok"] and not dry["wrote"] and not state["writes"]
            out = legacy_link.app_install("Cycling", 0, 2, path, catalog_dir=tmp, write=True,
                                          backup_dir=tmp)
        finally:
            LA.catalog_lookup = saved
        assert out["wrote"] and out["apps"] == 2 and state["store"] == store2
        assert state["writes"] == [(0x2000, 10240, 0xFFFFFFFF), (LA.STORE_ADDR, LA.STORE_SIZE, 0xFFFFFFFF)]
        assert sorted(p.name.split("-")[0] for p in pathlib.Path(tmp).glob("*-*.bin")) == ["apps", "sportmodes"]
        listed = legacy_link.apps_list()["entries"]
        assert [(e["mode"], e["storePlace"]) for e in listed] == [("Cycling", 0), ("Alpine Skiing", 1)]
        # A failed store write puts the sport modes back.
        state.update(region=bare, store=b"\xff" * LA.STORE_SIZE, fail_store=True)
        path.write_bytes(LA.parse_store(store1)[0])
        try:
            legacy_link.app_install(legacy_link._mode_arg("@9"), 0, 2, path, write=True,
                                    backup_dir=tmp)
            raise AssertionError("a failed store write went unreported")
        except RuntimeError as exc:
            assert "put back" in str(exc)
        assert state["region"] == bare and state["writes"][-1] == (0x2000, 10240, 0xFFFFFFFF)


if __name__ == "__main__":
    test_synthetic()
    if not AMBIT1.exists():
        print("legacy apps: synthetic OK; captures absent, rest skipped")
        sys.exit(0)
    runs, s1, s2 = test_captures()
    test_cli(runs[77], s1, s2, runs[75])
    print("legacy apps: SuuntoLink's two Ambit1 installs replayed byte for byte (sport modes and "
          "the Apps store), refusals and the two-region write OK")
