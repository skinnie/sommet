#!/usr/bin/env python3
"""Decodes the Ambit3 ExerciseLog flash region (recorded moves: header + per-sample GPS/HR/
etc track data) into GPX and FIT. This is NOT a from-scratch reverse-engineering job: both
formats are already fully implemented in this project's own base fork,
`assets/opensportsync-main.zip` - GPX via openambit's `libambit` (C:
`android/app/src/main/cpp/libambit/pmem20.c` + `libambit.h` + `jni_bridge.cpp`'s
`convertEntryToGpx()`), FIT via `src/services/FitExport.ts` (pure TypeScript, unrelated code
path - FIT is built from a GPX + activity metadata, not from raw samples directly). This
module ports both, function/message-for-message, so they can be run and checked directly
against a real dump rather than trusted as-is - this project's standing practice for any
vendored/decompiled format.

`device_support.c` registers this exact watch ("Emu"/Suunto Ambit3 Peak) against
`ambit_device_driver_ambit3`, which always reads log entries with
`LIBAMBIT_PMEM20_FLAGS_UNKNOWN2_PADDING_48` - hardcoded here to match, not offered as a flag.

Verified 2026-08-05 against a real recorded move (see exercise_log_andre.md): decoded header
matched an independent SBEM `logbook` query exactly, the decoded GPS track's own summed
distance (454 m) matched the watch's own reported distance (451 m) to within 0.7%, and a real
`fitparse` read of the FIT output landed on the same figure (454.5 m) independently. All
timestamps (GPX and FIT) are anchored on the GPS-embedded `utc_base_time`, not the decoding
machine's system clock - confirmed identical output for the same real move decoded three
times, once each under `TZ=Europe/Paris`, `TZ=America/New_York`, `TZ=Asia/Tokyo`.

    ./tools/exercise_log.py --from /tmp/dump_ExerciseLog.bin --gpx-out /tmp/moves --fit-out /tmp/moves
"""

import argparse
import datetime
import math
import struct

EXERCISE_LOG_BASE = 0x027AC40
EXERCISE_LOG_SIZE = 5526464  # confirmed live via 0x0b21, this project, earlier this session

WRAP_START_OFFSET = 0x12  # PMEM20_LOG_WRAP_START_OFFSET, pmem20.c
HEADER_MIN_LEN = 512  # PMEM20_LOG_HEADER_MIN_LEN

FLAGS_UNKNOWN2_PADDING_48 = True  # always set for Ambit3 ("Emu"), device_driver_ambit3.c:705

PERIODIC_TYPE_NAMES = {
    0x01: "latitude", 0x02: "longitude", 0x03: "distance", 0x04: "speed", 0x05: "hr",
    0x06: "time", 0x07: "gpsspeed", 0x08: "wristaccspeed", 0x09: "bikepodspeed",
    0x0a: "ehpe", 0x0b: "evpe", 0x0c: "altitude", 0x0d: "abspressure", 0x0e: "energy",
    0x0f: "temperature", 0x10: "charge", 0x11: "gpsaltitude", 0x12: "gpsheading",
    0x13: "gpshdop", 0x14: "gpsvdop", 0x15: "wristcadence", 0x16: "snr",
    0x17: "noofsatellites", 0x18: "sealevelpressure", 0x19: "verticalspeed",
    0x1a: "cadence", 0x1f: "bikepower", 0x20: "swimingstrokecnt",
    0x64: "ruleoutput1", 0x65: "ruleoutput2", 0x66: "ruleoutput3", 0x67: "ruleoutput4",
    0x68: "ruleoutput5",
}

# Byte width/signedness per periodic-sample type, from each union member's C type in
# ambit_log_sample_periodic_value_s (libambit.h:272-309). Anything absent here falls back
# to unsigned u16 in parse_sample() below, matching every union member the struct itself
# declares as uint16_t (speed/gpsspeed/wristaccspeed/bikepodspeed/energy/gpsheading/
# wristcadence/bikepower) - not a guess, every member is accounted for one way or the other.
PERIODIC_TYPE_FORMATS = {
    0x01: "<i", 0x02: "<i",                          # latitude, longitude (i32)
    0x03: "<I", 0x06: "<I", 0x0a: "<I", 0x0b: "<I",   # distance, time, ehpe, evpe (u32)
    0x0d: "<I", 0x20: "<I",                           # abspressure, swimingstrokecnt (u32)
    0x11: "<i",                                       # gpsaltitude (i32)
    0x64: "<i", 0x65: "<i", 0x66: "<i", 0x67: "<i", 0x68: "<i",  # ruleoutput1-5 (i32)
    0x0c: "<h", 0x0f: "<h", 0x18: "<h", 0x19: "<h",   # altitude, temperature,
                                                        # sealevelpressure, verticalspeed (i16)
    0x16: "raw16",                                    # snr (16 raw bytes, not an int)
    0x05: "<B", 0x10: "<B", 0x13: "<B", 0x14: "<B",   # hr, charge, gpshdop, gpsvdop,
    0x17: "<B", 0x1a: "<B",                           # noofsatellites, cadence (u8)
}


def read8(data, offset):
    return data[offset]


def read16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def read32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


class Cursor:
    """Mirrors read8inc/read16inc/read32inc - a read that advances an offset in place."""

    def __init__(self, data, offset=0):
        self.data = data
        self.offset = offset

    def u8(self):
        v = read8(self.data, self.offset)
        self.offset += 1
        return v

    def u16(self):
        v = read16(self.data, self.offset)
        self.offset += 2
        return v

    def u32(self):
        v = read32(self.data, self.offset)
        self.offset += 4
        return v

    def skip(self, n):
        self.offset += n

    def bytes(self, n):
        v = self.data[self.offset:self.offset + n]
        self.offset += n
        return v


