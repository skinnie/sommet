import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import SommetUi

// Step 9. On-watch POIs shown as a real per-POI list with a thumbnail map and Export, since
// 2026-08-08 (see the "On the watch" Card below for the fuller story). Add is a REAL watch
// write since 2026-08-11 - write_nav.py's `addpoi`, the Android app's hardware-confirmed
// algorithm ported back into this repo (see backend/server.py's _handle_poi_add).
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
    property string formNote: ""            // under the Add card's buttons
    property string formTone: "success"
    property string importNote: ""          // in the Import card
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
            if (r.ok) root.refreshLibrary()
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
    // Where the last send was started, so its "sending / sent / failed" note shows next to the
    // button that was pressed and not in both cards: "form" (Add and Import cards) or "list".
    property string sentFrom: "form"
    // One POI to the connected device: a watch write, or a GPX on a Garmin's SD card.
    function sendToDevice(name, lat, lon, type, ele, from) {
        root.sentFrom = from || "form"
        if (HomeViewModel.isGarmin) {
            const safeName = (name || "poi").replace(/[\\/:*?"<>|]/g, "_")
            GarminService.writeGpxToDevice("Waypoints_" + safeName + ".gpx",
                                           PoiService.buildWaypointGpx(name, lat, lon))
        } else {
            PoiService.addPoi(name, lat, lon, type, ele !== undefined && ele !== null ? ele : NaN)
        }
    }
    // The Add card's POI, as the library keeps it (its type is a number in the picker's list).
    function formItem() {
        return { name: root.poiName, lat: root.poiLat, lon: root.poiLon,
                 type: root.poiType, typeList: DeviceCapabilities.poiTypeListKey }
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
            root.importNote = ""
            if (pois.length === 0) return
            root.keep(pois.map(x => ({ name: x.name, lat: x.lat, lon: x.lon, ele: x.ele })),
                      r => root.importNote = root.keptText(r, pois.length))
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
            })
        }
    }
    // The ⋯ menu of one row, as on the Routes page.
    ThemedMenu {
        id: poiMenu
        property var poi: ({})
        property string src: ""
        // Export is the row's own button when there is no device to send to.
        property bool withExport: false
        ThemedMenuItem { text: qsTr("Export…"); visible: poiMenu.src === "library" && poiMenu.withExport
                         onTriggered: root.exportPoi(poiMenu.poi) }
        ThemedMenuItem { text: qsTr("Rename…"); visible: poiMenu.src === "library"
                         onTriggered: { renameLibraryDialog.poi = poiMenu.poi; renameLibraryDialog.open() } }
        ThemedMenuItem { text: qsTr("Delete…"); visible: poiMenu.src === "library"
                         onTriggered: { deleteLibraryDialog.poi = poiMenu.poi; deleteLibraryDialog.open() } }
        ThemedMenuItem { text: qsTr("Save to library"); visible: poiMenu.src === "device"
                         onTriggered: root.keep([root.deviceItem(poiMenu.poi)], r => {
                             root.libraryTone = r.ok ? "success" : "error"
                             root.libraryNote = root.keptText(r, 1)
                         }) }
        ThemedMenuItem { text: qsTr("Remove from the watch…")
                         visible: poiMenu.src === "device" && !HomeViewModel.isGarmin
                                  && DeviceCapabilities.supportsPoiRemove
                         enabled: !PoiService.removing
                         onTriggered: { removePoiDialog.poi = poiMenu.poi; removePoiDialog.open() } }
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
        width: 480
        spacing: Theme.spacingMedium

        // --- Add ---
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                Row {
                    spacing: Theme.spacingSmall
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Add a POI"); font.bold: true; color: Theme.text
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
                }

                // The whole picker lives right here, no dialog - André, 2026-08-11, after
                // trying the button-plus-window version: "delete pick on a map ... and just
                // put everything you had on the window that open, directly on POI screen".
                // The content is the late PoiPickerDialog's, verbatim: instruction, name,
                // search, coordinates, and the crosshair map with drag-to-pick - the same
                // interaction as Settings' Kailash home picker, just not behind a click.
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("Drag the map to move it, right-click a spot and choose " +
                                "Point here. Click the map, then scroll to zoom.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                }

                // One line: the type dropdown (carrying the selected type's icon as its own
                // leading logo), the name, and the place search - all three inline (André,
                // 2026-08-13: "type + name + search all inline").
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
                    // Name - between the type and the search on the same line.
                    RoundedTextField {
                        id: poiNameField
                        anchors.verticalCenter: parent.verticalCenter
                        width: 124
                        // The watch stores at most 15 bytes for the name (ambit_format.py
                        // MAX_NAME_BYTES, which truncates on write) - cap the field so what you
                        // type is what the watch keeps, instead of a silent trim (André, 2026-08-13).
                        maximumLength: 15
                        placeholderText: qsTr("Name this POI")
                        onTextChanged: root.poiName = text
                    }
                    // Place search - fills the rest of the same line.
                    PlaceSearchBar {
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - poiTypeBox.width - poiNameField.width
                               - (Theme.spacingSmall * 2)
                        onPlaceChosen: (lat, lon) => {
                            root.poiLat = lat
                            root.poiLon = lon
                            addMap.resetView()
                        }
                    }
                }

                // #10: the watch keeps at most 15 characters - say so when the user hits it,
                // rather than the field just refusing more keystrokes silently.
                Text {
                    visible: poiNameField.length >= 15
                    text: qsTr("Names can be up to 15 characters.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
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
                                root.poiLat = v
                                root.coordError = ""
                                addMap.resetView()
                            } else {
                                // #10: don't silently revert - say why.
                                root.coordError = qsTr("Latitude must be a number between -90 and 90.")
                                text = root.poiLat.toFixed(6)
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
                                root.poiLon = v
                                root.coordError = ""
                                addMap.resetView()
                            } else {
                                // #10: don't silently revert - say why.
                                root.coordError = qsTr("Longitude must be a number between -180 and 180.")
                                text = root.poiLon.toFixed(6)
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

                Item {
                    width: parent.width
                    height: 300

                    MapView {
                        id: addMap
                        // scrollZoom deliberately OFF here: this map is embedded mid-way down a
                        // scrolling page (PageFlickable), and MapView's scroll-to-zoom
                        // WheelHandler swallows the wheel, so with it on the page couldn't be
                        // scrolled past this 300px map in a short/windowed window (André,
                        // 2026-09-05 - "on poi screen it doesn't scroll fully unless full
                        // screen"). Zoom is still available via the map's own +/- buttons.
                        // Full-screen/dedicated maps (MapWindow, OfflineMaps, Plan) keep
                        // scrollZoom because there is no page to scroll past them.
                        // 2026-11-20: wheel zoom on, after a click (inScrollingPage).
                        inScrollingPage: true
                        // Left drag moves the map (MapView's own drag); right-click opens
                        // "Point here" (André, 2026-11-21: "click to drag, right click, menu
                        // point here for pointing the poi"). The pin sits on the POI itself,
                        // not the map's centre, so it stays put while the map moves.
                        anchors.fill: parent
                        clip: true
                        latitude: root.poiLat
                        longitude: root.poiLon
                        zoomLevel: 15
                        markers: [{ lat: root.poiLat, lon: root.poiLon }]

                        TapHandler {
                            acceptedButtons: Qt.RightButton
                            onTapped: (event) => {
                                pointMenu.lat = addMap.latAtY(event.position.y)
                                pointMenu.lon = addMap.lonAtX(event.position.x)
                                pointMenu.popup()
                            }
                        }
                        ThemedMenu {
                            id: pointMenu
                            property real lat: 0
                            property real lon: 0
                            ThemedMenuItem {
                                text: qsTr("Point here")
                                onTriggered: {
                                    root.poiLat = pointMenu.lat
                                    root.poiLon = pointMenu.lon
                                    addMap.resetView()   // centre on the new POI
                                }
                            }
                        }
                    }
                }

                // Real, 2026-08-08 ("routes/POI we maintain the same feature... but on the
                // modes that send to the watch, they only should send for sdcard, check
                // android app") - same rule and same warning as Routes' own Send button,
                // see RoutesPage.qml's own comment on this for the full reasoning.
                // Real request 2026-08-08 (real hardware confirmed working): no frame at
                // all - plain text matching this page's own muted description text, not a
                // boxed warning.
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

                // "Save to library" keeps it in Sommet only. A send to a watch or SD card
                // keeps a copy in the library too: every POI made here is kept (André,
                // 2026-11-22), and the library and the watch then live their own lives.
                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        text: qsTr("Save to library")
                        onClicked: root.keep([root.formItem()], r => {
                            root.formTone = r.ok ? "success" : "error"
                            root.formNote = root.keptText(r, 1)
                        })
                    }
                    RoundedButton {
                        visible: root.deviceHere
                        text: HomeViewModel.isGarmin ? qsTr("Send to SD card") : qsTr("Send to watch")
                        enabled: HomeViewModel.isGarmin ? GarminService.hasSdCard : !PoiService.adding
                        onClicked: {
                            root.formNote = ""
                            root.sendToDevice(root.poiName, root.poiLat, root.poiLon, root.poiType)
                            if (root.poiName.trim().length > 0)
                                root.keep([root.formItem()], r => {
                                    if (!r.ok) { root.formTone = "error"; root.formNote = root.keptText(r, 1) }
                                })
                        }
                    }
                }

                StatusNote {
                    width: parent.width
                    text: root.formNote
                    tone: root.formTone
                    autoHideMs: 10000
                }
                StatusNote {
                    width: parent.width
                    text: HomeViewModel.isGarmin || root.sentFrom !== "form" ? ""
                        : PoiService.adding ? qsTr("Sending the POI to the watch...")
                        : PoiService.addResultText
                    tone: PoiService.adding ? "working" : PoiService.addOk ? "success" : "error"
                    autoHideMs: 10000
                }
                StatusNote {
                    width: parent.width
                    text: HomeViewModel.isGarmin && root.sentFrom === "form" ? GarminService.writeError : ""
                    tone: "error"
                }
                StatusNote {
                    width: parent.width
                    text: HomeViewModel.isGarmin && root.sentFrom === "form" && GarminService.writeOk
                          && GarminService.writeError.length === 0 ? qsTr("Sent to the SD card.") : ""
                    tone: "success"
                }
            }
        }

        // --- Import from GPX - real, confirmed-working on the Android app
        // (opensportsync-main's "POI import (GPX file and manual coordinates)"), missing
        // here until 2026-08-07. Parses real <wpt> waypoints; since 2026-08-11 each Add is
        // a real watch write (write_nav.py addpoi), same as manual entry above. ---
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                Text { text: qsTr("Import from GPX"); font.bold: true; color: Theme.text }

                RoundedButton {
                    text: qsTr("Import GPX…")
                    onClicked: poiFileDialog.open()
                }

                StatusNote {
                    width: parent.width
                    text: PoiService.importError
                    tone: "error"
                }
                StatusNote {
                    width: parent.width
                    text: root.importNote
                    tone: root.importNote.indexOf(qsTr("Couldn't")) === 0 ? "error" : "success"
                }

                Repeater {
                    model: PoiService.importedPois
                    delegate: Row {
                        width: parent.width
                        spacing: Theme.spacingSmall
                        Text {
                            width: parent.width - (addImportedButton.visible ? addImportedButton.width + Theme.spacingSmall : 0)
                            anchors.verticalCenter: parent.verticalCenter
                            elide: Text.ElideRight
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeLabel
                            text: qsTr("%1  (%2, %3)")
                                .arg(modelData.name)
                                .arg(modelData.lat.toFixed(5))
                                .arg(modelData.lon.toFixed(5))
                        }
                        RoundedButton {
                            id: addImportedButton
                            visible: root.deviceHere
                            text: HomeViewModel.isGarmin ? qsTr("Send to SD card") : qsTr("Send to watch")
                            enabled: HomeViewModel.isGarmin ? GarminService.hasSdCard : !PoiService.adding
                            // The watch's plain type (a number from the other family's list
                            // would draw another icon), and the file's elevation.
                            onClicked: root.sendToDevice(modelData.name, modelData.lat, modelData.lon,
                                                         DeviceCapabilities.poiTypeDefault, modelData.ele)
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

        // --- The POI list: ONE source at a time, picked in the drop-down, as on the Routes page:
        // the connected device, or the Library (always there). ---
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
                // The source drop-down, then the map/list view control (moved here from
                // Settings, André 2026-08-16; still persisted via Theme.poisView).
                Row {
                    width: parent.width
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
                    ViewModeToggle {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: !onDevicePoiCard.loading && onDevicePoiCard.items.length > 0
                        mode: Theme.poisView
                        onChosen: (m) => Theme.poisView = m
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
                Text {
                    visible: onDevicePoiCard.fromDevice && !onDevicePoiCard.loading
                             && onDevicePoiCard.items.length === 0
                             && (HomeViewModel.isGarmin || PoiService.ok)
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
                // What the library is, in one line (André, 2026-11-22: "library is one thing,
                // device library is another.. independent").
                Text {
                    visible: !onDevicePoiCard.fromDevice && onDevicePoiCard.items.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("Your POIs, kept in Sommet. A watch has its own list: send it the " +
                               "ones you want there. Deleting here leaves the watch as it is.")
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
                // A POI sent from the library: the same notes as under the Add card, here
                // where the button is.
                StatusNote {
                    width: parent.width
                    text: onDevicePoiCard.fromDevice || root.sentFrom !== "list" ? ""
                        : HomeViewModel.isGarmin
                          ? (GarminService.writeError.length > 0 ? GarminService.writeError
                             : GarminService.writeOk ? qsTr("Sent to the SD card.") : "")
                        : PoiService.adding ? qsTr("Sending the POI to the watch...")
                        : PoiService.addResultText
                    tone: HomeViewModel.isGarmin
                          ? (GarminService.writeError.length > 0 ? "error" : "success")
                          : PoiService.adding ? "working" : PoiService.addOk ? "success" : "error"
                    autoHideMs: 10000
                }

                Repeater {
                    model: onDevicePoiCard.loading ? [] : onDevicePoiCard.items
                    delegate: Column {
                        id: poiRow
                        width: parent.width
                        spacing: Theme.spacingSmall
                        // The icon and its name: a library POI keeps the list its number is in;
                        // one read off the device is numbered in that device's own list.
                        readonly property var typeInfo: onDevicePoiCard.fromDevice
                            ? DeviceCapabilities.poiTypeOf(modelData.type)
                            : DeviceCapabilities.poiTypeIn(modelData.typeList, modelData.type)

                        Item {
                            // Map hidden in "list" view (Settings -> POIs view) - André, 2026-08-16.
                            visible: Theme.poisView === "map"
                            width: parent.width
                            height: visible ? 120 : 0
                            MapView {
                                anchors.fill: parent
                                inScrollingPage: true
                                latitude: modelData.lat
                                longitude: modelData.lon
                                zoomLevel: 14
                                showMarker: true
                            }
                        }

                        Row {
                            width: parent.width
                            spacing: Theme.spacingSmall

                            // The POI's own watch icon, per type (André: "we miss the icons of
                            // type of poi").
                            Text {
                                id: onWatchPoiGlyph
                                anchors.verticalCenter: parent.verticalCenter
                                font.family: Icons.fontFamily
                                font.pixelSize: Theme.fontSizeBody + 3
                                color: Theme.primary
                                text: poiRow.typeInfo.glyph
                                // Hover: the type's name in the watch's own list.
                                HoverHandler { id: poiRowTypeHover }
                                ToolTip.visible: poiRowTypeHover.hovered
                                ToolTip.text: poiRow.typeInfo.name
                            }
                            Text {
                                width: parent.width - onWatchPoiGlyph.width - onWatchPoiType.width
                                       - poiMainButton.width - poiMoreButton.width
                                       - Theme.spacingSmall * 4
                                anchors.verticalCenter: parent.verticalCenter
                                elide: Text.ElideRight
                                text: modelData.name
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeBody
                                font.bold: true
                            }
                            // The type's name in the watch's own list ("Big game", "Camp" ...).
                            Text {
                                id: onWatchPoiType
                                anchors.verticalCenter: parent.verticalCenter
                                text: poiRow.typeInfo.name
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeCaption
                            }

                            // On the device: Export. In the library: send it to the connected
                            // device (Export is in the ⋯ menu), or Export when nothing is
                            // connected.
                            RoundedButton {
                                id: poiMainButton
                                readonly property bool sends: !onDevicePoiCard.fromDevice && root.deviceHere
                                text: !sends ? qsTr("Export")
                                    : HomeViewModel.isGarmin ? qsTr("Send to SD card") : qsTr("Send to watch")
                                enabled: !sends ? true
                                    : HomeViewModel.isGarmin ? GarminService.hasSdCard : !PoiService.adding
                                onClicked: {
                                    if (!sends) { root.exportPoi(modelData); return }
                                    root.libraryNote = ""
                                    root.sendToDevice(modelData.name, modelData.lat, modelData.lon,
                                                      modelData.typeHere, modelData.ele, "list")
                                }
                            }
                            RoundedButton {
                                id: poiMoreButton
                                text: "⋯"
                                onClicked: {
                                    poiMenu.poi = modelData
                                    poiMenu.src = root.source
                                    poiMenu.withExport = poiMainButton.sends
                                    poiMenu.popup(poiMoreButton, 0, poiMoreButton.height)
                                }
                            }
                        }
                    }
                }
            }
        }
    }

}
