# Magene C406 Pro — BLE protocol findings

Reverse-engineered against André's own Magene C406 Pro (2026-09-24), hardware-confirmed
end to end. The C406 Pro has **no USB data mode** — the cable only charges it, and it mounts
nothing — so Bluetooth Low Energy is the only import path. Implemented in `tools/magene_import.py`
(scan / ride list / ride download), wrapped by the desktop backend at `/api/magene/{devices,
rides,import}`, and surfaced in the app as a bike computer (source tag `c406`) alongside the
USB/MTP Edge & Karoo import.

Device control (2026-09-25) — everything the OneLap app does except firmware upgrade — is decoded
from the OneLap 1.9.3 APK (`com.onelap.lib_ble`) and verified against a tablet HCI capture:
`tools/magene_device.py` (battery, device info, clock + time zone, altitude calibration, rider
profile, function settings), `tools/magene_route.py` (send a GPX route) and
`tools/magene_workout.py` (send a structured workout). Backend: `/api/magene/{device,profile/*,
route,workout}`. **Android** uses line-for-line TypeScript ports over a thin native BLE transport
(`android/src/services/Magene*.ts`, `MageneBleModule.kt`); `Magene.test.ts` pins them
byte-for-byte to the Python encoders.

Building blocks — the GATT UUIDs and the Pages / ride-list wire format — came from the
open-source **OpenBikeCompanion** project (see `docs/reference/credits.md`). Its own docs mark
the ride-**download** framing "work in progress" with no code behind it; that part (`CC03`
framing below) was worked out here and CRC-verified before being trusted.

## Transport

Command service: `8ce5cc01-0a4d-11e9-ab14-d663bd873d93`, with two characteristics:

| Characteristic | UUID | Role |
|---|---|---|
| CC02 | `8ce5cc02-0a4d-11e9-ab14-d663bd873d93` | command channel — write a command, replies/acks arrive as notifications |
| CC03 | `8ce5cc03-0a4d-11e9-ab14-d663bd873d93` | bulk channel — ride FIT data streams here in ~244-byte notifications |

The device advertises this service UUID (its advertised name is a bare serial like `60562-197`,
so scanning **by name fails** — filter on the service UUID instead). Also present but unused
here: a Nordic Secure DFU service (`0xFE59`) and a Nordic UART service, plus a second vendor
service `8ce5ee01-…` whose role is unknown.

Multi-byte fields are little-endian.

## Pairing (bonding) — required

The C406 sits on a **"please pair" screen** (no Bluetooth icon) and stays there until a companion
**BLE-bonds** with it. A plain connect — all that's needed to read pages or pull rides — leaves it
stuck on that screen forever. So `_connect()` bonds on connect (`bleak`'s `client.pair()` → BlueZ
`Device.Pair`, a Just-Works pairing, **no passkey**). The instant it bonds, the device drops to
its normal screen with the Bluetooth icon on (hardware-confirmed). Bonding is idempotent and the
bonded device advertises when idle and reconnects from its normal screen, so the pair call is a
no-op on every sync after the first. Removing the bond (OS Bluetooth settings / `bluetoothctl
remove`) sends the device back to the pair screen.

## Commands (CC02)

| Command | Bytes | Reply |
|---|---|---|
| Read data screens | `40 42` | `40 42 <status> <screens block>` (see below) |
| Write data screens | `40 43 <screens block>` | `40 43 <status>` (0 ok, 2 still applying) |
| Ride list | `40 49 <cursor u32>` | `40 49 <status> <count u8> <more u8> [<ride_id u32> × count]` |
| Ride download | `40 4a <ride_id u32>` | `40 4a <status>`, then the FIT streamed on CC03 |
| Read rider profile | `40 40` | `40 40 <status> <sex><age><height><maxHR><LTHR> <FTP u16> <bikeWeight×100 u16> <weight×100 u16>` |
| Write rider profile | `40 41` + the same 11-byte struct | `40 41 <status>` |
| Read function settings | `40 4c` | `40 4c <status> <block>` (see below) |
| Write function settings | `40 4d <block>` | `40 4d <status>` (0 = ok) |
| Set clock | `40 4e <unix seconds u32>` | ack |
| Set time zone | `40 4f <whole hours u8>` and `40 57 <offset seconds u32>` | ack each |
| Altitude calibration | `40 55` | ack (device re-baselines its barometer to its GPS fix) |
| Workout info (then file on CC03) | `40 88 …` | credit grants `40 8c 00 <n>` |
| Route info (then file on CC03) | `40 8d …` (54 bytes) | credit grants `40 8c 00 <n>` |
| Transfer end | `40 52` | ack |

- **Ride IDs are the ride's UTC start time as a raw Unix timestamp** (confirmed: matches the FIT's
  own `session.start_time`). Import files are named `YYYY-MM-DD-HH-MM-SS.fit` from it, the same
  convention as the MTP importer, so both share one sync-history / dedup path.
- `cursor 0` = first page; pagination (`more != 0`, next cursor = last ride id) is inferred from
  OpenBikeCompanion's docs, **not** hardware-tested (test unit only ever held a couple of rides).
- Battery and identity use the standard GATT Battery (`0x2A19`) and Device Information
  (`0x180A`) characteristics, not Magene commands.
