#!/usr/bin/env python3
"""Reads the Suunto Kailash's real on-watch `sml.DeviceHistory` object live - the same data
shown on the watch's own "7R" button screen (visited cities/countries, travel stats) plus, as
a real bonus found in the same reply, the "activity mode" logbook (`LogHeaders`) this project
had separately been unable to locate in flash.

**Real mechanism, confirmed working end to end against real hardware, 2026-08-08**: the
existing `0x1200` object-by-identifier query (`write_nav.py`'s own `CMD_LOG_HEADERS`, already
used there for `sml.DeviceLogBook`, entry `0x8d`) also answers for `sml.DeviceHistory`, entry
`0x67` - found via this exact watch's own real SBEM schema descriptor (`assets/APK/kailash/
Suunto 7R/Container/Documents/descr+79DC39510E000100+2.0.5`, `tools/sbem_schema.py --group
0x67` lists every field). Cross-checked field by field against what the watch's own screen and
the `7R` iOS app's local database (`7r-trackLog.db`, `device_history` table) show for the same
real watch: `NumberOfVisitedPlaces`/`NumberOfVisitedCountries`=1/1 (Lille, France),
`TravellingDays`/`TravelledDistance`/`FurthestFromHome`=0/0/0 - exact match.

Two unit conversions applied here that `sbem_schema.py`'s own `decode_entry()` deliberately
does *not* apply (it only unpacks the raw stored type - the `<MOD>` conversion is documented
in the schema text but is a free-form formula, not something the generic decoder evaluates):
`LogHeaders.Header.Duration` is `raw/10` seconds (confirmed against the schema's own
`<MOD>x/10,y*10`), `Speed.Max` is `raw/360` (`<MOD>x/360,y*360`, unit unconfirmed - m/s is
plausible but not independently checked). `VisitedCities...Location.Longitude/Latitude` are
a real, separate case: plain `float32`, genuinely different from every *other* lat/lon field
in this same schema (all `int32`, degrees*1e7) - decoding them as degrees directly gives
implausible values (~0.05, ~0.88); as **radians** (`value * 180 / pi`), they land exactly on
Lille's real coordinates, confirming the unit even though the descriptor's own `<MOD>` tag for
these two specific fields is empty.

    ./tools/kailash_history.py
    ./tools/kailash_history.py --json   # for desktop/backend/server.py, not for a person
    ./tools/kailash_history.py --gps    # also reads sml.DeviceLog - see its own docstring below

**`--gps`, added 2026-08-08, still needs a real cable test**: `sml.DeviceHistory` (this file's
original scope) is one of seven real queryable top-level objects in this watch's own
descriptor (`sbem_schema.py`'s `Schema.queries`, `<QRY>`-tagged entries) - `sml.DeviceLog`,
entry `0x53`, is a sibling, not touched by anything in this project until now. It's exactly
what a real BLE capture of a watch-only "GPS Power Use" recording (7R-button long-press, no
phone) showed being pushed over `0x1200` to the paired phone - see `KAILASH-BLE-FINDINGS.md`
Findings 5-6: a `Header` (Duration/DateTime/Device name+serial) plus a `Samples.Sample` array
(`Latitude`/`Longitude`/`GPSAltitude`/`Time`/`UTC` per point). Real, open question those
findings raise that this flag exists to answer: the BLE-synced sample list ran at a real,
measured ~31-second interval, not the ~1 Hz a decade-old review of this watch's firmware
claims for the recording itself - reading `sml.DeviceLog` **directly off the watch over the
cable**, bypassing the 7R app and its BLE sync entirely, is the way to tell "firmware really
changed" from "the app downsamples before pushing over BLE" apart: if a cable read of the same
just-recorded activity comes back denser than what BLE showed, the app is the one thinning it
out; if it's the same ~31 s spacing, that's what the watch itself is actually recording now.
Also note, matching `write_nav.py`'s own documented caveat for the sibling `logbook` query:
**multi-page replies are not handled here** - a single `0x1200` reply is returned as-is, no
continuation-cursor paging attempted (the real BLE captures needed two separate `0x1200`
messages for a mere 36 samples, so a long recording may come back truncated over cable too;
not yet known whether the cable path's own reply-reassembly in `Link._read_reply()`, which
already spans multiple 64-byte HID reports per command, makes this a non-issue in practice or
not - that's part of what the first real run of this flag will show).
"""

import argparse
import json
import math
import struct
import sys

from write_nav import CMD_LOG_HEADERS, Link
import sbem_schema

DEVICE_HISTORY_ENTRY = 0x67
HISTORY_REQUEST = (bytes.fromhex("00000000") + (1).to_bytes(2, "little")
                   + (10).to_bytes(2, "little") + b"SBEM0102"
                   + bytes([DEVICE_HISTORY_ENTRY, 0x00]))

