import QtQuick
import QtQuick.Controls
import AmbitApp

// Magene C406 data screens panel on the GPS settings page (André, 2026-09-25) - the OneLap app's page editor: which data fields
// each screen shows. Format + field catalogue: tools/magene_pages.py (decoded from the OneLap
// APK); BLE read/write: /api/magene/device read-pages / write-pages. Same limits as the app:
// 2..8 fields per screen, 1..30 screens. The backend only writes a layout it could read back in
// this format, and reports success only when the device reads back exactly what was sent.
Column {
    id: root
    spacing: Theme.spacingMedium

    property string address: ""


    property bool loading: true
    property bool saving: false
    property string error: ""
    property string msg: ""
    property var pages: []          // [[code, ...], ...]
    property string original: ""    // JSON of the pages as read, to know if anything changed
    property var fieldCodes: []     // flattened catalogue, picker order
    property var fieldNames: []     // "Group · Name", same order
    readonly property bool changed: JSON.stringify(root.pages) !== root.original

    function api(method, path, body, done) {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            let r = {}
            try { r = JSON.parse(xhr.responseText) } catch (e) { r = { ok: false } }
            done(r)
        }
        xhr.open(method, "http://127.0.0.1:8766" + path)
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(body ? JSON.stringify(body) : null)
    }

    function setPages(described) {
        root.pages = (described || []).map(p => p.map(f => f.code))
        root.original = JSON.stringify(root.pages)
    }

    // shared: the GPS settings page reads screens + settings in ONE connection ("read-config") and
    // hands the result here via `preloaded`; standalone the panel reads on its own.
    property bool shared: false
    property var preloaded: null
    onPreloadedChanged: if (preloaded && root.fieldCodes.length > 0) root.takePages(preloaded)
    function takePages(r) {
        root.loading = false
        if (!r.ok || !r.pages) { root.error = r.pagesError || r.error || qsTr("Could not read the C406 screens."); return }
        root.error = ""
        root.setPages(r.pages)
    }
    Component.onCompleted: root.load()
    function load() {
        root.loading = true; root.error = ""; root.msg = ""
        // The field catalogue is local (no Bluetooth); the screens come from the device.
        root.api("GET", "/api/magene/fields", null, function (f) {
            const codes = [], names = []
            for (const g of (f.groups || []))
                for (const x of g.fields) { codes.push(x.code); names.push(g.group === "Empty" ? x.name : g.group + " · " + x.name) }
            root.fieldCodes = codes; root.fieldNames = names
            if (root.shared) {
                if (root.preloaded) root.takePages(root.preloaded)
                return
            }
            const body = { action: "read-pages" }
            if (root.address.length > 0) body.address = root.address
            root.api("POST", "/api/magene/device", body, root.takePages)
        })
    }

    function mutate(fn) {
        const next = JSON.parse(JSON.stringify(root.pages))
        fn(next)
        root.pages = next
    }

    function save() {
        root.saving = true; root.msg = ""
        const body = { action: "write-pages", pages: root.pages }
        if (root.address.length > 0) body.address = root.address
        root.api("POST", "/api/magene/device", body, function (r) {
            root.saving = false
            if (r.ok) { root.setPages(r.pages); root.msg = qsTr("Saved to the C406 ✓") }
            else root.msg = r.error || qsTr("The C406 didn't take the new screens.")
        })
    }

    Column {
        width: parent.width
        spacing: Theme.spacingMedium

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

        Item {
            visible: !root.loading && root.error.length === 0
            width: parent.width
            height: pagesColumn.implicitHeight

            Column {
                id: pagesColumn
                width: parent.width
                spacing: Theme.spacingMedium

                Repeater {
                    model: root.pages
                    delegate: Rectangle {
                        id: pageCard
                        required property var modelData
                        required property int index
                        width: pagesColumn.width
                        height: pageInner.implicitHeight + Theme.spacingMedium * 2
                        radius: Theme.radiusSmall
                        color: Theme.cardNested
                        border.width: 1; border.color: Theme.border

                        Column {
                            id: pageInner
                            anchors.fill: parent
                            anchors.margins: Theme.spacingMedium
                            spacing: Theme.spacingSmall

                            Row {
                                spacing: Theme.spacingSmall
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: qsTr("Screen %1").arg(pageCard.index + 1)
                                    color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
                                }
                                RoundedButton {
                                    text: "▲"; enabled: pageCard.index > 0
                                    onClicked: root.mutate(p => { const x = p.splice(pageCard.index, 1)[0]; p.splice(pageCard.index - 1, 0, x) })
                                }
                                RoundedButton {
                                    text: "▼"; enabled: pageCard.index < root.pages.length - 1
                                    onClicked: root.mutate(p => { const x = p.splice(pageCard.index, 1)[0]; p.splice(pageCard.index + 1, 0, x) })
                                }
                                RoundedButton {
                                    text: qsTr("Remove screen"); enabled: root.pages.length > 1
                                    onClicked: root.mutate(p => p.splice(pageCard.index, 1))
                                }
                            }

                            Flow {
                                width: parent.width
                                spacing: Theme.spacingSmall
                                Repeater {
                                    model: pageCard.modelData
                                    delegate: RoundedComboBox {
                                        id: fieldBox
                                        required property var modelData
                                        required property int index
                                        width: 180
                                        model: root.fieldNames
                                        currentIndex: root.fieldCodes.indexOf(fieldBox.modelData)
                                        onActivated: (i) => root.mutate(p => { p[pageCard.index][fieldBox.index] = root.fieldCodes[i] })
                                    }
                                }
                            }

                            Row {
                                spacing: Theme.spacingSmall
                                RoundedButton {
                                    text: qsTr("+ Field"); enabled: pageCard.modelData.length < 8
                                    onClicked: root.mutate(p => p[pageCard.index].push(255))
                                }
                                RoundedButton {
                                    text: qsTr("− Field"); enabled: pageCard.modelData.length > 2
                                    onClicked: root.mutate(p => p[pageCard.index].pop())
                                }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: qsTr("%1 fields (2–8)").arg(pageCard.modelData.length)
                                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                }
                            }
                        }
                    }
                }
            }
        }

        RoundedButton {
            visible: !root.loading && root.error.length === 0
            text: qsTr("+ Screen"); enabled: root.pages.length < 30
            // A new screen starts like the app's: speed + moving time, then edit.
            onClicked: root.mutate(p => p.push([16, 177]))
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
                text: root.saving ? qsTr("Saving…") : qsTr("Save to C406")
                enabled: !root.loading && !root.saving && root.changed
                onClicked: root.save()
            }
            RoundedButton {
                visible: root.changed
                text: qsTr("Undo changes"); enabled: !root.saving
                onClicked: root.pages = JSON.parse(root.original)
            }
        }
    }
}
