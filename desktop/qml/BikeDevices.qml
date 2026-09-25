pragma Singleton
import QtQuick

// Bike computers available right now, shared across pages (2026-09-25). USB ones (Bryton/Edge/
// Karoo) are cheap to re-probe from any page (/api/mtp/devices), but the Magene C406 is Bluetooth:
// it's only known once Home's "Pair over Bluetooth -> Magene" scan found it, and a fresh BLE scan
// per page would take seconds. Home records it here; the Training Program reads it to offer
// "Create for / Send to Magene".
QtObject {
    // The found C406: {kind: "c406", name, address, activityCount, files}, or null.
    property var magene: null
}
