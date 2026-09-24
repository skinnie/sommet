# Supported devices

Every device this project targets (PROJECT_RULES.md rule 2: "cross-device... the whole real
device family, not just the Ambit3 Peak reference watch"), its internal codename where one
exists, and its official manual link. Manual URLs are sourced from `manualslinks` at the repo
root and wired into the app on Home (desktop `HomePage.qml`/`HomeViewModel.qml`, Android
`HomeScreen.tsx`/`config/manuals.ts`) next to the Hardware field.

## Suunto (NSP flash protocol, cable + BLE)

| Device | Codename | Manual |
|---|---|---|
| Suunto Ambit | Bluebird | [PDF](https://ns.suunto.com/Manuals/Ambit/Userguides/Suunto_Ambit_UserGuide_EN.pdf) |
| Suunto Ambit 2 | Duck | [PDF](https://ns.suunto.com/Manuals/Ambit2/Userguides/Suunto_Ambit2_UserGuide_EN.pdf) |
| Suunto Ambit 2 S | Colibri | [PDF](https://ns.suunto.com/Manuals/Ambit2_S/Userguides/Suunto_Ambit2_S_UserGuide_EN.pdf) |
| Suunto Ambit 2 R | Greentit | [PDF](https://ns.suunto.com/Manuals/Ambit2_R/Userguides/Suunto_Ambit2_R_UserGuide_EN.pdf) |
| Suunto Ambit 3 Peak | Emu | [PDF](https://ns.suunto.com/Manuals/Ambit3_Peak/Userguides/Suunto_Ambit3_Peak_UserGuide_EN.pdf) |
| Suunto Ambit 3 Sport | Finch | [PDF](https://ns.suunto.com/Manuals/Ambit3_Sport/Userguides/Suunto_Ambit3_Sport_UserGuide_EN.pdf) |
| Suunto Ambit 3 Run | Ibisbill | [PDF](https://ns.suunto.com/Manuals/Ambit3_Run/Userguides/Suunto_Ambit3_Run_UserGuide_EN.pdf) |
| Suunto Ambit 3 Vertical | Kaka | [PDF](https://ns.suunto.com/Manuals/Ambit3_Vertical/Userguides/Suunto_Ambit3_Vertical_UserGuide_EN.pdf) |
| Suunto Traverse | Jabiru | [PDF](https://ns.suunto.com/Manuals/Traverse/Userguides/Suunto_Traverse_UserGuide_EN.pdf) |
| Suunto Traverse Alpha | Loon | [PDF](https://ns.suunto.com/Manuals/Traverse_Alpha/Userguides/Suunto_TraverseAlpha_UserGuide_EN.pdf) |
| Suunto Kailash | Hoopoe | [PDF](https://ns.suunto.com/Manuals/Kailash/Userguides/Suunto_Kailash_UserGuide_EN.pdf) |

Ambit3 Peak (Emu) is this project's one reference watch and the fallback used anywhere a
codename can't be resolved (e.g. `HomeViewModel.qml`'s `deviceDisplayName`/`manualUrl`).
Reference watch identification is the real 0x0000 device-info reply
(`DeviceService.model`/`AmbitDeviceInfo.model` on Android) - see `history.md`/
`workout_gui.py`'s `VARIANT_NAMES` for how these codenames were confirmed.

## Garmin (USB mass storage, activity import only)

No codename - `GarminDevice.xml`'s own `<Model><Description>` free-text field
(`GarminService.model` / `GarminModule`'s `model`) is matched by family instead, since Garmin's
manual pages group several models under one guide each:

| Family | Example models | Manual |
|---|---|---|
| eTrex 10 / 20 / 20x / 30 / 30x | eTrex 10, eTrex 30 (hardware-confirmed, André's own unit) | [PDF](https://www8.garmin.com/manuals/webhelp/eTrex_10_20x_30x/EN-US/eTrex_10_20_20x_30_30x_OM_EN-US.pdf) |
| eTrex 22x / 32x | eTrex 22x, eTrex 32x | [PDF](https://www8.garmin.com/manuals/webhelp/eTrex22x-32x/EN-US/eTrex_22x_32x_OM_EN-US.pdf) |

Matching rule (`garminManualUrl` on desktop, `garminManualUrlFor()` on Android): a model
description containing "22x" or "32x" gets the second guide; everything else in the eTrex
10/20/30 generation gets the first.

## Bike computers (activity import only)

Head units whose recorded rides Sommet imports into the library (source-tagged per brand) and,
when the intervals.icu export scope is "all", uploads to intervals.icu as the device's own FIT.
Import only - Sommet does not configure these devices.

| Device | Source tag | Transport | Notes |
|---|---|---|---|
| Garmin Edge | `edge` | USB mass storage (MTP / gvfs) | `Garmin/Activities/*.fit`; Linux gvfs path (`tools/mtp_import.py`) |
| Hammerhead Karoo | `karoo` | USB mass storage (MTP / gvfs) | `FitFiles/*.fit`; MTP enabled in the Karoo's dev options |
| Magene C406 Pro | `c406` | **Bluetooth LE** (must bond) | no USB data mode; `tools/magene_import.py`, protocol in [`magene_c406_protocol.md`](magene_c406_protocol.md) |

The Magene is found by an on-demand Bluetooth scan (behind the experimental Bluetooth toggle,
with the watch's own BLE pairing) rather than the USB poll the Edge/Karoo use, and it **must be
BLE-bonded** or it stays on its "please pair" screen - see the protocol doc. Ride sport type
(Cycling vs Indoor Cycling) comes from the FIT's `sub_sport`, so it matches on intervals.icu by
construction.

## Not yet in `manualslinks` / not linked in-app

Nothing currently known to be missing - if a new device gets added to this project (a new
Ambit/Traverse/Kailash variant, a different Garmin model, or a different brand entirely), add
its manual URL to `manualslinks` first, then this table and the two in-app tables
(`HomeViewModel._manualUrls`/`garminManualUrl` on desktop, `config/manuals.ts` on Android) stay
in sync by hand - same convention already used for `_modelNames`/`SUUNTO_PID_NAMES`.
