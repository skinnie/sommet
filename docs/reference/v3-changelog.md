# AmbitApp V3 changelog

Tracks work that goes beyond AmbitApp V2's initial scope (see `AMBITAPP_SPEC.md` and
`ambitapp-v2/README.md` for what V2 itself is) - the real capabilities layered on top as
they land, on the way to what André/Vincent have been calling "V3": wireless sync
(Milestone 7/BLE) and anything else V2 deliberately deferred. Newest entries first.

---

## 2026-09-04: Windows packaging fix, take 2 (0.2.32)

The Windows `.zip` now always builds. v0.2.31 got past the CMake error but then hit a compile
error building the vendored Ambit1/2 helper on Windows (`le16toh`/`htole32` don't exist under
MinGW — the same missing-`<endian.h>` class of break macOS had). Fixed with a Windows endian
shim, and the helper's build step is now non-fatal: if that vendored code ever fails to compile,
the Windows app still ships (Ambit3/Traverse/Kailash unaffected; only Ambit1/2-over-USB on
Windows would degrade, exactly as before the step existed). Otherwise identical to 0.2.30/0.2.31.

## 2026-09-04: Windows packaging fix (0.2.31)

Same as 0.2.30, plus a Windows-build fix: the Windows desktop package failed to build the
Ambit1/2 helper (`ambit_legacy_cli`) because the vendored libambit's old CMake minimum is now
rejected by the CI's CMake. The build script already worked around this on macOS; it now does so
on every platform, so the Windows `.zip` builds again. No app behaviour change from 0.2.30.

## 2026-09-04: average-user UX pass, Training Program native cards, morning HRV (0.2.30)

A release focused on making the app clearer for a non-technical owner, plus two feature
additions. All new watch-facing features default OFF or are opt-in, so nothing changes for an
existing setup unless you turn it on.

**Usability pass (from a full average-user audit; desktop + Android):**
- **Copy one watch to another** — the old two-watch "freefly" Sync page was rewritten as a
  guided flow: plug the watch to copy *from* (read automatically), swap the cable, plug the
  watch to copy *to*, review exactly what will change, then one **Write** button. No more
  mirror/merge/snapshot jargon or greyed-out placeholder options.
- **Activity detail** — tabs now appear only when that activity actually has the data (charts
  when it logged app outputs, etc.); no more empty "not built yet" tabs.
- **Routes** — removed the confusing "Rehearse (no write)" button; it's just one **Upload to
  watch**, which validates before writing.
- **Sport Modes** — screen/field edits now save themselves (no separate "Save to watch").
- **Training Program** — a single **Sync to watch** button.
- **Totals** — removed the "More to come!" placeholder.
- **Coach** — clearer wording that AI chat needs a developer API key from console.anthropic.com
  (separate from a claude.ai login).
- **Connections** — one-tap "Open Settings → Connections" from the Health / Weight / Coach /
  Gear empty states.
- **Feedback** — invalid coordinates, over-long names and bad dates now say what's wrong instead
  of silently reverting; backend errors show a friendly line with an expandable **Details**.
- **Feature discovery** — the workout builder, App Zone and the HR-belt features are on by
  default now (T6/X6 and GPS Pod stay off); watch-only menu items are hidden when no watch is
  connected.

**Training Program — native "Today" planned-move cards:** one **Sync to watch** now writes both
the WORKOUT-menu guided workouts *and* the watch's own "Today 1/2" planned-move cards shown on
the time screen.

**Morning HRV from a heart-rate strap (opt-in):** measure morning HRV (RMSSD) from a COOSPO/BLE
chest strap on desktop and Android — no watch needed. Off by default; enable in Settings → Health.
Desktop measuring needs the `bleak` Python package.

**Fixes:**
- **Two watches at once** — with a Peak and a Sport (or any two Ambit3) plugged in together,
  activities now import from **both**, not just the selected one. Each watch is read scoped to
  itself and its activities are kept under its own device, so neither clobbers the other.
- Locale-dependent GUI text, Ambit1/2 GPS-orbit status, and the Windows legacy-CLI build.

## 2026-08-30: two-watch "freefly" sync (0.2.16)

André: "we have the option to backup our watch... would be nice both [watches] had the same
sports modes, settings etc." A new **Sync watches** page copies data between two watches of a
compatible model. One watch connects at a time, so the flow is sequential: snapshot watch A,
swap the cable, snapshot B, preview the diff, plug the target and apply. Every write reuses an
already-proven per-category path - no new watch-write mechanism was invented - and the backend
re-checks the connected serial before every write (409 SYNC_TARGET_MISMATCH), so a plan built
for one watch can never be applied to another.

- **Backend `/api/sync/{snapshot,state,plan,apply,clear}`** (`desktop/backend/server.py`),
  category-generic. Four categories, all hardware-verified on a real Ambit3 Peak pair + Kailash:
  - **Settings** (0x1101) and **Sport modes** (whole CustomModes+Apps region mirror,
    byte-exact readback via new `tools/sync_write_sportmodes.py` + `dump_sportmode_regions.py`):
    **same model only** - sensors/hardware differ (Ambit3 Peak → Peak, Sport → Sport, ...).
  - **POIs** and **Routes** (item-list union / add-missing): allowed **across** models too, as
    plain geographic data, when both watches support them.
- **Mirror / two-way-merge** modes, A→B / B→A direction, per-category selection, a grouped
  dry-run **preview** before any write, and the serial guard. Desktop `SyncPage.qml` +
  `SyncService`.
- **POI write bug found & fixed (the hard one).** The Ambit3's `0x0b25` POI write only ever
  **appends** on its own - it never replaces or shrinks the list. The clear is the nav-region
  commit that must run *first*: `0x0b16` writes + `0x0b18` tail + **`0x0b04` commit** (which
  clears the POI SBEM region), *then* `0x0b25` sets the whole list - the SuuntoLink `poiimport`
  sequence. New `write_nav.py --set-pois-json` does exactly this (empty list = clear all), and
  packs POIs into ≤254-byte single-byte-length entries, never the extended `0xFF+u32` header
  (which the watch only emits on read and corrupts on write). Verified live: a watch left with
  34 duplicate POIs was cleared and set to a clean 9, routes untouched.

## 2026-08-30: back up the app's own database (activities + gear) to any folder (0.2.15)

