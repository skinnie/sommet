#!/usr/bin/env python3
"""Dumps the Suunto Kailash's `TrackLog` flash region and decodes it - a real, confirmed
20-byte fixed-stride GPS record format, found 2026-08-08 against real hardware after the
initial hypothesis (reusing Ambit3's own `ExerciseLog` PMEM20 format) was tried and cleanly
falsified (no PMEM magic, implausible master-index numbers - see git history/
`custom_modes_andre.md` for that attempt).

**Real format, confirmed against real hardware AND a real known location, corrected once
already - worth reading, not just trusting the numbers below:** the first pass at this had
every field right except the record boundary, off by exactly one byte. That misalignment
still produced smoothly-varying, plausible-*looking* coordinates (a real, coherent-seeming
track through the Canadian Arctic) - "varies smoothly" alone was not proof of a correct
decode, and it took André pointing out the real location should be Lille, France for the
actual bug (a one-byte record-boundary shift) to surface. Corrected by searching the raw bytes
directly for Lille's own real coordinates (~50.63N, ~3.06E) rather than trusting the earlier
alignment, which is what pinned the true record start.

    record stride: 20 bytes, starting at region offset 1 - the region's own byte 0 is a real,
    currently-unexplained leading byte, not part of any record

    offset  size  field
    0       4     lat, int32 LE, degrees * 1e7 (same convention as this project's own
                   WaypointDescriptor/exercise_log.py samples)
    4       4     lon, int32 LE, degrees * 1e7
    8       4     third field - real values cluster in the low thousands (roughly
                   2,000-9,000 across real records) - unit/meaning not confirmed
                   (accuracy/HDOP is plausible; not cross-checked against another source)
    12      2     year, u16 LE (confirmed: 0x07ea = 2026, matching the real capture date)
    14      1     month
    15      1     day
    16      1     hour
    17      1     minute
    18      2     two more bytes, real values, no clear pattern found yet - reported raw,
                   not asserted (a sub-second/sequence/checksum field is plausible, unconfirmed)

Confirmed against real hardware, 2026-08-08: 56 consecutive real-looking records (index 1-56;
index 0 has a completely different byte shape, unrelated third-field magnitude and an
implausible lat - almost certainly a header/init record, not a GPS fix, and is skipped)
spanning 2026-08-02 through 2026-08-07, coordinates matching André's own real, independently
known location (Lille, France) closely and consistently across every real record - the actual
confirmation, not just "looks smooth." Past record 56 the same fields degrade into implausible
values - the same "real data, then unused flash" pattern already established for Ambit3's own
ExerciseLog, not a bug.

    ./tools/kailash_tracklog.py --gpx-out /tmp/kailash_track.gpx
    ./tools/kailash_tracklog.py --from /tmp/tracklog_dump.bin --gpx-out /tmp/kailash_track.gpx
"""

import argparse
import json
import math
import re
import struct
import sys
from datetime import datetime, timedelta

RECORD_START = 1
RECORD_SIZE = 20

# From the real kailash capture's own 0x0b21 memory-map reply, parsed directly (see
# custom_modes_andre.md's "Kailash" section for the full region table) - not guessed.
TRACKLOG_BASE = 0x48A1C0
TRACKLOG_SIZE = 1310713


def _plausible_record(rec):
    """The same real plausibility check walk_records() uses on one already-sliced 20-byte
    record - factored out so _real_data_end() below can reuse the exact same bounds without
    them drifting apart if either is ever tuned."""
    lat, lon, third = struct.unpack_from("<iii", rec, 0)
    year = struct.unpack_from("<H", rec, 12)[0]
    month, day, hour, minute = rec[14], rec[15], rec[16], rec[17]
    return (
        2015 <= year <= 2035 and 1 <= month <= 12 and 1 <= day <= 31
        and hour <= 23 and minute <= 59
        and -900000000 <= lat <= 900000000 and -1800000000 <= lon <= 1800000000
        # Real bound, not guessed: every confirmed record's own "third" field clusters
        # tightly (roughly 2,000-9,000) - wide enough for real variation, still narrow
        # enough to reject record 0's own 3,735,608 (a different-shaped header/init record
        # whose date fields alone happen to look valid too, so this field is what actually
        # tells the two apart).
        and 500 <= third <= 50000
    )


