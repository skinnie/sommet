import QtQuick
import QtQuick.Controls
import AmbitApp

// Wahoo ELEMNT device settings (André, 2026-10-03: "follow your order. be sure to have them usb
// and bluetooth. don't re-invent the wheel UI wise") - the same read / edit / "Save" flow and row
// controls as the Bryton Bluetooth settings panel, grouped like the Wahoo app's Settings tab:
// display, LEDs, sounds, ride, planned workouts. Backend: GET/POST /api/wahoo/settings
// (tools/wahoo_settings.py). Save sends only the fields changed here; the tool reads everything
// back and reports any setting the ELEMNT didn't take.
Column {
    id: root
    spacing: Theme.spacingMedium

    property bool loading: true
    property bool applying: false
    property string error: ""
    property string msg: ""
    property var original: ({})
    property var edited: ({})
    property real autoPauseOn: 0.447
    property string via: ""

    function set(key, value) {
        const next = Object.assign({}, root.edited)
        next[key] = value
        root.edited = next
    }
    function has(key) { return root.edited[key] !== undefined }
    readonly property var changes: {
        const out = {}
        for (const k in root.edited)
            if (root.edited[k] !== root.original[k]) out[k] = root.edited[k]
        return out
    }
    readonly property int changeCount: Object.keys(root.changes).length

    function api(method, body, done) {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            let r = {}
            try { r = JSON.parse(xhr.responseText) } catch (e) { r = { ok: false } }
            done(r)
        }
        xhr.open(method, "http://127.0.0.1:8766/api/wahoo/settings")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(body ? JSON.stringify(body) : null)
    }
    function take(r) {
        root.loading = false
        if (!r.ok || !r.settings) {
            root.error = r.error
                ? qsTr("Could not read the ELEMNT: %1").arg(r.error)
                : qsTr("Could not read the ELEMNT — turn it on and make sure no phone is connected to it.")
            return
        }
        root.error = ""
        root.via = r.via || ""
        root.autoPauseOn = r.autoPauseOn || 0.447
        root.original = r.settings
        root.edited = Object.assign({}, r.settings)
    }
    function load() {
        root.loading = true; root.error = ""; root.msg = ""
        root.api("GET", null, root.take)
    }
    Component.onCompleted: root.load()

    function apply() {
        if (root.changeCount === 0) return
        root.applying = true; root.msg = ""
        root.api("POST", { settings: root.changes }, function (r) {
            root.applying = false
            if (r.ok && r.settings) {
                root.take(r)
                root.msg = qsTr("Saved to the ELEMNT ✓")
            } else {
                root.msg = r.error || qsTr("The ELEMNT refused the change.")
            }
        })
    }

    // The Wahoo app's own menus.
    readonly property var backlightChoices: [{ v: 0, t: qsTr("On") }, { v: 1, t: qsTr("Timed") }, { v: 2, t: qsTr("Off") }]
    readonly property var shutdownChoices: [{ v: 0, t: qsTr("Never") }, { v: 15, t: qsTr("15 min") },
        { v: 30, t: qsTr("30 min") }, { v: 60, t: qsTr("1 hour") }, { v: 120, t: qsTr("2 hours") }]
    readonly property var ledChoices: [{ v: 0, t: qsTr("Off") }, { v: 1, t: qsTr("Speed") },
        { v: 5, t: qsTr("Power") }, { v: 6, t: qsTr("Heart rate") }]
    readonly property var lapChoices: [{ v: 0, t: qsTr("Off") }, { v: 1, t: qsTr("Distance") }, { v: 2, t: qsTr("Time") }]

    component Section: Text {
        topPadding: Theme.spacingSmall
        color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
    }
    component SettingRow: Item {
        id: row
        property string label: ""
        default property alias control: holder.data
        width: parent ? parent.width : 0
        height: Math.max(36, holder.childrenRect.height)
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: row.label
            color: Theme.text; font.pixelSize: Theme.fontSizeBody
        }
        Item {
            id: holder
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            width: childrenRect.width; height: childrenRect.height
        }
    }
    component ChoiceBox: RoundedComboBox {
        property string key: ""
        property var choices: []
        width: 200
        model: choices.map(c => c.t)
        currentIndex: choices.findIndex(c => c.v === root.edited[key])
        onActivated: (i) => root.set(key, choices[i].v)
    }
    component OnOff: RoundedSwitch {
        property string key: ""
        checked: !!root.edited[key]
        onToggled: root.set(key, checked)
    }
    component Number: RoundedTextField {
        property string key: ""
        property real scale: 1          // shown = stored / scale
        width: 90
        text: root.edited[key] !== undefined ? String(Math.round(root.edited[key] / scale * 10) / 10) : ""
        inputMethodHints: Qt.ImhFormattedNumbersOnly
        onEditingFinished: {
            const v = parseFloat(text.replace(",", "."))
            if (!isNaN(v) && v > 0) root.set(key, Math.round(v * scale))
        }
    }

    Column {
        width: parent.width
        spacing: Theme.spacingSmall
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
        RoundedButton {
            visible: !root.loading && root.error.length > 0
            text: qsTr("Try again")
            onClicked: root.load()
        }
        Column {
            visible: !root.loading && root.error.length === 0
            width: parent.width
            spacing: Theme.spacingSmall

            Section { text: qsTr("Display") }
            SettingRow { label: qsTr("Backlight"); visible: root.has("backlight")
                ChoiceBox { key: "backlight"; choices: root.backlightChoices } }
            SettingRow { label: qsTr("Backlight timer (seconds)"); visible: root.edited.backlight === 1
                Number { key: "backlightSeconds" } }
            SettingRow { label: qsTr("Auto shutdown"); visible: root.has("autoShutdownMin")
                ChoiceBox { key: "autoShutdownMin"; choices: root.shutdownChoices } }

            Section { text: qsTr("LEDs") }
            SettingRow { label: qsTr("LED mode"); visible: root.has("ledMode")
                ChoiceBox { key: "ledMode"; choices: root.ledChoices } }
            SettingRow { label: qsTr("Workout paused / resumed"); visible: root.has("ledWorkout")
                OnOff { key: "ledWorkout" } }
            SettingRow { label: qsTr("Notification received"); visible: root.has("ledNotification")
                OnOff { key: "ledNotification" } }
            SettingRow { label: qsTr("Turn-by-turn directions"); visible: root.has("ledNavigation")
                OnOff { key: "ledNavigation" } }
            SettingRow { label: qsTr("Strava Live Segments"); visible: root.has("ledSegments")
                OnOff { key: "ledSegments" } }
            SettingRow { label: qsTr("Planned workouts"); visible: root.has("ledPlans")
                OnOff { key: "ledPlans" } }

            Section { text: qsTr("Sounds") }
            SettingRow { label: qsTr("Workout paused / resumed"); visible: root.has("soundWorkout")
                OnOff { key: "soundWorkout" } }
            SettingRow { label: qsTr("Notification received"); visible: root.has("soundNotification")
                OnOff { key: "soundNotification" } }
            SettingRow { label: qsTr("Turn-by-turn directions"); visible: root.has("soundNavigation")
                OnOff { key: "soundNavigation" } }
            SettingRow { label: qsTr("Planned workouts"); visible: root.has("soundPlans")
                OnOff { key: "soundPlans" } }

            Section { text: qsTr("Ride") }
            SettingRow { label: qsTr("Auto pause"); visible: root.has("autoPauseSpeed")
                RoundedSwitch {
                    checked: (root.edited.autoPauseSpeed || 0) > 0
                    onToggled: root.set("autoPauseSpeed", checked
                        ? ((root.original.autoPauseSpeed || 0) > 0 ? root.original.autoPauseSpeed : root.autoPauseOn) : 0)
                }
            }
            SettingRow { label: qsTr("Auto lap"); visible: root.has("autoLapMode")
                ChoiceBox { key: "autoLapMode"; choices: root.lapChoices } }
            SettingRow { label: qsTr("Auto lap every (km)"); visible: root.edited.autoLapMode === 1
                Number { key: "autoLapMeters"; scale: 1000 } }
            SettingRow { label: qsTr("Auto lap every (minutes)"); visible: root.edited.autoLapMode === 2
                Number { key: "autoLapSeconds"; scale: 60 } }
            SettingRow { label: qsTr("Always rotate maps"); visible: root.has("rotateMaps")
                OnOff { key: "rotateMaps" } }
            SettingRow { label: qsTr("Include zeros in average cadence"); visible: root.has("zerosInCadence")
                OnOff { key: "zerosInCadence" } }
            SettingRow { label: qsTr("Include zeros in average power"); visible: root.has("zerosInPower")
                OnOff { key: "zerosInPower" } }

            Section { text: qsTr("Planned workouts") }
            SettingRow { label: qsTr("Notifications on other pages"); visible: root.has("plansNotifyOtherPages")
                OnOff { key: "plansNotifyOtherPages" } }
            SettingRow { label: qsTr("Auto lap on interval"); visible: root.has("plansAutoLapInterval")
                OnOff { key: "plansAutoLapInterval" } }
            SettingRow { label: qsTr("Strava Live Segments during workouts"); visible: root.has("plansSegments")
                OnOff { key: "plansSegments" } }
        }
        Text {
            visible: root.msg.length > 0
            width: parent.width; wrapMode: Text.WordWrap
            text: root.msg
            color: root.msg.indexOf("✓") >= 0 ? Theme.success : Theme.error
            font.pixelSize: Theme.fontSizeCaption
        }
        Row {
            spacing: Theme.spacingSmall
            layoutDirection: Qt.RightToLeft
            width: parent.width
            visible: !root.loading && root.error.length === 0
            RoundedButton {
                text: root.applying ? qsTr("Saving…") : qsTr("Save to ELEMNT")
                enabled: !root.applying && root.changeCount > 0
                onClicked: root.apply()
            }
            RoundedButton {
                visible: root.changeCount > 0
                text: qsTr("Undo changes"); enabled: !root.applying
                onClicked: root.edited = Object.assign({}, root.original)
            }
            RoundedButton {
                visible: root.changeCount === 0
                text: qsTr("Re-read"); enabled: !root.applying
                onClicked: root.load()
            }
        }
    }
}
