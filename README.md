# Sommet

Sommet, french name for Peak, is an application to sync and manage Suunto Ambit 1, Ambit 2, Ambit 3, Traverse , Traverse Alpha and Kailash families/variants + Garmin Etrex 10/20/20x/30/30x GPSr.
The aim is to provide the same features to Android, Linux and later on if possible Mac and Windows.


All features that are visible in the app are available by USB/USB-OTG, and some are also available/being tested by bluetooth.
Bluetooth currently is only available as experimental feature, which can be enabled in settings. Currently it works on the first pair, but then it is flaky.
Within the features we have:
- Sync time with watch: from the current time of the device or another timezone
- Update orbital data: gps and glonass (for the glonass devices)
- Device manual: download them in pdf format
- Activities: download from the watch,view on a map, export them in gpx, fit, to local, cloud or 3rd party services (strava, runalyze, intervals.icu, others to come)
- Routes: Import/send a gpx route to the watch. export the routes on the watch to gpx format
- POIs: Import/send a POI to the watch (via a map search, or coordinates), export the POIs on the map to gpx
- Calendar: visual calendar with days where activities were done
- Totals: totals of a certain activity
- Backup: backup routes and POI altogether to a folder. Backup of your watch firmware (these watches firmwares should be matching the hw version)
- Intervals: a visual workout builder, which later can be used as an app on the watch (I recommend to use suunto link to install them)
- Sports modes: sports mode editor for the watches
- Firmware: flasher of firmware to the watch
- Suunto HR Sensor: connection of the sensor, check battery level, firmware version, etc
- Kailash specific: places visit, countries visited, export activity (categorized by default as walk)
- Etrex specific: download activities (categorized by default as walk), import/export POIs, import/export routes.
- GPS Pod Support : integration of Suunto GPS Pod support (experimental - no device to test)
- Suunto T6 support: integration of Suunto T6 support (experimental - no device to test)


## Screenshots

The desktop app (Linux):

| | | |
|:---:|:---:|:---:|
| ![Home](screenshots/01-home.png)<br>**Home** | ![Routes](screenshots/02-routes.png)<br>**Routes** | ![POIs](screenshots/03-pois.png)<br>**POIs** |
| ![Backup](screenshots/04-backup.png)<br>**Backup** | ![Settings](screenshots/05-settings.png)<br>**Settings** | |