def parse_master_header(data):
    """The region's own 16-byte index at mem_start: last_entry, first_entry, entries,
    next_free_address, each u32 LE - libambit_pmem20_log_init()."""
    last_entry, first_entry, entries, next_free_address = struct.unpack_from("<IIII", data, 0)
    return {
        "last_entry": last_entry, "first_entry": first_entry,
        "entries": entries, "next_free_address": next_free_address,
    }


def logical_read(data, mem_size, offset, length):
    """Reads `length` bytes starting at logical offset `offset` within the region, wrapping
    past WRAP_START_OFFSET if the read runs past mem_size - mirrors the wraparound handling
    in libambit_pmem20_log_read_entry()."""
    if offset + length <= mem_size:
        return data[offset:offset + length]
    first_part = data[offset:mem_size]
    remaining = length - len(first_part)
    second_part = data[WRAP_START_OFFSET:WRAP_START_OFFSET + remaining]
    return first_part + second_part


def parse_log_header(data, offset, datalen, unknown2_padding_48=FLAGS_UNKNOWN2_PADDING_48):
    """Port of libambit_pmem20_log_parse_header() - one ambit_log_header_t, ~129+ bytes."""
    if datalen < 129:
        raise ValueError(f"header too short: {datalen} bytes")
    c = Cursor(data, offset + 1)  # offset=1 in the C source: first byte is skipped/unused
    h = {}
    h["year"] = c.u16()
    h["month"] = c.u8()
    h["day"] = c.u8()
    h["hour"] = c.u8()
    h["minute"] = c.u8()
    h["msec"] = c.u8() * 1000
    c.skip(5)  # unknown1
    h["duration_ms"] = c.u32() * 100
    h["ascent"] = c.u16()
    h["descent"] = c.u16()
    h["ascent_time_ms"] = c.u32() * 1000
    h["descent_time_ms"] = c.u32() * 1000
    h["recovery_time_ms"] = c.u16() * 60 * 1000
    h["speed_avg_mh"] = c.u16() * 10  # 10 m/h units -> m/h
    h["speed_max_mh"] = c.u16() * 10
    h["altitude_max"] = c.u16()
    h["altitude_min"] = c.u16()
    h["heartrate_avg"] = c.u8()
    h["heartrate_max"] = c.u8()
    h["peak_training_effect"] = c.u8()
    h["activity_type"] = c.u8()
    name_bytes = c.bytes(16)
    # CORRECTED 2026-08-22: was iso-8859-15 - real hardware (André's French Ambit3 Sport)
    # proved the watch sends UTF-8 for name fields, see ambit_format.py's encode_name().
    h["activity_name"] = name_bytes.split(b"\0")[0].decode("utf-8", "replace")
    h["heartrate_min"] = c.u8()
    h["unknown2"] = c.u8()
    if unknown2_padding_48:
        c.skip(46)
        h["heartrate_min"] = c.u8()
        c.skip(1)
    h["temperature_max"] = c.u16()
    h["temperature_min"] = c.u16()
    h["distance"] = c.u32()
    h["samples_count"] = c.u32()
    h["energy_consumption"] = c.u16()
    h["cadence_max"] = c.u8()
    h["cadence_avg"] = c.u8()
    c.skip(2)  # unknown3
    h["swimming_pool_lengths"] = c.u16()
    h["speed_max_time"] = c.u32()
    h["altitude_max_time"] = c.u32()
    h["altitude_min_time"] = c.u32()
    h["heartrate_max_time"] = c.u32()
    h["heartrate_min_time"] = c.u32()
    h["temperature_max_time"] = c.u32()
    h["temperature_min_time"] = c.u32()
    h["cadence_max_time"] = c.u32()
    h["swimming_pool_length"] = c.u32()
    h["first_fix_time_ms"] = c.u16() * 1000
    h["battery_start"] = c.u8()
    h["battery_end"] = c.u8()
    c.skip(4)  # unknown5
    h["distance_before_calib"] = c.u32()
    return h


