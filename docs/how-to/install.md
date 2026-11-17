# Install Sommet

Ready-made downloads for every platform are on the [Releases](https://github.com/skinnie/sommet/releases) page. To build from source instead, see [BUILDING.md](../../BUILDING.md).

## Linux

Download the **AppImage** from the [Releases](https://github.com/skinnie/sommet/releases) page,
make it executable and run it — one file, no Qt install, no Python, nothing to set up:

```
chmod +x Sommet-linux-x86_64.AppImage
./Sommet-linux-x86_64.AppImage
```

It is built to run on **older distros and older CPUs** (tested target: Linux Mint 22.x on an Intel
Core 2 Quad Q9550), because an old watch often means an old computer. If your machine has no
libfuse2, run it with `./Sommet-linux-x86_64.AppImage --appimage-extract-and-run`. Updates are
delta downloads via `appimageupdatetool`, and the app tells you when a new version is out. Building
from source is only for developers — see `desktop/README.md` and `desktop/packaging/appimage/README.md`.

To use a watch over USB, Linux also needs a udev rule, once. The AppImage can't add it itself:
without it the app sees the watch but gets "Permission denied". Install the rule, then unplug
and replug the watch:

```
curl -fsSL https://raw.githubusercontent.com/skinnie/sommet/main/desktop/packaging/appimage/99-suunto-sommet.rules | sudo tee /etc/udev/rules.d/99-suunto-sommet.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## Android

**The recommended path is to install the prebuilt APK — you do not need to build anything.**

1. On your Android device, open the [latest release](https://github.com/skinnie/sommet/releases/latest)
   and download `Sommet_vX.Y.Z.apk` (X.Y.Z = the release's version).
2. Tap the downloaded file to install it. The first time, Android will ask you to allow
   "Install from unknown sources" for your browser/file manager — allow it, then retry.
3. To sync over USB you also need a **USB-OTG cable/adapter** between the phone and the
   watch; Bluetooth needs no cable.

This is a signed, self-contained release build — it runs on its own, with **no Metro
development server** required.

## Windows and macOS

**Windows and macOS users: you don't need to build anything.** Each GitHub release carries
a ready-made **Windows `.zip`** (unzip, double-click `Sommet.exe`) and **macOS `.dmg`**
(open, drag to Applications) built automatically in the cloud — see the
[latest release](https://github.com/skinnie/sommet/releases/latest). They are unsigned, so
the first launch shows a one-time "unverified developer" prompt: on macOS right-click the
app → Open → Open; on Windows click "More info" → "Run anyway".

> **Note:** the watch engine (the Python helper) is bundled inside these downloads and the
> app starts it automatically, so no separate setup is needed. The Windows `.zip` was checked
> with a real Ambit2; the macOS `.dmg` is **not yet confirmed against a real watch**. If a
> download misbehaves, building from source with `run-desktop.sh` (below) is the proven path.

## macOS: the "Input Monitoring" permission

On recent macOS, the first time you sync a watch the system may ask you to **allow the app to
receive keystrokes**, and until you do, some features may be missing or *very* slow.

**Why macOS needs it:** the Ambit connects as a **USB HID device** — the same class of device a
keyboard belongs to. macOS requires the **Input Monitoring** permission to read data from *any*
HID device and can't tell watch reads apart from keyboard reads, so it asks for the same
permission for both. There's no way around it — it's how macOS gates all HID access. To grant
it: **System Settings → Privacy & Security → Input Monitoring** → enable the app (and its
backend/`python3` if listed), then relaunch.

**Why it's slow:** macOS hands over the watch's USB data one small 64-byte chunk at a time,
roughly every 100 ms, versus about 1 ms on Linux. That pacing is built into Apple's USB stack,
so large reads (e.g. flashing) are inherently slower on Mac — it's a platform limitation, not a
bug in the app. Without the permission granted, the reads are throttled further, which is the
"very slow / unavailable" behaviour some users see.

## Firmware flashing — urgency only

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
