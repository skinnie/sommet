# Magene C406 Pro — BLE protocol findings

Reverse-engineered against André's own Magene C406 Pro (2026-09-24), hardware-confirmed
end to end. The C406 Pro has **no USB data mode** — the cable only charges it, and it mounts
nothing — so Bluetooth Low Energy is the only import path. Implemented in `tools/magene_import.py`
(scan / ride list / ride download), wrapped by the desktop backend at `/api/magene/{devices,
rides,import}`, and surfaced in the app as a bike computer (source tag `c406`) alongside the
USB/MTP Edge & Karoo import.

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
| Read pages (display config) | `40 42` | `40 42 00 <page_count> <7 bytes × page>` |
| Ride list | `40 49 <cursor u32>` | `40 49 <status> <count u8> <more u8> [<ride_id u32> × count]` |
| Ride download | `40 4a <ride_id u32>` | `40 4a <status>`, then the FIT streamed on CC03 |

- **Ride IDs are the ride's UTC start time as a raw Unix timestamp** (confirmed: matches the FIT's
  own `session.start_time`). Import files are named `YYYY-MM-DD-HH-MM-SS.fit` from it, the same
  convention as the MTP importer, so both share one sync-history / dedup path.
- `cursor 0` = first page; pagination (`more != 0`, next cursor = last ride id) is inferred from
  OpenBikeCompanion's docs, **not** hardware-tested (test unit only ever held a couple of rides).
- Pages/function-settings **writes** (`40 43` / `40 4d`) exist in the protocol but aren't used
  here — this is import-only.

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
GD25Q256 32 MB flash. This project does not touch the firmware — import only.
