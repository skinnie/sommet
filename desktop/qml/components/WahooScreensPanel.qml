import QtQuick
import QtQuick.Controls
import AmbitApp

// Wahoo ELEMNT data pages over the USB cable (tools/wahoo_pages.py, /api/wahoo/pages). The
// built-in pages (Workout, Lap, Elevation, Map, ...) are listed read-only: over adb the ELEMNT
// only lets us add a custom page at the end or remove the last one, so changing a built-in
// page needs Bluetooth. Custom pages can be added, edited, reordered and deleted freely - the
// tool pops them off the end and re-adds them in order. Changes show on the ELEMNT right away.
// A panel on the GPS settings page, like the Bryton/Magene screens (André, 2026-10-02: "2nd step
// change screens, I prefer cable than bluetooth").
Column {
    id: root
    spacing: Theme.spacingMedium

    Component.onCompleted: root.load()

    property bool loading: true
    property bool saving: false
    property string error: ""
    property string msg: ""
    property var builtins: []          // [{typeName, fields:[{id,name}]}]
    property var custom: []            // [[field id, ...], ...] - the editable part
    property string original: "[]"
    property int maxFields: 11
    property var fieldIds: []
    property var fieldNames: []
    property var nameById: ({})
    readonly property bool changed: JSON.stringify(root.custom) !== root.original

    function api(method, body, done) {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            let r = {}
            try { r = JSON.parse(xhr.responseText) } catch (e) { r = { ok: false } }
            done(r)
        }
        xhr.open(method, "http://127.0.0.1:8766/api/wahoo/pages")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(body ? JSON.stringify(body) : null)
    }

    function take(r) {
        const ids = [], names = [], byId = {}
        for (const g of (r.groups || []))
            for (const f of g.fields) { ids.push(f.id); names.push(g.group + " · " + f.name); byId[f.id] = f.name }
        root.fieldIds = ids; root.fieldNames = names; root.nameById = byId
        root.maxFields = r.maxFields || 11
        const pages = r.pages || []
        root.builtins = pages.filter(p => !p.custom)
        root.custom = pages.filter(p => p.custom).map(p => p.fields.map(f => f.id))
        root.original = JSON.stringify(root.custom)
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
        const next = JSON.parse(JSON.stringify(root.custom))
        fn(next)
        root.custom = next
    }

    function save() {
        root.saving = true; root.msg = ""
        root.api("POST", { custom: root.custom }, function (r) {
            root.saving = false
            if (r.ok) { root.take(r); root.msg = qsTr("Saved to the ELEMNT ✓ — the pages are live on the device.") }
            else root.msg = r.error || qsTr("Couldn't change the ELEMNT's pages.")
        })
    }

    Text {
        text: qsTr("Data pages")
        color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
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

    Column {
        visible: !root.loading && root.error.length === 0
        width: parent.width
        spacing: Theme.spacingMedium

        // ---- Built-in pages: read-only over the cable ----
        Text {
            width: parent.width; wrapMode: Text.WordWrap
            text: qsTr("Built-in pages — shown as they are; changing these needs Bluetooth.")
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
        }
        Repeater {
            model: root.builtins
            delegate: Rectangle {
                required property var modelData
                width: parent.width
                height: builtinCol.implicitHeight + Theme.spacingSmall * 2
                radius: Theme.radiusSmall
                color: Theme.cardNested
                border.width: 1; border.color: Theme.border
                Column {
                    id: builtinCol
                    anchors.fill: parent; anchors.margins: Theme.spacingSmall
                    spacing: 2
                    Text {
                        text: modelData.typeName
                        color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
                    }
                    Text {
                        width: parent.width; wrapMode: Text.WordWrap
                        text: modelData.fields.map(f => f.name).join(" · ")
                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                    }
                }
            }
        }

        // ---- Custom pages: editable ----
        Text {
            width: parent.width; wrapMode: Text.WordWrap
            text: root.custom.length === 0
                  ? qsTr("No custom pages yet — add one with the fields you want.")
                  : qsTr("Custom pages — they come after the built-in pages on the ELEMNT.")
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
        }
        Repeater {
            model: root.custom
            delegate: Rectangle {
                id: card
                required property var modelData
                required property int index
                width: parent.width
                height: customRow.implicitHeight + Theme.spacingMedium * 2
                radius: Theme.radiusSmall
                color: Theme.cardNested
                border.width: 1; border.color: Theme.border

                Row {
                    id: customRow
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
                                model: card.modelData
                                delegate: Rectangle {
                                    required property var modelData
                                    required property int index
                                    width: parent.width
                                    height: (parent.height) / Math.max(1, card.modelData.length)
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
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("Custom page %1").arg(card.index + 1)
                                color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
                            }
                            RoundedButton {
                                text: "↑"; visible: card.index > 0; enabled: !root.saving
                                onClicked: root.mutate(c => { const t = c[card.index]; c[card.index] = c[card.index - 1]; c[card.index - 1] = t })
                            }
                            RoundedButton {
                                text: "↓"; visible: card.index < root.custom.length - 1; enabled: !root.saving
                                onClicked: root.mutate(c => { const t = c[card.index]; c[card.index] = c[card.index + 1]; c[card.index + 1] = t })
                            }
                            RoundedButton {
                                text: qsTr("Delete page"); enabled: !root.saving
                                onClicked: root.mutate(c => c.splice(card.index, 1))
                            }
                        }

                        Flow {
                            width: parent.width
                            spacing: Theme.spacingSmall
                            Repeater {
                                model: card.modelData
                                delegate: Row {
                                    id: cell
                                    required property var modelData
                                    required property int index
                                    spacing: 2
                                    RoundedComboBox {
                                        width: 200
                                        model: root.fieldNames
                                        currentIndex: root.fieldIds.indexOf(cell.modelData)
                                        onActivated: (i) => root.mutate(c => { c[card.index][cell.index] = root.fieldIds[i] })
                                    }
                                    RoundedButton {
                                        text: "✕"
                                        visible: card.modelData.length > 1
                                        enabled: !root.saving
                                        onClicked: root.mutate(c => c[card.index].splice(cell.index, 1))
                                    }
                                }
                            }
                            RoundedButton {
                                text: qsTr("+ Field")
                                visible: card.modelData.length < root.maxFields
                                enabled: !root.saving
                                onClicked: root.mutate(c => c[card.index].push(201))
                            }
                        }
                    }
                }
            }
        }

        RoundedButton {
            text: qsTr("+ Add custom page")
            enabled: !root.saving
            onClicked: root.mutate(c => c.push([201, 70, 60]))   // Speed, Heart rate, Cadence
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
            text: root.saving ? qsTr("Saving… (a few seconds per page)") : qsTr("Save to ELEMNT")
            enabled: root.changed && !root.saving
            onClicked: root.save()
        }
        RoundedButton {
            visible: root.changed
            text: qsTr("Undo changes"); enabled: !root.saving
            onClicked: root.custom = JSON.parse(root.original)
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
