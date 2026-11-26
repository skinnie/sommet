#!/usr/bin/env python3
"""Ambit1 Suunto Apps (App Zone): the rule store and a sport mode's app link (watch-protocol/10 L1).

What SuuntoLink 4.1.15 writes when an app is put on an Ambit1 sport mode, read from its captures
of André's Ambit1 (assets/pcap/2026-08-23-ambit1-suuntolink/zzambit1full.pcap, two installs;
ambit1languages.pcap, the same two apps rewritten) and from its own code (the rule-store builder
BluebirdRules.cpp saveRuleTreeToDevice in SDSApplicationServer.exe, decompiled; openambit's
serialize_app_data writes the same layout):

  The rule store, Devices.xml rulestorelocation 160000 (0x27100), rulestoresize 20000:
      [u16 n][u8 byte0 ^ byte1 ^ 2][u32 offset x (n + 1)]   offset 0 = 4n + 7, the header's length
      then per app: the app's compiled bytes as SuuntoLink's catalogue holds them (IAMRULE...),
      then one checksum byte (XOR of the app's bytes and of its length's low byte); offset k + 1
      is where app k + 1 starts, the last one the used extent. The rest of the 20000 is 0xFF.
      Written as the sport-mode region is: 0x0b16 in 512-byte chunks from 0x27100 (39 x 512 +
      32), then 0x0b18 [0x27100][0xFFFFFFFF]. Never written before the first install (it read
      back FF FF FF).
  The mode that shows the app, in the sport-mode region (one region write before the store's):
      0x010C { 0x010D [u16 app's place in the store][u16 1][u16 0] } appended to its 0x0101, and
      field 51 (0x0033, the mode's app slot) added to the multi-value row of one of its own
      screens. SuuntoLink's getMaxSuuntoApps is 1 on the Ambit1 (one app per mode). Its row
      editor (ui/sport_mode_display_editor.js showRowData) saves the row's values in the order of
      its menu (getDisplayRows: assets/sportmode_rows.json, the menu order tools/row_bridge.py
      gives), then the app - which is why capture 2 below also reorders Cycling's four values.

Captured, each between two consecutive writes (tools/test_legacy_apps.py replays them byte for
byte, the mode's display time in 0x01FF aside, left as read as by every display edit here). Both
put the app on a 3-field display's bottom row, so only that row takes one here:
  1. Alpine Skiing gets Sunrise/Sunset (catalogue 10000003), the store empty before: store
     [Sunrise/Sunset], Alpine Skiing's Bottom row [5] -> [5, 51], its 0x010D place 0.
  2. Cycling gets Storm Alarm (10000002), Sunrise/Sunset already on Alpine Skiing: store [Storm
     Alarm, Sunrise/Sunset], Cycling's Bottom row [1, 12, 23, 10] -> [12, 10, 1, 23, 51] (the menu
     order, then the app) and place 0, Alpine Skiing's place 0 -> 1.
Where a new app goes in the store is not settled by these two: in capture 2 the new app comes
first, and it is also on the earlier mode, has the lower catalogue number and the lower bytes.
SuuntoLink rebuilds the store in C++ code this project has not traced, so install() writes only
when all four orders agree (always for a first app; for a second, when the new app is first by
all of them, as captured), and says so otherwise. Removing an app is in no capture (and running
capture 2 backwards would not give the write before it: SuuntoLink's editor had reordered the row),
so Sommet does not remove one. Apps on the Ambit2 / 2S / 2R: no capture writes their store
(Devices.xml 600000/200000; the Ambit2 capture only reads it, empty) - refused.

Not seen on a real watch: André no longer has an Ambit1 (routes/03).
"""
import struct

import legacy_sport_modes as LSM

STORE_ADDR = 160000                       # Devices.xml rulestorelocation (Ambit1)
STORE_SIZE = 20000                        # Devices.xml rulestoresize
COMMIT_EXTRA = 0xFFFFFFFF                 # the store's 0x0b18 tail, as captured
APP_SLOT = 0x0033                         # the mode's (one) app as a row value, both captures
TAG_APP_LINK, TAG_APP_ENTRY = 0x010C, 0x010D
MAX_APPS_PER_MODE = 1                     # sport_mode.js getMaxSuuntoApps(AMBIT)
APP_LAYOUT, APP_ROW = 260, 2              # a 3-field display's bottom row, both captures
AMBIT1_ONLY = ("Suunto Apps are written on the Ambit1 only: no capture shows SuuntoLink writing an "
               "Ambit2, 2S or 2R's apps, so Sommet does not change them")