- Route and workout file formats (protobuf, sint32 zigzag coordinates, the A5 5A 5A A5 packet
  framing, FIT CRC-16, the app-exact TSS) are documented in the docstrings of
  `tools/magene_route.py` and `tools/magene_workout.py`.

## Function settings block (`40 4c` / `40 4d`)

Hardware-confirmed 2026-09-25 (24-byte block on the C406 Pro; write acked with status 0). The app reads the block, patches single fields in place and writes the **whole** block back (same
length). The layout depends on the reply length — the app branches the same way
(`DecodeProFuncProduct`); the C406 Pro replies 24 bytes. Offsets are into the full reply
(block offset = offset − 3):

| Offset | Size | Field | Values (what the OneLap UI offers) |
|---|---|---|---|
| 3 | u32 | time-zone offset, seconds | read-only here — set by `40 4f` / `40 57` on connect |
| 7 | u8 | auto backlight | 0/1 |
| 8 | u16 | backlight duration | 0 = always on, 5/10/15/30/60 s |
| 10 | u8 | backlight level | 0 low, 1 medium, 2 high |
| 11 | u8 | auto power-off | 0 = off, 5/10/15/20/30/40/60 min |
| 12 | u8 | auto pause | 0 = off, else km/h threshold |
| 13 | u8 | prompt tone | 0/1 |
| 14 | u8 | key tone | 0/1 |
| 15 | u8 | start-ride reminder | 0/1 |
| 16 | u8 | estimated power | 0/1 |
| 17 | u8 | heart-rate alert | 0 = off, 100–240 bpm |
| 18 | u16 | power alert | 0 = off, 100–2500 W |
| 20 | u8 | auto lap | 0/1 (24-byte block only) |
| 21 | u8 | auto-lap type | 0 distance, 1 time |
| 22 | u16 | auto-lap value | distance: km × 10; time: minutes |

A 15-byte block (older unit, one-byte time zone, a key-function byte) is decoded differently and
is **refused for writing** here. Both implementations check every name, value and offset
before touching a byte.

## Data screens block (`40 42` / `40 43`)

Decoded from the OneLap APK (`ProDecodePageStrategy`; field names from the app's own English
string arrays) — `tools/magene_pages.py` holds the format, the 100-field catalogue and the limits:

```
<pageCount u8> <totalLen u8>  then pageCount × ( <n u8> <n field codes> )
```

A field code is one byte: high nibble = group (0x1 speed, 0x2 cadence, 0x3 heart rate, 0x4–0x6
power, 0x7 distance, 0x8 slope, 0x9 elevation, 0xA gain/loss, 0xB time, 0xC electronic shifting,
0xF calories/others), low nibble = variant; `0xFF` = empty slot. The app allows 2–8 fields per
screen and up to 30 screens. Writes here are refused unless the device's current layout decodes
in this format, and count as done only when the device reads back exactly what was sent.
Hardware-confirmed 2026-09-25: the C406 Pro's `40 42` reply is exactly this block (the OneLap app
never reads it back, so this was checked on the device), and a `40 43` write reads back
byte-for-byte. The older fixed 7-byte-per-page reading in OpenBikeCompanion's notes is for other models.

## CC03 ride-download framing (found here, CRC-verified)

Each CC03 notification during a download is:

```
bytes 0:4   ride_id (echoes the request; constant across every chunk of one download)
bytes 4:8   FIT bytes remaining AFTER this chunk (u32 LE; 0 on the last chunk)
bytes 8:10  constant 0x0004 (purpose unknown; always this value in testing)
bytes 10:12 sequence number (u16 LE, 1-based)
byte  12    a per-chunk marker byte — NOT part of the FIT stream
bytes 13:   FIT fragment
```

Concatenate `bytes[13:]` of every chunk in sequence order and you get the FIT file **exactly** —
verified both ways: the 12-byte FIT header CRC-16 and the whole-file CRC-16 match, using the FIT
spec's CRC. The download is complete when a chunk reports `remaining == 0`.

## Ride types → sport

The C406's ride profiles differ only in FIT **`sub_sport`** (session field 6), **not** `sport`
(both are `sport = 2`, cycling) — hardware-confirmed on real rides of each type:

| Profile | sport | sub_sport | Decoded as | Notes |
|---|---|---|---|---|
| Outdoor | 2 (cycling) | 7 (road) | Cycling | has a GPS track |
| Training | 2 (cycling) | 6 (indoor_cycling) | Indoor Cycling | no GPS track, distance 0 |

`tools/fit_decode.py` reads `sub_sport` and maps the indoor cases (and treadmill for running);
reading `sport` alone had labelled both profiles "Cycling". The device's own FIT is stored and
uploaded to intervals.icu verbatim, so intervals reads the same `sport`/`sub_sport` and its type
matches the app's library by construction.

## Hardware (for reference, not used)

Per the third-party **WSTRN/C406pro_Hack** teardown (see credits): Nordic **nRF52840** BLE SoC,
Sitronix ST75256 128×160 mono LCD, Unicore UC6226 multi-GNSS, Goertek SPL06 baro, GigaDevice
GD25Q256 32 MB flash. This project does not touch the firmware (the OneLap firmware upgrade is
deliberately not implemented).
