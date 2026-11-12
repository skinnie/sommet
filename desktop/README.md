# Sommet (desktop)

The Qt 6 / QML desktop app for Linux, macOS and Windows. A Python backend
(`backend/server.py`, on `127.0.0.1:8766`) does the USB and Bluetooth work; the Qt app talks to
it over local HTTP.

## Building and running

Most Linux users don't need to build: download the AppImage (see
`packaging/appimage/README.md`). Windows and macOS users get a `.zip` and a `.dmg` with every
release. Prerequisites, per-platform commands and the udev rule are in the main README,
"Building and installing".

From the repo root:

    ./build-desktop.sh      # cmake -S desktop -B desktop/build && cmake --build desktop/build
    ./run-desktop.sh        # starts backend/server.py, then the app

The binary is `build/Sommet` (`build/Sommet.app` on macOS). Release builds start their bundled
backend themselves (`src/services/backendprocess.cpp`). A source build has none bundled, so
use `run-desktop.sh`; a bare `Sommet` with no backend shows "backend not running".

The QML is compiled into the binary, so a QML change needs a rebuild.

**Building on Debian 13, Ubuntu 25.04 or newer** (their own Qt is 6.8, no `aqtinstall` needed;
`build-desktop.sh` checks these packages and prints what's missing). Ubuntu 24.04 and Mint 22
have Qt 6.4: use the AppImage, or Qt from `aqtinstall` only.
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

## Structure

`src/main.cpp` starts the app. The C++ services in `src/services/` (device, activities, routes,
POIs, weather, tile cache, sync and so on) are `QML_SINGLETON` types; most of them are HTTP
clients of the Python backend. The QML is in `qml/`: `Main.qml` holds the window and the page
list, the pages are in `qml/pages/`, shared parts in `qml/components/` and presentation logic in
`qml/viewmodels/`. Fonts, icons and map styles are in `assets/`. The backend stays in Python
because the watch code in `../tools/*.py` is already tested on real watches.

## Maps

Maps are drawn by a plain XYZ tile renderer with Qt only (no MapLibre, no Qt Location). Tiles are
cached, and you can save offline areas to use a map without internet
(`src/services/tilecacheservice.cpp`).

## Checks

Run `../tools/check.sh` before every commit. It runs every automatic check that needs no watch.

## Status

Released: AppImage, Windows `.zip` and macOS `.dmg` with every release. What changed in each
version is in `../docs/reference/v3-changelog.md`.