# Real, 2026-08-09 ("why on the ambit the activities is faster? ... check the
# documentation") - exercise_log.py's own real fix for the exact same class of problem
# (probe a small prefix, read a real watch-reported "next_free_address" boundary, then read
# only that much instead of the full declared region - a real 119x speedup there). TrackLog
# has no equivalent master-header/next-free-address field to read directly (it's a plain
# flash region, not a PMEM20 log with its own index), but the same *principle* applies: real
# records only ever occupy a small, contiguous run from the start of the region (every real
# capture so far has been under 100 records, ~2KB, out of a 1.3MB region - see module
# docstring), so probing a generous prefix and only reading the rest when that prefix isn't
# enough gets the same real win without needing a watch-reported boundary at all.
TRACKLOG_PROBE_SIZE = 65536  # ~32x more than every real capture has needed so far


# A real record run is only judged "ended" when this many confirmed-implausible (padding)
# records follow the last real one. Much larger than any real mid-track gap (a live Kailash
# loop had a 6-record gap mid-track), so a gap never ends the scan, but far smaller than the
# region's true unused tail (thousands of records), so the probe optimization still fires.
PADDING_MARGIN_RECORDS = 64


def _real_data_end(data):
    """Byte offset just past the LAST real-looking record in `data`, or None if the probe
    isn't conclusive - the last real record sits within PADDING_MARGIN_RECORDS of the probe's
    end, so real data may continue past it and the caller must read the full region instead.

    Unlike a first-gap cutoff, a short run of implausible records mid-track does NOT end the
    scan: a real Kailash loop had a 6-record gap partway through, and stopping there truncated
    the track (read only the first 15 min of a 65-min walk). Scanning to the last real record
    and requiring a clear padding margin after it distinguishes a mid-track gap from the
    region's genuine unused tail."""
    n = (len(data) - RECORD_START) // RECORD_SIZE
    last_end = None
    for i in range(n):
        off = RECORD_START + i * RECORD_SIZE
        if _plausible_record(data[off:off + RECORD_SIZE]):
            last_end = off + RECORD_SIZE
    if last_end is None:
        return None
    if (len(data) - last_end) // RECORD_SIZE >= PADDING_MARGIN_RECORDS:
        return last_end
    return None


def walk_records(data):
    """Every 20-byte slot (starting at RECORD_START, not 0) that looks like a real GPS fix,
    in order. Does NOT stop at the first run of implausible records: a real mid-track gap
    (a live Kailash loop had a 6-record gap at slot 36, resuming afterwards) would otherwise
    truncate the track to its first 15 minutes. The plausibility filter rejects the region's
    padding tail record-by-record, and split_into_activities() windows the survivors to each
    session, so scanning the whole buffer is safe and keeps the full track."""
    n = (len(data) - RECORD_START) // RECORD_SIZE
    for i in range(n):
        off = RECORD_START + i * RECORD_SIZE
        rec = data[off:off + RECORD_SIZE]
        if _plausible_record(rec):
            lat, lon, third = struct.unpack_from("<iii", rec, 0)
            year = struct.unpack_from("<H", rec, 12)[0]
            month, day, hour, minute = rec[14], rec[15], rec[16], rec[17]
            yield {
                "index": i, "lat": lat / 1e7, "lon": lon / 1e7, "third_raw": third,
                "year": year, "month": month, "day": day, "hour": hour, "minute": minute,
                "trailer": rec[18:20],
            }