def _xor(data):
    x = 0
    for b in data:
        x ^= b
    return x


def parse_store(data):
    """The store's apps (each one's bytes, its checksum checked), in store order. A store never
    written (FF FF FF, the capture's state before the first install) has none. Raises ValueError
    for anything else that is not this layout."""
    data = bytes(data)
    if data[:3] == b"\xff\xff\xff":
        return []
    if len(data) < 7:
        raise ValueError("the Apps store read short")
    n = struct.unpack_from("<H", data, 0)[0]
    if data[2] != data[0] ^ data[1] ^ 2 or 7 + 4 * n > len(data):
        raise ValueError("the Apps store's header is not the one SuuntoLink writes")
    offsets = struct.unpack_from(f"<{n + 1}I", data, 3)
    if offsets[0] != 4 * n + 7 or list(offsets) != sorted(offsets) or offsets[-1] > len(data):
        raise ValueError("the Apps store's directory does not hold together")
    apps = []
    for k in range(n):
        app, check = data[offsets[k]:offsets[k + 1] - 1], data[offsets[k + 1] - 1]
        if not app or check != _xor(app) ^ (len(app) & 0xFF):
            raise ValueError(f"app {k + 1} in the Apps store fails its checksum")
        apps.append(bytes(app))
    return apps


def build_store(apps, size=STORE_SIZE):
    """The store holding `apps` (their bytes, in order), 0xFF to `size`: BluebirdRules.cpp's
    layout, as both captured stores are written. At least one app (an empty store is in no
    capture)."""
    n = len(apps)
    if n == 0:
        raise ValueError("an empty Apps store is in no capture: not written")
    head = bytearray(struct.pack("<H", n))
    head.append(head[0] ^ head[1] ^ 2)
    offsets, body, at = [4 * n + 7], bytearray(), 4 * n + 7
    for app in apps:
        body += app + bytes([_xor(app) ^ (len(app) & 0xFF)])
        at += len(app) + 1
        offsets.append(at)
    out = bytes(head) + struct.pack(f"<{n + 1}I", *offsets) + bytes(body)
    if len(out) > size - 1:                # saveRules: "Out of rule FLASH area"
        raise ValueError(f"the apps need {len(out)} of the Apps store's {size} bytes")
    return out + b"\xff" * (size - len(out))


def mode_links(region):
    """{mode place in 0x0100: (0x010C header offset, [store places])} for the modes that link an
    app."""
    out = {}
    _at, modes = LSM._mode_list(region)
    for place, (at, ln, _name, _blen) in enumerate(modes):
        for tag, body, n in LSM._children(region, at + 4, at + ln):
            if tag == TAG_APP_LINK:
                refs = [struct.unpack_from("<H", region, b)[0]
                        for t, b, n2 in LSM._children(region, body, body + n)
                        if t == TAG_APP_ENTRY and n2 == 6]
                out[place] = (body - 4, refs)
    return out


def _link_record(store_place):
    return LSM._tlv(TAG_APP_LINK, LSM._tlv(TAG_APP_ENTRY, struct.pack("<HHH", store_place, 1, 0)))


def _check_ambit1(region):
    lens = {n for _o, n in LSM.named_blobs(region)}
    if lens != {LSM.AMBIT1_BLOB_LEN}:
        raise ValueError(AMBIT1_ONLY)


def _mode(region, mode):
    """(index in the readers' list, name, place in 0x0100) of `mode` (an index or a name)."""
    blobs = LSM.named_blobs(region)
    names = [region[o:o + LSM.NAME_LEN].split(b"\x00")[0].decode("latin-1") for o, _ in blobs]
    if isinstance(mode, str):
        if mode not in names:
            raise ValueError(f"no sport mode named {mode!r} on the watch")
        k = names.index(mode)
    else:
        k = int(mode)
        if not 0 <= k < len(names):
            raise ValueError(f"sport mode {k} not found (the watch has {len(names)})")
    return k, names[k], LSM.named_mode_places(region)[k]