André: "I believe we have backup of database, but I believe we don't know, can't choose where
to save the database". Investigation confirmed the gap: every existing "Backup" card saves the
connected **watch's** nav regions - the app's own SQLite databases (`activities.db`, which is
also where each activity's GPX/FIT text lives, and `gear.db`) had **no** backup path at all. On
desktop that made `activities.db` a silent single point of failure.

- **New "Your activities & gear" card on the Backup page**, always visible (app data exists
  with or without a watch). Two actions: *Create backup now* (writes to `~/AmbitAppBackups`) and
  *Save to a folder…* (a `FolderDialog` - point it at a Dropbox/OneDrive/Drive-synced folder for
  keyless cloud backup, the same model as the watch-backup cards), plus *Open backup folder*.
- **`LocalFileService::backupDatabase()`** - pure local copy, no watch and no Python backend, so
  it works offline and with nothing plugged in. Writes a timestamped `sommet-data-YYYYMMDD-HHmmss/`
  folder containing `activities.db` (+ `gear.db` when present).
- **Consistent snapshot via SQLite `VACUUM INTO`** from a private second connection, not a raw
  file copy - can't tear even if a sync is writing the live DB. Verified: snapshot passes
  `PRAGMA integrity_check`, all 41 activities carried across with their `gpx_text` intact.
- Still **open** (tracked): a setting to relocate the *live* DB/tile cache, and a maps
  backup/destination option - see the storage-location roadmap issues.

## 2026-08-13: native Training program (planned moves) breakthrough + Suunto nomenclature

Two related pieces of work; see `training_program_andre.md` Finding 59 and its Nomenclature
block for detail.

- **Nomenclature made coherent with Suunto's own manual terms**, across the docs: **Training
  program / planned moves** (§3.39, native dated targets in the `TrainingProgram` flash
  region), **Interval workouts** (§3.18), **Suunto Apps** (§3.35). "Training plan" was our own
  drift and is retired. This also disambiguates the two features that had collided under the
  name "Training Program".
- **The Finding 58 desktop feature is a scheduled *Suunto App*, not a §3.39 Training program** —
  a workout gated on `SUUNTO_DAYS_AFTER_1_1_2000`, pinned to a sport-mode data field. Its page/
  service/routes (and `tools/training_plan.py`) are mislabelled; rename pending, feature set
  aside by André for now.
- **Native Training program / planned moves — real progress on the harder, previously-walled
  feature** (`tools/training_program.py`, the native region):
  - The region write is now **proven firmware-accepted** — the 0x0b21 `TrainingProgram` hash
    (empty sentinel when erased) changes after a write to SHA256 of the used extent, exactly.
    Findings 30–32 never ran this check; the write mechanism was never the problem.
  - **The header base date is a packed calendar date `[u16 year][u8 month][u8 day]`**, decoded
    from `TrainingProgramAreaConverter::createBinary` (its date helper is a Julian-Day-Number →
    Gregorian converter). Every prior test wrote a raw timestamp there, so the firmware read a
    garbage date — the real reason the feature never surfaced.
  - A decompile-correct, firmware-accepted write still did not surface a Today target on
    hardware; remaining suspects are a same-day date match, per-item validity, or an
    app-triggered re-parse. Region restored to empty; watch left clean.

## 2026-08-12: sport-mode creation/deletion and the multisport format, from André's new capture

- **`tools/sport_mode_manage.py`** - new tool, one file for this format per the project's
  own rule. Creates and deletes sport modes, and creates, edits and deletes multisport
  combos, reusing `custom_modes`/`custom_modes_write` and `write_nav.send_plan`, with the
  same "refuse to write what we cannot re-encode byte-exact" gate `custom_modes_edit.py`
  uses. `--selftest` replays André's `removeandaddsportsmodeandmultisport` capture and
  reproduces **all 16 real SuuntoLink transitions byte-exact** - the create/delete rules
  are SuuntoLink's, demonstrably, not ours.
- **The choreography, from the capture**: creating a sport mode is THREE region writes
  (append with no displays and `AppMeta {now, 0}`; the same mode with its 8 default
  displays and `{now, now+2}`; then the `SPORT_MODES` menu entry). Creating a multisport
  combo is ONE write and creates no exercise mode at all - a combo is purely a
  `SPORT_MODES` entry pointing at modes that already exist. Deleting a mode renumbers
  every leg index above it, which is the part that had to be got right.
- **The limits, now sourced rather than guessed**: 10 sport modes with a combo spending
  one of them (SuuntoLink's own `countSportModes`), 2 combos, 2-6 legs with repeats
  allowed, and a transition being an ordinary sport mode (activity 99) you create first
  that also costs a slot. Only three activities can be containers - Multisport, Triathlon,
  Adventure racing - from `activity.js`'s `getMultisportPhrases()`.
- **Two long-open questions closed as a side effect.** `UseHw 0x0004` is
  **UseAccelerometer**, not an unidentified sensor (`sport_mode.js` exports
  `useAccelerometer()`, and it matches the bit on all 11 modes), and `0x0080` is the
  **cadence pod**, which was missing from the list entirely. Separately, the old "Cycling
  decodes to 15 displays but the UI says 8/8" puzzle is answered: only `Type=10` displays
  are user screens, and counting just those reproduces SuuntoLink's own per-mode screen
  counts exactly.
- **`assets/sportmode_rows.json` regenerated** with three additions, all from SuuntoLink:
  `activityDefaults` (per variant, per activity - what a freshly created mode starts out
  as), `multisportActivities`, and the 2/6 leg bounds.
- Docs: full derivation appended to `custom_modes_andre.md`; `BUGS_ANDRE.md` items 20, 21
  and 23 updated - the format work is done on all three, what remains is the desktop and
  Android UI.

## 2026-08-11: BLE settings writes proven real; a route-write data-loss bug found and fixed; BLE scoped to an off-by-default Experimental Feature, macOS/Windows dropped

- **Settings writes over BLE, visually confirmed**: read the real settings blob, previewed
  a dry-run change, then sent two real writes - `backlight_brightness` and `display_dark`
  (dark -> light) - the watch's own screen visibly changed live over Bluetooth.
- **Real incident**: a route write reported success but wiped two of André's existing
  routes - `build_routes()` rebuilds the entire on-watch Routes region from exactly the
  GPX paths given, and neither the new BLE path nor the existing USB path had ever
  included what was already on the watch. Not a BLE-specific bug - identical gap existed
  over cable, just never triggered before. Root-caused and fixed at the source: new
  `write_nav.existing_routes_as_gpx()`, read by both `ble_routes.write_route()` and
  `server.py`'s USB handler before every real write, so an add-a-route no longer deletes
  what's already there. Confirmed live after the fix.
- Also found and fixed live: a silent-data-corruption bug in outgoing BLE writes larger
  than one ATT fragment (no pacing between notify() calls - fixed with real per-fragment
  delay), a `ble_bridge.BleBridge` shape mismatch against what `write_nav.py`'s functions
  actually need (`dry_run`/`sent` attributes, `command()`'s full signature), and two
  smaller bugs in the new route-preservation code itself.
- **Real scope decision**: BLE now sits behind a new, off-by-default "Experimental
  Features" toggle (Settings page) - cable stays the default. macOS/Windows BLE backends
  are dropped from scope entirely, not deferred. See `HANDOFF.md` Milestone 7 item 19.

## 2026-08-11: Garmin eTrex manuals linked, new SUPPORTED_DEVICES.md - written, NOT yet compiled/tested

- **Garmin manual links** (André added the two real eTrex guide PDFs to `manualslinks`):
  `GarminService.model`/`GarminModule`'s model field is free text off the watch's own
  `GarminDevice.xml` (e.g. "eTrex 30", "eTrex 32x"), not a codename, and Garmin's own manual
  pages group several models under one guide each - so this matches by family ("22x"/"32x"
  substring -> the eTrex22x-32x guide, everything else in the 10/20/30 generation -> the
  eTrex_10_20x_30x guide) rather than an exact key. Desktop: `HomeViewModel.garminManualUrl`
  + a new Manual field on the Garmin info row in `HomePage.qml`. Android:
  `garminManualUrlFor()` in `config/manuals.ts` + a manual row on the Garmin device card in
  `HomeScreen.tsx`.
- **New `SUPPORTED_DEVICES.md`**: every device this project targets (all 11 Suunto
  codenames + the two Garmin eTrex manual families), its manual link, and how the family
  match works - one place to check instead of re-deriving it from `_modelNames`/
  `manualslinks` each time.
- **Not yet built or hardware-tested** - PROJECT_RULES.md rule 4 (never compile without
  asking).

## 2026-08-11: per-device manual links + Home info layout - written, NOT yet compiled/tested

- **Device -> manual correlation**: new `manualslinks` at the repo root (Suunto's own
  ns.suunto.com Userguides PDF per model), mapped onto this project's existing codename
  table (`_modelNames` in `HomeViewModel.qml` / `SUUNTO_PID_NAMES` on Android) - one entry
  per supported device (Ambit, Ambit2/S/R, Ambit3 Peak/Sport/Run/Vertical, Traverse,
  Traverse Alpha, Kailash). Desktop: `HomeViewModel._manualUrls`/`manualUrl`. Android: new
  `android/src/config/manuals.ts` (`manualUrlFor()`), same table.
- **Desktop HomePage.qml**: the two Ambit3 info `Row`s became one `GridLayout` (André:
  "move clock up, and put the manual next to hardware") - Clock moved up next to
  Battery/Firmware, a new Manual link sits next to Hardware, GPS orbit kept its own row
  rather than being dropped. Exact target screenshot (`Screenshot from 2026-08-11
  19-48-07.png`) wasn't found on disk to check pixel-for-pixel - this is a reasonable
  read of "move clock up"/"manual next to hardware" pending André's visual confirm.
- **Android HomeScreen.tsx**: a "View manual (PDF)" row added to the Ambit device card,
  next to the existing Sync time row (`homeManualLink` i18n key, EN+FR) - Android's info
  card never had the desktop's Serial/Hardware/Clock row layout to begin with, so this is
  parity on function (a manual link exists), not a pixel-identical port of the desktop
  rearrangement.
- **Not yet built or hardware-tested** - PROJECT_RULES.md rule 4 (never compile without
  asking).

## 2026-08-11: desktop BLE reaches the actual UI - CONFIRMED WORKING END TO END

- **New "Connect via Bluetooth" button + passkey dialog on HomePage.qml**, backed by new
  `DeviceService` properties/methods (`connectBle()`/`disconnectBle()`/`submitBlePasskey()`,
  polling `/api/ble/status`). Everything through the previous entries only worked from
  Python scripts directly against the backend - this is the first time any of it is
  reachable from the app itself.
- **`/api/ble/connect` no longer blocks server-side** waiting for the watch to subscribe
  (used to, up to 25s) - a fresh pairing's passkey relay needs longer than that in the
  general case, so the client now polls status itself instead.
- New `POST /api/ble/passkey` endpoint.
- **Built clean on the first try, then confirmed live**: André paired a fresh Ambit3
  through the app's own dialog - screenshot shows `Suunto Ambit 3 Peak, Connected`,
  battery 100%, firmware 2.4.17, hardware 70.2.17414, serial 1849100781. Three more real
  bugs fixed along the way, found only by testing this live: a startup-timing race in
  `/api/ble/connect`'s status reporting, an orphaned daemon process silently blocking new
  connect attempts from binding the control socket, and a stale pairing-agent log message
  still saying "NoInputNoOutput" after item 16 switched to "KeyboardDisplay". See
  `HANDOFF.md` Milestone 7 item 18.

## 2026-08-11: desktop BLE - activity-log transport proven (first request of three)

- New `tools/ble_logs.py` + `GET /api/ble/logs/summary`: the first of three real
  post-handshake `0x1200` requests in the activity-log sequence (found by decoding the real
  Suunto app's own capture). Confirmed live against real hardware: a 670-byte reply
  containing a live timestamp read straight off the watch's clock.
- The other two requests (paginated per-activity entries) are intentionally not
  implemented yet - the capture doesn't settle what ends the pagination loop, and guessing
  risks an infinite or silently-truncated read. See `HANDOFF.md` Milestone 7 item 17.

## 2026-08-11: desktop BLE - fresh pairing solved, clean serial recovered

- **Fresh pairing now works standalone on Linux**: registered a real `org.bluez.Agent1`
  with `KeyboardDisplay` capability (this watch family needs LE Legacy Passkey Entry, not
  Just Works or Numeric Comparison - confirmed from this project's own prior IO-capability
  decode). `RequestPasskey()` blocks until a human reports the passkey the watch is
  displaying, submitted via a new control-socket op / `ble_bridge.submit_passkey()`.
  Confirmed live: watch showed a code, submitted it, pairing completed, real device data
  read straight after - no more depending on the desktop environment's own Bluetooth applet.
- **Clean numeric serial** ("1849100781") now read via a real post-handshake `0x0b1e`
  request, reverse-engineered by decoding the real Suunto app's own capture directly
  (btsnoop + tshark). Previously only the handshake's raw hello id was available.
- **Two real bugs fixed** that were making retries look like protocol failures: the
  scanner's own "already seen" bookkeeping permanently blacklisted the watch after any
  dropped connection instead of allowing a retry, and the daemon subprocess wasn't properly
  detached, so it could die between tool calls independent of anything BLE-related.
- See `HANDOFF.md` Milestone 7 item 16 for the full story.

## 2026-08-11: desktop BLE - real handshake fix, confirmed working end-to-end on hardware

- **Fixed the actual protocol bug**, found by testing the bridge below against a real
  Ambit3: `ServerLink` was sending `0x0000` and waiting for a reply, the USB pattern - over
  BLE the watch pushes first (`0x1201`, then `0x0002` hello) and the phone must answer, not
  ask. Ported Android's already-proven native handshake responder
  (`protocol_ble.c`'s `libambit_ble_handshake_device_info()`) into `ServerLink`, and fixed
  the post-handshake driver-path flags (0x05, not `ble_link.py`'s client-role 0x0A).
- **Confirmed live**: real device identity (`fw 2.4.17`, `hw 70.2.17414` - exact match to
  this project's own documented value) parsed from the handshake, and a real post-handshake
  `0x0306` battery read (99%) - the first watch-facing BLE read to work through the actual
  app. See `HANDOFF.md` Milestone 7 item 15 for the full story, including a real pairing-
  agent gap hit along the way (fresh pairing needs a Bluetooth agent this project doesn't
  register itself yet).

## 2026-08-11: desktop backend can reach the BLE connection, not just USB

- **`tools/ble_server.py` gained a local control socket** (`ControlSocketServer`, Unix
  socket at `~/.cache/AmbitApp/ble.sock`) so it can stay the one long-lived process holding
  the watch's BLE connection open while other processes issue commands through it - it was
  previously only reachable from a terminal.
- **New `desktop/backend/ble_bridge.py`**: starts/stops the `ble_server.py` daemon and
  presents a `Link`-compatible `.command()`, so code that already knows how to read the
  watch's replies (`device_info.py`'s `read_device_info()`/`read_battery()`) works against
  it with no changes.
- **New endpoints** `/api/ble/connect`, `/api/ble/status`, `/api/ble/disconnect` in
  `desktop/backend/server.py`; `/api/device` now answers over BLE when a watch is
  subscribed. Daemon start, socket status, and clean teardown proven end-to-end on real
  Linux/BlueZ hardware this session; the watch-facing reply itself still needs a real watch
  in range to confirm. See `HANDOFF.md` Milestone 7 item 14 for the full architecture and
  what's still open (every other watch-facing endpoint still USB-only; macOS/Windows need
  their own GATT-server backend behind the same bridge interface).

## 2026-08-08: eTrex screen inset further; Workout Builder gets a real light/dark/system theme and text cleanup

- **eTrex icon screen inset further** - the white/grey-framed screen from the same day's
  earlier pass was still touching the body's own outline at this icon's small sizes; shrunk
  (0.74/0.5 of body size -> 0.62/0.42) with a bit more bottom margin so it sits clearly
  inside the frame.
- **`tools/workout_gui.py` (the Intervals launcher's actual UI) reworked, then rebuilt into
  `dist/linux/Ambit3 Workout Builder`** (the packaged binary `IntervalsService` launches -
  without a rebuild the running app keeps serving whatever was packaged before, which is
  exactly what happened mid-session here: the first round of fixes were correct in source
  but invisible until the binary was actually rebuilt):
  - Real light/dark/system theme (CSS variables + a `data-theme` cycle button,
    `light -> dark -> system`, persisted in `localStorage`) - previously just a bare
    `color-scheme: light dark` hint with no explicit light palette and no way to force one.
  - Intro text split into real paragraphs instead of one comma/semicolon-run-on block; the
    "Add to SuuntoLink" / SuuntoLink's own "Add Suunto App" picker relationship got its own
    dedicated paragraph, spelled out as two separate steps (this tool's button, then
    SuuntoLink's own picker) rather than folded into the compile explanation.
    "Important"/macOS-permission/disclaimer text moved out of the top of the page into a
    `#notes` section at the very bottom, so the page opens on the actual builder instead of a
    wall of caveats.
  - New inline Linux note (two paragraphs, split at "Either") explaining the real options
    since there's no native SuuntoLink for Linux: copy the compiled `.json` to a Windows/Mac
    SuuntoLink install, or run SuuntoLink under Wine/a VM on the same machine and do the same
    "Import compiled JSON" + "Add to SuuntoLink" steps there - explicitly marked untested/
    unsupported either way, not a documented path. The old "Open instructions" button (which
    just opened a README file saying roughly the same thing) was removed on Linux along with
    its now-dead backend route/handler (`/api/open-instructions`, `open_path()`) - the
    guidance lives on the page itself now, nothing to click for it.
  - `VARIANT_NAMES` (the compiled-app compatibility list) gets the same "Ambit3" -> "Ambit 3"
    spacing as `ambitapp-v2`'s own `HomeViewModel._modelNames` - the tool's own product name
    ("Ambit3 Workout Builder") is intentionally unchanged, only the Suunto watch model names
    it reports.

---

## 2026-08-08: Intervals menu, Ambit3 Peak icon export, eTrex icon redrawn against a real photo, "Ambit 3" naming

- **New "Intervals" nav entry (Suunto-only)**: launches `tools/workout_gui.py` (the App-Zone
  interval workout compiler, already packaged via PyInstaller - `dist/linux/Ambit3 Workout
  Builder`) as a real detached process rather than embedding a browser view in-app (would
  need `Qt6::WebEngine`, a dependency this project doesn't otherwise carry, just to duplicate
  what the user's own default browser already does once the tool's local server is up). New
  `IntervalsService::launch()` prefers the packaged build for the current OS (checked
  directly - only Linux exists so far), falling back to `python3 tools/workout_gui.py`; the
  repo-root path it needs is baked in at CMake configure time (`AMBITAPP_REPO_ROOT`), the
  same "fixed convention" reasoning `DeviceService`'s hardcoded backend address already uses.
  Live-verified: clicking "Open Workout Builder" opened a real browser tab on
  `127.0.0.1:8765` showing the real tool. New `IntervalsIcon.qml` (four bars, alternating
  low/high heights) redraws `tools/packaging/icon.png`'s own motif as a monochrome
  `color`-driven shape instead of embedding that raster PNG directly - keeps NavItem's
  selected-state color inversion working the same way it does for every other nav icon,
  which a fixed two-tone PNG sitting on a solid selected background would have broken.
- **Ambit3 Peak icon exported to Downloads**: no such asset existed inside `ambitapp-v2`
  itself (it draws the Ambit hero icon as a plain Material Symbols glyph, not a custom image -
  see `HomePage.qml`'s own comment on why a real product photo isn't embedded there, a
  licensing call, unrelated to this). The real one turned out to already be sitting in the
  decompiled SuuntoLink assets, found via the codename match confirmed elsewhere in this repo
  (`ambit3peak_Emu-fw...zip` -> codename "Emu"): `assets/WIndows apps/suuntoapp_local/img/
  watch-emu.png`, copied to `~/Downloads/Ambit3_Peak_Icon.png`.
- **eTrex icon redrawn against a real reference photo** (`etrex10.jpg`) instead of the first
  pass's guessed silhouette: removed the antenna "bump" (a real eTrex 10 has a flat/rounded
  top, no dome - that detail belonged to a different model), added the real side button nubs
  (up/down/menu on the left, back/page on the right) and the backlight-button circle, and
  enlarged the screen to fill most of the lower body instead of a small upper strip. Follow-
  up same day: screen fill changed from solid grey to white with a grey frame, closer to a
  real monochrome GPS LCD than a flat grey block.
- **"Ambit3" -> "Ambit 3" spacing** added everywhere the app displays a Suunto model name
  (`HomeViewModel._modelNames`' whole Ambit2/Ambit3 family, its static fallback, and
  Settings' "Supported devices" list) - explicit request, applied consistently rather than
  just the one Home-screen instance first reported.

---

## 2026-08-08: Real-hardware follow-ups - map zoom actually fits now, Garmin re-detects live, Ambit disconnect no longer gets stuck

- **Map auto-fit zoom, second pass**: the first fix (padding margin 0.8→0.65) still cropped
  the track on one side, confirmed by zooming into the actual screenshot pixels rather than
  guessing ("as you can see the route still doesn't fit totally when you are inside the
  activity"). Tightened the margin further (0.65→0.5) and moved the initial-fit trigger
  (`Component.onCompleted`, `onTrackPointsChanged`) to `Qt.callLater(_refitZoom)` to rule out
  a layout-timing race in deeply-nested items (`Card`→`Column`→`Item`→`MapView`) not having
  their final resolved width/height yet on the first call. Confirmed live against the same
  real eTrex "Current Track" file ("now the map is fixed").
- **Garmin device state going stale on Home** ("I had two devices connected at the same
  time. It only shows one, and it takes a bit of time to re-detect the etrex after I unplug
  the suunto"): `GarminService::detect()` used to only ever run once, from `HomePage.qml`'s
  own `Component.onCompleted` - if you're already on Home when the other device's state
  changes, nothing re-triggers it. Unlike `DeviceService`'s Ambit3 polling (a real USB round
  trip through a Python subprocess, deliberately not polled continuously), `detect()` here is
  a cheap filesystem check (`QStorageInfo` + one small XML file) - now runs on its own
  continuous 2s `QTimer`, decoupled from page navigation, only re-parsing activity/route
  files on an actual connect-state transition rather than every tick.
- **Self-caused regression, found and fixed same day**: the earlier "if watch is connected
  don't refresh, remove the refresh button" change (this same file, 2026-08-08 entry below)
  had no mechanism left to ever notice a *subsequent* disconnection once
  `DeviceService::deviceInfoOk` went true - no timer running, no button to fall back on. Real
  symptom: "it is blocked on ambit connected even if it disconnected." Fixed with a slow
  10s heartbeat (`m_heartbeatTimer`) that only runs while connected, distinct from the fast
  1s "searching" `m_pollTimer` - a real disconnect is now noticed within a bounded time
  without going back to continuous fast polling.

---

## 2026-08-07: Qt's OSM plugin abandoned too - a plain direct tile renderer instead; real connection-speed fix; Runalyze corrected

- **Maps, for real this time**: the "missing API key" watermark survived two separate
  parameter-based fixes against Qt's `"osm"` GeoServices plugin. Before guessing a third
  time, ran `strings` on the actual installed plugin binary
  (`libqtgeoservices_osm.so`) and the core `libQt6Location.so` - **zero** occurrences of
  any documented `osm.mapping.*` parameter name anywhere in either. This Qt 6.12 build's
  plugin doesn't match what's documented, full stop. `MapView.qml` is now a plain, direct
  XYZ slippy-tile renderer - no GeoServices plugin, no `Qt6::Location`/`Qt6::Positioning`
  dependency at all, standard Web Mercator tile math (the same formula every slippy map
  uses), a `Canvas` for the track polyline (a long-stable QML API, not a newer
  version-sensitive one like `QtQuick.Shapes`). Every caller kept the exact same public API
  (`latitude`/`longitude`/`zoomLevel`/`trackPoints`/`showMarker`) - none of them changed.
  CyclOSM's click not doing anything was a real, separate bug in the same batch (see below).
- **A real QML scoping bug, found in the same log check**: `oauthDialogService = modelData`
  inside a `Repeater` delegate's `TapHandler` was writing to the wrong scope entirely
  (`Error: Invalid write to global property`) - needed `root.oauthDialogService =`.
  Silently broke the Runalyze/Strava status dialog and was very likely why CyclOSM's radio
  button appeared to do nothing too (same file, same session).
- **Removed the offline MBTiles checkbox** - a "future, not built" placeholder with no real
  function, asked to be removed rather than sit there half-implemented.
- **Home was slower to show "Connected" than the real Android app - found why, fixed
  properly, not just papered over**: `DeviceService.refresh()` used to also fetch
  `/api/nav` (a full read of the Waypoints+Routes flash regions, ~146KB over USB) purely to
  use its success/failure as the connectivity signal - checked directly, nothing in the UI
  ever showed `navOk`/`navRawOutput` otherwise. `/api/device` (a single ~40-byte `0x0000`
  command, already being fetched in parallel) is a real, much faster connectivity signal on
  its own. Removed the nav fetch from `DeviceService` entirely - `HomeViewModel` now checks
  `deviceInfoOk` instead of the old `navOk`.
- **Real auto-retry added**, matching what the real Android app does on its own Home screen
  (poll while searching for the device on startup) - a bounded `QTimer`-based retry (6
  attempts, 3s apart) on connection failure, found missing via real testing ("it didn't
  refresh automatically... like it was implemented on android version").
- **Raw technical errors no longer shown directly in the UI** - "Error transferring
  http://127.0.0.1:8766/api/nav - server replied: Bad Gateway" is accurate but not useful
  to look at; `DeviceService` now shows a plain "Watch not connected" and logs the real
  detail to a real file (`QStandardPaths::AppDataLocation`/`ambitapp.log`) instead, so
  nothing is actually lost, just moved somewhere a person debugging this can find it without
  it being thrown at whoever's just using the app.
- **Activities got a real "Retry" button** on its error state - the only way to retry used
  to be navigating away and back (which happens to re-trigger the fetch since Main.qml's
  `Loader` recreates the page, not exactly discoverable) - a real problem if the page's
  first load raced the watch still connecting on startup, which is very likely what
  happened in the "activities couldn't load" report.
- **Runalyze corrected**: checked the real Android app's own code
  (`src/services/ApiRunalyze.ts`) after being corrected - it's simple personal-API-key auth
  (a single token header), **not OAuth** as this project wrongly assumed initially. Now set
  up for real in `ConnectionsService`/Settings, the same way Intervals.icu already was.
  Only Strava genuinely needs real OAuth (confirmed: `src/services/ApiStrava.ts` has a real
  registered client ID/secret, authorize/token URLs, refresh tokens) - still honestly
  unbuilt, not faked.

Not yet re-confirmed live against real hardware - this is a lot of change in one round
(maps rebuilt from scratch twice, the connection-speed path restructured) and deserves a
real, careful look before assuming it's actually fixed rather than just plausible.


## 2026-08-07: a full real-testing round - maps' real "missing API key" cause, real track/pin rendering, real POI import from GPX, real Connections, real IP-based weather location

A big round of real, hands-on-hardware feedback, each item root-caused and fixed rather than
guessed at:

- **Maps really did show a "missing API key" watermark** - real, not a misdiagnosis on
  André's part. Root cause: Qt's `"osm"` plugin, in its default configuration, routes every
  tile request through Qt's own shared proxy (`maps-redirect.qt.io`), which gates/rate-limits
  unregistered traffic - OpenStreetMap's own tiles were never the ones asking for a key.
  Fixed by setting the plugin's own `osm.mapping.host` parameter to a real tile server
  directly (`osm.useragent` set too, since tile providers' usage policies expect a real
  identifying user agent, not a blank one) - bypasses that proxy entirely. Confirmed via
  Qt's own current docs (doc.qt.io/qt-6/location-plugin-osm.html), not guessed.
- **Real provider choice added**: OpenStreetMap and CyclOSM, both real and working (same
  standard tile-addressing scheme the override mechanism needs). Esri World Topo and IGN
  were asked about specifically - not offered, for real reasons: Esri uses a different tile
  scheme (z/y/x order, no file extension) this mechanism doesn't support, and IGN has the
  same scheme mismatch on top of being France-only coverage.
- **Maps never actually drew anything on top of the tiles** - every route/activity/POI
  preview just centered the map on content without rendering it, so imported GPX tracks and
  POI coordinates showed a plain map with nothing marked. `MapView.qml` now takes real
  `trackPoints`/`showMarker` and draws a real `MapPolyline`/pin - wired into Routes' import
  preview, POIs' Add form, Activity cards, and Activity detail.
- **Activities was a blank white screen while loading** - real bug, not a crash: no loading
  indicator existed at all, and the real ExerciseLog read takes a couple of minutes (~5.3MB
  over USB). Added a real "this can take a couple of minutes" message.
- **Routes' "no routes / couldn't read it" message was ambiguous** - showed the same text
  for a genuinely empty watch (confirmed live: the reference watch currently has zero
  routes) and a real read error, both with an empty trailing `lastError` either way. Split
  into two distinct, honest messages.
- **POI import from GPX, built for real** - confirmed missing, confirmed present and
  working on the real Android app (`oss/opensportsync-main`'s "POI import (GPX file and
  manual coordinates)"). Parses real `<wpt>` waypoints (GPX's own POI element, distinct
  from `<trkpt>`/`<rtept>`); submitting each one still goes through the same honest
  `addPoi()` 501 as manual entry, since the actual watch-write isn't in this repo's tools
  yet either way.
- **Route export's disclaimer was stale** (said "not built yet" when the real Android app
  has shipped it since V2/0.2.1) - corrected, and the fabricated search-by-name filter on
  Routes' on-watch list removed (didn't reflect the real, simpler Android workflow).
- **Connections went from static/unclickable to real** - checked the real Android app's own
  implementation first this time (`src/services/ApiIntervalsIcu.ts`) rather than guessing:
  Intervals.icu uses simple personal-API-key auth (HTTP Basic, athlete ID + key from
  intervals.icu's own Developer Settings), not OAuth - genuinely implementable, so a new
  `ConnectionsService` (QSettings-backed local storage) plus a real credential dialog now
  exist on both Home and Settings. Strava/Runalyze are clickable too, honestly stating real
  OAuth (a redirect flow) isn't built rather than staying inert or faking it.
- **Weather gained real computer-based location** - IP geolocation (ip-api.com, no key),
  since desktops have no GPS; stated as approximate/city-level, not overstated as precise.
- **About was missing the LICENSE/CREDITS.md content that already existed in the repo** -
  added real GPLv3 + full credits text to the in-app About section, not just repo files
  nobody opening the app would ever see.

Also, precisely: a real backend serialization bug from the previous round
(`ThreadingHTTPServer` letting concurrent requests race each other for the watch's USB
connection) was fixed with a `threading.Lock()` in `server.py` - not yet re-confirmed live
under this round's testing, worth watching for if "still not openable" reappears.

**Not yet done, known real gaps**: GPS orbit validity on Home (separate query, never wired
into this app - "Not available yet" means the feature doesn't exist yet, not that the data
is stale), and cross-page navigation from Home's Connections card to Settings (no shared
navigation state between Loader-loaded pages yet, real but small plumbing).


## 2026-08-07: MapLibre-to-osm swap confirmed real on hardware, plus one more real bug: the backend's own concurrent requests race each other for the watch

Ran the Qt-osm-plugin build against real hardware (Routes GPX-import, Backup, Settings,
Home). **No crash anywhere in the session** - a real difference from every MapLibre-backed
run before it, which crashed within a page or two every time. The Routes import map
rendered real tiles (previously blank/black). Both accuracy fixes from the entry below
(Settings' "Maps"/"About" text, Weather's city name) confirmed live: Weather correctly
showed "Paris" above the temperature.

**One more real bug, found in the same session**: Backup and Firmware both failed with
`write_nav.py`'s new "still not openable after 5 tries" message, and Home hit a nav "Bad
Gateway," all within about 40 seconds. Root cause: `backend/server.py` uses
`ThreadingHTTPServer` - every incoming request gets its own thread - and the app fires
several independent requests close together in normal use (Home's device+weather refresh,
each page's own `onCompleted`, a Backup/Firmware check). Each of those calls
`run_tool()`, which opens the watch's USB connection fresh in its own subprocess; only one
process can hold it at a time, so concurrent requests from different threads genuinely race
each other for it - a real, correctly-reported failure, just not one the reconnect-race
retry (`Link.open()`'s 5 tries/2s) was built to cover, since that's about a single caller
retrying, not two callers colliding. Fixed with a single `threading.Lock()` around
`run_tool()` in `server.py`, serializing every watch-touching backend call regardless of
which HTTP request thread it came from - the correct fix (deterministically avoids the
race) rather than a bigger retry budget (would just narrow the window, not close it).

Not yet re-confirmed live (needs the backend restarted and another real session) - real
next step, alongside actually confirming Activities and POIs specifically, which this
round's screenshots didn't capture.


## 2026-08-07: MapLibre replaced with Qt's own "osm" plugin - real crashes on real hardware, root-caused and swapped out

A second round of real testing (POIs, Routes' GPX-import preview, Activities) found MapLibre
itself - not just the GridView instantiation-count issue from the entry below - genuinely
unstable: `std::bad_alloc` crashes on real hardware, reproducible even with
`LIBGL_ALWAYS_SOFTWARE=1` forcing pure software OpenGL rendering, which rules out "just a
buggy Intel driver" as the explanation (confirmed separately: this machine's GPU is a real
OpenGL 4.2 core profile part with 1536MB shared VRAM, not unreasonably limited on paper).
Something in `maplibre-native-qt`'s own rendering path is unstable here regardless of
renderer.

**Switched to Qt's own built-in `"osm"` GeoServices plugin** - real OpenStreetMap tiles
(same source, same attribution requirement, already correctly handled), ships with Qt
itself, no separate native library, no ~1.7GB build, no `QT_PLUGIN_PATH`/`QML2_IMPORT_PATH`
setup. `MapView.qml` needed one real change (`Plugin { name: "osm" }` instead of
`"maplibre"` + its style-JSON parameter; `map.copyrightsVisible: false` since this app's own
attribution overlay already covers it) - everything above it (`MapService`, every page/card
using `MapView`) needed zero changes, confirming the spec's own "the UI must never care
where tiles come from" abstraction actually held. `CMakeLists.txt` lost the whole
`find_package(QMapLibre ...)` block and gained `Qt6::Positioning`/`Qt6::Location` instead
(already-installed Qt modules, no extra build step). `assets/map/osm-raster-style.json`
(MapLibre's own style file) is now unused, left on disk but out of the build.

Considered and ruled out: IGN (the tile source `oss/opensportsync-main`'s Android app also
uses) - France-only coverage, not a fit for a worldwide default.

Real clean rebuild (not incremental - CMake cache fully regenerated to avoid stale MapLibre
references) succeeded, app launches clean. Not yet re-confirmed on real hardware that this
actually fixes the crash (that needs eyes on Activities/Routes-import/POIs again) - real
next step, but every known cause of the previous crash (the heavy native renderer itself) is
now gone from the binary entirely, not just worked around.

**Same round, a real accuracy fix on Routes**: the "On the watch" section's disclaimer
claimed real GPS-point route export "isn't built yet" and paired it with a search-by-name
filter that implied more interactivity than the real capability. Checked against
`oss/opensportsync-main`'s own `CHANGELOG.md`: real GPS-point route export ("read
routes/waypoints from the watch, save as GPX") shipped in that app's real V2 (0.2.1) - the
disclaimer was stale, same category of mistake as the earlier POI one. Fixed: corrected the
disclaimer to say the capability is confirmed real (elsewhere), just not yet ported into
this repo's own `tools`/`RouteService`, and removed the search box since it didn't reflect
the real, simpler on-Android workflow.

**Same round, a real weather improvement**: `WeatherService` now reverse-geocodes
latitude/longitude into a place name via Nominatim (OSM's own geocoder, no API key - same
provider this app's map tiles already come from) and `WeatherCard.qml` shows it above the
temperature. Same "just don't show it" failure handling as weather data itself if the lookup
fails.


## 2026-08-07: first real end-to-end run - four real bugs found and fixed against real hardware

With the app actually built and launched (see the entry below), André ran it for real against
the connected watch and reported back what broke - screenshots, not just logs. Four separate,
real bugs, each found and fixed the same way: reproduce directly, find the actual root cause,
fix it precisely.

**1. Every page's cards overlapped each other.** `Card.qml`'s content container was a plain
`Item`, whose `implicitHeight` is always 0 regardless of children - unlike `Column`/`Row`, a
bare `Item` doesn't compute implicit size from its content. Every card collapsed to just its
padding, so the next card in a page's Column started drawing over whatever the previous one
actually rendered. Fixed by binding to `contentItem.childrenRect.height`/`width` instead,
which does track children's real bounds. Confirmed with real before/after screenshots -
every page (Home, Settings, Backup, POIs, Routes) now spaces correctly.

**2. Activities always failed with "Bad Gateway", hiding all 7 real recorded moves.**
`exercise_log.py`'s `to_fit()` deliberately raises on an entry with zero GPS points (a real,
unremarkable case - this watch has one, a 7-second accidental start/stop) since a FIT file
needs a track to build records from. `main()` never caught it, so one ordinary entry crashed
the whole run and `server.py`'s `_handle_activities()` correctly reported that as a 502 -
discarding the 6 other, perfectly good entries along with it. Fixed in `main()`: catch that
specific `ValueError` per-entry, skip FIT for that one entry (its GPX, which doesn't need
GPS points, is unaffected), and keep going. Confirmed live: `/api/activities` now returns
all 7 entries, the GPS-less one correctly marked `has_fit: false`.

**3. The watch looked disconnected right after a physical reconnect, and Refresh didn't
seem to help.** Root cause traced through `lsusb`/`getfacl`/sysfs, not guessed: `write_nav.py`
opens the watch through `hid`, which (confirmed by checking which `.so` Python actually
loads) is the **libusb**-backed variant of the `hidapi` package, not the separate `hidraw`
module - meaning the already-installed `/etc/udev/rules.d/99-suunto.rules` (written for
`SUBSYSTEM=="hidraw"`) has never actually done anything for this transport. Real permission
for this backend comes from `/dev/bus/usb/<bus>/<device>`, granted dynamically per desktop
session by systemd-logind's "uaccess" ACL tagging - and there's a real, short race between
the kernel creating that device node on plug-in and logind finishing that tagging. A request
landing in that window fails with a real, correctly-reported "none of them openable" error
that would have succeeded half a second later. Fixed with a bounded retry (5 attempts, 0.4s
apart) in `Link.open()`, and rewrote the error text to describe the actual mechanism instead
of the hidraw advice that was never applicable. Confirmed: current device node reads
`crw-rw-rw-` already, so the fix specifically targets the reconnect race window, not a
persistent misconfiguration.

**4. Maps never rendered tiles anywhere - attribution text showed, the map itself was
blank.** The app's log (only visible once a page with a map actually loads, not at
cold-launch) was full of `QGeoServiceProvider::NotSupportedError`. Cause: `maplibre-native-qt`
installs its actual map-rendering plugin as a native Qt **GeoServices** plugin
(`plugins/geoservices/libqtgeoservices_maplibre.so`) - a completely separate discovery
mechanism from `QML2_IMPORT_PATH` (which only covers the QML-visible `MapLibre.Location`
import, already working). Qt's plugin system needs `QT_PLUGIN_PATH` pointing at the
directory *containing* `geoservices/`, which nothing had ever set. Fixed by adding it
(alongside Qt's own plugin directory, so window-system plugins like `xcb` stay found too)
and persisting it in `~/.bashrc` next to the other Qt environment variables from the build
setup below. Not yet re-confirmed visually (needs a real screenshot of a map-bearing page
after this fix) - real next step.

---

## 2026-08-07: first real Qt6 build - `ambitapp-v2` compiles, one real bug found and fixed

The build gap flagged since `ambitapp-v2` was first written - "structurally correct Qt6/QML,
but nobody has actually compiled this yet" - is closed. Built for the first time, on André's
own Linux Mint (Ubuntu-based) machine, with real terminal access shared between André and
this session.

**Environment, for anyone reproducing this:**
- Qt 6.12.0 via `aqtinstall`/`pipx` (`sudo apt install qmake6`'s own suggestion would have
  installed an unrelated, much older system Qt - the distro's own `apt` package for Qt6 tops
  out at 6.4.2, below the `6.5` this project's `CMakeLists.txt` requires). `aqt`'s CLI calls
  the architecture `linux_gcc_64`; it actually installs into a directory named `gcc_64` - a
  real gotcha worth remembering, cost one failed `qmake6: command not found` round trip.
- System libs `qt_add_qml_module`'s Wayland/EGL plugin dependencies needed that weren't
  installed by default: `libwayland-dev`, `wayland-protocols`, `libgles2-mesa-dev`,
  `libegl1-mesa-dev`.
- `maplibre-native-qt` - no packaged version exists anywhere, built from source (its own
  `docs/Building.md`, Ninja + OpenGL backend). Needed `ccache`, `libicu-dev`,
  `libxcb-xkb-dev`, `ninja-build`, plus Qt's own `qtlocation` module (separate from
  `qtpositioning`, easy to miss). ~1.7 GB cloned (it vendors the full MapLibre native
  rendering engine as a submodule), all 535 build targets succeeded clean. Given the
  machine's internal disk only had 8.9 GB free, everything large - the Qt SDK itself, the
  MapLibre clone/build/install, and the `ccache` cache - was deliberately kept on the
  external drive `ambit-app` already lives on, not the internal disk.
- Machine has 4 cores but only ~8.8 GB available RAM and no swap - built at `-j2`, not
  `-j4`, specifically to avoid an OOM kill partway through a long native C++ build.

**The one real compile bug, found and fixed:** every one of the app's six `QML_SINGLETON`
C++ service classes (`DeviceService`, `WeatherService`, `ActivityService`, `RouteService`,
`PoiService`, `BackupService`) failed identically - "was not declared in this scope" inside
`qt_add_qml_module`'s auto-generated `ambitapp_qmltyperegistrations.cpp`. Root cause: that
generated file looks up each header by bare filename
(`#if __has_include(<deviceservice.h>)`), not by its real path under `src/services/` -
without that directory explicitly on the include path, every check silently fails and the
`#include` gets skipped. Fixed with one line, `target_include_directories(ambitapp PRIVATE
src/services)` in `CMakeLists.txt` - see `ambitapp-v2/README.md` for the same note in
context. Confirmed on real hardware, not simulated: `python3 tools/selftest.py`-style
before/after, the binary (`build/ambitapp`, 3.1 MB) exists and links after the fix, didn't
before.

**Follow-up, same session: a second real bug, found by actually running the binary.**
Launched it for the first time (backend bridge running, real display) and every page was a
storm of `Unable to assign [undefined] to QColor/double/int/QString/bool` - Theme and Icons
(and every other QML singleton: FeatureFlags, DeviceCapabilities, MapService, and all four
ViewModels) had every property come back undefined. Root cause, found in the generated
`build/AmbitApp/qmldir`: entries read `Theme 1.0 qml/Theme.qml` instead of `singleton Theme
1.0 qml/Theme.qml` - the `singleton` keyword was missing for every one of those 9 types.
`CMakeLists.txt` had `set_source_files_properties(qml/Theme.qml PROPERTIES
QT_QML_SINGLETON_TYPE true)` *after* `qt_add_qml_module()`, but `qt_add_qml_module` reads
that property while generating `qmldir`, at the time it processes the file list - setting it
afterward is too late, every file got registered as an ordinary type instead. Fixed by
moving that whole block above `qt_add_qml_module()`. Rebuilt, relaunched: **zero QML
warnings, zero errors**, confirmed directly in the generated `qmldir` (`singleton Theme 1.0
...` now present) and in the running app's own log (empty, where it used to be a wall of
errors).

**Still not yet done**: the app has been launched and starts clean, but nobody has looked at
the actual rendered screen yet, or exercised it against the real watch through the GUI.

## 2026-08-07: shared-core investigation - a real finding on both the write mechanism and this project's own GPL boundary

Started as "step 1" of merging `csrc/`'s navigation code and the Android app's vendored
`libambit` into one shared library. Turned up two things worth recording precisely before any
more of that work happens.

**openambit already has a real sport-mode write implementation - just disabled for Ambit3.**
`assets/openambit-master.zip`'s `src/libambit/sport_mode_serialize.c/h` (Emil Ljungdahl, 2014,
with its own unit test) is a complete encoder for exactly what this project needed to build:
settings, displays, rows, sport-mode groups, and app data - the earlier "nobody has built
build_settings()/build_display()/build_mode()/build_sport_modes_table()" framing was wrong.
It's real, it's vendored (unused) in `oss/opensportsync-main`'s Android build too. Confirmed
why it's never fired for this watch: `device_driver_ambit3.c`'s driver table has `NULL, //
sport_mode_write` and `NULL, // app_data_write` - Ambit3 support was deliberately never wired
up, only Ambit1/2 (`device_driver_ambit.c`) got it. Traced the actual transport
(`pmem20.c`): `sport_mode_write`/`app_data_write` call the bare chunked writer
(`libambit_pmem20_data_write`) with **no finishing step**, unlike the sibling `gps_orbit_write`
(enabled for Ambit3, and already proven working on real hardware via `sgee.py`), which does
the same chunked write **plus a tail command with an optional SHA-256 hash**. A concrete,
testable hypothesis for the `workout_install.py` "app error" bug: the missing tail/commit
write, not the wrapper bytes originally suspected. `PMEM20_SPORT_MODE_START` (`0x2000`) and
`PMEM20_APP_START` (`0x927c0`) match this project's own independently-discovered region
addresses exactly - strong cross-confirmation this is the right mechanism.

**This project deliberately keeps GPL code out of `ambit-app`, on purpose - almost missed
that and copied `libambit` straight in.** `csrc/test/crypto.c`'s own header says it plainly:
CRC16/SHA-256 are reimplemented there specifically "so the serializer can be built without
copying GPL code into this repository." `device_driver_ambit3_navigation.c` was written as a
clean-room module meant to be *dropped into* an openambit-derived tree, not to have
openambit's tree pulled into this one - confirmed already happening in practice: it's
byte-identical in `oss/opensportsync-main`'s vendored `libambit/`. Corrected course before
writing anything: no openambit source was copied into this repo.

**Built instead**: `tools/custom_modes_write.py` - a clean-room Python encoder (not derived
from `sport_mode_serialize.c`'s text, built from this project's own already-documented,
byte-verified format spec in `custom_modes_andre.md`) for the BXml tag tree: settings,
displays, display fields/shortcuts, rules, app-meta, and the `SPORT_MODES` table including
multisport combos. `--selftest` round-trips real, byte-verified example values (Openwater
swim's ActivityID/CustomModeID/UseHw, the exact `EXERCISE_MODES_TYPE` header bytes
`custom_modes_andre.md` transcribed from the real dump, Triathlon's confirmed 5-leg/104-byte
encoding) through `build_*` then `custom_modes.py`'s own trusted `decode_*` functions and
checks for exact equality - passes. `tools/selftest.py` stays 25/25, additive only.

**Scope, stated precisely - this is not write-ready yet**: `custom_modes_write.py` covers the
BXml body only (confirmed ~10240 of the region's 12288 bytes). The remaining tail - presumed
header/checksum by analogy with Routes/Waypoints, per `custom_modes_andre.md`'s own
"Traced further" section - was never confirmed and isn't built here. Also still open: a C
port (mirroring how `device_driver_ambit3_navigation.c` was built and cross-verified against
its own Python reference via `c_reference.py`), and testing the tail/commit hypothesis above
against real hardware. Parked for now alongside the sport-mode write-path scoping from the
entries below - same underlying feature, converging investigations.

**Follow-up, same day: real hardware confirms the write mechanism, and a real decode gap was
caught and fixed by testing against live data before writing.** Round-tripping the encoder
against a live-read `CustomModes` dump (decode -> rebuild -> compare) turned up two BXml tags
this project's decoder had been silently dropping since 2026-08-05: `SPORT_MODE_ORDER`
(`0x2fe`, a persistent per-slot ID that survives deletion - values 1-10 skip 8, the deleted
Alpine skiing slot) and `SPORT_MODE_APP_META` (`0x2ff`, present only on the 3 app-carrying
slots, a timestamp a few seconds after that mode's own `EXERCISE_MODES_APP_META`). Both now
decoded and encoded; the encoder round-trips **byte-for-byte identical** to the real watch's
current `CustomModes` region. Separately, found the region also has ~222 bytes of genuinely
undecoded structure past the BXml body (offsets 7416-7637 on this watch) that even openambit's
own code never figured out (hardcoded as a verbatim constant, `UNKNOWN_DISPLAYES`) - flagged,
not touched, not written over. Real next step, tracked separately.

With the encoder verified against live data, tested the write mechanism itself: read
`CustomModes` fresh, wrote the *exact same bytes* back (zero content change) using
`write_nav.py`'s existing generic writer (`send_plan`) with `ambit_format.py`'s own
pre-existing `HASH_PADDED` classification for this region (recorded 2026-08-05, by analogy
with Routes/Waypoints, never empirically confirmed until now) - 12 chunked `CMD_DATA_WRITE`s,
one padded-region-SHA256 `CMD_DATA_TAIL`, one `CMD_NAV_COMMIT`. **Confirmed on real
hardware: the watch accepted it, and a fresh read back afterward is byte-for-byte identical.**
This is the actual missing piece from the "app error" bug - not the GpsSGEE-style tail
hypothesized earlier in this document, but the full Routes/Waypoints-style closing sequence
(chunk writes + padded hash + commit), now confirmed empirically rather than guessed. Real
next step: retry the app-install path (`workout_install.py`) with this now-confirmed
sequence instead of its previous, incomplete one.

**Follow-up, same day: the ~222-byte trailing block solved - stale flash residue, not a new
format.** The gap between the BXml body (7416 bytes) and the full 12288-byte region wasn't
blank as assumed - 222 non-`0xff` bytes there turned out to be a literal leftover copy of
Triathlon's own `SPORT_MODE` entry from before `SPORT_MODE_ORDER` existed on this watch
(104 bytes, the pre-discovery length, byte-for-byte matching Triathlon's real current name/
ActivityID/leg sequence), plus a scrap of Trekking's data - ordinary flash write-history
residue, most likely from around the Alpine-skiing deletion, not a third on-device format.
Full derivation, including the offset-realignment that made it parse cleanly, in
`custom_modes_andre.md`. Confirms the encoder's current `0xff`-padding of everything past
the BXml body is fine going forward - nothing there needs reproducing.

**Follow-up, same day: C port built and cross-verified, dropped into both apps.**
`csrc/device_driver_ambit3_sport_modes.c/h` - a clean-room C port of
`custom_modes_write.py`, same convention `device_driver_ambit3_navigation.c` already
established (bounds-checked writer, every computed offset checked against the region's real
size before touching memory, per this project's own standing rule about flash writes).
Cross-verified the way routes already are: `csrc/build/sport_modes_harness` builds the exact
same synthetic mode + two sport-mode slots as the Python selftest, and
`tools/sport_modes_c_reference.py` demands the resulting 12288-byte region be byte-for-byte
identical between C and Python - passes (434-byte body, confirmed identical). Wired into
`make -C csrc test` and `tools/selftest.py` (now 26/26). Dropped into
`oss/opensportsync-main`'s vendored `libambit/` alongside the navigation module - its
`CMakeLists.txt` globs `libambit/*.c`, so it's picked up automatically, no build-file edit
needed. **Not wired to anything yet, on either side** - no JNI bridge call on Android, no
caller in `ambitapp-v2`'s backend - deliberately, since nothing here has touched a real
watch. That's the next real step, alongside the still-open region-closing-structure question
and the tail/commit-write hypothesis from the entry above.

## 2026-08-07: found `opensportsync-main` is real, actively developed, and unversioned

Discovered while following up on André's "V3.1 added garmin etrex usb support" - the
`GARMIN_USB_IMPORT_SPEC.md` path he gave (`oss/opensportsync-main/`) turned out to be a
sibling folder on the same mounted drive as this repo (`/media/skinnie/.../oss/`), not a
subfolder of `ambit-app`. Confirmed it's a real, mature, currently-developed React Native
Android app - v0.2.1 shipped per its own `CHANGELOG.md`, v0.3.0 (BLE) in progress, 14
completed architecture phases including real Strava/Runalyze/Livelox OAuth2 (PKCE S256), IGN
map tiles, and a real Kotlin+NDK USB bridge to `libambit` - not a disposable "test base" as
earlier framing in this document assumed. `HANDOFF.md`'s "full pivot away from
opensportsync"/"superseded" language (written 2026-08-06, before this was found) was
premature and has been corrected - see its "Two real apps" note.

**Separate, more urgent finding**: `oss/opensportsync-main` has **no git repository at all**
(`ambit-app/.git` exists, `oss/opensportsync-main/.git` does not, confirmed with `find`
across the whole mount). Real, versioned-by-changelog, substantially-featured software with
zero version control is a real backup/history risk independent of any decision about how the
two apps relate - worth fixing on its own.

## 2026-08-07: serial number + hardware version on Home, firmware backup UI

Both requested after the entries below, now built:

- **Home** shows serial number and hardware version now (a second stat row, next to
  battery/firmware/GPS orbit) - `HomeViewModel.serialText`/`hardwareText`, reading properties
  `DeviceService` already had from the device-info work.
- **Backup page** gets a real "Firmware" section: checks for the latest firmware
  (`BackupService.checkFirmware()` -> `GET /api/firmware`) and can save it locally
  (`downloadFirmware()` -> `POST /api/firmware/download`, backend saves straight to
  `~/AmbitAppBackups/firmware/`, no base64-through-JSON detour for a 2 MB file). **Carries an
  explicit, bold, always-visible warning, exactly as asked**: this is for backup only, there
  is no known way to flash the watch over this protocol, and the only supported firmware
  update path is Suunto's own official app.

Both new backend endpoints (`GET /api/firmware`, `POST /api/firmware/download`) tested live
against the connected reference watch before being wired into the UI - confirmed the same
2,161,424-byte file, saved to `~/AmbitAppBackups/firmware/Emu-fw_2.4.17-70.2.17414.bin`.
`selftest.py` still 25/25. Not compiled (same standing caveat as all of AmbitApp V2 - no Qt6
dev headers in this sandbox), but every backend call behind it is real and verified.

## 2026-08-07: real firmware-update check + download link, verified live

**New**: `tools/firmware_check.py` - given a watch's model codename and hardware version
(read live via `device_info.py`, or passed explicitly), asks Suunto's own real device-info
service what the latest firmware is and gets back a real, working download URL. Not
this project's own discovery - the exact recipe is a public gist by marguslt
(`gist.github.com/marguslt/8cffaa78152503b29b91920de845e536`,
"suuntolink-firmware-download-links.ipynb"), the same contributor already cited elsewhere in
this project (`HANDOFF.md` Finding 11, `training_program_andre.md`).

**Confirmed live, both the gist's own example and this project's actual watch** (same host
as the already-confirmed-unauthenticated AGPS endpoint, `devices.suunto-operations.com`,
but this one does need the app key the gist itself embeds):

```
GET https://devices.suunto-operations.com/devices/Emu/70.2.17414?appkey=<gist's key>
-> {"DeviceName":"emu","Version":"70.2.17414","LatestFirmwareVersion":"2.4.17",
    "LatestFirmwareURI":"https://devices.suunto-operations.com/firmwares/Emu-fw_2.4.17-70.2.17414.zip",
    "FirmwareUploadDate":"2022-03-09T20:32:42ZZZ","ReleaseType":"production", ...}
```

That URL is real and downloads a genuine 2,161,424-byte file - matches the connected
reference watch's own currently-installed firmware version (2.4.17) exactly.

**Real, worth recording precisely: despite the `.zip` name, it is not a standard zip
archive.** Downloaded and checked directly - starts with an `"SFI2STmp"` magic (not `PK\x03\x04`),
and `unzip -l` fails outright ("End-of-central-directory signature not found"). Looks like a
proprietary, likely encrypted Suunto firmware container - genuinely unopened, not decoded by
this tool or anywhere else in this project yet. Real next step if this gets picked up again:
figure out the `SFI2` container format before assuming anything about what's inside it.

## 2026-08-07: real device identity + live battery, verified on real hardware

**New**: `tools/device_info.py` - model, serial, firmware/hardware version, and live battery
charge percentage, read straight off the watch. Wired into `ambitapp-v2/backend/server.py`
(`GET /api/device`), `DeviceService` (C++), and Home's device-hero card - battery and
firmware no longer say "Not available yet."

**Not new reverse-engineering - both underlying commands were already known to this
project, just never used this way:**

- `write_nav.py` already sends `CMD_DEVICE_INFO` (`0x0000`) on every reset/route run, but
  discarded the reply completely. It's just being parsed now, nothing new sent to the watch.
- `ambit_pcap.py`'s own `CMD_NAMES` table already named `0x0306` as `"status"` from real
  captures - confirming it's a real command this project had already seen - but nothing had
  ever sent it before `device_info.py`.

**Where the actual reply layout came from**: openambit's own real, working implementation
(`assets/openambit-master.zip`) - `libambit.c`'s `device_info_get()` for the `0x0000` reply
(16 bytes model + 16 bytes serial + 4 bytes fw_version + 4 bytes hw_version) and
`device_driver_common.c`'s `libambit_device_driver_status_get()` for `0x0306` (charge
percentage at reply byte offset 1). Confirmed Ambit3's own driver
(`device_driver_ambit3.c`) wires `status_get` to that exact shared implementation, not a
different one - so this isn't an Ambit1/2-only path.

**Verified against real hardware in this same session** (a real Ambit3 Peak was connected
throughout):

```
model     Emu
serial    8A153C5111000900
firmware  2.4.17
hardware  70.2.17414
battery   99%
```

Caught and fixed a real bug along the way: the first version parsed `hw_version` as
`70.2.6`, not matching `HANDOFF.md`'s already-documented `70.2.17414`. openambit's own
`version_string()` helper showed why - the third version component is 16-bit little-endian
(`version[2] | (version[3] << 8)`), not a single byte. Fixed, re-verified, now matches
`HANDOFF.md` exactly - a real independent cross-check, not just "it ran without crashing."

`python3 tools/selftest.py` still 25/25 after this - `device_info.py` is additive, nothing
existing was touched.

**What this does *not* cover yet**: GPS orbit validity (Home's third stat, still "Not
available yet" - a separate query, `0x0b15`, documented in `sgee_andre.md`, not part of this
change) and the Ambit3-specific "compact serial" (`0x0b1e`,
`ambit_command_ambit3_get_compact_serial`) - openambit's own request payload for that one
has an uninitialized-looking stack buffer in its real C source, so it wasn't reused without
a cleaner reference; the primary `serial` field above already comes from a real, working
command instead.

---

## 2026-08-06/07: AmbitApp V2 - all 11 spec steps built

Full Qt 6 / QML desktop app per `AMBITAPP_SPEC.md` - a second real app alongside
`opensportsync`/React Native (`oss/opensportsync-main`), not a replacement of it; see
`HANDOFF.md`'s "Two real apps" note, corrected 2026-08-07 after finding that fork is real and
actively developed on this machine. Real, hardware-tested Python tooling (`tools/*.py`) stays
the actual backend for the desktop app,
reached through a local HTTP bridge (`ambitapp-v2/backend/server.py`) rather than ported to
C++ - see `ambitapp-v2/README.md`'s "Architecture decision" section for why.

All 11 steps built: Theme, Card, navigation shell, Home (device hero + Connections),
Weather (Open-Meteo), Maps (real MapLibre Qt bindings over OpenStreetMap), Activities (real
GPX parsing into map + stats + detail view), Routes (on-watch summary + Import/Upload),
POIs (raw dump + live coordinate-preview Add form), Backup (create/list/rehearse/restore,
built on `write_nav.py`'s own already-proven `nav --save`/`restore`), and Settings.

Full detail, including every honestly-stated gap (battery/firmware were still "Not
available yet" at this point - closed by the entry above), lives in
`ambitapp-v2/README.md` - not duplicated here. Written but never compiled in this
environment (no Qt6 dev headers in this sandbox) - real build/test still needed on a
machine with a proper Qt 6.5+ dev setup, except where individual pieces (like the entry
above) were separately verified against real hardware.

---

## 2026-08-07: v6 screenshot round - white screens, Activities speed, Weather requests

Three real bugs found from the v6 screenshot round (Activities/Routes/POIs all rendering as
blank white pages) plus two explicit feature requests, all fixed and verified against real
hardware/real build:

**`MapView.qml`'s `z` property collision (the actual cause of all three white screens).**
The rewritten plain-tile-renderer `MapView.qml` (see the Maps entry above) declared its
zoom-level property as `property int z`. `z` is `Item`'s own built-in FINAL stacking-order
property, so this is not a shadowing/scoping issue but an outright QML compile error -
`Cannot override FINAL property`. Every page that embeds `MapView` (Activities, Routes,
POIs) failed to load *that whole component* as a result, and QML's own error cascades one
failure into "Type MapView unavailable" for every consumer - exactly matching the v6
screenshots (three unrelated-looking pages, one shared root cause). Found by reading the
real running app's own stdout log, not guessed. Fixed by renaming `z` to `tileZ` everywhere
it's used inside the file.

**`SettingsPage.qml`'s `oauthDialogService` scope bug.** A property meant to drive the
Strava/Runalyze "not connected" dialog was declared inside the nested `Column { id: column
... }`, not on the `Flickable`'s own `id: root`. QML property scope isn't lexical the way a
variable would be - `root.oauthDialogService` genuinely did not exist from outside that
Column, producing `Cannot assign to non-existent property`. This silently broke the
dialog and was the likely reason CycloSM's radio button appeared to do nothing too (both
symptoms traced to the same file). Fixed by moving the declaration to directly under
`id: root`.

**Activities' real slowness vs. the Android app ("blazing fast" there, minutes here).**
`tools/exercise_log.py` was unconditionally reading the full preallocated `ExerciseLog`
flash region (5,526,464 bytes) on every call, regardless of how much of it is actually real
data. The region's own master header (offset 0, `<IIII` LE: `last_entry, first_entry,
entries, next_free_address`) already tells you exactly how much is real - reading it first
(1024-byte probe) showed only 46,364 bytes were actually used on this watch, a **119x**
unnecessary read. Fixed with a probe-then-sized-read: read the header, compute `needed =
next_free_address - EXERCISE_LOG_BASE + 8192` (safety margin), clamp to
`[1024, EXERCISE_LOG_SIZE]`, and read only that. Added a `try/except (IndexError,
struct.error)` fallback to a full-region re-read, since `logical_read()`'s wraparound
handling (for when the circular log has filled and wrapped once) assumes the buffer it's
given spans the full declared region - a real correctness risk for watches with much more
history than this one, worth keeping even though it doesn't trigger here.
Verified against the real connected watch: `time python3 exercise_log.py --gpx-out ...
--fit-out ...` went from 2-3 minutes to **1.77 seconds**, all 7 entries (including a new 7th
"Trekking" entry dated 2026-08-07, i.e. genuinely live watch state) decoding identically to
the prior slow-path output. `tools/selftest.py` still 26/26 after this change.

**Weather: 10-minute background auto-refresh + offline message, both explicitly requested.**
`WeatherService` now owns a `QTimer` (10 min interval) started in its constructor that calls
`refresh()` on its own, independent of whichever page triggered the first load. The old
spec rule ("if weather retrieval fails: hide the card, no popup, no error") was explicitly
superseded by the real request - `WeatherCard.qml` now shows "You're offline, go outside to
check the weather!" in place of the weather content on failure, rather than collapsing to
nothing. New `hasFetchedOnce` property keeps the card hidden until the very first refresh()
attempt has actually completed (success or failure), so there's no flash of the offline
message on a cold launch before any request has even gone out. Implementation note: `Card`
sizes itself off `contentItem.childrenRect`, which includes invisible children - so the
offline/weather-content swap uses a `Loader` (only one Component ever instantiated) rather
than two children with one `visible: false`, to avoid silently reserving the full weather
layout's height behind a one-line message.

All three page-breaking bugs (MapView, SettingsPage) and the exercise_log.py change went
through a real incremental rebuild (`cmake --build .`) after this round of fixes to confirm
they actually compile - not just edited and assumed correct.

---

## 2026-08-07/08: real OAuth2, real GPS orbit update, real GPX export, offline cache, map polish

A long round of rapid-fire real feature requests and bug reports, all landed the same
session, verified against real hardware and a real (later rebuilt) build throughout.

**OpenStreetMap standard tiles: root-caused, not just reported.** A fresh app launch
defaulted correctly to `"osm"`, but `tile.openstreetmap.org` was returning its own "Access
blocked" HTTP 418 image instead of tiles - confirmed by reading the actual pixels off a real
screenshot (a "volunteer-run servers... osm.wiki/Blocked" tile), not guessed. Root cause:
`MapView.qml`'s plain `Image` requests went through Qt's default `QNetworkAccessManager`,
which sends no `User-Agent` at all - a real violation of OSMF's tile usage policy
(operations.osmfoundation.org/policies/tiles/), which explicitly requires one. Fixed with a
`TileNetworkAccessManagerFactory` in `main.cpp` (a `QQmlNetworkAccessManagerFactory`
subclass) that stamps `AmbitApp/2.0` on every QML network request. Since OSMF's block is
typically IP-based and time-limited (may not clear immediately even with the fix),
`MapService.qml`'s default provider was switched to `"cyclosm"` (confirmed working
throughout this same session) until it lapses - `"osm"` is still one click away in Settings.
Separately found and fixed the real cause of "clicks for CyclOSM don't do anything" from
earlier in this session: the two provider `RadioButton`s' declarative `checked` bindings
were fighting with QQC2's `autoExclusive` default, which explicitly overwrites `checked` on
the losing button and silently destroys its binding - fixed with `autoExclusive: false`
(exclusivity is already fully guaranteed by the shared `MapService.provider` string) and
`onClicked` instead of `onCheckedChanged` (which also fires from binding evaluation, not
just real clicks).

**GPS orbit: real update, not a placeholder.** Home's "GPS orbit" stat was hardcoded "Not
available yet." The real mechanism (0x0b15 `gps_orbit_head`, a live unauthenticated data
source, byte-exact write verification) was already fully built and hardware-tested back in
`sgee_andre.md` - only the UI and a `--status` mode were missing. Added `sgee.py --status
--json` (reads 0x0b15 read-only, decodes `[valid][year][month][day][seconds]`, no file
argument needed). Rewrote `backend/server.py`'s `/api/agps/update` to the real requested
flow: check the watch's own current orbit date first (no network needed for this part); if
under a day old, report "No update needed" and stop; otherwise try the live download - if
that fails (no internet), fall back to honestly reporting the watch's already-known date
instead of erroring; only then run `sgee.py --write`. New `/api/agps/status` GET mirrors the
read-only half for passive display. `DeviceService` gained `checkGpsOrbitStatus()` (called
on every Home load, like the other stats) and `updateGpsOrbit()` (explicit tap only - this
app's established rule that only a real user action writes to the watch, matching Routes/
Backup, not auto-fired from a page load). Verified live against the real watch: correctly
reported "2026-08-07 - tap to update" from a genuine 0x0b15 read.

**Real bug during this: the backend server process was stale.** `backend/server.py` had
been edited extensively this session, but the running `python3 server.py` process (started
hours earlier) never reloaded it - Python doesn't hot-reload source files. Every new
endpoint (`/api/agps/status`, the rewritten `/api/agps/update`, `/api/routes/export`) was
silently unavailable until the process was restarted. Found by a real symptom that didn't
add up: the GPS orbit UI showed "Updated" (a string only the write-success path produces) on
what should have been a read-only passive check - impossible unless a stale process was
serving old routing. Restarted the backend; re-verified correct behavior immediately after.

**Route export: real GPX, not a placeholder.** `RouteService.downloadAvailable` was
hardcoded `false` with a comment explaining the watch's on-watch route points couldn't be
reconstructed from `nav`'s text summary alone. Added `write_nav.py`'s `route_to_gpx()`:
decodes one route's full point list and turns each point's watch-relative `(x, y)` back into
absolute lat/lon via `ambit_format.inverse_xy()` - already-existing, already-tested code,
the exact inverse of what `build_routes()`/`serialize()` do when writing a route, not new
math. New `nav --route-gpx INDEX --route-gpx-out PATH` CLI flags and a
`/api/routes/export` backend endpoint. **Verified directly against a real capture**
(`assets/ambit3 pcap/route12km`) before ever touching the UI: 336/336 points decoded, first
point matched the known-correct reference GPX (`Gare-du-Nord-to-...gpx`) to within about a
metre - real, independent validation, not just "it ran." `RoutesPage.qml` gained a real
per-route "Export" button opening a real save dialog (`FileDialog`, `SaveFile` mode)
defaulting to the Downloads folder, matching the real Android app's own save-to-Downloads
behavior as closely as a desktop file picker can.

**Activities: real GPX/FIT export, plus a local offline cache.** `ActivityService` already
received full `gpx`/`fit_base64` per activity from the backend but discarded the raw GPX
text after parsing it into fields - kept now (`gpxText`) alongside the base64 FIT. Added a
real Export tab to `ActivityDetail.qml` ("Export as GPX"/"Export as FIT", same save-dialog
pattern as Routes). Separately, real request: "activities... saved in the computer... loads
when the watch isn't plugged in." Every successful live `refresh()` now also writes each
activity's GPX/FIT to `QStandardPaths::AppDataLocation + "/activities_cache/"`
(`moveN.gpx`/`.fit`); a failed live read falls back to loading from that cache instead of
just showing an error, with a `showingCachedData` flag surfaced honestly in both
`ActivitiesPage.qml` (a banner) and Home's Last Activity card ("(cached)"). A plain per-file
cache, not a database - this app has no database anywhere else either (`QSettings` for
Connections), and activities are already real, self-contained files once read; a SQL layer
would solve a problem that doesn't exist here.

**Strava: real OAuth2, not a placeholder dialog.** Checked the real Android app's
implementation first (`ApiStrava.ts`): real client ID/secret from a self-registered app,
authorize/token/refresh against Strava's own endpoints, `opensportsync://oauth/strava` as
the redirect via a custom URL scheme. The desktop equivalent doesn't need OS-level URL
scheme registration: `ConnectionsService::connectStrava()` opens a local loopback
`QTcpServer` on an ephemeral port, points the system browser (`QDesktopServices::openUrl`)
at Strava's authorize page with `redirect_uri=http://127.0.0.1:<port>/callback` (Strava's own
docs list `localhost` as a valid Authorization Callback Domain), and completes the token
exchange once the one-shot local server catches the callback. Same credential-storage
pattern as Intervals.icu/Runalyze (`QSettings`, entered directly in a real Settings dialog,
not compiled into the binary the way the Android app's gitignored `secrets.ts` does - this
is actually a small improvement on that pattern, not a lesser version of it: credentials can
change without a rebuild). Scope matches the other two connections exactly (Connect + status
only, no sync/upload feature exists for any of the three yet).

**MapView: track visibility, POI marker visibility, real fit-to-bounds zoom.** Three related
real requests. The track polyline was `Theme.primary` (a teal) with no contrast handling -
found to visually blend into OSM/CyclOSM's own parks-and-water palette. Fixed with a real
cartography technique: the same path is stroked twice (a wide white halo, then a narrower
vivid magenta `#E6007A` on top), staying visible over roads, parks and water alike. The POI
marker got the same halo treatment (a white circle behind the pin) since a plain red glyph
had the identical problem sitting on OSM's own red/orange road cartography. Zoom: every
`MapView` now computes a real fit-to-bounds zoom from the track's actual bounding box
(`_trackBounds`/`_refitZoom()`, a standard slippy-map "highest zoom where the box still fits
with ~20% padding" search) whenever real track points exist, instead of relying on each
caller's own averaged center + a fixed guessed `zoomLevel` - every activity/route thumbnail
and the detail view all get a real, correctly-tight view automatically, not just Home.
Optional `showZoomControls` (a real +/- overlay, opt-in since a small card thumbnail
shouldn't get two more tap targets) was turned on for Activities' large detail map. Routes'
own import-preview map, previously hidden entirely (or centered on `(0, 0)`, the Gulf of
Guinea) until a GPX was loaded, now always shows a real map centered on
`WeatherService`'s own IP-detected location - matching what POIs' "Add" form map already
did, and fixing a real "no map while nothing's imported" report. POIs' own preview map
default zoom went from 10 (city level) to 15 (street level) for the same "make it usable to
actually place a point" reason.

**Backup: "Open backup folder" replacing "Rehearse restore".** Real request - the per-backup
"Rehearse restore" button was replaced with "Open backup folder"
(`LocalFileService::openFolder()`, `QDesktopServices::openUrl` on `~/AmbitAppBackups`,
mirroring `backend/server.py`'s own `BACKUP_DIR` constant client-side rather than adding a
network round trip just to ask for a fixed, already-known path). "Restore" itself already
reports its own result text, which was Rehearse's whole purpose here.

**New shared service: `LocalFileService`.** Every "export a real file, let the user pick
where" feature above (Routes' export, Activities' GPX/FIT export, Backup's folder button)
goes through one small QML singleton (`saveText()`/`saveBase64()`/`openFolder()`/
`downloadsLocation`/`backupsLocation`) instead of duplicating `QFile` I/O per Service - the
same reasoning `Card.qml`/`Theme.qml` already established for UI.

**Smaller real fixes and cleanups the same round:** Home's placeholder "Last Activity" card
made real (wired to `ActivityViewModel.mostRecent()`); the redundant "New Activities" card
and Home's own "Connections" card removed (Settings already owns Connections for real);
Weather now defaults to IP-based location automatically on startup instead of a hardcoded
central-Europe coordinate, with a 10-minute background auto-refresh `QTimer` and a friendly
"You're offline, go outside to check the weather!" message replacing the old "hide the card
on any failure" rule; several now-stale UI strings removed (Settings' "Qt OSM plugin"/"Maps:
Qt Location's own osm plugin" credits, "no background timer built yet" - contradicted by the
timer just added -, the disabled "Automatic (watch GPS) - future" radio the user confirmed
"will never be possible," the "Esri/IGN were considered" dev-log-style paragraph, "(no API
key)" from the Weather provider line).

Every change in this entry went through a real incremental rebuild and a real relaunch
against the actual connected watch before being reported done - several (the stale-backend
bug, the RadioButton `autoExclusive` bug, the 418-blocked-tiles root cause) were only found
because of that, not from reading the code alone.

**Follow-up, same session: track/marker color changed to `Theme.primary`.** The vivid
magenta chosen above for contrast was a real, reasoned choice, but the real request that
followed it was more specific: match the nav rail's own selected-item green
(`NavItem.qml`'s `color: selected ? Theme.primary : ...`) for brand consistency, not an
unrelated accent color. Both the track polyline and the POI marker's border/icon were
switched from the hardcoded `#E6007A` to `Theme.primary` - the white halo underneath both
(the actual contrast mechanism) is unchanged, so visibility against the map tiles is still
real, just recolored. Verified live: both render in the app's own green, confirmed against
a real screenshot after rebuild. Also incidentally confirmed the offline activities cache
end to end during this check - the watch was unplugged between builds, and Home's Last
Activity card correctly showed "(cached)" instead of erroring.

**Follow-up, same session: Home auto-refresh, and real thumbnails for Routes/POIs.**

*Device connection auto-refresh - 5s, not the requested 2s.* Real request: "refresh is not
automatic to the watch." Explicitly flagged back before implementing literally:
`DeviceService.refresh()` isn't a cheap poll - every call is a real Python subprocess spawn
plus a real USB open/command/close round trip against physical hardware, serialized behind
`WATCH_LOCK` with every other watch action (Activities, Routes, POIs, GPS orbit). Polling
that every 2s indefinitely would mean continuous, real CPU/process churn and could make an
unrelated tap feel stalled if it landed mid-poll - the opposite of what auto-retry's own
existing back-off logic (3s between attempts, capped at 6 tries) was written to avoid.
Implemented at 5s instead (`DeviceService::m_autoRefreshTimer`, same pattern as
WeatherService's own background timer), explained to the user, verified live (the Refresh
button visibly flips to "Checking..." on its own every ~5s).

*Routes/POIs "On the watch": real loading text, real thumbnail maps, no extra USB reads.*
Both sections used to just sit blank while `refresh()` was in flight - "add a loading text
... instead of white" fixed with a plain `Text { visible: ...loading }`, matching
Activities' own existing pattern. The bigger piece: "add a map for each gpx" /
"thumbnail on map for each poi". For Routes, this needed real per-route point data the
summary view never had - rather than adding N extra USB round trips (one per route,
reusing the `/api/routes/export` endpoint built earlier), `write_nav.py`'s `nav` command
gained a `--json` flag (`nav_summary_json()`) that decodes every route's full track from
the *same* flash data `show_navigation()`'s text summary already reads - one JSON line
appended after the existing human-readable output, the same "find the last JSON line"
convention `sgee.py --status --json` already established (backend's `_parse_sgee_status_
output` renamed to `_parse_last_json_line`, now shared by both). `/api/nav` was updated to
request `--json` and return the parsed `routes` array (with `track`) alongside the
unchanged `raw_output`; `RouteService::parseOnWatchRoutesJson()` consumes it directly,
falling back to the old regex parser only if the JSON array comes back empty (an older
backend, or a genuinely empty watch - the regex fallback correctly finds nothing there
too). Verified against the real capture used earlier (`route12km`): `nav_summary_json()`
decoded the same 336/336 points as `route_to_gpx()` did. For POIs, no backend change was
needed at all - `/api/pois`' existing raw text output already carries real
`Location.Latitude=`/`Location.Longitude=` fields per record (confirmed directly against
live hardware output this session, superseding the schema-uncertainty caveat this section's
own header comment used to carry); `PoiService::parseOnWatchPois()` just needed a real
regex over data that was already being fetched. Both pages now show a real, small `MapView`
per route/POI at zero extra USB cost - the coordinates were already in the same response
either way.

*POI export, matching Routes.* "1 POI => name => export (choose location, default downloads
folder)" - `PoiService::buildWaypointGpx()` builds a single-`<wpt>` GPX locally (no backend
round trip needed for one point that's already fully known client-side), fed into the same
`LocalFileService`/`FileDialog` save pattern Routes and Activities already use.

Backend server restarted again after these changes (see the earlier stale-process lesson in
this same entry) before any of the above was verified live.

---

## 2026-08-08: v2.3.0 tagged, connection auto-refresh redesigned, Garmin eTrex support built

**v2.3.0, tagged and committed for the first time.** This whole `ambitapp-v2/` tree (and the
supporting `tools/*.py` changes underneath it) had never actually been committed to this
repo before - a huge amount of real, hardware-verified work sitting only in the working
tree. Committed the real source (Qt app, backend, the `tools/*.py` files it depends on,
`AMBITAPP_SPEC.md`/`CREDITS.md`/`LICENSE`) - deliberately not the many scratch/binary
artifacts sitting alongside it in this same working tree (test flash-region dumps, `dist/`,
`obsolete/`, build logs, a couple of personal-route GPX files left out on privacy grounds
given the remote is a real public GitHub repo) - and tagged `v2.3.0` as stable. `CMakeLists.
txt`'s own `project(... VERSION ...)` and Settings' About text both bumped to match.

**Connection auto-refresh, redesigned again.** The 5s-always-polling design from earlier
this session is gone - real request: "if watch is connected don't refresh, if not connected
refresh with a 1 second interval... remove the refresh button." This is a genuine
improvement on the earlier design, not a reversion to the 2s ask that was flagged as too
heavy: `DeviceService` now polls at 1s *only* while genuinely searching for the watch
(uncapped, unlike the old 6-try retry logic it replaces) and stops entirely, at zero
ongoing cost, the instant `deviceInfoOk` is true. The manual "Refresh" button was removed
from Home - nothing left for it to do that isn't already happening automatically.

**Garmin eTrex support, built from a real spec.** Checked the real Android app
(opensportsync-main) first, as asked, before writing anything: a fully-built feature set
(`GarminModule.kt/.ts`, `GarminActivityService.ts`, `GarminGpxExportService.ts`, two
screens) backed by `GARMIN_USB_IMPORT_SPEC.md`, confirmed against real hardware (the
author's own eTrex 30). Garmin devices are nothing like the Ambit3 - plain USB Mass Storage
(FAT filesystem), not the NSP flash protocol - so this is a genuinely separate feature set,
not an extension of the existing Services, matching that spec's own explicit stance.

The one real architectural win over the Android implementation: Android needed `libaums` (a
userspace USB/SCSI/FAT driver) because BlissOS didn't reliably auto-mount MSC devices. The
same spec doc already confirmed a real eTrex 30 auto-mounts cleanly on desktop Linux via
udisks2, zero special handling - so the new `GarminService` needs no native USB code at
all: `QStorageInfo` finds the already-mounted volume, `QDir`/`QFile`/`QXmlStreamReader` do
everything else.

- **Discovery**: scans every mounted volume for `Garmin/GarminDevice.xml` (real, confirmed
  file) and parses `<Model>` (Description/SoftwareVersion/PartNumber - firmware formatted
  with Garmin's own "implied decimal point two digits from the right" convention, 501 ->
  5.01) plus the `GPSData` `DataType`'s two `<File>` entries, told apart by
  `TransferDirection` (`OutputFromUnit` = real activities live here; `InputToUnit` = where
  to write, always `Garmin/GPX`) - no hardcoded paths.
- **SD card detection**: real-hardware-unverified (this session had no SD card to test
  against) - Android identifies it via real USB topology (`libaums` sees both volumes are
  the same physical device); a desktop mounted filesystem doesn't expose that through
  `QStorageInfo` directly, so this instead treats another volume under the same parent
  removable-media directory without its own `GarminDevice.xml` as the SD card. Flagged
  honestly in the code as a heuristic, not claimed as confirmed the way the rest of this
  class's discovery logic is.
- **Activities, Routes, POI - real GPX parsing, real derived stats.** Real eTrex GPX carries
  none of `exercise_log.py`'s own `<extensions>` block (that's this project's own
  Ambit3-specific convention) - so distance/duration/ascent for a Garmin activity or route
  are computed from the track's own points: a real haversine great-circle formula for
  distance (a known formula, not derived custom math, matching this project's own stated
  preference), elevation-gain summation for ascent, first-to-last `<time>` for duration.
  POI files are recognized by BaseCamp's own real "Waypoints*.gpx" naming convention
  (confirmed against real files already on the reference eTrex).
- **SAFETY RULE, enforced in code, not just the UI**: `writeGpxToDevice()` refuses outright
  if no SD card volume is present - internal memory is never written to, no exceptions,
  matching the real Android app's own non-negotiable rule (confirmed with that app's
  author). Every "send to device" button in the UI (Routes, POI - both add-form and
  imported-list rows) is also disabled and shows an explicit warning box when this applies,
  per that same spec's own instruction that this has to be visible to the user, not just
  silently enforced.
- **UI, device-aware rather than duplicated**: Home's hero card shows either the Ambit3 or
  the new `EtrexIcon.qml` (a plain-shapes silhouette - a body, the eTrex's own distinctive
  antenna bump, a screen, two buttons - drawn rather than added as a font glyph, since no
  icon in this app's Material Symbols subset represents a handheld GPS unit) plus real
  firmware/part-number/SD-card fields, matching `GARMIN_USB_IMPORT_SPEC.md`'s own
  "Implementation-ready: device identification" section layout exactly. Activities/Routes/
  POIs are the *same* pages/UI as the Ambit3 versions, sourced from `GarminService` instead
  of `ActivityService`/`RouteService`/`PoiService` when a Garmin is the connected device
  (`HomeViewModel.isGarmin`) - matching the real request to reuse the existing feature
  rather than duplicate it. New Garmin backup Card (Backup page, Garmin-only, real file
  copy - not a database export or a parsed re-serialization) copies every real file from
  `Garmin/GPX` on every mounted volume into a user-chosen folder (default Downloads) - no
  separate `Garmin\POI` folder exists on real hardware per the same spec, POI files already
  live inside that same `Garmin/GPX` folder, so backing that one up covers both.

**Real bug caught and fixed during this same pass**: two spots (`RoutesPage.qml`'s new
device-aware Card) initially used `parent.parent.<property>` to reach a `Card`'s own custom
properties from a descendant - broken, because `Card.qml`'s `default property alias
content: contentItem.data` reparents a `Card`'s children into an internal `contentItem`,
not the `Card` itself, so `parent.parent` actually resolved to `contentItem`, not the
`Card`. The exact same class of bug as the earlier `SettingsPage.qml` `oauthDialogService`
scope issue this session, caught this time before ever compiling rather than after - fixed
by giving the `Card` an explicit `id` and referencing it directly (ids resolve by name
across the whole file regardless of the visual parent-child tree, unlike `parent`).

**Honesty note on verification**: everything above compiled cleanly and was verified live
against the real, connected Ambit3 to confirm zero regression to the existing path (device
hero, GPS orbit, cached Last Activity, Routes/POIs/Backup/Settings all navigated with no
QML errors in the app's own log). **No real Garmin/eTrex hardware was available this
session to verify the Garmin-specific code paths against** - the discovery/parsing/write
logic is built directly from `GARMIN_USB_IMPORT_SPEC.md`'s own real-hardware findings, not
guessed, but "matches a real spec" and "verified against real hardware" are different
claims, and only the former is true for this entry. A real eTrex test is still owed before
trusting this with an actual SD-card write.

**Follow-up, same day: Backup/Settings polish, v2.5.0 tagged.** Three small real requests
after reviewing the Garmin work above:
- Backup's "Backup & Restore," "Existing backups," and "Firmware" Cards are all Suunto-
  specific (the last one's own text even says "Suunto's own official app") - all three now
  hidden (`visible: !HomeViewModel.isGarmin`) while a Garmin is connected, since none of
  them have anything to do while one is.
- The Garmin backup button was "Choose folder and back up"; renamed to "Create backup now"
  to match Suunto's own wording exactly - still opens the folder-choose dialog first (a
  real difference from Suunto's fixed `~/AmbitAppBackups`), just worded the same way.
- Settings' "General" Card reports the Python backend bridge's own status, which Garmin
  support has nothing to do with (`GarminService` talks directly to a mounted filesystem,
  no backend involved). Swapped for a "Supported devices" Card while a Garmin is connected,
  rather than just hidden outright.

Version bumped to **v2.5.0** (`CMakeLists.txt`, Settings' About text) and tagged as stable,
superseding v2.3.0 as the current baseline (that tag is left in place, not deleted - a real
earlier milestone, not a mistake). Committed with the same scoping discipline as the
v2.3.0 commit (real source only, no build artifacts/logs). Neither tag has been pushed to
origin - that needs separate, explicit confirmation.

---

## 2026-08-08: Garmin support confirmed on real eTrex 30 hardware - v2.5.9, then a real bug fix

**Real hardware test, first one for the Garmin side of this app**: André's own eTrex 30,
the exact reference unit `GARMIN_USB_IMPORT_SPEC.md` was written against - confirmed via
Home showing the real firmware (5.01) and part number (006-B1305-00), matching that spec's
own worked example exactly. `screenshots/v8/` and `v9_home_real.png` are real captures from
this session, not staged.

**v2.5.9**: real feedback from that first test - the SD-card-only warning box on Routes/POI
had a loud tinted-orange background; restyled to a plain white (`Theme.card`) background
with grey (`Theme.mutedText`) text. Wording changed: "Garmin devices never accept writes to
internal memory" -> "writing to internal memory can break your device." Version bumped to
v2.5.9 and tagged stable (v2.3.0/v2.5.0 left in place, not moved).

**Follow-up, same session - two more real requests, then a real bug found from the live
data itself:**
- The warning box lost its frame entirely per a second round of feedback - now plain text
  matching each page's own muted description text one level down, no `Rectangle` wrapper at
  all.
- `MapView`'s auto-fit zoom margin loosened from 0.8 to 0.65 (35% padding instead of 20%)
  after a real track still looked slightly cropped by default.
- **Real bug, found directly from the live eTrex still connected to this session**: opening
  the real device's own "Current Track" activity showed **"Duration: 997h 21m"** for a
  35 km move - obviously wrong, caught by inspection, not a test failure. Root cause: a
  Garmin `Current.gpx` file can hold multiple disjoint `<trkseg>` recording sessions (real
  device behavior - nothing guarantees the file gets cleared between separate recordings),
  and `GarminService::parseActivityGpx()` was summing distance/duration across the *whole*
  file as if it were one continuous move - bridging a real multi-day gap between two
  sessions into one bogus number. Fixed: track points and timestamps are now grouped by
  segment (indexed by segment number, not a raw pointer into the growing `QList` - `QList::
  append()` can reallocate and invalidate that), and both distance and duration are summed
  *within* each segment only, never across a segment boundary. **Verified live against the
  same real file, same real session**: 997h 21m -> a correct **3h 38m**; 35.0 km -> **33.2
  km** (the bogus cross-segment "teleport" distance excluded). This is the first Garmin-side
  bug this project found from real data rather than from reading the spec - a genuine
  confirmation the earlier "not yet verified on real hardware" caveat was honest, not just
  cautious phrasing.

All four changes committed together (not yet re-tagged - a bugfix follow-up on v2.5.9, not
a new version number on its own).

## 2026-08-08: the "opensportsync-main has no git repository at all" risk (flagged above,
## 2026-08-07 entry) is resolved

Real `git subtree` import (not squashed) of `guiguoz/opensportsync`'s own upstream history
(29 commits) at prefix `android/` (commit `bc35f15`), then the real diverged state (Garmin
support, BLE, everything built on top since) landed as a follow-up commit (`a4d2680`) - see
that commit's own message for the file-by-file summary. `oss/opensportsync-main` on the
external drive is now a stale, no-longer-updated snapshot (last real edit 2026-08-07,
`2.3.4-beta`); checked directly, every difference between it and this repo's `android/` is
`android/` being ahead - nothing unique was left behind on the external copy. `HANDOFF.md`'s
"Two real apps" note and `CREDITS.md` updated to stop pointing anyone at the external
folder. One real, still-open item from the import itself, not resolved by this entry:
`a4d2680`'s own message flags `src/services/UpdateService.ts` as present in the real
upstream project but not carried into this fork's checkout - unconfirmed whether that's
because it was added upstream after this fork's snapshot was taken, or dropped locally on
purpose. Flagged for André/Vincent to decide.

Separately, real Android feature work landed the same day on top of this import - Kailash
support (settings read/write, including a writable Home Location field found from real BLE
captures and confirmed against the watch's own schema descriptor) and Ambit3 CustomModes
sport-mode editing (rename, autolap, HR limits, sensor pods, per-display field type) - not
detailed here to avoid duplicating the full write-up; see `custom_modes_andre.md`'s own
dated entries for the technical derivation.
