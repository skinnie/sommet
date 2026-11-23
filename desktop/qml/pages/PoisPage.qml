import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import SommetUi

// One map and one list (André, 2026-11-23: "integrate the idea of the map + list but we take the
// map where we already have to add poi ... make it dragable and zoomable like all others"). The
// map shows every POI of the list and is also where a new one is placed: a double-click, a
// right-click > Point here, or + Add POI puts an orange pin that can be dragged, and the form
// opens beside the map. A click on a pin marks its line; a click on a line goes to its pin and
// opens its actions. Add is a real watch write (write_nav.py's `addpoi`, see
// backend/server.py's _handle_poi_add).
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true
    property string saveError: ""

    Component.onCompleted: {
        // Real, 2026-08-22: Ambit1/2 predate the SBEM POI database PoiService reads - same
        // fix as Watch Settings/Sport Modes/Routes. Real waypoints for this family are
        // shown below instead, via /api/legacy/settings.
        // Only with a watch: the page is reachable without one now (it has the library).
        if (HomeViewModel.connected && DeviceCapabilities.supportsPOIs)
            PoiService.refresh()
        GarminService.refreshDeviceGpx()
        root.refreshLibrary()
        RouteService.syncLibraries()      // what other devices saved since (Sommet Sync)
    }
    // The library is shared between devices through Sommet Sync: read it again when a sync
    // brought something, and send a change here a few seconds after it is made.
    Connections {
        target: RouteService
        function onLibrariesSynced(changed) { if (changed) root.refreshLibrary() }
    }
    // A watch that connects while this page is open: read its POIs then.
    Connections {
        target: HomeViewModel
        function onConnectedChanged() {
            if (HomeViewModel.connected && DeviceCapabilities.supportsPOIs)
                PoiService.refresh()
        }
    }

    // ---- The library: Sommet's own list of POIs (tools/poi_library.py; André, 2026-11-22: "we
    // have a library for the routes but not for the POIs... these two should be similar"). It is
    // one thing and what is on a watch is another: a POI kept here stays whatever happens on a
    // watch, and nothing here changes a watch until a POI is sent to it. What goes in: every POI
    // added or imported on this page, and "Save to library" on a watch's POI. ----
    property var library: []
    property string libraryNote: ""
    property string libraryTone: "success"
    // The POI being added: its pin on the map and the form beside it. A send closes the form
    // once the watch has it; `pendingPick` is the POI to mark when the list comes back.
    property bool adding: false
    property bool closeAfterSend: false
    property var pendingPick: null
    // Something to send a POI to: a watch with POIs, or a Garmin (its SD card).
    readonly property bool deviceHere: HomeViewModel.isGarmin
                                       || (HomeViewModel.connected && DeviceCapabilities.supportsPOIs)
    // The list card's source drop-down, as on the Routes page: the device when there is one,
    // then the Library (always there).
    readonly property var sources: {
        const out = []
        if (root.deviceHere)
            out.push({ key: "device", label: HomeViewModel.isGarmin ? qsTr("On the device") : qsTr("On the watch") })
        out.push({ key: "library", label: qsTr("Library (saved POIs)") })
        return out
    }
    property string sourceKey: ""
    readonly property string source: root.sources.some(x => x.key === root.sourceKey) ? root.sourceKey
                                                                                      : root.sources[0].key

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
    // `typeHere` is each POI's type as a number in the connected kind of watch's list: an Ambit3
    // and a Traverse number their types differently, so a POI goes to the other kind by its icon.
    function refreshLibrary() {
        root.api("GET", "/api/poilibrary?for=" + DeviceCapabilities.poiTypeListKey, null,
                 r => { if (r.ok) root.library = r.pois })
    }
    // Keep POIs ({name, lat, lon, ele?, type?, typeList?}) in the library; done(r) gets the answer.
    function keep(items, done) {
        root.api("POST", "/api/poilibrary/save", { pois: items }, r => {
            if (r.ok) { root.refreshLibrary(); RouteService.syncLibrariesSoon() }
            if (done) done(r)
        })
    }
    function keptText(r, count) {
        if (!r.ok)
            return qsTr("Couldn't save to the library: %1").arg(r.error || qsTr("no answer"))
        if (r.saved === 0)
            return count === 1 ? qsTr("Already in the library.") : qsTr("All of them are already in the library.")
        return r.saved === 1 ? qsTr("Saved to the library.") : qsTr("%1 POIs saved to the library.").arg(r.saved)
    }
    // One POI to the connected device: a watch write, or a GPX on a Garmin's SD card.
    function sendToDevice(name, lat, lon, type, ele) {
        if (HomeViewModel.isGarmin) {
            const safeName = (name || "poi").replace(/[\\/:*?"<>|]/g, "_")
            GarminService.writeGpxToDevice("Waypoints_" + safeName + ".gpx",
                                           PoiService.buildWaypointGpx(name, lat, lon))
        } else {
            PoiService.addPoi(name, lat, lon, type, ele !== undefined && ele !== null ? ele : NaN)
        }
    }
    // The form's POI, as the library keeps it (its type is a number in the picker's list).
    function formItem() {
        return { name: root.poiName.trim(), lat: root.poiLat, lon: root.poiLon,
                 type: root.poiType, typeList: DeviceCapabilities.poiTypeListKey }
    }
    // Puts the new POI's pin at a place and opens the form (moves the pin if it is already open).
    function startAdd(lat, lon) {
        const fresh = !root.adding
        root.poiLat = lat
        root.poiLon = lon
        root.coordError = ""
        root.adding = true
        poiArea.picked = -1
        if (fresh) {
            poiNameField.text = ""
            root.poiType = DeviceCapabilities.poiTypeDefault
            Qt.callLater(() => poiNameField.forceActiveFocus())
        }
    }
    function closeForm() {
        root.adding = false
        root.closeAfterSend = false
        root.coordError = ""
    }
    function showFormPin() {
        poiMap.centerOn(root.poiLat, root.poiLon, Math.max(poiMap.currentZoom, 12))
    }
    // Keeps the new POI in the library, then shows it there, marked.
    function saveForm() {
        const item = root.formItem()
        root.pendingPick = item
        root.keep([item], r => {
            root.libraryTone = r.ok ? "success" : "error"
            root.libraryNote = root.keptText(r, 1)
            if (!r.ok) { root.pendingPick = null; return }
            root.closeForm()
            if (root.source !== "library") root.sourceKey = "library"
        })
    }
    // To the device, with a copy in the library. The form closes when the watch has it.
    function sendForm() {
        const item = root.formItem()
        root.libraryNote = ""
        root.closeAfterSend = !HomeViewModel.isGarmin
        root.sendToDevice(item.name, item.lat, item.lon, item.type)
        root.keep([item], r => {
            if (!r.ok) { root.libraryTone = "error"; root.libraryNote = root.keptText(r, 1) }
        })
        if (HomeViewModel.isGarmin) root.closeForm()
    }
    Connections {
        target: PoiService
        function onAddResultChanged() {
            if (!root.closeAfterSend || PoiService.adding) return
            root.closeAfterSend = false
            if (PoiService.addOk) root.closeForm()
        }
    }
    function exportPoi(poi) {
        const safeName = (poi.name || "poi").replace(/[\\/:*?"<>|]/g, "_")
        poiExportDialog.pendingGpx = PoiService.buildWaypointGpx(poi.name, poi.lat, poi.lon)
        poiExportDialog.currentFile = LocalFileService.exportFolder + "/" + safeName + ".gpx"
        poiExportDialog.open()
    }
    // A POI of the device's list, as the library keeps it.
    function deviceItem(poi) {
        return { name: poi.name, lat: poi.lat, lon: poi.lon, ele: poi.ele,
                 type: poi.type, typeList: DeviceCapabilities.readPoiTypeListKey }
    }
    // A POI imported from a GPX: listed here, and kept in the library straight away.
    Connections {
        target: PoiService
        function onImportedPoisChanged() {
            const pois = PoiService.importedPois
            if (pois.length === 0) return
            // The file's POIs go to the library, and the page shows them there.
            root.keep(pois.map(x => ({ name: x.name, lat: x.lat, lon: x.lon, ele: x.ele })), r => {
                root.libraryTone = r.ok ? "success" : "error"
                root.libraryNote = root.keptText(r, pois.length)
                if (r.ok) root.sourceKey = "library"
            })
        }
    }

    FileDialog {
        id: poiFileDialog
        title: qsTr("Import POIs from GPX")
        nameFilters: [qsTr("GPX files (*.gpx)")]
        onAccepted: PoiService.importGpxFile(selectedFile)
    }

    // Real request 2026-08-08: "1 POI => name => export (choose location, default
    // downloads folder)" - same save-dialog pattern as Routes/Activities export, via
    // LocalFileService. pendingGpx is set right before open() since PoiService.
    // buildWaypointGpx() is synchronous (no backend round trip needed for a single point).
    FileDialog {
        id: poiExportDialog
        title: qsTr("Export POI as GPX")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("GPX files (*.gpx)")]
        currentFolder: LocalFileService.exportFolder
        property string pendingGpx: ""
        onAccepted: root.saveError = LocalFileService.saveText(selectedFile, pendingGpx)
    }

    // Removing a POI from the watch (2026-10-25, the Android app's Remove ported): a confirm
    // first, then PoiService.removePoi() - the backend saves a copy of the list and writes it
    // back without this one.
    ThemedDialog {
        id: removePoiDialog
        property var poi: null
        title: qsTr("Remove from the watch")
        standardButtons: Dialog.Yes | Dialog.No
        width: 380
        contentItem: Text {
            width: 340
            wrapMode: Text.WordWrap
            color: Theme.text
            font.pixelSize: Theme.fontSizeBody
            text: removePoiDialog.poi
                  ? qsTr("Remove “%1” from the watch? Sommet saves a copy of the POIs first.")
                        .arg(removePoiDialog.poi.name)
                  : ""
        }
        onAccepted: if (poi) PoiService.removePoi(poi.name, poi.lat, poi.lon)
    }

    ThemedDialog {
        id: renameLibraryDialog
        property var poi: null
        title: qsTr("Rename")
        standardButtons: Dialog.Ok | Dialog.Cancel
        width: 380
        contentItem: RoundedTextField { id: renameLibraryField; width: 340; onAccepted: renameLibraryDialog.accept() }
        onOpened: { renameLibraryField.text = poi ? poi.name : ""; renameLibraryField.forceActiveFocus() }
        onAccepted: {
            const n = renameLibraryField.text.trim()
            if (!poi || n.length === 0) return
            root.api("POST", "/api/poilibrary/rename", { id: poi.id, name: n }, r => {
                root.libraryTone = r.ok ? "success" : "error"
                root.libraryNote = r.ok ? "" : (r.error || qsTr("Renaming failed."))
                root.refreshLibrary()
                if (r.ok) RouteService.syncLibrariesSoon()
            })
        }
    }
    ThemedDialog {
        id: deleteLibraryDialog
        property var poi: null
        title: qsTr("Delete from the library")
        standardButtons: Dialog.Yes | Dialog.No
        width: 380
        contentItem: Text {
            width: 340
            wrapMode: Text.WordWrap
            color: Theme.text
            font.pixelSize: Theme.fontSizeBody
            text: deleteLibraryDialog.poi
                  ? qsTr("Delete “%1” from your saved POIs? A watch that has it keeps it.")
                        .arg(deleteLibraryDialog.poi.name)
                  : ""
        }
        onAccepted: {
            if (!poi) return
            root.api("POST", "/api/poilibrary/delete", { id: poi.id }, r => {
                root.libraryTone = r.ok ? "success" : "error"
                root.libraryNote = r.ok ? qsTr("Deleted from the library.") : (r.error || qsTr("Deleting failed."))
                root.refreshLibrary()
                if (r.ok) RouteService.syncLibrariesSoon()
            })
        }
    }

    // POI note dialog - what happens to POIs sent to the watch (André, 2026-08-17), the same
    // little "i" affordance as the Routes page. Opens next to its badge.
    ThemedDialog {
        id: poiInfoDialog
        parent: Overlay.overlay
        title: qsTr("Points of interest")
        standardButtons: Dialog.NoButton
        onAboutToShow: {
            const p = poiInfoBadge.mapToItem(Overlay.overlay, 0, poiInfoBadge.height + Theme.spacingSmall);
            x = Math.max(Theme.spacingLarge, Math.min(p.x, Overlay.overlay.width - width - Theme.spacingLarge));
            y = Math.min(p.y, Overlay.overlay.height - height - Theme.spacingLarge);
        }
        contentItem: Column {
            width: 360
            spacing: Theme.spacingSmall
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: qsTr("POIs that are sent to the watch will be pushed to the Suunto app by SuuntoLink.")
                color: Theme.text
                font.bold: true
                font.pixelSize: Theme.fontSizeBody
            }
            RoundedButton {
                text: qsTr("Close")
                onClicked: poiInfoDialog.close()
            }
        }
    }

    property string poiName: ""
    // #10 (André, 2026-09-02): shown under the coordinate fields when a typed value is out of
    // range, instead of silently snapping back.
    property string coordError: ""
    // Real request 2026-08-07: this used to default to an arbitrary Alps coordinate (46.8,
    // 8.2), so the preview map below showed some unrelated place until the user typed real
    // numbers. WeatherService.latitude/longitude are IP-detected by default now (see
    // HomePage.qml's own startup call), so binding to them directly gives a real, locally
    // relevant starting point instead - and it's a genuine binding, not just an initial
    // value, so it keeps tracking if the IP lookup resolves after this page has already
    // loaded. Typing into either field below assigns to these directly, which breaks the
    // binding for that one exactly as expected - the usual QML behavior, not special-cased.
    property real poiLat: WeatherService.latitude
    property real poiLon: WeatherService.longitude

    // POI type (the icon the watch shows): the type BYTE written to the watch, one of the
    // connected watch's own list (DeviceCapabilities.poiTypes: the Ambit3's 18, or the 48 of a
    // Traverse / Traverse Alpha, which are numbered differently). Starts at that watch's plain
    // type ("Waypoint" 17, "POI" 26 on a Traverse), what the watch itself uses.
    property int poiType: DeviceCapabilities.poiTypeDefault
    // Another kind of watch selected: a number picked from the other list means something else
    // there, so the picker goes back to the plain type.
    Connections {
        target: DeviceCapabilities
        function onTraversePoiTypesChanged() {
            root.poiType = DeviceCapabilities.poiTypeDefault
            root.refreshLibrary()          // typeHere is per kind of watch
        }
    }

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: Math.max(280, Math.min(root.width - Theme.spacingLarge * 2, 1120))
        spacing: Theme.spacingMedium

        // --- The POIs: ONE source at a time, picked in the drop-down as on the Routes page (the
        // connected device, or the Library, always there), on one map and one list. ---
        Card {
            id: onDevicePoiCard
            width: parent.width
            readonly property bool fromDevice: root.source === "device"
            readonly property bool loading: fromDevice
                && (HomeViewModel.isGarmin ? GarminService.deviceGpxLoading : PoiService.loading)
            readonly property var onDevicePois:
                HomeViewModel.isGarmin ? GarminService.onDevicePois : PoiService.onWatchPois
            readonly property var items: fromDevice ? onDevicePois : root.library
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                // The list on the left; Import and + Add POI on the right, or under it when
                // the window is narrow.
                Flow {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Row {
                        id: poiBarLeft
                        spacing: Theme.spacingMedium
                        RoundedComboBox {
                            id: poiSourceBox
                            width: 220
                            model: root.sources.map(x => x.label)
                            currentIndex: root.sources.findIndex(x => x.key === root.source)
                            // A new model (a watch connecting) resets the index to 0 and drops
                            // the binding (seen on the Routes page's own drop-down).
                            onModelChanged: Qt.callLater(() => poiSourceBox.currentIndex = Qt.binding(
                                                () => root.sources.findIndex(x => x.key === root.source)))
                            onActivated: (i) => {
                                root.sourceKey = root.sources[i].key
                                root.libraryNote = ""
                                if (root.sourceKey === "library") root.refreshLibrary()
                            }
                        }
                        Rectangle {
                            id: poiInfoBadge
                            anchors.verticalCenter: parent.verticalCenter
                            width: 15; height: 15; radius: 7.5
                            color: "transparent"
                            border.width: 1
                            border.color: Theme.mutedText
                            Text {
                                anchors.centerIn: parent
                                text: "i"
                                font.pixelSize: Theme.fontSizeLabel
                                font.bold: true
                                color: Theme.mutedText
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: poiInfoDialog.open()
                            }
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: onDevicePoiCard.fromDevice && !onDevicePoiCard.loading
                                     && onDevicePoiCard.items.length > 0
                            text: qsTr("Save all to library")
                            color: saveAllHover.hovered ? Theme.primary : Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                            font.bold: true
                            HoverHandler { id: saveAllHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler {
                                onTapped: {
                                    const pois = onDevicePoiCard.onDevicePois.map(x => root.deviceItem(x))
                                    root.keep(pois, r => {
                                        root.libraryTone = r.ok ? "success" : "error"
                                        root.libraryNote = root.keptText(r, pois.length)
                                    })
                                }
                            }
                        }
                    }
                    Item {
                        width: Math.max(0, parent.width - poiBarLeft.width - poiBarRight.width
                                           - Theme.spacingSmall * 2)
                        height: 1
                    }
                    Row {
                        id: poiBarRight
                        spacing: Theme.spacingSmall
                        RoundedButton {
                            text: qsTr("Import GPX…")
                            onClicked: poiFileDialog.open()
                        }
                        RoundedButton {
                            accent: true
                            text: qsTr("+ Add POI")
                            onClicked: root.startAdd(poiMap.latAtY(poiMap.height / 2),
                                                     poiMap.lonAtX(poiMap.width / 2))
                        }
                    }
                }

                Text {
                    visible: onDevicePoiCard.loading
                    color: Theme.mutedText
                    text: qsTr("Reading POIs off the %1...")
                        .arg(HomeViewModel.isGarmin ? qsTr("device") : qsTr("watch"))
                }
                ErrorBanner {
                    width: parent.width
                    detail: (onDevicePoiCard.fromDevice && !HomeViewModel.isGarmin
                             && !PoiService.loading && !PoiService.ok) ? PoiService.lastError : ""
                    context: qsTr("reading POIs from the watch")
                    // Unplugging the watch mid-session is the usual cause here (real case,
                    // 2026-08-11) - plug it back in and this re-runs the same read.
                    canRetry: true
                    onRetry: PoiService.refresh()
                }
                // The map and the list, side by side (the map above it in a narrow window).
                Item {
                    id: poiArea
                    width: parent.width
                    readonly property var shown: {
                        const all = onDevicePoiCard.loading ? [] : onDevicePoiCard.items
                        const q = poiFind.text.trim().toLowerCase()
                        return q.length > 0 ? all.filter(p => (p.name || "").toLowerCase().indexOf(q) >= 0) : all
                    }
                    readonly property bool sideBySide: width >= 720
                    readonly property real gap: Theme.spacingMedium
                    readonly property real paneH: Math.max(430, Math.min(680, root.height - 170))
                    property int picked: -1
                    // The map opens where you are: on a computer, the place the weather uses.
                    // Any press on the map, All, or Find leaves it there (André, 2026-11-23).
                    property bool atHere: true
                    function goHere() {
                        atHere = true
                        picked = -1
                        poiMap.centerOn(WeatherService.latitude, WeatherService.longitude, 13)
                    }
                    Timer { interval: 0; running: true; onTriggered: poiArea.goHere() }
                    Connections {
                        target: WeatherService
                        function onLocationChanged() { if (poiArea.atHere) poiArea.goHere() }
                    }
                    height: sideBySide ? Math.max(paneH, poiSide.height)
                                       : poiMapBox.height + gap + poiSide.height
                    onShownChanged: {
                        picked = -1
                        const want = root.pendingPick
                        if (!want) return
                        const i = shown.findIndex(p => p.name === want.name
                                                  && Math.abs(p.lat - want.lat) < 0.0002
                                                  && Math.abs(p.lon - want.lon) < 0.0002)
                        if (i < 0) return
                        root.pendingPick = null
                        Qt.callLater(() => pick(i, true))
                    }
                    function typeInfoOf(p) {
                        // A library POI keeps the list its number is in; one read off the device
                        // is numbered in that device's own list.
                        return onDevicePoiCard.fromDevice ? DeviceCapabilities.poiTypeOf(p.type)
                                                          : DeviceCapabilities.poiTypeIn(p.typeList, p.type)
                    }
                    // From the list: the map goes to the pin (a second click shows all again).
                    // From the map: the list scrolls to the line.
                    function pick(i, fromList) {
                        atHere = false
                        if (fromList && picked === i) { picked = -1; poiMap.resetView(); return }
                        picked = i
                        const p = shown[i]
                        if (!p) return
                        if (fromList) poiMap.centerOn(p.lat, p.lon, Math.max(poiMap.currentZoom, 12))
                        Qt.callLater(() => poiRows.positionViewAtIndex(i, ListView.Contain))
                    }

                    Item {
                        id: poiMapBox
                        width: poiArea.sideBySide ? poiArea.width - poiSide.width - poiArea.gap : poiArea.width
                        height: poiArea.sideBySide ? poiArea.height : 300
                        MapView {
                            id: poiMap
                            anchors.fill: parent
                            clip: true
                            // The wheel zooms after a click on the map (it scrolls the page until
                            // then); drag moves it, as on every map.
                            inScrollingPage: true
                            showZoomControls: true
                            // No POI yet: the place the weather uses.
                            latitude: WeatherService.latitude
                            longitude: WeatherService.longitude
                            zoomLevel: 13                 // one POI alone: a neighbourhood
                            markers: poiArea.shown.map(p => {
                                const t = poiArea.typeInfoOf(p)
                                return { lat: p.lat, lon: p.lon, label: p.name, type: t.name, glyph: t.glyph }
                            })
                            selectedMarker: poiArea.picked
                            onMarkerTapped: (i) => poiArea.pick(i, false)
                            PointHandler {
                                acceptedButtons: Qt.LeftButton
                                onActiveChanged: if (active) poiArea.atHere = false
                            }
                            // The map's own centre follows its pins. When the list changes, keep
                            // what is on screen where it is.
                            property real keptLat: NaN
                            property real keptLon: NaN
                            on_CenterLatChanged: {
                                if (userControlled && !isNaN(keptLat))
                                    panY += latToWorldY(keptLat) - latToWorldY(_centerLat)
                                keptLat = _centerLat
                            }
                            on_CenterLonChanged: {
                                if (userControlled && !isNaN(keptLon))
                                    panX += lonToWorldX(keptLon) - lonToWorldX(_centerLon)
                                keptLon = _centerLon
                            }
                            Component.onCompleted: { keptLat = _centerLat; keptLon = _centerLon }

                            function onAPin(x, y) {
                                return poiArea.shown.some(p => Math.hypot(lonToWorldX(p.lon) - originX - x,
                                                                         latToWorldY(p.lat) - originY - 19 - y) < 22)
                            }
                            // Double-click: a new POI here (a double-click on a pin is two picks).
                            TapHandler {
                                acceptedButtons: Qt.LeftButton
                                onDoubleTapped: (ev) => {
                                    if (poiMap.onAPin(ev.position.x, ev.position.y)) return
                                    root.startAdd(poiMap.latAtY(ev.position.y), poiMap.lonAtX(ev.position.x))
                                }
                            }
                            // Right-click: "Point here" (André, 2026-11-21).
                            TapHandler {
                                acceptedButtons: Qt.RightButton
                                onTapped: (event) => {
                                    pointMenu.lat = poiMap.latAtY(event.position.y)
                                    pointMenu.lon = poiMap.lonAtX(event.position.x)
                                    pointMenu.popup()
                                }
                            }
                            ThemedMenu {
                                id: pointMenu
                                property real lat: 0
                                property real lon: 0
                                ThemedMenuItem {
                                    text: qsTr("Point here")
                                    onTriggered: root.startAdd(pointMenu.lat, pointMenu.lon)
                                }
                            }

                            // The new POI's pin: orange, and it can be dragged to the exact spot.
                            Item {
                                id: draftPin
                                visible: root.adding
                                z: 200
                                width: 36; height: 44
                                x: poiMap.lonToWorldX(root.poiLon) - poiMap.originX - width / 2
                                y: poiMap.latToWorldY(root.poiLat) - poiMap.originY - height
                                property point grabOffset: Qt.point(0, 0)
                                Rectangle {
                                    width: 32; height: 32; radius: 16
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    color: "#C4622D"
                                    border.width: 2.5
                                    border.color: "#FFFFFF"
                                    Text {
                                        anchors.centerIn: parent
                                        font.family: Icons.fontFamily
                                        font.pixelSize: 20
                                        color: "#FFFFFF"
                                        text: DeviceCapabilities.poiTypeGlyph(root.poiType)
                                    }
                                }
                                Rectangle {
                                    width: 3; height: 13
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    anchors.bottom: parent.bottom
                                    color: "#C4622D"
                                }
                                HoverHandler { cursorShape: draftDrag.active ? Qt.ClosedHandCursor : Qt.SizeAllCursor }
                                DragHandler {
                                    id: draftDrag
                                    target: null
                                    // Not given up to the map's own drag.
                                    grabPermissions: PointerHandler.TakeOverForbidden
                                    onActiveChanged: if (active) draftPin.grabOffset = Qt.point(
                                        draftPin.width / 2 - centroid.pressPosition.x,
                                        draftPin.height - centroid.pressPosition.y)
                                    onCentroidChanged: {
                                        if (!active) return
                                        const at = poiMap.mapFromItem(null, centroid.scenePosition.x,
                                                                      centroid.scenePosition.y)
                                        root.poiLat = poiMap.latAtY(at.y + draftPin.grabOffset.y)
                                        root.poiLon = poiMap.lonAtX(at.x + draftPin.grabOffset.x)
                                    }
                                }
                            }
                        }
                        // Every POI back in view, under the map's own + and - buttons.
                        Rectangle {
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.topMargin: 72
                            anchors.rightMargin: 8
                            width: 28; height: 28; radius: 4
                            color: "#CCFFFFFF"
                            visible: poiArea.shown.length > 0
                            Text {
                                anchors.centerIn: parent
                                text: qsTr("All")
                                font.pixelSize: Theme.fontSizeCaption
                                font.bold: true
                                color: "#333333"
                            }
                            TapHandler { onTapped: { poiArea.atHere = false; poiArea.picked = -1; poiMap.resetView() } }
                        }
                        // Back to where you are.
                        Rectangle {
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.topMargin: 104
                            anchors.rightMargin: 8
                            width: 28; height: 28; radius: 4
                            color: "#CCFFFFFF"
                            // A target, drawn: the app's icon font holds only the glyphs it uses.
                            Rectangle {
                                anchors.centerIn: parent
                                width: 12; height: 12; radius: 6
                                color: "transparent"
                                border.width: 2
                                border.color: "#333333"
                            }
                            Rectangle { anchors.centerIn: parent; width: 4; height: 4; radius: 2; color: "#333333" }
                            Rectangle { anchors.horizontalCenter: parent.horizontalCenter; y: 5; width: 2; height: 4; color: "#333333" }
                            Rectangle { anchors.horizontalCenter: parent.horizontalCenter; y: 19; width: 2; height: 4; color: "#333333" }
                            Rectangle { anchors.verticalCenter: parent.verticalCenter; x: 5; width: 4; height: 2; color: "#333333" }
                            Rectangle { anchors.verticalCenter: parent.verticalCenter; x: 19; width: 4; height: 2; color: "#333333" }
                            TapHandler { onTapped: poiArea.goHere() }
                        }
                        // What just happened, over the bottom of the map: in the page's flow a
                        // note that hides by itself moved every line under the pointer.
                        Column {
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.bottom: parent.bottom
                            anchors.margins: 8
                            z: 250
                            spacing: 4
                            StatusNote {
                                width: parent.width
                                text: PoiService.importError
                                tone: "error"
                            }
                            StatusNote {
                                width: parent.width
                                text: root.saveError.length > 0 ? qsTr("Couldn't save: %1").arg(root.saveError) : ""
                                tone: "error"
                            }
                            StatusNote {
                                width: parent.width
                                text: root.libraryNote
                                tone: root.libraryTone
                                autoHideMs: 10000
                            }
                            StatusNote {
                                width: parent.width
                                text: HomeViewModel.isGarmin || !onDevicePoiCard.fromDevice ? ""
                                    : PoiService.removing ? qsTr("Removing the POI from the watch...")
                                    : PoiService.removeResultText
                                tone: PoiService.removing ? "working" : PoiService.removeOk ? "success" : "error"
                                autoHideMs: 10000
                            }
                            // A POI sent to the device, from the form or from a line of the list.
                            StatusNote {
                                width: parent.width
                                text: HomeViewModel.isGarmin
                                      ? (GarminService.writeError.length > 0 ? GarminService.writeError
                                         : GarminService.writeOk ? qsTr("Sent to the SD card.") : "")
                                    : PoiService.adding ? qsTr("Sending the POI to the watch...")
                                    : PoiService.addResultText
                                tone: HomeViewModel.isGarmin
                                      ? (GarminService.writeError.length > 0 ? "error" : "success")
                                      : PoiService.adding ? "working" : PoiService.addOk ? "success" : "error"
                                autoHideMs: 10000
                            }
                        }
                        // Search for a place: goes there, and moves the new POI's pin if one is open.
                        PlaceSearchBar {
                            x: 8; y: 8
                            z: 300
                            width: Math.min(260, parent.width - 60)
                            onPlaceChosen: (lat, lon) => {
                                if (root.adding) { root.poiLat = lat; root.poiLon = lon }
                                poiMap.centerOn(lat, lon, 13)
                            }
                        }
                    }

                    Column {
                        id: poiSide
                        x: poiArea.sideBySide ? poiMapBox.width + poiArea.gap : 0
                        y: poiArea.sideBySide ? 0 : poiMapBox.height + poiArea.gap
                        width: poiArea.sideBySide ? Math.round(poiArea.width * 0.40) : poiArea.width
                        spacing: Theme.spacingSmall

                        // The new POI.
                        Rectangle {
                            width: parent.width
                            visible: root.adding
                            height: poiForm.height + Theme.spacingMedium * 2
                            radius: Theme.radiusSmall
                            color: Theme.cardNested
                            Column {
                                id: poiForm
                                x: Theme.spacingMedium
                                y: Theme.spacingMedium
                                width: parent.width - Theme.spacingMedium * 2
                                spacing: Theme.spacingSmall
                                Item {
                                    width: parent.width
                                    height: poiFormTitle.height
                                    Text { id: poiFormTitle; text: qsTr("New POI"); font.bold: true; color: Theme.text }
                                    // The watch keeps at most 15 characters: count them as they
                                    // are typed instead of the field just refusing more.
                                    Text {
                                        anchors.right: parent.right
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: qsTr("%1 / 15").arg(poiNameField.length)
                                        color: Theme.mutedText
                                        font.pixelSize: Theme.fontSizeCaption
                                    }
                                }
                                Row {
                                    width: parent.width
                                    spacing: Theme.spacingSmall
                                // Type dropdown, same rounded-square shape as the name field. ClickFocus stops
                                // it grabbing initial focus on load, and the neutral-grey background override
                                // keeps it from lighting up green on focus/open (André, 2026-08-13). Width 168
                                // fits the leading icon + the longest name ("Crossroads") + the arrow.
                                RoundedComboBox {
                                    id: poiTypeBox
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 158
                                    focusPolicy: Qt.ClickFocus
                                    leftPadding: Theme.spacingSmall
                                    rightPadding: indicator.width + Theme.spacingSmall
                                    model: DeviceCapabilities.poiTypes
                                    textRole: "name"
                                    currentIndex: DeviceCapabilities.poiTypeIndex(root.poiType)
                                    onActivated: (i) => root.poiType = DeviceCapabilities.poiTypes[i].id
                                    // Open the list just below the box instead of on top of it. Overlapping,
                                    // the popup's rounded rectangle sat over the box's own rounded rectangle
                                    // and the two borders doubled up at the top corners (André, 2026-08-13:
                                    // the open dropdown's "corner issue").
                                    popup.y: poiTypeBox.height
                                    // Real, 2026-08-25 (app-wide button coherence pass): this override
                                    // duplicated RoundedComboBox's OLD white+border default, which shadowed
                                    // the shared component's own fix (now flat Theme.cardNested) - dropped
                                    // so this box inherits the current shared look instead of a frozen copy
                                    // of the old one. Only popup.y above (unrelated) needed to be per-instance.
                                    // The selected type's own watch icon sits inside, left of the name, and
                                    // updates live - the "logo" now lives in the dropdown itself.
                                    contentItem: Item {
                                        Row {
                                            anchors.left: parent.left
                                            anchors.verticalCenter: parent.verticalCenter
                                            spacing: Theme.spacingSmall
                                            Icon {
                                                anchors.verticalCenter: parent.verticalCenter
                                                glyph: DeviceCapabilities.poiTypeGlyph(root.poiType)
                                                size: 22
                                                color: Theme.text
                                            }
                                            Text {
                                                anchors.verticalCenter: parent.verticalCenter
                                                text: poiTypeBox.displayText
                                                color: Theme.text
                                                font.pixelSize: Theme.fontSizeBody
                                                elide: Text.ElideRight
                                            }
                                        }
                                    }
                                    // Per-row icon in the dropdown popup too, not just the selected box: the
                                    // shared RoundedComboBox delegate is text-only, so override it here so every
                                    // type in the list carries its own watch glyph (André: "we miss the icons of
                                    // type of poi"). Each row is one entry of DeviceCapabilities.poiTypes.
                                    delegate: ItemDelegate {
                                        width: poiTypeBox.width
                                        height: 34
                                        highlighted: poiTypeBox.highlightedIndex === index
                                        contentItem: Row {
                                            spacing: Theme.spacingSmall
                                            Icon {
                                                anchors.verticalCenter: parent.verticalCenter
                                                glyph: modelData.glyph
                                                size: 20
                                                color: Theme.text
                                            }
                                            Text {
                                                anchors.verticalCenter: parent.verticalCenter
                                                text: modelData.name
                                                color: Theme.text
                                                font.pixelSize: Theme.fontSizeBody
                                                elide: Text.ElideRight
                                            }
                                        }
                                    }
                                }
                                    // The watch stores at most 15 bytes for the name
                                    // (ambit_format.py MAX_NAME_BYTES).
                                    RoundedTextField {
                                        id: poiNameField
                                        anchors.verticalCenter: parent.verticalCenter
                                        width: parent.width - poiTypeBox.width - Theme.spacingSmall
                                        maximumLength: 15
                                        placeholderText: qsTr("Name this POI")
                                        onTextChanged: root.poiName = text
                                    }
                                }
                                Row {
                                    width: parent.width
                                    spacing: Theme.spacingSmall
                                    RoundedTextField {
                                        width: (parent.width - Theme.spacingSmall) / 2
                                        placeholderText: qsTr("Latitude")
                                        text: root.poiLat.toFixed(6)
                                        onEditingFinished: {
                                            const v = WatchUnits.parseDecimal(text)
                                            if (!isNaN(v) && v >= -90 && v <= 90) {
                                                if (Math.abs(v - root.poiLat) < 0.0000005) return
                                                root.poiLat = v
                                                root.coordError = ""
                                                root.showFormPin()
                                            } else {
                                                // #10: don't silently revert - say why.
                                                root.coordError = qsTr("Latitude must be a number between -90 and 90.")
                                                text = Qt.binding(() => root.poiLat.toFixed(6))
                                            }
                                        }
                                    }
                                    RoundedTextField {
                                        width: (parent.width - Theme.spacingSmall) / 2
                                        placeholderText: qsTr("Longitude")
                                        text: root.poiLon.toFixed(6)
                                        onEditingFinished: {
                                            const v = WatchUnits.parseDecimal(text)
                                            if (!isNaN(v) && v >= -180 && v <= 180) {
                                                if (Math.abs(v - root.poiLon) < 0.0000005) return
                                                root.poiLon = v
                                                root.coordError = ""
                                                root.showFormPin()
                                            } else {
                                                // #10: don't silently revert - say why.
                                                root.coordError = qsTr("Longitude must be a number between -180 and 180.")
                                                text = Qt.binding(() => root.poiLon.toFixed(6))
                                            }
                                        }
                                    }
                                }
                                // #10 (André, 2026-09-02): validation feedback instead of a silent revert.
                                Text {
                                    width: parent.width
                                    visible: root.coordError.length > 0
                                    wrapMode: Text.WordWrap
                                    text: root.coordError
                                    color: Theme.error
                                    font.pixelSize: Theme.fontSizeCaption
                                }
                                // Same rule and same warning as Routes' own Send button (2026-08-08).
                                Text {
                                    visible: HomeViewModel.isGarmin
                                    width: parent.width
                                    wrapMode: Text.WordWrap
                                    color: Theme.mutedText
                                    font.pixelSize: Theme.fontSizeCaption
                                    text: GarminService.hasSdCard
                                        ? qsTr("This will be sent to the SD card only - writing to " +
                                                "internal memory can break your device.")
                                        : qsTr("No SD card detected in this Garmin device - sending a " +
                                                "POI is disabled. Writing to internal memory can " +
                                                "break your device.")
                                }
                                // "Save to library" keeps it in Sommet only. A send keeps a copy in
                                // the library too (André, 2026-11-22).
                                Flow {
                                    width: parent.width
                                    spacing: Theme.spacingSmall
                                    RoundedButton {
                                        accent: !root.deviceHere
                                        text: qsTr("Save to library")
                                        enabled: root.poiName.trim().length > 0
                                        onClicked: root.saveForm()
                                    }
                                    RoundedButton {
                                        visible: root.deviceHere
                                        accent: true
                                        text: HomeViewModel.isGarmin ? qsTr("Send to SD card") : qsTr("Send to watch")
                                        enabled: root.poiName.trim().length > 0
                                                 && (HomeViewModel.isGarmin ? GarminService.hasSdCard : !PoiService.adding)
                                        onClicked: root.sendForm()
                                    }
                                    RoundedButton {
                                        text: qsTr("Cancel")
                                        onClicked: root.closeForm()
                                    }
                                }
                            }
                        }

                        Text {
                            visible: !root.adding
                            width: parent.width
                            wrapMode: Text.WordWrap
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                            text: qsTr("Double-click the map to add a POI, or right-click and choose Point here.")
                        }
                        RoundedTextField {
                            id: poiFind
                            visible: !root.adding && onDevicePoiCard.items.length > 0
                            width: parent.width
                            placeholderText: qsTr("Find in this list")
                            onTextChanged: { if (text.length > 0) poiArea.atHere = false; if (!poiArea.atHere) poiMap.resetView() }
                        }
                        Text {
                            visible: onDevicePoiCard.fromDevice && !onDevicePoiCard.loading
                                     && onDevicePoiCard.items.length === 0
                                     && (HomeViewModel.isGarmin || PoiService.ok)
                            width: parent.width
                            wrapMode: Text.WordWrap
                            color: Theme.mutedText
                            text: qsTr("Nothing to sync.")
                        }
                        Text {
                            visible: !onDevicePoiCard.fromDevice && onDevicePoiCard.items.length === 0
                            width: parent.width
                            wrapMode: Text.WordWrap
                            color: Theme.mutedText
                            text: qsTr("Nothing saved yet. A POI you add or import here is kept in the " +
                                       "library, and so is one you save from a watch.")
                        }
                        Text {
                            visible: onDevicePoiCard.items.length > 0 && poiArea.shown.length === 0
                            width: parent.width
                            color: Theme.mutedText
                            text: qsTr("No POI with that name.")
                        }

                        ListView {
                            id: poiRows
                            width: parent.width
                            visible: count > 0
                            clip: true
                            spacing: 2
                            boundsBehavior: Flickable.StopAtBounds
                            height: poiArea.sideBySide ? Math.max(130, poiArea.paneH - y)
                                                       : Math.min(contentHeight, 420)
                            ScrollBar.vertical: ScrollBar {}
                            model: poiArea.shown
                            delegate: Rectangle {
                                id: poiRow
                                required property var modelData
                                required property int index
                                readonly property bool isPicked: poiArea.picked === index
                                readonly property var typeInfo: poiArea.typeInfoOf(modelData)
                                readonly property bool sends: !onDevicePoiCard.fromDevice && root.deviceHere
                                width: ListView.view.width - 10
                                height: poiRowColumn.height + 8
                                radius: Theme.radiusSmall
                                color: isPicked ? Theme.cardNested : poiRowHover.hovered ? Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.04) : "transparent"
                                HoverHandler { id: poiRowHover }

                                Column {
                                    id: poiRowColumn
                                    x: 6; y: 4
                                    width: parent.width - 12
                                    spacing: 6
                                    Item {
                                        width: parent.width
                                        height: Math.max(poiRowText.height, 26)
                                        TapHandler { onTapped: poiArea.pick(poiRow.index, true) }
                                        Text {
                                            id: onWatchPoiGlyph
                                            anchors.verticalCenter: parent.verticalCenter
                                            font.family: Icons.fontFamily
                                            font.pixelSize: Theme.fontSizeBody + 5
                                            color: Theme.primary
                                            text: poiRow.typeInfo.glyph
                                        }
                                        Column {
                                            id: poiRowText
                                            x: onWatchPoiGlyph.width + Theme.spacingSmall
                                            width: parent.width - x
                                            anchors.verticalCenter: parent.verticalCenter
                                            Text {
                                                width: parent.width
                                                elide: Text.ElideRight
                                                text: poiRow.modelData.name
                                                color: Theme.text
                                                font.pixelSize: Theme.fontSizeBody
                                                font.bold: true
                                            }
                                            Text {
                                                width: parent.width
                                                elide: Text.ElideRight
                                                text: qsTr("%1 · %2, %3").arg(poiRow.typeInfo.name)
                                                    .arg(poiRow.modelData.lat.toFixed(4)).arg(poiRow.modelData.lon.toFixed(4))
                                                color: Theme.mutedText
                                                font.pixelSize: Theme.fontSizeCaption
                                            }
                                        }
                                    }
                                    // The marked line's actions.
                                    Flow {
                                        visible: poiRow.isPicked
                                        width: parent.width
                                        spacing: Theme.spacingSmall
                                        RoundedButton {
                                            visible: poiRow.sends
                                            text: HomeViewModel.isGarmin ? qsTr("Send to SD card") : qsTr("Send to watch")
                                            enabled: HomeViewModel.isGarmin ? GarminService.hasSdCard : !PoiService.adding
                                            onClicked: {
                                                root.libraryNote = ""
                                                root.sendToDevice(poiRow.modelData.name, poiRow.modelData.lat, poiRow.modelData.lon,
                                                                  poiRow.modelData.typeHere, poiRow.modelData.ele)
                                            }
                                        }
                                        RoundedButton {
                                            text: qsTr("Export…")
                                            onClicked: root.exportPoi(poiRow.modelData)
                                        }
                                        RoundedButton {
                                            visible: !onDevicePoiCard.fromDevice
                                            text: qsTr("Rename…")
                                            onClicked: { renameLibraryDialog.poi = poiRow.modelData; renameLibraryDialog.open() }
                                        }
                                        RoundedButton {
                                            visible: !onDevicePoiCard.fromDevice
                                            danger: true
                                            text: qsTr("Delete…")
                                            onClicked: { deleteLibraryDialog.poi = poiRow.modelData; deleteLibraryDialog.open() }
                                        }
                                        RoundedButton {
                                            visible: onDevicePoiCard.fromDevice
                                            text: qsTr("Save to library")
                                            onClicked: root.keep([root.deviceItem(poiRow.modelData)], r => {
                                                root.libraryTone = r.ok ? "success" : "error"
                                                root.libraryNote = root.keptText(r, 1)
                                            })
                                        }
                                        RoundedButton {
                                            visible: onDevicePoiCard.fromDevice && !HomeViewModel.isGarmin
                                                     && DeviceCapabilities.supportsPoiRemove
                                            danger: true
                                            enabled: !PoiService.removing
                                            text: qsTr("Remove from the watch…")
                                            onClicked: { removePoiDialog.poi = poiRow.modelData; removePoiDialog.open() }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // --- On-watch POIs - real, 2026-08-08 ("do like for the routes: name => export,
        // thumbnail on map for each"). Real field names (Name=/Location.Latitude=/
        // Location.Longitude=) confirmed directly against live hardware output - the
        // schema-uncertainty caveat this section used to carry no longer applies (see
        // PoiService::parseOnWatchPois's own comment). A thumbnail per POI costs nothing
        // extra over what refresh() already fetched - lat/lon come straight from the same
        // parsed record, no per-POI network/USB round trip. ---
        // Real, 2026-08-22: Ambit1/2 waypoints via /api/legacy/settings' own waypoints[]
        // (tools/legacy_link.py -> openambit's libambit_navigation_read). Read-only: write
        // is openambit's libambit_navigation_write, real but never hardware-tested here for
        // this family - see the sommet-hardware-fleet-check memory.
        Card {
            id: legacyPoiCard
            width: parent.width
            visible: HomeViewModel.connected && !HomeViewModel.isGarmin && !DeviceCapabilities.supportsPOIs

            property bool loading: false
            property string error: ""
            property var waypoints: []

            // Real write now (2026-08-22, openambit's libambit_navigation_write, see the
            // "Add a POI" card above which already posts to /api/pois - the backend routes
            // that to the legacy path itself). Re-read after a successful add so the new
            // POI shows up here without a manual page reload.
            Connections {
                target: PoiService
                // addOk notifies through addResultChanged (poiservice.h) - there is no
                // addOkChanged, so this never ran (audit 2026-10-22).
                function onAddResultChanged() {
                    if (legacyPoiCard.visible && PoiService.addOk) legacyPoiCard.refresh()
                }
            }

            function refresh() {
                legacyPoiCard.loading = true
                legacyPoiCard.error = ""
                const xhr = new XMLHttpRequest()
                xhr.onreadystatechange = function() {
                    if (xhr.readyState !== XMLHttpRequest.DONE) return
                    legacyPoiCard.loading = false
                    let d = null
                    try { d = JSON.parse(xhr.responseText) } catch (e) {}
                    if (!d || !d.ok) {
                        legacyPoiCard.waypoints = []
                        legacyPoiCard.error = (d && d.error) ? d.error : qsTr("Couldn't read POIs from the watch.")
                        return
                    }
                    legacyPoiCard.waypoints = d.waypoints || []
                }
                xhr.open("GET", "http://127.0.0.1:8766/api/legacy/settings")
                xhr.send()
            }
            onVisibleChanged: if (visible && waypoints.length === 0 && error === "" && !loading) refresh()

            // Re-read when the connected watch actually changes (see RoutesPage's legacy card):
            // after a hot-swap the backend heals the stale pin (#16) but this card can be stuck
            // on its stale-window error, since onVisibleChanged won't re-fetch past a non-empty
            // error. Keying on serial fires only on a real change, not every poll.
            property string _lastWatchKey: ""
            Connections {
                target: DeviceService
                function onDeviceInfoChanged() {
                    const key = DeviceService.deviceInfoOk ? DeviceService.serial : ""
                    if (key === legacyPoiCard._lastWatchKey)
                        return
                    legacyPoiCard._lastWatchKey = key
                    legacyPoiCard.error = ""
                    legacyPoiCard.waypoints = []
                    if (legacyPoiCard.visible && !legacyPoiCard.loading)
                        legacyPoiCard.refresh()
                }
            }

            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text { text: qsTr("On the watch"); font.bold: true; color: Theme.text }
                Text { visible: legacyPoiCard.loading; color: Theme.mutedText; text: qsTr("Reading POIs off the watch...") }
                ErrorBanner {
                    width: parent.width
                    detail: legacyPoiCard.error
                    context: qsTr("reading legacy POIs")
                    canRetry: true
                    onRetry: legacyPoiCard.refresh()
                }
                Text {
                    visible: !legacyPoiCard.loading && legacyPoiCard.error === "" && legacyPoiCard.waypoints.length === 0
                    color: Theme.mutedText
                    text: qsTr("No POIs on this watch.")
                }
                Repeater {
                    model: legacyPoiCard.waypoints
                    delegate: Column {
                        width: parent.width
                        spacing: 2
                        Row {
                            width: parent.width
                            spacing: Theme.spacingSmall
                            // The POI's own watch icon, per type (André: "we miss the icons of
                            // type of poi"). type is the Ambit waypoint type byte off the watch.
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                font.family: Icons.fontFamily
                                font.pixelSize: Theme.fontSizeBody + 3
                                color: Theme.primary
                                text: DeviceCapabilities.poiTypeGlyph(modelData.type)
                                // Hover: the type's name in the watch's own list.
                                HoverHandler { id: poiTypeHover }
                                ToolTip.visible: poiTypeHover.hovered
                                ToolTip.text: DeviceCapabilities.poiTypeName(modelData.type)
                            }
                            Text { anchors.verticalCenter: parent.verticalCenter; color: Theme.text; font.bold: true; text: modelData.name }
                        }
                        Text {
                            color: Theme.mutedText
                            text: qsTr("%1, %2  (%3 m)")
                                .arg(modelData.lat.toFixed(5)).arg(modelData.lon.toFixed(5)).arg(modelData.altitude_m)
                        }
                    }
                }
            }
        }
    }

}