def parse_sample(data, offset, periodic_spec_ref, samples, time_compensators):
    """Port of parse_sample() - one [u16 len][u8 type][...] framed sample, appended to
    `samples` (a list) unless it's a type-0 periodic-spec update (which just mutates
    periodic_spec_ref[0], a 1-element list used as a mutable box)."""
    sample_len = read16(data, offset)
    sample_type = read8(data, offset + 2)
    body_off = offset + 3

    if sample_type == 0:
        periodic_spec_ref[0] = offset + 2  # matches "*spec = buf + offset + 2" in C
        return

    if sample_type == 2:
        spec = periodic_spec_ref[0]
        spec_count = read16(data, spec + 1)
        values = []
        entry_off = spec + 3
        for _ in range(spec_count):
            spec_type = read16(data, entry_off)
            spec_offset = read16(data, entry_off + 2)
            entry_off += 6  # periodic_sample_spec_t: u16 type, u16 offset, u16 length
            name = PERIODIC_TYPE_NAMES.get(spec_type, f"0x{spec_type:02x}")
            field_off = body_off + spec_offset
            fmt = PERIODIC_TYPE_FORMATS.get(spec_type, "<H")  # u16 is the union's default
            if fmt == "raw16":
                val = data[field_off:field_off + 16]
            else:
                val = struct.unpack_from(fmt, data, field_off)[0]
            values.append({"type": spec_type, "name": name, "value": val})
        samples.append({
            "type": "periodic", "time": read32(data, offset + sample_len - 2),
            "values": values,
        })
        return

    if sample_type == 3:
        c = Cursor(data, body_off)
        rel_time = c.u32()
        episodic_type = c.u8()
        s = {"time": rel_time}
        if episodic_type == 0x04:
            s["type"] = "logpause"
        elif episodic_type == 0x05:
            s["type"] = "logrestart"
        elif episodic_type == 0x06:
            s["type"] = "ibi"
            count = (sample_len - 6) // 2
            s["ibi"] = [c.u16() for _ in range(count)]
        elif episodic_type == 0x07:
            s["type"] = "ttff"
            s["value"] = c.u16()
        elif episodic_type == 0x08:
            s["type"] = "distance_source"
            s["value"] = c.u8()
        elif episodic_type == 0x09:
            s["type"] = "lapinfo"
            s["event_type"] = c.u8()
            s["year"] = c.u16(); s["month"] = c.u8(); s["day"] = c.u8()
            s["hour"] = c.u8(); s["minute"] = c.u8(); s["msec"] = c.u8() * 1000
            s["duration_ms"] = c.u32() * 100
            s["distance"] = c.u32()
        elif episodic_type == 0x0d:
            s["type"] = "altitude_source"
            s["source_type"] = c.u8()
            s["altitude_offset"] = struct.unpack_from("<h", data, c.offset)[0]; c.skip(2)
            s["pressure_offset"] = struct.unpack_from("<h", data, c.offset)[0]; c.skip(2)
        elif episodic_type == 0x0f:
            s["type"] = "gps_base"
            s["navvalid"] = c.u16(); s["navtype"] = c.u16()
            s["utc_year"] = c.u16(); s["utc_month"] = c.u8(); s["utc_day"] = c.u8()
            s["utc_hour"] = c.u8(); s["utc_minute"] = c.u8(); s["utc_msec"] = c.u16()
            s["latitude"] = struct.unpack_from("<i", data, c.offset)[0]; c.skip(4)
            s["longitude"] = struct.unpack_from("<i", data, c.offset)[0]; c.skip(4)
            s["altitude"] = struct.unpack_from("<i", data, c.offset)[0]; c.skip(4)
            s["speed"] = c.u16(); s["heading"] = c.u16()
            s["ehpe"] = c.u32()
            s["noofsatellites"] = c.u8(); s["hdop"] = c.u8()
            sat_count = (sample_len - 40) // 4
            sats = []
            for _ in range(sat_count):
                sv = c.u8(); state = c.u8(); c.skip(1); snr = c.u8()
                sats.append({"sv": sv, "state": state, "snr": snr})
            s["satellites"] = sats
        elif episodic_type == 0x10:
            s["type"] = "gps_small"
            s["latitude"] = struct.unpack_from("<h", data, c.offset)[0]; c.skip(2)
            s["longitude"] = struct.unpack_from("<h", data, c.offset)[0]; c.skip(2)
            c.skip(2)  # time (seconds), unused
            s["ehpe"] = c.u8() * 100
            s["noofsatellites"] = c.u8()
        elif episodic_type == 0x11:
            s["type"] = "gps_tiny"
            s["latitude"] = struct.unpack_from("<b", data, c.offset)[0]; c.skip(1)
            s["longitude"] = struct.unpack_from("<b", data, c.offset)[0]; c.skip(1)
            s["unknown"] = c.u8()
        elif episodic_type == 0x12:
            s["type"] = "time"
            s["hour"] = c.u8(); s["minute"] = c.u8(); s["second"] = c.u8()
        elif episodic_type == 0x14:
            s["type"] = "swimming_turn"
            c.skip(1)
            time_compensators.append((len(samples), 0 - c.u16() * 100))
            c.skip(1)
            s["distance"] = c.u32()
            s["lengths"] = c.u16()
            c.skip(18)
            s["classification"] = [c.u16() for _ in range(4)]
            s["style"] = c.u8()
        elif episodic_type == 0x15:
            s["type"] = "swimming_stroke"
            time_compensators.append((len(samples), 0 - c.u16() * 100))
        elif episodic_type == 0x18:
            s["type"] = "activity"
            s["activitytype"] = c.u16()
            s["sportmode"] = c.u32()
        elif episodic_type == 0x1a:
            s["type"] = "cadence_source"
            s["value"] = c.u8()
        elif episodic_type == 0x1b:
            s["type"] = "position"
            s["latitude"] = struct.unpack_from("<i", data, c.offset)[0]; c.skip(4)
            s["longitude"] = struct.unpack_from("<i", data, c.offset)[0]; c.skip(4)
        elif episodic_type == 0x1c:
            s["type"] = "fwinfo"
            s["version"] = c.bytes(4)
            s["build_year"] = c.u16(); s["build_month"] = c.u8(); s["build_day"] = c.u8()
            s["build_hour"] = c.u8(); s["build_minute"] = c.u8(); s["build_msec"] = c.u16()
        else:
            s["type"] = "unknown"
            s["episodic_type"] = episodic_type
            s["data"] = data[body_off + 1:body_off + 1 + sample_len - 5]
        samples.append(s)
        return

    samples.append({"type": "unknown", "raw_type": sample_type,
                     "data": data[body_off:body_off + sample_len - 1]})


