import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import AmbitApp

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
        if (DeviceCapabilities.supportsPOIs)
            PoiService.refresh()
        GarminService.refreshDeviceGpx()
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
        currentFolder: LocalFileService.downloadsLocation
        property string pendingGpx: ""
        onAccepted: root.saveError = LocalFileService.saveText(selectedFile, pendingGpx)
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

    // POI type (the icon the watch shows). The 18 Ambit type bytes, from Lars's
    // "Types of Poi.md" (assets/Lars) - the single source is tools/ambit_format.py's
    // WAYPOINT_TYPES; index in this list == the type id 0-17. Default "Waypoint" (17), what
    // the watch itself uses. The watch renders a distinct icon per type; the app shows the
    // name (the font is subsetted, so no per-type glyph here).
    property int poiType: 17
    readonly property var poiTypeNames: [
        qsTr("Building"), qsTr("Cave"), qsTr("Camp"), qsTr("Car"), qsTr("Crossroads"),
        qsTr("Beginning"), qsTr("End"), qsTr("Food"), qsTr("Forest"), qsTr("Geocache"),
        qsTr("Lodging"), qsTr("Meadow"), qsTr("Mountain"), qsTr("Sight"), qsTr("Road"),
        qsTr("Rock"), qsTr("Water"), qsTr("Waypoint")
    ]

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
                    text: qsTr("Drag the map to put the POI under the crosshair, or click " +
                                "a point. Scroll to zoom.")
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
                        model: root.poiTypeNames
                        currentIndex: root.poiType
                        onActivated: (i) => root.poiType = i
                        // Open the list just below the box instead of on top of it. Overlapping,
                        // the popup's rounded rectangle sat over the box's own rounded rectangle
                        // and the two borders doubled up at the top corners (André, 2026-08-13:
                        // the open dropdown's "corner issue").
                        popup.y: poiTypeBox.height
                        background: Rectangle {
                            implicitHeight: 36
                            radius: Theme.radiusSmall
                            color: Theme.card
                            border.width: 1
                            border.color: Theme.mutedText
                        }
                        // The selected type's own watch icon sits inside, left of the name, and
                        // updates live - the "logo" now lives in the dropdown itself.
                        contentItem: Item {
                            Row {
                                anchors.left: parent.left
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: Theme.spacingSmall
                                Icon {
                                    anchors.verticalCenter: parent.verticalCenter
                                    glyph: Icons.poiTypeGlyphs[poiTypeBox.currentIndex]
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

                Row {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    RoundedTextField {
                        width: (parent.width - Theme.spacingSmall) / 2
                        placeholderText: qsTr("Latitude")
                        text: root.poiLat.toFixed(6)
                        onEditingFinished: {
                            const v = parseFloat(text)
                            if (!isNaN(v) && v >= -90 && v <= 90) {
                                root.poiLat = v
                                addMap.resetView()
                            } else {
                                text = root.poiLat.toFixed(6)
                            }
                        }
                    }
                    RoundedTextField {
                        width: (parent.width - Theme.spacingSmall) / 2
                        placeholderText: qsTr("Longitude")
                        text: root.poiLon.toFixed(6)
                        onEditingFinished: {
                            const v = parseFloat(text)
                            if (!isNaN(v) && v >= -180 && v <= 180) {
                                root.poiLon = v
                                addMap.resetView()
                            } else {
                                text = root.poiLon.toFixed(6)
                            }
                        }
                    }
                }

                Item {
                    width: parent.width
                    height: 300

                    MapView {
                        id: addMap
                        scrollZoom: true
                        anchors.fill: parent
                        clip: true
                        latitude: root.poiLat
                        longitude: root.poiLon
                        zoomLevel: 15
                        showMarker: true

                        TapHandler {
                            onTapped: (event) => {
                                // Through the map's own inverse projection - the same maths
                                // the tiles are drawn with.
                                root.poiLat = addMap.latAtY(event.position.y)
                                root.poiLon = addMap.lonAtX(event.position.x)
                                addMap.resetView()
                            }
                        }
                        DragHandler {
                            id: addMapPanner
                            target: null
                            property real lastX: 0
                            property real lastY: 0
                            onActiveChanged: {
                                if (active) {
                                    lastX = centroid.position.x
                                    lastY = centroid.position.y
                                } else {
                                    // Drag-to-pick, same as the Kailash home picker: on
                                    // release, the point under the crosshair IS the choice.
                                    root.poiLat = addMap.latAtY(addMap.height / 2)
                                    root.poiLon = addMap.lonAtX(addMap.width / 2)
                                    addMap.resetView()
                                }
                                addMap.userControlled = true
                            }
                            onCentroidChanged: {
                                if (!active)
                                    return
                                addMap.panX -= centroid.position.x - lastX
                                addMap.panY -= centroid.position.y - lastY
                                lastX = centroid.position.x
                                lastY = centroid.position.y
                            }
                        }
                        HoverHandler {
                            cursorShape: addMapPanner.active ? Qt.ClosedHandCursor
                                                             : Qt.CrossCursor
                        }

                        // The crosshair the drag aims at. Ignores input so it never eats a
                        // click meant for the map underneath.
                        Item {
                            anchors.centerIn: parent
                            width: 26
                            height: 26
                            Rectangle {
                                anchors.centerIn: parent
                                width: 2; height: parent.height
                                color: Theme.mapAccent
                            }
                            Rectangle {
                                anchors.centerIn: parent
                                width: parent.width; height: 2
                                color: Theme.mapAccent
                            }
                            Rectangle {
                                anchors.centerIn: parent
                                width: 12; height: 12; radius: 6
                                color: "transparent"
                                border.width: 2
                                border.color: Theme.mapAccent
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

                RoundedButton {
                    text: HomeViewModel.isGarmin ? qsTr("Send to SD card") : qsTr("Add")
                    enabled: !HomeViewModel.isGarmin || GarminService.hasSdCard
                    onClicked: {
                        if (HomeViewModel.isGarmin) {
                            const safeName = (root.poiName || "poi").replace(/[\\/:*?"<>|]/g, "_")
                            GarminService.writeGpxToDevice(
                                "Waypoints_" + safeName + ".gpx",
                                PoiService.buildWaypointGpx(root.poiName, root.poiLat, root.poiLon))
                        } else {
                            PoiService.addPoi(root.poiName, root.poiLat, root.poiLon, root.poiType)
                        }
                    }
                }

                Text {
                    visible: !HomeViewModel.isGarmin && PoiService.addResultText.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontSizeCaption
                    color: PoiService.addOk ? Theme.success : Theme.error
                    text: PoiService.addResultText
                }
                Text {
                    visible: HomeViewModel.isGarmin && GarminService.writeError.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontSizeCaption
                    color: Theme.error
                    text: GarminService.writeError
                }
                Text {
                    visible: HomeViewModel.isGarmin && GarminService.writeOk
                             && GarminService.writeError.length === 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontSizeCaption
                    color: Theme.success
                    text: qsTr("Sent to the SD card.")
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

                Text {
                    visible: PoiService.importError.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeCaption
                    text: PoiService.importError
                }

                Repeater {
                    model: PoiService.importedPois
                    delegate: Row {
                        width: parent.width
                        spacing: Theme.spacingSmall
                        Text {
                            width: parent.width - addImportedButton.width - Theme.spacingSmall
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
                            text: HomeViewModel.isGarmin ? qsTr("Send to SD card") : qsTr("Add")
                            enabled: !HomeViewModel.isGarmin || GarminService.hasSdCard
                            onClicked: {
                                if (HomeViewModel.isGarmin) {
                                    const safeName = (modelData.name || "poi").replace(/[\\/:*?"<>|]/g, "_")
                                    GarminService.writeGpxToDevice(
                                        "Waypoints_" + safeName + ".gpx",
                                        PoiService.buildWaypointGpx(modelData.name, modelData.lat, modelData.lon))
                                } else {
                                    PoiService.addPoi(modelData.name, modelData.lat, modelData.lon)
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
        // this family - see the ambit-app-hardware-fleet-check memory.
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
                function onAddOkChanged() {
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
                        Text { color: Theme.text; font.bold: true; text: modelData.name }
                        Text {
                            color: Theme.mutedText
                            text: qsTr("%1, %2  (%3 m)")
                                .arg(modelData.lat.toFixed(5)).arg(modelData.lon.toFixed(5)).arg(modelData.altitude_m)
                        }
                    }
                }
            }
        }

        Card {
            id: onDevicePoiCard
            width: parent.width
            visible: HomeViewModel.isGarmin || DeviceCapabilities.supportsPOIs
            readonly property bool loading:
                HomeViewModel.isGarmin ? GarminService.deviceGpxLoading : PoiService.loading
            readonly property var onDevicePois:
                HomeViewModel.isGarmin ? GarminService.onDevicePois : PoiService.onWatchPois
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                // Title + the map/list view control (moved here from Settings, André 2026-08-16;
                // still persisted via Theme.poisView). Text dropdown on the LEFT, right after the
                // title, on the same horizontal line (André, 2026-08-16).
                Item {
                    width: parent.width
                    height: Math.max(onWatchPoiTitle.implicitHeight, 26)
                    Text {
                        id: onWatchPoiTitle
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: HomeViewModel.isGarmin ? qsTr("On the device") : qsTr("On the watch")
                        font.bold: true
                        color: Theme.text
                    }
                    ViewModeToggle {
                        anchors.left: onWatchPoiTitle.right
                        anchors.leftMargin: Theme.spacingMedium
                        anchors.verticalCenter: parent.verticalCenter
                        visible: !onDevicePoiCard.loading && onDevicePoiCard.onDevicePois.length > 0
                        mode: Theme.poisView
                        onChosen: (m) => Theme.poisView = m
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
                    detail: (!HomeViewModel.isGarmin && !PoiService.loading && !PoiService.ok)
                            ? PoiService.lastError : ""
                    context: qsTr("reading POIs from the watch")
                    // Unplugging the watch mid-session is the usual cause here (real case,
                    // 2026-08-11) - plug it back in and this re-runs the same read.
                    canRetry: true
                    onRetry: PoiService.refresh()
                }
                Text {
                    visible: !onDevicePoiCard.loading && onDevicePoiCard.onDevicePois.length === 0
                             && (HomeViewModel.isGarmin || PoiService.ok)
                    color: Theme.mutedText
                    text: qsTr("Nothing to sync.")
                }
                Text {
                    visible: root.saveError.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("Couldn't save: %1").arg(root.saveError)
                }

                Repeater {
                    model: onDevicePoiCard.loading ? [] : onDevicePoiCard.onDevicePois
                    delegate: Column {
                        width: parent.width
                        spacing: Theme.spacingSmall

                        Item {
                            // Map hidden in "list" view (Settings -> POIs view) - André, 2026-08-16.
                            visible: Theme.poisView === "map"
                            width: parent.width
                            height: visible ? 120 : 0
                            MapView {
                                anchors.fill: parent
                                latitude: modelData.lat
                                longitude: modelData.lon
                                zoomLevel: 14
                                showMarker: true
                            }
                        }

                        Row {
                            width: parent.width
                            spacing: Theme.spacingSmall

                            Text {
                                width: parent.width - poiExportButton.width - Theme.spacingSmall
                                anchors.verticalCenter: parent.verticalCenter
                                elide: Text.ElideRight
                                text: modelData.name
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeBody
                                font.bold: true
                            }

                            RoundedButton {
                                id: poiExportButton
                                text: qsTr("Export")
                                onClicked: {
                                    const safeName = (modelData.name || "poi")
                                        .replace(/[\\/:*?"<>|]/g, "_")
                                    poiExportDialog.pendingGpx = PoiService.buildWaypointGpx(
                                        modelData.name, modelData.lat, modelData.lon)
                                    poiExportDialog.currentFile =
                                        LocalFileService.downloadsLocation + "/" + safeName + ".gpx"
                                    poiExportDialog.open()
                                }
                            }
                        }
                    }
                }
            }
        }
    }

}
