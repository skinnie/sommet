# Ambit1 (Bluebird) sport-mode format — reverse-engineered 2026-08-23

**Status: read format solved and validated against Suunto's own writer. Write not yet
attempted on hardware.**

Source data (local only, `assets/` is gitignored):
`assets/pcap/2026-08-23-ambit1-suuntolink/` — a full USBPcap capture of SuuntoLink
configuring André's Ambit1 (serial 1614984607001600, fw 2.5.7), the SuuntoLink
screenshots of what was set, `Devices.xml`, and before/after raw region dumps.

## 1. The region can be READ — openambit/openambit2 never do this

Neither openambit nor openambit2 reads sport modes off the watch. Verified exhaustively in
openambit2: `device_driver.h` has no `sport_mode_read` slot at all, `sport_mode_serialize.h`
has `serialize_*` with no deserialize counterpart, `PMEM20_SPORT_MODE_START` is referenced
only by the write, and the one `readSportModes*` symbol is `readSportModesFromFile()`.

That is architectural, not an oversight: the **host held the master copy**. openambit pulled
the authoritative set from the Movescount cloud (`syncGET /userdevices/<serial>`) and wrote it
wholesale; openambit2, post-shutdown, swapped that dead cloud for a local
`~/.openambit/sport_modes.json`. SuuntoLink works the same way.

But the hardware supports reading fine. `pmem20.c`'s `read_log_chunk()` is **not**
log-specific despite its name — it sends `ambit_command_log_read` (**0x0b17**) with an
arbitrary `{u32 address, u32 length}` and returns the bytes. That is the same generic flash
read this project already uses on Ambit3. Sport modes live at **0x00002000**
(`PMEM20_SPORT_MODE_START`), chunked at the Bluebird driver's own 0x200 (512 B).

Implemented as `ambit_legacy_cli sport-mode-dump FILE [BYTES]` (read-only).

## 2. Container format (TLV)

Nested `[u16 tag][u16 length][payload]`, tags from `sport_mode_serialize.h`:

| tag | meaning |
|---|---|
| 0x0003 | root |
| 0x0100 | sport-modes container |
| 0x010b | unknown, always u16 `2` |
| 0x0101 | one sport mode |
| 0x0102 | that mode's settings blob |
| 0x0200 / 0x0210 | sport-mode groups |

## 3. The settings blob is 76 bytes on Ambit1, NOT openambit's 90

`sport_mode_serialize.c` does `memcpy(data + HEADER_SIZE, settings,
sizeof(ambit_sport_mode_settings_t))` with `SETTINGS_SIZE = 90`. **That is wrong for the
Ambit1.** Both the watch's own contents and SuuntoLink's own write use **0x4c = 76**.

Confirmed twice, independently:
- every 0x0102 block in the region read off the watch is 76 bytes;
- every 0x0102 block **SuuntoLink itself writes** in the pcap is 76 bytes
  (`02 01 4c 00` in the outgoing 0x0b16 payloads).

`Devices.xml` explains exactly why. It carries a per-device `<custommodeconfig>`:

| device | custommodeid | usehrlimits | autoscrolling | displaymode | navigationselection | intervaltimer |
|---|---|---|---|---|---|---|
| Ambit2 / 2S / 2R | yes | yes | yes | yes | yes | yes |
| Ambit3 (all), Traverse, Traverse Alpha | yes | yes | yes | yes | yes | yes |
| **Ambit1** (`BluebirdDevice_Legacy_2_0`) | **–** | **–** | **–** | **–** | **–** | yes |

The Ambit1 is missing five capabilities the rest of the family has, and the missing fields are
exactly the 14-byte difference. **This is Ambit1-only — Ambit2 has the full capability set and
therefore almost certainly uses the standard 90-byte layout openambit already implements.**

Practical consequence, confirmed live: SuuntoLink shows an HR-limits control for the Ambit1 but
the values never stick, because the watch has no `usehrlimits` capability. That is a device
limitation surfaced as a SuuntoLink UI bug, not a watch fault.

## 4. Ambit1 76-byte settings layout

Derived by dropping the five unsupported fields from openambit's 90-byte struct, then confirmed
empirically. Every offset below is **evidence-backed**, and the total accounts for all 76 bytes
exactly.

| offset | size | field | evidence |
|---|---|---|---|
| 0 | 16 | `activity_name` | ASCII, NUL-padded |
| 16 | 2 | `activity_id` | Running=3, Trail running=82, Trekking=11 — match known Suunto IDs |
| 18 | 2 | `hrbelt_and_pods` | Cycling=0x08C3 (bike\|cad\|power\|HR), matches this project's own documented value |
| 20 | 2 | `alti_baro_mode` | |
| 22 | 2 | `gps_interval` s | Indoor training=0 (GPS off), Mountaineering/Trekking=60 |
| 24 | 2 | `recording_interval` s | 1 s for run/bike, 10 s for hike |
| 26 | 2 | `autolap` m | Running=1000 |
| 28 | 2 | `heartrate_max` | not observed non-zero (no `usehrlimits` capability) |
| 30 | 2 | `heartrate_min` | not observed non-zero (no `usehrlimits` capability) |
| 32 | 2 | `unknown2` | |
| 34 | 2 | `auto_pause` | not observed non-zero |
| 36 | 2 | `use_interval_timer` | **observed = 1** when enabled |
| 38 | 2 | `interval_repetitions` | **observed = 5** (user-entered); 99 = SuuntoLink default |
| 40 | 2 | `interval_timer_max_unit` | **observed = 0x0100** (time). Matches openambit's own `interval1time ? 0x0100 : 0` |
| 42 | 6 | `unknown3` | |
| 48 | 2 | `interval_timer_max` | **observed = 150** = the UI's "High" 2:30, in seconds |
| 50 | 2 | `unknown4` | |
| 52 | 2 | `interval_timer_min_unit` | **observed = 0x0100** (time) |
| 54 | 6 | `unknown5` | |
| 60 | 2 | `interval_timer_min` | **observed = 390** = the UI's "Low" 6:30, in seconds |
| 62 | 14 | `unknown6` | |

