# v2.3 beta — Garmin USB mass-storage GPX/FIT import (planning doc)

Status: implemented. The code is in `android/src/services/GarminActivityService.ts`,
`GarminGpxExportService.ts` and `EtrexExport.ts`. This document was written as the plan before
the code, and the sections below are kept as written then.

## App architecture: multi-device support (André, 2026-08-07)

Sommet's existing feature set is built entirely around the Ambit3's internal flash memory
(routes/POIs/orbital data all live in specific flash regions, read/written via the NSP
protocol). Adding Garmin support does **not** mean extending that model — Garmin devices work
completely differently (plain files on a FAT filesystem), so Garmin needs its own,
separately-implemented feature set, selected by device detection at connect time, not a
shared code path pretending the two devices are similar.

**Detection, first thing on connect:** identify whether the connected device is an Ambit
(existing USB VID/PID detection, unchanged) or a Garmin (USB VID `091e`, confirmed above,
mass-storage class). Route to the appropriate feature set below — the two are not merged or
cross-compatible.

**If Ambit:** everything already built and tested stays exactly as-is (routes, POI, orbital
data, activity read, 3rd-party sync, BLE). No changes.

**If Garmin, feature-by-feature:**

| Feature | Behavior |
|---|---|
| Activities (read) | Read from the device's `GPSData` `OutputFromUnit` path (`Garmin/GPX/Current/Current.gpx` on this unit — resolved from `GarminDevice.xml` per the discovery strategy above, not hardcoded) |
| 3rd-party sync | Identical pipeline to Ambit — same Strava/Runalyze/Intervals.icu integrations, since they operate on the already-parsed GPX/FIT data, not on anything Ambit-specific |
| Orbital/AGPS data | **Not applicable, not offered.** This is a Suunto-specific concept (GPS ephemeris upload via `0x0b15`) with no Garmin equivalent — the Garmin feature set simply doesn't have this button |
| Upload GPX (routes) | **Real safety rule, explicit from André, confirmed 2026-08-07: NEVER write to the Garmin's internal memory, no exceptions.** Only available when an SD card is detected inside the device; writes go to `<SD card volume>\Garmin\GPX`, never the internal-memory volume. If no SD card is present, the upload feature must be disabled/hidden with a clear reason shown, not silently fail or fall back to internal memory. **A warning must also be shown to the user** (André, 2026-08-07) — surface the SD-card-only restriction explicitly in the UI, not just enforce it silently in code |
| POI | **Confirmed 2026-08-07: same "never write internal memory, SD-card-only, with a user-facing warning" restriction applies here too** — same underlying write operation as routes (a GPX file, just with `<wpt>` elements instead of `<trk>`/`<rte>`), same rule, no distinction. Written to `<SD card volume>\Garmin\GPX`, same as routes |

**POI format, confirmed 2026-08-07:** simple GPX-waypoint mechanism (not Garmin's separate
`.gpi` "Custom POI" system - that remains out of scope, a materially different, more complex
feature using a proprietary binary format normally built with Garmin's own POI Loader tool).
Confirmed directly against real files already on André's eTrex 30 (not just spec reading)
that a single GPX file can and does contain multiple `<wpt>` entries — `Waypoints2.gpx` (9
waypoints), `Waypoints4.gpx` (5), `Waypoints.gpx` (7), all real, pre-existing files on the
device. **Correction, same day:** these files were not created by the device itself - their
GPX header reads `creator="Garmin Desktop App"` (André identified them as Garmin BaseCamp
output). That's actually a stronger confirmation for this plan, not a weaker one: they're
real proof of official Garmin software performing exactly the write operation we're planning
to replicate - standard multi-waypoint GPX files written into `Garmin/GPX/`. So POI upload is
not limited to one point per file; it's the exact same mechanism as route upload (a GPX file
dropped into `Garmin/GPX/` on the SD card), just with `<wpt>` elements instead of `<trk>`/
`<rte>` - and BaseCamp doing exactly this is direct precedent, not just a format-spec
inference.

