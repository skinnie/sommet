#!/usr/bin/env python3
"""Decodes the Ambit3 "Apps" flash region - where a Suunto App's compiled bytecode gets
written once assigned to a sport mode's display via SuuntoLink.

**SOLVED, 2026-08-08 (docs/training_program_andre.md Finding 25), from 4 real, clean USBPcap
captures of SuuntoLink actually installing apps** (`assets/ambit3 pcap/v2/`, provided by
André - each one a single deliberate action, exactly the "genuine gap" Finding 19 flagged as
missing). The whole region write is a real, self-describing directory, not a flat list of
opaque entries:

    [u16 num_entries][u16 unknown2][u32 entry_offset]*num_entries [u32 total_length]
    then, back to back, one block per entry at its own entry_offset:
        [u8 reserved=0][u8 activityId][u8 marker][name, null-padded to fill 29 bytes]
        [8-byte "IAMRULE\\0" magic][binary bytes, up to the next entry_offset or total_length]

Verified byte-exact against all 4 real captures (3-5 entries each) AND against a real live
11-entry region read straight off the reference watch: every `entry_offset` in the table points
exactly at a real entry, every entry's magic is exactly 32 bytes after its own `entry_offset`
(confirming the 3-byte header + 29-byte name field is genuinely fixed-size), `activityId`
matches the app's real catalog `activityId` for every single entry with a catalog match, and
`total_length` (the table's last value) always equals the write's own real total length exactly
- zero exceptions across close to 20 real entries checked this way. `num_entries` is the real
entry count in every sample checked. `unknown2` varies (1, 1, 6, 7, 9 across the 5 samples
checked) and is NOT yet explained - not entry count, not directly a RuleIdx, still open.
One real edge case found live: an app name longer than 29 bytes (a 38-char name) simply isn't
null-terminated within its own block and reads into the following magic - the client doesn't
appear to truncate, so this project's own writer should.

**This confirms the whole region is rewritten (directory + all live entries) on every single
install**, not appended to - explains every earlier "field_a/field_b/field_c look inconsistent
across entries" observation without needing per-entry hacks: those were reads of different
directory generations, not fields with unstable meaning.

**Retraction of Finding 23's retraction**: the "activityId + marker" theory from Finding 22 was
right all along. Finding 23 called it wrong after checking it against 6 new real entries, but
that check used the flawed "backward-scan from magic" heuristic (the same one this whole
docstring's history has repeatedly shown breaks once more than one entry is present) instead of
the real fixed-offset block now confirmed above - the theory wasn't wrong, the extraction method
checking it was. Re-run against those same "disproving" entries (`Cooper estimate`,
`Real Temerature`, `25m Swimming pool counter`) with the real fixed offsets: all three decode
cleanly, `activityId` matches their real catalog values exactly (3, 1, 6), no exceptions.

`build_apps_entry()`/`find_apps_free_offset()` in `workout_install.py` are rewritten to match
this real format - see that module for what changed.

    ./tools/apps.py --from /tmp/dump_Apps.bin --catalog ".../suunto-apps/index.json"
"""

import argparse
import json
import pathlib
import struct

APPS_BASE = 0x0927C0
APPS_SIZE = 200000  # confirmed live via 0x0b21, 2026-08-05

MAGIC = b"IAMRULE\x00"
ENTRY_HEADER_LEN = 3  # [u8 reserved=0][u8 activityId][u8 marker]
NAME_LEN = 29  # null-padded (or truncated if the real name doesn't fit - see module docstring)
ENTRY_BLOCK_LEN = ENTRY_HEADER_LEN + NAME_LEN  # 32: entry_offset -> magic, fixed, confirmed


def entry_checksum(binary):
    """The per-entry 'marker' byte (SOLVED 2026-08-09, Finding 29): a 1-byte XOR checksum of
    the entry's payload = the 8-byte IAMRULE magic followed by the compiled binary, XORed with
    the low byte of that payload's length. This is exactly openambit's own
    `calculate_app_rule_checksum` (src/libambit/sport_mode_serialize.c) - independent community
    prior art for the Ambit app-rule format - applied to MAGIC+binary. Verified byte-exact
    against all 26 real entries this project has (the live 11-entry region plus all 4
    assets/ambit3 pcap/v2/ install captures), zero exceptions. openambit places this checksum
    as a trailing byte after each app and keeps a separate checksum-position table; the real
    Ambit3 layout instead carries it here in the fixed entry header, but the formula is
    identical - strong cross-validation of both."""
    payload = MAGIC + bytes(binary)
    c = 0
    for x in payload:
        c ^= x
    return c ^ (len(payload) & 0xFF)


