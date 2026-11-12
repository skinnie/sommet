# Packaging the Workout Builder as a standalone app

Turns `tools/workout_gui.py` (a local web server and browser page, standard library only) into
a double-click app for Windows, macOS and Linux, with [PyInstaller](https://pyinstaller.org/).
PyInstaller cannot cross-compile, so each binary is built on its own OS. The Sommet desktop
downloads already bundle the builder (the Apps page has an "Open Workout Builder" button), so
this page is only for running it on its own.

## What ships

Double-clicking the built app starts a local server on `127.0.0.1:8765` and opens it in your
default browser. It is the same page as `python3 tools/workout_gui.py`, without needing Python
installed. It can:

- build a guided workout step by step (time, distance or lap steps, with heart rate, pace,
  speed or power targets, and repeats);
- "Create Workout": compile it into the watch's own guidance format on the community compiler
  (`ambitappscompiler.azurewebsites.net`). This is the only step that needs an internet
  connection;
- "Install to Watch": add it to a sport mode's WORKOUT menu straight over USB. No SuuntoLink
  and no account, on Linux, macOS and Windows alike. The watch shows five workouts; when the
  menu is full the page offers to replace one, and nothing is installed until you choose;
- save every created workout as its own `.json` file in `~/Downloads/AmbitWorkouts` (your real
  Downloads folder, also when it has another name or lives in OneDrive), and keep a History
  list in the browser;
- export and import a workout as JSON.

See [Create a structured workout](../how-to/create-a-structured-workout.md) for using it.

The old SuuntoLink flow (write App Zone source, "Create App", "Add to SuuntoLink", "Import
compiled JSON") now lives in a separate tool, `tools/apps_gui.py` (the App Zone builder, on
`127.0.0.1:8767`). It is not part of this packaged app.

## Offline use

The page itself (HTML, CSS, JS) is self-contained: no CDN and no external fonts. Building and
editing a workout, History, export and import, and "Install to Watch" for a workout created
earlier all work with no network. "Create Workout" calls the live community compiler, which
this project doesn't run a copy of, so offline it fails with a plain "couldn't reach the
compiler" message. Create what you need while online; it is saved and usable offline after
that.

## One-time setup (per platform)

None, really - each build script now detects Python and PyInstaller itself:

- if Python 3 is missing, Linux/macOS print the right install command for you (macOS also
  opens the download page); Windows tries `winget` automatically, falling back to opening
  the download page if `winget` isn't available either;
- if PyInstaller is missing, it's installed into a throwaway local venv
  (`tools/packaging/.build-venv`) rather than touching your system/user Python - sidesteps
  Debian/Ubuntu's "externally managed environment" pip restriction entirely, since a venv is
  always installable-into regardless of that policy.

`workout_gui.py` and `guided_workout.py` use the standard library for the page and the
compile step. Writing to the watch over USB needs the `hid` package and the native hidapi
library, as for the rest of `tools/`.

## Building

PyInstaller does not cross-compile: build each one by running its script on that OS.

**Linux**:

```
./tools/packaging/build_linux.sh
```

Produces `dist/linux/Ambit3 Workout Builder`.

**Windows** (run on Windows):

```
tools\packaging\build_windows.bat
```

Produces `dist\windows\Ambit3 Workout Builder.exe`.

**macOS** (run on a Mac):

```
./tools/packaging/build_mac.sh
```

Produces `dist/mac/Ambit3 Workout Builder.app`, for whichever architecture (Intel or Apple
Silicon) the build machine has, **and copies it straight into `/Applications`** (replacing any
previous copy there) so there's no manual drag-and-drop step. If that copy fails (permissions),
it's left at `dist/mac/Ambit3 Workout Builder.app` for you to move yourself. **For both
architectures from one machine**: install the official universal2 build from python.org (not
Homebrew's, which is single-arch), then:

```
PYINSTALLER_TARGET_ARCH=universal2 ./tools/packaging/build_mac.sh
```

**First launch**: macOS Gatekeeper blocks a plain double-click of an unsigned, unnotarized app
the first time - it looks like nothing happened at all, not like an error dialog. Right-click
the app -> Open -> Open (once) to get past it, or System Settings -> Privacy & Security ->
scroll down -> "Open Anyway". Real code-signing needs an Apple Developer account, out of scope
here. After that first approval, if double-clicking ever still seems to do nothing, check
`~/Downloads/AmbitWorkouts/app.log` - the packaged app has no visible console window (`console=False` in
the spec), so a startup failure that used to be silent now gets logged there instead.

## Icon

`icon.icns` (macOS) / `icon.ico` (Windows) are this project's own, original design - four
simple bars in an alternating low/high pattern (an interval-training profile), not any real
Suunto asset. Generated by `make_icon.py` (needs Pillow; already run once, the output files
are committed like any other binary asset - a plain Windows/macOS build never needs Pillow
installed). Deliberate choice, not an oversight: using SuuntoLink's own icon (a real one sits
in `assets/mac/Contents/Resources/electron.icns` from the decompiled reference copy) would
look like impersonation and directly contradict the "not affiliated with Suunto" disclaimer
already in the app's own UI. Needs Pillow to (re)generate - reuses the same throwaway build venv `build_mac.sh`/
`build_windows.bat` already set up for PyInstaller, rather than touching system Python:

```
tools/packaging/.build-venv/bin/pip install pillow     # Windows: tools\packaging\.build-venv\Scripts\pip
tools/packaging/.build-venv/bin/python tools/packaging/make_icon.py   # Windows: ...\Scripts\python
```

## Disclaimer

Independent, unofficial software - not affiliated with, endorsed by, or supported by Suunto.
"Suunto" and the watch model names it reports compatibility with are trademarks of their
respective owner, used here only to describe compatibility. Provided as-is, no warranty of any
kind: test carefully before relying on it, and nobody involved in building it is responsible
for any malfunction, data loss, or damage to a watch from using it. This same notice is shown
in the app's own UI.

## Supported operating systems

Checked directly, not guessed - two independent floors, and the *higher* one is what actually
applies to this packaged app, not SuuntoLink's own alone:

| OS | SuuntoLink's own minimum | This app's real minimum | Which one binds |
|---|---|---|---|
| macOS | **10.11** (El Capitan) - read directly from the real `SuuntoLink.app`'s own `Info.plist` (`LSMinimumSystemVersion`, `assets/mac/Contents/Info.plist`) | **10.15** (Catalina) | PyInstaller's own current floor (pyinstaller.org's stated requirements) - higher than SuuntoLink needs, so 10.15 is the real number to publish, not SuuntoLink's 10.11 |
| Windows | Not stated as a single version number anywhere found, but real evidence it supports pre-Windows-10: the real SuuntoLink installer (`assets/WIndows apps/suuntoapp_local/install/windows_ucrt_install.js`) checks the registry for a Windows-10 version key and, if absent, installs the standalone Universal C Runtime redistributable - the standard accommodation for Windows 7 SP1/8/8.1 | **8** | PyInstaller's own stated floor ("PyInstaller runs in Windows 8 and newer") - within what SuuntoLink itself already appears to tolerate, so this is the binding number |
| Linux | N/A - SuuntoLink has no Linux build at all | no hard floor found or expected | most 64-bit distros from the last several years; PyInstaller's Linux requirements are mainly a reasonably current glibc, not a specific named release |

Practical wording for a release page: **macOS 10.15 (Catalina) or later, Windows 8 or later,
64-bit Linux.**

## Android

The Android app has its own Workout Builder and Training Program screens, written in
TypeScript, so it does not use this packaged app.