def haversine_meters(lat1, lon1, lat2, lon2):
    """Great-circle distance - the same formula this project already uses elsewhere
    (desktop/src/services/garminservice.cpp's own haversineMeters()), not re-derived."""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def to_activity(points):
    """One activity-shaped dict, the same field names desktop/src/services/garminservice.cpp
    (ActivityService.activities) already uses, so the existing ActivityCard/MapView QML
    components can show this with no new UI code: name/startTime/distanceMeters/
    durationSeconds/track/gpxText. No ascent/FIT here - TrackLog carries no altitude field
    confirmed yet (see custom_modes_andre.md), and there's no FIT writer for this format."""
    if not points:
        return None
    first, last = points[0], points[-1]
    start = f"{first['year']:04d}-{first['month']:02d}-{first['day']:02d}T" \
            f"{first['hour']:02d}:{first['minute']:02d}:00Z"
    end_minutes = (last["day"] - first["day"]) * 1440 + \
        (last["hour"] - first["hour"]) * 60 + (last["minute"] - first["minute"])
    distance = sum(haversine_meters(a["lat"], a["lon"], b["lat"], b["lon"])
                   for a, b in zip(points, points[1:]))
    return {
        # "Walk" - the same name a real Ambit3 default sport mode uses (SportModesPage.qml's
        # own confirmed SuuntoLink defaults list). Kailash's DeviceHistory sessions carry no
        # decoded sport-mode field of their own (kailash_history.py) to name this from, and
        # every real capture of this watch so far is a walk - naming it exactly what Ambit
        # would is what lets ActivityTypes.forName() (desktop/qml/ActivityTypes.qml) resolve
        # both to the same "Walking" id, so TotalsPage.qml groups them into one bucket
        # instead of splitting Kailash's walks into their own device-specific pile.
        "name": "Walking",
        "startTime": start,
        "distanceMeters": round(distance, 1),
        "durationSeconds": max(0, end_minutes * 60),
        "track": [{"lat": p["lat"], "lon": p["lon"]} for p in points],
        "gpxText": to_gpx(points),
    }


def _parse_when(when_str):
    """Pulls (year, month, day, hour, minute) out of a real DeviceHistory
    Header.DateTime string (kailash_history.py's own `sessions[].when` - a real utf8
    field, confirmed via sbem_schema.load(KAILASH_DESCRIPTOR).fields, but this project has
    never captured a live sample of its exact separator formatting). Only the digit run
    matters here - tolerant of "-"/":"/"T"/" " or no separator at all - since TrackLog's own
    points are only ever compared at minute resolution anyway."""
    m = re.match(r"(\d{4})\D?(\d{2})\D?(\d{2})\D?(\d{2})\D?(\d{2})", when_str or "")
    if not m:
        return None
    y, mo, d, h, mi = (int(x) for x in m.groups())
    try:
        return datetime(y, mo, d, h, mi)
    except ValueError:
        return None



# Real, found live 2026-08-09 debugging "activities still don't show gps track" after the
# very first version of this correlation matched nothing at all: DeviceHistory's
# LogHeaders.Header.DateTime (kailash_history.py's own `sessions[].when`) is the watch's
# LOCAL time, while TrackLog's own embedded year/month/day/hour/minute fields are UTC - two
# independently-decoded clocks with a real, non-obvious offset between them (no explicit
# timezone marker on either field in the descriptor - see sbem_schema field 74/98 "DateTime"
# vs field 81 "Sample.UTC" on a *different* object, the only explicit UTC-labelled sibling
# field this project has found). Confirmed against three independent real events the same
# day, not a guess: the 2026-08-03T08:25:27 session lines up exactly with TrackLog's
# 06:26-06:53 UTC points, and 2026-08-08T19:32:56 (duration 1117s, ending ~19:51 local) lines
# up exactly with TrackLog's own 17:33-17:51 UTC walk - both a clean 2-hour local-ahead-of-UTC
# offset, i.e. CEST (France, August). NOT necessarily correct outside daylight saving time or
# for a watch configured to a different timezone - there is no confirmed dynamic offset field
# to read this from instead, so this is recorded as real-but-seasonal, worth revisiting the
# next time this watch is read outside CEST.
SESSION_LOCAL_UTC_OFFSET_HOURS = 2