def decode(data):
    """Finds every real app entry via the region's own directory table (see module
    docstring) - not by scanning for the IAMRULE magic and guessing backwards, which is what
    every earlier version of this function did and which is exactly what made per-entry
    fields look unreliable once more than one entry was present. Returns [] if the header
    doesn't look like a real directory (e.g. an empty/all-0xFF region)."""
    if len(data) < 4:
        return []
    num_entries, unknown2 = struct.unpack_from("<HH", data, 0)
    # unknown2 SOLVED 2026-08-09 (Finding 29) via openambit's serialize_app_data: it's
    # num_entries ^ 0x02 (verified: 11^2=9, 3^2=1, 5^2=7, 4^2=6 across real samples). Kept as a
    # sanity signal, not required for decoding.
    table_len = 4 + 4 * (num_entries + 1)
    if num_entries == 0 or num_entries > 1000 or table_len > len(data):
        return []  # doesn't look like a real directory - don't guess further
    table = struct.unpack_from(f"<{num_entries + 1}I", data, 4)
    if table[0] != table_len:
        return []  # first entry_offset must equal the directory's own size - real invariant
    total_length = table[-1]
    entries = []
    for i in range(num_entries):
        off = table[i]
        magic_off = off + ENTRY_BLOCK_LEN
        if data[magic_off:magic_off + len(MAGIC) - 1] != MAGIC[:-1]:
            entries.append({"entry_offset": off, "unknown2": unknown2,
                             "_warning": "magic not found where the directory says it should "
                             "be - region doesn't match this format"})
            continue
        reserved, activity_id, marker = data[off], data[off + 1], data[off + 2]
        name_field = data[off + ENTRY_HEADER_LEN:off + ENTRY_BLOCK_LEN]
        # CORRECTED 2026-08-22: was iso-8859-15 - real hardware (André's French Ambit3
        # Sport) proved the watch sends UTF-8 for name fields, see ambit_format.py's own
        # encode_name() header comment for the mojibake evidence.
        name = name_field.split(b"\0", 1)[0].decode("utf-8", "replace")
        bin_start = magic_off + len(MAGIC)
        bin_end = table[i + 1] if i + 1 < num_entries else total_length
        entry = {
            "entry_offset": off, "reserved": reserved, "activityId": activity_id,
            "marker": marker, "name": name, "magic_offset": magic_off, "unknown2": unknown2,
            "binary": data[bin_start:bin_end],
        }
        # Bytecode header fields, decoded 2026-08-31 against a genuine Movescount-compiled
        # app recovered from André's Ambit3 Sport (backups/finch-sport-2026-08-31) plus a
        # catalog-wide correlation (13k SuuntoLink binaries x the corpus metadata, joined on
        # RuleID, zero exceptions): u32 @4 of the binary is the display divisor mapped 1:1
        # from Movescount's OutputFormatID (1=integer, 10=one decimal, 100=two decimals,
        # 255=time format), and u32 @12 is the Movescount RuleID (0 on compiler-built apps).
        if len(entry["binary"]) >= 16:
            fmt_div, rule_id = struct.unpack_from("<I", entry["binary"], 4)[0], \
                struct.unpack_from("<I", entry["binary"], 12)[0]
            entry["outputFormatDivisor"] = fmt_div
            entry["ruleId"] = rule_id
        entries.append(entry)
    return entries


def read_apps_region(link, label="Apps"):
    """Reads only as much of the real Apps region as its own directory says is actually
    used (`total_length`, the last entry of its own offset table) instead of the full
    200,000-byte declared region size. Real, 2026-08-09 ("check if we can implement the
    same speed hack for routes and POis that we did for activities... apply the same to
    apps"). Live-verified: a full read of this region took 8.4s; real total_length values
    seen so far are a few KB. Unlike TrackLog's own version of this fix (a heuristic bad-
    streak cutoff, no exact boundary available), this region has a real, exact boundary -
    a small probe of the directory table alone is enough to know precisely how much more
    to read. Mirrors decode()'s own validation (not calling decode() on the possibly-
    truncated probe directly - byte-slicing past a short buffer doesn't raise, so a
    truncated decode could silently return wrong, truncated binaries instead of erroring)
    - same safety net every other version of this fix uses: falls back to the full,
    always-correct read if the probe doesn't look like a real directory."""
    from write_nav import read_flash
    probe = read_flash(link, APPS_BASE, 4096, label=f"{label} (probe)")
    if len(probe) >= 4:
        num_entries, _unknown2 = struct.unpack_from("<HH", probe, 0)
        table_len = 4 + 4 * (num_entries + 1)
        if 0 < num_entries <= 1000 and table_len <= len(probe):
            table = struct.unpack_from(f"<{num_entries + 1}I", probe, 4)
            if table[0] == table_len:
                total_length = table[-1]
                if total_length <= len(probe):
                    return probe[:total_length]
                return read_flash(link, APPS_BASE, total_length, label=label)
    return read_flash(link, APPS_BASE, APPS_SIZE, label=label)


