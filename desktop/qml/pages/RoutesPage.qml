import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import AmbitApp

// Step 8. Real on-watch route list (parsed from write_nav.py's own summary output) + real
// Import/Upload flow. "Drag & Drop GPX" stays future per the spec itself; a file picker
// covers Import GPX for real today. Export (real, 2026-08-07 - see RouteService's own header
// comment) opens a real save dialog per route, defaulting to the Downloads folder.
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    Component.onCompleted: {
        // Real, 2026-08-22: Ambit1/2 predate the SBEM nav database RouteService reads -
        // calling it against a connected Ambit1 got a real "ok: false" (correct - that
        // family doesn't have this mechanism), same fix as Watch Settings/Sport Modes.
        if (DeviceCapabilities.supportsRoutes)
            RouteService.refresh()
        GarminService.refreshDeviceGpx()
    }

    // Which on-watch route the last "Export" tap was for - the backend response
    // (exportedGpx) doesn't carry the route's name back, so it's kept here to suggest a
    // real filename in the save dialog below rather than a generic one.
    property int pendingExportIndex: -1
    property string pendingExportName: ""
    property string saveError: ""

    FileDialog {
        id: fileDialog
        title: qsTr("Import GPX")
        nameFilters: [qsTr("GPX files (*.gpx)")]
        onAccepted: RouteService.loadGpxFile(selectedFile)
    }

    // Real request 2026-08-07: "export as gpx and open window to select where to save,
    // where default location is downloads folder" - matches the real Android app's own
    // save-to-Downloads behavior as closely as a real desktop save dialog can (Android
    // saves there directly with no picker at all; a picker defaulting there is the honest
    // desktop equivalent, not a lesser version of it).
    FileDialog {
        id: exportDialog
        title: qsTr("Export route as GPX")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("GPX files (*.gpx)")]
        currentFolder: LocalFileService.downloadsLocation
        // Garmin's own on-device routes already carry their real GPX text (no fetch step
        // needed, see the Export button's own onClicked below) - set right before open()
        // and cleared after use so a later Ambit export doesn't accidentally reuse it.
        property string pendingGpxOverride: ""
        onAccepted: {
            const gpx = pendingGpxOverride.length > 0 ? pendingGpxOverride : RouteService.exportedGpx
            root.saveError = LocalFileService.saveText(selectedFile, gpx)
            pendingGpxOverride = ""
        }
    }

    Connections {
        target: RouteService
        function onExportedGpxChanged() {
            if (RouteService.exportedGpx.length === 0) return
            const safeName = root.pendingExportName.replace(/[\\/:*?"<>|]/g, "_")
            exportDialog.currentFile = LocalFileService.downloadsLocation + "/" + safeName + ".gpx"
            exportDialog.open()
        }
    }

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: 520
        spacing: Theme.spacingMedium

        // --- Import / Upload ---
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                // Title + a "little i" that opens the route-planner help dialog (André,
                // 2026-08-16). Same tap-to-open "i" affordance as SettingsPage's orbital info.
                Row {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Import a route")
                        font.bold: true
                        color: Theme.text
                    }
                    Rectangle {
                        id: routeInfoBadge
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
                            onClicked: routePlannerDialog.open()
                        }
                    }
                }

                RoundedButton {
                    text: qsTr("Import GPX…")
                    onClicked: fileDialog.open()
                }

                // Thumbnail map preview - real request 2026-08-07 ("the bug of no map
                // while not importing gpx persists... use pc location"): always visible now,
                // not just once a file's loaded - centers on WeatherService's own IP-detected
                // location (same default HomePage/PoisPage already use) until a real track
                // exists, instead of hiding the map entirely or showing (0, 0), the Gulf of
                // Guinea, empty ocean either way.
                Item {
                    width: parent.width
                    height: 160

                    MapView {
                        anchors.fill: parent
                        readonly property var center:
                            RouteViewModel.trackCenter(RouteService.pendingRoute.track)
                        latitude: center ? center.lat : WeatherService.latitude
                        longitude: center ? center.lon : WeatherService.longitude
                        zoomLevel: center ? 12 : 10
                        trackPoints: RouteService.pendingRoute.track || []
                        // André, R2: clicking a map opens the big one.
                        TapHandler {
                            onTapped: {
                                bigMap.trackPoints = RouteService.pendingRoute.track || []
                                bigMap.markers = []
                                bigMap.trackTitle = RouteService.pendingRoute.name || ""
                                bigMap.open()
                            }
                        }
                    }
                }

                Text {
                    visible: RouteService.pendingRoute.name !== undefined
                    text: RouteService.pendingRoute.name || ""
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBody
                }

                // Real, 2026-08-08 ("routes/POI we maintain the same feature... but on the
                // modes that send to the watch, they only should send for sdcard, check
                // android app"): GARMIN_USB_IMPORT_SPEC.md's own explicit, non-negotiable
                // rule - never write to a Garmin's internal memory, no exceptions - and its
                // own explicit instruction that the UI must show a visible warning, not just
                // enforce it silently. GarminService.writeGpxToDevice() also enforces this
                // itself (real, not UI-only), this is the "warn the user" half of the rule.
                // Real request 2026-08-08 (real hardware confirmed working): no frame at
                // all - plain text matching the on-device route list's own description text
                // just below (mutedText, 11px), not a boxed warning.
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
                                "route is disabled. Writing to internal memory can " +
                                "break your device.")
                }

                Row {
                    visible: RouteService.pendingRoute.name !== undefined
                    spacing: Theme.spacingSmall

                    RoundedButton {
                        visible: !HomeViewModel.isGarmin
                        text: qsTr("Rehearse (no write)")
                        onClicked: RouteService.uploadPendingRoute(false)
                    }
                    RoundedButton {
                        text: HomeViewModel.isGarmin ? qsTr("Send to SD card") : qsTr("Upload to watch")
                        enabled: !HomeViewModel.isGarmin || GarminService.hasSdCard
                        onClicked: {
                            if (HomeViewModel.isGarmin) {
                                const safeName = (RouteService.pendingRoute.name || "route")
                                    .replace(/[\\/:*?"<>|]/g, "_")
                                GarminService.writeGpxToDevice(
                                    safeName + ".gpx", RouteService.pendingRouteGpxText)
                            } else {
                                RouteService.uploadPendingRoute(true)
                            }
                        }
                    }
                }

                Text {
                    visible: !HomeViewModel.isGarmin && RouteService.uploadResultText.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontSizeCaption
                    color: RouteService.uploadOk ? Theme.success : Theme.error
                    text: RouteService.uploadResultText
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

        // --- On the watch/device - real, 2026-08-08: device-aware, same structure either
        // way (RouteService.onWatchRoutes for Ambit, GarminService.onDeviceRoutes for
        // Garmin - both share the same {name, pointCount, distanceMeters, ascentMeters,
        // descentMeters, track} shape on purpose). Garmin's own export needs no async fetch
        // step: its route objects already carry gpxText straight from the local file
        // that's already been read, unlike Ambit's own routes which need a real backend/USB
        // round trip per Export tap (see RouteService.exportRoute()). ---
        // Real, 2026-08-22: Ambit1/2 routes read via /api/legacy/settings' own routes[]
        // (tools/legacy_link.py -> openambit's libambit_navigation_read - see
        // tools/vendor/openambit_libambit/README.md). Read-only: writing a route back is
        // openambit's libambit_navigation_write, real but never hardware-tested by this
        // project for this device family - see the ambit-app-hardware-fleet-check memory.
        Card {
            id: legacyRoutesCard
            width: 560
            visible: HomeViewModel.connected && !HomeViewModel.isGarmin && !DeviceCapabilities.supportsRoutes

            property bool loading: false
            property string error: ""
            property var routes: []

            function refresh() {
                legacyRoutesCard.loading = true
                legacyRoutesCard.error = ""
                const xhr = new XMLHttpRequest()
                xhr.onreadystatechange = function() {
                    if (xhr.readyState !== XMLHttpRequest.DONE) return
                    legacyRoutesCard.loading = false
                    let d = null
                    try { d = JSON.parse(xhr.responseText) } catch (e) {}
                    if (!d || !d.ok) {
                        legacyRoutesCard.routes = []
                        legacyRoutesCard.error = (d && d.error) ? d.error : qsTr("Couldn't read routes from the watch.")
                        return
                    }
                    legacyRoutesCard.routes = d.routes || []
                }
                xhr.open("GET", "http://127.0.0.1:8766/api/legacy/settings")
                xhr.send()
            }
            onVisibleChanged: if (visible && routes.length === 0 && error === "" && !loading) refresh()

            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                Text { text: qsTr("On the watch"); font.bold: true; color: Theme.text }
                Text { visible: legacyRoutesCard.loading; color: Theme.mutedText; text: qsTr("Reading routes off the watch...") }
                ErrorBanner {
                    width: parent.width
                    detail: legacyRoutesCard.error
                    context: qsTr("reading legacy routes")
                    canRetry: true
                    onRetry: legacyRoutesCard.refresh()
                }
                Text {
                    visible: !legacyRoutesCard.loading && legacyRoutesCard.error === "" && legacyRoutesCard.routes.length === 0
                    color: Theme.mutedText
                    text: qsTr("No routes on this watch.")
                }
                Repeater {
                    model: legacyRoutesCard.routes
                    delegate: Column {
                        width: parent.width
                        spacing: 2
                        Text { color: Theme.text; font.bold: true; text: modelData.name }
                        Text {
                            color: Theme.mutedText
                            text: qsTr("%1 points, %2 m, +%3/-%4 m")
                                .arg(modelData.points_count).arg(modelData.distance_m)
                                .arg(modelData.altitude_asc_m).arg(modelData.altitude_dec_m)
                        }
                    }
                }
            }
        }

        Card {
            id: onDeviceCard
            visible: HomeViewModel.isGarmin || DeviceCapabilities.supportsRoutes
            width: parent.width
            readonly property bool loading:
                HomeViewModel.isGarmin ? GarminService.deviceGpxLoading : RouteService.loading
            readonly property var onDeviceRoutes:
                HomeViewModel.isGarmin ? GarminService.onDeviceRoutes : RouteService.onWatchRoutes
            // In-page sort (André, 2026-08-16). Routes have no on-device upload timestamp, so
            // the keys are name/distance/ascent. CRUCIAL: export is by ORIGINAL list index
            // (RouteService.exportRoute(index) / pendingExportIndex), so each sorted item
            // carries its own `_origIndex` and the delegate exports/compares by THAT, never the
            // Repeater's own post-sort index.
            property string routeSortKey: "name"
            readonly property var sortedRoutes: {
                var list = (onDeviceCard.onDeviceRoutes || []).map(function(r, i) {
                    return { r: r, _origIndex: i }
                })
                var key = onDeviceCard.routeSortKey
                if (key === "distance")
                    list.sort(function(a, b) { return (b.r.distanceMeters || 0) - (a.r.distanceMeters || 0) })
                else if (key === "ascent")
                    list.sort(function(a, b) { return (b.r.ascentMeters || 0) - (a.r.ascentMeters || 0) })
                else
                    list.sort(function(a, b) { return (a.r.name || "").localeCompare(b.r.name || "") })
                return list
            }
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                // Title + the map/list view control (moved here from Settings, André 2026-08-16;
                // still persisted via Theme.routesView). The control is a text dropdown on the
                // LEFT, right after the title, on the same horizontal line (André, 2026-08-16).
                Item {
                    width: parent.width
                    height: Math.max(onWatchTitle.implicitHeight, 26)
                    Text {
                        id: onWatchTitle
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: HomeViewModel.isGarmin ? qsTr("On the device") : qsTr("On the watch")
                        font.bold: true
                        color: Theme.text
                    }
                    ViewModeToggle {
                        anchors.left: onWatchTitle.right
                        anchors.leftMargin: Theme.spacingMedium
                        anchors.verticalCenter: parent.verticalCenter
                        visible: !onDeviceCard.loading && onDeviceCard.sortedRoutes.length > 0
                        mode: Theme.routesView
                        onChosen: (m) => Theme.routesView = m
                    }
                }

                // Real request 2026-08-08 ("add a loading text while it is loading instead
                // of white") - this Card used to just sit empty until RouteService.refresh()
                // finished, which reads the whole navigation database over USB and can take
                // a real, noticeable moment.
                Text {
                    visible: onDeviceCard.loading
                    color: Theme.mutedText
                    text: qsTr("Reading routes off the %1...")
                        .arg(HomeViewModel.isGarmin ? qsTr("device") : qsTr("watch"))
                }

                Text {
                    visible: !HomeViewModel.isGarmin && RouteService.exportError.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("Export failed: %1").arg(RouteService.exportError)
                }
                Text {
                    visible: root.saveError.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("Couldn't save: %1").arg(root.saveError)
                }

                // Sort control (André, 2026-08-16). Name / distance / ascent.
                Row {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    visible: !onDeviceCard.loading && onDeviceCard.sortedRoutes.length > 1
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Sort:")
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    Repeater {
                        model: [
                            { key: "name", label: qsTr("Name") },
                            { key: "distance", label: qsTr("Distance") },
                            { key: "ascent", label: qsTr("Ascent") },
                        ]
                        delegate: Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData.label
                            color: onDeviceCard.routeSortKey === modelData.key ? Theme.primary : Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                            font.bold: onDeviceCard.routeSortKey === modelData.key
                            TapHandler { onTapped: onDeviceCard.routeSortKey = modelData.key }
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                        }
                    }
                }

                Repeater {
                    model: onDeviceCard.loading ? [] : onDeviceCard.sortedRoutes
                    delegate: Column {
                        width: parent.width
                        spacing: Theme.spacingSmall
                        // Sorted item wrapper (see onDeviceCard.sortedRoutes): the real route is
                        // `route`, and `origIndex` is its position in the UNSORTED list - export
                        // must use origIndex, not the Repeater's own index.
                        readonly property var route: modelData.r
                        readonly property int origIndex: modelData._origIndex

                        // Real request 2026-08-08 ("add a map for each gpx") - real points,
                        // not a placeholder: RouteService.onWatchRoutes' own track field now
                        // comes straight from write_nav.py's nav --json (see RouteService's
                        // own comment), the same already-read data the summary below uses,
                        // no extra USB round trip per route. Garmin's own track field comes
                        // straight from the local GPX file already read.
                        Item {
                            // Map hidden in "list" view (Settings -> Routes view), collapsed to
                            // zero height so the list is compact - André, 2026-08-16.
                            visible: Theme.routesView === "map" && route.track && route.track.length > 1
                            width: parent.width
                            height: visible ? 140 : 0
                            MapView {
                                anchors.fill: parent
                                readonly property var center: RouteViewModel.trackCenter(route.track)
                                latitude: center ? center.lat : 0
                                longitude: center ? center.lon : 0
                                trackPoints: route.track || []
                                TapHandler {
                                    onTapped: {
                                        bigMap.trackPoints = route.track || []
                                        bigMap.markers = []
                                        bigMap.trackTitle = route.name || ""
                                        bigMap.open()
                                    }
                                }
                            }
                        }

                        Row {
                            width: parent.width
                            spacing: Theme.spacingSmall

                            Column {
                                width: parent.width - exportButton.width - Theme.spacingSmall
                                spacing: 2
                                Text {
                                    text: route.name
                                    color: Theme.text
                                    font.pixelSize: Theme.fontSizeBody
                                    font.bold: true
                                }
                                Text {
                                    text: qsTr("%1 · %2 points · ascent %3 m · descent %4 m")
                                        .arg(RouteViewModel.formatDistance(route.distanceMeters))
                                        .arg(route.pointCount)
                                        .arg(route.ascentMeters)
                                        .arg(route.descentMeters)
                                    color: Theme.mutedText
                                    font.pixelSize: Theme.fontSizeCaption
                                }
                            }

                            RoundedButton {
                                id: exportButton
                                text: (RouteService.exporting && root.pendingExportIndex === origIndex)
                                    ? qsTr("Exporting...") : qsTr("Export")
                                enabled: !RouteService.exporting
                                onClicked: {
                                    if (HomeViewModel.isGarmin) {
                                        // Already have the real bytes - no fetch step needed.
                                        const safeName = (route.name || "route")
                                            .replace(/[\\/:*?"<>|]/g, "_")
                                        exportDialog.currentFile =
                                            LocalFileService.downloadsLocation + "/" + safeName + ".gpx"
                                        root.saveError = ""
                                        exportDialog.pendingGpxOverride = route.gpxText
                                        exportDialog.open()
                                    } else {
                                        root.pendingExportIndex = origIndex
                                        root.pendingExportName = route.name
                                        RouteService.exportRoute(origIndex)
                                    }
                                }
                            }
                        }
                    }
                }

                Text {
                    // Real bug, found 2026-08-07: this used to say "no routes, or couldn't
                    // read it" either way, so a genuinely empty watch (real, confirmed live -
                    // "routes 0 points 0 waypoints 0") read exactly like an error. Split by
                    // whether lastError actually has anything in it. Garmin has no separate
                    // error concept here - a local file read just finds nothing or it doesn't.
                    visible: !onDeviceCard.loading && onDeviceCard.onDeviceRoutes.length === 0
                             && (HomeViewModel.isGarmin || RouteService.lastError.length === 0)
                    text: qsTr("Nothing to sync.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                }
                ErrorBanner {
                    width: parent.width
                    detail: (!HomeViewModel.isGarmin && !onDeviceCard.loading
                             && RouteService.onWatchRoutes.length === 0)
                            ? RouteService.lastError : ""
                    context: qsTr("reading routes from the watch")
                }
            }
        }
    }

    // One big map for the whole page - real request, 2026-08-11 (André, R2). Shared rather
    // than one per thumbnail: a route list can hold many maps and each would otherwise carry
    // its own dialog and its own tile set.
    MapWindow {
        id: bigMap
        anchors.centerIn: parent
    }

    // Route-planner help (André, 2026-08-16): the "i" next to "Import a route" opens this. The
    // watch imports a GPX; these are the tools that produce one. Links open in the OS browser
    // via the same Qt.openUrlExternally() used for the user manual elsewhere.
    ThemedDialog {
        id: routePlannerDialog
        // Open next to the "i" that triggers it (André, 2026-08-16: "can the screen open more
        // near the i?") - dropped just below the badge rather than centered on the whole window.
        // Parent stays Overlay.overlay (reparenting to the tiny badge stopped it rendering); the
        // position is the badge's own spot mapped into the overlay, computed as it opens.
        parent: Overlay.overlay
        onAboutToShow: {
            const p = routeInfoBadge.mapToItem(Overlay.overlay, 0, routeInfoBadge.height + Theme.spacingSmall);
            x = p.x;
            y = p.y;
        }
        title: qsTr("Plan a route")
        // No footer button row (André, 2026-08-16: "the square seems too big") - the Close sits
        // on the last line's right corner instead, inside the content, to save that vertical space.
        standardButtons: Dialog.NoButton

        contentItem: Item {
            implicitWidth: plannerCol.width
            implicitHeight: plannerCol.height

            Column {
                id: plannerCol
                width: 360
                spacing: Theme.spacingSmall

                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("Routes that are sent to the watch will be erased by SuuntoLink and not pushed to the Suunto app.")
                    color: Theme.text
                    font.bold: true
                    font.pixelSize: Theme.fontSizeBody
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("You can export your routes from Suunto App to gpx and import them here, or you can use any of these or others:")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBody
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
                        width: 360
                        wrapMode: Text.WordWrap
                        textFormat: Text.StyledText
                        linkColor: Theme.primary
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBody
                        text: "•  <a href=\"" + modelData.url + "\">" + modelData.name + "</a>"
                              + (modelData.qual.length > 0 ? " " + modelData.qual : "")
                        onLinkActivated: (link) => Qt.openUrlExternally(link)
                    }
                }
            }

            // Close on the last line's right corner (André, 2026-08-16).
            RoundedButton {
                anchors.right: plannerCol.right
                anchors.bottom: plannerCol.bottom
                text: qsTr("Close")
                onClicked: routePlannerDialog.close()
            }
        }
    }
}