def _user_displays(region, mode):
    """(where, [(body offset, length, parsed)]) of the mode's own screens (type 10)."""
    where = LSM._mode_records(region, mode)
    if where["displays"] is None:
        raise ValueError("this sport mode has no display list")
    db, dl = where["displays"]
    out = []
    for tag, body, n in LSM._children(region, db, db + dl):
        disp = LSM.parse_display(region[body - 4:body + n]) if tag == LSM.TAG_DISPLAY else None
        if disp is None or disp["type"] != LSM.USER_DISPLAY:
            break
        out.append((body - 4, n + 4, disp))
    return where, out


def _relink(region, places_by_mode):
    """`region` with each linking mode's 0x010D store place rewritten (same lengths)."""
    out = bytearray(region)
    for place, (head, _refs) in mode_links(region).items():
        if place in places_by_mode:
            end = head + 4 + LSM._u16(region, head + 2)
            for tag, body, _n in LSM._children(region, head + 4, end):
                if tag == TAG_APP_ENTRY:
                    struct.pack_into("<H", out, body, places_by_mode[place])
    return bytes(out)


def _rows_rewrite(region, record_at, record_len, new_record, extra_inserts=()):
    """`region` with one display record replaced (and records inserted), every enclosing length
    fixed (legacy_sport_modes._rewrite applies the cut before the insert at the same offset)."""
    cuts = [(record_at, record_len)]
    inserts = [(record_at, new_record, LSM._enclosing(region, record_at))] + list(extra_inserts)
    return LSM._rewrite(region, cuts, inserts)


def _row_menu(region, k):
    """The watch field ids of SuuntoLink's menu for a 3-field display's bottom row on mode `k`'s
    sport, in its order (tools/row_bridge.py over assets/sportmode_rows.json). The menu there is
    the Ambit3 Peak's: SuuntoLink's getDisplayRows only leaves rows out per model, never reorders,
    and its order for Cycling's four values is the one capture 2 shows."""
    import row_bridge                                       # noqa: PLC0415
    blob, blen = LSM.named_blobs(region)[k]
    activity = LSM.decode_settings(region[blob:blob + blen]).get("activity_id", 0)
    return row_bridge.allowed_field_ids(row_bridge.load_rows(), activity, APP_LAYOUT, "BOTTOM") or []


def _store_orders(apps, owner_place, new_app, new_place, rule_id_of):
    """The store order after adding `new_app` (on the mode at `new_place`) by each rule the two
    captures leave open: [by mode place, by catalogue number, new first, by bytes]."""
    places = {a: owner_place[a] for a in apps}
    places[new_app] = new_place
    every = list(apps) + [new_app]
    ids = {a: rule_id_of(a) if rule_id_of else None for a in every}
    orders = [sorted(every, key=lambda a: places[a]), [new_app] + list(apps), sorted(every)]
    if all(ids[a] is not None for a in every):
        orders.insert(1, sorted(every, key=lambda a: ids[a]))
    else:
        orders.insert(1, None)
    return orders


