# Sommet (desktop)

The real, polished Qt 6 / QML desktop app described in `../docs/reference/sommet-spec.md`. Started
2026-08-06 - see that file for the full spec (mission, principles, design language, the
full implementation step list this README tracks).

## Status

**All 11 implementation steps from docs/reference/sommet-spec.md are built, and it now compiles for
real.** Written 2026-08-06/07 with no Qt6 dev headers available to verify it; **built
successfully for the first time on 2026-08-07** on André's real Linux Mint machine (Qt
6.12.0 via `aqtinstall`, `maplibre-native-qt` built from source) - see "Cannot be built in
the environment this was written in" below for the one real bug that build found and fixed,
and `../V3_CHANGELOG.md` for the full account. Not yet run against the real watch end to
end through the GUI (that's the next real step), but the binary exists and links clean.

The spec's own QML -> ViewModels -> Services layering is followed for real throughout, not
skipped anywhere for convenience: seven C++ `Services` (`DeviceService`, `WeatherService`,
`ActivityService`, `RouteService`, `PoiService`, `BackupService`, all `QML_SINGLETON`),
paired `ViewModels` in QML for real presentation logic, `Card`/`Icon`/`MapView` as the shared
building blocks every page uses. Icons are a real, properly-licensed Material Symbols Rounded
font, subsetted to ~9 KB (see `assets/fonts/NOTICE.md`). Maps are drawn by a **plain direct XYZ
tile renderer** over OpenStreetMap, correctly attributed — Qt-only, with **no MapLibre and no
Qt Location/Positioning dependency** (the earlier note below about MapLibre-from-source is
historical: both GeoServices backends were tried and abandoned on 2026-08-07, see
`desktop/CMakeLists.txt` and `../V3_CHANGELOG.md`). So from source the only prerequisite is
**Qt 6.5+** (Mint's `apt` tops out at 6.4.2, so use `aqtinstall`) plus the backend running.

## Running it

**Most Linux users should not build from source — download the AppImage** (one file, bundles Qt
and the Python backend, runs on old distros and old CPUs like the Core 2 Quad Q9550). See
`packaging/appimage/README.md`. Building from source is a developer convenience: run
`../run-desktop.sh`, which starts the Python backend *and* the app — a bare `Sommet` binary with
no backend boots with Settings and every backend-fed page **blank** (see `src/services/backendprocess.cpp`).

**Building on Debian 13 / Ubuntu 24.04 or newer** (their own Qt is 6.8, no `aqtinstall` needed).
Checked on Debian 13 (Qt 6.8.2), 2026-11-12:

    sudo apt install build-essential cmake ca-certificates qt6-base-dev qt6-declarative-dev \
      qt6-declarative-dev-tools qt6-tools-dev libqt6sql6-sqlite qt6-svg-plugins \
      qml6-module-qtquick qml6-module-qtquick-controls qml6-module-qtquick-dialogs \
      qml6-module-qtquick-layouts qml6-module-qtcore qml6-module-qtquick-window \
      qml6-module-qtquick-templates qml6-module-qtqml-workerscript qml6-module-qtquick-effects \
      qml6-module-qtquick-shapes
    ./build-desktop.sh && ./run-desktop.sh

Without `qt6-svg-plugins` the app runs but some icons stay blank. Don't mix this with a Qt from
`aqtinstall`: two Qt copies in one build is what gives errors like "Cannot assign object of type
Core.Settings to property of type QQmlSettings*". The open/save windows of a source build are
GTK's when your desktop uses Qt's gtk3 theme, and that GTK window can crash on its own
(`gtkpathbar.c ... assertion failed`); the AppImage doesn't have this problem.

**What's genuinely live** (calls the real backend/Open-Meteo, not mock data): connection
status, weather (current + 3-day forecast, hides itself with no error on failure), Activities
(real GPX parsing into distance/duration/elevation/map per recorded move, "Apple Photos"
cards with a working detail view), Routes (on-watch summary + Import/Upload with a real
thumbnail preview and a rehearse-before-write flow), POIs (raw on-watch dump + a live
coordinate-preview Add form), Backup (create/list/rehearse/restore, fully real, the one
section with no caveats since `write_nav.py` already had this hardware-tested), Settings'
Weather section (manual lat/lon actually drives `WeatherService`), and - added 2026-08-07,
see `../V3_CHANGELOG.md` - Home's model/serial/battery/firmware/hardware version, via
`tools/device_info.py`, plus a real "Firmware" section on Backup (check + download-for-backup,
with an explicit, bold "cannot be used to flash the watch" warning). All of it **verified
against a real connected watch**, not just written and hoped for like most of this app.

**Honest, stated gaps, not silently missing:**
- GPS orbit validity on Home - a separate query (`0x0b15`, see `sgee_andre.md`), not part of
  the battery/firmware work above.
- Home's Last Activity / New Activities cards - real backend data exists, just not
  summarized into that specific UI shape yet.
- Sport-specific activity icons - the raw `sport_type` byte in exported GPX is never decoded
  to a name anywhere in this project's own tooling, so every card uses one generic icon
  rather than a guessed mapping.
- Downloading an on-watch route's actual GPS points, and POI import/export - both real,
  hardware-confirmed capabilities *elsewhere* in this project, but the code isn't in this
  repo's `tools/write_nav.py` copy yet, so those specific actions return honest errors
  instead of pretending to work.
- Activity detail's Charts/Laps/Export/Upload/Notes tabs, Connections (Intervals.icu/
  Runalyze/Strava), and offline MBTiles maps - each explicitly needs its own real piece of
  work (a charting library choice, real OAuth, etc.) that docs/reference/sommet-spec.md itself mostly
  marks as "Future" anyway.

## Cannot be built in the sandbox this was originally written in

This was scaffolded in a Linux sandbox with Qt6 *runtime* libraries but no Qt6 *development*
headers - real build/dev work needs a real Qt 6.5+ environment on real hardware, which is
what happened 2026-08-07 (Linux Mint, Qt 6.12.0 via `aqtinstall` since the distro's own
`apt` package tops out at 6.4.2, plus `maplibre-native-qt` built from source - no packaged
version exists). See `../V3_CHANGELOG.md`'s 2026-08-07 "first real Qt6 build" entry for the
exact package list and environment setup.

```
cmake -S . -B build
cmake --build build
./build/sommet     # or build/sommet.exe on Windows, build/Sommet.app on macOS
```

**The one real bug that first build found, now fixed:** `qt_add_qml_module`'s
auto-generated type-registration file (`sommet_app_qmltyperegistrations.cpp`) looks up every
`QML_ELEMENT`-tagged header by bare filename (`__has_include(<deviceservice.h>)`), not by
its real path under `src/services/` - without that directory on the include path, every one
of those checks silently fails, the `#include` gets skipped, and every service class fails
with "was not declared in this scope." Fixed with an explicit
`target_include_directories(sommet_app PRIVATE src/services)` in `CMakeLists.txt`. Worth
knowing if a future subdirectory of C++ `QML_ELEMENT` classes gets added and hits the same
thing.

**The second real bug, found by actually running it:** the compiled binary launched but every
page was a storm of `Unable to assign [undefined]` - every QML singleton (`Theme`, `Icons`,
`FeatureFlags`, `DeviceCapabilities`, `MapService`, all four ViewModels) had every property
undefined. Cause: `set_source_files_properties(... QT_QML_SINGLETON_TYPE true)` was called
*after* `qt_add_qml_module()` - too late, since that macro reads the property while
generating `qmldir`. Every one of those 9 files got registered as a plain type instead of
`singleton NAME ...`, confirmed directly in the generated `build/SommetUi/qmldir`. Fixed by
moving the whole `set_source_files_properties` block above `qt_add_qml_module()` in
`CMakeLists.txt`. Rebuilt: zero QML warnings, confirmed both in `qmldir` and the running
app's own (now empty) log.

If a future build throws CMake/QML errors beyond either of these two, that's expected and
useful information - report them back rather than silently patching around them.

## Backend bridge server - built, `backend/server.py`

Stdlib-only, same shape as `tools/workout_gui.py`. Wraps `tools/write_nav.py`,
`tools/exercise_log.py`, and `tools/sgee.py` by calling their real CLI entry points via
`subprocess` (not their internal functions directly - see the file's own docstring for why),
so every request gets exactly the same validation and safety behavior as running those tools
by hand.

**Every write is opt-in.** `/api/routes` and `/api/agps/update` both default to the
underlying tool's own dry-run/rehearsal mode and only pass `--write` through when the
request body sets `"confirm": true` - real hardware writes never happen by accident.

Endpoints: `GET /api/health`, `GET /api/nav` (routes+POIs currently on the watch, raw output
- not parsed yet, see the handler's own comment for why), `GET /api/activities` (recorded
moves as GPX/FIT), `GET /api/pois` (raw, same reasoning as `/api/nav` - see Step 9 below),
`GET /api/backups` + `POST /api/backup` + `POST /api/restore` (Step 10, wraps `write_nav.py`'s
own `nav --save`/`restore`), `GET /api/device` (model/serial/firmware/battery, added
2026-08-07, `tools/device_info.py` - see `../V3_CHANGELOG.md`), `POST /api/routes`,
`POST /api/agps/update`.

POI import/export (GPX and typed coordinates) and AGPS write are both **confirmed working,
real hardware, 2026-08-06** (see `HANDOFF.md`'s POI section and Milestone 6 row) - real
capability, not a gap. `POST /api/pois` still returns a 501 here specifically though, and
that's a narrower, honest statement: the actual code for it isn't in *this repo's* copy of
`tools/write_nav.py` yet (only preserves POIs already on the watch across a write, can't add
one), so there's nothing correct for this endpoint to call until that code is located or
ported in - not that the feature doesn't work.

**Tested without hardware, from this sandbox**: every endpoint's error handling, temp-file
cleanup, and JSON plumbing - all clean, no crashes, no leaked files. The AGPS endpoint went
further and validated for real: it fetched genuine live orbital data (70,659 bytes) from
Suunto's real, unauthenticated server and ran a full dry-run simulation through `sgee.py`
successfully - on top of `sgee.py --write` itself already being hardware-proven separately.
What's specifically untested is this new HTTP wrapper's own `--write` path end to end
against a real watch - a normal, expected gap for code written this session, not a doubt
about AGPS writing itself, which is real and proven.

## Maps - built, but needs a real extra dependency first

docs/reference/sommet-spec.md asks for MapLibre specifically, not Qt's own built-in "osm" map plugin, so
this needs [maplibre-native-qt](https://github.com/maplibre/maplibre-native-qt) - the real,
official, actively-maintained MapLibre Qt bindings (confirmed current, last updated the same
week this was written). **This is a separate native library, not part of Qt6 itself** - build
and install it first (its own `docs/Building.md` has the real steps), then point this
project's CMake at it via `QMapLibre_DIR` or `CMAKE_PREFIX_PATH` before `cmake -S . -B build`
will get past `find_package(QMapLibre ...)`.

- `qml/MapService.qml` - the abstraction the spec asks for. One property, `styleUrl`, is the
  entire interface: `components/MapView.qml` is the only thing that reads it, so switching
  online/offline (MBTiles, still marked "future" in the spec itself, not built) never touches
  any page.
- `assets/map/osm-raster-style.json` - a real, minimal, correctly-attributed MapLibre style
  pointing at OpenStreetMap's own raster tile server. Deliberately raster, not MapLibre's own
  demo vector style (`demotiles.maplibre.org`, used in their example code) - that's
  documented as a testing-only endpoint, not something to depend on for real; plain OSM
  raster tiles are the real "OpenStreetMap" source the spec actually asks for, and only need
  the attribution `MapView.qml` already shows in-app (OSM's own tile usage policy expects
  that, not just good manners).
- Not wired into any page yet - Steps 7-9 (Activities/Routes/POIs) are where a map actually
  needs to show something real (a route line, a POI pin), not this step.

## Architecture decision: the backend stays Python

`docs/reference/sommet-spec.md`'s own architecture diagram is:

```
QML -> ViewModels -> Services -> Current Backend -> libambit
```

"Current Backend" and "libambit" don't exist in C++ yet, and everything that actually talks
to the watch - USB nav-database reads/writes, route/POI encoding, exercise-log export, AGPS,
sport-mode display wiring, workout compiling - already exists, hardware-tested, in this
repo's Python tooling (`../tools/*.py`). Rewriting all of that from scratch in C++ would mean
re-deriving a lot of hard-won reverse-engineering, directly against the spec's own "reuse
existing code, refactor only when necessary" rule.

**Decision (confirmed with André, 2026-08-06): keep Python as the real backend.** A local
HTTP/JSON server (stdlib-only, same pattern already proven in `tools/workout_gui.py`) wraps
the existing `tools/*.py` modules and exposes what the Services layer needs - device info,
activities, routes, POIs, and eventually sport modes. C++ `Services` classes become thin
HTTP clients (`QNetworkAccessManager`) against that local server. QML still never talks to
the backend directly either way - it only ever sees `ViewModels`, matching the spec's own
layering rule, just with Python sitting where the diagram draws "libambit".

Built now, both halves: `backend/server.py` (above) and the first real Service,
`DeviceService` (`src/services/`) - a `QML_SINGLETON` C++ type, backend host/port hardcoded
to `127.0.0.1:8766` for now (a real Settings-driven config is Step 11's problem, not before).
`HomeViewModel.qml` sits on top of it for Home's specific presentation needs, and no page
talks to `DeviceService` directly - the layering is real, not just drawn in a diagram.

## Layout

```
desktop/
  CMakeLists.txt
  src/
    main.cpp              - bootstraps the app, registers the bundled icon font, nothing else
    services/
      deviceservice.h/cpp    - Step 4: first real Service, thin HTTP client against backend/
      weatherservice.h/cpp    - Step 5: calls Open-Meteo directly, no backend involvement
      activityservice.h/cpp    - Step 7: parses backend GPX into distance/duration/track
      routeservice.h/cpp        - Step 8: on-watch summary + generic GPX import/upload
      poiservice.h/cpp           - Step 9: raw on-watch dump + honest 501 add attempt
      backupservice.h/cpp         - Step 10: thin wrapper over write_nav.py's own save/restore
  assets/
    fonts/
      MaterialSymbolsRounded.ttf - subsetted icon font, see NOTICE.md for license + how to
                                    regenerate after adding a new icon
    map/
      osm-raster-style.json  - Step 6: real, attributed OpenStreetMap MapLibre style
  qml/
    Main.qml             - the real window: NavRail + a Loader over the current page
    Theme.qml            - Step 1: color/spacing token singleton, light+dark
    Icons.qml             - Material Symbols Rounded codepoints, by name
    FeatureFlags.qml      - sportModes=false until that feature is real
    DeviceCapabilities.qml - static placeholder capability flags (see its own header comment)
    MapService.qml          - Step 6: the map abstraction, one property (styleUrl)
    components/
      Card.qml             - Step 2: the base surface every card in the app builds on
      Icon.qml              - one glyph from Icons.qml, sized/colored
      NavItem.qml            - one row in NavRail
      NavRail.qml             - Step 3: the sidebar itself
      PagePlaceholder.qml     - temporary content for pages whose real step hasn't landed yet
      WeatherCard.qml          - Step 5: hides itself with no error if the fetch fails
      MapView.qml               - Step 6: the only thing that reads MapService.styleUrl
      ActivityCard.qml           - Step 7: map preview thumbnail + stats
      ActivityDetail.qml          - Step 7: large map + Overview (real) + 5 honest placeholders
    viewmodels/
      HomeViewModel.qml       - Step 4: Home's view of DeviceService (connection status, etc.)
      WeatherViewModel.qml     - Step 5: WMO weather code -> icon/label mapping
      ActivityViewModel.qml     - Step 7: duration/distance/elevation formatting, map center
      RouteViewModel.qml         - Step 8: distance formatting, name search filter
      (no PoiViewModel - Step 9's PoiService has no presentation logic worth the extra file)
    pages/
      HomePage.qml            - Step 4/5: real device-hero + Connections + Weather
      ActivitiesPage.qml       - Step 7: real card grid + detail view, backed by ActivityService
      RoutesPage.qml            - Step 8: real on-watch list + Import/Upload, backed by RouteService
      PoisPage.qml               - Step 9: raw on-watch dump + Add form with live map preview
      BackupPage.qml              - Step 10: real create/list/rehearse/restore, no caveats
      SettingsPage.qml, SportModesPage.qml - still PagePlaceholder content; Settings is
      Step 11, Sport Modes stays hidden regardless
  backend/
    server.py                - the Python bridge server, see its own section above
```
