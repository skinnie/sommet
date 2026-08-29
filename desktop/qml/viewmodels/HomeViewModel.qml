pragma Singleton
import QtQuick
import AmbitApp

// AMBITAPP_SPEC.md's QML -> ViewModels -> Services layering: HomePage.qml binds to this,
// never to DeviceService directly. Right now that's a thin pass-through plus one piece of
// real presentation logic (the status text/color) - as Weather/Activities services land in
// later steps, Home's view of them gets added here too, not scattered across HomePage.qml.
QtObject {
    // Real, 2026-08-08 ("home page: instead of ambit it detects an etrex... like on the
    // android version"). Ambit and Garmin are two completely separate device mechanisms
    // (NSP flash protocol vs. plain USB-mass-storage GPX files - see GarminService's own
    // header comment), so Home shows whichever one is actually present rather than trying
    // to merge them into one card. Ambit takes priority if, implausibly, both are connected
    // at once - it's this app's original/primary device, and the two would never really be
    // plugged in together in practice.
    readonly property bool isGarmin: !connected && GarminService.connected
    readonly property bool isAmbit: connected && DeviceService.model !== "Hoopoe"

    // Real, 2026-08-08 ("Yes I want to implement it both to desktop and android version").
    // Kailash ("Hoopoe") answers the same 0x0000 identity command every Ambit/Traverse watch
    // does (DeviceService needed no changes for /api/device to already work for it - see
    // write_nav.py's PRODUCT_IDS fix), so `connected` is already true for it; this just picks
    // out which real device is actually plugged in, the same way isGarmin/isAmbit already do.
    readonly property bool isKailash: connected && DeviceService.model === "Hoopoe"
    // Traverse (Jabiru) / Traverse Alpha (Loon). Used to hide features these watches don't
    // have - e.g. planned moves: their 0x0b21 memory map declares NO TrainingProgram region
    // (confirmed in the real traverse pcaps), so Intervals must not be offered for them, same
    // as it already isn't for the Kailash. André, 2026-08-18.
    readonly property bool isTraverse: connected && (DeviceService.model === "Jabiru" || DeviceService.model === "Loon")

    // 2026-08-07: switched from DeviceService.navOk to deviceInfoOk - navOk came from a
    // slow, unnecessary full flash read (see DeviceService's own header comment); a
    // deviceInfoOk. checking one small identity command is both faster and just as real a
    // connectivity signal, matching what made the real Android app feel fast.
    readonly property bool connected: DeviceService.backendReachable && DeviceService.deviceInfoOk

    // Is ANY device present - a watch over USB, or a mounted Garmin. The app stays fully
    // usable when this is false (André, 2026-08-11): cached activities, Totals, saved routes
    // and POIs, and every app setting work with nothing plugged in, because none of them need
    // the device to answer. Pages that genuinely cannot function without one are HIDDEN
    // rather than shown empty - his call: an empty page invites the user to wonder what they
    // did wrong.
    //
    // Deliberately not tied to Testing mode, which is a debugging tool and simulates a
    // CONNECTED device; offline is the real state of a real user with the watch in a drawer.
    readonly property bool anyDevice: connected || GarminService.connected

    readonly property string connectionStatusText: {
        if (DeviceService.loading) return qsTr("Checking...");
        if (!DeviceService.backendReachable) return qsTr("Backend not running");
        if (!DeviceService.deviceInfoOk) return qsTr("Watch not connected");
        return qsTr("Connected");
    }

    readonly property color connectionStatusColor:
        connected ? Theme.success : (DeviceService.loading ? Theme.mutedText : Theme.error)

    // DeviceService.model is the real internal engineering codename (e.g. "Emu") - the
    // 0x0000 reply itself has no commercial name to give back. Same confirmed codename
    // table history.md documents (and tools/workout_gui.py's own VARIANT_NAMES already
    // uses for the same reason) - not guessed here a second time.
    // 2026-08-08: spacing between "Ambit" and its generation number added on request
    // ("Ambit3 Peak" -> "Ambit 3 Peak") - applied to every Ambit2/Ambit3 entry, not just
    // Emu, for consistency. "Ambit" (Bluebird, the original) has no number, so it's
    // unaffected.
    // Real, 2026-08-09 ("check if the namings already have the correct spacing as we
    // decided elsewhere... not 'Ambit3 Peak' but 'Suunto Ambit 3 Peak'") - this table was
    // missing the "Suunto" brand prefix the Android app's own PID->name table
    // (AmbitUsbModule.kt's SUUNTO_PID_NAMES) already uses for every single entry. Aligned to
    // match it exactly rather than re-deciding the convention here a second time.
    readonly property var _modelNames: ({
        Bluebird: "Suunto Ambit", Duck: "Suunto Ambit 2", Colibri: "Suunto Ambit 2 S",
        Greentit: "Suunto Ambit 2 R", Emu: "Suunto Ambit 3 Peak", Finch: "Suunto Ambit 3 Sport",
        Ibisbill: "Suunto Ambit 3 Run", Kaka: "Suunto Ambit 3 Vertical",
        Jabiru: "Suunto Traverse", Loon: "Suunto Traverse Alpha", Hoopoe: "Suunto Kailash",
    })
    // The friendly name for ANY model codename, not just the connected one - the backup list
    // needs to name the watch each saved backup came from (André, 2026-08-27: "be sure that
    // they are not from other device"), which may well not be the one plugged in now. Reuses
    // the same _modelNames table deviceDisplayName reads, so a new model is still added once.
    function displayNameForModel(model) {
        if (!model) return "";
        return _modelNames[model] || model;
    }

    readonly property string deviceDisplayName:
        DeviceService.deviceInfoOk
            ? (_modelNames[DeviceService.model] || DeviceService.model)
            : qsTr("Suunto Ambit 3 Peak")  // static fallback - this project's one reference watch

    // Real, 2026-08-11 (André: "correlation between the devices we support and their manual
    // link"). One official Suunto user-guide PDF per codename, from `manualslinks` at the repo
    // root (Suunto's own ns.suunto.com Userguides paths, one per model page) - keyed the same
    // way _modelNames already is so a new model only ever needs adding once. Falls back to the
    // Ambit3 Peak guide alongside deviceDisplayName's own fallback above.
    readonly property var _manualUrls: ({
        Bluebird: "https://ns.suunto.com/Manuals/Ambit/Userguides/Suunto_Ambit_UserGuide_EN.pdf",
        Duck: "https://ns.suunto.com/Manuals/Ambit2/Userguides/Suunto_Ambit2_UserGuide_EN.pdf",
        Colibri: "https://ns.suunto.com/Manuals/Ambit2_S/Userguides/Suunto_Ambit2_S_UserGuide_EN.pdf",
        Greentit: "https://ns.suunto.com/Manuals/Ambit2_R/Userguides/Suunto_Ambit2_R_UserGuide_EN.pdf",
        Emu: "https://ns.suunto.com/Manuals/Ambit3_Peak/Userguides/Suunto_Ambit3_Peak_UserGuide_EN.pdf",
        Finch: "https://ns.suunto.com/Manuals/Ambit3_Sport/Userguides/Suunto_Ambit3_Sport_UserGuide_EN.pdf",
        Ibisbill: "https://ns.suunto.com/Manuals/Ambit3_Run/Userguides/Suunto_Ambit3_Run_UserGuide_EN.pdf",
        Kaka: "https://ns.suunto.com/Manuals/Ambit3_Vertical/Userguides/Suunto_Ambit3_Vertical_UserGuide_EN.pdf",
        Jabiru: "https://ns.suunto.com/Manuals/Traverse/Userguides/Suunto_Traverse_UserGuide_EN.pdf",
        Loon: "https://ns.suunto.com/Manuals/Traverse_Alpha/Userguides/Suunto_TraverseAlpha_UserGuide_EN.pdf",
        Hoopoe: "https://ns.suunto.com/Manuals/Kailash/Userguides/Suunto_Kailash_UserGuide_EN.pdf",
    })
    readonly property string manualUrl:
        DeviceService.deviceInfoOk
            ? (_manualUrls[DeviceService.model] || _manualUrls["Emu"])
            : _manualUrls["Emu"]

    // Real, 2026-08-11 (André: "I added etrex manuals to the files, can you link it to the
    // supported devices?"). Garmin has no codename table to key off (GarminService.model is
    // free text straight from the watch's own GarminDevice.xml <Model><Description> - e.g.
    // "eTrex 30", "eTrex 32x", see garminservice.cpp's parseGarminDeviceXml), and `manualslinks`
    // only has two real eTrex guide PDFs covering two whole sub-families each (Garmin's own
    // manual page groups 10/20/20x/30/30x under one guide and 22x/32x under the other) - not
    // one-per-model like the Suunto table above, so this matches by family instead of an exact
    // key. "22x"/"32x" is the one substring that tells the two families apart; everything else
    // in the eTrex 10/20/30 generation falls to the first guide.
    readonly property string garminManualUrl:
        /32x|22x/i.test(GarminService.model)
            ? "https://www8.garmin.com/manuals/webhelp/eTrex22x-32x/EN-US/eTrex_22x_32x_OM_EN-US.pdf"
            : "https://www8.garmin.com/manuals/webhelp/eTrex_10_20x_30x/EN-US/eTrex_10_20_20x_30_30x_OM_EN-US.pdf"

    readonly property string batteryText:
        DeviceService.deviceInfoOk && DeviceService.batteryPercent >= 0
            ? qsTr("%1%").arg(DeviceService.batteryPercent)
            : qsTr("Not available yet")

    readonly property string firmwareText:
        DeviceService.deviceInfoOk && DeviceService.firmwareVersion.length > 0
            ? DeviceService.firmwareVersion
            : qsTr("Not available yet")

    // Added 2026-08-07 alongside firmware downloads (V3_CHANGELOG.md) - André asked for
    // these on Home specifically.
    readonly property string serialText:
        DeviceService.deviceInfoOk && DeviceService.serial.length > 0
            ? DeviceService.serial
            : qsTr("Not available yet")

    readonly property string hardwareText:
        DeviceService.deviceInfoOk && DeviceService.hardwareVersion.length > 0
            ? DeviceService.hardwareVersion
            : qsTr("Not available yet")
}