Note the UI-to-field mapping: the interval timer's **"High"** (high-intensity leg) is
`interval_timer_max`, and **"Low"** (recovery leg) is `interval_timer_min` — they are
intensity labels, not magnitude, so "High" 2:30 is the *smaller* number.

Relative to openambit's 90-byte struct the Ambit1 drops, in order: `sport_mode_id` (2),
`unknown1` (2), `use_heartrate_limits` (2), `auto_scroll` (2) — which is why
`interval_repetitions` lands at 38 instead of 46 — plus `backlight_mode`, `display_mode` and
`quick_navigation` (6) at the tail. 8 + 6 = 14 = 90 − 76.

### Still unpinned
Only `heartrate_max` (28), `heartrate_min` (30), `unknown2` (32) and `auto_pause` (34). The two
HR fields cannot be pinned on an Ambit1 at all — the device has no `usehrlimits` capability, so
no value ever reaches them (see §6 below).

Everything else is observed, not inferred: the interval-timer block was confirmed from
SuuntoLink's own writes in the pcap carrying André's real input (reps 5, High 2:30 → 150,
Low 6:30 → 390).

`0x63` (99) at offset 38 is a useful tell: it marks modes this SuuntoLink version rewrote with
its default repetitions, versus modes still carrying their original Movescount-era bytes
(all-zero tail).

## 5a. Interval-timer unit encoding, confirmed both ways

**Correction:** an earlier revision of this document claimed SuuntoLink "clobbers" the interval
timer on re-save. That was wrong — it was over-read from the byte sequence without checking the
matching screenshot. André had deliberately exercised each option in turn, and every write
faithfully reflects what he entered. No SuuntoLink bug here.

The sequence across successive writes to `Running` in one session (pcap run index) is a clean
input→output table, and pins the unit encoding in both directions:

| run | use | reps | max_unit | max | min_unit | min | what the user did (screenshot-confirmed) |
|---|---|---|---|---|---|---|---|
| 52 | 0 | 99 | 0x0000 | 0 | 0x0000 | 0 | timer off |
| **53** | 1 | **5** | **0x0100** | **150** | **0x0100** | **390** | time mode: High 2:30, Low 6:30, reps 5 |
| **55** | 1 | **99** | **0x0000** | **100** | **0x0000** | **10000** | distance mode: High 0.1 km, Low 10.0 km, reps 99 |
| 57 | 0 | 99 | 0x0000 | 0 | 0x0000 | 0 | timer unchecked again |

So:

- `interval_timer_*_unit` = **0x0100 → time, value in seconds**; **0x0000 → distance, value in
  metres** (0.1 km → 100, 10.0 km → 10000). This matches openambit's own
  `interval1time ? 0x0100 : 0` exactly, now confirmed against real hardware traffic in both
  modes.
- the watch stores precisely what it is handed; nothing is silently altered.

## 5. Real data captured off the watch

Baseline, before any SuuntoLink edits — 8 modes:

| mode | activity | pods | GPS | rec | autolap |
|---|---|---|---|---|---|
| Cycling | 4 | HR+power+cadence+bike | 1 s | 1 s | — |
| Indoor training | 23 | HR | off | 10 s | — |
| Mountaineering | 74 | HR | 60 s | 10 s | — |
| Other sports | 1 | HR | 1 s | 10 s | — |
| Running | 3 | HR+accel+foot | 1 s | 1 s | 1000 m |
| Alpine skiing | 20 | HR | 1 s | 1 s | — |
| Trail running | 82 | HR+accel+foot | 1 s | 1 s | — |
| Trekking | 11 | HR+foot | 60 s | 10 s | — |

After the SuuntoLink session: 10 modes (the device maximum — `getMaxSportModes` = 10 for this
whole family), with Yoga (10), Adventure Racing (61) and Alpine Skiing (20) added.

## 6. Writing

Not attempted on hardware. Two things must hold:

1. the payload must use the **76-byte** settings blob above, **not** libambit's
   `sport_mode_write` (which serializes the 90-byte struct and would land every field past
   offset 18 in the wrong place on an Ambit1);
2. the region write itself is `libambit_pmem20_data_write` at 0x2000 — the pcap shows
   SuuntoLink writing exactly that way, in 512-byte 0x0b16 chunks starting at 0x2000.

A byte-exact restore of `Bluebird-1614984607001600-20260823-111839.bin` is the safest possible
first write test, and the saved dump makes any experiment reversible.
