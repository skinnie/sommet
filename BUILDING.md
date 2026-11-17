# Building Sommet from source

Most people don't need this: the ready-made downloads are described in [Install Sommet](docs/how-to/install.md).

> **Under testing.** Everything below is a work in progress and is provided for testing
> only. Use it on your own hardware, at your own risk.

There are two apps in this repo: a **desktop** app (Qt 6 / QML, for Linux / macOS /
Windows) and an **Android** app (React Native). Both talk to a watch over USB; the Android app
also over Bluetooth, and the desktop has experimental Bluetooth (Ambit3 family; tested on Linux with mixed results, not on macOS or Windows). Start by cloning:

```
git clone https://github.com/skinnie/sommet.git sommet
cd sommet
```

## Android

Prerequisites:
- **Node** ≥ 22.11 and npm
- **Java JDK 17** (for Gradle)
- **Android SDK** with `compileSdk 36`, `minSdk 28`, and **NDK `27.1.12297006`**
  (Gradle downloads the NDK/CMake automatically if the SDK command-line tools are set up).
  Set `ANDROID_HOME` (or `ANDROID_SDK_ROOT`) to your SDK path.
- A **signing config** — a release APK must be signed to install on a device.

```
cd android && npm install && cd ..

./build-android.sh release      # release APK
```

The script prints the finished APK's path (under
`android/android/app/build/outputs/apk/release/`). Install it on a connected device with:

```
adb install -r <path-to-apk>
```

## Desktop (Linux / macOS / Windows)

To build it yourself (on Linux the AppImage above is easier; building is for developers):

The desktop app is Qt 6 / QML with a Python backend that does the actual USB work. The
backend runs on the standard library; some features need extra packages, listed in
`tools/packaging/backend-requirements.txt` (`bleak` for Bluetooth, `garminconnect` for Garmin
Connect, `pyserial` for the T6 / X6HR cable, `numpy` for sleep stages). Prerequisites (all three platforms):
- **Qt 6.5+** and **CMake** (3.21+) with a C++ compiler. On **Debian 13 / Ubuntu 25.04+** the
  system Qt is new enough: `./build-desktop.sh` checks the packages and prints the
  `sudo apt install` line for anything missing (the full list is in `desktop/README.md`).
  **Ubuntu 24.04 / Mint 22** ship Qt 6.4: install Qt with `aqtinstall` and build with
  `CMAKE_PREFIX_PATH=~/Qt/6.8.3/gcc_64 ./build-desktop.sh`, without the system `qt6-*`
  packages (two Qt copies in one build give errors like "Cannot assign object of type
  Core.Settings").
- **Python 3.9+** on `PATH` (runs the backend)
- **USB access to the watch** (Linux): the `hid` module and a udev rule, or the watch is never
  found. On Mint/Ubuntu: `sudo apt install python3-hid libhidapi-hidraw0`, then
  `echo 'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1493", MODE="0666"' | sudo tee /etc/udev/rules.d/99-suunto.rules`
  and `sudo udevadm control --reload-rules`, and unplug/replug the watch. Other
  distributions: `pip install hid` - see [the runbook](docs/tutorials/runbook.md), step 0.3.

That's the whole list — the map is drawn with plain Qt, so there is **no extra native
library to build** (an earlier MapLibre dependency was removed; see `desktop/CMakeLists.txt`).

Build, then run:

```
./build-desktop.sh      # = cmake -S desktop -B desktop/build && cmake --build desktop/build
./run-desktop.sh        # starts the Python backend on :8766, then launches the app
```

**Run it with `run-desktop.sh`, not the bare binary.** The release downloads start their
bundled backend by themselves, but a source build has none bundled, so launching
`desktop/build/Sommet` directly shows "backend not running".

Per-platform build command and output (the two helper scripts are Linux/macOS shell
scripts; on Windows run the raw `cmake` commands):

| Platform | Command | Output | Status |
|----------|---------|--------|--------|
| **Linux** | `./build-desktop.sh` | `desktop/build/Sommet` | ✅ Built and run on real hardware (Linux Mint, Qt 6.12.0); source build also checked on Debian 13 (system Qt 6.8.2) |
| **macOS** | `cmake -S desktop -B desktop/build && cmake --build desktop/build` | `desktop/build/Sommet.app` | ⚠️ App runs; not tested with a watch yet |
| **Windows** | `cmake -S desktop -B desktop/build && cmake --build desktop/build` | `desktop/build/Release/Sommet.exe` | ✅ Release `.zip` tested with a real Ambit2 |

On macOS, `run-desktop.sh` starts the backend and opens `Sommet.app`. On Windows, start the
backend yourself before launching the binary: `cd desktop/backend && python server.py`.

## Interval Workout Builder (optional companion)

The **Apps** page has an **Open Workout Builder** button for building a structured interval
workout and installing it as a native guided workout in the watch's WORKOUT menu (Ambit3
family). That feature is powered by a **separate little tool**, the *Workout Builder* — it
opens in your web browser as its own local app, not inside the main window.

Two things worth knowing:

- **In the ready-made downloads it just works** — the Workout Builder is bundled inside the
  app, so the "Open Workout Builder" button launches it with nothing extra to install.
- **It also runs completely standalone** — you can use the Workout Builder on its own, with
  or without the main desktop app.

To run it standalone from source:

```
python3 tools/workout_gui.py      # opens http://127.0.0.1:8765 in your browser
```

To turn it into a double-click app instead (no Python needed to *use* it afterwards), build
it with the per-OS packaging scripts — run each on its own OS, they don't cross-compile:

```
./tools/packaging/build_linux.sh      # -> dist/linux/Ambit3 Workout Builder
./tools/packaging/build_mac.sh        # -> dist/mac/Ambit3 Workout Builder.app   (run on a Mac)
tools\packaging\build_windows.bat     # -> dist\windows\Ambit3 Workout Builder.exe (run on Windows)
```

Full details — what it does, offline use, and getting compiled workouts into SuuntoLink — are
in [`docs/tutorials/packaging.md`](docs/tutorials/packaging.md).
