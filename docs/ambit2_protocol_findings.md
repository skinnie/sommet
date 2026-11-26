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

SuuntoLink writes the region's first 10240 bytes (0x2000–0x47FF) on every save, all 159 times
in this capture: 10 x 1024-byte 0x0b16 chunks (the Ambit1 capture: 20 x 512), then the 0x0b18
tail `[0x2000][0xFFFFFFFF]`. One save is up to three such writes (the mode's settings, its
displays, its group), and each also puts a Unix time in the mode's `0x01FF` record (two u32:
settings, displays) or the group's `0x02FF`. A first edit of a mode adds those records.

Settings edits, each seen alone in this capture (blob offsets, 90-byte layout): pods 22 (the HR
belt switches bits 0+1 together: 0x0003 <-> 0x0000), autolap 30, HR limits 32/34/36 (off writes
0/0/0), plus GPS 26, recording 28, alti-baro 24, auto pause 40, auto scroll 42, backlight 84,
display mode 86, quick navigation 88. A rename writes the 16-byte name and the mode's group name
(`0x0210` > `0x0212`, 24 bytes; the group whose one `0x0214` is the mode's place in `0x0100`).

Sommet writes the name, autolap, HR limits and pods this way on both apps
(`tools/legacy_sport_modes.py patch_region`, `AmbitLegacySportModes.ts`), leaving the time
records as read; `tools/test_legacy_sport_mode_edit.py` replays the captured writes.

### 3a. Creating and deleting a sport mode (2026-11-26, watch-protocol/10 L3)

SuuntoLink's own code (4.1.15 `ui/sport_mode_editor.js`, `ambit/sport_mode.js`, `ui/input.js`)
and its writes agree. A create is `defaultSettings(new id)` with the chosen sport's
`getActivityDefaults` row put in the form (pods, alti-baro, GPS and recording interval), saved
as three region writes on the Ambit2 (settings, displays, menu entry) and two on the Ambit1
(`Variant === AMBIT` skips the menu entry). Captured four times: Ambit1 Yoga, Adventure Racing
and Alpine Skiing (`zzambit1full.pcap`, writes 0-6), Ambit2 Adventure Racing (this capture,
writes 2-5). The new mode is appended to `0x0100`:

    0x0101 { 0x0102 settings, 0x01FF [u32 settings time][u32 display time],
             0x0105 the same seven displays every time (a 3-row Top 10, Center 11, Bottom [5],
                    then the built-in views 4, 5, 6, 65, 7, 50) }

The settings record is zero apart from the name, the sport, those four defaults, the interval
timer's 99 repetitions and, on the Ambit2, the mode id at 18 (`getNextModeId`, the lowest free
id: 1, the factory ones being 0x2E1B...) and 0x00FF (null) backlight and display at 84 and 86.
The pods field is the belt (bit 0), the pods, 0x0002 with any of them, and 0x0004 for the eight
`getFusedSpeedActivities` sports (SuuntoLink's own reader calls bit 2 UseAccelerometer). On the
Ambit2 the menu entry is appended to `0x0200`: `0x0212` name (24), `0x0213` sport, `0x0214` the
new place, `0x02FE` the lowest free entry id (9 here: 1-8 and 10 taken), `0x02FF` the time.

SuuntoLink deletes a menu entry (`deleteSportModeGroup`) and refuses the last one, a mode with no
entry of its own (the factory Transition) and a mode a multisport uses. Deleting a multisport also
drops the modes only it used. That is write 0->1 here: it is the Triathlon entry that was
deleted (the editor offers no delete for the Transition), and the Transition went with it.
Write 1->2 is then a new multisport made in the editor (`showCreateMultisportMode`): legs Cycling,
Running, Openwater swim in the order picked, entry id 1 (the lowest free again) and a `0x02FF`
time the old entry did not have. The Ambit3 capture of the same program
(`removeandaddsportsmodeandmultisport`, saves 3->4) shows the same delete with the dropped mode
in the middle of the list: the order is kept and every leg above it moves down by one.

Sommet writes create and delete this way on both apps (`tools/legacy_sport_modes.py
create_mode / delete_mode / delete_multisport`, `AmbitLegacySportModes.ts`), each in one region
write; `tools/test_legacy_sport_mode_manage.py` replays the four creates and the Triathlon
delete byte for byte, and checks that deleting a created mode gives back the write before it.
The limit is SuuntoLink's `countSportModes`: max(single entries + modes only a multisport uses,
all entries), 10 on all four models.