# sml.DeviceLog - see this file's own docstring, "--gps" section, for why this exists and
# what it's meant to settle. Same request shape as HISTORY_REQUEST, different queryable
# object id (0x53, confirmed real via sbem_schema.load(KAILASH_DESCRIPTOR).queries - not
# guessed).
DEVICE_LOG_ENTRY = 0x53
DEVICE_LOG_REQUEST = (bytes.fromhex("00000000") + (1).to_bytes(2, "little")
                      + (10).to_bytes(2, "little") + b"SBEM0102"
                      + bytes([DEVICE_LOG_ENTRY, 0x00]))

# Real bug, found live 2026-08-08: sbem_schema.default_descriptor() globs for
# `descr+*+{REFERENCE_FW}` where REFERENCE_FW is hardcoded to the *Ambit3's* own reference
# firmware ("2.4.17") - silently wrong for Kailash. It doesn't fail cleanly either: if any
# real Ambit3 descriptor happens to exist in assets/, it loads that one instead and applies
# *its* field-ID meanings to Kailash's actual reply bytes, corrupting exactly the fields whose
# IDs mean something different-sized in the two schemas (0x55/0x5e here) while leaving others
# looking fine by coincidence - real, reproducible, silent corruption, not a crash. Kailash
# needs its own real descriptor explicitly, not the generic Ambit3-reference lookup.
KAILASH_DESCRIPTOR_NAME = "descr+79DC39510E000100+2.0.5"


def _find_kailash_descriptor():
    """Locate this Kailash's real SBEM schema descriptor (descr+SERIAL+FW) wherever it lives.

    It originally came from a real 7R app-container extraction, so that path is preferred, but
    the identical file also ships in other real extractions in assets/ (SuuntoLink keeps its
    own copy under `WIndows apps/Suuntolink/`). Real bug, 2026-08-15: hardcoding only the 7R
    container path meant a checkout that had the descriptor under a *different* assets folder
    reported it "missing" and every Kailash history/tracklog read 502'd, even though a valid
    same-serial/same-fw descriptor was right there. Glob for it instead: exact serial+fw
    anywhere under assets/ first, then any descriptor for this serial, else fall back to the
    canonical 7R path so the "missing" error still names where it's meant to come from."""
    primary = (sbem_schema.ASSETS / "APK" / "kailash" / "Suunto 7R" / "Container"
               / "Documents" / KAILASH_DESCRIPTOR_NAME)
    if primary.exists():
        return primary
    for pattern in (KAILASH_DESCRIPTOR_NAME, "descr+79DC39510E000100+*"):
        matches = sorted(sbem_schema.ASSETS.rglob(pattern))
        if matches:
            return matches[0]
    return primary


KAILASH_DESCRIPTOR = _find_kailash_descriptor()


def _descriptor_for(link):
    """The connected Kailash's own schema, read from the watch when no stored copy matches
    (sbem_schema.for_link) - KAILASH_DESCRIPTOR above is one specific watch's file from a
    private assets/ folder, so on anyone else's machine it was simply missing. Cable only: a
    Bluetooth link keeps the stored file, the watch drives that session and the schema read
    has not been tried over it."""
    from write_nav import Link
    if type(link) is Link and not link.dry_run:
        try:
            return sbem_schema.for_link(link)
        except Exception:  # hid backends and short replies each raise their own type
            pass
    return KAILASH_DESCRIPTOR


