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

Read these flags carefully: they name **fields**, not features. `usehrlimits` is the separate
`use_heartrate_limits` *toggle field*, not HR limits as a capability — the Ambit1 supports
sports-specific HR limits (manual §6.3, and confirmed in the pcap), it just has nowhere to
store the enabled/disabled flag. See §6a.

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
| 28 | 2 | `heartrate_max` | **observed = 239** |
| 30 | 2 | `heartrate_min` | **observed = 30** |
| 32 | 2 | `unknown2` | never observed non-zero |
| 34 | 2 | `auto_pause` | **observed = 56** — speed ×100, matching openambit's `autoPauseSpeed * 100` |
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
Only `unknown2` (32). Every other field in the table is observed in real traffic.

**Correction:** an earlier revision claimed `heartrate_max`/`heartrate_min` were "unpinnable on
an Ambit1 by definition" because the device lacks the `usehrlimits` capability. That was wrong,
and the Suunto Ambit manual §6.3 says so plainly — a custom mode can carry "sports-specific
heart rate limits". The pcap agrees: SuuntoLink really does write `hr_max = 239`, `hr_min = 30`
to this watch. See §6a for what the missing capability flag actually means.

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

## 6a. What the missing `usehrlimits` capability actually means

The Ambit1 **does** support sports-specific HR limits. The Suunto Ambit manual §6.3 states a
custom mode lets you customise "sports-specific heart rate limits, autolap distance, or the
recording rate", and the pcap confirms SuuntoLink writing `hr_max = 239` / `hr_min = 30` to
this watch.

What the Ambit1 lacks is the separate **`use_heartrate_limits` toggle field** — present at
offset 36 in the 90-byte struct, absent from the 76-byte one. The limit *values* exist; the
stored *enabled/disabled flag* does not.

That is consistent with the observed behaviour rather than contradicting it. Across successive
saves to `Running` the limits oscillate `239/30 -> 0/0 -> 239/30 -> 0/0`. **Most likely
mechanism** (inference, not proof): with nowhere to persist "limits are on", SuuntoLink cannot
round-trip the checkbox state, so it reloads unchecked and the next save writes zeros — which
is exactly the "HR limits don't stick" symptom André reported. On the original Movescount this
presumably never surfaced, because the cloud held the enabled flag host-side.

Stated as a hypothesis deliberately: two earlier readings of this same byte trajectory (the
interval timer) were called bugs and both turned out to be the user deliberately changing
settings. The bytes alone cannot distinguish "SuuntoLink reverted it" from "the user unchecked
it".

## 6b. One app per sport mode

André, from using the device: **on the Ambit1 each sport mode can hold only one app**, though
different sport modes can each have a different app. (Ambit3 allows 5 per mode — Finding 11.)

`Devices.xml` is consistent with this: the Ambit1's `<custommodeconfig>` declares
`rulestoresize` **20000** and `rulestorelocation` 160000, against **200000** / 600000 for the
Ambit3 family — a 10x smaller rule store.


## 7. Text encoding: ISO-8859 on Ambit1, UTF-8 on Ambit3 — per DEVICE

Settled 2026-08-23 from `ambit1languages.pcap`. SuuntoLink wrote the Portuguese mode name
**"Corrida de Acção"**, and on the wire it is:

```
43 6f 72 72 69 64 61 20 64 65 20 41 63 e7 e3 6f      "Corrida de Ac ç ã o"
                                        ^^ ^^
```

One byte per accent → **ISO-8859-1/15**. UTF-8 would require `c3 a7` / `c3 a3`.

The Ambit3 family is genuinely UTF-8, proven separately on a French Ambit3 Sport whose activity
name `Entraîn. salle` was mojibake until decoded as UTF-8. So this is **per-device**, matching
Suunto's own `supportsUtf8Encoding` capability — do **not** unify the two.

Consequence for this project: `pmem20.c`'s log-header name decode is hardcoded to `"UTF-8"` on
a path shared by both families. That is correct for the Ambit3 and **wrong for the Ambit1**;
making it device-dependent is an open TODO. `ambit1_sport_mode.c` handles its own names
correctly (transcoded to `\uXXXX`, so JSON output stays pure ASCII and cannot be mis-decoded).

Two follow-ups, one now closed:

- **How the Portuguese name got there: RESOLVED — André typed it manually**, with SuuntoLink's
  own UI in English. So sport-mode names are **never** auto-translated: not by the watch
  language, not by SuuntoLink's UI language. They are plain user strings that whatever
  configured the watch last wrote verbatim. (A watch that ships with localised names got them
  from its factory/first-run setup in that language, which is why André's French units had
  French modes and nothing he does now re-triggers it.)

  This makes the encoding result *more* load-bearing, not less: since accented names reach the
  watch by a user typing them, any app that lets users name sport modes must get Ambit1
  encoding right or it will corrupt them.

  Note what it proves precisely: **SuuntoLink chose single-byte ISO-8859 when writing to this
  device**, which is exactly the `supportsUtf8Encoding` per-device switch. Strictly, confirming
  the *watch* also decodes it that way needs one look at the watch screen showing
  "Corrida de Acção" rendered correctly — not yet done, and cheap if the watch is still around.

- **Whether the Ambit3 uses the same sport-mode container** is still unknown here.
  `ambit3language.pcap` contains **zero** sport-mode writes — SuuntoLink errored out on that
  language and never wrote any, matching what André observed on screen.

## 6. Writing

Not attempted on hardware. Two things must hold:

1. the payload must use the **76-byte** settings blob above, **not** libambit's
   `sport_mode_write` (which serializes the 90-byte struct and would land every field past
   offset 18 in the wrong place on an Ambit1);
2. the region write itself is `libambit_pmem20_data_write` at 0x2000 — the pcap shows
   SuuntoLink writing exactly that way, in 512-byte 0x0b16 chunks starting at 0x2000.

A byte-exact restore of `Bluebird-1614984607001600-20260823-111839.bin` is the safest possible
first write test, and the saved dump makes any experiment reversible.
