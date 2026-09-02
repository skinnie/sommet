# AmbitApp — iOS/iPadOS port status & build guide

iOS target for Ambit3 / Traverse / Kailash (the **BLE** watches). Ambit1/2 are
USB-only and unreachable from iOS — they stay desktop/Android-only.

Started 2026-08-28. See memory `sommet-ios-ble-central-model` /
`sommet-ios-port-react-native` and the Suunto/7R iOS PacketLogger captures
(`~/10.08.2026 …pklg`) that proved the central-role approach.

## What is DONE and verified

- **Shared C core builds for iOS.** All 21 libambit TUs (protocol, pmem20, sbem,
  Ambit3 drivers, navigation, sport modes, and the refactored `protocol_ble.c`)
  **compile and link for `arm64` against the iOS 26 SDK** — verified by producing
  a Mach-O arm64 executable. Only external dependency: system `-liconv`.
- **`protocol_ble.c` made transport-agnostic** via `#ifdef __ANDROID__` guards.
  The Android branch is byte-identical to before (safe for the existing build);
  the `#else` branch sends outgoing chunks through a plain C callback
  (`ambit_ble_write_fn`) instead of JNI.
- **New iOS-only C sources** (in `.../cpp/libambit/`):
  - `libambit_ios.c` — iOS transport constructor (`libambit_new_from_ble_ios`),
    the notify router, and the pre-init RX stash (twin of the Android jni_bridge
    machinery, in portable C).
  - `hid_stub.c` — satisfies the USB `hid_*` symbols libambit.c/protocol.c
    reference (never called on the BLE path).
  - `endian_compat.h` — Darwin shim for `le16toh`/`htole32`/… (force-included).
- **Swift BLE module** (`ios/AmbitBle/`): `AmbitBleModule.swift` (+ `.m` bridge,
  `AmbitBridge.h`) — a `CBCentralManager` central mirroring the Kotlin module's
  JS surface (`scanAndConnect` / `scanAndConnectTo` / `listBondedWatches` /
  `disconnectBle` + `AmbitBleDisconnected`). Registers as
  `NativeModules.AmbitBleModule`.
- **TypeScript: no changes needed.** `src/native/AmbitBleModule.ts` and
  `src/services/devices/AmbitBleDeviceProvider.ts` are already transport-agnostic
  — they just need the native module present on iOS.

## What REMAINS (needs the iOS toolchain: node + CocoaPods + Xcode)

1. **Scaffold the iOS RN project.** This Mac has Xcode 26.3 but **no node/CocoaPods**.
   With them installed, generate the `ios/` app project for RN 0.84 (e.g. a
   fresh `npx @react-native-community/cli init` at matching version, then copy in
   `ios/AmbitApp.xcodeproj` + `Podfile`), or add an iOS target to the RN app.
2. **Compile the C core into the app.** Add these to the app target (or a small
   pod/static-lib), with build settings:
   - Sources: everything in `cpp/libambit/*.c` **except** `libambit_android.c`,
     `firmware_flash_android.c`, `hidapi/hid-android.c`; **plus** `libambit_ios.c`,
     `hid_stub.c`, and `shared/libambit/device_driver_ambit3_{navigation,sport_modes}.c`.
   - `OTHER_CFLAGS`: `-include $(SRCROOT)/…/libambit/endian_compat.h`
     `-DDEBUG_PRINT_INFO -DDEBUG_PRINT_WARNING -DDEBUG_PRINT_ERROR`
   - Header search paths: `cpp/libambit`, `cpp/libambit/hidapi`, `shared/libambit`.
   - `OTHER_LDFLAGS`: `-liconv`.
   - C standard: c11.
3. **Bridging header** exposing `AmbitBridge.h` to Swift (set
   `SWIFT_OBJC_BRIDGING_HEADER`), and enable a module for the C entry points.
4. **The data-method bridge (`AmbitCore`) — the main remaining code.** The Android
   `jni_bridge.cpp` (~1400 lines) exposes the g_device operations the sync flow
   uses (`getDeviceInfo`, `getLogs`, `writeRoute`, `addPoi`, `readRegion`,
   settings, sport modes, …) under `NativeModules.AmbitUsbModule` /
   `AmbitCatalog` / `AmbitSmartSensor`. iOS needs an Objective-C++ twin that
   registers the same module names and calls the **same C functions** —
   translation is 1:1, dropping only the JNI marshalling (JNI arrays ↔
   NSArray/NSData, `env->NewStringUTF` ↔ NSString). The base64/GPX/JSON helpers
   in jni_bridge.cpp are portable C++ and copy over unchanged. This module holds
   the shared `g_device` for iOS; the BLE module hands it the connected object.
   (Firmware-flash and USB-only paths can be stubbed on iOS.)
5. **Info.plist**: `NSBluetoothAlwaysUsageDescription` (and
   `NSBluetoothPeripheralUsageDescription` for older iOS).

## Concurrency contract (already honored in the Swift module)

`libambit_new_from_ble_ios()` BLOCKS waiting for the watch's handshake frames,
which are delivered on the CBCentralManager delegate queue. The module runs the
handshake on `DispatchQueue.global()` while notifications arrive on a dedicated
`cbQueue` — two different threads, mirroring Android's binder-vs-executor split.
Do not point CBCentralManager at the main queue or run the handshake on `cbQueue`.