I am not at all responsible for errors, failures, bricked devices, etc. This software is provided as is.
Currently main features were tested with Ambit 3, Traverse and Kailash in Android and Linux Mint 22. Normally most features work with Ambit 1 and 2 (I don't have yet the watches to test). Complications of each feature may require further testing (sports modes, settings, etc)


The app was entirely vibe coded with Claude, given my lack of knowledge. It was fueled in stubbornness, coffee and good will to bring some features for these watches for some operative systems.
The app is free, no login/account and can be used almost fully offline (Orbital data, and intervals workout builder need internet)





## F.A.Q.:


- Found something that you think is not correct from a dev perspective, contact me. I have zero experience, but willing to learn.
- Found a bug? open an issue with logs and screenshots. Easier if you follow up [this](https://claude.ai/code/artifact/c88a8a2b-31cc-4ca1-93ef-b120a48fc1ae)
- Are the Spartan, Suunto 5,9, and later suuntos be supported: No. Suunto link and suunto app already provide great features for them.
- Can we get back scheduled workouts, guided workouts and training plans feature back from Movescount? I didn't arrive, if you have material that can help feel free to contact me
- Can you implement X,Y,Z feature? Propose, beware that if I don't have the hardware if may be complicated to implement it
- My watch bugged, what I do now?! Go to mac or windows, connect to suunto link and reset. Your settings will be lost but your watch will be alive.
- Are you gonna implement translations to X,Y,Z language? For the moment no, but if anyone has envy to do it go ahead.


## Why?


There are newer watches, "better watches", more accurate watches, more confortable, more everything. And I also enjoy them, but I always enjoyed the Ambit line up.
Using an Ambit is like driving a classic car, is like home. 

When movescount was closed features were lost, and some were unfortunately not able to be implemented due to complexity/lack of resources/timeline from Suunto. 
The use of suunto app + suunto link although grateful was far from easy for the average joe, putting these watches to the side. The main of this app is also to bring some easyness to this and so that these devices could be used for more people and not end in the landfill.
Fortunately we leave wonderful times, and with AI and community work, why not try to get those features back? 


## Desktop: Why Linux first?
First because there was one projects already advanced: openambit.
Second, because linux is also, on my opinion a great way to "revive" old hardware and make usable again.
Third: windows and mac users have suunto app which can get them to the functionalities. Normally the app will run on mac and windows, I just haven't tested it.


## Mobile: why android?
First because there was one projects already advanced: opensportsync.
Second because I have an old Panasonic Thoughpad windows tablet that I can get android on it, also lots of old android tablets/phones that can be used for this and/or be revived with custom roms.


## Building and installing

> **Under testing.** Everything below is a work in progress and is provided for testing
> only. Use it on your own hardware, at your own risk.

There are two apps in this repo: a **desktop** app (Qt 6 / QML, for Linux / macOS /
Windows) and an **Android** app (React Native). Both talk to a watch over USB; the Android
app additionally supports Bluetooth. Start by cloning:

```
git clone https://github.com/skinnie/sommet.git ambit-app
cd ambit-app
```

### Android

**The recommended path is to install the prebuilt APK — you do not need to build anything.**


1. On your Android device, open the [latest release](https://github.com/skinnie/sommet/releases/latest)
   and download `app-release-testing.apk`.
2. Tap the downloaded file to install it. The first time, Android will ask you to allow
   "Install from unknown sources" for your browser/file manager — allow it, then retry.
3. To sync over USB you also need a **USB-OTG cable/adapter** between the phone and the
   watch; Bluetooth needs no cable.

This is a signed, self-contained release build — it runs on its own, with **no Metro
development server** required.

<details>
<summary><b>Building the release APK yourself</b></summary>

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

</details>

### Desktop (Linux / macOS / Windows)

**Windows and macOS users: you don't need to build anything.** Each GitHub release carries
a ready-made **Windows `.zip`** (unzip, double-click `ambitapp.exe`) and **macOS `.dmg`**
(open, drag to Applications) built automatically in the cloud — see the
[latest release](https://github.com/skinnie/sommet/releases/latest). They are unsigned, so
the first launch shows a one-time "unverified developer" prompt: on macOS right-click the
app → Open → Open; on Windows click "More info" → "Run anyway".

> **Note:** the watch engine (the Python helper) is bundled inside these downloads and the
> app starts it automatically, so no separate setup is needed. This end-to-end packaging is
> new and **not yet confirmed against a real watch on Mac/Windows** — if a download misbehaves,
> building from source with `run-desktop.sh` (below) is the proven path.

To build it yourself (the only path on Linux):

The desktop app is Qt 6 / QML with a small stdlib-only Python backend that does the actual
USB work. Prerequisites (all three platforms):
- **Qt 6.5+** and **CMake** (3.21+) with a C++ compiler
- **Python 3.8+** on `PATH` (runs the backend — no pip packages needed, stdlib only)

That's the whole list — the map is drawn with plain Qt, so there is **no extra native
library to build** (an earlier MapLibre dependency was removed; see `desktop/CMakeLists.txt`).

Build, then run:

```
./build-desktop.sh      # = cmake -S desktop -B desktop/build && cmake --build desktop/build
./run-desktop.sh        # starts the Python backend on :8766, then launches the app
```

**Run it with `run-desktop.sh`, not the bare binary** — the app does not spawn its own
backend, so launching `desktop/build/ambitapp` directly shows "backend not running".

Per-platform build command and output (the two helper scripts are Linux/macOS shell
scripts; on Windows run the raw `cmake` commands):

| Platform | Command | Output | Status |
|----------|---------|--------|--------|
| **Linux** | `./build-desktop.sh` | `desktop/build/ambitapp` | ✅ Built and run on real hardware (Linux Mint, Qt 6.12.0) |
| **macOS** | `cmake -S desktop -B desktop/build && cmake --build desktop/build` | `desktop/build/AmbitApp.app` | ⚠️ Should work; not tested yet |
| **Windows** | `cmake -S desktop -B desktop/build && cmake --build desktop/build` | `desktop/build/ambitapp.exe` | ⚠️ Should work; not tested yet |

On macOS and Windows, start the backend yourself before launching the binary:
`cd desktop/backend && python3 server.py` (use `python` on Windows). Only the **Linux**
build is confirmed on real hardware so far.

### Interval Workout Builder (optional companion)

The app has an **Intervals** section for building a structured interval workout and compiling
it into a real Suunto App for the watch (Ambit3 only). That feature is powered by a **separate
little tool**, the *Workout Builder* — it opens in your web browser as its own local app,
not inside the main window.

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

### Firmware flashing — urgency only

Flashing watch firmware from the app **is possible and has been verified on real hardware**
(an Ambit3 Peak was flashed and recovered end to end over USB). Even so, **we only recommend
it in an emergency** — when a watch needs firmware and no computer with SuuntoLink is
available. For a normal firmware update, use SuuntoLink; the in-app flasher is a fallback,
not the default path.

This is **especially true on Android**, where flashing goes over a USB-OTG cable: OTG cables
and adapters may be unreliable, and USB power delivery during the flash can be marginal. A
failure mid-flash can leave the watch in bootloader mode.

Whenever you do flash — desktop or Android — make sure the **phone/computer, the watch, and
(on Android) everything on the OTG chain are fully charged** before you start, and don't
interrupt it.


## How can I contribute to this project?
Donations are welcome, paypal, mbway, bank transfer, but also captures of watches I don't have as explained [here](https://claude.ai/code/artifact/c88a8a2b-31cc-4ca1-93ef-b120a48fc1ae).
Also with your time and skills to correct bugs/implement features.
To share it, so more people put these devices to use and not on the landfill


## I loved so much my watch but it has:
- Dead battery: amazon, aliexpress and youtube are your friends
- Strap is broken: suunto still sells them new, also amazon, aliexpress, etc
- Connect to moveslink icon: try to charge it overnight, connect and disconnect on suunto link and reset firmware


## Legal & licenses
Independent, unofficial software — **not affiliated with, endorsed by, or supported by Suunto or
Garmin.** Suunto, Ambit, Traverse, Kailash, T6 Garmin and eTrex are trademarks of their respective
owners, used here only to describe compatibility.

This software is provided **as is, without warranty of any kind** — you are responsible for
anything that happens to your device.

Licensed under the **[GNU GPLv3](LICENSE)** — the same license as
[openambit](https://github.com/openambitproject/openambit), whose `libambit` this project's
protocol work is checked against throughout. Because it is GPLv3, the full source is this
repository; you may use, study, modify and redistribute it under the same license.

- **Desktop** links **Qt 6** under the **LGPLv3** (dynamically linked — you may relink it against
  your own build of Qt).
- **Android** uses **React Native** and its ecosystem (**MIT**).

## Credits

Built on the real work of others:

- **[openambit](https://github.com/openambitproject/openambit)** — `libambit`, the reference the
  USB/BLE protocol work is checked against (GPLv3).
- **opensportsync** — the starting point for the Android app.
- **marguslt** — firmware-download recipe, gists, Movescount knowledge.
- **sebchastang** — published Suunto App Zone interval-training scripts.
- **[bwaldvogel/openmoves](https://github.com/bwaldvogel/openmoves)** — Openmoves.
- **[iwanders/gps_track_pod](https://github.com/iwanders/gps_track_pod)** — Suunto GPS Track Pod
  support (MIT), vendored in `tools/vendor/gpspod/`.
- **[evelbulgroz/suunto-t6-sync](https://github.com/evelbulgroz/suunto-t6-sync)** — Suunto
  T6/T6c/T6d read support (MIT), vendored in `tools/vendor/suunto_t6_sync/`; basis for the
  experimental T6 heart-rate export and GPS-Track-Pod merge.
- **App Zone workout examples** — real published App-Zone interval scripts the workout findings
  were checked against: [claha/suunto](https://github.com/claha/suunto) (its `Suunto.mod(STEP,N)`
  repeat encoding), [follesoe/suunto-ambit-intervals](https://github.com/follesoe/suunto-ambit-intervals),
  [hefler/SuuntoApps](https://github.com/hefler/SuuntoApps),
  [AdamHodgson/Suunto-Interval-Training](https://github.com/AdamHodgson/Suunto-Interval-Training),
  [Httqm/Suunto](https://github.com/Httqm/Suunto).
- **[ruvido/goambit](https://github.com/ruvido/goambit)** & **[AlexLBraits/ambit2gpx](https://github.com/AlexLBraits/ambit2gpx)**
  — independent implementations of the same cloud-free USB paths (route upload / activity read),
  confirming they're real.
- **[mihaildemidoff/suunto-sml-model](https://github.com/mihaildemidoff/suunto-sml-model)** —
  a JAXB model of Suunto's SML format, a reference for the exercise-log work.
- the **Suunto forum community** and **wanarun.net**.

Services and libraries the app talks to at runtime:

- **[cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect)** (MIT) —
  the Garmin Connect client behind activity, weight and health sync (`tools/garmin_sync.py`,
  `tools/garmin_weight.py`).
- **[intervals.icu](https://intervals.icu/)** — its public API backs activity/gear import and
  export, the wellness and weight feeds, and the profile/activity-level writes to the watch.
- **[joaodrp/wahoo-systm-mcp](https://github.com/joaodrp/wahoo-systm-mcp)** — the Wahoo SYSTM
  workout catalogue the Coach can read from (its offline sample ships as `coach/data/`).
- **[Anthropic Claude API](https://www.anthropic.com/api)** — optional, powers the Coach chat
  when you supply your own API key. The app works fully without it.
- **[Qt](https://www.qt.io/)** (LGPLv3) — the desktop app's UI framework; **React Native** for
  the Android app.


## Data & assets:

- Map data © **OpenStreetMap** contributors, under the **Open Database License (ODbL)**. Tiles:
  **CyclOSM** / OpenStreetMap France, standard OSM, and **IGN Géoplateforme** (France).
- Weather by **[Open-Meteo](https://open-meteo.com/)** (CC BY 4.0).
- Icons: **Google Material Symbols** (Apache License 2.0).
