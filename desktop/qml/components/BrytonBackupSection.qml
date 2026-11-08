import QtQuick
import QtQuick.Controls
import AmbitApp

// Bryton Aero 60 backup & restore, one half per connection (André, 2026-11-08: "for screens use
// cable, for sensors use bluetooth"):
//   mode "screens" - on the USB screens panel: saves System/Grid.ini; Restore copies it back.
//   mode "sensors" - on the Bluetooth panel: saves the paired-sensor list read over BLE. The Aero
//     60 has no "write this sensor id" command (its own app pairs by scanning with the sensor
//     awake), so a sensors backup is a record: each entry says what to re-pair, on which bike.
// Both list the same ~/AmbitAppBackups/bryton folder (tools/bryton_backup.py), filtered to the
// backups that hold their half.
Column {
    id: root
    spacing: Theme.spacingSmall

    property string mode: "screens"          // "screens" | "sensors"
    property string address: ""              // the Bryton's Bluetooth address (sensors mode)
    property bool busy: false
    property string msg: ""
    property var backups: []
    property string openPath: ""             // sensors mode: the backup whose re-pair steps are shown

    readonly property bool sensorsMode: mode === "sensors"
    readonly property var shown: backups.filter(b => root.sensorsMode ? b.sensors !== undefined
                                                                       : (b.screenCount || 0) > 0)

    Component.onCompleted: load()

    function apiTo(path, method, body, done) {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            let r = {}
            try { r = JSON.parse(xhr.responseText) } catch (e) { r = { ok: false } }
            done(r)
        }
        xhr.open(method, "http://127.0.0.1:8766" + path)
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(body ? JSON.stringify(body) : null)
    }

    function load() {
        root.apiTo("/api/bryton/backups", "GET", null, function (r) {
            root.backups = (r && r.ok && r.backups) ? r.backups : []
        })
    }

    readonly property var typeNames: ({ heartRate: qsTr("Heart Rate"), speed: qsTr("Speed"),
                                        cadence: qsTr("Cadence"), speedCadence: qsTr("Speed/CAD"),
                                        power: qsTr("Power") })
    function sensorLine(s) {
        return qsTr("Bike %1 · %2 · %3 %4").arg(s.bike).arg(typeNames[s.type] || s.type)
               .arg(s.hw === "ant" ? "ANT+" : "BLE").arg(s.id)
    }
    function sensorSummary(list) {
        if (!list || list.length === 0) return qsTr("no sensors paired")
        return list.map(s => (typeNames[s.type] || s.type) + " " + (s.hw === "ant" ? "ANT+" : "BLE") + " " + s.id)
                   .join(", ")
    }

    function backupNow() {
        root.busy = true; root.msg = ""
        const body = root.sensorsMode ? { only: "sensors", address: root.address } : { only: "screens" }
        root.apiTo("/api/bryton/backup", "POST", body, function (r) {
            root.busy = false
            if (!r.ok) { root.msg = r.error || qsTr("Backup failed."); return }
            root.msg = root.sensorsMode
                ? qsTr("Sensors saved ✓ — %1").arg(root.sensorSummary(r.manifest && r.manifest.sensors))
                : qsTr("Screens saved ✓ — %1 screens").arg(r.screenCount || 0)
            root.load()
        })
    }

    function restoreScreens(path) {
        root.busy = true; root.msg = ""
        root.apiTo("/api/bryton/restore", "POST", { path: path }, function (r) {
            root.busy = false
            root.msg = r.ok ? qsTr("Screens restored ✓ — unplug the Bryton to see them.")
                            : (r.error || qsTr("Restore failed."))
        })
    }

    Rectangle { width: parent.width; height: 1; color: Theme.border }

    Text {
        text: root.sensorsMode ? qsTr("Back up sensors") : qsTr("Back up screens")
        color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
    }
    Text {
        width: parent.width; wrapMode: Text.WordWrap
        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
        text: root.sensorsMode
              ? qsTr("Saves the list of sensors paired to each bike, read over Bluetooth. The Bryton pairs " +
                     "sensors by scanning, so this is your record to re-pair from after a reset. " +
                     "Data screens are backed up over the cable.")
              : qsTr("Saves your data screens over the cable, so you can put them back after a reset. " +
                     "Paired sensors are backed up over Bluetooth.")
    }

    RoundedButton {
        text: root.sensorsMode ? qsTr("Back up sensors now") : qsTr("Back up screens now")
        busy: root.busy
        enabled: !root.busy && (!root.sensorsMode || root.address.length > 0)
        onClicked: root.backupNow()
    }

    StatusNote {
        width: parent.width
        tone: root.msg.indexOf("✓") >= 0 ? "success" : "error"
        text: root.msg
    }

    Text {
        visible: root.shown.length === 0
        text: root.sensorsMode ? qsTr("No sensor backups yet.") : qsTr("No screen backups yet.")
        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
    }

    Repeater {
        model: root.shown
        delegate: Rectangle {
            id: row
            required property var modelData
            readonly property bool open: root.sensorsMode && root.openPath === modelData.path
            width: root.width
            height: rowCol.implicitHeight + Theme.spacingSmall * 2
            radius: Theme.radiusSmall
            color: Theme.cardNested
            border.width: 1; border.color: Theme.border

            Column {
                id: rowCol
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: Theme.spacingSmall
                spacing: Theme.spacingSmall

                Row {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Column {
                        width: parent.width - actionBtn.width - Theme.spacingSmall
                        anchors.verticalCenter: parent.verticalCenter
                        Text {
                            text: (row.modelData.created || "").replace("T", "  ").split("+")[0]
                            color: Theme.text; font.pixelSize: Theme.fontSizeCaption
                        }
                        Text {
                            width: parent.width; wrapMode: Text.WordWrap
                            text: root.sensorsMode ? root.sensorSummary(row.modelData.sensors)
                                                   : qsTr("%1 screens").arg(row.modelData.screenCount)
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeTiny
                        }
                    }
                    RoundedButton {
                        id: actionBtn
                        anchors.verticalCenter: parent.verticalCenter
                        visible: !root.sensorsMode || (row.modelData.sensors || []).length > 0
                        text: root.sensorsMode ? (row.open ? qsTr("Hide") : qsTr("How to put back"))
                                               : qsTr("Restore")
                        enabled: !root.busy
                        onClicked: root.sensorsMode ? (root.openPath = row.open ? "" : row.modelData.path)
                                                    : root.restoreScreens(row.modelData.path)
                    }
                }

                // Re-pair steps for a sensors backup (no automatic write exists on the Aero 60).
                Column {
                    visible: row.open
                    width: parent.width
                    spacing: 2
                    Text {
                        width: parent.width; wrapMode: Text.WordWrap
                        color: Theme.text; font.pixelSize: Theme.fontSizeCaption
                        text: qsTr("On the Bryton: Settings → Sensors, pick the bike, then for each one below " +
                                   "choose the type and Add with the sensor awake (strap on, cranks or wheel " +
                                   "turning). Check the ID it finds matches.")
                    }
                    Repeater {
                        model: row.modelData.sensors || []
                        delegate: Text {
                            required property var modelData
                            text: "• " + root.sensorLine(modelData)
                            color: Theme.text; font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                }
            }
        }
    }
}
