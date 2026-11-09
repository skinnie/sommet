#!/usr/bin/env python3
"""Shared exporters for the EXPERIMENTAL legacy-device tools (Suunto T6 family + GPS Track
Pod merge).

This is the one place the T6 wrapper (tools/suunto_t6.py) and the merge tool
(tools/legacy_merge.py) write their output, so GPX/FIT come out identical no matter which
tool produced them. There is deliberately no reverse-engineering here: the FIT writer is a
trimmed copy of tools/exercise_log.py's own `to_fit()` - itself a constant-for-constant port
of opensportsync's src/services/FitExport.ts, verified byte-exact against a `fitparse` read
(exercise_log.py's own header comment). The only additions over that proven code are (a) an
optional `heart_rate` record field, since the T6 is a heart-rate watch, and (b) making the
GPS position fields optional, since a T6 with no GPS Track Pod has heart rate + barometric
altitude but no latitude/longitude.

A "point" everywhere below is a plain dict:

    {"time": datetime (tz-aware UTC), "lat": float|None, "lon": float|None,
     "ele": float|None, "hr": int|None}

built by whichever tool is calling. Keeping the exporters dumb (they just serialise points)
is what lets the merge tool reuse them unchanged.

Built blind, like everything in this legacy-device corner: see tools/suunto_t6.py's header for
why "blind", and treat any output as unverified until someone with the real hardware confirms
it.
"""

from __future__ import annotations

import math
import struct
from datetime import datetime, timezone

# ─── GPX (with Garmin heart-rate extension) ───────────────────────────────────────────────

_GPX_NS = 'xmlns="http://www.topografix.com/GPX/1/1"'
_GPXTPX_NS = 'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"'


