# Sommet desktop — Linux AppImage

A single-file build of the Linux desktop app that a user downloads and runs with **no toolchain,
no Qt install, and no Python**. It is the answer to the real problem a 10-year-old watch brings
with it: its owner often has a 10-year-old computer and an older distro, and the from-source build
asks for Qt 6.5+ (Mint's own `apt` tops out at 6.4.2) plus a running Python backend. The AppImage
carries all of that inside itself.

This is the Linux counterpart to the Windows `.zip` and macOS `.dmg` the release workflow already
produces. Like them, it bundles the **frozen Python backend** (`backend/sommet-backend`, made with
PyInstaller) next to the executable, exactly where `desktop/src/services/backendprocess.cpp`
looks for it — so the app auto-starts the backend and **Settings and every other backend-fed page
are populated on first launch**. (Running a bare from-source `Sommet` binary without the backend
is what leaves those pages blank — see `run-desktop.sh`.)

## How it is built — and why it is built the way it is

The authoritative builder is the **`linux` job in `.github/workflows/desktop-release.yml`**. Two
rules there matter for old hardware, and both are easy to get wrong:

1. **Build on an OLD base (build-old, run-everywhere).** An AppImage bundles Qt and the app but
   still links against the **glibc of the build machine**. Built on a current distro it would
   demand a current glibc and refuse to start on an older one — the exact failure we are fixing.
   The CI job therefore builds inside an **`ubuntu:20.04` container** (glibc 2.31, 2020), so the
   result runs on Mint 22.x and on distros years older.

2. **Target the baseline CPU (`-march=x86-64 -mtune=generic`).** The reference old machine is an
   Intel **Q9550** (Core 2 Quad, 2008): it has SSE4.1 but **no SSE4.2, no POPCNT, no AVX** — i.e.
   x86-64-**v1** only. A binary compiled with `-march=native` or any AVX default `SIGILL`s on that
   CPU. Qt's own `aqt` binaries are already generic x86-64; we only have to keep our own compile
   flags conservative. (The old MapLibre dependency that used SIMD is gone — maps are a plain XYZ
   tile renderer now, Qt-only. See `desktop/CMakeLists.txt`.)

The Qt version is **6.8** (`aqt`), chosen as the highest an old box runs comfortably while
satisfying the app's `6.5+` floor. Newer Qt works too; 6.8 is just the pinned bundle target.

### FUSE
AppImages mount themselves with libfuse2. Older distros have it; some very new ones dropped it. If
a user's machine has no libfuse2, the AppImage still runs with:

    ./Sommet-linux-x86_64.AppImage --appimage-extract-and-run

### Graphics libraries (checked on Debian 13, 2026-11-12)
The AppImage leaves out the OpenGL/EGL libraries on purpose: they have to match the computer's own
graphics driver. Every desktop install has them, but a minimal one may not. If it won't start and
says `libOpenGL.so.0` or `libEGL.so.1` is missing:

    sudo apt install libopengl0 libegl1

Checked: v0.2.65 starts on a clean Debian 13 with only those two (plus `libgpg-error0`, which every
desktop has), the backend answers, and the open/save windows are Qt's own (or the desktop
portal's), so the GTK save-window crash seen in a Debian 13 source build cannot happen.

## Updates

The release attaches two files: `Sommet-linux-x86_64.AppImage` and its `.zsync`. The AppImage
carries embedded update information (`gh-releases-zsync|skinnie|sommet|latest|Sommet-linux-x86_64.AppImage.zsync`),
so it can be updated in place with **delta downloads** (only the changed bytes — kind on a slow
line):

    appimageupdatetool ./Sommet-linux-x86_64.AppImage

In-app, `UpdateService` already checks the GitHub Releases API once a day and offers the download
made for this computer; with this asset in place, Linux now gets a direct AppImage download rather
than only the release page.

## Building it yourself (optional)

`build-appimage.sh` in this folder does the assembly locally. For a portable result it must run on
an old base — use the CI job, or run the script inside an `ubuntu:20.04` container. Run on a
current distro it still produces a working AppImage, but one that only runs on distros as new as
the build machine.

## Credits and licenses

The AppImage is assembled with these tools (added to the project credits):

- **linuxdeploy** and **linuxdeploy-plugin-qt** — MIT license. https://github.com/linuxdeploy
- **appimagetool** / AppImageKit — MIT license. https://github.com/AppImage/AppImageKit
- **AppImageUpdate** (`appimageupdatetool`) — MIT license. https://github.com/AppImage/AppImageUpdate

These are build/packaging tools invoked at release time; none of their source is vendored into
Sommet. `libhidapi` (bundled so the frozen `hid` backend can find the watch over USB) is
GPL-3.0-or-later / BSD-3-Clause (dual) — https://github.com/libusb/hidapi — and ships as an
unmodified shared library inside the AppImage.
