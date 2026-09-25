import QtQuick
import QtQuick.Controls
import AmbitApp

// Magene C406 device settings panel on the GPS settings page (André, 2026-09-25) - the OneLap app's "bike computer function
// settings" screen: backlight, auto power-off, auto pause, tones, start reminder, estimated
// power, HR / power alerts and auto lap. Backend: POST /api/magene/device read-settings /
// set-settings (tools/magene_device.py, layout decoded from OneLap's DecodeProFuncProduct).
// On open the device is read; Apply sends ONLY the fields changed here, and the tool itself
// does the read-modify-write + range checks. The time zone lives in the same block but is set
// by the clock sync on connect, so it isn't shown.
Column {
    id: root
    spacing: Theme.spacingMedium

    property string address: ""


    property bool loading: true
    property bool applying: false
    property string error: ""
    property string msg: ""
    property var original: ({})
    property var edited: ({})

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

    function post(body, done) {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            let r = {}
            try { r = JSON.parse(xhr.responseText) } catch (e) { r = { ok: false } }
            done(r)
        }
        xhr.open("POST", "http://127.0.0.1:8766/api/magene/device")
        xhr.setRequestHeader("Content-Type", "application/json")
        if (root.address.length > 0) body.address = root.address
        xhr.send(JSON.stringify(body))
    }

    // shared: the GPS settings page reads settings + screens in ONE connection ("read-config") and
    // hands the result here via `preloaded`; standalone the panel reads on its own.
    property bool shared: false
    property var preloaded: null
    onPreloadedChanged: if (preloaded) root.take(preloaded)
    Component.onCompleted: if (!root.shared) root.load()
    function take(r) {
        root.loading = false
        if (!r.ok || !r.settings) {
            root.error = r.error || qsTr("Could not read the C406 settings.")
            return
        }
        root.error = ""
        root.original = r.settings
        root.edited = Object.assign({}, r.settings)
    }
    function load() {
        root.loading = true; root.error = ""; root.msg = ""
        root.post({ action: "read-settings" }, root.take)
    }

    function apply() {
        if (root.changeCount === 0) return
        root.applying = true; root.msg = ""
        root.post({ action: "set-settings", settings: root.changes }, function (r) {
            root.applying = false
            if (r.ok && r.settings) {
                root.original = r.settings
                root.edited = Object.assign({}, r.settings)
                root.msg = qsTr("Saved to the C406 ✓")
            } else {
                root.msg = r.error || qsTr("The C406 refused the change.")
            }
        })
    }

    // Choice lists = what the OneLap app offers (LocalBikeComputerFuncDataSource etc.).
    readonly property var backlightDurations: [
        { v: 0, t: qsTr("Always on") }, { v: 5, t: "5 s" }, { v: 10, t: "10 s" },
        { v: 15, t: "15 s" }, { v: 30, t: "30 s" }, { v: 60, t: "60 s" }]
    readonly property var backlightLevels: [
        { v: 0, t: qsTr("Low") }, { v: 1, t: qsTr("Medium") }, { v: 2, t: qsTr("High") }]
    readonly property var autoOffChoices: [{ v: 0, t: qsTr("Off") }].concat(
        [5, 10, 15, 20, 30, 40, 60].map(m => ({ v: m, t: qsTr("%1 min").arg(m) })))
    readonly property var autoPauseChoices: [{ v: 0, t: qsTr("Off") }].concat(
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map(k => ({ v: k, t: qsTr("Below %1 km/h").arg(k) })))
    readonly property var lapTypes: [{ v: 0, t: qsTr("Distance") }, { v: 1, t: qsTr("Time") }]

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
        width: 170
        model: choices.map(c => c.t)
        currentIndex: choices.findIndex(c => c.v === root.edited[key])
        onActivated: (i) => root.set(key, choices[i].v)
    }
    component OnOff: RoundedSwitch {
        property string key: ""
        checked: (root.edited[key] || 0) > 0
        onToggled: root.set(key, checked ? 1 : 0)
    }
    // An alert: 0 = off; switching on uses the app's own default (170 bpm / 1000 W).
    component AlertField: Row {
        id: alert
        property string key: ""
        property int defaultOn: 0
        property int lo: 0
        property int hi: 0
        spacing: Theme.spacingSmall
        RoundedTextField {
            visible: (root.edited[alert.key] || 0) > 0
            width: 80
            text: String(root.edited[alert.key] || "")
            validator: IntValidator { bottom: alert.lo; top: alert.hi }
            onEditingFinished: if (acceptableInput) root.set(alert.key, Number(text))
        }
        RoundedSwitch {
            checked: (root.edited[alert.key] || 0) > 0
            onToggled: root.set(alert.key, checked ? alert.defaultOn : 0)
        }
    }

    Column {
        width: parent.width
        spacing: Theme.spacingSmall

        Text {
            visible: root.loading
            text: qsTr("Reading the C406…")
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
        }
        Text {
            visible: root.error.length > 0
            width: parent.width; wrapMode: Text.WordWrap
            text: root.error; color: Theme.error; font.pixelSize: Theme.fontSizeBody
        }

        Column {
            visible: !root.loading && root.error.length === 0
            width: parent.width
            spacing: Theme.spacingSmall

            SettingRow { label: qsTr("Auto backlight"); visible: root.has("autoBacklight")
                OnOff { key: "autoBacklight" } }
            SettingRow { label: qsTr("Backlight duration"); visible: root.has("backlightDuration")
                ChoiceBox { key: "backlightDuration"; choices: root.backlightDurations } }
            SettingRow { label: qsTr("Backlight level"); visible: root.has("backlightLevel")
                ChoiceBox { key: "backlightLevel"; choices: root.backlightLevels } }
            SettingRow { label: qsTr("Auto power-off"); visible: root.has("autoOff")
                ChoiceBox { key: "autoOff"; choices: root.autoOffChoices } }
            SettingRow { label: qsTr("Auto pause"); visible: root.has("autoPause")
                ChoiceBox { key: "autoPause"; choices: root.autoPauseChoices } }
            SettingRow { label: qsTr("Prompt tone"); visible: root.has("promptTone")
                OnOff { key: "promptTone" } }
            SettingRow { label: qsTr("Key tone"); visible: root.has("keyTone")
                OnOff { key: "keyTone" } }
            SettingRow { label: qsTr("Start-ride reminder"); visible: root.has("startReminder")
                OnOff { key: "startReminder" } }
            SettingRow { label: qsTr("Estimated power"); visible: root.has("estimatedPower")
                OnOff { key: "estimatedPower" } }
            SettingRow { label: qsTr("Heart-rate alert (bpm)"); visible: root.has("hrAlert")
                AlertField { key: "hrAlert"; defaultOn: 170; lo: 100; hi: 240 } }
            SettingRow { label: qsTr("Power alert (W)"); visible: root.has("powerAlert")
                AlertField { key: "powerAlert"; defaultOn: 1000; lo: 100; hi: 2500 } }
            SettingRow {
                label: qsTr("Auto lap"); visible: root.has("autoLap")
                RoundedSwitch {
                    checked: root.edited.autoLap === 1
                    // Same defaults the app writes when switching it on: every 10 km.
                    onToggled: {
                        root.set("autoLap", checked ? 1 : 0)
                        if (checked && !(root.edited.autoLapValue > 0)) {
                            root.set("autoLapType", 0); root.set("autoLapValue", 100)
                        }
                    }
                }
            }
            SettingRow {
                label: qsTr("Lap every")
                visible: root.has("autoLap") && root.edited.autoLap === 1
                Row {
                    spacing: Theme.spacingSmall
                    // Distance is stored as km x 10, time as minutes.
                    RoundedTextField {
                        width: 70
                        text: root.edited.autoLapType === 0
                              ? String((root.edited.autoLapValue || 0) / 10)
                              : String(root.edited.autoLapValue || 0)
                        validator: DoubleValidator { bottom: root.edited.autoLapType === 0 ? 0.1 : 1
                                                     top: root.edited.autoLapType === 0 ? 100 : 999 }
                        onEditingFinished: if (acceptableInput)
                            root.set("autoLapValue", root.edited.autoLapType === 0
                                     ? Math.round(Number(text) * 10) : Math.round(Number(text)))
                    }
                    RoundedComboBox {
                        width: 120
                        model: [qsTr("km"), qsTr("min")]
                        currentIndex: root.edited.autoLapType === 1 ? 1 : 0
                        // Switching the unit resets to the app's defaults (10 km / 30 min).
                        onActivated: (i) => { root.set("autoLapType", i)
                                              root.set("autoLapValue", i === 0 ? 100 : 30) }
                    }
                }
            }
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
            RoundedButton {
                text: root.applying ? qsTr("Saving…") : qsTr("Save to C406")
                enabled: !root.loading && !root.applying && root.changeCount > 0
                onClicked: root.apply()
            }
            RoundedButton {
                visible: root.changeCount > 0
                text: qsTr("Undo changes"); enabled: !root.applying
                onClicked: root.edited = Object.assign({}, root.original)
            }
        }
    }
}
