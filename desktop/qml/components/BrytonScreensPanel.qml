import QtQuick
import QtQuick.Controls
import AmbitApp

// Bryton Aero 60 data screens - System/Grid.ini via tools/bryton_grid.py (/api/bryton/grid): per
// screen, shown or hidden, how many fields and which field sits in each cell, with a preview in
// the Aero 60's own grid geometry (Rider 450 table). Screens the device always shows (Screen 1,
// Follow Track, Altitude) can't be hidden. Changes take effect once the Bryton is unplugged.
// A panel on the GPS settings page (André, 2026-09-25: no Home buttons, like Watch settings).
Column {
    id: root
    spacing: Theme.spacingMedium

    Component.onCompleted: root.load()

    property bool loading: true
    property bool saving: false
    property string error: ""
    property string msg: ""
    property var pages: []
    property string original: ""
    property var fieldIds: []
    property var fieldNames: []
    property var nameById: ({})
    property var gridTable: ({})
    readonly property bool changed: JSON.stringify(root.pages) !== root.original

    function api(method, body, done) {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            let r = {}
            try { r = JSON.parse(xhr.responseText) } catch (e) { r = { ok: false } }
            done(r)
        }
        xhr.open(method, "http://127.0.0.1:8766/api/bryton/grid")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(body ? JSON.stringify(body) : null)
    }

    function take(r) {
        const ids = [], names = [], byId = {}
        for (const g of (r.groups || []))
            for (const f of g.fields) { ids.push(f.id); names.push(g.group + " · " + f.name); byId[f.id] = f.name }
        root.fieldIds = ids; root.fieldNames = names; root.nameById = byId
        root.gridTable = r.gridTable || {}
        root.pages = (r.pages || []).map(p => ({ page: p.page, title: p.title, fixed: p.fixed, enabled: p.enabled,
                                                  count: p.count, sizes: p.sizes, layouts: p.layouts }))
        root.original = JSON.stringify(root.pages)
    }

    function load() {
        root.loading = true; root.error = ""; root.msg = ""
        root.api("GET", null, function (r) {
            root.loading = false
            if (!r.ok) { root.error = r.error || qsTr("Could not read the Bryton screens."); return }
            root.take(r)
        })
    }

    function mutate(fn) {
        const next = JSON.parse(JSON.stringify(root.pages))
        fn(next)
        root.pages = next
    }

    function save() {
        const before = JSON.parse(root.original)
        const changes = []
        root.pages.forEach((p, i) => {
            const b = before[i]
            const ch = { page: p.page }
            if (p.enabled !== b.enabled) ch.enabled = p.enabled
            if (p.count !== b.count || JSON.stringify(p.layouts[p.count]) !== JSON.stringify(b.layouts[p.count])) {
                ch.count = p.count; ch.fields = p.layouts[p.count]
            }
            if (Object.keys(ch).length > 1) changes.push(ch)
        })
        root.saving = true; root.msg = ""
        root.api("POST", { pages: changes }, function (r) {
            root.saving = false
            if (r.ok) { root.take(r); root.msg = qsTr("Saved to the Bryton ✓ — unplug it to see the new screens.") }
            else root.msg = r.error || qsTr("Couldn't write Grid.ini.")
        })
    }

        Text {
            text: qsTr("Data screens")
            color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
        }

        Text {
            visible: root.loading
            text: qsTr("Reading the Bryton…")
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
        }
        Text {
            visible: root.error.length > 0
            width: parent.width; wrapMode: Text.WordWrap
            text: root.error; color: Theme.error; font.pixelSize: Theme.fontSizeBody
        }

        Item {
            visible: !root.loading && root.error.length === 0
            width: parent.width
            height: screensColumn.implicitHeight

            Column {
                id: screensColumn
                width: parent.width
                spacing: Theme.spacingMedium

                Repeater {
                    model: root.pages
                    delegate: Rectangle {
                        id: card
                        required property var modelData
                        required property int index
                        readonly property var cells: card.modelData.layouts[card.modelData.count] || []
                        readonly property var geometry: root.gridTable[card.modelData.count] || []
                        width: screensColumn.width
                        height: cardRow.implicitHeight + Theme.spacingMedium * 2
                        radius: Theme.radiusSmall
                        color: Theme.cardNested
                        border.width: 1; border.color: Theme.border
                        opacity: card.modelData.enabled === 0 ? 0.6 : 1

                        Row {
                            id: cardRow
                            anchors.fill: parent
                            anchors.margins: Theme.spacingMedium
                            spacing: Theme.spacingMedium

                            // Proportional preview of the screen (portrait head unit).
                            Rectangle {
                                width: 120; height: 160
                                radius: 6; color: Theme.card
                                border.width: 1; border.color: Theme.border
                                Flow {
                                    anchors.fill: parent; anchors.margins: 3
                                    Repeater {
                                        model: card.cells
                                        delegate: Rectangle {
                                            required property var modelData
                                            required property int index
                                            readonly property var g: card.geometry[index] || [100, 25]
                                            width: (parent.width) * g[0] / 100
                                            height: (parent.height) * g[1] / 100
                                            color: "transparent"
                                            border.width: 1; border.color: Theme.border
                                            Text {
                                                anchors.fill: parent; anchors.margins: 2
                                                text: root.nameById[modelData] || String(modelData)
                                                wrapMode: Text.WordWrap; elide: Text.ElideRight
                                                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                                                color: Theme.text; font.pixelSize: 9
                                            }
                                        }
                                    }
                                }
                            }

                            Column {
                                width: parent.width - 120 - Theme.spacingMedium
                                spacing: Theme.spacingSmall

                                Row {
                                    spacing: Theme.spacingSmall
                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: card.modelData.title
                                        color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
                                    }
                                    RoundedSwitch {
                                        anchors.verticalCenter: parent.verticalCenter
                                        visible: !card.modelData.fixed
                                        checked: card.modelData.enabled > 0
                                        onToggled: root.mutate(p => { p[card.index].enabled = checked ? 1 : 0 })
                                    }
                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: card.modelData.fixed ? qsTr("always shown") : (card.modelData.enabled ? qsTr("shown") : qsTr("hidden"))
                                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                    }
                                    RoundedComboBox {
                                        visible: card.modelData.sizes.length > 1
                                        width: 120
                                        model: card.modelData.sizes.map(n => qsTr("%1 fields").arg(n))
                                        currentIndex: card.modelData.sizes.indexOf(card.modelData.count)
                                        onActivated: (i) => root.mutate(p => { p[card.index].count = card.modelData.sizes[i] })
                                    }
                                }

                                Flow {
                                    width: parent.width
                                    spacing: Theme.spacingSmall
                                    Repeater {
                                        model: card.cells
                                        delegate: RoundedComboBox {
                                            id: cellBox
                                            required property var modelData
                                            required property int index
                                            width: 190
                                            model: root.fieldNames
                                            currentIndex: root.fieldIds.indexOf(cellBox.modelData)
                                            onActivated: (i) => root.mutate(p => { p[card.index].layouts[card.modelData.count][cellBox.index] = root.fieldIds[i] })
                                        }
                                    }
                                }
                            }
                        }
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
            visible: !root.loading && root.error.length === 0
            RoundedButton {
                text: root.saving ? qsTr("Saving…") : qsTr("Save to Bryton")
                enabled: root.changed && !root.saving
                onClicked: root.save()
            }
            RoundedButton {
                visible: root.changed
                text: qsTr("Undo changes"); enabled: !root.saving
                onClicked: root.pages = JSON.parse(root.original)
            }
        }
        RoundedButton {
            visible: root.error.length > 0
            text: qsTr("Retry")
            onClicked: root.load()
        }
}
