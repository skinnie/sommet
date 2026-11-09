#!/usr/bin/env python3
"""Decode the Ambit3/Kailash `0x1104` schema push into a full field -> SBEM-id table.

Own file, this project's own "one file per format" convention (see `ble_routes.py`'s own
docstring for the rule). The `0x1104` "schema push" is the self-describing field-path tree
the watch streams to the phone right after the handshake (26 chunks, ~24.7 KB in the real
`ambit3suuntoappbetawithroutesandactivities_thennewrouteandpoi.pklg`). It is the ground
truth that maps every wire SBEM entry id to its `sml.` path and value type, so decoding it
turns any raw `0x1201`/`0x1200`/`0x1101` capture from "entry 0xf6 = ?" into
"entry 0xf6 = sml.EventBoard.Event.SyncRequest.SyncScope".

Record wire format (one per field), reverse-engineered from the real capture:

    "<FRM>" <type> 0x0a "<PTH>" <dotted.path> 0x00 <trailer>

where <trailer> is 4 bytes `00 <marker> <id> <bank>`: <id> is the on-wire SBEM entry id
(byte-confirmed: `sml.DeviceLogBook.Summary.Count` decodes to id 0x5a, exactly one of
`write_nav.py`'s own known logbook entry ids), <bank> selects the id page (0x00 for the
first ~256 fields, 0x01 for the overflow), and <marker>'s role is not yet pinned (looks
like a per-field checksum/type tag - never needed to read a value, so left unnamed rather
than guessed). A record's <type> is itself a small grammar: a bare primitive
(`uint16`, `bool`, `utf8`, `float32`, ...), an `enum:1=Full,2=Moves,...` value map, or a
primitive followed by `\n<MOD>k1*x,k2*y` scale factors (e.g. Energy's `4186.8*x` = kcal<->J,
matching this project's own energy=kcal finding). `<QRY>` records (query stubs, no <FRM>)
are skipped - they carry a path but no field.

Key finding this tool exists to make reproducible (2026-08-16): 0x1201 is a *generic*
single-entry SBEM push whose meaning is entirely its entry id. Over BLE the Suunto app uses
it for `EventBoard` events (0xf5 NewMove.DateTime, 0xf6 SyncRequest.SyncScope), NOT to write
the per-activity synced flag. `IsMcSynced` (id 0x5c, read-only in the 0x1200 logbook
summary) never appears as a wire write in either activity capture. See
docs/explanation/kailash-ble-findings.md and the sommet memory for the fuller writeup.

    ./tools/ble_schema.py CAPTURE.pklg              # full field table, sorted by id
    ./tools/ble_schema.py CAPTURE.pklg --grep Sync  # only paths matching a substring
    ./tools/ble_schema.py CAPTURE.pklg --id 0xf6    # look up one entry id
    ./tools/ble_schema.py CAPTURE.pklg --json
"""

import argparse
import json
import re
import sys

from ble_pklg import messages

SCHEMA_COMMAND = 0x1104
# One field record. The path may embed '+' as a separator variant (seen in
# `Entries+Entry.Time`), kept verbatim. Non-greedy trailer stops at the next record marker.
_RECORD = re.compile(rb"<FRM>(.*?)\x0a<PTH>(.*?)\x00(.*?)(?=<FRM>|<QRY>|\Z)", re.S)


class Field:
    __slots__ = ("path", "type", "sbem_id", "bank", "marker")

    def __init__(self, path, type_, sbem_id, bank, marker):
        self.path = path
        self.type = type_
        self.sbem_id = sbem_id
        self.bank = bank
        self.marker = marker

    def as_dict(self):
        return {
            "path": self.path,
            "type": self.type,
            "sbem_id": self.sbem_id,
            "bank": self.bank,
        }


def schema_blob(path):
    """Reassemble the 0x1104 payload chunks (phone-side, in packet order) into one blob.

    The watch streams the schema as many ~1 KB `0x1104` messages; ble_pklg groups them by
    (handle, direction) so within one direction they arrive in capture order, but pkt_num
    is the authoritative sequence - sort by it before joining."""
    chunks = [m for m in messages(path)
              if m.command == SCHEMA_COMMAND and len(m.payload) > 4]
    if not chunks:
        return b""
    chunks.sort(key=lambda m: m.pkt_num)
    return b"".join(m.payload for m in chunks)


def parse_fields(blob):
    """Every field record in a schema blob, in wire order."""
    fields = []
    for m in _RECORD.finditer(blob):
        type_ = m.group(1).decode("latin1").replace("\n", " | ")
        path = m.group(2).decode("latin1")
        trailer = m.group(3)
        if len(trailer) < 4:
            # A few records carry an inline default/example value before the 4-byte id
            # (e.g. the Altitude field embeds a huge <GRP> list); the id descriptor is
            # still the last 4 bytes ending in the bank byte. Fall back to the tail.
            if len(trailer) < 4:
                continue
        sbem_id, bank, marker = trailer[-2], trailer[-1], trailer[-3]
        fields.append(Field(path, type_, sbem_id, bank, marker))
    return fields


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("capture", help="a .pklg/.btsnoop BLE capture containing a 0x1104 push")
    ap.add_argument("--grep", metavar="SUBSTR", help="only paths containing this substring")
    ap.add_argument("--id", metavar="0xNN", help="look up a single SBEM entry id")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    blob = schema_blob(args.capture)
    if not blob:
        print(f"no 0x1104 schema push found in {args.capture}", file=sys.stderr)
        return 1

    fields = parse_fields(blob)

    if args.id is not None:
        want = int(args.id, 0)
        fields = [f for f in fields if f.sbem_id == want]
    if args.grep:
        needle = args.grep.lower()
        fields = [f for f in fields if needle in f.path.lower()]

    if args.json:
        print(json.dumps([f.as_dict() for f in fields], indent=2))
        return 0

    print(f"{len(fields)} field(s) from {args.capture} ({len(blob)} schema bytes)\n")
    for f in sorted(fields, key=lambda f: (f.bank, f.sbem_id)):
        print(f"  id=0x{f.sbem_id:02x} bank={f.bank}  {f.path}")
        print(f"           [{f.type}]")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
    raise SystemExit(main())
