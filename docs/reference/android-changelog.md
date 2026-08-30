# Changelog

All notable changes to the AmbitApp Android app (fork of `guiguoz/opensportsync`) are
recorded here, newest first.

## 0.2.19 (2026-08-30)

### iOS: fix BLE sync with real Ambit3 hardware

The iOS watch connection would pair and subscribe but then hang and fail the device-info
handshake ("NSP device handshake failed"). Root-caused from the Suunto-app packet capture
(`tools/ble_pklg.py` + tshark): the NSP bootstrap is **phone-first**, not watch-driven — right
after the watch subscribes, the app must send `0x0000` device_info (payload `82 06 08 00`), and
only then does the watch reply its `0x0002` hello. Our handshake was waiting for the watch to
speak first, so both sides sat silent until timeout.

- `protocol_ble.c`: send the `0x0000` opener proactively at handshake start (iOS only; Android's
  separately-proven path is unchanged).
- `AmbitBleModule.swift`: the NSP notify/write characteristics now **require encryption**, so the
  watch pairs/bonds (LE Secure Connections) exactly as it does with the Suunto app — the capture
  shows the watch won't run NSP over an unsecured link. Added GATT read/write tracing.

Confirmed connecting on a real Ambit3 over BLE on iPhone.

## 0.2.18 (2026-08-30)

### Offline maps polish + POIs on more maps

- **Desktop: saved-areas manager.** The desktop Offline maps page now names, lists and deletes
  downloaded areas just like mobile — a name field, a **Saved areas** list (size + Delete, tap a
  row to fly to it), backed by `TileCacheService` (QSettings metadata; deleting an area removes
  only the tiles no other saved area still needs).
- **Desktop: the map provider is remembered** across restarts (was in-memory only).
- **POI pins on more maps.** The watch's cached waypoints are now shown as pins on the mobile
  **weather-along-route** map and the **Offline maps** browsing map (they were already on the
  activity map) — all offline.

## 0.2.17 (2026-08-30)

### Offline maps — download any area of the world (iOS + Android + desktop)

OruxMaps-style offline maps: pick **any** area anywhere (be in France, download Utah), see the
size, download it, and use the map with no signal — plus a saved-areas manager. Same feature on
iPhone, iPad, Android and the desktop app.

- **Bundled the map engine (Leaflet 1.9.4) into the app.** Previously the mobile maps loaded
  Leaflet from an Android-only `file:///android_asset/leaflet` path whose files weren't even in
  the repo — so the activity map / POI picker were broken as vendored and **iOS had no map at
  all**. Leaflet is now inlined (`leafletInline.ts`) and every map WebView loads from a
  caches-dir `file://` page with per-platform read access — so **maps now render on iOS/iPadOS**
  and read cached tiles off disk for offline use. (Leaflet credited; BSD-2-Clause.)
- **Offline maps screen** (Home → Offline maps): pan/zoom anywhere, a box marks exactly what
  saves, pick a **Detail** level (zoom presets), see a live **"N tiles · ~X MB"** estimate,
  download with progress, and manage **saved areas** (size + delete; delete frees only tiles no
  other saved area still needs). Downloaded areas render with **no signal**.
