import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtCore
import SommetUi

// Routes - the first screen (André, 2026-09-26): the old Suunto Routes page, like POIs. The top
// card imports a GPX (preview, then send it to a connected device - no planning needed) and opens
// the planner; the list card shows ONE source picked in a drop-down - the watch, the eTrex, the
// Bryton's Follow Track list, the Magene's current route, or the Library of saved routes - with
// the old map/list cards (Export, ⋯ Open in planner / Rename / Delete where the source allows).
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    signal openRequested(string name, string gpx, string libraryId)
    signal openPlanner()

    // ---- state -----------------------------------------------------------------------------
    property var saved: []
    property var bryton: null          // null = no Bryton mounted
    property var legacyRoutes: []
    property string busyKey: ""
    property string removingKey: ""     // the watch route being removed (busyKey is set too)
    property string msg: ""
    property string sortKey: "name"
    readonly property bool watchHere: HomeViewModel.connected
                                      && DeviceCapabilities.supportsRoutes
    readonly property bool legacyWatch: HomeViewModel.connected
                                        && !DeviceCapabilities.supportsRoutes
    // An eTrex PLUGGED (not only when it's the active device - the watch can be active too).
    readonly property bool etrexHere: GarminService.connected

    // The list card's source drop-down: connected devices, then the Library (always there).
    readonly property var sources: {
        const out = []
        if (root.watchHere || root.legacyWatch) out.push({ key: "watch", label: qsTr("On the watch") })
        if (root.etrexHere) out.push({ key: "etrex", label: qsTr("On the eTrex") })
        if (root.bryton !== null) out.push({ key: "bryton", label: qsTr("On the Bryton") })
        if (BikeDevices.magene !== null && BikeDevices.mageneReachable) out.push({ key: "magene", label: qsTr("On the Magene") })
        out.push({ key: "library", label: qsTr("Library (saved routes)") })
        return out
    }
    property string sourceKey: ""
    // Until one is picked, open on the active device (Home's switcher), else the first listed.
    readonly property string activeKey: HomeViewModel.isGarmin && !DeviceService.bikeActive ? "etrex"
        : DeviceService.activeBikeKind === "bryton" ? "bryton"
        : DeviceService.activeBikeKind === "c406" ? "magene" : ""
    readonly property string source: root.sources.some(x => x.key === root.sourceKey) ? root.sourceKey
        : root.sources.some(x => x.key === root.activeKey) ? root.activeKey : root.sources[0].key
    readonly property var sourceItems: root.source === "watch" ? root.watchItems
        : root.source === "etrex" ? root.etrexItems
        : root.source === "bryton" ? root.brytonItems
        : root.source === "magene" ? root.mageneItems : root.savedItems

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
        root.api("GET", "/api/edge500/info", null, r => root.edge500Here = !!r.ok)
        if (root.watchHere && !RouteService.loading) RouteService.refresh()
        if (root.etrexHere) GarminService.refreshDeviceGpx()
        if (root.legacyWatch)
            root.api("GET", "/api/legacy/settings", null, r => root.legacyRoutes = r.ok ? (r.routes || []) : [])
    }
    Component.onCompleted: refresh()
    // Re-read just the chosen source when the drop-down changes (the watch's is a USB round
    // trip, so only on an explicit pick, never on every source).
    function refreshSource(key) {
        if (key === "saved" || key === "library")
            root.api("GET", "/api/library", null, r => root.saved = r.ok ? r.routes : [])
        else if (key === "bryton")
            root.api("GET", "/api/bryton/tracks", null, r => root.bryton = r.ok ? r.routes : null)
        else if (key === "etrex")
            GarminService.refreshDeviceGpx()
    }

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
            if (!gpx || gpx.length === 0) {
                const err = RouteService.exportError || qsTr("Couldn't read that route from the watch.")
                p.fail ? p.fail(err) : root.msg = err
                return
            }
            if (p.ok) p.ok(p.name, gpx)
            else if (p.export) root.saveGpx(p.name, gpx)
            else root.openRequested(p.name, gpx, "")
        }
    }
    // The GPX of one item, from wherever it lives. fail(error) is optional; without it the error
    // shows under the list. The watch's goes through RouteService (a USB read, one at a time).
    function fetchGpx(item, cb, fail) {
        const bad = fail || (err => root.msg = err)
        root.busyKey = item.key
        if (item.src === "watch") {
            root.pendingWatch = { index: item.index, name: item.name, ok: cb, fail: bad }
            RouteService.exportRoute(item.index)
        } else if (item.src === "saved" || (item.src === "magene" && item.libraryId)) {
            root.api("POST", "/api/library/get", { id: item.id || item.libraryId }, r => {
                root.busyKey = ""
                r.ok ? cb(r.name, r.gpx) : bad(r.error || qsTr("Couldn't open it."))
            })
        } else if (item.src === "bryton") {
            root.api("POST", "/api/bryton/tracks/gpx", { name: item.name }, r => {
                root.busyKey = ""
                r.ok ? cb(item.name, r.gpx) : bad(r.error || qsTr("Couldn't read it from the Bryton."))
            })
        } else if (item.src === "etrex") {
            root.busyKey = ""
            item.gpxText ? cb(item.name, item.gpxText) : bad(qsTr("Couldn't read it from the eTrex."))
        } else {
            root.busyKey = ""
            bad(qsTr("This route can't be read back."))
        }
    }

    // ---- Export all (a user's request, 2026-11-13): every route of the source shown, one GPX
    // each, into a folder you pick (it starts in the last export folder). One at a time, so the
    // watch's USB reads never overlap.
    property var exportQueue: []
    property url exportAllTarget
    property int exportAllDone: 0
    property int exportAllTotal: 0
    property int exportAllSaved: 0
    property var exportAllFailed: []
    property bool exportAllRunning: false
    property bool exportAllStop: false
    property string exportAllResult: ""
    function startExportAll(folder) {
        LocalFileService.rememberExportFolder(folder)
        root.exportAllTarget = folder
        root.exportQueue = root.sorted(root.sourceItems)
        root.exportAllTotal = root.exportQueue.length
        root.exportAllDone = 0; root.exportAllSaved = 0; root.exportAllFailed = []
        root.exportAllResult = ""; root.exportAllStop = false; root.exportAllRunning = true
        root.exportNext()
    }
    function exportNext() {
        if (root.exportAllStop || root.exportAllDone >= root.exportAllTotal) {
            root.exportAllRunning = false
            let m = root.exportAllSaved === 1 ? qsTr("Saved 1 GPX file.") : qsTr("Saved %1 GPX files.").arg(root.exportAllSaved)
            if (root.exportAllFailed.length)
                m += " " + qsTr("Not saved: %1.").arg(root.exportAllFailed.join(", "))
            if (root.exportAllStop && root.exportAllDone < root.exportAllTotal)
                m += " " + qsTr("Stopped.")
            root.exportAllResult = m
            return
        }
        const item = root.exportQueue[root.exportAllDone]
        const next = () => { root.exportAllDone++; exportNextTimer.restart() }
        root.fetchGpx(item, (name, gpx) => {
            const err = LocalFileService.saveTextInFolder(root.exportAllTarget,
                                                          (name || item.name || "route") + ".gpx", gpx)
            if (err.length) root.exportAllFailed = root.exportAllFailed.concat([(item.name || "?") + " (" + err + ")"])
            else root.exportAllSaved++
            next()
        }, err => { root.exportAllFailed = root.exportAllFailed.concat([item.name || "?"]); next() })
    }
    // Next item from the event loop, not from inside the previous one's callback.
    Timer { id: exportNextTimer; interval: 0; onTriggered: root.exportNext() }

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
        currentFolder: LocalFileService.exportFolder
        property string gpx: ""
        onAccepted: {
            const err = LocalFileService.saveText(selectedFile, gpx)
            root.msg = err.length ? qsTr("Couldn't save: %1").arg(err) : ""
        }
    }
    FolderDialog {
        id: exportAllFolder
        title: qsTr("Choose a folder for the GPX files")
        currentFolder: LocalFileService.exportFolder
        onAccepted: root.startExportAll(selectedFolder)
    }
    ThemedDialog {
        id: exportAllDialog
        title: qsTr("Export all routes")
        standardButtons: Dialog.NoButton
        width: 430
        onOpened: { if (!root.exportAllRunning) root.exportAllResult = "" }
        contentItem: Column {
            spacing: Theme.spacingLarge
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeBody
                text: (root.sourceItems.length === 1
                       ? qsTr("Saves the route in the list")
                       : qsTr("Saves the %1 routes in the list").arg(root.sourceItems.length))
                      + " " + qsTr("into a folder you pick, one GPX file each, named after the route. "
                                   + "Nothing is overwritten.")
            }
            Text {
                visible: root.exportAllRunning || root.exportAllResult !== ""
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.text
                font.pixelSize: Theme.fontSizeBody
                text: root.exportAllRunning
                      ? qsTr("Saving %1 of %2…").arg(Math.min(root.exportAllDone + 1, root.exportAllTotal)).arg(root.exportAllTotal)
                      : root.exportAllResult
            }
            Item {
                width: parent.width
                height: 36
                Row {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        text: root.exportAllRunning ? qsTr("Stop") : qsTr("Close")
                        onClicked: root.exportAllRunning ? root.exportAllStop = true : exportAllDialog.close()
                    }
                    RoundedButton {
                        visible: !root.exportAllRunning && root.exportAllResult !== ""
                        text: qsTr("Open folder")
                        onClicked: LocalFileService.openFolder(root.exportAllTarget)
                    }
                    RoundedButton {
                        visible: !root.exportAllRunning && root.exportAllResult === ""
                        text: qsTr("Choose folder…")
                        onClicked: exportAllFolder.open()
                    }
                }
            }
        }
    }
    function saveGpx(name, gpx) {
        exportDialog.gpx = gpx
        exportDialog.currentFile = LocalFileService.exportFolder + "/" + (name || "route").replace(/[\\/:*?"<>|]/g, "_") + ".gpx"
        exportDialog.open()
    }

    // ---- upload a GPX (the old page's flow): preview it, then send it to a connected device.
    // It's also kept in the Library, and can go to the planner. RouteService does the preview
    // + the watch upload exactly as before.
    property string pendingLibraryId: ""
    FileDialog {
        id: importDialog
        title: qsTr("Upload GPX")
        nameFilters: [qsTr("GPX files (*.gpx)"), qsTr("All files (*)")]
        onAccepted: {
            RouteService.loadGpxFile(selectedFile)
            root.brytonMsg = ""; root.mageneMsg = ""; root.edgeMsg = ""; root.pendingLibraryId = ""
            const gpx = LocalFileService.readText(selectedFile)
            if (!gpx || gpx.length === 0) return
            // The name the import card shows (the GPX's own, else the file's), not the file name.
            const name = PlanStore.routeNameOf(gpx, selectedFile).replace(/\.gpx$/i, "")
            root.api("POST", "/api/library/save", { name: name, gpx: gpx }, r => {
                if (r.ok) root.pendingLibraryId = r.id
                root.api("GET", "/api/library", null, l => root.saved = l.ok ? l.routes : root.saved)
            })
        }
    }
    readonly property bool hasPending: RouteService.pendingRoute.name !== undefined
    function pendingName() { return (RouteService.pendingRoute.name || "route").replace(/\.gpx$/i, "") }
    property string brytonMsg: ""
    property bool brytonOk: false
    property bool brytonSending: false
    function sendPendingToBryton() {
        root.brytonMsg = qsTr("Sending to Bryton…"); root.brytonOk = false; root.brytonSending = true
        root.api("POST", "/api/bryton/route", { name: root.pendingName(), gpx: RouteService.pendingRouteGpxText }, r => {
            root.brytonSending = false
            root.brytonOk = !!r.ok
            root.brytonMsg = r.ok ? qsTr("Sent to the Bryton — it shows up once you unplug it.")
                                  : (r.error || qsTr("Send to Bryton failed"))
            if (r.ok) root.refresh()
        })
    }
    // A USB-drive Garmin Edge (Edge 500, 2026-11-05): routes go in as a TCX course.
    property bool edge500Here: false
    property string edgeMsg: ""
    property bool edgeOk: false
    property bool edgeSending: false
    function sendPendingToEdge500() {
        root.edgeMsg = qsTr("Sending to the Edge 500…"); root.edgeOk = false; root.edgeSending = true
        root.api("POST", "/api/edge500/route", { name: root.pendingName(), gpx: RouteService.pendingRouteGpxText }, r => {
            root.edgeSending = false
            root.edgeOk = !!r.ok
            root.edgeMsg = r.ok ? qsTr("Sent to the Edge 500 — unplug it, then find it under Training › Courses.")
                                : (r.error || qsTr("Send to Edge 500 failed"))
        })
    }
    property string mageneMsg: ""
    property bool mageneOk: false
    property bool mageneSending: false
    function sendPendingToMagene() {
        root.mageneSending = true; root.mageneOk = false
        BikeDevices.mageneConnecting = true
        root.mageneMsg = qsTr("Sending to Magene… keep the C406 awake and close by")
        const body = { name: root.pendingName(), gpx: RouteService.pendingRouteGpxText }
        if (BikeDevices.magene && BikeDevices.magene.address) body.address = BikeDevices.magene.address
        root.api("POST", "/api/magene/route", body, r => {
            root.mageneSending = false
            BikeDevices.mageneConnecting = false
            BikeDevices.mageneAnswered(!!r.ok)
            root.mageneOk = !!r.ok
            root.mageneMsg = r.ok ? qsTr("Sent to Magene — it's now the route under Navigation")
                                  : (r.error || qsTr("Send to Magene failed"))
            if (r.ok) { mageneRoute.name = root.pendingName(); mageneRoute.libraryId = root.pendingLibraryId }
        })
    }

    // "Send to device" (André, 2026-09-26): only the devices connected right now. One -> send
    // straight away; several -> a menu to pick.
    readonly property var sendTargets: {
        const out = []
        HomeViewModel.routeWatches.forEach((w, i) => out.push({ key: "watch:" + i, label: w.label }))
        if (root.etrexHere && GarminService.hasSdCard) out.push({ key: "etrex", label: qsTr("eTrex (SD card)") })
        if (root.bryton !== null) out.push({ key: "bryton", label: qsTr("Bryton") })
        if (root.edge500Here) out.push({ key: "edge500", label: qsTr("Garmin Edge 500") })
        if (BikeDevices.magene !== null && BikeDevices.mageneReachable) out.push({ key: "magene", label: qsTr("Magene") })
        return out
    }
    function sendPendingTo(key) {
        if (key.startsWith("watch:")) {
            const w = HomeViewModel.routeWatches[parseInt(key.substring(6))]
            if (!w) return
            if (w.productId < 0) RouteService.uploadPendingRoute(true)      // the Bluetooth watch
            else RouteService.uploadPendingRouteTo(w.productId, w.serial)
        }
        else if (key === "etrex") GarminService.writeGpxToDevice(root.pendingName().replace(/[\\/:*?"<>|]/g, "_") + ".gpx",
                                                                 RouteService.pendingRouteGpxText)
        else if (key === "bryton") root.sendPendingToBryton()
        else if (key === "magene") root.sendPendingToMagene()
        else if (key === "edge500") root.sendPendingToEdge500()
    }
    ThemedMenu {
        id: sendMenu
        // One row per plugged watch, named (three Peaks -> three rows).
        Instantiator {
            model: HomeViewModel.routeWatches
            delegate: ThemedMenuItem {
                required property var modelData
                required property int index
                text: modelData.label
                onTriggered: root.sendPendingTo("watch:" + index)
            }
            onObjectAdded: (i, item) => sendMenu.insertItem(i, item)
            onObjectRemoved: (i, item) => sendMenu.removeItem(item)
        }
        ThemedMenuItem { text: qsTr("eTrex (SD card)"); visible: root.sendTargets.some(t => t.key === "etrex"); onTriggered: root.sendPendingTo("etrex") }
        ThemedMenuItem { text: qsTr("Bryton"); visible: root.sendTargets.some(t => t.key === "bryton"); onTriggered: root.sendPendingTo("bryton") }
        ThemedMenuItem { text: qsTr("Magene"); visible: root.sendTargets.some(t => t.key === "magene"); onTriggered: root.sendPendingTo("magene") }
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
        title: item && item.src === "watch" ? qsTr("Remove from the watch") : qsTr("Delete route")
        standardButtons: Dialog.Yes | Dialog.No
        width: 380
        contentItem: Text {
            width: 340; wrapMode: Text.WordWrap
            color: Theme.text; font.pixelSize: Theme.fontSizeBody
            text: deleteDialog.item
                  ? (deleteDialog.item.src === "watch"
                     ? qsTr("Remove “%1” from the watch? The other routes and the POIs stay.").arg(deleteDialog.item.name)
                     : deleteDialog.item.src === "bryton"
                     ? qsTr("Delete “%1” from the Bryton?").arg(deleteDialog.item.name)
                     : deleteDialog.item.src === "etrex"
                     ? qsTr("Delete “%1” (%2) from the eTrex's SD card?").arg(deleteDialog.item.name).arg(deleteDialog.item.fileName)
                     : qsTr("Delete “%1” from your saved routes?").arg(deleteDialog.item.name))
                  : ""
        }
        onAccepted: {
            if (!item) return
            if (item.src === "saved")
                root.api("POST", "/api/library/delete", { id: item.id }, r => r.ok ? root.refresh() : root.msg = r.error)
            else if (item.src === "bryton")
                root.api("POST", "/api/bryton/tracks/delete", { name: item.name }, r => r.ok ? root.refresh() : root.msg = r.error)
            else if (item.src === "etrex")
                root.msg = GarminService.deleteRouteFromSdCard(item.fileName)   // SD card only; "" = done
            else if (item.src === "watch") {
                // The routes database is rewritten without it and the POIs put back
                // (POST /api/routes/remove, write_nav.remove_route); the Android app's Remove.
                const key = item.key
                root.busyKey = key
                root.removingKey = key
                root.msg = ""
                root.api("POST", "/api/routes/remove", { name: item.name }, r => {
                    if (root.busyKey === key) root.busyKey = ""
                    root.removingKey = ""
                    if (r.ok) RouteService.refresh()
                    else root.msg = r.error || qsTr("Removing the route failed.")
                })
            }
        }
    }

    ThemedMenu {
        id: itemMenu
        property var item: ({})
        ThemedMenuItem { text: qsTr("Open in planner"); onTriggered: root.openItem(itemMenu.item) }

        ThemedMenuItem { text: qsTr("Rename…"); visible: itemMenu.item.src === "saved" || itemMenu.item.src === "bryton"
                         onTriggered: { renameDialog.item = itemMenu.item; renameDialog.open() } }
        // eTrex: only files on the SD card (never internal memory - GARMIN_USB_IMPORT_SPEC.md).
        ThemedMenuItem { text: qsTr("Delete…"); visible: itemMenu.item.src === "saved" || itemMenu.item.src === "bryton"
                                                         || (itemMenu.item.src === "etrex" && itemMenu.item.onSdCard === true)
                                                         || (itemMenu.item.src === "watch" && !root.legacyWatch
                                                             && DeviceCapabilities.supportsNavRemove)
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
    // The route cards of one source (the old "On the watch" list), inside the list card.
    component RouteSection: Item {
        id: section
        property string title: ""
        property var items: []
        property bool loading: false
        property string emptyTitle: qsTr("No routes here")
        property string emptyText: ""
        property string emptyActionText: ""
        width: parent ? parent.width : 0
        implicitHeight: sectionCol.implicitHeight
        height: implicitHeight

        Column {
            id: sectionCol
            width: parent.width
            spacing: Theme.spacingSmall

            Text { visible: section.title.length > 0; text: section.title; font.bold: true; color: Theme.text }
            Text { visible: section.loading; text: qsTr("Reading…"); color: Theme.mutedText }
            // An empty source says what to do next (desktop picks #11).
            EmptyState {
                visible: !section.loading && section.items.length === 0
                glyph: Icons.routes
                title: section.emptyTitle
                text: section.emptyText
                actionText: section.emptyActionText
                onAction: importDialog.open()
            }

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
                            route.ascentMeters !== undefined ? qsTr("ascent %1 m").arg(Math.round(route.ascentMeters)) : "",
                            route.descentMeters !== undefined ? qsTr("descent %1 m").arg(Math.round(route.descentMeters)) : ""
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
                                        text: root.removingKey === routeDelegate.route.key ? qsTr("Removing…") : qsTr("Export")
                                        busy: root.removingKey === routeDelegate.route.key || root.busyKey === routeDelegate.route.key
                                        enabled: root.busyKey === "" && (routeDelegate.route.src !== "magene" || !!routeDelegate.route.libraryId)
                                        onClicked: root.exportItem(routeDelegate.route)
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
                            TapHandler { onTapped: root.showBig(routeDelegate.route) }
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
                                           text: root.busyKey === routeDelegate.route.key ? qsTr("Working…") : routeDelegate.stats
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
        // Full width with the same side margin as Health, Gear and Totals (André, 2026-10-04:
        // "match") - this page and Settings sat in a narrow centred strip.
        anchors.left: parent.left
        anchors.leftMargin: Theme.spacingLarge
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: parent.width - Theme.spacingLarge * 2
        spacing: Theme.spacingMedium

        // --- Import a route (the old card): Upload GPX -> preview -> send to a device; or the planner
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
                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton { text: qsTr("Upload GPX…"); onClicked: importDialog.open() }
                    // The planner: weather, climbs, days, race plan - for people who want to plan.
                    // With a GPX uploaded it opens THAT route in the planner.
                    RoundedButton {
                        text: root.hasPending ? qsTr("Open in planner") : qsTr("Open planner")
                        onClicked: root.hasPending
                                   ? root.openRequested(root.pendingName(), RouteService.pendingRouteGpxText, root.pendingLibraryId)
                                   : root.openPlanner()
                    }
                    RoundedButton {
                        id: sendButton
                        visible: root.hasPending
                        enabled: root.sendTargets.length > 0 && !root.mageneSending
                        text: root.sendTargets.length > 1 ? qsTr("Send to device ▾") : qsTr("Send to device")
                        busy: root.mageneSending || root.brytonSending
                        onClicked: {
                            if (root.sendTargets.length === 1) root.sendPendingTo(root.sendTargets[0].key)
                            else sendMenu.popup(sendButton, 0, sendButton.height)
                        }
                    }
                }
                Item {
                    width: parent.width
                    height: 160
                    MapView {
                        anchors.fill: parent
                        readonly property var center: RouteViewModel.trackCenter(RouteService.pendingRoute.track)
                        latitude: center ? center.lat : WeatherService.latitude
                        longitude: center ? center.lon : WeatherService.longitude
                        zoomLevel: center ? 12 : 10
                        trackPoints: RouteService.pendingRoute.track || []
                        TapHandler { onTapped: root.showBig({ name: RouteService.pendingRoute.name, track: RouteService.pendingRoute.track }) }
                    }
                }
                Text {
                    visible: root.hasPending
                    width: parent.width
                    elide: Text.ElideRight
                    text: RouteService.pendingRoute.name || ""
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody
                }
                Text {
                    visible: root.hasPending && root.sendTargets.length === 0
                    width: parent.width; wrapMode: Text.WordWrap
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("No device connected that takes routes - plug one in (or wake the Magene) to send this one.")
                }
                // Garmin: SD card only (GARMIN_USB_IMPORT_SPEC.md - never internal memory).
                Text {
                    visible: root.etrexHere && root.hasPending
                    width: parent.width; wrapMode: Text.WordWrap
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                    text: GarminService.hasSdCard
                        ? qsTr("This will be sent to the SD card only - writing to internal memory can break your device.")
                        : qsTr("No SD card detected in this Garmin device - sending a route is disabled. Writing to internal memory can break your device.")
                }
                // Ambit1/2 predate the route region: reading works, writing doesn't.
                Text {
                    visible: root.watchHere && root.hasPending && !HomeViewModel.isGarmin && !DeviceCapabilities.supportsRouteWrite
                    width: parent.width; wrapMode: Text.WordWrap
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("%1 can't have routes written to it from this app yet - its routes are legacy waypoints, and only adding POIs is supported. You can still export this one.")
                        .arg(HomeViewModel.deviceDisplayName)
                }
                // Results of a send / upload, under the buttons (desktop picks #7).
                StatusNote { width: parent.width; text: root.brytonMsg
                             tone: root.brytonSending ? "working" : root.brytonOk ? "success" : "error" }
                StatusNote { width: parent.width; text: root.edgeMsg
                             tone: root.edgeSending ? "working" : root.edgeOk ? "success" : "error" }
                StatusNote { width: parent.width; text: root.mageneMsg
                             tone: root.mageneSending ? "working" : root.mageneOk ? "success" : "error" }
                StatusNote { width: parent.width; text: RouteService.uploadResultText
                             tone: RouteService.uploadOk ? "success" : "error" }
                StatusNote { width: parent.width; tone: "error"
                             text: root.etrexHere ? GarminService.writeError : "" }
                StatusNote { width: parent.width; tone: "success"
                             text: root.etrexHere && GarminService.writeOk && GarminService.writeError.length === 0
                                   ? qsTr("Sent to the SD card.") : "" }
            }
        }

        // --- The route list: ONE source at a time, picked in the drop-down --------------------
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Row {
                    width: parent.width
                    spacing: Theme.spacingMedium
                    RoundedComboBox {
                        id: sourceBox
                        width: 240
                        model: root.sources.map(x => x.label)
                        currentIndex: root.sources.findIndex(x => x.key === root.source)
                        // A new model (a device's list arriving late) resets the index to 0 and
                        // drops the binding - label read "On the watch" over the Bryton's list.
                        onModelChanged: Qt.callLater(() => sourceBox.currentIndex = Qt.binding(
                                            () => root.sources.findIndex(x => x.key === root.source)))
                        onActivated: (i) => { root.sourceKey = root.sources[i].key; root.msg = ""; root.refreshSource(root.sourceKey) }
                    }
                    ViewModeToggle {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: root.source !== "watch" || !root.legacyWatch
                        mode: Theme.routesView
                        onChosen: (m) => Theme.routesView = m
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: root.sourceItems.length > 0 && !(root.source === "watch" && root.legacyWatch)
                        text: qsTr("Export all…")
                        color: exportAllHover.hovered ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                        font.bold: true
                        TapHandler { onTapped: exportAllDialog.open() }
                        HoverHandler { id: exportAllHover; cursorShape: Qt.PointingHandCursor }
                    }
                }
                Row {
                    spacing: Theme.spacingSmall
                    visible: root.sourceItems.length > 1
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
                StatusNote { width: parent.width; tone: "error"; text: root.msg }

                // Ambit1/2: readable but not writable, no track - read-only rows (as before).
                Column {
                    visible: root.source === "watch" && root.legacyWatch
                    width: parent.width
                    spacing: Theme.spacingSmall
                    EmptyState {
                        visible: root.legacyRoutes.length === 0
                        glyph: Icons.routes
                        title: qsTr("No routes on the watch")
                    }
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
                RouteSection {
                    visible: !(root.source === "watch" && root.legacyWatch)
                    title: ""
                    items: root.sourceItems
                    loading: (root.source === "watch" && RouteService.loading)
                             || (root.source === "etrex" && GarminService.deviceGpxLoading)
                    emptyTitle: root.source === "library" ? qsTr("Nothing saved yet")
                              : root.source === "magene" ? qsTr("No route on the Magene")
                              : qsTr("No routes here")
                    emptyText: root.source === "library" ? qsTr("Upload a GPX and it's kept here.")
                             : root.source === "magene" ? qsTr("It holds one route — sending one replaces it.")
                             : ""
                    emptyActionText: root.source === "library" ? qsTr("Upload GPX…") : ""
                }
            }
        }
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