def match_catalog(binary, catalog):
    for e in catalog:
        if bytes(e["binary"]) == binary:
            return e
    return None


# NOT shipped/committed (2026-08-12 - this project will be public, and the catalog is
# thousands of individual Movescount community members' compiled apps plus Suunto's own
# compiled/hosted catalog, neither of which the interoperability basis this project relies on
# extends to redistributing - see docs/PROJECT_OVERVIEW.md's "Scope and legal basis"). Built
# locally instead, lazily, from the user's OWN copy of suunto-apps/index.json - metadata-only
# entries plus offset/length pointers into a separate blob file, loaded/matched without ever
# holding all 13,104 binaries in memory at once. `--catalog` above still supports pointing
# straight at a raw index.json for a one-off research match; these two helpers are for the
# fast local cache `ensure_catalog_built()` produces from one.
DEFAULT_CATALOG_DIR = pathlib.Path(__file__).resolve().parent.parent / "data" / "suunto_apps"
# Where a user drops their own suunto-apps/index.json - see
# data/suunto_apps_source/README.md for exactly where to find it on their own machine. Never
# committed (gitignored), same as the built cache above.
SOURCE_INDEX_PATH = (pathlib.Path(__file__).resolve().parent.parent / "data"
                     / "suunto_apps_source" / "index.json")


def ensure_catalog_built(catalog_dir=DEFAULT_CATALOG_DIR, source_path=SOURCE_INDEX_PATH):
    """Builds/refreshes the fast local cache (catalog.json + catalog.bin) from the user's own
    dropped-in suunto-apps/index.json if needed - the cache doesn't exist yet, or the source
    file is newer than it (the user replaced it with a fresher/different copy). No-op
    otherwise, so routine calls stay cheap (an mtime comparison, not a re-extraction).

    Tries the drop-in `source_path` first, then falls back to auto-detecting a real,
    currently-installed SuuntoLink on this machine (`suuntolink_catalog.find_index_json()`) -
    a real convenience on Mac/Windows where SuuntoLink is often already there, with the
    drop-in file as the one path that also works on Linux and for anyone without SuuntoLink
    installed. Raises FileNotFoundError with a message meant to be shown to a person if
    neither source is available and there is no cache to fall back on."""
    catalog_dir = pathlib.Path(catalog_dir)
    source_path = pathlib.Path(source_path)
    cache_marker = catalog_dir / "catalog.json"

    real_source = source_path if source_path.is_file() else None
    if real_source is None:
        import suuntolink_catalog
        found = suuntolink_catalog.find_index_json()
        if found:
            real_source = pathlib.Path(found[-1])

    if real_source is not None:
        if not cache_marker.is_file() or real_source.stat().st_mtime > cache_marker.stat().st_mtime:
            import extract_apps_catalog
            extract_apps_catalog.extract(real_source, catalog_dir)
        return

    if not cache_marker.is_file():
        raise FileNotFoundError(
            "no Suunto Apps catalog found. Copy suunto-apps/index.json from your own "
            f"SuuntoLink installation to {source_path} (see "
            "data/suunto_apps_source/README.md for exactly where to find it), or run "
            "SuuntoLink at least once if it's already installed on this machine.")


def load_distributable_catalog(catalog_dir=DEFAULT_CATALOG_DIR):
    catalog_dir = pathlib.Path(catalog_dir)
    ensure_catalog_built(catalog_dir)
    with open(catalog_dir / "catalog.json") as f:
        entries = json.load(f)["entries"]
    with open(catalog_dir / "catalog.bin", "rb") as f:
        blob = f.read()
    return entries, blob


def catalog_entry_binary(entry, blob):
    return blob[entry["binaryOffset"]:entry["binaryOffset"] + entry["binaryLength"]]