SuuntoLink's create list (`ui/input.js movescountActivity`) has no per-model filter and holds the
three multisport sports too (Multisport 2, Triathlon 19, Adventure racing 61): the Adventure Racing
modes above are ordinary modes. Both apps offer that list on this family (2026-11-26).

### 3b. Creating a multisport (2026-11-26, watch-protocol/10 L2)

Write 1 -> 2 is SuuntoLink's `showCreateMultisportMode`: one `addSportModeGroup(getNextGroupId,
{ActivityID, Name, CustomModeIDs})` and nothing else in the region changes. The entry is appended
to `0x0200`:

    0x0210 { 0x0212 name (24), 0x0213 sport (19), 0x0214 1, 0x0214 0, 0x0214 4 (Cycling, Running,
             Openwater swim: places in 0x0100, in the order picked), 0x02FE 1 (the lowest free
             id), 0x02FF the save time }

and the `0x0200` and root lengths grow by its 72 bytes. The editor's rules: a name of up to 16
characters (`getMaxNameLength`), one of the three multisport sports (`getMultisportPhrases`), 2 to 6
legs (`MAX_MULTISPORT_SPORT_MODES`, repeats allowed) from the modes with a menu entry of their own
and the modes only a multisport uses (its `hiddenModes`, the factory Transition), at most 2
multisports (`MAX_MULTISPORT_MODES`) and the 10 sport modes, and only where the capability record
has `supportsMultisportModes` (Ambit2, Ambit2 S). The Triathlon is never changed in the other 157
writes, so no capture shows an edit. Sommet writes the create this way on both apps
(`tools/legacy_sport_modes.py create_multisport`, `AmbitLegacySportModes.ts
createLegacyMultisportRegion`); `tools/test_legacy_sport_mode_manage.py` replays write 1 -> 2 byte
for byte and deletes it back to write 1.

## 4. Displays and the sport-mode menu (2026-11-26, watch-protocol/10 L5, L2)

The display list (`0x0105`) is the same record format as the Ambit3's, byte-identical on the
Ambit1 and the Ambit2 (every display in both captures, 20,000+ records, has this shape):

    0x0106 { 0x0107 [u16 layout][u16 type],
             one 0x0108 per row: { 0x0109 [u16 row][u16 item], 0x010A [u16 value]... } }

Layouts 260 = 3 rows, 261 = 2, 262 = 1, 257 = graph. `type` 10 is a screen the wearer set up;
4, 5, 6, 7, 50, 65 and 70 are the watch's own views (compass, navigation, map...), and they
always come after the user screens. A row whose item is 0 and which holds `0x010A` values is
the multi-value row; any other row shows its item. openambit's serialize_display writes the
same, with `0x000a` as the type of every display it writes.

What SuuntoLink changed, each alone between two region writes (each also moves the display
time, the second u32 of the mode's `0x01FF`):

| change | Ambit2 (Treino Forção) | Ambit1 (Running) |
|---|---|---|
| add a display (after the last user one) | 1-row, 2-row, 3-row, graph | graph, 3-row, then 2-row + 1-row + graph in one save |
| remove a display | a graph | - |
| change a display's layout (in place) | - | 3-row -> 1-row, 3-row -> 2-row |
| Top / Center value | 70 -> 11, 21 -> 33 | 5 -> 68, 28 -> 10 |
| multi-value row | 3 -> 5 values | 4 -> 5 values |

A new display (added or retyped) always has the same rows: 3-row Top 10, Center 11, Bottom
[5]; 2-row Top 10, Center [5]; 1-row Top 10; graph 6, 32, 5. Every enclosing length (`0x0105`,
the mode's `0x0101`, `0x0100`, the root) moves by the size change, what follows shifts, and the
rest of the 10240 bytes is 0xFF. Sommet writes these changes on both apps
(`tools/legacy_sport_modes.py patch_displays`, `AmbitLegacySportModes.ts
patchLegacyDisplayRegion`; the Android screen offers the row change),
`tools/test_legacy_display_edit.py` replays every one of them.

The `0x0200` groups are the watch's sport-mode menu: `0x0212` name (24 bytes), `0x0213`
activity, one `0x0214` per leg (a place in the `0x0100` list), `0x02FE` a u32 that numbers the
entries, `0x02FF` a save time. A group with several legs is a multisport: the capture's
Triathlon (activity 19). Both apps list them read only. The multisport changes in the
capture are a delete of the Triathlon (with the Transition only it used) and a new Triathlon
made right after, with legs Cycling, Running, Openwater swim (it had Openwater swim, Transition,
Cycling, Transition, Running); see sections 3a and 3b. Both apps create and delete a multisport
this way; neither edits one (no capture).