def install(region, store, mode, display, row, app, rule_id_of=None):
    """(sport-mode region, Apps store) with `app` (its catalogue bytes) put on one mode, on the
    bottom row of one of its 3-field screens, as SuuntoLink's editor saves that row. `mode` is the
    mode's place in the readers' list (int) or its name; `display` the screen's index among the
    mode's own screens; `row` the row's index in that display (Bottom 2, one field per row).
    `rule_id_of(bytes)` gives an app's catalogue number (None if not in it). Raises ValueError for
    anything the captures do not show."""
    _check_ambit1(region)
    apps = parse_store(store)
    app = bytes(app)
    if not app.startswith(b"IAMRULE"):
        raise ValueError("this is not a compiled Suunto App (no IAMRULE header)")
    k, name, place = _mode(region, mode)
    links = mode_links(region)
    if place in links:
        raise ValueError(f"{name!r} already shows a Suunto App: the Ambit1 holds "
                         f"{MAX_APPS_PER_MODE} per sport mode")
    if app in apps:
        raise ValueError("this app is already on another sport mode of this watch; one app on two "
                         "modes is in no capture")
    owner = {}
    for p, (_h, refs) in links.items():
        for r in refs:
            if not 0 <= r < len(apps):
                raise ValueError("a sport mode links an app the store does not hold: nothing changed")
            owner.setdefault(apps[r], p)
    if any(a not in owner for a in apps):
        raise ValueError("the Apps store holds an app no sport mode shows: nothing changed")
    if apps:
        orders = _store_orders(apps, owner, app, place, rule_id_of)
        if any(o is None or o != orders[0] for o in orders):
            raise ValueError("where SuuntoLink puts this app in the Apps store is not in a capture "
                             "(it is when the new app is on an earlier sport mode than the others, "
                             "with a lower catalogue number): not written")
        new_apps = orders[0]
    else:
        new_apps = [app]                   # capture 1: the first app
    where, screens = _user_displays(region, k)
    if not 0 <= int(display) < len(screens):
        raise ValueError(f"display {display} is not one of this mode's {len(screens)} own screens")
    rec_at, rec_len, disp = screens[int(display)]
    rows = disp["rows"]
    if disp["layout"] != APP_LAYOUT or int(row) != APP_ROW or len(rows) != 3 \
            or rows[APP_ROW][0] != APP_ROW:
        raise ValueError("both captures put the app on the bottom row of a 3-field display; "
                         "pick that row")
    target = rows[APP_ROW]
    if target[1] != 0 or not target[2]:
        raise ValueError("this bottom row shows no list of values, which no capture adds an app to")
    if len(target[2]) >= LSM.MAX_ROW_VALUES:
        raise ValueError(f"this row holds {LSM.MAX_ROW_VALUES} values at most: take one off first")
    menu = _row_menu(region, k)
    if len(set(target[2])) != len(target[2]) or any(v not in menu for v in target[2]):
        raise ValueError("a value on this row is not in SuuntoLink's menu for it, so where its "
                         "editor would put it is not known: not written")
    target[2] = sorted(target[2], key=menu.index) + [APP_SLOT]
    new_record = LSM.display_record(disp["layout"], rows, disp["type"])
    mode_end = where["mode"] + 4 + LSM._u16(region, where["mode"] + 2)
    link = (mode_end, _link_record(new_apps.index(app)), [where["mode"], where["modes"], 0])
    out = _rows_rewrite(region, rec_at, rec_len, new_record, [link])
    out = _relink(out, {p: new_apps.index(a) for a, p in owner.items()})
    return out, build_store(new_apps)


def installed(region, store, rule_id_of=None, name_of=None):
    """[{"mode", "place", "storePlace", "ruleId", "name"}] for each mode that shows an app."""
    apps = parse_store(store)
    blobs = LSM.named_blobs(region)
    names = {p: region[o:o + LSM.NAME_LEN].split(b"\x00")[0].decode("latin-1")
             for p, (o, _n) in zip(LSM.named_mode_places(region), blobs)}
    out = []
    for place, (_h, refs) in sorted(mode_links(region).items()):
        for r in refs:
            app = apps[r] if 0 <= r < len(apps) else None
            out.append({"mode": names.get(place, ""), "place": place, "storePlace": r,
                        "ruleId": rule_id_of(app) if app and rule_id_of else None,
                        "name": name_of(app) if app and name_of else None})
    return out


def catalog_lookup(catalog_dir):
    """(rule_id_of, name_of) over SuuntoLink's catalogue as tools/extract_apps_catalog.py ships it
    (catalog.json + catalog.bin): an app is known by its exact bytes, as SuuntoLink's own
    suunto_apps.js findApp knows it. Both answer None for an app not in it (a private one)."""
    import json                                             # noqa: PLC0415
    import pathlib                                          # noqa: PLC0415
    folder = pathlib.Path(catalog_dir)
    entries = json.loads((folder / "catalog.json").read_text())["entries"]
    by_len = {}
    for e in entries:
        by_len.setdefault(e["binaryLength"], []).append(e)
    cache = {}

    def find(app):
        app = bytes(app)
        if app not in cache:
            cache[app] = None
            with open(folder / "catalog.bin", "rb") as f:
                for e in by_len.get(len(app), []):
                    f.seek(e["binaryOffset"])
                    if f.read(e["binaryLength"]) == app:
                        cache[app] = e
                        break
        return cache[app]
    return (lambda app: (find(app) or {}).get("ruleId"),
            lambda app: (find(app) or {}).get("name"))