def read_history(link, warn=print):
    """Queries and decodes the real sml.DeviceHistory object. Returns a plain dict (JSON-
    serializable as-is) with `ok`, and on success `name`/`serial`/`cities_visited`/
    `countries_visited`/`last_known_location`/`last_known_country`/`last_known_time`/
    `travelling_days`/`travelled_distance_m`/`cumulated_distance_m`/`furthest_from_home_m`/
    `sessions` (the activity-mode logbook, see module docstring). `warn` receives non-fatal
    per-entry decode problems - defaults to `print`, pass a no-op to silence them (e.g. from
    a JSON caller that shouldn't mix prose into stdout)."""
    payload = link.command(CMD_LOG_HEADERS, HISTORY_REQUEST)

    head = payload.find(sbem_schema.MAGIC)
    if head < 0:
        return {"ok": False, "error": "no SBEM0102 payload in the reply"}

    descriptor = _descriptor_for(link)
    if not descriptor.exists():
        return {"ok": False, "error": f"the real Kailash descriptor is missing - expected it "
                f"at {descriptor} (a real file from a real 7R app container extraction; see "
                f"custom_modes_andre.md's Kailash section for where it came from)"}
    schema = sbem_schema.load(descriptor)
    entries = list(sbem_schema.entries(payload[head:]))

    summary = {}
    location = None
    country = None
    sessions = []
    # Real, 2026-08-09 ("Add a world map with the points were kailash has been") - every
    # real VisitedCities.Location/VisitedCountries.CountryCode entry encountered, not just
    # the last one. Before this, `location`/`country` above (kept for backward
    # compatibility - `last_known_location` still means the same thing) only ever held the
    # final value seen, silently discarding every earlier visited place on a watch with real
    # multi-place travel history. This reference watch has only ever had exactly one real
    # visited place (Lille) the whole time this project has tested against it, so the
    # "overwrite" bug never had a visible symptom to catch it by - genuinely unconfirmed
    # whether a second real place preserves the same paired order assumed here (Location
    # then CountryCode, appearing in real visit order) until a watch with real multi-place
    # history is available to check against.
    visited_places = []
    for entry_id, data in entries:
        try:
            records = schema.decode_entry(entry_id, data) or []
        except (ValueError, IndexError, UnicodeDecodeError, struct.error) as exc:
            warn(f"  (couldn't decode entry 0x{entry_id:02x}, {len(data)} bytes: {exc} - "
                 f"skipped, not fatal)")
            continue
        for record in records:
            fields = {schema.field_name(entry_id, f.fid): v for f, v in record}
            if entry_id == 0x5B:  # VisitedCities...Location - float32 radians, see docstring
                lon_rad, lat_rad = fields.get("Longitude"), fields.get("Latitude")
                if lon_rad is not None and lat_rad is not None:
                    location = (lat_rad * 180 / math.pi, lon_rad * 180 / math.pi)
                    visited_places.append({"lat": location[0], "lon": location[1], "country": None})
            elif entry_id == 0x5C:
                country = fields.get("CountryCode")
                if visited_places:
                    visited_places[-1]["country"] = country
            elif entry_id == 0x66:  # LogHeaders.Header - real "activity mode" logbook entry
                sessions.append({
                    "when": fields.get("DateTime"),
                    "duration_s": (fields.get("Duration") or 0) / 10,
                    "distance_m": fields.get("Distance"),
                    "max_speed": (fields.get("Speed.Max") or 0) / 360,
                })
            else:
                summary.update(fields)

    p = "sml.DeviceHistory.Histories.History."
    return {
        "ok": True,
        "name": summary.get("sml.DeviceHistory.Device.Name"),
        "serial": summary.get("sml.DeviceHistory.Device.SerialNumber"),
        "cities_visited": summary.get(p + "VisitedCities.NumberOfVisitedPlaces"),
        "countries_visited": summary.get(p + "VisitedCountries.NumberOfVisitedCountries"),
        "last_known_location": location,  # [lat, lon] degrees, or None
        "last_known_country": country,
        "last_known_time": summary.get(p + "LastKnownTime"),
        "travelling_days": summary.get(p + "TravellingDays"),
        "travelled_distance_m": summary.get(p + "TravelledDistance"),
        "cumulated_distance_m": summary.get(p + "CumulatedDistance"),
        "furthest_from_home_m": summary.get(p + "FurthestFromHome"),
        "sessions": sessions,
        "visited_places": visited_places,  # [{lat, lon, country}, ...] - see comment above
    }


def show_history(h):
    if not h["ok"]:
        print(h["error"])
        return
    print(f"\n{h['name']} ({h['serial']})")
    loc = h["last_known_location"]
    print(f"  Cities visited:     {h['cities_visited']}"
          + (f"  (last known: {loc[0]:.4f}, {loc[1]:.4f})" if loc else ""))
    print(f"  Countries visited:  {h['countries_visited']}"
          + (f"  ({h['last_known_country']})" if h["last_known_country"] else ""))
    print(f"  Last known time:    {h['last_known_time']}")
    print(f"  Travelling days:    {h['travelling_days']}")
    print(f"  Travelled distance: {h['travelled_distance_m']} m")
    print(f"  Cumulated distance: {h['cumulated_distance_m']} m")
    print(f"  Furthest from home: {h['furthest_from_home_m']} m")

    sessions = h["sessions"]
    if sessions:
        print(f"\n{len(sessions)} activity-mode logbook entry(ies) "
              "(a real, separate system from the passive TrackLog - see "
              "custom_modes_andre.md's Kailash section):")
        for s in sessions:
            print(f"  {s['when']}  duration={s['duration_s']:.1f}s  "
                  f"distance={s['distance_m']}m  max_speed={s['max_speed']:.2f} (raw/360 unit)")


