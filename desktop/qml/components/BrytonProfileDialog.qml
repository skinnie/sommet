import QtQuick
import QtQuick.Controls
import SommetUi

// Bryton Aero 60 <-> intervals.icu profile reconciliation (André, 2026-09-24).
//
// The Aero 60 keeps FTP/LTHR/Max HR/weight/... in System/Profile.bin (the backend reads/writes it
// via tools/bryton_profile.py, hardware-proven). intervals.icu is André's source of truth for
// those numbers. On open we ask the backend to /compare the two and list every field that
// differs; Apply writes intervals.icu's values to the device. One way only (André, 2026-10-27:
// "bryton or any other device should not write to intervals"), so there is no side to pick.
//
// Shared with the Magene C406 (2026-09-25): its backend endpoints return the same {device,
// intervals, diff} shape, so a Magene instance only sets apiBase/deviceName/address.
ThemedDialog {
    id: root

    property string apiBase: "bryton"            // /api/<apiBase>/profile/{compare,apply}
    property string deviceName: qsTr("Bryton")
    property string address: ""                  // BLE address for the Magene; unused by Bryton

    title: qsTr("Sync %1 profile").arg(root.deviceName)
    standardButtons: Dialog.NoButton
    width: 460

    property bool loading: true
    property string error: ""
    property var device: null
    property var intervals: null

    function endpoint(what) { return "http://127.0.0.1:8766/api/" + root.apiBase + "/profile/" + what }
    function body(extra) {
        var b = { athlete_id: ConnectionsService.intervalsIcuAthleteId,
                  api_key: ConnectionsService.intervalsIcuApiKey() }
        if (root.address.length > 0) b.address = root.address
        for (var k in extra) b[k] = extra[k]
        return JSON.stringify(b)
    }

    readonly property var labels: ({
        "ftp": qsTr("FTP (W)"), "lthr": qsTr("LTHR (bpm)"), "max_hr": qsTr("Max HR (bpm)"),
        "weight": qsTr("Weight (kg)"), "height": qsTr("Height (cm)"), "gender": qsTr("Gender"),
        "age": qsTr("Age"), "map": qsTr("MAP (W)")
    })
    ListModel { id: diffModel }

    function fmtGender(v) { return v === 1 ? qsTr("Male") : qsTr("Female") }
    function fmt(field, v) { return field === "gender" ? fmtGender(v) : v }

    onOpened: {
        root.loading = true; root.error = ""; diffModel.clear()
        var xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            root.loading = false
            var r = {}
            try { r = JSON.parse(xhr.responseText) } catch (e) { r = { ok: false } }
            if (!r.ok) { root.error = r.error || qsTr("Could not read the %1 profile.").arg(root.deviceName); return }
            root.device = r.device; root.intervals = r.intervals
            if (!r.intervals) { root.error = qsTr("Connect intervals.icu in Settings to compare."); return }
            for (var i = 0; i < (r.diff || []).length; i++) {
                var d = r.diff[i]
                diffModel.append({ field: d.field, deviceVal: d.device, intervalsVal: d.intervals })
            }
        }
        xhr.open("POST", root.endpoint("compare"))
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(root.body({}))
    }

    property bool applying: false
    property string applyMsg: ""

    function apply() {
        var toDevice = {}
        for (var i = 0; i < diffModel.count; i++) {
            var row = diffModel.get(i)
            toDevice[row.field] = row.intervalsVal
        }
        if (diffModel.count === 0) { root.close(); return }
        root.applying = true; root.applyMsg = ""
        postApply("to_device", toDevice, function () {
            root.applying = false
            if (root.applyMsg.length === 0) root.close()
        })
    }

    function postApply(direction, fields, cb) {
        var xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            var r = {}; try { r = JSON.parse(xhr.responseText) } catch (e) {}
            if (!r.ok) root.applyMsg = r.error || qsTr("Write failed.")
            cb()
        }
        xhr.open("POST", root.endpoint("apply"))
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(root.body({ direction: direction, fields: fields }))
    }

    contentItem: Column {
        spacing: Theme.spacingMedium

        Text {
            visible: root.loading
            text: qsTr("Reading the device…")
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
        }
        Text {
            visible: root.error.length > 0
            width: parent.width; wrapMode: Text.WordWrap
            text: root.error; color: Theme.error; font.pixelSize: Theme.fontSizeBody
        }
        Text {
            visible: !root.loading && root.error.length === 0 && diffModel.count === 0
            text: qsTr("Device and intervals.icu already match. ✓")
            color: Theme.success; font.pixelSize: Theme.fontSizeBody
        }

        Text {
            visible: diffModel.count > 0
            width: parent.width; wrapMode: Text.WordWrap
            text: qsTr("These differ. Apply writes the intervals.icu values to the %1.")
                  .arg(root.deviceName)
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
        }

        Repeater {
            model: diffModel
            delegate: Row {
                width: 420
                spacing: Theme.spacingMedium
                Text {
                    width: 120
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.labels[field] || field
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
                }
                Text {
                    width: 280
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("%1 → %2").arg(root.fmt(field, deviceVal)).arg(root.fmt(field, intervalsVal))
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody
                    Accessible.name: qsTr("%1: %2 now, %3 from intervals.icu")
                        .arg(root.labels[field] || field).arg(root.fmt(field, deviceVal))
                        .arg(root.fmt(field, intervalsVal))
                }
            }
        }

        Text {
            visible: diffModel.count > 0 && root.intervals !== null && !!root.intervals.map_source
                     && root.intervals.map !== undefined
            width: parent.width; wrapMode: Text.WordWrap
            text: root.intervals ? qsTr("MAP from intervals.icu: %1").arg(root.intervals.map_source) : ""
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
        }

        StatusNote {
            width: parent.width
            tone: "error"
            text: root.applyMsg
        }

        Row {
            spacing: Theme.spacingSmall
            layoutDirection: Qt.RightToLeft
            width: parent.width
            RoundedButton {
                text: diffModel.count > 0 ? qsTr("Apply") : qsTr("Close")
                busy: root.applying
                enabled: !root.loading && !root.applying
                onClicked: diffModel.count > 0 ? root.apply() : root.close()
            }
            RoundedButton {
                visible: diffModel.count > 0
                text: qsTr("Cancel"); enabled: !root.applying
                onClicked: root.close()
            }
        }
    }
}