def split_into_activities(points, sessions):
    """One activity per real DeviceHistory session (kailash_history.py's own `sessions` -
    the watch's "activity mode" logbook), correlating each session's own [when, when +
    duration] window against TrackLog's own per-point timestamps. Real request 2026-08-09
    ("Something is bizarre on the activities, they say no gps, but they have gps") -
    DeviceHistory sessions carry summary stats only (no GPS of their own, see
    kailash_history.py's own docstring), and until now ActivitiesPage.qml filled every
    Kailash "Walk" card's track with an empty placeholder. This is what actually supplies it.

    `s["when"]` is converted from local time to UTC (SESSION_LOCAL_UTC_OFFSET_HOURS, see its
    own comment - a real fix, not the original version of this function, which compared local
    session times directly against UTC TrackLog points and silently matched nothing) before
    comparing against TrackLog's own points.

    A +/-2 minute tolerance is then applied on both ends: TrackLog points are minute-
    resolution and Header.Duration is tenths-of-a-second, two independently-derived clocks
    off the same watch, so exact-window matching would still drop real points to ordinary
    clock/rounding drift between the two sources.

    Always returns exactly one entry per session, in the same order `sessions` came in -
    ActivitiesPage.qml zips this 1:1 against KailashService.sessions by index (both ultimately
    decode the same real wire data in the same order, from two separate backend calls). A
    session with no correlated points gets a real, honest empty track rather than being
    dropped, matching this project's original per-session behaviour for the case where TrackLog
    genuinely doesn't cover it (e.g. a session predating this watch's TrackLog capture start).
    distanceMeters/durationSeconds/startTime always come from the session's own real watch-
    reported stats, not the GPS-derived approximation - only `track`/`gpxText` come from
    TrackLog, since a haversine sum over minute-resolution points is strictly less accurate
    than the watch's own reported values.

    Falls back to one bundled activity covering every point (this project's original,
    pre-2026-08-09 behaviour) only when there are no sessions to correlate against at all
    (e.g. the DeviceHistory query itself failed) - still shows something rather than nothing."""
    if not sessions:
        activity = to_activity(points)
        return [activity] if activity else []

    point_dts = []
    for p in points:
        try:
            point_dts.append((datetime(p["year"], p["month"], p["day"], p["hour"], p["minute"]), p))
        except ValueError:
            continue

    activities = []
    for s in sessions:
        matched = []
        start = _parse_when(s.get("when"))
        if start is not None:
            start -= timedelta(hours=SESSION_LOCAL_UTC_OFFSET_HOURS)
            end = start + timedelta(seconds=s.get("duration_s") or 0)
            lo, hi = start - timedelta(minutes=2), end + timedelta(minutes=2)
            matched = [p for dt, p in point_dts if lo <= dt <= hi]

        activity = to_activity(matched) or {
            "name": "Walking", "startTime": None, "distanceMeters": 0,
            "durationSeconds": 0, "track": [], "gpxText": "",
        }
        # Same "Walk" as to_activity()'s own comment - matches Ambit's real default sport
        # mode name so ActivityTypes.forName() classifies this the same way across devices.
        activity["name"] = "Walking"
        # The session's own real watch-reported stats win over the GPS-derived ones.
        if s.get("when") is not None:
            activity["startTime"] = s["when"]
        if s.get("distance_m") is not None:
            activity["distanceMeters"] = s["distance_m"]
        if s.get("duration_s") is not None:
            activity["durationSeconds"] = s["duration_s"]
        activities.append(activity)
    return activities


