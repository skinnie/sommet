import QtQuick
import QtQuick.Controls
import AmbitApp

// Offline maps - the OruxMaps-style "download any area of the world for use with no signal"
// page (André, 2026-08-30: "select zones to download... I can be in France and want to download
// for USA"). Parity with the mobile OfflineMapsScreen: pan/search anywhere, the accent box marks
// exactly what gets saved, pick a detail level, see the size, download. The tiles go into the
// same on-disk cache the map already reads from (TileCacheService + main.cpp's disk cache), so a
// downloaded area then renders with zero network. Reachable from the nav rail, not just a route.
Item {
    id: root

    // z12≈city, z15≈street, z17≈building. Higher detail = exponentially more tiles.
    readonly property var detailPresets: [
        { label: qsTr("Overview (region)"),      zooms: [10, 11, 12, 13] },
        { label: qsTr("Standard (city+streets)"), zooms: [12, 13, 14, 15] },
        { label: qsTr("Detailed (every street)"), zooms: [13, 14, 15, 16, 17] },
    ]
    property int detailIndex: 1
    property int estTiles: 0
    readonly property real avgTileKB: 15

    // The download box is the inset 84% of the map view (matches the drawn rectangle below).
    function boxCorners() {
        var pad = 0.08
        return [
            { lat: map.latAtY(map.height * pad),       lon: map.lonAtX(map.width * pad) },
            { lat: map.latAtY(map.height * (1 - pad)), lon: map.lonAtX(map.width * (1 - pad)) },
        ]
    }
    function refreshEstimate() {
        estTiles = TileCacheService.countRegionTiles(boxCorners(), detailPresets[detailIndex].zooms, 0)
    }

    // Saved areas (from TileCacheService's QSettings-backed list). `pending` remembers what a
    // download is for, so it can be recorded once the download actually finishes.
    property var saved: []
    property var pending: null
    function reloadSaved() { saved = TileCacheService.savedRegions() }
    Component.onCompleted: { refreshEstimate(); reloadSaved() }
    Connections {
        target: TileCacheService
        function onSavedRegionsChanged() { root.reloadSaved() }
        function onDownloadFinished(done, total, failed) {
            if (!root.pending) return
            TileCacheService.saveRegion(root.pending.name, root.pending.provider,
                                        root.pending.corners, root.pending.zooms, root.pending.tileCount)
            root.pending = null
        }
    }

    // ── Header ──
    Column {
        id: header
        anchors { top: parent.top; left: parent.left; right: parent.right; margins: Theme.spacingLarge }
        spacing: Theme.spacingSmall

        Text {
            text: qsTr("Offline maps")
            color: Theme.text; font.pixelSize: Theme.fontSizeLargeTitle; font.bold: true
        }
        Text {
            text: qsTr("Pan or search to any area in the world, frame it in the box, and download it for use with no signal.")
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
            width: parent.width; wrapMode: Text.WordWrap
        }
        PlaceSearchBar {
            width: parent.width
            onPlaceChosen: (lat, lon) => { map.centerOn(lat, lon, 11); root.refreshEstimate() }
        }
    }

    // ── Map with the selection box ──
    Rectangle {
        id: mapFrame
        anchors {
            top: header.bottom; bottom: bottomBar.top
            left: parent.left; right: parent.right; margins: Theme.spacingLarge
        }
        color: Theme.cardNested
        border.color: Theme.border
        radius: Theme.radiusSmall
        clip: true

        MapView {
            id: map
            anchors.fill: parent
            scrollZoom: true
            showZoomControls: true
            latitude: 40; longitude: -3; zoomLevel: 4

            DragHandler {
                target: null
                property real lastX: 0
                property real lastY: 0
                onActiveChanged: {
                    if (active) { lastX = centroid.position.x; lastY = centroid.position.y; map.userControlled = true }
                    else root.refreshEstimate()
                }
                onCentroidChanged: {
                    if (!active) return
                    map.panX -= centroid.position.x - lastX
                    map.panY -= centroid.position.y - lastY
                    lastX = centroid.position.x
                    lastY = centroid.position.y
                }
            }
            HoverHandler { cursorShape: Qt.OpenHandCursor }

            Connections {
                target: map
                function onCurrentZoomChanged() { root.refreshEstimate() }
            }
        }

        // The area that will be saved.
        Rectangle {
            anchors.fill: parent
            anchors.margins: Math.min(parent.width, parent.height) * 0.08
            color: "transparent"
            border.color: Theme.accent
            border.width: 2
            radius: Theme.radiusSmall
        }
    }

    // ── Controls (left) + saved areas (right) ──
    Rectangle {
        id: bottomBar
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        color: Theme.card
        border.color: Theme.border
        implicitHeight: 240

        Row {
            anchors { fill: parent; margins: Theme.spacingLarge }
            spacing: Theme.spacingLarge

            // Download controls
            Column {
                id: controls
                width: (parent.width - Theme.spacingLarge) * 0.55
                spacing: Theme.spacingMedium

                Row {
                    spacing: Theme.spacingMedium
                    Column {
                        spacing: 4
                        Text { text: qsTr("Map"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        RoundedComboBox {
                            width: 170
                            model: [MapService._providers.osm.name, MapService._providers.cyclosm.name, MapService._providers.ign.name]
                            currentIndex: MapService.provider === "osm" ? 0 : MapService.provider === "cyclosm" ? 1 : 2
                            onActivated: (i) => { MapService.provider = ["osm", "cyclosm", "ign"][i]; root.refreshEstimate() }
                        }
                    }
                    Column {
                        spacing: 4
                        Text { text: qsTr("Detail"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        RoundedComboBox {
                            width: 200
                            model: root.detailPresets.map(function (p) { return p.label })
                            currentIndex: root.detailIndex
                            onActivated: (i) => { root.detailIndex = i; root.refreshEstimate() }
                        }
                    }
                }

                RoundedTextField {
                    id: nameField
                    width: parent.width
                    placeholderText: qsTr("Area name (optional)")
                }

                Text {
                    text: root.estTiles > 0
                          ? qsTr("≈ %1 tiles · ~%2 MB").arg(root.estTiles).arg((root.estTiles * root.avgTileKB / 1024).toFixed(root.estTiles * root.avgTileKB / 1024 < 10 ? 1 : 0))
                          : qsTr("Move the map to frame an area.")
                    color: root.estTiles > 20000 ? Theme.accent : Theme.text
                    font.pixelSize: Theme.fontSizeBodyLarge; font.bold: true
                }
                Text {
                    visible: root.estTiles > 20000
                    text: qsTr("That's a lot of tiles — zoom in or pick a lower detail.")
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                }

                Row {
                    spacing: Theme.spacingMedium
                    RoundedButton {
                        text: TileCacheService.downloading
                              ? qsTr("Downloading %1/%2…").arg(TileCacheService.downloadDone).arg(TileCacheService.downloadTotal)
                              : qsTr("Download this area")
                        enabled: !TileCacheService.downloading && root.estTiles > 0 && root.estTiles <= 20000
                        onClicked: {
                            root.pending = {
                                name: nameField.text, provider: MapService.provider,
                                corners: root.boxCorners(), zooms: root.detailPresets[root.detailIndex].zooms,
                                tileCount: root.estTiles
                            }
                            nameField.text = ""
                            TileCacheService.downloadRegion(
                                root.pending.corners, root.pending.provider, root.pending.zooms, 0)
                        }
                    }
                    RoundedButton {
                        text: qsTr("Clear all (%1 MB)").arg((TileCacheService.cacheSizeBytes / 1048576).toFixed(0))
                        enabled: !TileCacheService.downloading && TileCacheService.cacheSizeBytes > 0
                        onClicked: TileCacheService.clearCache()
                    }
                }
            }

            // Saved areas
            Column {
                width: (parent.width - Theme.spacingLarge) * 0.45
                height: parent.height
                spacing: Theme.spacingSmall

                Text {
                    text: root.saved.length > 0 ? qsTr("Saved areas (%1)").arg(root.saved.length) : qsTr("Saved areas")
                    color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.bold: true
                }
                Text {
                    visible: root.saved.length === 0
                    text: qsTr("None yet. Frame an area and download it.")
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                }

                ListView {
                    width: parent.width
                    height: parent.height - Theme.spacingLarge * 2
                    clip: true
                    spacing: Theme.spacingSmall / 2
                    model: root.saved
                    delegate: Rectangle {
                        required property var modelData
                        width: ListView.view.width
                        height: rowCol.implicitHeight + Theme.spacingSmall * 2
                        radius: Theme.radiusSmall
                        color: Theme.cardNested
                        border.color: Theme.border

                        Row {
                            anchors { fill: parent; margins: Theme.spacingSmall }
                            spacing: Theme.spacingSmall

                            Column {
                                id: rowCol
                                width: parent.width - delBtn.width - Theme.spacingSmall
                                spacing: 2
                                Text {
                                    text: modelData.name
                                    color: Theme.text; font.pixelSize: Theme.fontSizeLabel; font.bold: true
                                    elide: Text.ElideRight; width: parent.width
                                }
                                Text {
                                    text: qsTr("z%1–%2 · %3 tiles · ~%4 MB")
                                        .arg(modelData.zooms[0]).arg(modelData.zooms[modelData.zooms.length - 1])
                                        .arg(modelData.tileCount).arg((modelData.bytes / 1048576).toFixed(1))
                                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                }
                            }

                            RoundedButton {
                                id: delBtn
                                anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("Delete")
                                enabled: !TileCacheService.downloading
                                onClicked: TileCacheService.deleteSavedRegion(modelData.id)
                            }
                        }

                        TapHandler {
                            // tap the row (not the button) to jump the map to that area
                            onTapped: map.centerOn((modelData.minLat + modelData.maxLat) / 2,
                                                   (modelData.minLon + modelData.maxLon) / 2, 11)
                        }
                    }
                }
            }
        }
    }
}
