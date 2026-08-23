pragma Singleton
import QtQuick
import AmbitApp

// AMBITAPP_SPEC.md, "Device Capabilities": "Never hardcode watch models. Instead expose
// capabilities... The UI should automatically adapt." A page/button just binds to
// `DeviceCapabilities.supportsRoutes` etc., never to `model === "Ambit3"` anywhere.
//
// WIRED TO THE REAL CONNECTED DEVICE, 2026-08-22 - this had been a static placeholder
// since it was first written (its own comment said so), and nothing in the app ever
// actually read it: real, live proof was a French Ambit1 connected while Watch
// Settings/Sport Modes/Routes were all open - each page called its Ambit3-only SBEM
// endpoint regardless, got a real "ok: false" back (the honest, correct answer - Ambit1
// predates SBEM, see [[ambit_app_hardware_fleet_check]] project history), and the shared
// ErrorBanner showed "An error has occurred. Please send the logs." for what is really
// just "this feature doesn't apply to this watch." That is the actual reason this keeps
// happening on every new device family: this file existed for exactly this, and was never
// connected to anything.
//
// Bluebird/Duck/Colibri/Greentit (Ambit1/2) speak the older, pre-SBEM PMEM 2.0 protocol -
// confirmed live (2026-08-22) that the SBEM object queries this app's Routes/POIs/Sport
// Modes/Apps pages depend on all come back empty on real Ambit1 hardware, not an error.
// Device identity/battery/settings-write (the one field-level SBEM path that predates the
// object model, see [[ambit_app_ambit12_settings_write]]) still work. No BLE on this
// family either ([[ambit_app_device_lineup]] - Ambit1/2 are USB-cable-only).
QtObject {
    readonly property var _legacyModels: (["Bluebird", "Duck", "Colibri", "Greentit"])
    readonly property bool _isLegacy: _legacyModels.indexOf(DeviceService.model) !== -1

    property bool supportsRoutes: !_isLegacy
    property bool supportsPOIs: !_isLegacy
    // The Watch Settings page's SettingsWriteService calls /api/settings, the Ambit3 SBEM
    // path - real device-level settings ARE readable/writable on Ambit1/2 too, but only
    // through the separate /api/legacy/settings this page doesn't call yet (see
    // [[ambit_app_ambit12_settings_write]]) - that's a real follow-up, not done here.
    property bool supportsWatchSettings: !_isLegacy
    // Display-slot assignment within a sport mode is proven on real Ambit3 hardware
    // (custom_modes_andre.md); full sport-mode *settings* writing (autolap thresholds,
    // sensor pods, intervals, etc.) is not - see unresolved_questions_for_devs.md #1.
    property bool supportsSportModes: !_isLegacy
    property bool supportsApps: !_isLegacy
    property bool supportsNavigation: !_isLegacy
    property bool supportsBluetooth: !_isLegacy
    // Firmware update: real and hardware-proven for Ambit3 (Peak) and, as of 2026-08-22,
    // Ambit1 too (see [[ambit_app_hardware_fleet_check]]) - but exposing a live Firmware
    // page to end users is a real product decision beyond fixing this file's own wiring,
    // not made here. Stays false; revisit deliberately, don't flip as a side effect.
    property bool supportsFirmware: false
}