def to_gpx(points):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="Sommet kailash_tracklog.py">',
             "  <trk><name>Kailash TrackLog</name><trkseg>"]
    for p in points:
        t = f"{p['year']:04d}-{p['month']:02d}-{p['day']:02d}T{p['hour']:02d}:{p['minute']:02d}:00Z"
        lines.append(f'    <trkpt lat="{p["lat"]:.7f}" lon="{p["lon"]:.7f}">'
                     f'<time>{t}</time></trkpt>')
    lines.append("  </trkseg></trk>")
    lines.append("</gpx>")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="from_file", metavar="FILE",
                     help="decode a raw TrackLog dump instead of the watch")
    ap.add_argument("--save", metavar="FILE",
                     help="also save the raw region bytes here (live read only)")
    ap.add_argument("--gpx-out", metavar="FILE",
                     help="write every real-looking point as one GPX track file")
    ap.add_argument("--json", action="store_true",
                     help="print one JSON activity object instead of human-readable lines - "
                          "for desktop/backend/server.py, not meant for a person to read")
    args = ap.parse_args()

    sessions = None
    if args.from_file:
        with open(args.from_file, "rb") as f:
            data = f.read()
    else:
        from write_nav import Link, read_flash
        link = Link(dry_run=False, verbose=not args.json)
        if not args.json:
            print("read-only: 0x0b17 reads flash, nothing is written")
        link.open()
        # Real, 2026-08-09 ("why on the ambit the activities is faster?... check the
        # documentation") - see TRACKLOG_PROBE_SIZE's own comment: probes a generous prefix
        # first and checks whether the real bad_streak cutoff already falls inside it (every
        # real capture so far has). If so, that's genuinely all of the real data - no reason
        # to keep reading another 1.2+MB of confirmed-unused flash. Falls back to the full,
        # always-correct read if the probe isn't conclusive, the same safety net
        # exercise_log.py's own version of this fix uses for ExerciseLog.
        probe = read_flash(link, TRACKLOG_BASE, TRACKLOG_PROBE_SIZE, label="TrackLog (probe)")
        real_end = _real_data_end(probe)
        data = probe[:real_end] if real_end is not None \
            else read_flash(link, TRACKLOG_BASE, TRACKLOG_SIZE, label="TrackLog")
        if args.save:
            with open(args.save, "wb") as f:
                f.write(data)
            if not args.json:
                print(f"\nsaved raw dump to {args.save}")
        # Real, 2026-08-09: reuses the same already-open `link` for the DeviceHistory query
        # (0x1200, see kailash_history.py) needed to correlate real sessions against these
        # TrackLog points - no second USB open/close round trip. Not fatal if it fails (a
        # decode hiccup here shouldn't take down TrackLog reading, which is the whole point
        # of this tool) - falls back to the bundled-everything activity in that case.
        import kailash_history
        try:
            history = kailash_history.read_history(link, warn=lambda *_a, **_k: None)
            if history.get("ok"):
                sessions = history.get("sessions")
        except Exception:
            sessions = None

    points = list(walk_records(data))

    if args.json:
        activities = split_into_activities(points, sessions)
        # An empty TrackLog is a real, valid state - a freshly reset watch, one whose 7R app
        # already drained its log, or (real, 2026-08-15) one just re-flashed: after André
        # flashed this Kailash to fw 2.0.5 its TrackLog read back empty and the Home "Travel
        # History" card showed a scary "server replied: Bad Gateway" because empty was reported
        # as ok:false -> 502. Empty is not an error: report ok with an empty list so the card
        # shows a plain "no travel history yet" instead. A genuine read failure still surfaces
        # as an exception (non-zero exit + stderr), which the caller renders differently.
        print(json.dumps({"ok": True, "activities": activities}))
        return 0

    print(f"{len(points)} real-looking GPS record(s) found")
    for p in points[:20]:
        print(f"  [{p['index']:5}] {p['year']:04d}-{p['month']:02d}-{p['day']:02d} "
              f"{p['hour']:02d}:{p['minute']:02d}  lat={p['lat']:.5f} lon={p['lon']:.5f}  "
              f"third_raw={p['third_raw']}  trailer={p['trailer'].hex()}")
    if len(points) > 20:
        print(f"  ... and {len(points) - 20} more")

    if args.gpx_out:
        if points:
            with open(args.gpx_out, "w") as f:
                f.write(to_gpx(points))
            print(f"\nwrote {args.gpx_out} ({len(points)} track point(s))")
        else:
            print("\nno real-looking points - not writing an empty GPX file")

    return 0


if __name__ == "__main__":
    sys.exit(main())
