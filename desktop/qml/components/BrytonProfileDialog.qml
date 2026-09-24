import QtQuick
import QtQuick.Controls
import QtCore
import AmbitApp

// Bryton Aero 60 <-> intervals.icu profile reconciliation (André, 2026-09-24).
//
// The Aero 60 keeps FTP/LTHR/Max HR/weight/... in System/Profile.bin (the backend reads/writes it
// via tools/bryton_profile.py, hardware-proven). intervals.icu is André's source of truth for
// those numbers. On open we ask the backend to /compare the two; for every field that differs we
// let him pick which side is right, remember that choice for next time, and write the winners to
// the device and/or back to intervals.icu. Gender/birthday/height reconcile device<->Sommet only
// (intervals.icu doesn't expose them for write); MAP is device-only and left untouched here.
ThemedDialog {
    id: root

    title: qsTr("Sync Bryton profile")
    standardButtons: Dialog.NoButton
    width: 460

    property bool loading: true
    property string error: ""
    property var device: null
    property var intervals: null

    // Persisted per-field "remember my choice" source: "intervals" or "device". Lives in the same
    // Sommet.conf as everything else (QSettings), so a remembered field auto-resolves next time.
    Settings {
        id: prefs
        category: "brytonProfileSync"
    }

    readonly property var labels: ({
        "ftp": qsTr("FTP (W)"), "lthr": qsTr("LTHR (bpm)"), "max_hr": qsTr("Max HR (bpm)"),
        "weight": qsTr("Weight (kg)"), "height": qsTr("Height (cm)"), "gender": qsTr("Gender")
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
            if (!r.ok) { root.error = r.error || qsTr("Could not read the Bryton profile."); return }
            root.device = r.device; root.intervals = r.intervals
            if (!r.intervals) { root.error = qsTr("Connect intervals.icu in Settings to compare."); return }
            for (var i = 0; i < (r.diff || []).length; i++) {
                var d = r.diff[i]
                var remembered = prefs.value("src_" + d.field, "")
                diffModel.append({
                    field: d.field, deviceVal: d.device, intervalsVal: d.intervals,
                    choice: remembered === "device" || remembered === "intervals" ? remembered : "intervals"
                })
            }
        }
        xhr.open("POST", "http://127.0.0.1:8766/api/bryton/profile/compare")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(JSON.stringify({ athlete_id: ConnectionsService.intervalsIcuAthleteId,
                                  api_key: ConnectionsService.intervalsIcuApiKey() }))
    }

    property bool remember: true
    property bool applying: false
    property string applyMsg: ""

    function apply() {
        var toDevice = {}, toIntervals = {}, n = 0
        for (var i = 0; i < diffModel.count; i++) {
            var row = diffModel.get(i)
            if (root.remember) prefs.setValue("src_" + row.field, row.choice)
            if (row.choice === "intervals") { toDevice[row.field] = row.intervalsVal; n++ }
            else { toIntervals[row.field] = row.deviceVal; n++ }
        }
        if (n === 0) { root.close(); return }
        root.applying = true; root.applyMsg = ""
        var pending = (Object.keys(toDevice).length > 0 ? 1 : 0)
                    + (Object.keys(toIntervals).length > 0 ? 1 : 0)
        function done() { if (--pending <= 0) { root.applying = false; root.close() } }
        if (Object.keys(toDevice).length > 0) postApply("to_device", toDevice, done)
        if (Object.keys(toIntervals).length > 0) postApply("to_intervals", toIntervals, done)
    }

    function postApply(direction, fields, cb) {
        var xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            var r = {}; try { r = JSON.parse(xhr.responseText) } catch (e) {}
            if (!r.ok) root.applyMsg = r.error || qsTr("Write failed.")
            cb()
        }
        xhr.open("POST", "http://127.0.0.1:8766/api/bryton/profile/apply")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(JSON.stringify({ direction: direction, fields: fields,
                                  athlete_id: ConnectionsService.intervalsIcuAthleteId,
                                  api_key: ConnectionsService.intervalsIcuApiKey() }))
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
            text: qsTr("These differ. Pick the value that's right for each — it's written to the "
                       + "device and/or intervals.icu.")
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
        }

        Repeater {
            model: diffModel
            delegate: Row {
                width: 420
                spacing: Theme.spacingMedium
                // The nested chip Repeater shadows `index`, so hold the row's own index here.
                property int rowIndex: index
                Text {
                    width: 120
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.labels[field] || field
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
                }
                // Device vs intervals as two selectable chips.
                Repeater {
                    model: [ { src: "device", val: deviceVal }, { src: "intervals", val: intervalsVal } ]
                    delegate: Rectangle {
                        width: 140; height: 40; radius: Theme.radiusSmall
                        color: choice === modelData.src ? Theme.primary : Theme.cardNested
                        border.width: 1
                        border.color: choice === modelData.src ? Theme.primary : Theme.border
                        Column {
                            anchors.centerIn: parent; spacing: 0
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: modelData.src === "device" ? qsTr("Bryton") : qsTr("intervals.icu")
                                color: choice === modelData.src ? Theme.card : Theme.mutedText
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: root.fmt(field, modelData.val)
                                color: choice === modelData.src ? Theme.card : Theme.text
                                font.pixelSize: Theme.fontSizeBody; font.bold: true
                            }
                        }
                        TapHandler { onTapped: diffModel.setProperty(rowIndex, "choice", modelData.src) }
                    }
                }
            }
        }

        Row {
            visible: diffModel.count > 0
            spacing: Theme.spacingSmall
            CheckBox {
                id: rememberBox
                checked: root.remember
                onToggled: root.remember = checked
                text: qsTr("Remember these choices")
            }
        }

        Text {
            visible: root.applyMsg.length > 0
            text: root.applyMsg; color: Theme.error; font.pixelSize: Theme.fontSizeCaption
        }

        Row {
            spacing: Theme.spacingSmall
            layoutDirection: Qt.RightToLeft
            width: parent.width
            RoundedButton {
                text: root.applying ? qsTr("Writing…")
                                    : (diffModel.count > 0 ? qsTr("Apply") : qsTr("Close"))
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