- **POI pins on the activity map** (the watch's cached waypoints, shown offline).
- **Desktop**: a new **Offline maps** page in the nav rail — search or pan to any area, frame it,
  pick detail, see the size, download. The C++ tile downloader gained optional zoom levels + a
  tile-count estimate; the existing route-area "Download for offline" button is unchanged.

## 0.2.16 (2026-08-30)

### Weather along a route — map + GPX import (iOS + Android)

- **Map of the coloured track.** The route now renders on a real Leaflet map (OSM tiles),
  drawn as one polyline per temperature run — the same temperature palette as the elevation
  profile — with per-point **wind arrows** (tinted head/cross/tailwind, pointing where the wind
  blows) and ● start / ● finish markers, auto-fit to the route. Leaflet and tiles load over the
  network, which this screen already needs for the forecast (no offline dependency added).
- **Load a GPX.** A **Load GPX** button imports a route/track file to plan against, replacing
  the demo route. On iOS this is a new native `UIDocumentPicker` implementation of
  `pickGpxFile` (`asCopy`, returns a real on-disk path the JS layer reads with RNFS) — the same
  entry point route upload already uses; cancel is a silent no-op. `<rte>` and `<trk>` GPX are
  both accepted (via the existing route parser).

## 0.2.15 (2026-08-29)

### New, stable (iOS + Android)

- **Weather along a route.** The weather/weather-analysis half of the desktop Plan page, ported
  to mobile. Give a start time and pace; the app resamples the route by ETA, fetches the live
  Open-Meteo hourly forecast at each point, and shows: a plain-language **verdict** (e.g. "bring
  a headlamp — you finish after sunset"), summary chips (finish time, temp range, rain, wind),
  a **temperature-coloured elevation profile** with rain bars and a sunset marker, and a full
  **sun/moon panel** (sunrise/sunset, the three twilights, solar noon, moonrise/set/transit,
  phase + illumination). Wind is classified head/cross/tailwind relative to travel bearing.
  Reached from Home → **Weather**; ships with a demo route so it works with no watch and no GPX.
  - The two computation cores are **faithful ports verified against their Python references**:
    `src/services/Astro.ts` (byte-identical to `tools/astro.py`, Schlyter sun/moon formulae) and
    `src/services/WeatherRoute.ts` (matches `tools/weather_route.py`'s self-test).
  - Offline routing is intentionally **not** on mobile (no on-device engine on iOS); only the
    weather/analysis portion of the Plan page is delivered here.

## 0.1.1 (2026-08-14)

Desktop→Android feature parity, plus an opt-in **Experimental** batch (App Zone, Intervals,
Smart Sensor) for community testing. Both apps compile (Android APK + desktop Qt binary).

### New, stable

- **Totals screen.** Yearly hours-outside (GPS activities), distance grouped by sport (tap to
  feature run/bike, or any sport), and the same sourced "funny equivalents" as desktop (Moon
  and back, marathons, CO₂, pastéis de nata…). Derived from activities already synced; a year
  picker only offers years with data. Reached from the Activities screen header. (Energy shows
  a note — Android's GPX-derived activities carry no kcal yet.)
- **Calendar screen.** Month grid with a per-sport coloured dot per day, sized by how much was
  done, grey rest-day dots, today ringed. Uses the same 84-sport colour table as desktop.
- **Sport modes: create, delete, and multisport.** The Sport Modes screen can now add a sport
  mode, delete one, and build/delete multisport combos (with an ordered legs picker) — not just
  rename/edit. Every byte written is **proven byte-exact against SuuntoLink's own captures**
  (16/16 real transitions replayed in an in-repo test); the region is re-encoded and checked
  before any write, and re-read after. Cable or Bluetooth. (First real-watch write is gated
  behind a clear warning — the payload is proven, the on-device write is new on Android.)
- **Removed Livelox** integration (service, OAuth, deep link, UI) — unused.

### Experimental (opt-in — Settings → Experimental, OFF by default)

Cable-tested, community-feedback features behind one toggle, each with a visible "unproven,
back up first" warning. Nothing here is on by default.

- **App Zone (Suunto Apps).** Install Suunto Apps onto the watch. AmbitApp ships **no** Suunto
  content (proprietary, and the App Zone service is long dead) — you **import your own**
  SuuntoLink `suunto-apps/index.json` (parsed natively into a compact local catalog), then
  browse/search and install onto a sport-mode screen. The Apps-region build and the
  CustomModes render shortcut are **proven byte-exact** against SuuntoLink's tooling.
- **Intervals workout.** Build an interval workout and either (a) generate its App-Zone source,
  compile it yourself on a third-party community site in your own browser (the app ships **no**
  compiler key and makes **no** automated call — it only generates the source and opens the
  public site), then import the compiled result and install it; or (b) write a native planned
  move. Both flagged unproven: the compiled-app install path has a known unresolved on-hardware
  "app error", and the planned-move format may not surface on the watch. The generated source
  and blobs are proven to match the reference tools.
- **Smart Sensor.** Read a Suunto Smart Sensor HR belt over Bluetooth — manufacturer, model,
  serial, firmware, battery, live heart rate — and Forget (unpair). Read-only; a separate BLE
  peripheral, independent of the watch.

### Under the hood

- New native `writeRegion` (C/JNI/Kotlin/TS) — a generic flash-region writer generalising the
  proven CustomModes writer (used-extent SHA-256 + data-tail, no commit), shared by App Zone
  and Intervals. New native BLE Smart Sensor module and a native streaming `index.json`
  extractor. 30 in-repo byte-exact tests cover the sport-mode, App-Zone, App-install,
  training-program and workout-source codecs.
- **Desktop parity:** the Qt app's App-Zone picker gained an **Import from SuuntoLink** button
  + a status/instructions notice (works on Linux too, where SuuntoLink isn't installed — copy
  the `index.json` over), via new backend `/api/apps/catalog_status` + `/api/apps/import`.

## 2.5.15 (2026-08-09)

Settings for the whole Ambit family, and a tidier settings screen.

- **Ambit 1 / Ambit 2 settings (read-only).** These 2012-era watches (Ambit, Ambit2,
  Ambit2 S, Ambit2 R — USB-only) use the legacy `personal_settings` mechanism, not the
  Ambit3/Kailash SBEM one. The app now reads and displays their settings (units, formats,
  backlight, tones, GPS format, language, alti/baro, etc.) via libambit's existing
  `personal_settings_get`, decoded with labels from openambit and the Movescount emulator
  schema (`assets/`). Read-only for now — there is no verified write path for these, and we
  won't invent an unverified whole-blob write to a 2012 watch.
- **Traverse / Traverse Alpha settings are read-only too.** They read fine with the Ambit3
  table (same openambit driver), but aren't editable yet — they have model-specific features
  (POD/backlight differences, Alpha shot-detection) still to map from a real descriptor.
- **Settings screen: Watch Settings card moved up** to right after Appearance, and its title
  is **device-name-adaptive** — "Suunto Kailash Settings", "Suunto Ambit 2 Settings", etc.,
  from the connected watch, instead of a hardcoded "Ambit3".
- Saved the Movescount emulator (`assets/Movescount_Emu/`) as a settings-schema reference.

## 2.5.13 (2026-08-09)

Kailash activity export over Bluetooth, plus BLE device-info fixes and a responsive
home screen.

- **Kailash GPS activity → GPX over BLE.** A connected Kailash now shows its recorded
  activity and exports the track to Downloads as GPX, read over the live Bluetooth
  session. This uses the watch's ephemeral `sml.DeviceLog` (0x53) sample store, which
  only returns real samples over an active BLE session (cable reads always came back
  empty) — confirmed on hardware, decoded end to end (a real ~800 m walk, coordinates
  matching the watch's own last-known location). New `KailashDeviceLogReader.ts` +
  native `readDeviceLogRaw()`; the watch's persistent travel history (0x67) is also
  shown. See `KAILASH-BLE-FINDINGS.md` Finding 7.
- **Fix: Kailash not auto-detected over BLE.** `getDeviceInfo()` rejected on a BLE link
  (it gated on the USB device), so the watch model came back empty and the Kailash
  branch never ran. It now reads the native device info directly and names the watch
  from its codename (Hoopoe → Suunto Kailash).
- **Fix: hardware version showed 0.0.0 over BLE.** The `0x0002` hello carries it at
  offset 36 (right after firmware); the handshake now reads it. Reports 72.1.0,
  matching the real firmware image — needed to pick the right firmware for download.
- **Responsive connected screen.** In landscape/on a tablet the device cards lay out
  side by side with the action tiles in a single row, instead of one tall column;
  portrait keeps the single-column layout. Adapts by orientation and width.
- Removed the "Ready to pair" confirmation dialog before a BLE scan — one less tap; the
  guidance moved onto the "Connecting via Bluetooth…" screen.

## 2.5.12 (2026-08-08)

Merges two real, independent lines of work that developed in parallel on separate machines/
terminals today, both starting from the same 2.3.4-beta base: the UI/theme redesign below
(2.5.0 through 2.5.10, built in a separate `ambitapp-v2.5.0` working copy specifically so
the other one stayed undisturbed) and the Kailash/CustomModes/Settings feature work (2.5.11,
built directly in this repo). Reconciled via a real three-way merge (base = the shared
2.3.4-beta ancestor, mirrored to Android's own git history at commit `a4d2680`) - most files
merged with zero conflicts since the redesign was deliberately "visual layer only, no
service/handler/state-machine logic changed"; the two real overlaps (HomeScreen.tsx,
SettingsScreen.tsx) were both independent same-spot additions, not competing changes, and
both sides were kept. One follow-up not done as part of the merge itself, on purpose rather
than guessed: the Ambit3/Kailash Settings section and the Sport Modes screen still use their
original hardcoded colors rather than the new `theme/tokens.ts` system - functionally
identical, just not yet visually reworked onto the new palette.

## 2.5.11 (2026-08-08)

Real feature work, sourced from this file's own git history rather than reconstructed from
memory - intermediate version numbers between 2.3.4-beta and this one were not individually
itemized in this file, this entry covers the whole real gap. Same day, this repo's own
`android/` also absorbed `guiguoz/opensportsync`'s real upstream git history via a `git
subtree` import (internal repo housekeeping, not a user-facing change - see
`V3_CHANGELOG.md`'s own dated entry for that).

- **Kailash (7R) support, real and hardware-confirmed**: USB product ID recognition
  (`0x002a`), travel-history and TrackLog sync (visited cities/countries, last known
  location, logbook), and a device-aware Settings screen sharing the same UI as the Ambit3
  (its own separately-curated field table, since the two watches' schemas don't share entry
  IDs even for identically-named fields).
- **Ambit3 Settings UI**: real cable settings read/write (`0x1100`/`0x1101`), confirmed on
  real hardware.
- **Kailash Home Location**: a real settings field (`sml.DeviceSettings.HomeLocation`,
  `Latitude`/`Longitude`) found from real BLE captures and confirmed against the watch's own
  schema descriptor - read+write, range-checked, confirmed-by-reread. See
  `custom_modes_andre.md`'s "Kailash Home Location" section for the full derivation. Not yet
  hardware-tested for the write side specifically.
- **Sport Modes screen (Ambit3-only, CustomModes)**: rename, autolap, HR limits, sensor
  pods, and per-display field type editing - the same real, hardware-confirmed mechanism the
  desktop app already has, ported to native/JNI/TypeScript. The native write path itself is
  not yet hardware-confirmed on this platform specifically (every prior write re-reads to
  confirm; a broken composition would show up as a write that doesn't stick, not a silent
  false "done").

## V2.5.10 - 2.5.10 (2026-08-08)

- Follow-up polish pass on the v2.5.0 redesign, based on real-device screenshots.
- Uniform `ActionTile` height regardless of whether a progress subtitle is showing.
- New `Logo` mark (mountain + "AmbitApp" wordmark) on the searching/connecting/timeout/
  error screens, with a version badge; text on those screens now scales up ~12.5%+,
  further scaled by the device's own screen width (clamped) instead of a fixed size.
- New outlined `watch` (Ambit3 Peak) and `etrex` (Garmin eTrex 10) icons, shown centered
  under the header on their respective connected-device dashboards in place of the generic
  mountain mark; eTrex screen glyph resized down to better match the real device's
  proportions.
- Device info cards (name, battery/sd-card status, "Connected" chip) are now centered
  instead of left/right split, for both the Ambit and Garmin flows.
- Connected dashboard now polls every ~4s to detect a device being unplugged or swapped
  for the other type, instead of only re-checking on screen focus.
- Launcher icon replaced: the old colorful skeuomorphic watch render is now a flat black-
  background / white-mountain-glyph icon matching the in-app `Logo`, regenerated at every
  density (legacy, round, adaptive foreground).
- Theme is now user-selectable — Settings' new top section offers Light / Dark / Follow
  system, persisted via AsyncStorage (`src/theme/ThemeModeContext.tsx`). Previously the
  app only ever followed the OS scheme with no override, so a system-dark device could
  never show the light palette.
- Suunto model names now read "Ambit 2", "Ambit 3 Peak", etc. (space before the digit)
  across the whole PID→name table in `AmbitUsbModule.kt`, not just "Ambit3 Peak".

## V2.5.0 - 2.5.0 (2026-08-08)

- Full UI redesign, built in a new sibling folder (`ambitapp-v2.5.0`, this one) so the
  `opensportsync-main` working copy stays untouched. Visual layer only — no service,
  handler, or state-machine logic changed.
- Replaced the neon-cyan-on-navy look with a flat, grayscale black/white (light) and dark/
  light-grey (dark) system — `src/theme/tokens.ts` + `src/theme/useTheme.ts`, driven by the
  OS color scheme. No accent hue. One muted alert tone exists, reserved strictly for errors
  and destructive actions (a failed operation, deleting a stored API key) — a normal
  connected/done state stays neutral, never colored green.
  Removed the old per-category hue coding (cyan/purple/amber per action type) — it wasn't
  encoding anything real.
- New outlined icon set (`src/components/ui/Icon.tsx`, `react-native-svg`) replacing the `⚙`
  text glyph and bare colored dots throughout.
- New shared presentational kit (`src/components/ui/primitives.tsx`): `Section`, `Button`
  (filled/outline/text), `StatusLine`, `WarningNote`, `Chip`, `Badge`, `IconBadge`,
  `FieldRow`, `ActionTile`, `ExportedFileRow` — every screen now styles itself from the same
  theme tokens instead of ad hoc hex values.
- Home: device card now shows a "Connected" chip (check icon) instead of a separate "Ready"
  status line; the five action buttons are flat bordered tiles instead of neon circles.
- Settings: each integration is a bordered card with an icon badge; "Disconnect" is neutral
  grey (unlinking a service isn't destructive); "Delete" (removing a stored key) uses the
  alert color, since that one actually is.
- Map/Elevation chart: recolored the surrounding RN chrome (stat chips, export menu, replay
  bar) to the new theme; left the Leaflet map's route line and start/end markers alone —
  real cartographic markers, not UI chrome.

## V2.3.4 beta - 2.3.4-beta (2026-08-07)

- Fixed "activities disappear after unplugging the device": they never actually did — the
  local SQLite DB and on-disk GPX files are untouched by device connection state, and
  LogListScreen already rebuilds its list from disk on every visit regardless. The real bug
  was a navigation dead-end: unplugging a device sends Home back into its
  searching/timeout/connect-error states, none of which had a way to reach "View
  activities" — only "Connect device later" did. Added a direct View Activities link to
  all three of those states so it's always reachable, not just after that one specific tap.

## V2.3.3 beta - 2.3.3-beta (2026-08-07)

- Ambit firmware Backup screen now opens the system "Save as" picker for the downloaded
  file instead of silently writing it to app-private storage — Downloads is the picker's
  default location, but any folder can be chosen. New generic `saveFileAs()` native call
  (Storage Access Framework `ACTION_CREATE_DOCUMENT`).
- Investigated "Garmin and Suunto both ask for USB permission every time" — this is
  Android's own device-access security prompt, not something this app added, and there is
  no supported way for a regular app to skip it entirely. In normal use it should only
  appear once per device: the system's own permission dialog has a "use by default for
  this device" option, and once granted, reconnecting the same device won't prompt again
  until the app is uninstalled or the grant is revoked. No code change — during this
  session's frequent uninstall/reinstall testing cycle, every reinstall wipes that stored
  grant, which is why it looked like it was prompting "every time."

## V2.3.2 beta - 2.3.2-beta (2026-08-07)

**Automatic connecting flow** — Home no longer has manual "Connect"/"Garmin" buttons.
Plugging in a watch or Garmin device now drives a single flow: "Searching for your
device…" → real connect (with Garmin's up-to-45s mount wait shown live) → device info
and the right menu, automatically. A 15s no-device timeout and a "Connect device later"
option (view activities + settings only) cover the no-hardware case.

**Ambit device info + firmware backup** — Home now shows the watch's name, battery
level, firmware version, and hardware version, same as Garmin already did. New
`getDeviceInfo()` native call (`CMD_STATUS`/0x0306 for battery, on top of the existing
device-info reply) backs this. A new Backup screen checks Suunto's live firmware-update
service and can save the firmware file locally — clearly marked backup-only: the file is
a proprietary Suunto container (not a real zip despite the name), so this app has no way
to flash it back onto the watch.

**Renamed menu buttons** — Ambit: Activities / Routes / POIs / Backup. Garmin: Sync
Activities / Routes / POIs.

**Garmin menus split to mirror the Ambit ones** — the three Garmin buttons used to all
open the same combined screen. Now: "Sync Activities" reads and logs activities directly
from Home, no sub-screen, same as Ambit's Activities button. "Routes" opens a screen with
just Send a route (GPX → SD card) and Export routes (reads saved GPX files from
`Garmin/GPX` on both internal memory and SD card, saves to Downloads, with a per-file
Share… for choosing another destination). "POIs" is its own screen with Send a POI (same
SD-card GPX mechanism) and Retrieve POIs (reads BaseCamp's `Waypoints*.gpx` files the same
way). New native `listGpxDirFiles`/`readGpxDirFile` calls back this.

- Garmin mass-storage mount can take up to ~40s after the USB link comes up; `connect()`
  now retries for up to 45s with live progress instead of failing on the first attempt
- Removed the "Test Bluetooth connection" debug button from the Route screen (BLE
  connect/pairing is exercised directly through Send/Export now, no separate probe needed)

## V2.3 beta - 2.3.0-beta (2026-08-07)

**Garmin support (new device family)** — detects whether a connected USB device is an
Ambit/Traverse or a Garmin (eTrex series) and routes to a separate, purpose-built feature
set for Garmin, since it works completely differently (plain GPX files on a FAT filesystem
via USB Mass Storage, not the Ambit3's NSP flash protocol):
- Device identification: model, firmware version, part number, read from the device's own
  `GarminDevice.xml` descriptor — no hardcoded lookup table
- Import recorded activities (reads the device's resolved GPX/Current-equivalent folder);
  imported activities join the same local activity list as Ambit-sourced ones, so FIT
  export and all existing 3rd-party sync (Strava/Runalyze/Livelox/Intervals.icu) work
  identically, with no new sync code
- Upload a GPX file (route or POI — same file format either way) to the device — **SD card
  only, by design: never writes to the device's internal memory**, with an explicit in-app
  warning; the feature is disabled entirely if no SD card is detected
- Built on `libaums` (Apache 2.0) for USB Mass Storage access, since stock Android has no
  built-in support for arbitrary USB-OTG mass storage devices
- See `GARMIN_USB_IMPORT_SPEC.md` for the full research trail (real hardware: a Garmin
  eTrex 30) and open items (SD-card-write behavior on a real populated card, and other
  eTrex generations' folder conventions, are not yet verified on hardware)

**Bluetooth support for Ambit3/Traverse (from v0.3.0, carried into this build)** — send/
read route, POI, orbital data, activity sync. Experimental, gated behind a clear in-app
disclaimer. Protocol layer (frame format, command set, Service Changed handling) is
confirmed against a real hardware capture; the BLE connect/pairing step itself is still
being debugged on real hardware and not yet fully reliable. See `HANDOFF.md` in the
ambit-app research project for the detailed status.

## V2 - 0.2.1

- Orbital (AGPS) data download and update
- POI import (GPX file and manual coordinates)
- POI export
- Activity export as FIT file
- Third-party sync to intervals.icu
- Route import via GPX, sent to the watch
- Route export: read routes/waypoints from the watch, save as GPX
- Forced English translation (removed inconsistent partial French)
- New adaptive app icon
- `armeabi-v7a` and `x86_64` build targets added, alongside `arm64-v8a`

## V1

- Ambit3 family support added: retrieve recorded activities over USB OTG

## V0

- Fixed the fork so it launches at all (upstream `opensportsync` shipped a debug-only build
  with no JS bundling step, crashing on start)
