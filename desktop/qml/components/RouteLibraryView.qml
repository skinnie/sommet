import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtCore
import AmbitApp

// Routes - the Library view (André, 2026-09-26: the old Routes page back, and the Routes page
// "switches completely" between this and the planner). The old page's look - import card with the
// planner-tools "i", map-preview cards (tap = big map), map/list view, sort - now for EVERY source:
//   Saved             - every imported GPX (tools/route_library.py, same file twice = one entry)
//   On the watch      - the Suunto's own routes (RouteService); Ambit1/2 listed read-only
//   On the eTrex      - GPX files on the Garmin (GarminService)
//   On the Bryton     - its Follow Track list (tools/bryton_tracks.py)
//   On the Magene     - the one route it holds (the last sent from Sommet)
// Open (or tapping a list row) loads the route into the planner - openRequested - and the page
// switches over. ⋯ holds what the source allows: Export GPX, Rename, Delete.
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    signal openRequested(string name, string gpx, string libraryId)

    // ---- state -----------------------------------------------------------------------------
    property var saved: []
    property var bryton: null          // null = no Bryton mounted
    property var legacyRoutes: []
    property string busyKey: ""
    property string msg: ""
    property string sortKey: "name"
    readonly property bool watchHere: HomeViewModel.anyDevice && !HomeViewModel.isGarmin
                                      && DeviceCapabilities.supportsRoutes
    readonly property bool legacyWatch: HomeViewModel.connected && !HomeViewModel.isGarmin
                                        && !DeviceCapabilities.supportsRoutes
    readonly property bool etrexHere: HomeViewModel.isGarmin

    // The Magene's current route: noted by the planner when it sends one (the C406 keeps one).
    Settings { id: mageneRoute; category: "mageneRoute"; property string name: ""; property string libraryId: "" }

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
        root.msg = ""
        root.api("GET", "/api/library", null, r => root.saved = r.ok ? r.routes : [])
        root.api("GET", "/api/bryton/tracks", null, r => root.bryton = r.ok ? r.routes : null)
        if (root.watchHere && !RouteService.loading) RouteService.refresh()
        if (root.etrexHere) GarminService.refreshDeviceGpx()
        if (root.legacyWatch)
            root.api("GET", "/api/legacy/settings", null, r => root.legacyRoutes = r.ok ? (r.routes || []) : [])
    }
    Component.onCompleted: refresh()

    function sorted(list) {
        const l = (list || []).slice()
        if (root.sortKey === "distance") l.sort((a, b) => (b.distanceMeters || 0) - (a.distanceMeters || 0))
        else if (root.sortKey === "ascent") l.sort((a, b) => (b.ascentMeters || 0) - (a.ascentMeters || 0))
        else l.sort((a, b) => (a.name || "").localeCompare(b.name || ""))
        return l
    }

    // ---- sources -> one item shape: {key, src, name, distanceMeters, ascentMeters,
    //      descentMeters, pointCount, track, ...source fields} -----------------------------------
    readonly property var savedItems: root.saved.map(r => Object.assign({ key: "s:" + r.id, src: "saved" }, r))
    readonly property var watchItems: RouteService.onWatchRoutes.map((r, i) => Object.assign({ key: "w:" + i, src: "watch", index: i }, r))
    readonly property var etrexItems: GarminService.onDeviceRoutes.map((r, i) => Object.assign({ key: "g:" + i, src: "etrex" }, r))
    readonly property var brytonItems: (root.bryton || []).map(r => Object.assign({ key: "b:" + r.name, src: "bryton" }, r))
    readonly property var mageneItems: {
        if (!mageneRoute.name) return []
        const e = root.saved.find(r => r.id === mageneRoute.libraryId)
        return [Object.assign({ key: "m:", src: "magene", name: mageneRoute.name, libraryId: mageneRoute.libraryId },
                              e ? { distanceMeters: e.distanceMeters, ascentMeters: e.ascentMeters,
                                    descentMeters: e.descentMeters, pointCount: e.pointCount, track: e.track } : {})]
    }

    // ---- open / export ---------------------------------------------------------------------
    property var pendingWatch: null     // {index, name, export}
    Connections {
        target: RouteService
        function onExportedGpxChanged() {
            const p = root.pendingWatch
            if (!p) return
            root.pendingWatch = null
            root.busyKey = ""
            const gpx = RouteService.exportedGpx
            if (!gpx || gpx.length === 0) { root.msg = RouteService.exportError || qsTr("Couldn't read that route from the watch."); return }
            if (p.export) root.saveGpx(p.name, gpx)
            else root.openRequested(p.name, gpx, "")
        }
    }
    function fetchGpx(item, cb) {
        root.busyKey = item.key
        if (item.src === "saved" || (item.src === "magene" && item.libraryId)) {
            root.api("POST", "/api/library/get", { id: item.id || item.libraryId }, r => {
                root.busyKey = ""
                r.ok ? cb(r.name, r.gpx) : root.msg = r.error || qsTr("Couldn't open it.")
            })
        } else if (item.src === "bryton") {
            root.api("POST", "/api/bryton/tracks/gpx", { name: item.name }, r => {
                root.busyKey = ""
                r.ok ? cb(item.name, r.gpx) : root.msg = r.error || qsTr("Couldn't read it from the Bryton.")
            })
        } else if (item.src === "etrex") {
            root.busyKey = ""
            cb(item.name, item.gpxText)
        }
    }
    function openItem(item) {
        if (item.src === "watch") {
            root.busyKey = item.key
            root.pendingWatch = { index: item.index, name: item.name, export: false }
            RouteService.exportRoute(item.index)
            return
        }
        fetchGpx(item, (name, gpx) => root.openRequested(name, gpx,
                        item.src === "saved" ? item.id : (item.src === "magene" ? item.libraryId : "")))
    }
    function exportItem(item) {
        if (item.src === "watch") {
            root.busyKey = item.key
            root.pendingWatch = { index: item.index, name: item.name, export: true }
            RouteService.exportRoute(item.index)
            return
        }
        fetchGpx(item, (name, gpx) => root.saveGpx(name, gpx))
    }

    FileDialog {
        id: exportDialog
        title: qsTr("Export route as GPX")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("GPX files (*.gpx)")]
        currentFolder: LocalFileService.downloadsLocation
        property string gpx: ""
        onAccepted: {
            const err = LocalFileService.saveText(selectedFile, gpx)
            root.msg = err.length ? qsTr("Couldn't save: %1").arg(err) : ""
        }
    }
    function saveGpx(name, gpx) {
        exportDialog.gpx = gpx
        exportDialog.currentFile = LocalFileService.downloadsLocation + "/" + (name || "route").replace(/[\\/:*?"<>|]/g, "_") + ".gpx"
        exportDialog.open()
    }

    // ---- import (kept in Saved, then opened) ---------------------------------------------
    FileDialog {
        id: importDialog
        title: qsTr("Import GPX")
        nameFilters: [qsTr("GPX files (*.gpx)"), qsTr("All files (*)")]
        onAccepted: {
            const gpx = LocalFileService.readText(selectedFile)
            if (!gpx || gpx.length === 0) { root.msg = qsTr("Couldn't read that file"); return }
            const s = selectedFile.toString()
            const name = decodeURIComponent(s.substring(s.lastIndexOf("/") + 1)).replace(/\.gpx$/i, "")
            root.api("POST", "/api/library/save", { name: name, gpx: gpx }, r => {
                root.refresh()
                root.openRequested(name, gpx, r.ok ? r.id : "")
            })
        }
    }

    // ---- rename / delete (saved + Bryton) ------------------------------------------------
    ThemedDialog {
        id: renameDialog
        property var item: null
        title: qsTr("Rename route")
        standardButtons: Dialog.Ok | Dialog.Cancel
        width: 380
        contentItem: RoundedTextField { id: renameField; width: 340; onAccepted: renameDialog.accept() }
        onOpened: { renameField.text = item ? item.name : ""; renameField.forceActiveFocus(); renameField.selectAll() }
        onAccepted: {
            const n = renameField.text.trim()
            if (!item || !n) return
            if (item.src === "saved")
                root.api("POST", "/api/library/rename", { id: item.id, name: n }, r => r.ok ? root.refresh() : root.msg = r.error)
            else if (item.src === "bryton")
                root.api("POST", "/api/bryton/tracks/rename", { name: item.name, newName: n }, r => r.ok ? root.refresh() : root.msg = r.error)
        }
    }
    ThemedDialog {
        id: deleteDialog
        property var item: null
        title: qsTr("Delete route")
        standardButtons: Dialog.Yes | Dialog.No
        width: 380
        contentItem: Text {
            width: 340; wrapMode: Text.WordWrap
            color: Theme.text; font.pixelSize: Theme.fontSizeBody
            text: deleteDialog.item
                  ? (deleteDialog.item.src === "bryton"
                     ? qsTr("Delete “%1” from the Bryton?").arg(deleteDialog.item.name)
                     : qsTr("Delete “%1” from your saved routes?").arg(deleteDialog.item.name))
                  : ""
        }
        onAccepted: {
            if (!item) return
            if (item.src === "saved")
                root.api("POST", "/api/library/delete", { id: item.id }, r => r.ok ? root.refresh() : root.msg = r.error)
            else if (item.src === "bryton")
                root.api("POST", "/api/bryton/tracks/delete", { name: item.name }, r => r.ok ? root.refresh() : root.msg = r.error)
        }
    }

    ThemedMenu {
        id: itemMenu
        property var item: ({})
        ThemedMenuItem { text: qsTr("Open"); onTriggered: root.openItem(itemMenu.item) }
        ThemedMenuItem { text: qsTr("Export GPX…"); visible: itemMenu.item.src !== "magene" || !!itemMenu.item.libraryId
                         onTriggered: root.exportItem(itemMenu.item) }
        ThemedMenuItem { text: qsTr("Rename…"); visible: itemMenu.item.src === "saved" || itemMenu.item.src === "bryton"
                         onTriggered: { renameDialog.item = itemMenu.item; renameDialog.open() } }
        ThemedMenuItem { text: qsTr("Delete…"); visible: itemMenu.item.src === "saved" || itemMenu.item.src === "bryton"
                         onTriggered: { deleteDialog.item = itemMenu.item; deleteDialog.open() } }
    }

    // One big map for the whole page (old page, André R2): a thumbnail tap opens it.
    MapWindow { id: bigMap; anchors.centerIn: parent }
    function showBig(item) {
        if (!item.track || item.track.length <= 1) return
        bigMap.trackPoints = item.track
        bigMap.markers = []
        bigMap.trackTitle = item.name || ""
        bigMap.open()
    }

    // ---- a section of route cards (the old "On the watch" card, for any source) -------------
    component RouteSection: Card {
        id: section
        property string title: ""
        property var items: []
        property bool loading: false
        property string emptyText: qsTr("No routes.")
        width: parent ? parent.width : 0

        Column {
            width: parent.width
            spacing: Theme.spacingSmall

            Text { text: section.title; font.bold: true; color: Theme.text }
            Text { visible: section.loading; text: qsTr("Reading…"); color: Theme.mutedText }
            Text { visible: !section.loading && section.items.length === 0
                   text: section.emptyText; color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }

            Column {
                width: parent.width
                spacing: Theme.routesView === "list" ? 2 : Theme.spacingSmall
                Repeater {
                    model: section.loading ? [] : root.sorted(section.items)
                    delegate: Item {
                        id: routeDelegate
                        required property var modelData
                        readonly property var route: modelData
                        readonly property bool listMode: Theme.routesView === "list"
                        readonly property string stats: [
                            route.distanceMeters ? RouteViewModel.formatDistance(route.distanceMeters) : "",
                            route.pointCount ? qsTr("%1 points").arg(route.pointCount) : "",
                            route.ascentMeters !== undefined ? qsTr("ascent %1 m").arg(route.ascentMeters) : "",
                            route.descentMeters !== undefined ? qsTr("descent %1 m").arg(route.descentMeters) : ""
                        ].filter(x => x.length > 0).join(" · ")
                        width: parent.width
                        height: listMode ? 44 : mapCol.implicitHeight

                        // ---- map view: preview + name/stats + Open ⋯ ----
                        Column {
                            id: mapCol
                            visible: !routeDelegate.listMode
                            width: parent.width
                            spacing: Theme.spacingSmall
                            Item {
                                visible: routeDelegate.route.track && routeDelegate.route.track.length > 1
                                width: parent.width
                                height: visible ? 140 : 0
                                MapView {
                                    anchors.fill: parent
                                    readonly property var center: RouteViewModel.trackCenter(routeDelegate.route.track)
                                    latitude: center ? center.lat : 0
                                    longitude: center ? center.lon : 0
                                    trackPoints: routeDelegate.route.track || []
                                    TapHandler { onTapped: root.showBig(routeDelegate.route) }
                                }
                            }
                            Row {
                                width: parent.width
                                spacing: Theme.spacingSmall
                                Column {
                                    width: parent.width - actions.width - Theme.spacingSmall
                                    spacing: 2
                                    Text { width: parent.width; elide: Text.ElideRight; text: routeDelegate.route.name
                                           color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true }
                                    Text { width: parent.width; elide: Text.ElideRight; text: routeDelegate.stats
                                           color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                }
                                Row {
                                    id: actions
                                    spacing: Theme.spacingSmall
                                    RoundedButton {
                                        text: root.busyKey === routeDelegate.route.key ? qsTr("Opening…") : qsTr("Open")
                                        enabled: root.busyKey === "" && (routeDelegate.route.src !== "magene" || !!routeDelegate.route.libraryId)
                                        onClicked: root.openItem(routeDelegate.route)
                                    }
                                    RoundedButton {
                                        id: moreButton
                                        text: "⋯"
                                        enabled: root.busyKey === ""
                                        onClicked: { itemMenu.item = routeDelegate.route; itemMenu.popup(moreButton, 0, moreButton.height) }
                                    }
                                }
                            }
                        }

                        // ---- list view: the old compact row (tap = open, right-click / ⋯ = menu) ----
                        Rectangle {
                            visible: routeDelegate.listMode
                            anchors.fill: parent
                            radius: Theme.radiusCard
                            color: "transparent"
                            Rectangle {
                                anchors.fill: parent; radius: parent.radius; color: Theme.card
                                opacity: rowHover.hovered ? 1 : 0
                                Behavior on opacity { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
                            }
                            HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler { enabled: root.busyKey === ""; onTapped: root.openItem(routeDelegate.route) }
                            TapHandler {
                                acceptedButtons: Qt.RightButton
                                onTapped: (p) => { itemMenu.item = routeDelegate.route; itemMenu.popup(p.position.x, p.position.y) }
                            }
                            Row {
                                anchors.left: parent.left; anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: Theme.spacingMedium; anchors.rightMargin: Theme.spacingMedium
                                spacing: Theme.spacingMedium
                                Rectangle {
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 32; height: 32; radius: 16; color: Theme.cardNested
                                    Icon { anchors.centerIn: parent; glyph: Icons.routes; size: 18; color: Theme.mutedText }
                                }
                                Column {
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: parent.width - 32 - listMore.width - Theme.spacingMedium * 2
                                    spacing: 1
                                    Text { width: parent.width; elide: Text.ElideRight; text: routeDelegate.route.name
                                           color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true }
                                    Text { width: parent.width; elide: Text.ElideRight
                                           text: root.busyKey === routeDelegate.route.key ? qsTr("Opening…") : routeDelegate.stats
                                           color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                }
                                RoundedButton {
                                    id: listMore
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "⋯"
                                    onClicked: { itemMenu.item = routeDelegate.route; itemMenu.popup(listMore, 0, listMore.height) }
                                }
                            }
                            Rectangle {
                                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                                height: 1; color: Theme.mutedText; opacity: 0.15
                            }
                        }
                    }
                }
            }
        }
    }

    // ---- layout ----------------------------------------------------------------------------
    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: 520
        spacing: Theme.spacingMedium

        // Import card (the old page's, with its planner-tools "i")
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Row {
                    spacing: Theme.spacingSmall
                    Text { anchors.verticalCenter: parent.verticalCenter; text: qsTr("Import a route"); font.bold: true; color: Theme.text }
                    Rectangle {
                        id: routeInfoBadge
                        anchors.verticalCenter: parent.verticalCenter
                        width: 15; height: 15; radius: 7.5
                        color: "transparent"; border.width: 1; border.color: Theme.mutedText
                        Text { anchors.centerIn: parent; text: "i"; font.pixelSize: Theme.fontSizeLabel; font.bold: true; color: Theme.mutedText }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: routePlannerDialog.open() }
                    }
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: qsTr("It's kept in Saved and opens in the planner — weather, climbs, days — to send to any device.")
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton { text: qsTr("Import GPX…"); onClicked: importDialog.open() }
                    Text { anchors.verticalCenter: parent.verticalCenter; text: qsTr("View:"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                    ViewModeToggle { anchors.verticalCenter: parent.verticalCenter; mode: Theme.routesView; onChosen: (m) => Theme.routesView = m }
                    Text { anchors.verticalCenter: parent.verticalCenter; text: qsTr("Sort:"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                    Repeater {
                        model: [{ key: "name", label: qsTr("Name") }, { key: "distance", label: qsTr("Distance") },
                                { key: "ascent", label: qsTr("Ascent") }]
                        delegate: Text {
                            required property var modelData
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData.label
                            color: root.sortKey === modelData.key ? Theme.primary : Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                            font.bold: root.sortKey === modelData.key
                            TapHandler { onTapped: root.sortKey = modelData.key }
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                        }
                    }
                }
                Text {
                    visible: root.msg.length > 0
                    width: parent.width; wrapMode: Text.WordWrap
                    text: root.msg; color: Theme.error; font.pixelSize: Theme.fontSizeCaption
                }
            }
        }

        RouteSection { title: qsTr("Saved"); items: root.savedItems
                       emptyText: qsTr("Nothing saved yet — Import GPX keeps it here.") }
        RouteSection { visible: root.watchHere; title: qsTr("On the watch"); items: root.watchItems
                       loading: RouteService.loading; emptyText: qsTr("No routes on the watch.") }
        Card {
            // Ambit1/2: readable (openambit) but not writable and without a track - read-only.
            visible: root.legacyWatch
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text { text: qsTr("On the watch (read-only)"); font.bold: true; color: Theme.text }
                Text { visible: root.legacyRoutes.length === 0; text: qsTr("No routes on the watch."); color: Theme.mutedText }
                Repeater {
                    model: root.legacyRoutes
                    delegate: Column {
                        required property var modelData
                        spacing: 2
                        Text { color: Theme.text; font.bold: true; text: modelData.name }
                        Text { color: Theme.mutedText
                               text: qsTr("%1 points, %2 m, +%3/-%4 m").arg(modelData.points_count).arg(modelData.distance_m)
                                     .arg(modelData.altitude_asc_m).arg(modelData.altitude_dec_m) }
                    }
                }
            }
        }
        RouteSection { visible: root.etrexHere; title: qsTr("On the eTrex"); items: root.etrexItems
                       loading: GarminService.deviceGpxLoading; emptyText: qsTr("No routes on the eTrex.") }
        RouteSection { visible: root.bryton !== null; title: qsTr("On the Bryton (Follow Track)"); items: root.brytonItems
                       emptyText: qsTr("No routes on the Bryton.") }
        RouteSection { visible: BikeDevices.magene !== null; title: qsTr("On the Magene (current route)"); items: root.mageneItems
                       emptyText: qsTr("It holds one route — sending one from the planner replaces it.") }
    }

    // Route-planner help - the old page's "i" dialog, unchanged: the tools that make a GPX.
    ThemedDialog {
        id: routePlannerDialog
        parent: Overlay.overlay
        onAboutToShow: {
            const p = routeInfoBadge.mapToItem(Overlay.overlay, 0, routeInfoBadge.height + Theme.spacingSmall);
            x = p.x; y = p.y;
        }
        title: qsTr("Plan a route")
        standardButtons: Dialog.NoButton
        contentItem: Item {
            implicitWidth: plannerCol.width
            implicitHeight: plannerCol.height
            Column {
                id: plannerCol
                width: 360
                spacing: Theme.spacingSmall
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: qsTr("Routes that are sent to the watch will be erased by SuuntoLink and not pushed to the Suunto app.")
                    color: Theme.text; font.bold: true; font.pixelSize: Theme.fontSizeBody
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: qsTr("You can export your routes from Suunto App to gpx and import them here, or you can use any of these or others:")
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody
                }
                Repeater {
                    model: [
                        { name: "Suunto planner", qual: qsTr("(online)"), url: "https://routeplanner.suunto.com/" },
                        { name: "Komoot", qual: qsTr("(online)"), url: "https://www.komoot.com/" },
                        { name: "Openrunner", qual: qsTr("(online)"), url: "https://www.openrunner.com/" },
                        { name: "Garmin Basecamp", qual: qsTr("(offline, Win/Mac)"), url: "https://www.garmin.com/en-GB/software/basecamp/" },
                        { name: "Qmapshack", qual: qsTr("(offline, Linux/Win/Mac)"), url: "https://github.com/Maproom/qmapshack" },
                        { name: "Maps for Basecamp/garmin devices", qual: "", url: "http://www.frikart.no/garmin/index.html" },
                        { name: "Maps for Basecamp/Qmapshack", qual: "", url: "https://download2.bbbike.org/osm/" },
                    ]
                    delegate: Text {
                        required property var modelData
                        width: 360; wrapMode: Text.WordWrap
                        textFormat: Text.StyledText; linkColor: Theme.primary
                        color: Theme.text; font.pixelSize: Theme.fontSizeBody
                        text: "•  <a href=\"" + modelData.url + "\">" + modelData.name + "</a>"
                              + (modelData.qual.length > 0 ? " " + modelData.qual : "")
                        onLinkActivated: (link) => Qt.openUrlExternally(link)
                    }
                }
            }
            RoundedButton {
                anchors.right: plannerCol.right; anchors.bottom: plannerCol.bottom
                text: qsTr("Close"); onClicked: routePlannerDialog.close()
            }
        }
    }
}