**Summary of the write-safety rule, since it now applies uniformly:** every write operation
for a Garmin device (routes, POI - anything that puts a file onto the device) is SD-card-only,
never internal memory, with no silent fallback, and the UI must warn the user about this
restriction rather than just quietly enforcing it. Read operations (activities) are
unaffected - reading from internal memory is fine, only writes are restricted.

## Real hardware confirmation, 2026-08-07 — André's own eTrex 30, plugged into a Linux desktop

Every open question about folder structure and device identification below is now settled,
not guessed, for this device generation. `lsusb`: `091e:2519 Garmin International eTrex 30`
— confirms the VID found in the decompiled apps (`091e`/2334) against real hardware, exact
match. Auto-mounted cleanly by desktop Linux (`udisks2`) as `/dev/sdd` → `/media/skinnie/
GARMIN`, plain FAT (`vfat`). A second, empty (0-byte) block device (`sde`) also appeared —
almost certainly the SD card slot, confirming the multi-volume concern was real, just with
no card inserted this time to fully exercise it.

**`Garmin/GarminDevice.xml` is real and exactly matches the discovery strategy found in the
decompiled apps.** Confirmed content (this unit):
```xml
<Model><PartNumber>006-B1305-00</PartNumber><SoftwareVersion>501</SoftwareVersion>
  <Description>eTrex 30</Description></Model>
<Id>3889775294</Id>
...
<DataType><Name>GPSData</Name>
  <File><Location><Path>Garmin/GPX</Path></Location><TransferDirection>InputToUnit</TransferDirection></File>
  <File><Location><Path>Garmin/GPX/Current</Path><BaseName>Current</BaseName>
    <FileExtension>GPX</FileExtension></Location><TransferDirection>OutputFromUnit</TransferDirection></File>
</DataType>
```
Two real, concrete wins here: (1) **`<Description>eTrex 30</Description>` gives the exact
model name as plain text, straight from the device — no product-ID lookup table needed at
all for model identification**, better than the FIT-SDK approach the decompiled apps used.
(2) The `GPSData` entry's two `<File>` elements, distinguished by `TransferDirection`, are
exactly the machine-readable answer to "which folder do I read the recorded track from":
`Garmin/GPX` is `InputToUnit` (files we'd push onto the device — waypoints/routes, not what
we want), `Garmin/GPX/Current` is `OutputFromUnit` (what the device produces for us to read —
exactly the recorded track). This is a clean, generalizable rule, not specific to this one
unit: **read the `GPSData` DataType's `OutputFromUnit` file entry, don't hardcode a path.**

**`Garmin/GPX/Current/Current.gpx` is a real 159,845-byte recorded track**, GPX 1.1,
`creator="eTrex 30"` embedded directly in the GPX header too (independent confirmation of
the model name, redundant with `GarminDevice.xml`). No FIT-related `DataType` exists
anywhere in this unit's `GarminDevice.xml` — this specific device/firmware only does GPX,
confirming FIT is a newer-generation-only concern, not something to assume universally.

## Implementation-ready: device identification (model + firmware version) for the GUI