def correct_samples(samples, header):
    """Port of correct_samples(): accumulates time onto episodic samples, applies the
    time_compensators collected during parsing, delta-decodes gps_small/gps_tiny positions
    relative to the last gps_base/gps_small fix, applies an altitude_source correction to
    periodic samples that appear BEFORE the altitude_source sample (matching the C code's
    own `sample_count < altisource_index` condition, an odd-looking but deliberate rule -
    preserved exactly, not "fixed"), computes a real per-sample UTC time from the first
    gps_base sample's own `utc_base_time` field, and finally stable-sorts by time.

    UTC handling is the one place this port deliberately goes beyond a faithful copy of the
    reference: `pmem20.c`'s own `correct_samples()` computes this same `utc_time` per sample
    (via `add_time(&utcbase, samples[i].time, &samples[i].utc_time)`), but neither
    `opensportsync`'s own `convertEntryToGpx()` nor this port's first draft actually used it -
    both instead treated the header's local date_time as if it were already UTC, which is
    only correct when the decoding machine happens to share the watch's timezone. GPS time is
    unambiguous (it comes from the satellite constellation, not the watch's clock), so anchor
    on it instead: `utc_base_time` is the true UTC time AT the first gps_base sample's own
    (already time-corrected) relative timestamp, so subtracting that gives `utcbase`, the true
    UTC time at entry-relative time=0 - matching `add_time(&utcsource.utc_base_time,
    0-utcsource.time, &utcbase)` exactly, just via Python's own (correct) calendar arithmetic
    instead of hand-rolled leap-year/month-length C code. Requested explicitly, 2026-08-05,
    after the first draft's local-time assumption was caught during FIT verification."""
    last_periodic_time = None
    utcsource = None
    altisource = None
    altisource_index = None
    last_base_lat = last_base_long = 0
    last_small_lat = last_small_long = 0
    last_ehpe = 0

    time_compensators = {i: v for i, v in samples[0].pop("_time_compensators", [])} \
        if samples and "_time_compensators" in samples[0] else {}

    for i, s in enumerate(samples):
        if s["type"] == "periodic":
            last_periodic_time = s["time"]
        elif last_periodic_time is not None:
            s["time"] = s.get("time", 0) + last_periodic_time
        else:
            s["time"] = s.get("time", 0)

        comp = time_compensators.get(i, 0)
        if comp < 0 and s["time"] < -comp:
            s["time"] = 0
        else:
            s["time"] += comp

        if utcsource is None and s["type"] == "gps_base":
            utcsource = s

        if s["type"] == "gps_base":
            last_base_lat = s["latitude"]
            last_base_long = s["longitude"]
            last_small_lat = s["latitude"]
            last_small_long = s["longitude"]
            last_ehpe = s["ehpe"]
        elif s["type"] == "gps_small":
            s["latitude"] = last_base_lat + s["latitude"] * 10
            s["longitude"] = last_base_long + s["longitude"] * 10
            last_small_lat = s["latitude"]
            last_small_long = s["longitude"]
            last_ehpe = s["ehpe"]
        elif s["type"] == "gps_tiny":
            s["latitude"] = last_small_lat + s["latitude"] * 10
            s["longitude"] = last_small_long + s["longitude"] * 10
            s["ehpe"] = min(last_ehpe, 700)
            last_small_lat = s["latitude"]
            last_small_long = s["longitude"]

        if altisource is None and s["type"] == "altitude_source":
            altisource = s
            altisource_index = i

    utcbase = None
    if utcsource is not None:
        gps_utc = datetime.datetime(
            utcsource["utc_year"], utcsource["utc_month"], utcsource["utc_day"],
            utcsource["utc_hour"], utcsource["utc_minute"],
            tzinfo=datetime.timezone.utc,
        ) + datetime.timedelta(milliseconds=utcsource["utc_msec"])
        utcbase = gps_utc - datetime.timedelta(milliseconds=utcsource["time"])
        header["utc_start"] = utcbase

    for i, s in enumerate(samples):
        if utcbase is not None:
            s["utc_time"] = utcbase + datetime.timedelta(milliseconds=s["time"])
        if altisource is not None and s["type"] == "periodic" and i < altisource_index:
            for v in s["values"]:
                if v["name"] == "sealevelpressure":
                    v["value"] += altisource["pressure_offset"]
                if v["name"] == "altitude":
                    v["value"] += altisource["altitude_offset"]

    # Stable-ish insertion re-sort by time, matching the C code's shuffle-into-place loop
    for i in range(1, len(samples)):
        if samples[i]["time"] < samples[i - 1]["time"]:
            j = i - 1
            while j > 0 and samples[i]["time"] < samples[j - 1]["time"]:
                j -= 1
            item = samples.pop(i)
            samples.insert(j, item)


def read_entry_at(data, mem_size, entry_address, mem_start):
    """Port of libambit_pmem20_log_read_entry() - reads one full entry (header + samples)
    starting at an absolute address, given the region already fully in `data`. Returns
    (header_dict, samples_list, next_address, prev_address)."""
    base = entry_address - mem_start
    magic = data[base:base + 4]
    if magic != b"PMEM":
        raise ValueError(f"no PMEM magic at 0x{entry_address:x} (got {magic!r})")
    off = base + 4
    next_addr = read32(data, off); off += 4
    prev_addr = read32(data, off); off += 4

    tmp_len = read16(data, off); off += 2
    periodic_spec_ref = [off]  # matches: spec initially points right after this u16
    off += tmp_len

    tmp_len = read16(data, off); off += 2
    header = parse_log_header(data, off, tmp_len)
    off += tmp_len

    # NOTE: parse_sample() reads directly from `data` at absolute/wrapped-logical offsets -
    # correct as long as no single sample's bytes straddle the mem_size -> WRAP_START_OFFSET
    # boundary. True for anything but a multi-megabyte single entry, not guarded further.
    samples = []
    time_compensators = []
    logical_off = off
    while len(samples) < header["samples_count"]:
        if logical_off >= mem_size:
            logical_off = WRAP_START_OFFSET + (logical_off - mem_size)
        sample_len = struct.unpack("<H", logical_read(data, mem_size, logical_off, 2))[0]
        parse_sample(data, logical_off, periodic_spec_ref, samples, time_compensators)
        logical_off += 2 + sample_len

    if samples:
        samples[0]["_time_compensators"] = time_compensators
    correct_samples(samples, header)

    return header, samples, next_addr, prev_addr