def _iso_utc(dt: datetime) -> str:
    """ISO-8601 with a trailing Z, same shape as exercise_log.py's `_iso_utc`."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def write_gpx(points: list[dict], out_path, *, name: str = "Suunto activity") -> int:
    """Write GPX 1.1. Points without a lat/lon are skipped for the track (a GPS-less T6-only
    session therefore produces an empty track - the merge tool is what supplies position).
    Heart rate, where present, is written as a Garmin TrackPointExtension so Strava / Garmin
    Connect / most tools pick it up. Returns bytes written."""
    fixed = [p for p in points if p.get("lat") is not None and p.get("lon") is not None]
    meta_time = _iso_utc(points[0]["time"]) if points else _iso_utc(datetime.now(timezone.utc))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<gpx version="1.1" creator="Sommet legacy_export.py" {_GPX_NS} {_GPXTPX_NS}>',
        f'  <metadata><time>{meta_time}</time></metadata>',
        f'  <trk><name>{_xml_escape(name)}</name><trkseg>',
    ]
    for p in fixed:
        ele = f'<ele>{p["ele"]:.1f}</ele>' if p.get("ele") is not None else ""
        if p.get("hr") is not None:
            ext = ('<extensions><gpxtpx:TrackPointExtension>'
                   f'<gpxtpx:hr>{int(p["hr"])}</gpxtpx:hr>'
                   '</gpxtpx:TrackPointExtension></extensions>')
        else:
            ext = ""
        lines.append(
            f'    <trkpt lat="{p["lat"]:.7f}" lon="{p["lon"]:.7f}">'
            f'{ele}<time>{_iso_utc(p["time"])}</time>{ext}</trkpt>')
    lines.append('  </trkseg></trk>')
    lines.append('</gpx>')
    blob = ("\n".join(lines) + "\n").encode("utf-8")
    with open(out_path, "wb") as fh:
        fh.write(blob)
    return len(blob)


# ─── FIT ──────────────────────────────────────────────────────────────────────────────────
# Trimmed from tools/exercise_log.py's to_fit(): same 14-byte header, same file_id/activity/
# session/lap framing, same Garmin CRC-16, same semicircle/altitude scaling. Differences,
# both additive so the file stays a valid superset of exercise_log's output:
#   * the record definition gains heart_rate (record field 3, u8) when any point has hr;
#   * position (fields 0/1) is emitted only when any point has a fix, so a T6-only file is
#     a legal FIT of timestamp+hr+altitude records with no bogus 0,0 coordinates.

GARMIN_EPOCH = 631065600  # 1989-12-31T00:00:00Z as a Unix timestamp

_CRC_TABLE = [
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
]


def _fit_crc(data) -> int:
    crc = 0
    for byte in data:
        tmp = _CRC_TABLE[crc & 0x0F]
        crc = ((crc >> 4) ^ tmp ^ _CRC_TABLE[byte & 0x0F]) & 0xFFFF
        tmp = _CRC_TABLE[crc & 0x0F]
        crc = ((crc >> 4) ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0x0F]) & 0xFFFF
    return crc & 0xFFFF


def _u8(b, v): b.append(v & 0xFF)
def _u16(b, v): b.extend(struct.pack("<H", v & 0xFFFF))
def _u32(b, v): b.extend(struct.pack("<I", v & 0xFFFFFFFF))
def _s32(b, v): b.extend(struct.pack("<i", v))


_E, _U8, _U16, _U32, _S32 = 0x00, 0x02, 0x84, 0x86, 0x85  # FIT base-type codes


def _write_def(b, local, global_num, fields):
    _u8(b, 0x40 | local)
    _u8(b, 0)            # reserved
    _u8(b, 0)            # architecture: little-endian
    _u16(b, global_num)
    _u8(b, len(fields))
    for num, size, base_type in fields:
        _u8(b, num); _u8(b, size); _u8(b, base_type)


_SPORT_CODES = {"run": 1, "cycle": 2, "hike": 17, "walk": 11, "generic": 0}


def _epoch(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def write_fit(points: list[dict], out_path, *, sport: str = "generic",
              name: str = "Suunto activity") -> int:
    """Write a FIT activity from `points`. Returns bytes written. Records carry heart rate
    when present; latitude/longitude only when a fix exists."""
    if not points:
        raise ValueError("no samples to write")
    has_pos = any(p.get("lat") is not None and p.get("lon") is not None for p in points)
    has_hr = any(p.get("hr") is not None for p in points)

    start_g = _epoch(points[0]["time"]) - GARMIN_EPOCH
    end_g = _epoch(points[-1]["time"]) - GARMIN_EPOCH
    duration_s = max(0, _epoch(points[-1]["time"]) - _epoch(points[0]["time"]))
    sport_code = _SPORT_CODES.get(sport.lower(), 0)

    data = bytearray()

    # file_id (local 0, global 0)
    _write_def(data, 0, 0, [(0, 1, _E), (1, 2, _U16), (2, 2, _U16), (4, 4, _U32)])
    _u8(data, 0); _u8(data, 4); _u16(data, 255); _u16(data, 0); _u32(data, start_g)

    # activity (local 1, global 34)
    _write_def(data, 1, 34,
               [(253, 4, _U32), (1, 2, _U16), (2, 1, _E), (3, 1, _E), (4, 1, _E)])
    _u8(data, 1); _u32(data, end_g); _u16(data, 1); _u8(data, 0); _u8(data, 26); _u8(data, 1)

    # session (local 2, global 18)
    _write_def(data, 2, 18, [
        (254, 2, _U16), (253, 4, _U32), (2, 4, _U32), (7, 4, _U32), (8, 4, _U32),
        (5, 1, _E), (0, 1, _E), (1, 1, _E),
    ])
    _u8(data, 2); _u16(data, 0); _u32(data, end_g); _u32(data, start_g)
    _u32(data, round(duration_s * 1000)); _u32(data, round(duration_s * 1000))
    _u8(data, sport_code); _u8(data, 8); _u8(data, 1)

    # record (local 4, global 20) - definition adapts to what the points actually carry.
    rec_fields = [(253, 4, _U32)]                     # timestamp
    if has_pos:
        rec_fields += [(0, 4, _S32), (1, 4, _S32)]    # position_lat / position_long
    rec_fields += [(2, 2, _U16)]                      # altitude
    if has_hr:
        rec_fields += [(3, 1, _U8)]                   # heart_rate
    _write_def(data, 4, 20, rec_fields)

    SEMI = (2 ** 31) / 180
    for p in points:
        _u8(data, 4)
        _u32(data, _epoch(p["time"]) - GARMIN_EPOCH)
        if has_pos:
            lat = p.get("lat"); lon = p.get("lon")
            _s32(data, round(lat * SEMI) if lat is not None else 0x7FFFFFFF)
            _s32(data, round(lon * SEMI) if lon is not None else 0x7FFFFFFF)
        ele = p.get("ele")
        _u16(data, max(0, round((ele + 500) * 5)) if ele is not None else 0xFFFF)
        if has_hr:
            hr = p.get("hr")
            _u8(data, int(hr) if hr is not None else 0xFF)

    hdr = bytearray()
    _u8(hdr, 14); _u8(hdr, 0x10); _u16(hdr, 0x0834); _u32(hdr, len(data))
    hdr.extend(b".FIT"); _u16(hdr, _fit_crc(hdr))
    file_crc = bytearray(); _u16(file_crc, _fit_crc(data))
    blob = bytes(hdr) + bytes(data) + bytes(file_crc)
    with open(out_path, "wb") as fh:
        fh.write(blob)
    return len(blob)


# ─── self-test ──────────────────────────────────────────────────────────────────────────
# Runs with no hardware and no third-party module: build a tiny synthetic activity and prove
# both writers emit non-empty, structurally sane output (FIT header magic + CRC round-trip).

def _selftest() -> int:
    from datetime import timedelta
    t0 = datetime(2015, 6, 1, 8, 0, 0, tzinfo=timezone.utc)
    pts = [{"time": t0 + timedelta(seconds=i * 10),
            "lat": 48.8566 + i * 1e-4, "lon": 2.3522 + i * 1e-4,
            "ele": 35.0 + i, "hr": 120 + i} for i in range(5)]
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    n_gpx = write_gpx(pts, d / "t.gpx", name="selftest")
    n_fit = write_fit(pts, d / "t.fit", sport="run", name="selftest")
    blob = (d / "t.fit").read_bytes()
    assert blob[8:12] == b".FIT", "FIT magic missing"
    body = blob[14:-2]
    assert _fit_crc(body) == struct.unpack("<H", blob[-2:])[0], "FIT file CRC mismatch"
    # HR-only (no position) path must also produce a legal file.
    hr_only = [{"time": p["time"], "lat": None, "lon": None, "ele": p["ele"], "hr": p["hr"]}
               for p in pts]
    write_fit(hr_only, d / "hr.fit", sport="generic")
    assert (d / "hr.fit").stat().st_size > 14
    print(f"legacy_export self-test OK  gpx={n_gpx}B fit={n_fit}B  ({d})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
