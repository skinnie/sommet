#!/usr/bin/env python3
"""The swim styles a watch has learned ("Swimming style training"): read, save, write back.

Where they live (found 2026-10-30): in the watch's SETTINGS, not in a flash region. Group
`sml.DeviceSettings.Sports.Swimming.StyleCoeff`, one record per stroke, each sent as its own
settings entry: `[u8 Style][4 x int16 LE Vectors.Item]`. Style is the schema's own enum
(Ambit3 Peak 2.4.17: 0 Other, 1 Butterfly, 2 Backstroke, 3 Breaststroke, 4 Freestyle,
5 Drill). A stroke the watch was never taught reads all zeros. Moveslink synced these to
Movescount as `Settings.SwimmingStyleCalibration` (ServiceAdapter.xml), so writing them back
is a normal settings write - Moveslink did it on every sync.

A watch whose schema has no StyleCoeff group (Traverse, Kailash) reports supported=false.
Ambit1/2 have no SBEM settings at all and are not handled here.

    ./tools/swim_styles.py                          # read and print (read-only)
    ./tools/swim_styles.py --json                   # same, one JSON line
    ./tools/swim_styles.py --save FILE.json         # read and save to FILE (read-only)
    ./tools/swim_styles.py --restore FILE.json      # dry run: what would change, nothing sent
    ./tools/swim_styles.py --restore FILE.json --write [--expect-serial SERIAL]

The write sends only the StyleCoeff entries (one 0x1101, like SuuntoLink sends one settings
screen at a time), after saving the watch's current settings to
~/AmbitAppBackups/swim-styles/, and is confirmed by reading the settings back. Strokes the
file doesn't list keep the watch's own values.
"""

import argparse
import datetime
import json
import struct
import sys

import sbem_schema
from settings_write import CMD_SETTINGS_WRITE, _find_field, _group_containing, build_write_payload
from write_nav import (CMD_DEVICE_INFO, CMD_SETTINGS_READ, Link, pre_write_backup_path,
                       schema_for_link)

FILE_KIND = "sommet-swim-styles"
STYLE_SUFFIX = "Swimming+StyleCoeff.Style"


def layout(schema):
    """(group_id, style_field, item_fields) of this schema's StyleCoeff group, or None if the
    watch has none. Checked, not assumed: the group must be one enum byte then int fields."""
    try:
        style = _find_field(schema, STYLE_SUFFIX)
    except KeyError:
        return None
    gid = _group_containing(schema, style.fid)
    if gid is None:
        return None
    members = [schema.fields[m] for m in schema.groups[gid]]
    if not members or members[0].fid != style.fid or len(members) < 2:
        return None
    items = members[1:]
    if any(not f.base.startswith("int") and not f.base.startswith("uint") for f in items):
        return None
    return gid, style, items


def style_names(style_field):
    """{id: name} from the schema's own enum text ("enum:0=Other,1=Butterfly,...")."""
    names = {}
    frm = style_field.frm or ""
    if frm.startswith("enum:"):
        for part in frm[5:].split(","):
            k, _, v = part.partition("=")
            if k.strip().isdigit():
                names[int(k)] = v.strip()
    return names


def _entries(payload):
    head = payload.find(sbem_schema.MAGIC)
    if head < 0:
        return None
    return list(sbem_schema.entries(payload[head:]))


def decode(schema, payload):
    """The watch's records in wire order: [{"id", "style", "vectors"}]. None if the reply has
    no SBEM payload; [] if this watch has no StyleCoeff group."""
    lay = layout(schema)
    entries = _entries(payload)
    if entries is None:
        return None
    if lay is None:
        return []
    gid, style, items = lay
    names = style_names(style)
    fmt = "<" + style.fmt.lstrip("<") + "".join(f.fmt.lstrip("<") for f in items)
    size = struct.calcsize(fmt)
    out = []
    for eid, data in entries:
        if eid != gid or len(data) != size:
            continue
        values = struct.unpack(fmt, data)
        out.append({"id": values[0], "style": names.get(values[0], f"Style {values[0]}"),
                    "vectors": list(values[1:])})
    return out


def encode(schema, record):
    _gid, style, items = layout(schema)
    fmt = "<" + style.fmt.lstrip("<") + "".join(f.fmt.lstrip("<") for f in items)
    return struct.pack(fmt, record["id"], *record["vectors"])


def plan_restore(schema, before, wanted):
    """(patches, changes, problem). `wanted` = records to put on the watch, matched to the
    watch's own records by stroke id. Refuses (problem set, no patches) rather than guessing
    when the file and the watch disagree on shape."""
    lay = layout(schema)
    if lay is None:
        return [], [], "this watch has no swim style settings"
    gid, style, items = lay
    current = decode(schema, before)
    if current is None:
        return [], [], "no settings in the watch's reply"
    if not current:
        return [], [], "the watch's settings reply carries no swim style records"
    by_id = {r["id"]: (n, r) for n, r in enumerate(current)}
    patches, changes = [], []
    for rec in wanted:
        sid = rec.get("id")
        vec = rec.get("vectors")
        if sid not in by_id:
            return [], [], f"stroke {rec.get('style', sid)!r} is not on this watch"
        if not isinstance(vec, list) or len(vec) != len(items):
            return [], [], (f"{rec.get('style', sid)}: {len(vec or [])} numbers, this watch "
                            f"stores {len(items)}")
        for f, v in zip(items, vec):
            try:
                struct.pack(f.fmt, v)
            except (struct.error, TypeError):
                return [], [], f"{rec.get('style', sid)}: {v!r} is not a value the watch can store"
        n, cur = by_id[sid]
        if cur["vectors"] == vec:
            continue
        patches.append((gid, n, encode(schema, {"id": sid, "vectors": vec})))
        changes.append({"id": sid, "style": cur["style"], "from": cur["vectors"], "to": vec})
    return patches, changes, None


