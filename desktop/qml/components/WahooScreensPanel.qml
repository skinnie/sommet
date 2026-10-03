import QtQuick
import QtQuick.Controls
import AmbitApp

// Wahoo ELEMNT data pages - the whole layout, built-in pages included (tools/wahoo_pages.py,
// /api/wahoo/pages). The ELEMNT takes a complete layout over either link: the USB cable (adb
// root: its settings file) or Bluetooth (the companion app's display-config message), so the
// panel saves through "Auto" (cable when plugged, else Bluetooth) or a link picked by hand
// (André, 2026-10-02: "why cant we do BLE and Cable for page editing?"). Every page can have its
// fields picked and reordered, be shown or hidden and moved; Workout, Lap and custom pages can
// gain or lose fields; only custom pages can be deleted. Each save is read back and compared.
Column {
    id: root
    spacing: Theme.spacingMedium

    Component.onCompleted: root.load()

    property bool loading: true
    property bool saving: false
    property string error: ""
    property string msg: ""
    property string via: "auto"          // auto | usb | ble
    property string lastVia: ""          // the link the last read/save actually used
    property var pages: []               // [{id, typeName, custom, resizable, enabled, isNew, fields:[ids]}]
    property string original: "[]"
    property int maxFields: 11
    property var fieldIds: []
    property var fieldNames: []
    property var nameById: ({})
    readonly property bool changed: JSON.stringify(root.pages) !== root.original
    readonly property var viaOptions: ["auto", "usb", "ble"]

    function api(method, body, done) {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            let r = {}
            try { r = JSON.parse(xhr.responseText) } catch (e) { r = { ok: false } }
            done(r)
        }
        xhr.open(method, "http://127.0.0.1:8766/api/wahoo/pages" + (method === "GET" ? "?via=" + root.via : ""))
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(body ? JSON.stringify(body) : null)
    }

    function take(r) {
        const ids = [], names = [], byId = {}
        for (const g of (r.groups || []))
            for (const f of g.fields) { ids.push(f.id); names.push(g.group + " · " + f.name); byId[f.id] = f.name }
        // Fields the catalogue doesn't list (rare built-in ones) stay selectable under their own name.
        for (const p of (r.pages || []))
            for (const f of p.fields)
                if (byId[f.id] === undefined) { ids.push(f.id); names.push(f.name); byId[f.id] = f.name }
        root.fieldIds = ids; root.fieldNames = names; root.nameById = byId
        root.maxFields = r.maxFields || 11
        root.lastVia = r.via || ""
        root.pages = (r.pages || []).map(p => ({ id: p.id, typeName: p.typeName, custom: p.custom,
                                                 resizable: p.resizable, enabled: p.enabled, isNew: false,
                                                 name: p.name || "", fields: p.fields.map(f => f.id) }))
        root.original = JSON.stringify(root.pages)
    }

    // Reset to Wahoo's default pages (custom pages are dropped) - asks first.
    property bool confirmReset: false
    function resetPages() {
        root.confirmReset = false; root.saving = true; root.msg = ""
        root.api("POST", { via: root.via, reset: true }, function (r) {
            root.saving = false
            if (r.ok) { root.take(r); root.msg = qsTr("Pages reset to Wahoo's default over %1 ✓").arg(root.viaLabel(r.via)) }
            else root.msg = r.error || qsTr("Couldn't reset the ELEMNT's pages.")
        })
    }

    function viaLabel(v) {
        return v === "usb" ? qsTr("USB cable") : v === "ble" ? qsTr("Bluetooth") : qsTr("Auto")
    }

    function load() {
        root.loading = true; root.error = ""; root.msg = ""
        root.api("GET", null, function (r) {
            root.loading = false
            if (!r.ok) { root.error = r.error || qsTr("Could not read the ELEMNT's pages."); return }
            root.take(r)
        })
    }

    function mutate(fn) {
        const next = JSON.parse(JSON.stringify(root.pages))
        fn(next)
        root.pages = next
    }

    function save() {
        root.saving = true; root.msg = ""
        const body = { via: root.via,
                       pages: root.pages.map(p => p.isNew ? { new: true, fields: p.fields, enabled: p.enabled, name: p.name }
                                                          : { id: p.id, fields: p.fields, enabled: p.enabled, name: p.name }) }
        root.api("POST", body, function (r) {
            root.saving = false
            if (r.ok) {
                root.take(r)
                root.msg = qsTr("Saved to the ELEMNT over %1 ✓ — read back and checked.").arg(root.viaLabel(r.via))
            } else {
                root.msg = r.error || qsTr("Couldn't change the ELEMNT's pages.")
            }
        })
    }

    Row {
        spacing: Theme.spacingSmall
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("Data pages")
            color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("Link:")
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
        }
        RoundedComboBox {
            width: 150
            enabled: !root.saving && !root.loading
            model: root.viaOptions.map(v => root.viaLabel(v))
            currentIndex: root.viaOptions.indexOf(root.via)
            onActivated: (i) => { root.via = root.viaOptions[i]; if (!root.changed) root.load() }
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: root.lastVia.length > 0 && !root.loading
            text: qsTr("(read over %1)").arg(root.viaLabel(root.lastVia))
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
        }
    }
    Text {
        visible: root.loading
        text: root.via === "ble" ? qsTr("Reading the ELEMNT over Bluetooth…") : qsTr("Reading the ELEMNT…")
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
        spacing: Theme.spacingMedium

        Text {
            width: parent.width; wrapMode: Text.WordWrap
            text: qsTr("Pages in the order the ELEMNT shows them. Built-in pages keep their number of fields; Workout, Lap and custom pages can have 1–%1.").arg(root.maxFields)
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
        }

        Repeater {
            model: root.pages
            delegate: Rectangle {
                id: card
                required property var modelData
                required property int index
                width: parent.width
                height: pageRow.implicitHeight + Theme.spacingMedium * 2
                radius: Theme.radiusSmall
                color: Theme.cardNested
                border.width: 1; border.color: Theme.border
                opacity: card.modelData.enabled ? 1 : 0.6

                Row {
                    id: pageRow
                    anchors.fill: parent
                    anchors.margins: Theme.spacingMedium
                    spacing: Theme.spacingMedium

                    // The ELEMNT stacks fields top to bottom, first one biggest.
                    Rectangle {
                        width: 72; height: 120
                        radius: 6; color: Theme.card
                        border.width: 1; border.color: Theme.border
                        Column {
                            anchors.fill: parent; anchors.margins: 3
                            Repeater {
                                model: card.modelData.fields
                                delegate: Rectangle {
                                    required property var modelData
                                    required property int index
                                    width: parent.width
                                    height: parent.height / Math.max(1, card.modelData.fields.length)
                                    color: "transparent"
                                    border.width: 1; border.color: Theme.border
                                    Text {
                                        anchors.fill: parent; anchors.margins: 1
                                        text: root.nameById[modelData] || String(modelData)
                                        elide: Text.ElideRight
                                        horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                                        color: Theme.text; font.pixelSize: index === 0 ? 9 : 8
                                    }
                                }
                            }
                        }
                    }

                    Column {
                        width: parent.width - 72 - Theme.spacingMedium
                        spacing: Theme.spacingSmall

                        Row {
                            spacing: Theme.spacingSmall
                            // The page's name on the ELEMNT; empty = Wahoo's default for the type.
                            RoundedTextField {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 200
                                text: card.modelData.name
                                placeholderText: card.modelData.custom ? qsTr("Custom page") : card.modelData.typeName
                                maximumLength: 30
                                enabled: !root.saving
                                onEditingFinished: if (text !== card.modelData.name)
                                                       root.mutate(p => { p[card.index].name = text.trim() })
                            }
                            RoundedSwitch {
                                anchors.verticalCenter: parent.verticalCenter
                                checked: card.modelData.enabled
                                enabled: !root.saving
                                onToggled: root.mutate(p => { p[card.index].enabled = checked })
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: card.modelData.enabled ? qsTr("shown") : qsTr("hidden")
                                color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                            }
                            RoundedButton {
                                text: "↑"; visible: card.index > 0; enabled: !root.saving
                                onClicked: root.mutate(p => { const t = p[card.index]; p[card.index] = p[card.index - 1]; p[card.index - 1] = t })
                            }
                            RoundedButton {
                                text: "↓"; visible: card.index < root.pages.length - 1; enabled: !root.saving
                                onClicked: root.mutate(p => { const t = p[card.index]; p[card.index] = p[card.index + 1]; p[card.index + 1] = t })
                            }
                            RoundedButton {
                                text: qsTr("Delete page"); visible: card.modelData.custom; enabled: !root.saving
                                onClicked: root.mutate(p => p.splice(card.index, 1))
                            }
                        }

                        Flow {
                            width: parent.width
                            spacing: Theme.spacingSmall
                            // One numbered row per field, in the ELEMNT's top-to-bottom order.
                            // Buttons that don't apply are dimmed, not hidden, so rows stay aligned.
                            Repeater {
                                model: card.modelData.fields
                                delegate: Row {
                                    id: cell
                                    required property var modelData
                                    required property int index
                                    readonly property int count: card.modelData.fields.length
                                    width: 380
                                    spacing: 4
                                    Text {
                                        width: 22
                                        anchors.verticalCenter: parent.verticalCenter
                                        horizontalAlignment: Text.AlignRight
                                        text: (cell.index + 1) + "."
                                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                    }
                                    RoundedComboBox {
                                        width: 210
                                        model: root.fieldNames
                                        currentIndex: root.fieldIds.indexOf(cell.modelData)
                                        displayText: root.nameById[cell.modelData] || String(cell.modelData)
                                        enabled: !root.saving
                                        onActivated: (i) => root.mutate(p => { p[card.index].fields[cell.index] = root.fieldIds[i] })
                                    }
                                    RoundedButton {
                                        text: "↑"
                                        enabled: !root.saving && cell.index > 0
                                        opacity: cell.index > 0 ? 1 : 0.3
                                        onClicked: root.mutate(p => { const f = p[card.index].fields; const t = f[cell.index]; f[cell.index] = f[cell.index - 1]; f[cell.index - 1] = t })
                                    }
                                    RoundedButton {
                                        text: "↓"
                                        enabled: !root.saving && cell.index < cell.count - 1
                                        opacity: cell.index < cell.count - 1 ? 1 : 0.3
                                        onClicked: root.mutate(p => { const f = p[card.index].fields; const t = f[cell.index]; f[cell.index] = f[cell.index + 1]; f[cell.index + 1] = t })
                                    }
                                    RoundedButton {
                                        text: "✕"
                                        visible: card.modelData.resizable
                                        enabled: !root.saving && cell.count > 1
                                        opacity: cell.count > 1 ? 1 : 0.3
                                        onClicked: root.mutate(p => p[card.index].fields.splice(cell.index, 1))
                                    }
                                }
                            }
                            RoundedButton {
                                text: qsTr("+ Field")
                                visible: card.modelData.resizable && card.modelData.fields.length < root.maxFields
                                enabled: !root.saving
                                onClicked: root.mutate(p => p[card.index].fields.push(201))
                            }
                        }
                    }
                }
            }
        }

        Row {
            spacing: Theme.spacingSmall
            RoundedButton {
                text: qsTr("+ Add custom page")
                enabled: !root.saving
                onClicked: root.mutate(p => p.push({ id: -1, typeName: "Custom", custom: true, resizable: true,
                                                     enabled: true, isNew: true, name: "", fields: [201, 70, 60] }))
            }
            RoundedButton {
                visible: !root.confirmReset
                text: qsTr("Reset all pages to Wahoo default…")
                enabled: !root.saving && !root.changed
                onClicked: root.confirmReset = true
            }
            Text {
                visible: root.confirmReset
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("Custom pages will be deleted. Reset?")
                color: Theme.error; font.pixelSize: Theme.fontSizeCaption
            }
            RoundedButton {
                visible: root.confirmReset
                text: qsTr("Yes, reset"); onClicked: root.resetPages()
            }
            RoundedButton {
                visible: root.confirmReset
                text: qsTr("Cancel"); onClicked: root.confirmReset = false
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
        visible: !root.loading && root.error.length === 0
        RoundedButton {
            text: root.saving ? qsTr("Saving…") : qsTr("Save to ELEMNT")
            enabled: root.changed && !root.saving
            onClicked: root.save()
        }
        RoundedButton {
            visible: root.changed
            text: qsTr("Undo changes"); enabled: !root.saving
            onClicked: root.pages = JSON.parse(root.original)
        }
        RoundedButton {
            visible: !root.changed
            text: qsTr("Re-read"); enabled: !root.saving
            onClicked: root.load()
        }
    }
    RoundedButton {
        visible: root.error.length > 0
        text: qsTr("Retry")
        onClicked: root.load()
    }
}