def read_device_log(link, warn=print):
    """Queries and decodes the real sml.DeviceLog object - see this file's own docstring,
    "--gps" section. Returns a plain dict: `ok`, and on success `duration_s`/`when` (the
    object's own Header) plus `samples`, a list of `{"utc", "lat", "lon", "alt_cm"}` in
    capture order. `lat`/`lon` already converted to real degrees (raw int32 / 1e7, the same
    convention as every other lat/lon field in this schema - see read_history()'s own
    docstring for the one real exception, which does not apply to this object).

    Real, hardware-observed shape from the BLE captures this mirrors (KAILASH-BLE-FINDINGS.md
    Findings 5-6): the first entry in `Samples.Sample` is consistently a stale, carried-over
    "last known point" from a previous sync, not part of the object's own new recording -
    included here as `samples[0]`, not filtered out, since which entry is "stale" is only
    knowable by comparing against a previous read, not from this reply alone."""
    payload = link.command(CMD_LOG_HEADERS, DEVICE_LOG_REQUEST)

    head = payload.find(sbem_schema.MAGIC)
    if head < 0:
        return {"ok": False, "error": "no SBEM0102 payload in the reply"}

    descriptor = _descriptor_for(link)
    if not descriptor.exists():
        return {"ok": False, "error": f"the real Kailash descriptor is missing - expected it "
                f"at {descriptor}"}
    schema = sbem_schema.load(descriptor)
    entries = list(sbem_schema.entries(payload[head:]))

    duration_s, when, device_name, serial = None, None, None, None
    samples = []
    for entry_id, data in entries:
        try:
            records = schema.decode_entry(entry_id, data) or []
        except (ValueError, IndexError, UnicodeDecodeError, struct.error) as exc:
            warn(f"  (couldn't decode entry 0x{entry_id:02x}, {len(data)} bytes: {exc} - "
                 f"skipped, not fatal)")
            continue
        for record in records:
            fields = {schema.field_name(entry_id, f.fid): v for f, v in record}
            if entry_id == 0x49:
                duration_s = (fields.get("sml.DeviceLog.Header.Duration") or 0) / 10
            elif entry_id == 0x4A:
                when = fields.get("sml.DeviceLog.Header.DateTime")
            elif entry_id == 0x4B:
                device_name = fields.get("sml.DeviceLog.Device.Name")
            elif entry_id == 0x4C:
                serial = fields.get("sml.DeviceLog.Device.SerialNumber")
            elif entry_id == 0x52:  # Samples.Sample
                lat, lon = fields.get("Latitude"), fields.get("Longitude")
                samples.append({
                    "utc": fields.get("UTC"),
                    "lat": lat / 1e7 if lat is not None else None,
                    "lon": lon / 1e7 if lon is not None else None,
                    "alt_cm": fields.get("GPSAltitude"),
                })

    return {"ok": True, "when": when, "duration_s": duration_s, "device_name": device_name,
            "serial": serial, "samples": samples}


def show_device_log(d):
    if not d["ok"]:
        print(d["error"])
        return
    print(f"\nsml.DeviceLog ({d['device_name']}, {d['serial']}): "
          f"header duration={d['duration_s']}s @ {d['when']}")
    print(f"{len(d['samples'])} GPS sample(s):")
    for s in d["samples"]:
        print(f"  {s['utc']}  {s['lat']:.7f}, {s['lon']:.7f}  alt={s['alt_cm']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true",
                     help="print one JSON object instead of human-readable lines - for "
                          "desktop/backend/server.py, not meant for a person to read")
    ap.add_argument("--gps", action="store_true",
                     help="also query sml.DeviceLog (raw GPS samples of the most recent "
                          "'GPS Power Use' recording) - see this file's own docstring")
    args = ap.parse_args()

    link = Link(dry_run=False, verbose=not args.json)
    if not args.json:
        print("read-only: the 0x1200 sml.DeviceHistory query, nothing is written")
    link.open()
    h = read_history(link, warn=(lambda *a, **k: None) if args.json else print)

    d = None
    if args.gps:
        if not args.json:
            print("read-only: the 0x1200 sml.DeviceLog query, nothing is written")
        d = read_device_log(link, warn=(lambda *a, **k: None) if args.json else print)

    if args.json:
        out = dict(h)
        if d is not None:
            out["device_log"] = d  # new key, existing keys unchanged - safe for existing
                                    # desktop/backend/server.py consumers of this JSON
        print(json.dumps(out))
    else:
        show_history(h)
        if d is not None:
            show_device_log(d)
    return 0 if h["ok"] and (d is None or d["ok"]) else 1


if __name__ == "__main__":
    sys.exit(main())
