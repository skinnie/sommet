import QtQuick
import QtQuick.Controls
import AmbitApp

// Planned workouts on the Wahoo ELEMNT (tools/wahoo_workout.py, /api/wahoo/workouts) - the twin of
// WahooRoutesPanel. Workouts are sent from Training Program (right-click a day → Send to Wahoo
// ELEMNT); this card lists the ELEMNT's workouts and deletes the ones Sommet put there. Workouts
// from a training site (TrainingPeaks etc.) are listed but left alone; the ELEMNT's built-in ones
// are hidden. Deleting needs the cable.
Column {
    id: root
    spacing: Theme.spacingSmall

    property bool loading: true
    property bool busy: false
    property string error: ""
    property string msg: ""
    property string via: ""
    property var workouts: []
    property string confirmDelete: ""

    function api(method, path, body, done) {
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
        root.loading = true; root.error = ""
        root.api("GET", "/api/wahoo/workouts", null, function (r) {
            root.loading = false
            if (!r.ok) { root.error = r.error || qsTr("Could not read the ELEMNT's workouts."); return }
            root.via = r.via || ""
            // provider 0 = the ELEMNT's own built-in workouts.
            root.workouts = (r.workouts || []).filter(w => w.provider === undefined || w.provider !== 0)
        })
    }
    function remove(name) {
        root.confirmDelete = ""; root.busy = true; root.msg = ""
        root.api("POST", "/api/wahoo/workouts/delete", { name: name }, function (r) {
            root.busy = false
            if (r.ok) { root.msg = qsTr("Deleted “%1” ✓").arg(name); root.load() }
            else root.msg = r.error || qsTr("Couldn't delete the workout.")
        })
    }
    Component.onCompleted: root.load()

    Text {
        text: qsTr("Workouts on the ELEMNT")
        color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
    }
    Text {
        width: parent.width; wrapMode: Text.WordWrap
        text: qsTr("Send workouts from Training Program (right-click a day → Send to Wahoo ELEMNT). Over Bluetooth, press SYNC on the ELEMNT's Workouts screen to import them (or they import when it next starts).")
        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
    }
    Text {
        visible: root.loading
        text: qsTr("Reading the ELEMNT…")
        color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
    }
    Text {
        visible: root.error.length > 0
        width: parent.width; wrapMode: Text.WordWrap
        text: root.error; color: Theme.error; font.pixelSize: Theme.fontSizeBody
    }
    Text {
        visible: !root.loading && root.error.length === 0 && root.workouts.length === 0
        text: qsTr("No workouts from Sommet or a training site on the ELEMNT.")
        color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
    }
    Repeater {
        model: root.workouts
        delegate: Item {
            id: row
            required property var modelData
            width: root.width
            height: 36
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: row.modelData.name + (row.modelData.pendingSync ? "  ·  " + qsTr("waiting for SYNC on the ELEMNT")
                      : !row.modelData.fromSommet ? "  ·  " + qsTr("from your training site") : "")
                color: Theme.text; font.pixelSize: Theme.fontSizeBody
                elide: Text.ElideRight
                width: parent.width - actions.width - Theme.spacingSmall
            }
            Row {
                id: actions
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: Theme.spacingSmall
                RoundedButton {
                    visible: root.confirmDelete !== row.modelData.name && row.modelData.fromSommet === true
                    text: qsTr("Delete")
                    enabled: !root.busy && root.via === "usb"
                    onClicked: root.confirmDelete = row.modelData.name
                }
                Text {
                    visible: root.confirmDelete === row.modelData.name
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("Delete it?"); color: Theme.error; font.pixelSize: Theme.fontSizeCaption
                }
                RoundedButton {
                    visible: root.confirmDelete === row.modelData.name
                    text: qsTr("Yes"); onClicked: root.remove(row.modelData.name)
                }
                RoundedButton {
                    visible: root.confirmDelete === row.modelData.name
                    text: qsTr("Cancel"); onClicked: root.confirmDelete = ""
                }
            }
        }
    }
    Text {
        visible: !root.loading && root.via === "ble" && root.workouts.length > 0
        width: parent.width; wrapMode: Text.WordWrap
        text: qsTr("Deleting a workout needs the USB cable.")
        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
    }
    Text {
        visible: root.msg.length > 0
        width: parent.width; wrapMode: Text.WordWrap
        text: root.msg
        color: root.msg.indexOf("✓") >= 0 ? Theme.success : Theme.error
        font.pixelSize: Theme.fontSizeCaption
    }
    RoundedButton {
        text: qsTr("Re-read"); enabled: !root.busy && !root.loading
        onClicked: root.load()
    }
}
