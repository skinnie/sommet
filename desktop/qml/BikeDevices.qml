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
    function isReachable(bike) { return !bike || bike.kind !== "c406" || mageneReachable }

    function scanMagene() {
        if (!magene || _scanning || mageneConnecting)
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
}
