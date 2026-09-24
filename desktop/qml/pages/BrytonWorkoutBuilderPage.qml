import QtQuick
import QtQuick.Controls
import AmbitApp

// Bryton Aero 60 Workout Builder (André, 2026-09-24) - the desktop twin of the Bryton Active app's
// "Plan Workout" screen (see the app screenshots: UNIT / BASED ON / INTERVAL header, then a list of
// Warm Up / Work / Recovery / Cool Down steps with a duration and a target band). Targets are typed
// straight in the workout's unit (%FTP / %MHR / %LTHR / km-h / rpm) exactly like the app - no
// athlete numbers needed to build - and for the % units we show the watt/bpm equivalent next to the
// field, computed from the DEVICE'S OWN FTP/Max HR/LTHR (read on open) so it matches the watch.
//
// The Bryton stores one unit + one interval mode for the whole workout and flattens repeats, so the
// model here is a single unit/mode + a flat step list. "Send to Bryton" encodes it as-is (backend
// /api/bryton/workout/native -> tools/bryton_workout.py) into System/Plan/Cycling.
Item {
    id: root

    property string workoutName: qsTr("New Workout")
    property string unit: "ftp"                    // ftp | mhr | lthr | speed | cadence
    property bool rangeMode: true                  // true = Range (low..high), false = Target (single)
    property string intervalMode: "time"           // time | distance
    property var device: ({})                      // {ftp, max_hr, lthr, ...} for the live preview
    property string sendMsg: ""
    property bool sending: false

    readonly property var unitKeys: ["ftp", "mhr", "lthr", "speed", "cadence"]
    readonly property var unitLabels: ["FTP", "MHR", "LTHR", "Speed", "Cadence"]
    readonly property var intensityKeys: ["warmup", "work", "recovery", "cooldown"]
    readonly property var intensityLabels: [qsTr("Warm Up"), qsTr("Work"), qsTr("Recovery"), qsTr("Cool Down")]

    function unitSuffix() {
        if (unit === "speed") return qsTr("km/h")
        if (unit === "cadence") return qsTr("rpm")
        return "%"
    }
    // For a % unit, the absolute watt/bpm a percentage resolves to on THIS device.
    function preview(pct) {
        var base = unit === "ftp" ? device.ftp : unit === "mhr" ? device.max_hr
                 : unit === "lthr" ? device.lthr : 0
        if (!base || unit === "speed" || unit === "cadence") return ""
        var v = Math.round(pct / 100 * base)
        return "≈ " + v + (unit === "ftp" ? " W" : " bpm")
    }

    ListModel { id: stepsModel }

    function addStep(intensity) {
        var isPct = (unit === "ftp" || unit === "mhr" || unit === "lthr")
        var lo = isPct ? (intensity === "work" ? 88 : 55) : (unit === "cadence" ? 85 : 25)
        var hi = isPct ? (intensity === "work" ? 95 : 65) : (unit === "cadence" ? 90 : 30)
        stepsModel.append({ intensity: intensity, durVal: intensity === "work" ? 5 : 10,
                            low: lo, high: hi })
    }

    function fetchDevice() {
        var xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            try { var r = JSON.parse(xhr.responseText); if (r.ok && r.device) root.device = r.device } catch (e) {}
        }
        xhr.open("POST", "http://127.0.0.1:8766/api/bryton/profile/compare")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(JSON.stringify({}))     // no creds needed - we only want the device side
    }

    function send() {
        if (stepsModel.count === 0) { root.sendMsg = qsTr("Add at least one step."); return }
        var steps = []
        for (var i = 0; i < stepsModel.count; i++) {
            var s = stepsModel.get(i)
            var dur = root.intervalMode === "time" ? Math.round(s.durVal * 60)   // min -> s
                                                   : Math.round(s.durVal * 1000) // km -> m
            steps.push({ intensity: s.intensity, duration: dur,
                         low: s.low, high: root.rangeMode ? s.high : s.low })
        }
        var workout = { name: root.workoutName, unit: root.unit, sport: 2,
                        based_on: root.rangeMode ? "range" : "target",
                        interval_mode: root.intervalMode, steps: steps }
        root.sending = true; root.sendMsg = ""
        var xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            root.sending = false
            var r = {}; try { r = JSON.parse(xhr.responseText) } catch (e) {}
            root.sendMsg = r.ok ? qsTr("Sent “%1” to the Bryton.").arg(r.file || root.workoutName)
                                : (r.error || qsTr("Send failed."))
        }
        xhr.open("POST", "http://127.0.0.1:8766/api/bryton/workout/native")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(JSON.stringify({ workout: workout }))
    }

    Component.onCompleted: { fetchDevice(); if (stepsModel.count === 0) { addStep("warmup"); addStep("work"); addStep("cooldown") } }

    Flickable {
        anchors.fill: parent
        anchors.margins: Theme.spacingLarge
        contentHeight: col.height
        clip: true

        Column {
            id: col
            width: parent.width
            spacing: Theme.spacingLarge

            Text {
                text: qsTr("Workout Builder")
                color: Theme.text; font.pixelSize: Theme.fontSizeTitle; font.bold: true
            }
            Text {
                text: qsTr("Build a workout for the Bryton Aero 60, like its own app. Targets are in "
                           + "the workout's unit; the watt/bpm shown uses the device's own thresholds.")
                width: parent.width; wrapMode: Text.WordWrap
                color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
            }

            // ---- header: name + unit / based-on / interval -------------------------------------
            Card {
                width: parent.width
                Column {
                    width: parent.width; spacing: Theme.spacingMedium

                    Row {
                        width: parent.width; spacing: Theme.spacingMedium
                        Text { anchors.verticalCenter: parent.verticalCenter; text: qsTr("Name")
                               color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody; width: 90 }
                        TextField {
                            id: nameField
                            width: parent.width * 0.6
                            text: root.workoutName
                            onTextChanged: root.workoutName = text
                        }
                    }

                    // UNIT
                    Row {
                        width: parent.width; spacing: Theme.spacingSmall
                        Text { anchors.verticalCenter: parent.verticalCenter; text: qsTr("Unit")
                               color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody; width: 90 }
                        Repeater {
                            model: root.unitKeys.length
                            delegate: RoundedButton {
                                text: root.unitLabels[index]
                                onClicked: root.unit = root.unitKeys[index]
                                // filled look when selected
                                opacity: root.unit === root.unitKeys[index] ? 1.0 : 0.55
                            }
                        }
                    }

                    // BASED ON
                    Row {
                        width: parent.width; spacing: Theme.spacingSmall
                        Text { anchors.verticalCenter: parent.verticalCenter; text: qsTr("Based on")
                               color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody; width: 90 }
                        RoundedButton { text: qsTr("Range");  opacity: root.rangeMode ? 1 : 0.55
                                        onClicked: root.rangeMode = true }
                        RoundedButton { text: qsTr("Target"); opacity: root.rangeMode ? 0.55 : 1
                                        onClicked: root.rangeMode = false }
                    }

                    // INTERVAL
                    Row {
                        width: parent.width; spacing: Theme.spacingSmall
                        Text { anchors.verticalCenter: parent.verticalCenter; text: qsTr("Interval")
                               color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody; width: 90 }
                        RoundedButton { text: qsTr("Time");     opacity: root.intervalMode === "time" ? 1 : 0.55
                                        onClicked: root.intervalMode = "time" }
                        RoundedButton { text: qsTr("Distance"); opacity: root.intervalMode === "distance" ? 1 : 0.55
                                        onClicked: root.intervalMode = "distance" }
                    }
                }
            }

            // ---- steps -------------------------------------------------------------------------
            Card {
                width: parent.width
                Column {
                    width: parent.width; spacing: Theme.spacingMedium

                    Text { text: qsTr("Steps"); color: Theme.text
                           font.pixelSize: Theme.fontSizeHeading; font.bold: true }

                    Repeater {
                        model: stepsModel
                        delegate: Rectangle {
                            width: col.width - 2 * Theme.spacingMedium
                            height: 56; radius: Theme.radiusSmall; color: Theme.cardNested
                            property int rowIndex: index
                            Row {
                                anchors.fill: parent
                                anchors.margins: Theme.spacingSmall
                                spacing: Theme.spacingMedium

                                RoundedComboBox {
                                    width: 120; anchors.verticalCenter: parent.verticalCenter
                                    model: root.intensityLabels
                                    currentIndex: root.intensityKeys.indexOf(intensity)
                                    onActivated: stepsModel.setProperty(rowIndex, "intensity",
                                                                        root.intensityKeys[currentIndex])
                                }
                                Column {
                                    anchors.verticalCenter: parent.verticalCenter
                                    Text { text: root.intervalMode === "time" ? qsTr("min") : qsTr("km")
                                           color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                    SpinBox {
                                        width: 90; from: 0; to: 600; value: durVal
                                        onValueModified: stepsModel.setProperty(rowIndex, "durVal", value)
                                    }
                                }
                                Column {
                                    anchors.verticalCenter: parent.verticalCenter
                                    Text { text: root.rangeMode ? qsTr("low %1").arg(root.unitSuffix())
                                                                : root.unitSuffix()
                                           color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                    SpinBox {
                                        width: 90; from: 0; to: 999; value: low
                                        onValueModified: stepsModel.setProperty(rowIndex, "low", value)
                                    }
                                }
                                Column {
                                    visible: root.rangeMode
                                    anchors.verticalCenter: parent.verticalCenter
                                    Text { text: qsTr("high %1").arg(root.unitSuffix())
                                           color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                    SpinBox {
                                        width: 90; from: 0; to: 999; value: high
                                        onValueModified: stepsModel.setProperty(rowIndex, "high", value)
                                    }
                                }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: root.preview(root.rangeMode ? Math.round((low + high) / 2) : low)
                                    color: Theme.secondary; font.pixelSize: Theme.fontSizeCaption
                                }
                                RoundedButton {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: qsTr("✕"); onClicked: stepsModel.remove(rowIndex)
                                }
                            }
                        }
                    }

                    Row {
                        spacing: Theme.spacingSmall
                        Repeater {
                            model: root.intensityKeys.length
                            delegate: RoundedButton {
                                text: qsTr("+ %1").arg(root.intensityLabels[index])
                                onClicked: root.addStep(root.intensityKeys[index])
                            }
                        }
                    }
                }
            }

            // ---- send --------------------------------------------------------------------------
            Row {
                spacing: Theme.spacingMedium
                RoundedButton {
                    text: root.sending ? qsTr("Sending…") : qsTr("Send to Bryton")
                    enabled: !root.sending
                    onClicked: root.send()
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: root.sendMsg.length > 0
                    text: root.sendMsg; color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
                }
            }
        }
    }
}
