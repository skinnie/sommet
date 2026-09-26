pragma Singleton
import QtQuick

// Bike computers available right now, shared across pages (2026-09-25). USB ones (Bryton/Edge/
// Karoo) are cheap to re-probe from any page (/api/mtp/devices), but the Magene C406 is Bluetooth:
// it's only known once Home's "Pair over Bluetooth -> Magene" scan found it, and a fresh BLE scan
// per page would take seconds. Home records it here; the Training Program reads it to offer
// "Create for / Send to Magene".
QtObject {
    id: bikes
    // The found C406: {kind: "c406", name, address, activityCount, files}, or null.
    property var magene: null

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
        if (!brytonBle || _brytonScanning || mageneConnecting || _scanning)
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
        if (!magene || _scanning || _brytonScanning || mageneConnecting)
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
        running: bikes.magene !== null
        repeat: true
        onTriggered: if (!bikes.mageneReachable || Date.now() - bikes.mageneSeenAt > 300000) bikes.scanMagene()
    }
    // Offset by 30 s from the Magene's so the two scans never share the radio.
    property Timer _brytonScanTimer: Timer {
        interval: 60000
        running: bikes.brytonBle !== null
        repeat: true
        onTriggered: bikes._brytonDelay.restart()
    }
    property Timer _brytonDelay: Timer {
        interval: 30000
        onTriggered: if (!bikes.brytonBleReachable || Date.now() - bikes.brytonBleSeenAt > 300000) bikes.scanBrytonBle()
    }
}
