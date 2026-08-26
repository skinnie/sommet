# Ambit2 (Duck) protocol findings — from a real SuuntoLink USB capture (2026-08-26)

Source: `assets/pcap/ambit2_suuntolink_settings_sportmodes.pcap` (Windows USBPcap, André's
own Ambit2, serial 6FB2095111001800, fw 2.0.16). Decoded with `tools/ambit_pcap.py`.
Compared against the Ambit1 (`docs/ambit1_sport_mode_format.md`) and Ambit3 (SBEM, 0x1100).

The Ambit1/2 family speaks the legacy PMEM 2.0 command set (0x0b00/0x0b01 settings,
0x0b16 region write, 0x0b17 region read, 0x0b05 waypoint), NOT the Ambit3's SBEM/0x1100.
Command histogram in this capture: 0x0b00 read x106, 0x0b01 write x25, 0x0b16 write x1605,
0x0b17 read x16717, 0x0b05 waypoint_write x33, plus 0x0b06/07/08/0a/0b (legacy, seen but
not yet decoded).

## 1. Personal settings write (0x0b01) — the previously-uncaptured format

- **The struct is 188 bytes on the Ambit2** (every 0x0b00 read reply and every 0x0b01
  write in the capture is 188 B), NOT the Ambit1's 132. This is exactly why
  `ambit_legacy_cli`'s `settings-write` (hardcoded `A1_SETTINGS_BLOB = 132`,
  `if product_id != 0x0010 refuse`) must refuse the Ambit2: writing 132 back would truncate
  the 188-byte struct.
- **Field offsets are the SAME as the Ambit1** for every confirmed field (proven by diffing
  the 25 consecutive 0x0b01 writes as SuuntoLink changed one setting at a time):

  | offset | size | field | evidence (diff between consecutive writes) |
  |---|---|---|---|
  | 8 | 1 | units_mode | 01→02 |
  | 9–18 | 1 each | units.{pressure,altitude,distance,height,temperature,verticalspeed,weight,compass,heartrate,speed} | flip together on metric↔imperial |
  | 25, 36 | 1 | extra unit-related fields (present in 188, absent from the CLI's table) | flip with unit changes |
  | 48 | 2 | weight (x100 kg) | 4c1d→b20b→234e |
  | 50 | 2 | birthyear | b5→6c→ea |
  | 52 | 1 | max_hr | b4→64→f0 |
  | 53 | 1 | rest_hr | 3c→1e→64 |
  | 54 | 1 | fitness_level | 32→0a→64 |
  | 55 | 1 | is_male | 01→00 |
  | 56 | 1 | length (height cm) | b4→d3 |

- **Fix to enable Ambit2 settings write** (needs a C recompile — held for approval): in
  `ambit_legacy_cli.c cmd_settings_write`, (a) use the actual `replylen` from the 0x0b00
  read as the blob/write length instead of the hardcoded 132, and (b) allow the Bluebird
  family (0x0010 Ambit1 + 0x0019 Ambit2, plus 2S/2R) not just 0x0010. Field offsets ≤56 are
  identical and safe; RMW preserves every untouched byte, including the extra 188-byte tail.

## 2. Sport-mode `hrbelt_and_pods` bitfield (offset 22 in the 90-byte 0x0102 blob)

openambit stores this as an opaque "bit pattern"; decoded here from the capture (one mode,
"Treino Forção", toggled one accessory at a time, base HR = 0x0003):

| accessory | bit(s) | mask |
|---|---|---|
| HR belt | 0,1 | 0x0003 |
| Power POD | 6 | 0x0040 |
| Cadence POD | 7 | 0x0080 |
| Foot POD | 8 | 0x0100 |
| Bike POD | 11 | 0x0800 |

Cross-checked against the factory **Cycling** mode `0x08c3` = HR + Bike + Power + Cadence
(no foot pod) — exactly right. `tools/legacy_sport_modes.py` now decodes these. bit 2
(0x04) appears set on Running (0x0107) but not Trekking (0x0103) — an extra foot-related
flag, not one of the five UI checkboxes; left undecoded.

## 3. Sport-mode WRITE

SuuntoLink writes the whole sport-mode region (0x2000–~0x4800) via 0x0b16 data_write in
0x200-byte chunks (same mechanism openambit's sport-mode write already uses). Setting pods
correctly on write = encode `hrbelt_and_pods` from the table above. Read side is done
(`tools/legacy_sport_modes.py`); a pod-aware write is a small follow-up on the existing
0x0b16 path.
