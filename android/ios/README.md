# iOS app icon (staged)

This repo's mobile app is React Native (root: `android/`), and **no iOS Xcode project has
been generated yet** (the app currently ships Android-only; iOS is future scope — Ambit3
over BLE, per the project notes). There is therefore no `Images.xcassets` to place the icon
into yet.

`AppIcon.appiconset/` here is the finished Sommet **"Summit sync"** app icon, rendered as a
complete, standard iOS icon set (opaque, no alpha, no rounded corners — iOS masks its own
superellipse). When the iOS target is scaffolded, copy this folder into
`ios/<App>/Images.xcassets/` (replacing the template `AppIcon.appiconset`).

Regenerate with:

    ./tools/packaging/make_ios_appicon.py

The artwork/palette is the shared source of truth in `tools/packaging/sommet_icon.py`, the
same one the desktop and Android icons render from — so all platforms stay in step.
