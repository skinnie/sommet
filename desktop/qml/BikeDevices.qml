pragma Singleton
import QtQuick
import QtCore
import SommetUi

// Bike computers available right now, shared across pages (2026-09-25). USB ones (Bryton/Edge/
// Karoo) are cheap to re-probe from any page (/api/mtp/devices), but the Magene C406 is Bluetooth:
// it's only known once Home's "Pair over Bluetooth -> Magene" scan found it, and a fresh BLE scan
// per page would take seconds. Home records it here; the Training Program reads it to offer
// "Create for / Send to Magene".
QtObject {
    id: bikes
    // The found C406: {kind: "c406", name, address, activityCount, files}, or null.
    property var magene: null
    // The active Edge is a USB-drive one (Edge 500 & co., 2026-11-05), not an MTP Edge 1040: it has
    // a GPS settings page (profile over Garmin/NewFiles) and no Connect IQ. Home sets this.
    property bool edgeMass: false

    // --- Devices on Home (André, 2026-10-22): one switch per Bluetooth bike computer, in
    // Settings > Show in the sidebar > Devices. Off: no card on Home, not in the pairing list, no
    // background scan, not offered by Routes or the Training Program - but still remembered, so
    // turning it back on brings it back as it was. A plugged-in device always shows. The first
    // time this runs, each switch starts on for a device already paired (or, for the Wahoo,
    // already seen on the cable) and off otherwise. ---
    property QtObject shownObj: Settings {
        id: shownId
        category: "devicesOnHome"
        property bool magene: false
        property bool brytonBle: false
        property bool wahoo: false
        property bool initialised: false
    }
    property alias mageneShown: shownId.magene
    property alias brytonBleShown: shownId.brytonBle
    property alias wahooShown: shownId.wahoo
    // What Home remembers (same QSettings keys as HomePage.qml), read once for the first run.
    property QtObject mageneMemoryObj: Settings { id: mageneMemoryId; category: "magene"; property string address: "" }
    property QtObject brytonMemoryObj: Settings { id: brytonMemoryId; category: "brytonble"; property string address: "" }
    property QtObject wahooMemoryObj: Settings { id: wahooMemoryId; category: "wahoo"; property bool seen: false }
    function _initShown() {
        if (shownId.initialised)
            return
        shownId.magene = mageneMemoryId.address.length > 0
        shownId.brytonBle = brytonMemoryId.address.length > 0
        shownId.wahoo = wahooMemoryId.seen
        shownId.initialised = true
    }
    Component.onCompleted: Qt.callLater(_initShown)
    // Switched off: drop it here at once (stops its scan; Routes and the Training Program stop
    // offering it), and hand the active device back if it was this one. Home rebuilds its list
    // from what it remembers when it next opens.
    onMageneShownChanged: if (!mageneShown) {
        magene = null
        mageneAnswered(false)
        if (DeviceService.activeBikeKind === "c406") DeviceService.selectBikeComputer("")
    }
    onBrytonBleShownChanged: if (!brytonBleShown) {
        brytonBle = null
        brytonBleAnswered(false)
        if (DeviceService.activeBikeKind === "brytonble") DeviceService.selectBikeComputer("")
    }

    // Is the remembered C406 actually on (André, 2026-09-26: "the c406 is on but appears as
    // disconnected")? Set by any real answer (Home's "hello", a ride list) AND by a background
    // advertisement scan (/api/magene/devices - listens only, never connects, so no drop/reconnect
    // on the C406): every minute while it isn't seen, every 5 minutes once it is. Routes, the
    // planner and Home all read this one flag.
    property bool mageneReachable: false
    property real mageneSeenAt: 0
    // True while a page holds a BLE connection to the C406 - the scan waits rather than compete.
    property bool mageneConnecting: false
    property bool _scanning: false
    function mageneAnswered(ok) { mageneSeenAt = ok ? Date.now() : 0; mageneReachable = ok }
    function isReachable(bike) {
        if (!bike) return true
        if (bike.kind === "c406") return mageneReachable
        if (bike.kind === "brytonble") return brytonBleReachable
        return true
    }

    // The Aero 60 over Bluetooth (André, 2026-09-26) - {kind: "brytonble", name, address} or
    // null. Same idea as the Magene: a listen-only scan says whether it's on. It only advertises
    // while it isn't connected to the phone app, so "not seen" can also mean "on the phone".
    property var brytonBle: null
    property bool brytonBleReachable: false
    property real brytonBleSeenAt: 0
    property bool _brytonScanning: false
    function brytonBleAnswered(ok) { brytonBleSeenAt = ok ? Date.now() : 0; brytonBleReachable = ok }
    function scanBrytonBle() {
        if (!brytonBle || !brytonBleShown || _brytonScanning || mageneConnecting || _scanning)
            return
        _brytonScanning = true
        const addr = brytonBle.address
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return
            bikes._brytonScanning = false
            var r = null
            try { r = JSON.parse(xhr.responseText) } catch (e) { r = null }
            if (!r || !r.ok)
                return
            const seen = (r.devices || []).some(d => (d.address || "").toUpperCase() === (addr || "").toUpperCase())
            if (seen) bikes.brytonBleAnswered(true)
            else if (Date.now() - bikes.brytonBleSeenAt > 60000) bikes.brytonBleAnswered(false)
        }
        xhr.open("GET", "http://127.0.0.1:8766/api/brytonble/devices")
        xhr.send()
    }
    onBrytonBleChanged: if (brytonBle && !brytonBleReachable) Qt.callLater(scanBrytonBle)

    function scanMagene() {
        if (!magene || !mageneShown || _scanning || _brytonScanning || mageneConnecting)
            return
        _scanning = true
        const addr = magene.address
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return
            bikes._scanning = false
            var r = null
            try { r = JSON.parse(xhr.responseText) } catch (e) { r = null }
            if (!r || !r.ok)                 // a failed scan says nothing about the C406 - keep the flag
                return
            const seen = (r.devices || []).some(d => (d.address || "").toUpperCase() === (addr || "").toUpperCase())
            // Seen advertising = on. Not seen: off - unless a connection answered in the last
            // minute (a connected C406 stops advertising).
            if (seen) bikes.mageneAnswered(true)
            else if (Date.now() - bikes.mageneSeenAt > 60000) bikes.mageneAnswered(false)
        }
        xhr.open("GET", "http://127.0.0.1:8766/api/magene/devices")
        xhr.send()
    }
    onMageneChanged: if (magene && !mageneReachable) Qt.callLater(scanMagene)

    property Timer _scanTimer: Timer {
        interval: 60000
        running: bikes.magene !== null && bikes.mageneShown
        repeat: true
        onTriggered: if (!bikes.mageneReachable || Date.now() - bikes.mageneSeenAt > 300000) bikes.scanMagene()
    }
    // Offset by 30 s from the Magene's so the two scans never share the radio.
    property Timer _brytonScanTimer: Timer {
        interval: 60000
        running: bikes.brytonBle !== null && bikes.brytonBleShown
        repeat: true
        onTriggered: bikes._brytonDelay.restart()
    }
    property Timer _brytonDelay: Timer {
        interval: 30000
        onTriggered: if (!bikes.brytonBleReachable || Date.now() - bikes.brytonBleSeenAt > 300000) bikes.scanBrytonBle()
    }
}
