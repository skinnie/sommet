import QtQuick
import QtQuick.Controls
import AmbitApp

// Routes on the Wahoo ELEMNT (tools/wahoo_routes.py, /api/wahoo/routes). Routes are sent from the
// Routes page's "Send to…" menu like for every other device; this card lists what's on the ELEMNT
// and deletes. Over the cable the list is the ELEMNT's own route table; over Bluetooth it's the
// imported courses plus files still waiting for a SYNC on the device. Deleting needs the cable
// (it edits the ELEMNT's database).
Column {
    id: root
    spacing: Theme.spacingSmall

    property bool loading: true
    property bool busy: false
    property string error: ""
    property string msg: ""
    property string via: ""
    property var routes: []
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
        root.api("GET", "/api/wahoo/routes", null, function (r) {
            root.loading = false
            if (!r.ok) { root.error = r.error || qsTr("Could not read the ELEMNT's routes."); return }
            root.via = r.via || ""
            root.routes = r.routes || []
        })
    }
    function remove(name) {
        root.confirmDelete = ""; root.busy = true; root.msg = ""
        root.api("POST", "/api/wahoo/routes/delete", { name: name }, function (r) {
            root.busy = false
            if (r.ok) { root.msg = qsTr("Deleted “%1” ✓").arg(name); root.load() }
            else root.msg = r.error || qsTr("Couldn't delete the route.")
        })
    }
    Component.onCompleted: root.load()

    Text {
        text: qsTr("Routes on the ELEMNT")
        color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
    }
    Text {
        width: parent.width; wrapMode: Text.WordWrap
        text: qsTr("Send routes from the Routes page (Send to… → Wahoo ELEMNT). Over Bluetooth, press SYNC on the ELEMNT's Routes screen to import them (or they import when it next starts).")
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
        visible: !root.loading && root.error.length === 0 && root.routes.length === 0
        text: qsTr("No routes on the ELEMNT.")
        color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
    }
    Repeater {
        model: root.routes
        delegate: Item {
            id: row
            required property var modelData
            width: root.width
            height: 36
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: row.modelData.name + (row.modelData.distanceKm !== undefined
                      ? "  ·  " + row.modelData.distanceKm.toFixed(1) + " km  ·  " + row.modelData.ascentM + " m"
                      : row.modelData.pendingSync ? "  ·  " + qsTr("waiting for SYNC on the ELEMNT") : "")
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
                    visible: root.confirmDelete !== row.modelData.name
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
        visible: !root.loading && root.via === "ble" && root.routes.length > 0
        width: parent.width; wrapMode: Text.WordWrap
        text: qsTr("Deleting a route needs the USB cable.")
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
