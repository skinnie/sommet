import QtQuick
import QtQuick.Controls
import QtCore
import AmbitApp

// The Routes page's library menu (André, 2026-09-26: "for library I thought it could be a menu.
// these are old devices, they won't have a gazillion routes"). A drop-down under the route picker:
//   Saved             - routes imported / planned here (tools/route_library.py)
//   On the watch      - the Suunto's own route list (RouteService, read over the cable)
//   On the eTrex      - GPX files on the Garmin (GarminService)
//   On the Bryton     - its Follow Track list (tools/bryton_tracks.py, over USB)
//   On the Magene     - the one route it holds: the last one sent from Sommet
// Click a row to open it in the planner (openRequested). The row's ⋯ holds what that source
// supports - Rename / Delete on saved + Bryton routes, Export GPX on device routes.
Popup {
    id: root

    signal openRequested(string name, string gpx)
    signal exportRequested(string name, string gpx)

    // Same look as every ThemedMenu (border, radius, padding, 34 px rows, primary-tint highlight).
    width: 400
    padding: 4
    modal: false
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    background: Rectangle {
        radius: Theme.radiusCard
        color: Theme.card
        border.width: 1
        border.color: Theme.mutedText
    }

    // The Magene's current route: set by the Routes page when it sends one (the C406 keeps one).
    Settings { id: mageneRoute; category: "mageneRoute"; property string name: ""; property string libraryId: "" }
    function noteMageneRoute(name, libraryId) { mageneRoute.name = name; mageneRoute.libraryId = libraryId || "" }

    property var saved: []
    property var bryton: null         // null = no Bryton mounted
    property string busyRow: ""
    property string msg: ""
    property string editKey: ""       // row being renamed
    property string confirmKey: ""    // row asking "Delete?"
    readonly property bool watchHere: HomeViewModel.anyDevice && !HomeViewModel.isGarmin
                                      && DeviceCapabilities.supportsRoutes
    readonly property bool etrexHere: HomeViewModel.isGarmin
    // Ambit1/2: routes readable (openambit), not writable, and without a track - listed read-only.
    readonly property bool legacyWatch: HomeViewModel.connected && !HomeViewModel.isGarmin
                                        && !DeviceCapabilities.supportsRoutes
    property var legacyRoutes: []

    function api(method, path, body, cb) {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            let r = null
            try { r = JSON.parse(xhr.responseText) } catch (e) { r = null }
            cb(r || { ok: false })
        }
        xhr.open(method, "http://127.0.0.1:8766" + path)
        if (body) xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(body ? JSON.stringify(body) : null)
    }

    function refresh() {
        root.msg = ""; root.editKey = ""; root.confirmKey = ""
        root.api("GET", "/api/library", null, r => root.saved = r.ok ? r.routes : [])
        root.api("GET", "/api/bryton/tracks", null, r => root.bryton = r.ok ? r.routes : null)
        if (root.watchHere && RouteService.onWatchRoutes.length === 0 && !RouteService.loading)
            RouteService.refresh()
        if (root.etrexHere) GarminService.refreshDeviceGpx()
        if (root.legacyWatch)
            root.api("GET", "/api/legacy/settings", null, r => root.legacyRoutes = r.ok ? (r.routes || []) : [])
    }
    onOpened: refresh()

    function km(m) { return (m === undefined || m === null) ? "" : (m / 1000).toFixed(1) + " km" }

    // ---- open / export per source -----------------------------------------------------------
    property int pendingWatchIndex: -1
    property string pendingWatchName: ""
    property bool pendingWatchExport: false
    Connections {
        target: RouteService
        function onExportedGpxChanged() {
            if (root.pendingWatchIndex < 0) return
            const gpx = RouteService.exportedGpx
            root.busyRow = ""
            if (gpx && gpx.length > 0) {
                if (root.pendingWatchExport) root.exportRequested(root.pendingWatchName, gpx)
                else { root.openRequested(root.pendingWatchName, gpx); root.close() }
            } else {
                root.msg = RouteService.exportError || qsTr("Couldn't read that route from the watch.")
            }
            root.pendingWatchIndex = -1
        }
    }
    function openSaved(r, exportIt) {
        root.busyRow = "s:" + r.id
        root.api("POST", "/api/library/get", { id: r.id }, res => {
            root.busyRow = ""
            if (!res.ok) { root.msg = res.error || qsTr("Couldn't open it."); return }
            if (exportIt) root.exportRequested(res.name, res.gpx)
            else { root.openRequested(res.name, res.gpx); root.close() }
        })
    }
    function openWatch(i, name, exportIt) {
        root.busyRow = "w:" + i
        root.pendingWatchIndex = i; root.pendingWatchName = name; root.pendingWatchExport = !!exportIt
        RouteService.exportRoute(i)
    }
    function openBryton(name, exportIt) {
        root.busyRow = "b:" + name
        root.api("POST", "/api/bryton/tracks/gpx", { name: name }, res => {
            root.busyRow = ""
            if (!res.ok) { root.msg = res.error || qsTr("Couldn't read it from the Bryton."); return }
            if (exportIt) root.exportRequested(name, res.gpx)
            else { root.openRequested(name, res.gpx); root.close() }
        })
    }

    // ---- rename / delete (saved + Bryton) ---------------------------------------------------
    function renameRow(key, newName) {
        root.editKey = ""
        if (!newName || !newName.trim()) return
        if (key.indexOf("s:") === 0)
            root.api("POST", "/api/library/rename", { id: key.slice(2), name: newName.trim() }, r => r.ok ? root.refresh() : root.msg = r.error)
        else if (key.indexOf("b:") === 0)
            root.api("POST", "/api/bryton/tracks/rename", { name: key.slice(2), newName: newName.trim() }, r => r.ok ? root.refresh() : root.msg = r.error)
    }
    function deleteRow(key) {
        root.confirmKey = ""
        if (key.indexOf("s:") === 0)
            root.api("POST", "/api/library/delete", { id: key.slice(2) }, r => r.ok ? root.refresh() : root.msg = r.error)
        else if (key.indexOf("b:") === 0)
            root.api("POST", "/api/bryton/tracks/delete", { name: key.slice(2) }, r => r.ok ? root.refresh() : root.msg = r.error)
    }

    // Row model for the whole menu: section headers + rows, so one Repeater draws it.
    readonly property var rows: {
        const out = []
        out.push({ header: qsTr("Saved") })
        if (root.saved.length === 0) out.push({ note: qsTr("Nothing saved yet — Import GPX adds it here.") })
        for (const r of root.saved)
            out.push({ key: "s:" + r.id, name: r.name, sub: km(r.distanceMeters), src: "saved", r: r, canEdit: true })
        if (root.watchHere) {
            out.push({ header: qsTr("On the watch") })
            const w = RouteService.onWatchRoutes
            if (RouteService.loading) out.push({ note: qsTr("Reading the watch…") })
            else if (w.length === 0) out.push({ note: qsTr("No routes on the watch.") })
            for (let i = 0; i < w.length; i++)
                out.push({ key: "w:" + i, name: w[i].name, sub: km(w[i].distanceMeters), src: "watch", index: i })
        }
        if (root.legacyWatch) {
            out.push({ header: qsTr("On the watch (read-only)") })
            if (root.legacyRoutes.length === 0) out.push({ note: qsTr("No routes on the watch.") })
            for (const lr of root.legacyRoutes)
                out.push({ note: lr.name + " · " + km(lr.distance_m) })
        }
        if (root.etrexHere) {
            out.push({ header: qsTr("On the eTrex") })
            const g = GarminService.onDeviceRoutes
            if (g.length === 0) out.push({ note: GarminService.deviceGpxLoading ? qsTr("Reading the eTrex…") : qsTr("No routes on the eTrex.") })
            for (let i = 0; i < g.length; i++)
                out.push({ key: "g:" + i, name: g[i].name, sub: km(g[i].distanceMeters), src: "etrex", gpx: g[i].gpxText })
        }
        if (root.bryton !== null) {
            out.push({ header: qsTr("On the Bryton (Follow Track)") })
            if (root.bryton.length === 0) out.push({ note: qsTr("No routes on the Bryton.") })
            for (const b of root.bryton)
                out.push({ key: "b:" + b.name, name: b.name, sub: km(b.distanceMeters), src: "bryton", canEdit: true })
        }
        if (BikeDevices.magene !== null) {
            out.push({ header: qsTr("On the Magene (current route)") })
            out.push(mageneRoute.name.length > 0
                     ? { key: "m:", name: mageneRoute.name, sub: qsTr("sent from Sommet"), src: "magene",
                         libraryId: mageneRoute.libraryId }
                     : { note: qsTr("It holds one route — Send to Magene replaces it.") })
        }
        return out
    }

    contentItem: Column {
        spacing: 2

        ScrollView {
            width: parent.width
            height: Math.min(460, list.implicitHeight)
            clip: true
            Column {
                id: list
                width: root.width - root.padding * 2
                spacing: 0
                Repeater {
                    model: root.rows
                    delegate: Item {
                        id: row
                        required property var modelData
                        width: list.width
                        height: modelData.header ? 28 : modelData.note ? 26 : 40
                        readonly property bool editing: root.editKey === modelData.key
                        readonly property bool confirming: root.confirmKey === modelData.key

                        Text {   // section header
                            visible: !!row.modelData.header
                            anchors.bottom: parent.bottom; anchors.bottomMargin: 4
                            x: Theme.spacingSmall
                            text: row.modelData.header || ""
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; font.bold: true
                        }
                        Text {   // empty-section note
                            visible: !!row.modelData.note
                            anchors.verticalCenter: parent.verticalCenter
                            x: Theme.spacingMedium
                            text: row.modelData.note || ""
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }
                        Rectangle {   // a route row
                            visible: !row.modelData.header && !row.modelData.note
                            anchors.fill: parent; anchors.margins: 2
                            radius: Theme.radiusSmall
                            color: rowHover.hovered ? Theme.primary + "26" : "transparent"
                            HoverHandler { id: rowHover }
                            TapHandler {
                                enabled: !row.editing && !row.confirming && root.busyRow === ""
                                onTapped: {
                                    const m = row.modelData
                                    if (m.src === "saved") root.openSaved(m.r, false)
                                    else if (m.src === "watch") root.openWatch(m.index, m.name, false)
                                    else if (m.src === "etrex") { root.openRequested(m.name, m.gpx); root.close() }
                                    else if (m.src === "bryton") root.openBryton(m.name, false)
                                    else if (m.src === "magene" && m.libraryId) root.openSaved({ id: m.libraryId }, false)
                                }
                            }
                            Column {
                                visible: !row.editing
                                anchors.verticalCenter: parent.verticalCenter
                                x: Theme.spacingMedium
                                width: parent.width - 70
                                Text { width: parent.width; text: row.modelData.name || ""; elide: Text.ElideRight
                                       color: Theme.text; font.pixelSize: Theme.fontSizeBody }
                                Text { text: root.busyRow === row.modelData.key ? qsTr("Opening…") : (row.modelData.sub || "")
                                       color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            }
                            RoundedTextField {
                                id: renameField
                                visible: row.editing
                                anchors.verticalCenter: parent.verticalCenter
                                x: Theme.spacingSmall; width: parent.width - 110
                                text: row.modelData.name || ""
                                onAccepted: root.renameRow(row.modelData.key, text)
                            }
                            Row {
                                anchors.right: parent.right; anchors.rightMargin: Theme.spacingSmall
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 4
                                // rename confirm / cancel
                                RoundedButton { visible: row.editing; text: qsTr("OK")
                                                onClicked: root.renameRow(row.modelData.key, renameField.text) }
                                RoundedButton { visible: row.editing; text: "✕"; onClicked: root.editKey = "" }
                                // delete confirm
                                Text { visible: row.confirming; text: qsTr("Delete?"); color: Theme.error
                                       anchors.verticalCenter: parent.verticalCenter; font.pixelSize: Theme.fontSizeCaption }
                                RoundedButton { visible: row.confirming; text: qsTr("Yes"); onClicked: root.deleteRow(row.modelData.key) }
                                RoundedButton { visible: row.confirming; text: qsTr("No"); onClicked: root.confirmKey = "" }
                                // ⋯ actions
                                RoundedButton {
                                    visible: !row.editing && !row.confirming && !row.modelData.header && !row.modelData.note
                                             && row.modelData.src !== "magene"
                                    text: "⋯"
                                    onClicked: { rowMenu.m = row.modelData; rowMenu.popup() }
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
            text: root.msg; color: Theme.error; font.pixelSize: Theme.fontSizeCaption
        }
    }

    ThemedMenu {
        id: rowMenu
        property var m: ({})
        ThemedMenuItem { text: qsTr("Open"); onTriggered: {
            const m = rowMenu.m
            if (m.src === "saved") root.openSaved(m.r, false)
            else if (m.src === "watch") root.openWatch(m.index, m.name, false)
            else if (m.src === "etrex") { root.openRequested(m.name, m.gpx); root.close() }
            else if (m.src === "bryton") root.openBryton(m.name, false)
        } }
        ThemedMenuItem { text: qsTr("Export GPX…"); onTriggered: {
            const m = rowMenu.m
            if (m.src === "saved") root.openSaved(m.r, true)
            else if (m.src === "watch") root.openWatch(m.index, m.name, true)
            else if (m.src === "etrex") root.exportRequested(m.name, m.gpx)
            else if (m.src === "bryton") root.openBryton(m.name, true)
        } }
        ThemedMenuItem { text: qsTr("Rename"); visible: !!rowMenu.m.canEdit; onTriggered: root.editKey = rowMenu.m.key }
        ThemedMenuItem { text: qsTr("Delete"); visible: !!rowMenu.m.canEdit; onTriggered: root.confirmKey = rowMenu.m.key }
    }
}
