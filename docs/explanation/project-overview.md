# AmbitApp

Open tooling and apps that let the owner of a Suunto Ambit, Traverse or Kailash watch move
their own data — routes, waypoints, points of interest, sport-mode configuration, device
settings and recorded activities — between their own computer or phone and their own watch,
over USB cable and Bluetooth LE.

> **Independent project.** Not affiliated with, endorsed by, or connected to Suunto Oy.
> "Suunto", "Ambit", "Traverse", "Kailash", "Movescount" and "SuuntoLink" are trademarks of
> their respective owner and are used here only to identify the hardware and software this
> project interoperates with.

---

## Scope and legal basis

This is an **interoperability** project, in the ordinary sense of that word: enabling
independently created software to exchange data with a device its owner already owns.

- It targets **hardware the user owns**, using **lawfully obtained copies** of the
  manufacturer's software running on the user's own machine.
- It **does not circumvent any technical protection measure**. These watches expose an
  unprotected, unencrypted HID and GATT interface; there is no access control, licence check
  or copy protection involved at any point.
- It **redistributes no manufacturer software, firmware, schema file, catalogue or captured
  data**. Nothing of that kind is in this repository, and nothing of that kind is accepted
  into it (see *What this repository does not contain*).
- It **does not modify firmware**. The firmware-update path is documented but deliberately
  not implemented; firmware updates remain the manufacturer's own tool's job.
- It **does not impersonate, replace or interfere with any online service**, and it holds no
  credentials.
- Work of this kind is expressly permitted for interoperability purposes under, among
  others, Article 6 of EU Directive 2009/24/EC and 17 U.S.C. §1201(f).