def match_distributable_catalog(binary, entries, blob):
    for e in entries:
        if catalog_entry_binary(e, blob) == binary:
            return e
    return None


def to_json(entries, catalog_entries=None, catalog_blob=None):
    """JSON-friendly view for backend/server.py - real, 2026-08-09, alongside the App
    Slot picker work. `ruleIdx` is the entry's own 0-based position in this list -
    confirmed (workout_install.py's own module docstring, Finding via real
    CustomModes-cross-reference) to be exactly the RuleIdx a display field's own RULE
    record points at, i.e. entries[N] is "Suunto App Slot" wherever RuleIdx=N is wired.
    Deliberately excludes the raw binary (only its length) - this is for listing/
    labeling, not for re-installing an app elsewhere."""
    out = []
    for i, e in enumerate(entries):
        row = {
            "ruleIdx": i, "name": e.get("name"), "activityId": e.get("activityId"),
            "binaryLength": len(e["binary"]) if "binary" in e else None,
        }
        if "_warning" in e:
            row["warning"] = e["_warning"]
        if catalog_entries is not None and catalog_blob is not None and "binary" in e:
            match = match_distributable_catalog(e["binary"], catalog_entries, catalog_blob)
            if match:
                row["catalogMatch"] = {
                    "ruleId": match["ruleId"], "name": match["name"],
                    "categoryId": match["categoryId"], "description": match["description"],
                }
        out.append(row)
    return out


def show(entries, catalog=None):
    if not entries:
        print("no app entries found - Apps region is empty, or doesn't look like a real"
              " directory (see apps.py's module docstring)")
        return
    print(f"{len(entries)} app entry(ies) found:")
    for e in entries:
        marker = f"0x{e['marker']:02x}" if "marker" in e else "?"
        binary_length = len(e["binary"]) if "binary" in e else "?"
        # byte0 of the entry header is the guidance flag (Finding 39/60, confirmed against a
        # genuine Movescount workout on the Ambit3 Sport): 1 = native WORKOUT-menu guided
        # workout, 0 = display app.
        kind = {0: "app", 1: "workout"}.get(e.get("reserved"), f"type{e.get('reserved')}")
        print(f"  offset 0x{e['entry_offset']:x}: [{kind}] name={e.get('name', '?')!r}"
              f"  activityId={e.get('activityId', '?')}"
              f"  marker={marker}"
              f"  binary_length={binary_length}"
              + (f"  ruleId={e['ruleId']}" if e.get("ruleId") else "")
              + (f"  outputFormatDivisor={e['outputFormatDivisor']}"
                 if "outputFormatDivisor" in e else ""))
        if "_warning" in e:
            print(f"    WARNING: {e['_warning']}")
        if catalog is not None and "binary" in e:
            match = match_catalog(e["binary"], catalog)
            if match:
                print(f"    catalog match: ruleId={match['ruleId']} name={match['name']!r}"
                      f" activityId={match['activityId']} category={match['categoryId']}")
            else:
                print("    no exact catalog match (private/custom app, or catalog snapshot"
                      " doesn't include it)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="from_file", metavar="FILE",
                     help="decode a raw Apps dump (200000 bytes) instead of the watch")
    ap.add_argument("--catalog", metavar="FILE",
                     help="suunto-apps/index.json from a SuuntoLink install, to identify"
                          " apps by exact binary match against the public catalog")
    ap.add_argument("--json", action="store_true",
                     help="print one JSON line instead of human-readable lines - for"
                          " backend/server.py, matches this app's real shipped catalog"
                          " (data/suunto_apps/), not --catalog above")
    args = ap.parse_args()

    if args.from_file:
        with open(args.from_file, "rb") as f:
            data = f.read()
    else:
        from write_nav import Link
        link = Link(dry_run=False, verbose=not args.json)
        if not args.json:
            print("read-only: 0x0b17 reads flash, nothing is written")
        link.open()
        data = read_apps_region(link)

    entries = decode(data)

    if args.json:
        catalog_entries = catalog_blob = None
        try:
            catalog_entries, catalog_blob = load_distributable_catalog()
        except OSError:
            pass  # catalog not extracted yet - still show entries, just without matches
        print(json.dumps({"ok": True, "entries": to_json(
            entries, catalog_entries=catalog_entries, catalog_blob=catalog_blob)}))
        return 0

    catalog = None
    if args.catalog:
        with open(args.catalog) as f:
            catalog = json.load(f)

    show(entries, catalog=catalog)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