def walk_entries(data, mem_start=EXERCISE_LOG_BASE, mem_size=EXERCISE_LOG_SIZE, skip_count=0):
    """Yields (header, samples) for every entry reachable from the region's own master
    index, oldest or newest first per whatever `first_entry` points at - mirrors
    log_next_header()+log_read_entry() walking the on-flash linked list, no live NSP
    query needed since the whole region is already in `data`.

    skip_count: real, 2026-08-11 (desktop Activities page perf audit) - fast-skip this many
    oldest entries via a cheap peek (just the magic + next_addr, 8 bytes) instead of the full
    read_entry_at() decode (which walks every sample of every entry). The list is a linked
    list walked oldest-first, so entries the caller already has cached (by this same index
    convention - main()'s own `count` enumeration) are always a contiguous prefix - the
    caller can skip re-decoding activities it already has just by knowing how many. This
    does NOT reduce what's read off the watch (still one bulk flash read, already bounded to
    next_free_address by the 2026-08-07 fix above) - it only skips the CPU-side sample decode
    for entries that read produced but the caller doesn't need again, which is the actual
    cost that scaled with total history on every single Activities page refresh before this."""
    master = parse_master_header(data)
    if master["entries"] == 0:
        return
    current = mem_start
    nxt = master["first_entry"]
    skipped = 0
    while current != nxt:
        if skipped < skip_count:
            base = nxt - mem_start
            magic = data[base:base + 4]
            if magic != b"PMEM":
                raise ValueError(f"no PMEM magic at 0x{nxt:x} (got {magic!r}) while fast-skipping")
            next_addr = read32(data, base + 4)
            current = nxt
            nxt = next_addr
            skipped += 1
            continue
        header, samples, next_addr, prev_addr = read_entry_at(data, mem_size, nxt, mem_start)
        yield header, samples
        current = nxt
        nxt = next_addr


def extract_track_points(header, samples):
    """The GPS-position-tracking walk shared by to_gpx() and to_fit() - factored out of
    convertEntryToGpx()'s logic (both consumers need the same lat/lon/ele/time sequence;
    to_fit() mirrors what generateFitFile() gets handed after opensportsync's own GpxParser
    re-reads the GPX this same walk produces, so doing it once here instead of round-
    tripping through GPX text is equivalent, not a divergence). Returns a list of
    {lat, lon, ele, time (timezone-aware UTC datetime)}.

    Uses each sample's real `utc_time` (set by correct_samples() from the GPS-embedded
    `utc_base_time`, not the watch's local clock) - accurate regardless of what timezone the
    decoding machine is set to. Every point emitted here has GPS position data by
    definition (that's what "emit" means below), and utc_time is only ever unset when the
    whole entry had zero gps_base samples - i.e. zero position data either - so the fallback
    path (header-local-time, the old timezone-dependent behaviour) is unreachable in
    practice; kept only so this never raises on a genuinely GPS-less entry."""
    start = datetime.datetime(
        header["year"], header["month"], header["day"],
        header["hour"], header["minute"], header["msec"] // 1000,
        tzinfo=datetime.timezone.utc)

    points = []
    cur_lat = cur_lon = cur_ele = 0.0
    has_pos = False

    for s in samples:
        emit = False
        if s["type"] == "gps_base":
            cur_lat = s["latitude"] / 1e7
            cur_lon = s["longitude"] / 1e7
            cur_ele = s["altitude"] / 100.0
            has_pos = True
            emit = True
        elif s["type"] == "gps_small":
            cur_lat = s["latitude"] / 1e7
            cur_lon = s["longitude"] / 1e7
            has_pos = True
            emit = True
        elif s["type"] == "gps_tiny":
            cur_lat = s["latitude"] / 1e7
            cur_lon = s["longitude"] / 1e7
            has_pos = True
            emit = True
        elif s["type"] == "periodic" and has_pos:
            lat_ok = lon_ok = False
            lat, lon = cur_lat, cur_lon
            for v in s["values"]:
                if v["name"] == "latitude":
                    lat = v["value"] / 1e7; lat_ok = True
                if v["name"] == "longitude":
                    lon = v["value"] / 1e7; lon_ok = True
            if lat_ok and lon_ok:
                cur_lat, cur_lon = lat, lon
                emit = True

        if emit and has_pos and (cur_lat != 0.0 or cur_lon != 0.0):
            time = s.get("utc_time") or (start + datetime.timedelta(milliseconds=s["time"]))
            points.append({"lat": cur_lat, "lon": cur_lon, "ele": cur_ele, "time": time})

    return points