If you represent the manufacturer and something here concerns you, the maintainers would
rather hear it directly and fix it than have you guess at our intent — see
[`SUUNTO_DEV_REQUESTS.md`](https://github.com/skinnie/ambit-app/blob/main/SUUNTO_DEV_REQUESTS.md), which is a standing, good-faith request
for technical information, not a demand.

---

## Introduction

The Suunto Ambit (2012), Ambit2 (2013), Ambit3 (2014), Traverse (2015) and Kailash (2015)
were designed around **Movescount**, a web service where routes, points of interest,
sport-mode configuration, training programs and interval workouts were authored and then
synced to the watch over Bluetooth or cable.

Movescount was retired in 2021. These models are supported in the **Suunto app** for orbital
GPS data and activity upload, while route, POI and sport-mode transfer moved to
**SuuntoLink** over the cable. The Kailash synced with the separate iOS-only 7R app rather
than with Movescount, and is not supported in the Suunto app. Two capabilities that depended
on Movescount's authoring service — training programs (planned moves) and interval workouts —
have no current equivalent.

This project documents the wire protocol and on-device data formats of these watches and
implements them in open code, so an owner can move their own data without a service account.
Everything was derived from observation of traffic between software and hardware on the
maintainers' own machines, verified byte-for-byte against those observations, and then
confirmed against real hardware. Where something is understood but not yet proven on a watch,
this document says so.

---

## What it is

Three deliverables sharing one core:

| Component | What it is | Transport |
|---|---|---|
| **`tools/`** | The reference implementation: ~30 Python modules that read and write every format described below. Python 3 standard library only. | USB |
| **`desktop/`** | A Qt 6 / QML desktop app (Linux, Windows, macOS) driving those tools through a local Python bridge server. | USB |
| **`android/`** | A React Native + `libambit` (C/NDK) Android app, forked from [`guiguoz/opensportsync`](https://github.com/guiguoz/opensportsync). | USB-OTG **and** Bluetooth LE |
| **`csrc/`** | The route serialiser in plain C, written to drop into openambit's `libambit` unmodified. | — |

The on-device navigation database format is fully documented: memory map, record structures,
the coordinate projection, the route-simplification pass and the closing region hashes,
including two ordering quirks that are reproduced exactly so that data written by this
project is indistinguishable from data the watch already accepts. The specification is in
[`tools/README.md`](https://github.com/skinnie/ambit-app/blob/main/tools/README.md).

---

## What this still requires from the manufacturer

Stated up front, because "offline" here means "no account and no cloud round-trip for your
own data" — not "independent of Suunto". Four real dependencies remain, and this project does
not attempt to replace any of them:

| Dependency | What needs it | Detail |
|---|---|---|
| **Suunto's orbital-data service** | AGPS / SGEE updates | The orbital data written to the watch is fetched from the manufacturer's own service, exactly as the official software fetches it, on explicit user action. This project derived how the watch stores it, not the data itself. No credentials are used or held. |
| **Suunto's device-info service** | Firmware version check and download link | Both the "is a newer firmware available" query and the image itself come from the manufacturer. |
| **A SuuntoLink installation, once** | Naming any protocol field | The schema descriptor that names the device's data fields is downloaded and cached on disk by SuuntoLink. It is what makes protocol payloads readable rather than guessed. It is **not** redistributed here — each user supplies their own, from their own machine, and it never leaves it. |
| **SuuntoLink itself** | Firmware updates; installing watch apps from the official catalogue | The firmware protocol is documented but not implemented here. The watch-app install path adds an entry to SuuntoLink's own local catalogue and lets SuuntoLink perform the install. |

Everything else — routes, POIs, waypoints, sport modes, device settings, recorded activities,
backups — is local and works with no manufacturer software running and no network connection.

---

## What this repository does not contain

By design, and enforced by `.gitignore`:

- No manufacturer software, libraries, firmware images or installers.
- No schema descriptors, device tables, catalogues or orbital-data files copied from the
  manufacturer's software.
- No protocol captures. Captures also carry device serial numbers and the owner's saved
  locations, so they are personal data as well as third-party material.
- No decompiled or disassembled third-party code, and no output derived from it.

What *is* here is this project's own description of the formats — facts about how the device
stores data — and this project's own code implementing them.

---

## Communication protocols

| Layer | Detail |
|---|---|
| **USB** | HID, vendor ID `0x1493`, 64-byte reports, messages reassembled across reports. Verified by a round trip over 4 724 messages / 47 117 reports re-encoded identically. |
| **Bluetooth LE** | The **phone hosts the GATT server; the watch connects into it as a GATT client** (the same pattern as ANCS). The watch advertises the service UUID as a *solicitation*, not as a service it offers — which is why GATT-client approaches only ever see the two generic services on it. |
| **Pairing** | Standard LE Legacy **Passkey Entry**: the watch is `Display Only`, the central types the number in. A `NoInputNoOutput` central receives a Just-Works downgrade, which the watch accepts. Ordinary Bluetooth pairing, no vendor-specific authentication. |
| **Framing (BLE)** | `0x7e`-delimited, SLIP-escaped, 12-byte header, CRC32 trailer over header + payload. |
| **Payloads** | A command envelope identical over USB and BLE, carrying typed records in the device's own SBEM0102 encoding. Field identifiers come from the device's own schema descriptor, on the user's machine. |
| **On-device regions** | Navigation database (routes and waypoints), sport modes, watch apps, recorded-move log, orbital data, training program, event log, pairing info. |
| **Firmware** | **Documented, not implemented.** The tooling is read-only and has no write path; two details remain unverified and no write will be attempted. Use SuuntoLink to update firmware. |

Because the envelope, the record encoding and the region formats are identical over USB and
BLE, a feature implemented once works over both transports.

---

## Device compatibility

| Watch | Codename | USB PID | Cable (USB) | Bluetooth LE |
|---|---|---|---|---|
| Suunto Ambit | Bluebird | `0x0010` | ✅ | — *(no BLE in hardware)* |
| Suunto Ambit2 | Duck | `0x0019` | ✅ | — |
| Suunto Ambit2 S | Colibri | `0x001a` | ✅ | — |
| Suunto Ambit2 R | Greentit | `0x001d` | ✅ | — |
| **Suunto Ambit3 Peak** | **Emu** | `0x001b` | ✅ **reference watch** | ✅ **reference watch** |
| Suunto Ambit3 Sport | Finch | `0x001c` | ✅ | 🟡 |
| Suunto Ambit3 Run | Ibisbill | `0x001e` | ✅ | 🟡 |
| Suunto Ambit3 Vertical | Kaka | `0x002c` | ✅ | 🟡 |
| Suunto Traverse | Jabiru | `0x002b` | ✅ | 🟡 |
| Suunto Traverse Alpha | Loon | `0x002d` | ✅ | 🟡 |
| **Suunto Kailash** | **Hoopoe** | `0x002a` | ✅ hardware-confirmed (2026-08-10) | ✅ hardware-confirmed |
| Garmin eTrex (10 / 30) | — | VID `0x091e` | ✅ activity import only | — |

**Legend:** ✅ confirmed on real hardware · 🟡 same driver and protocol family, not yet tested
on that specific model · ❌ not possible · — the hardware does not have it.

Two points worth stating plainly:

- **The Ambit3 Peak is the only fully hardware-verified device**, with the Kailash verified
  over Bluetooth. The rest of the family shares the same driver and protocol, so it is
  expected to work — but "expected" is not "tested". On another model, read region addresses
  and sizes from the watch's own reply rather than trusting the reference constants.
- **The Kailash didn't support USB activity/cities-visited sync in Suunto's own software.**
  SuuntoLink can sync a Kailash's clock over cable (confirmed live, 2026-08-10), but its own
  activity/travel-history data path was Bluetooth-and-7R-app-only. This project's own USB
  driver used to crash on a Kailash too, but that was a real bug in this project's own code
  (a double-free in the shared flash-read path, fixed 2026-08-10 - see `HANDOFF.md`/git
  history), not a watch or protocol limitation. This app now supports full cable sync for
  Kailash: device identify, TrackLog and DeviceHistory reads, and watch clock sync are all
  confirmed live over USB.

---

## Feature matrix — Android app (USB-OTG + Bluetooth LE)

Version 3.0.0-beta. `U` = over USB-OTG, `B` = over Bluetooth LE.

| Feature | Ambit 1 | Ambit2 family | Ambit3 family | Traverse / Alpha | Kailash |
|---|---|---|---|---|---|
| Detect device, name, firmware / hardware version | ✅ U | ✅ U | ✅ U ✅ B | ✅ U 🟡 B | ✅ U *(2026-08-10)* ✅ B |
| Battery level | ✅ U | ✅ U | ✅ U ✅ B | ✅ U 🟡 B | ✅ U *(2026-08-10)* ✅ B |
| Send GPX route to watch | 🟡 U | 🟡 U | ✅ U ✅ B | 🟡 U 🟡 B | — *(no route feature on the watch)* |
| Read routes back off the watch | 🟡 U | 🟡 U | ✅ U ✅ B | 🟡 U 🟡 B | — |
| POI import (GPX / typed coordinates) | 🟡 U | 🟡 U | ✅ U ✅ B | 🟡 U 🟡 B | — |
| POI export | 🟡 U | 🟡 U | ✅ U ✅ B | 🟡 U 🟡 B | — |
| Activity sync → GPX / FIT | ✅ U | ✅ U | ✅ U ✅ B | ✅ U 🟡 B | ✅ B *(separate path)* |
| Activity map, elevation profile, replay | ✅ | ✅ | ✅ | ✅ | ✅ |
| Upload to Strava / Runalyze / Livelox | ✅ | ✅ | ✅ | ✅ | ✅ |
| Orbital / AGPS write *(data from the manufacturer's service)* | 🟡 U | 🟡 U | ✅ U ✅ B | 🟡 U 🟡 B | 🔬 |
| Sport modes read | — *(different mechanism)* | — | ✅ U ✅ B | 🟡 U | — |
| Sport modes edit (rename, autolap, HR limits, pods, display fields) | — | — | ✅ U 🟡 B | 🔬 | — |
| Watch settings **read** | ✅ U *(legacy structure)* | ✅ U *(legacy structure)* | ✅ U ✅ B | ✅ U | ✅ B |
| Watch settings **write** | ❌ | ❌ | ✅ U 🟡 B | ❌ | ✅ B *(incl. Home Location)* |
| Navigation backup (create) | 🟡 | 🟡 | ✅ | 🟡 | — |
| Kailash travel history / visited places | — | — | — | — | ✅ U *(2026-08-10)* ✅ B |
| Firmware version check + download link *(from the manufacturer's service)* | ✅ | ✅ | ✅ | ✅ | ✅ |
| Firmware updates | ❌ *(use SuuntoLink)* | ❌ | ❌ | ❌ | ❌ |
| Weather (Open-Meteo), maps (IGN / OSM / CyclOSM) | ✅ | ✅ | ✅ | ✅ | ✅ |

## Feature matrix — Desktop app (Qt 6 / QML, USB cable only)

| Feature | Ambit 1 | Ambit2 family | Ambit3 family | Traverse / Alpha | Kailash |
|---|---|---|---|---|---|
| Device identity, serial, firmware, hardware, live battery | ✅ | ✅ | ✅ | 🟡 | ✅ *(2026-08-10)* |
| Send GPX route | 🟡 | 🟡 | ✅ | 🟡 | — |
| Route rehearse-before-write + thumbnail preview | ✅ | ✅ | ✅ | ✅ | — |
| Read on-watch routes (summary) | 🟡 | 🟡 | ✅ | 🟡 | — |
| Download a route's actual GPS points | ❌ *(port pending)* | ❌ | ❌ | ❌ | — |
| POI list + Add form with live map preview | 🟡 | 🟡 | ✅ | 🟡 | — |
| POI import / export | ❌ *(port pending)* | ❌ | ❌ | ❌ | — |
| Activities → GPX / FIT, cards, map detail | ✅ | ✅ | ✅ | 🟡 | ❌ |
| Backup: create / list / rehearse / restore | 🟡 | 🟡 | ✅ | 🟡 | — |
| Orbital / AGPS write | 🟡 | 🟡 | ✅ | 🟡 | ❌ |
| Sport modes read + edit | — | — | ✅ | 🔬 | — |
| Firmware check + download-for-backup | ✅ | ✅ | ✅ | ✅ | ✅ |
| Weather, maps, Connections | ✅ | ✅ | ✅ | ✅ | ✅ |
| Bluetooth LE | ❌ *(desktop is cable-only)* | ❌ | ❌ | ❌ | ❌ |

**iOS:** not started. The transport is understood, a Mac and an iPhone are available, and the
serialiser is shared — so it is scoped implementation work, not research. Nothing is built.

### Why the desktop gaps exist

- **No Bluetooth on desktop.** The BLE transport was built in the Android app's native layer.
  Porting it to the desktop's Python backend is implementation work that has not been done —
  not an open technical question.
- **Route point download and POI import return honest errors on desktop.** Both are real,
  hardware-confirmed capabilities elsewhere in this project, but that code is not in the
  desktop backend's copy of the writer yet.
- **Activity charts / laps / notes tabs** need a charting library decision.
- **Sport-specific activity icons:** the raw sport-type byte in exported GPX is not decoded to
  a name anywhere in this project, so every activity card uses one generic icon rather than a
  guessed mapping.
- **GPS orbit validity display** is a separate query that is not wired to the UI.

---

## Movescount → Suunto app: capability changes, and what is implemented here

| Movescount-era capability | State after the transition | Implemented here? | Detail |
|---|---|---|---|
| Route transfer to watch, wireless | Moved to SuuntoLink over cable, for routes marked for watch use | ✅ **Yes**, cable and Bluetooth | GPX file → watch directly, with no account. Hardware-confirmed on Ambit3 Peak. A route needs at least one waypoint-type entry to appear on the watch. |
| POI transfer to watch, wireless | Same cable path as routes | ✅ **Yes**, cable and Bluetooth | Import from GPX or typed coordinates, and export back. |
| Sport-mode / display customisation, wireless | Moved to SuuntoLink over cable | ✅ **Yes**, cable and Bluetooth | Rename, autolap, HR limits, sensor pods, per-display field editing. Byte-exact against what the official tool writes. Ambit3 confirmed; Traverse family not yet mapped. |
| Personal info / body metrics sync, wireless | Not carried over | 🟡 **Partly** | The personal-profile fields (gender, weight, height, max/rest HR, activity level, birthday) are read and decoded live. The write channel works and is used for other settings, but these fields are not exposed in either UI yet. No format blocker — UI work. |
| Daily steps / activity trend | Not carried over | 🟡 **Read path found, live-tested** | The watch stores it, and it is reachable with the same query machinery already used for the logbook. Read confirmed on hardware; not yet surfaced in either app. |
| Training programs / planned moves | Not carried over | ✅ **Native, hardware-confirmed (2026-09-03)** | The watch shows the Movescount-era "Today 1/2" planned-move card from its own TrainingProgram flash region (TIME mode → [Next]), exactly as user-guide §3.39 describes — no app, no workaround. The one blocker behind every earlier failed write was a **4-byte header signature** at offset 4 (`3C 46 50 5A`): the desktop packer copies the prior region's bytes 4..7, which on a Movescount-initialised watch were always this constant, but on an erased region became `0xFFFFFFFF` and the firmware rejected the header. Found by decompiling the watch's **TI MSP430X** firmware (a year of prior "impenetrable firmware" analysis had been reading the nRF51 BLE co-processor image by mistake). `tools/training_program.py` writes single moves or a whole `--plan`; the desktop "Sync to watch" action writes the plan as native cards alongside the WORKOUT-menu guidance. |
| Interval workouts (browsable WORKOUT menu, live segment graph, §3.18) | Not carried over | ❌ **Blocked** | These are not watch-app bytecode but sport-mode rules carrying a declarative trigger structure the firmware interprets natively. **Open:** the trigger byte encoding cannot be confirmed, because there is no traffic left anywhere to observe it in. The authoring half is solved — the workout description schema round-trips through this project's own generator. |
| Watch apps (App Zone) | SuuntoLink still installs apps from its bundled catalogue; the authoring compiler is gone | 🟡 **Install yes, authoring no** | Installing a catalogue app with this project's tooling works and renders on hardware. **Open:** binaries from the surviving community compiler are accepted by the watch but never execute, even a trivial one-line app. There is no compiler whose output runs on this firmware. |
| Orbital / AGPS data | Kept (Suunto app and SuuntoLink) | ✅ **Yes**, using the manufacturer's own service | The write is byte-exact against reference traffic and hardware-confirmed. **The data still comes from the manufacturer** — a real dependency, not a replacement. |
| Activity upload and storage | Kept for these models via the Suunto app; the Movescount analysis layer is gone | ✅ **Local alternative** | Local library, GPX/FIT export, and OAuth2 upload to Strava, Runalyze, Livelox and Intervals.icu, at the user's choice. |
| Firmware updates | Kept (SuuntoLink) | 🟡 **Check and download only, by choice** | Version check and download link work against the manufacturer's service. The update protocol is documented but not implemented. |
| Kailash sync | Not supported in the Suunto app; activity/cities-visited sync was 7R-app-only (iOS, Bluetooth-only) | ✅ **Yes**, over Bluetooth and USB cable | Pair, identify, read and write settings including Home Location, read travel history and visited places, and export a recorded activity to GPX from the watch's ephemeral sample store. Confirmed live over both Bluetooth and, as of 2026-08-10, USB cable (device identify, TrackLog, DeviceHistory, and watch clock sync). |
| Movescount web route planner | Not carried over | ✅ **Local alternative** | In-app maps (IGN / OSM / CyclOSM) and route import with preview. |

**Legend:** ✅ implemented · 🟡 partially implemented, no technical blocker · 🔬 understood, one
unknown left · ❌ blocked on an unresolved unknown.

The two capabilities still blocked — interval workouts and training programs (planned moves) —
are the two whose authoring lived on a service that no longer runs. There is nothing left to observe for
either, which is why they are open questions rather than pending implementation. See
[`SUUNTO_DEV_REQUESTS.md`](https://github.com/skinnie/ambit-app/blob/main/SUUNTO_DEV_REQUESTS.md) for exactly what information would close
them.

---

## Tested hardware, operating systems and software

Nothing in the matrices above is inferred from a datasheet. This is what it ran on.

### Watches

| Device | Firmware / hardware | Role |
|---|---|---|
| Suunto Ambit3 Peak (`Emu`) | fw 2.4.17, hw 70.2.17414 | The reference watch: every route, POI, sport-mode and orbital write, and the whole Bluetooth milestone. |
| Suunto Kailash (`Hoopoe`) | fw 2.0.5, hw 72.1.0 | Second-generation cross-check: Bluetooth pairing, settings, travel history, activity export. |
| Garmin eTrex 30 | — | Activity import path (Android). |

### Computers, phones and operating systems

| Machine | Spec | What ran on it |
|---|---|---|
| Lenovo ThinkPad X230 | i5-3210M, x86_64, 16 GB, Linux Mint **+** Windows 10 64-bit (dual boot) | Windows side: the official desktop software, for reference behaviour. Linux side: openambit, `tools/*.py`, every real cable write, the Qt 6 desktop build (Qt 6.12.0), and live BlueZ pairing sessions on a BCM4352 / BCM20702A0 adapter. |
| MacBook Air M4 | Apple Silicon ARM, 16 GB, macOS 15.7.5 | Xcode and macOS packaging. Apple Silicon has no AArch32, so 32-bit ARM work stays on the X230. |
| Panasonic Toughpad FZ-M1 MK2 | Atom x5-Z8550, x86_64, 4 GB, Android 12 (BlissOS 15.9.2), rooted | The Android test bench: APK builds, USB-OTG against the watch, and every Bluetooth feature confirmation. |
| iPhone 13 mini | iOS 17 / 27 beta | Reference Bluetooth behaviour of the official apps, using Apple's own developer diagnostics tooling. No jailbreak, no instrumentation of third-party apps. |

### Software this was checked for compatibility against

SuuntoLink (Windows and macOS), the Suunto app (Android and iOS), the Suunto 7R app (iOS),
[openambit](https://github.com/openambitproject/openambit)'s `libambit` — used as a
cross-check throughout, with four real traps in it documented in `HANDOFF.md` — and
`guiguoz/opensportsync`.

**Build and runtime requirements:** Python 3 (standard library only) for `tools/`; `gcc` and
`libm` for the C serialiser; Qt 6.5+ for the desktop app; Android 9+ (API 28+), NDK r26+ and
an OTG cable for the Android app.

---

## Your watch isn't in the matrix? How to get it added

Every device covered here was added by reading facts about the device — its identifiers, its
field table, its memory layout — and implementing against them. You can supply those facts
for your own watch without writing any code.

**Please do not send us manufacturer files.** No schema descriptors, device tables, firmware
images, catalogues or orbital-data files. This project does not accept them, and it does not
need them: what it needs are the *facts* they describe about your device, which you can
extract yourself, on your own machine, from software you already have installed. Keep the
originals where they are.

### 1. Connect the watch once with the official software

That is what makes your device's own field table available locally, cached by SuuntoLink.

### 2. Run the local report and send its output

```
python3 tools/sbem_schema.py            # your device's field table, as text
python3 tools/device_info.py            # model, codename, firmware, hardware version
python3 tools/write_nav.py nav          # the memory region map, read from your own watch
```

Send the **text output** of those three commands. That is a description of how your watch is
organised — factual information about your own hardware — not a copy of anyone's file. It is
usually enough to tell whether the existing tooling should already drive your model, and what
would need to change.

**Also useful, and easy:** your watch's model name and USB product ID as your OS reports it
(`lsusb` on Linux, Device Manager on Windows, System Information on macOS).

### 3. Optional: verification traffic, if you're willing

Observing the official software talk to your own watch on your own machine is what turns
"should work" into "verified" — it is how every format here was confirmed. If you want to
help at that level, **contact the maintainers first rather than attaching anything to a
public issue.** A capture of your own device is still your own personal data: it carries your
watch's serial number, and typically your saved locations and recorded activities. It is
never committed to this repository.

### 4. Before you send anything — privacy

- Command output may include your **watch serial number**; redact it, and just say which
  firmware version you are on.
- Never post activity files, saved locations or captures publicly.
- Nothing of this kind is stored in this repository, by design.

### 5. What happens next

With the field table and region map alone, we can usually say whether your model is already
covered, and what specifically differs. Nothing is ever written to your watch until the
encoding has been checked against known-good reference data — which is the rule this project
applies to itself: every write is rehearsed in dry-run first, and no write path runs without
an explicit flag.

---

## Safety rules

- Every write path is **dry-run by default**; `--write` is explicit, on every tool and every
  API endpoint.
- Computed offsets are **hard-checked against the region size** before any real write.
- The navigation regions carry a checksum over their own contents; every read validates
  itself against it.
- Backups are real region dumps, restorable, and rehearsable before restore.
- **Firmware is never written.** Use SuuntoLink.
- Scope is matched to the hardware: a 2012–2015 black-and-white watch with no optical HR
  sensor, a slow CPU and little storage.

**No warranty.** This software talks to a device that is long out of production. Use it on
hardware you own, at your own risk; see the licence text for the full disclaimer.

---

## Documentation map

| Document | Contents |
|---|---|
| [`HANDOFF.md`](https://github.com/skinnie/ambit-app/blob/main/HANDOFF.md) | **Start here.** Full project state, milestone by milestone, with every derivation and dated finding. |
| [`tools/README.md`](https://github.com/skinnie/ambit-app/blob/main/tools/README.md) | The format specification: memory map, structures, coordinate formula, simplification, hashes, field schema. |
| [Runbook](../tutorials/runbook.md) | Step-by-step instructions for whoever physically has the watch. |
| [History](history.md) | Watch-family background, codenames, timeline, adjacent open-source projects. |
| [App spec](../reference/ambitapp-spec.md) | Design language and feature spec for the apps. |
| [`desktop/README.md`](https://github.com/skinnie/ambit-app/blob/main/desktop/README.md), [`android/README.md`](https://github.com/skinnie/ambit-app/blob/main/android/README.md) | Per-app architecture, build instructions, and the current gap lists. |
| [Kailash BLE findings](kailash-ble-findings.md) | The Kailash Bluetooth protocol. |
| [`SUUNTO_DEV_REQUESTS.md`](https://github.com/skinnie/ambit-app/blob/main/SUUNTO_DEV_REQUESTS.md) | A good-faith request for the specific technical information that would close the two remaining blocked features. |
| [Unresolved questions](unresolved-questions.md) | Open protocol questions, kept shareable standalone. |

---

## Licence and credits

[GPLv3](https://github.com/skinnie/ambit-app/blob/main/LICENSE), the same licence as
[openambit](https://github.com/openambitproject/openambit), whose `libambit` this project
checks its own work against throughout.

See [Credits](../reference/credits.md) — openambit,
[opensportsync](https://github.com/guiguoz/opensportsync),
[marguslt](https://github.com/marguslt), sebchastang, the Suunto forum community, and
wanarun.net.