Self-contained spec for whoever picks this up — everything below is confirmed against real
hardware (André's eTrex 30, 2026-08-07), not inferred.

**Source file**, always at a fixed, predictable location once the device's mass-storage
root is found: `<volume root>/Garmin/GarminDevice.xml`. Same file already used for the
folder-discovery strategy above — parse it once, use it for both purposes.

**Fields to read**, all inside the single top-level `<Model>` element (real example, this
unit):
```xml
<Model>
  <PartNumber>006-B1305-00</PartNumber>
  <SoftwareVersion>501</SoftwareVersion>
  <Description>eTrex 30</Description>
</Model>
```

| Field | Raw value (this unit) | What to show in the GUI | Notes |
|---|---|---|---|
| `Description` | `eTrex 30` | Model name, verbatim | Plain text, already human-readable — no lookup table needed |
| `SoftwareVersion` | `501` | `5.01` | **Formatting rule**: integer with an implied decimal point two digits from the right (`501` → `5.01`, `1250` → `12.50`) — this is Garmin's standard convention across their device XML schemas, matches what the device's own on-device "About" screen shows |
| `PartNumber` | `006-B1305-00` | Shown verbatim, e.g. as a smaller/secondary line under the model+firmware line | André's request, 2026-08-07: display this too, not just capture it — it's the exact identifier needed to look up the correct firmware file on Garmin's own site, so it's directly useful to the user, not just internal bookkeeping |

**Suggested display**, matching this unit's real values:
```
eTrex 30 — firmware 5.01
006-B1305-00
```
(model + firmware as the primary line, part number as a secondary/smaller line — exact
layout is up to whoever implements the GUI, but all three fields should be user-visible.)

**Parsing approach**: this is a small, well-formed XML file (a few KB), not something to
hand-roll with regex — use whatever XML parser is already available in the target codebase
(the GUI implementation should use its own standard XML library, not reinvent one). Only the
single `<Model>` element and its three children above are needed for this specific feature;
the much larger `<MassStorageMode>` section (used for the folder-discovery feature described
earlier in this document) can be parsed separately or at the same time, whichever fits the
target codebase's structure better.

**Not yet confirmed**: whether `SoftwareVersion`'s implied-decimal-point convention holds
for all Garmin devices/generations, or is specific to this one. Treat the formatting rule as
correct-until-shown-otherwise, not gospel, if a future device shows an implausible-looking
value (e.g. a version that doesn't look like `X.YZ` once the decimal point is inserted).

**Net effect on the open questions below:** the `GarminDevice.xml`-driven discovery
strategy is no longer a hypothesis borrowed from reading someone else's decompiled code —
it's now verified end-to-end against real hardware. The remaining open items are about
generalizing across more device generations/models, not about whether the core approach
works at all.

## Goal

Read recorded activity files (GPX and/or FIT) directly off a Garmin handheld's own storage
when connected over USB, without needing Garmin Connect, BaseCamp, or any Garmin account —
same "no vendor cloud dependency" philosophy as the existing Ambit3 support. Primary target:
the eTrex series.

## Prior art check (done, 2026-08-06/07)

No existing open-source project does this specific thing. The closest-sounding match,
`gimportexportdevs/gimporter`/`gexporter`, is unrelated on inspection — it's a Garmin
ConnectIQ app (runs on smartwatches like the Fenix/Marq) paired with an Android companion
app that transfers files over Bluetooth/ConnectIQ, not USB mass storage, and doesn't target
eTrex at all. Nothing to reuse from it.

The underlying general capability — USB Mass Storage (SCSI + FAT) access on Android without
root — is well-solved: `magnusja/libaums` (Apache 2.0, actively maintained, widely used).
Not yet decided whether we need it (see "Two possible implementation paths" below).

**Decompiled abandonware check (done, 2026-08-07):** André had four old, closed-source
Android apps that did something adjacent (`assets/APK/garmin/` — Sportablet, Uploader for
Garmin, Garmin Uploader, Exchanger for Garmin). Two distinct codebases, not four:
- "carlopescio" (Sportablet / Uploader for Garmin): targets exactly our case — Garmin USB
  Mass Storage devices, GPX/FIT file reading. But its USB layer is weak and not worth
  imitating: no real mass-storage/SCSI/FAT driver at all, it just *hopes* the OS already
  auto-mounted the drive (legacy `MEDIA_MOUNTED` broadcast + hardcoded `/mnt/usb_storage`
  fallback + a manual "type the mount path yourself" escape hatch for when that fails).
  Confirms modern Android generally does NOT reliably auto-mount OTG mass storage the way
  this app assumes - real evidence pointing toward needing `libaums` (Path B below), not
  supporting Path A as the likely winner.
- "bulkodel" (Garmin Uploader / Exchanger for Garmin, same build under two names): solves a
  *different* problem entirely - Garmin's proprietary binary GPS protocol over raw USB bulk
  endpoints (mock-location provider, live position feed), not mass storage. Not applicable
  to reading recorded activity files. No code from this one is relevant here.

**Two real, reusable patterns found, worth adopting:**
- **Discovery strategy** (carlopescio): don't hardcode one folder-path guess. Look for the
  top-level `Garmin/` folder, then read a machine-readable descriptor the device itself
  ships, **`GarminDevice.xml`** (`<MassStorageMode><DataType><Name>FitnessHistory</Name>
  ...<Location><Path>...`), which declares the *actual* data path + file extension for that
  specific unit (`FitnessHistory`/`FIT_TYPE_4` → activities path, `GPSData` → GPX path).
  Only fall back to a hardcoded guess (`Activities` or `History`) if the XML is missing or
  fails to parse. This should replace/supplement the hardcoded NewFiles/GPXActivities/
  GPX-Current path list above as the primary discovery method - more robust, and it's
  exactly the kind of "ask the device what it actually is" approach this project already
  prefers (e.g. `0x0b21` memory-map query over hardcoded offsets, for the Ambit3).
- **Garmin's USB Vendor ID is `2334` decimal / `0x091E`** (confirmed live in both codebases'
  `device_filter.xml` and code-level `getVendorId()` checks) - directly reusable for the
  same `device_filter.xml` + `USB_DEVICE_ATTACHED` auto-launch wiring the Ambit3 support
  already has (`AmbitUsbModule.kt`), just a different VID.

Also confirmed real and worth keeping in scope: `Current.gpx` (older-generation "currently
recording" track, kept separate from stored history) is a genuine, explicitly-handled
special case in the carlopescio codebase - matches what André described for old eTrex.

For FIT parsing specifically: carlopescio embeds Garmin/Dynastream's own official FIT SDK
rather than hand-rolling a decoder - confirms using an existing FIT library (once we're at
that step) is the right call, not writing a parser from scratch, consistent with this
project's general "prefer known formulas over derived custom math" practice.

**Licensing note:** none of these four apps have a real license or third-party attribution
(closed, unattributed indie apps - confirmed no LICENSE files, no credit for bundled XStream/
FIT-SDK/crash-reporting-SDK code). Nothing here is safe to copy code from directly - only the
patterns above (GarminDevice.xml discovery, VID, Current.gpx handling) are being reused as
concepts, not as ported code.

## Device-side folder structure (confirmed from Garmin's own manuals + André's direct
hardware knowledge — do not trust one source over the other where they might overlap
un-verified)

**Newer eTrex (22x/32x and similar current-generation models):**
- `Garmin\NewFiles\` — recorded activities, FIT format (the default recording format)
- `Garmin\GPXActivities\` — recorded activities, GPX format, only if the device is
  configured to record in GPX instead of FIT
- `Garmin\GPX\` — imported/geocaching GPX (NOT recordings — wrong folder for this feature)

**Older eTrex (10/20/20x/30/30x) — corrected by André, 2026-08-07, real hardware knowledge:**
- `Garmin\GPX\Current` — recorded activities. Different convention entirely from the newer
  models above; the importer needs to check both locations, not assume one.

**Multi-volume complication (André, 2026-08-07):** the relevant folder can live on either
the device's **internal memory** or an **SD card inserted inside the device** — when
connected over USB, these can enumerate as two separate mass-storage volumes. The importer
needs to search all currently-mounted volumes for the expected folder(s), not assume a
single fixed mount point.

**Not yet checked:** which folder the CURRENT eTrex SE/Solar models use (mentioned in
Garmin's manual during initial research, not yet cross-referenced against the
NewFiles/GPXActivities vs GPX/Current split above) — needs the same "confirm against real
manual or real hardware" treatment before the importer's folder-search list is considered
complete.

## Real scoping constraint: USB Mass Storage vs MTP

Garmin devices that mount as **MTP** (Media Transfer Protocol) instead of USB Mass Storage
are explicitly out of scope for this feature as currently conceived — MTP is a materially
different protocol (not a real block-device filesystem), and Android's handling of it
programmatically is a separate, harder problem. Confirmed from real user reports that MTP
Garmin devices simply don't work with the MSC-based approach at all. The eTrex series
(the stated target) mounts as MSC, so this shouldn't block the primary goal, but the
importer should detect device class and fail with a clear message rather than silently do
nothing if a user plugs in an MTP device.

## Two possible implementation paths — not yet decided which we need

**Path A — rely on Android's own OS-level USB Mass Storage automount.** Most Android
configurations with USB OTG host mode already auto-mount MSC devices as a normal browsable
volume, reachable through the Storage Access Framework (`ACTION_OPEN_DOCUMENT_TREE`) exactly
like `AmbitUsbModule.kt`'s existing `pickGpxFile()` already does for manual single-file
picking. If this works reliably on the target hardware, **no new native USB code is needed
at all** — just a folder-tree picker instead of a single-file picker, searching the picked
tree for the known subfolders above.

**Path B — `libaums`, raw USB Mass Storage/SCSI/FAT access, no OS automount involved.**
Needed only if Path A doesn't work reliably on the target OS (BlissOS on the Panasonic
FZ-M1 is a community Android-x86 build, not guaranteed to have full USB MSC automount
support the way a stock/OEM Android image would) or if we want auto-detection on plug-in
without the user manually opening a file/folder picker first (mirroring the existing
Ambit3 `USB_DEVICE_ATTACHED` auto-launch behavior).

**Decisive first test, partially done 2026-08-07:** André's real eTrex 30, plugged into a
Linux **desktop** (not the Android tablet), auto-mounted cleanly via `udisks2` with zero
special handling. That's a real, positive data point for Path A, but it's not conclusive for
Android/BlissOS specifically - desktop Linux's storage stack (`udisks2`) is a different, more
permissive automount path than Android's. **Still not done: the same test on the actual FZ-M1
tablet target.** Until that's run, treat Path A as "worked once, on the wrong OS" rather than
confirmed for where this feature actually needs to run.

## Open questions / not yet done

- [x] ~~Run the auto-mount test above on real hardware~~ - done on Linux desktop 2026-08-07,
      auto-mounted cleanly. **Still needed: the same test on the FZ-M1 Android tablet**,
      which is the one that actually decides Path A vs Path B for this feature.
- [ ] Confirm the eTrex SE/Solar folder convention (NewFiles/GPXActivities vs GPX/Current) -
      not resolved by today's eTrex 30 test, which is a different, older model generation.
- [x] ~~Implement `GarminDevice.xml` discovery~~ - strategy confirmed correct against real
      hardware 2026-08-07 (see above), not yet wired into any actual app code.
- [x] Confirm Garmin's USB VID (`0x091E`/2334) - confirmed exactly, `lsusb` on André's real
      eTrex 30 reports `091e:2519 Garmin International eTrex 30`.
- [ ] Decide FIT-only, GPX-only, or both — the app already has FIT-writing code
      (`FitExport.ts`) and GPX-parsing code (`RouteGpxParser.ts`/`GpxParser.ts`) to draw on
      either way, but parsing/importing FIT *from* a device is new (existing FIT code in
      this project only writes FIT, doesn't parse it).
- [ ] Decide how the "find recordings" search behaves when multiple volumes and/or multiple
      candidate folders are present — search all of them and merge results, most likely.
- [ ] UI: where this lives (new screen? a button on an existing screen?), matching the
      project's established "one file per format, new screen per major feature" convention
      already used for Route/POI.