def build_payload(schema, before, patches):
    """The 0x1101 payload: the read's own header (write flag set) + every StyleCoeff record,
    the patched ones replaced - nothing else from the settings."""
    payload, _omitted = build_write_payload(schema, before, [STYLE_SUFFIX], patches)
    return payload


def saved_file(info):
    """What --save writes and --restore reads."""
    return {"kind": FILE_KIND, "version": 1, "watch": info.get("watch"),
            "read": info.get("read"), "styles": info.get("styles", [])}


def load_file(path):
    data = json.loads(open(path, encoding="utf-8").read())
    styles = data.get("styles") if isinstance(data, dict) else None
    if not isinstance(styles, list):
        raise ValueError(f"{path}: no \"styles\" list - not a swim styles file")
    return data


def _emit(obj, as_json):
    if as_json:
        print(json.dumps(obj))
    elif obj.get("ok") is False:
        print("error:", obj.get("error"))
    else:
        for r in obj.get("styles", []):
            taught = any(r["vectors"])
            print(f"  {r['style']:14} {'taught' if taught else 'not taught':11} {r['vectors']}")
        for c in obj.get("changes", []):
            print(f"  {c['style']}: {c['from']} -> {c['to']}")
        for k in ("supported", "dry_run", "wrote", "confirmed", "backup", "note"):
            if k in obj:
                print(f"  {k}: {obj[k]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="one JSON line")
    ap.add_argument("--save", metavar="FILE", help="read and save the swim styles to FILE")
    ap.add_argument("--restore", metavar="FILE", help="put FILE's swim styles on the watch "
                    "(dry run unless --write)")
    ap.add_argument("--write", action="store_true", help="really send the --restore write")
    ap.add_argument("--expect-serial", metavar="SERIAL",
                    help="refuse to write unless the watch that opened has this serial")
    args = ap.parse_args()

    link = Link(dry_run=False, verbose=False)
    link.open()
    # Needed before any 0x1100 read (see settings_write.py main()).
    link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    descriptor = schema_for_link(link)
    if not descriptor.exists():
        _emit({"ok": False, "error": f"missing schema descriptor: {descriptor}"}, args.json)
        return 1
    schema = sbem_schema.load(descriptor)
    watch = {"serial": link.opened_serial, "productId": link.opened_product_id,
             "schema": descriptor.name}

    before = bytes(link.command(CMD_SETTINGS_READ, b"\0\0\0\0"))
    current = decode(schema, before)
    if current is None:
        _emit({"ok": False, "error": "the watch sent no settings"}, args.json)
        return 1
    now = datetime.datetime.now().isoformat(timespec="seconds")
    if layout(schema) is None:
        _emit({"ok": True, "supported": False, "watch": watch, "styles": []}, args.json)
        return 0 if not args.restore else 1
    info = {"ok": True, "supported": True, "watch": watch, "read": now, "styles": current}

    if not args.restore:
        if args.save:
            with open(args.save, "w", encoding="utf-8") as f:
                json.dump(saved_file(info), f, indent=1)
            info["saved"] = args.save
        _emit(info, args.json)
        return 0

    try:
        wanted = load_file(args.restore)["styles"]
    except (OSError, ValueError) as exc:
        _emit({"ok": False, "error": str(exc)}, args.json)
        return 1
    patches, changes, problem = plan_restore(schema, before, wanted)
    if problem:
        _emit({"ok": False, "error": problem, "watch": watch}, args.json)
        return 1
    result = {"ok": True, "watch": watch, "changes": changes}
    if not changes:
        result.update(wrote=False, note="the watch already has these swim styles")
        _emit(result, args.json)
        return 0
    payload = build_payload(schema, before, patches)
    if not args.write:
        result.update(dry_run=True, payload_hex=payload.hex())
        _emit(result, args.json)
        return 0
    if args.expect_serial and args.expect_serial != link.opened_serial:
        _emit({"ok": False, "error": f"refusing: expected watch {args.expect_serial}, "
               f"{link.opened_serial} is connected"}, args.json)
        return 1

    # What is about to be overwritten goes to disk first: the whole settings reply (raw) and
    # the swim styles as a file --restore takes back.
    raw_path = pre_write_backup_path("settings_pre_swim", link, kind="swim-styles", ext="bin")
    raw_path.write_bytes(before)
    json_path = raw_path.with_suffix(".json")
    json_path.write_text(json.dumps(saved_file(info), indent=1))

    link.command(CMD_SETTINGS_WRITE, payload)
    after = decode(schema, bytes(link.command(CMD_SETTINGS_READ, b"\0\0\0\0"))) or []
    got = {r["id"]: r["vectors"] for r in after}
    confirmed = all(got.get(c["id"]) == c["to"] for c in changes)
    result.update(wrote=True, confirmed=confirmed, backup=str(json_path), ok=confirmed)
    if not confirmed:
        result["error"] = "the watch did not keep the new swim styles (re-read differs)"
    _emit(result, args.json)
    return 0 if confirmed else 1


if __name__ == "__main__":
    sys.exit(main())
