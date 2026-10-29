import QtQuick
import QtQuick.Controls
import AmbitApp

// Bluetooth scale: set-up and manage (ticket weight-sources/03). The scale is an Etekcity FIT 8S,
// which only broadcasts its reading while someone stands on it - no pairing, no app, no account.
// Four steps, one per screen: 1 step on it, 2 pick what was heard, 3 check the display against
// the number shown, 4 height and sex - only when neither intervals.icu nor the watch has them.
// Opened from the Weight page and from Settings → Connections.
ThemedDialog {
    id: root
    title: qsTr("Bluetooth scale")
    modal: true
    parent: Overlay.overlay
    anchors.centerIn: Overlay.overlay
    standardButtons: Dialog.Close

    // 0 manage (already set up), 1 step on it, 2 searching/found, 3 check the display, 4 profile
    property int step: 1
    property var found: []
    property var chosen: null
    property string findError: ""
    property bool searching: false
    readonly property var sc: WeightService.scale
    readonly property bool setUp: !!sc.address

    function timeAgo(t) {
        if (!t) return qsTr("none yet")
        var s = Math.max(0, Math.round(Date.now() / 1000 - t))
        if (s < 90) return qsTr("just now")
        if (s < 5400) return qsTr("%1 min ago").arg(Math.round(s / 60))
        if (s < 129600) return qsTr("%1 h ago").arg(Math.round(s / 3600))
        return qsTr("%1 days ago").arg(Math.round(s / 86400))
    }

    onAboutToShow: {
        findError = ""; found = []; chosen = null; searching = false
        step = setUp ? 0 : 1
    }

    Connections {
        target: WeightService
        function onScaleFound(list, error) {
            root.searching = false
            root.found = list
            root.findError = error
            root.step = 2
        }
    }

    Column {
        width: 360
        spacing: Theme.spacingSmall

        // --- 0: manage ---
        Column {
            width: parent.width
            spacing: Theme.spacingSmall
            visible: root.step === 0
            Row {
                spacing: 6
                Rectangle {
                    width: 8; height: 8; radius: 4; anchors.verticalCenter: parent.verticalCenter
                    color: root.sc.listening ? Theme.success : Theme.mutedText
                }
                Text {
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody
                    text: root.sc.listening ? qsTr("Listening") : qsTr("Not listening")
                }
            }
            Text {
                width: parent.width; wrapMode: Text.WordWrap; color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                text: qsTr("Scale %1 · last weigh-in: %2").arg(root.sc.address || "")
                      .arg(root.timeAgo(root.sc.lastWeighIn ? root.sc.lastWeighIn.t : 0))
            }
            Text {
                width: parent.width; wrapMode: Text.WordWrap; color: Theme.error
                font.pixelSize: Theme.fontSizeCaption
                visible: !!root.sc.error
                text: root.sc.error || ""
            }
            Row {
                spacing: Theme.spacingSmall
                RoundedButton {
                    text: root.sc.listening ? qsTr("Stop listening") : qsTr("Start listening")
                    onClicked: root.sc.listening ? WeightService.scaleStop() : WeightService.scaleStart()
                }
                RoundedButton { text: qsTr("Forget scale"); onClicked: { WeightService.scaleForget(); root.step = 1 } }
            }
            RoundedButton { text: qsTr("Body profile…"); onClicked: root.step = 4 }
        }

        // --- 1: step on it ---
        Column {
            width: parent.width
            spacing: Theme.spacingSmall
            visible: root.step === 1
            Text { text: qsTr("Step 1 of 4"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
            Text {
                width: parent.width; wrapMode: Text.WordWrap; color: Theme.text
                font.pixelSize: Theme.fontSizeBody
                text: qsTr("Turn Bluetooth on, press Search, then step on the scale barefoot and stay " +
                           "until the display locks. The scale only talks while someone is on it.")
            }
            Text {
                width: parent.width; wrapMode: Text.WordWrap; color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                text: qsTr("Supported: Etekcity FIT 8S. No pairing, no app and no account are needed.")
            }
            RoundedButton {
                text: root.searching ? qsTr("Searching… step on the scale") : qsTr("Search")
                enabled: !root.searching
                onClicked: { root.searching = true; root.findError = ""; WeightService.scaleFind(20) }
            }
        }

        // --- 2: what was heard ---
        Column {
            width: parent.width
            spacing: Theme.spacingSmall
            visible: root.step === 2
            Text { text: qsTr("Step 2 of 4"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
            Text {
                width: parent.width; wrapMode: Text.WordWrap; color: Theme.error
                font.pixelSize: Theme.fontSizeBody
                visible: root.findError.length > 0
                text: root.findError
            }
            Text {
                width: parent.width; wrapMode: Text.WordWrap; color: Theme.mutedText
                font.pixelSize: Theme.fontSizeBody
                visible: root.findError.length === 0 && root.found.length === 0
                text: qsTr("No scale heard. Check that Bluetooth is on and that you stood on the scale " +
                           "while searching, then try again.")
            }
            Repeater {
                model: root.found
                delegate: RoundedButton {
                    width: parent.width
                    text: qsTr("Etekcity scale %1 · %2 kg · signal %3 dBm")
                          .arg(modelData.address).arg(Number(modelData.weightKg).toFixed(2)).arg(modelData.rssi)
                    onClicked: { root.chosen = modelData; root.step = 3 }
                }
            }
            RoundedButton { text: qsTr("Search again"); onClicked: root.step = 1 }
        }

        // --- 3: does the display match? ---
        Column {
            width: parent.width
            spacing: Theme.spacingSmall
            visible: root.step === 3
            Text { text: qsTr("Step 3 of 4"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
            Text {
                width: parent.width; wrapMode: Text.WordWrap; color: Theme.text
                font.pixelSize: Theme.fontSizeBody
                text: qsTr("The scale sent %1 kg. The display rounds to 0.05 kg, so it may differ by " +
                           "up to 0.05. Does it match?")
                      .arg(root.chosen ? Number(root.chosen.weightKg).toFixed(2) : "")
            }
            Row {
                spacing: Theme.spacingSmall
                RoundedButton {
                    text: qsTr("Yes, it matches")
                    // Height and sex already known (intervals.icu or the watch): nothing to ask.
                    onClicked: { WeightService.scaleStart(root.chosen.address)
                                 if (WeightService.bodyProfile.source) root.close(); else root.step = 4 }
                }
                RoundedButton { text: qsTr("No"); onClicked: root.step = 2 }
            }
        }

        // --- 4: profile ---
        Column {
            width: parent.width
            spacing: Theme.spacingSmall
            visible: root.step === 4
            Text { text: qsTr("Step 4 of 4"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
            Text {
                width: parent.width; wrapMode: Text.WordWrap; color: Theme.text
                font.pixelSize: Theme.fontSizeBody
                text: qsTr("Body fat and water are estimated from height and sex. They come from " +
                           "intervals.icu or your watch when either has them; type them only to override.")
            }
            Text {
                width: parent.width; wrapMode: Text.WordWrap; color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                visible: !!WeightService.bodyProfile.source
                text: qsTr("Now: %1 cm, %2 (%3)").arg(WeightService.bodyProfile.heightCm)
                      .arg(WeightService.bodyProfile.sex === "M" ? qsTr("male") : qsTr("female"))
                      .arg(WeightService.bodyProfile.source === "manual" ? qsTr("typed by hand")
                           : WeightService.bodyProfile.source === "watch" ? qsTr("from your watch")
                           : qsTr("from intervals.icu"))
            }
            Row {
                spacing: Theme.spacingSmall
                RoundedTextField {
                    id: heightField; width: 110; placeholderText: qsTr("height cm")
                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                    text: WeightService.bodyProfile.heightCm > 0 ? String(WeightService.bodyProfile.heightCm) : ""
                }
                RoundedRadioButton {
                    id: sexM; text: qsTr("Male")
                    checked: WeightService.bodyProfile.sex !== "F"
                }
                RoundedRadioButton {
                    id: sexF; text: qsTr("Female")
                    checked: WeightService.bodyProfile.sex === "F"
                }
            }
            Row {
                spacing: Theme.spacingSmall
                RoundedButton {
                    text: qsTr("Save")
                    enabled: WatchUnits.parseDecimal(heightField.text) >= 100 && WatchUnits.parseDecimal(heightField.text) <= 230
                    onClicked: { WeightService.setBodyProfile(WatchUnits.parseDecimal(heightField.text), sexF.checked ? "F" : "M")
                                 root.close() }
                }
                RoundedButton { text: qsTr("Skip"); onClicked: root.close() }
            }
        }
    }
}