def _iso_utc(dt):
    """ISO-8601 with a 'Z' suffix from a timezone-aware UTC datetime, GPX/FIT-tool style
    ('+00:00' also means UTC but 'Z' is the more universally expected form)."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def to_gpx(header, samples):
    """Port of jni_bridge.cpp's convertEntryToGpx() - same message/tag structure as the
    reference, not extended or simplified. The one change from the reference is what feeds
    the `time` values themselves: real GPS-derived UTC (see correct_samples()) rather than
    the reference's local-clock assumption - the metadata time falls back to the
    unconverted header fields only for the (position-less, so track-less anyway) entries
    that never got a GPS fix at all."""
    points = extract_track_points(header, samples)
    meta_time = (_iso_utc(header["utc_start"]) if "utc_start" in header else
                 f'{header["year"]:04d}-{header["month"]:02d}-{header["day"]:02d}'
                 f'T{header["hour"]:02d}:{header["minute"]:02d}:{header["msec"]//1000:02d}Z')

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="ambit-app exercise_log.py"'
        ' xmlns="http://www.topografix.com/GPX/1/1">',
        f'  <metadata><time>{meta_time}</time></metadata>',
        f'  <trk><name>{header["activity_name"]}</name>',
        '    <extensions>',
        f'      <duration>{header["duration_ms"] // 1000}</duration>',
        f'      <distance>{header["distance"]}</distance>',
        f'      <ascent>{header["ascent"]}</ascent>',
        # kcal, not joules - libambit's own header annotates this field
        # `uint16_t energy_consumption; /* kcal */` (see
        # android/.../libambit/libambit.h), and this project's decoder reads the same
        # u16 at the same offset. Exported so the app can show what a move actually cost
        # (André, 2026-08-11: "add kacolires in Kcal (this is the energy you spent)");
        # the watch already had it, it was simply never carried through.
        f'      <energy>{header["energy_consumption"]}</energy>',
        f'      <sport_type>{header["activity_type"]}</sport_type>',
        # Richer summary metrics the watch already records in its log header (André,
        # 2026-08-16: "show whatever is available from the file... pace? swolf?"). All were
        # parsed by parse_log_header() but never carried through to the app; emitted here so
        # the Activities list can offer them as selectable columns. Units are the watch's raw
        # header units; the app converts to the user's unit setting for display:
        #   hr = bpm, cadence = rpm/spm, speed = m/h (metres per hour), descent = m,
        #   recovery = seconds, peak_training_effect = value*10 (so 35 -> 3.5),
        #   pool_lengths = count, max_altitude = m.
        f'      <avg_hr>{header["heartrate_avg"]}</avg_hr>',
        f'      <max_hr>{header["heartrate_max"]}</max_hr>',
        f'      <avg_cadence>{header["cadence_avg"]}</avg_cadence>',
        f'      <max_cadence>{header["cadence_max"]}</max_cadence>',
        f'      <avg_speed>{header["speed_avg_mh"]}</avg_speed>',
        f'      <max_speed>{header["speed_max_mh"]}</max_speed>',
        f'      <descent>{header["descent"]}</descent>',
        f'      <recovery_time>{header["recovery_time_ms"] // 1000}</recovery_time>',
        f'      <peak_training_effect>{header["peak_training_effect"]}</peak_training_effect>',
        f'      <pool_lengths>{header["swimming_pool_lengths"]}</pool_lengths>',
        f'      <max_altitude>{header["altitude_max"]}</max_altitude>',
        '    </extensions>',
        '  <trkseg>',
    ]

    for p in points:
        lines.append(
            f'    <trkpt lat="{p["lat"]:.7f}" lon="{p["lon"]:.7f}">'
            f'<ele>{p["ele"]:.1f}</ele>'
            f'<time>{_iso_utc(p["time"])}</time></trkpt>')

    lines.append('  </trkseg></trk>')
    lines.append('</gpx>')
    return "\n".join(lines)


# ─── FIT export - port of opensportsync's src/services/FitExport.ts ───────────────────────
#
# A much smaller, self-contained format than the GPX side: a 14-byte header, a run of
# Definition+Data message pairs (file_id, activity, session, lap, then one record per GPS
# point), and a Garmin CRC-16 both over the header and over the data section. Ported
# constant-for-constant and message-for-message from the TypeScript - variable names below
# echo the originals (u8/u16/u32/s32, E/U8/U16/U32/S32 base-type codes) on purpose, to keep
# a line-for-line correspondence checkable against FitExport.ts rather than a paraphrase.

GARMIN_EPOCH = 631065600  # Garmin epoch (1989-12-31T00:00:00Z) as a Unix timestamp

_CRC_TABLE = [
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
]


def _fit_crc(data):
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
    """fields: list of (field_num, size, base_type)."""
    _u8(b, 0x40 | local)
    _u8(b, 0)   # reserved
    _u8(b, 0)   # architecture: little-endian
    _u16(b, global_num)
    _u8(b, len(fields))
    for num, size, base_type in fields:
        _u8(b, num); _u8(b, size); _u8(b, base_type)


def _to_fit_sport(activity_type):
    t = activity_type.lower()
    if any(k in t for k in ("run", "course", "jogging")):
        return 1
    if any(k in t for k in ("cycl", "vtt", "vélo", "bike")):
        return 2
    if "alpin" in t:
        return 13
    if any(k in t for k in ("fond", "nordic", "cross")):
        return 12
    if any(k in t for k in ("randon", "hik")):
        return 17
    if any(k in t for k in ("walk", "march")):
        return 11
    return 0  # generic


def to_fit(header, samples):
    """Port of generateFitFile() - takes this project's own already-decoded header/samples
    (the equivalent of what the TS version gets after parseTrackPoints() re-reads a GPX) and
    returns raw FIT bytes. header['distance']/['duration_ms']/['ascent'] are used directly,
    matching the TS `activity.duration_s || stats...` fallback (we always have the real
    watch-reported values, so the fallback branch never applies here)."""
    points = extract_track_points(header, samples)
    if not points:
        raise ValueError("no GPS points in this entry")

    start_epoch = int(points[0]["time"].timestamp())
    end_epoch = int(points[-1]["time"].timestamp())
    start_g = start_epoch - GARMIN_EPOCH
    end_g = end_epoch - GARMIN_EPOCH

    duration_s = header["duration_ms"] / 1000
    dist_m = header["distance"]
    d_plus = round(header["ascent"])
    d_minus = round(header["descent"])
    sport = _to_fit_sport(header["activity_name"])

    data = bytearray()

    # file_id (local 0, global 0)
    _write_def(data, 0, 0, [(0, 1, _E), (1, 2, _U16), (2, 2, _U16), (4, 4, _U32)])
    _u8(data, 0)
    _u8(data, 4)          # type = 4 (activity)
    _u16(data, 255)       # manufacturer = 255 (development)
    _u16(data, 0)         # product
    _u32(data, start_g)   # time_created

    # activity (local 1, global 34)
    _write_def(data, 1, 34,
                [(253, 4, _U32), (1, 2, _U16), (2, 1, _E), (3, 1, _E), (4, 1, _E)])
    _u8(data, 1)
    _u32(data, end_g)
    _u16(data, 1)         # num_sessions
    _u8(data, 0)          # type = 0 (manual)
    _u8(data, 26)         # event = 26 (activity)
    _u8(data, 1)          # event_type = 1 (stop)

    # session (local 2, global 18)
    _write_def(data, 2, 18, [
        (254, 2, _U16), (253, 4, _U32), (2, 4, _U32), (7, 4, _U32), (8, 4, _U32),
        (9, 4, _U32), (25, 2, _U16), (26, 2, _U16), (5, 1, _E), (0, 1, _E), (1, 1, _E),
    ])
    _u8(data, 2)
    _u16(data, 0)                             # message_index
    _u32(data, end_g)                         # timestamp
    _u32(data, start_g)                       # start_time
    _u32(data, round(duration_s * 1000))      # total_elapsed_time (scale=1000)
    _u32(data, round(duration_s * 1000))      # total_timer_time
    _u32(data, round(dist_m * 100))           # total_distance (scale=100, cm)
    _u16(data, d_plus)
    _u16(data, d_minus)
    _u8(data, sport)
    _u8(data, 8)          # event = 8 (session)
    _u8(data, 1)          # event_type = 1 (stop)

    # lap (local 3, global 19)
    _write_def(data, 3, 19,
                [(254, 2, _U16), (253, 4, _U32), (2, 4, _U32), (7, 4, _U32),
                 (9, 4, _U32), (0, 1, _E), (1, 1, _E)])
    _u8(data, 3)
    _u16(data, 0)
    _u32(data, end_g)
    _u32(data, start_g)
    _u32(data, round(duration_s * 1000))
    _u32(data, round(dist_m * 100))
    _u8(data, 9)          # event = 9 (lap)
    _u8(data, 1)

    # record (local 4, global 20) - definition, then one data record per point
    _write_def(data, 4, 20,
                [(253, 4, _U32), (0, 4, _S32), (1, 4, _S32), (2, 2, _U16), (5, 4, _U32)])

    SEMI = (2 ** 31) / 180  # degrees -> semicircles
    cum_dist = 0.0
    for i, p in enumerate(points):
        ts = int(p["time"].timestamp()) - GARMIN_EPOCH
        lat = round(p["lat"] * SEMI)
        lng = round(p["lon"] * SEMI)
        alt = max(0, round((p["ele"] + 500) * 5))

        if i > 0:
            q = points[i - 1]
            dy = (p["lat"] - q["lat"]) * 111320
            dx = (p["lon"] - q["lon"]) * 111320 * math.cos(p["lat"] * math.pi / 180)
            cum_dist += (dy * dy + dx * dx) ** 0.5

        _u8(data, 4)
        _u32(data, ts)
        _s32(data, lat)
        _s32(data, lng)
        _u16(data, alt)
        _u32(data, round(cum_dist * 100))

    hdr = bytearray()
    _u8(hdr, 14)                    # header size
    _u8(hdr, 0x10)                  # protocol version 1.0
    _u16(hdr, 0x0834)               # profile version 2100
    _u32(hdr, len(data))            # data size
    hdr.extend(b".FIT")
    _u16(hdr, _fit_crc(hdr))        # header CRC

    file_crc = bytearray()
    _u16(file_crc, _fit_crc(data))  # file CRC, over the data section only

    return bytes(hdr) + bytes(data) + bytes(file_crc)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="from_file", metavar="FILE",
                     help="decode a raw ExerciseLog dump (5526464 bytes) instead of the watch")
    ap.add_argument("--gpx-out", metavar="DIR",
                     help="write one .gpx file per entry found into this directory")
    ap.add_argument("--fit-out", metavar="DIR",
                     help="write one .fit file per entry found into this directory")
    # Real, 2026-08-11 (desktop Activities page perf audit: every refresh re-decoded every
    # activity ever recorded, every time). The caller (server.py, on behalf of desktop's own
    # local cache) already knows how many activities it has from a previous run - passing
    # that count here skips decoding (not re-reading) that many, via walk_entries()'s own
    # skip_count. 0 (default) behaves exactly as before: everything decoded, same as any
    # other direct/manual invocation of this tool.
    ap.add_argument("--known-count", type=int, default=0, metavar="N",
                     help="skip decoding the N oldest entries (already cached by the caller)")
    # Opt-in WRITE, off by default (the only thing that makes this tool not purely
    # read-only). Reuses the already-open Link and already-decoded headers to mark each
    # newly-read move synced on the watch (command 0x1201) so the Suunto app / SuuntoLink
    # don't duplicate it - the experimental Settings toggle. See tools/mark_synced.py and
    # the ambit-app-activity-sync-no-delete finding. Only marks the entries decoded THIS
    # run (with --known-count, that's just the new ones, so each move is marked once, the
    # call that first reads it), and never for --from FILE (nothing to write to).
    ap.add_argument("--mark-synced", action="store_true",
                     help="also tell the watch each newly-read move is synced (0x1201 write)")
    args = ap.parse_args()

    link = None
    if args.from_file:
        with open(args.from_file, "rb") as f:
            data = f.read()
    else:
        from write_nav import Link, read_flash, read_memory_map
        link = Link(dry_run=False, verbose=False)
        if args.mark_synced:
            print("reading flash (0x0b17); --mark-synced will then write the synced flag "
                  "(0x1201) for each new move")
        else:
            print("read-only: 0x0b17 reads flash, nothing is written")
        link.open()
        # Resolve the ExerciseLog region from the watch's own 0x0b21 memory map rather than
        # trusting the hardcoded Ambit3-Peak address - the same per-device discipline the nav
        # regions already use. Real bug, 2026-08-15: the Traverse keeps its ExerciseLog at
        # 0x2b7cd0, not the Peak's 0x27ac40, so the hardcoded read hit unmapped flash and
        # crashed ("0x0b17 ...: short reply") - and every activity read 502'd on a Traverse.
        # Falls back to the reference constants for a watch that declares no such region.
        log_base, log_size = read_memory_map(link).get(
            "ExerciseLog", (EXERCISE_LOG_BASE, EXERCISE_LOG_SIZE))
        # Real fix, 2026-08-07 - this used to always read the full log region regardless of
        # how much is actually used, which is why this was slow ("blazing fast" on the real
        # Android app, minutes here for the same watch) - confirmed with real numbers, not a
        # guess: on the reference watch, the master header's own next_free_address says only
        # 46,364 bytes are real (a 119x difference). Read a small probe first to learn the
        # real boundary, then read only that much (+8KB margin - next_free_address marks where
        # new data gets appended next, not necessarily the exact byte the last entry ends at).
        if log_base == 0xFFFFFFFF or log_size == 0:
            # This watch declares no ExerciseLog region (base 0xFFFFFFFF / size 0): the Kailash
            # logs to the ephemeral DeviceLog 0x53, not a flash ExerciseLog. Nothing to read -
            # report zero activities cleanly instead of crashing on a read of address
            # 0xFFFFFFFF (real, 2026-08-16, was a 502 on the Kailash Activities page).
            print("  no ExerciseLog region on this watch - 0 activities")
            data = b""
            entries = []
        else:
            probe = read_flash(link, log_base, 1024, label="ExerciseLog (header)")
            probe_master = parse_master_header(probe)
            needed = probe_master["next_free_address"] - log_base + 8192
            needed = max(1024, min(log_size, needed))
            data = read_flash(link, log_base, needed, label="ExerciseLog")

            # Correctness net for the read-less-than-everything optimization above:
            # logical_read()'s own wraparound handling (for a log old enough to have filled
            # the whole region and wrapped at least once) assumes `data` spans the declared
            # region size - true again once this reads less than that. Not expected to ever
            # fire on a watch synced anywhere near regularly (46KB of 5.3MB - nowhere close to
            # wrapping), but "probably fine" isn't the same as bounds-checked, so this actually
            # verifies it by fully walking the entries before committing to the fast read, and
            # falls back to the real, always-correct full read rather than risk truncated data.
            try:
                entries = list(walk_entries(data, mem_start=log_base, mem_size=log_size,
                                            skip_count=args.known_count))
            except (IndexError, struct.error) as exc:
                print(f"  fast read parsed incompletely ({exc}) - falling back to a full "
                      f"region read")
                data = read_flash(link, log_base, log_size, label="ExerciseLog")
                entries = list(walk_entries(data, mem_start=log_base, mem_size=log_size,
                                            skip_count=args.known_count))
    if args.from_file:
        entries = list(walk_entries(data, skip_count=args.known_count))

    master = (parse_master_header(data) if data
              else {"entries": 0, "first_entry": 0, "last_entry": 0, "next_free_address": 0})
    print(f"master index: entries={master['entries']} "
          f"first=0x{master['first_entry']:x} last=0x{master['last_entry']:x} "
          f"next_free=0x{master['next_free_address']:x}")
    if args.known_count:
        print(f"known-count={args.known_count}: skipping decode of that many oldest entries")

    count = args.known_count
    for header, samples in entries:
        count += 1
        print(f"\nentry {count}: {header['activity_name']!r} "
              f"{header['year']:04d}-{header['month']:02d}-{header['day']:02d} "
              f"{header['hour']:02d}:{header['minute']:02d}  "
              f"duration={header['duration_ms']/1000:.0f}s distance={header['distance']}m "
              f"samples={header['samples_count']} (parsed {len(samples)})")
        gps_samples = sum(1 for s in samples if s["type"] in
                           ("gps_base", "gps_small", "gps_tiny"))
        print(f"  {gps_samples} GPS-position sample(s)")
        if args.gpx_out:
            import os
            os.makedirs(args.gpx_out, exist_ok=True)
            path = os.path.join(args.gpx_out, f"move{count}.gpx")
            with open(path, "w") as f:
                f.write(to_gpx(header, samples))
            print(f"  wrote {path}")
        if args.fit_out:
            import os
            os.makedirs(args.fit_out, exist_ok=True)
            path = os.path.join(args.fit_out, f"move{count}.fit")
            try:
                fit_bytes = to_fit(header, samples)
            except ValueError as exc:
                # to_fit() deliberately requires at least one GPS point (real, not a bug -
                # a GPS-less entry has no track to build FIT records from). A genuine,
                # unremarkable case (e.g. an accidental few-second start/stop indoors) used
                # to crash the whole run here, discarding every already-processed entry
                # along with it - found 2026-08-07 via the real backend/GUI, where this
                # took down the entire Activities list over one 7-second, 0-GPS-point
                # entry. One bad entry should never cost every good one.
                print(f"  skipped FIT ({exc}), GPX above still has the metadata")
                continue
            with open(path, "wb") as f:
                f.write(fit_bytes)
            print(f"  wrote {path}")

    if count == 0:
        print("\nno entries found (empty logbook)")
    elif count == args.known_count:
        print(f"\nno new entries since known-count={args.known_count}")

    # Opt-in synced write-back (experimental Settings toggle). Same open Link, same decoded
    # headers - no extra flash reads. Only the moves decoded this run, i.e. the new ones.
    if args.mark_synced and link is not None and entries:
        import mark_synced
        mark_synced.mark_usb(link, entries)

    # Real total entry count, for the caller to detect a shrunk logbook (the watch's own
    # log wrapped/reset since the caller's known-count was recorded, so old cached indices
    # no longer mean the same activity) - server.py reads this to decide whether to ask
    # again with --known-count 0 instead of trusting its cache. Written next to the GPX/FIT
    # files themselves, same directory convention, no separate --json flag needed for one
    # integer.
    if args.gpx_out:
        import json
        import os
        with open(os.path.join(args.gpx_out, "master.json"), "w") as f:
            json.dump({"total_entries": master["entries"]}, f)

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
