import QtQuick
import QtQuick.Controls
import AmbitApp

// Bryton Aero 60 device settings over Bluetooth (André, 2026-09-26) - the Bryton Active app's
// "General settings" for the Aero 60 (SettingGeneralSetting460Activity): backlight, GPS mode,
// auto pause, key tone, sound, units. Backend: POST /api/brytonble/device hello / set-settings
// (tools/bryton_ble.py). On open the device is read; Save sends ONLY the fields changed here,
// range-checked in the tool before it connects. Auto lap is shown but not edited yet (its
// write format carries position fields not decoded yet).
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
            if (root.edited[k] !== root.original[k] && k !== "autoLapType" && k !== "autoLapMeters")
                out[k] = root.edited[k]
        return out
    }
    readonly property int changeCount: Object.keys(root.changes).length

    function post(body, done) {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            BikeDevices.mageneConnecting = false
            let r = {}
            try { r = JSON.parse(xhr.responseText) } catch (e) { r = { ok: false } }
            done(r)
        }
        BikeDevices.mageneConnecting = true        // keep the background scans off the radio
        xhr.open("POST", "http://127.0.0.1:8766/api/brytonble/device")
        xhr.setRequestHeader("Content-Type", "application/json")
        body.address = root.address
        xhr.send(JSON.stringify(body))
    }
    function take(r) {
        root.loading = false
        if (!r.ok || !r.settings) {
            root.error = r.error
                ? qsTr("Could not read the Bryton: %1").arg(r.error)
                : qsTr("Could not read the Bryton — turn it on and turn off your phone's Bluetooth.")
            return
        }
        root.error = ""
        root.original = r.settings
        root.edited = Object.assign({}, r.settings)
    }
    function load() {
        root.loading = true; root.error = ""; root.msg = ""
        root.post({ action: "hello" }, root.take)
    }
    Component.onCompleted: if (root.address.length > 0) root.load()

    function apply() {
        if (root.changeCount === 0) return
        root.applying = true; root.msg = ""
        root.post({ action: "set-settings", settings: root.changes }, function (r) {
            root.applying = false
            if (r.ok && r.settings) {
                root.original = r.settings
                root.edited = Object.assign({}, r.settings)
                root.msg = qsTr("Saved to the Bryton ✓")
            } else {
                root.msg = r.error || qsTr("The Bryton refused the change.")
            }
        })
    }

    // The Bryton Active app's own menus for the Aero 60 (BackLightMenuUtil / GpsMenuUtil).
    readonly property var backlightChoices: [
        { v: 0, t: "5 s" }, { v: 1, t: "15 s" }, { v: 2, t: "30 s" }, { v: 3, t: qsTr("1 min") },
        { v: 4, t: qsTr("2 min") }, { v: 5, t: qsTr("Never") }, { v: 6, t: qsTr("Auto") }]
    readonly property var gpsChoices: [
        { v: 3, t: "GPS + GLONASS" }, { v: 4, t: "GPS + BeiDou" }, { v: 1, t: "GPS + Galileo + QZSS" },
        { v: 2, t: qsTr("Power saving") }, { v: 0, t: qsTr("Off") }]
    readonly property var unitChoices: [{ v: 0, t: qsTr("Metric (km)") }, { v: 1, t: qsTr("Imperial (mi)") }]

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
        checked: (root.edited[key] || 0) > 0
        onToggled: root.set(key, checked ? 1 : 0)
    }

    Column {
        width: parent.width
        spacing: Theme.spacingSmall

        Text {
            visible: root.loading
            text: qsTr("Reading the Bryton over Bluetooth…")
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

            SettingRow { label: qsTr("Backlight off after"); visible: root.has("backlight")
                ChoiceBox { key: "backlight"; choices: root.backlightChoices } }
            SettingRow { label: qsTr("GPS"); visible: root.has("gpsMode")
                ChoiceBox { key: "gpsMode"; choices: root.gpsChoices } }
            SettingRow { label: qsTr("Auto pause"); visible: root.has("autoPause")
                OnOff { key: "autoPause" } }
            SettingRow { label: qsTr("Key tone"); visible: root.has("keytone")
                OnOff { key: "keytone" } }
            SettingRow { label: qsTr("Sound"); visible: root.has("sound")
                OnOff { key: "sound" } }
            SettingRow { label: qsTr("Units"); visible: root.has("unit")
                ChoiceBox { key: "unit"; choices: root.unitChoices } }
            SettingRow {
                label: qsTr("Auto lap"); visible: root.has("autoLapMeters")
                Text {
                    text: root.edited.autoLapMeters > 0
                          ? qsTr("every %1 km (change it on the device)").arg(root.edited.autoLapMeters / 1000)
                          : qsTr("off (change it on the device)")
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
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
                text: root.applying ? qsTr("Saving…") : qsTr("Save to Bryton")
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
