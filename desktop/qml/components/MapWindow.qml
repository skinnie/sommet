import QtQuick
import QtQuick.Controls
import SommetUi

// The big map - real request, 2026-08-11 (André, R2): "Routes when we click on the map, open
// a window with a map zoomed and the option to zoom in, zoom out, move the app, with mouse.
// Plus (if easy): also with the arrows on keyboard. and + for zoom in - for zoom out."
//
// A Dialog rather than a separate Window on purpose: this app already learned live
// (HomePage's time-sync menu, three rounds of it) that Overlay.overlay is null here, so
// anything relying on a floating layer gets fragile. A Dialog parented to the page is the
// shape that has actually worked in this app.
//
// Panning moves MapView's own panX/panY rather than its latitude/longitude, so the track and
// markers move with the tiles - see MapView's own comment on why that is the single right
// place for it.
ThemedDialog {
    id: root

    property var trackPoints: []
    property var markers: []
    property string trackTitle: ""

    title: trackTitle.length > 0 ? trackTitle : qsTr("Map")
    standardButtons: Dialog.Close
    width: Math.min(900, parent ? parent.width - Theme.spacingLarge * 2 : 900)
    height: Math.min(680, parent ? parent.height - Theme.spacingLarge * 2 : 680)

    // Arrow keys pan, +/- zoom. One screenful of arrow key is deliberately a chunk of the
    // view rather than a few pixels, so holding a key is not the only way to get anywhere.
    readonly property int keyPanStep: 80

    onOpened: {
        map.resetView()
        mapFocus.forceActiveFocus()
    }

    contentItem: FocusScope {
        id: mapFocus
        focus: true

        Keys.onPressed: (event) => {
            switch (event.key) {
            case Qt.Key_Left:   map.panX -= root.keyPanStep; map.userControlled = true; break
            case Qt.Key_Right:  map.panX += root.keyPanStep; map.userControlled = true; break
            case Qt.Key_Up:     map.panY -= root.keyPanStep; map.userControlled = true; break
            case Qt.Key_Down:   map.panY += root.keyPanStep; map.userControlled = true; break
            case Qt.Key_Plus:
            case Qt.Key_Equal:  map.zoomBy(1); break
            case Qt.Key_Minus:  map.zoomBy(-1); break
            case Qt.Key_0:      map.resetView(); break
            default: return
            }
            event.accepted = true
        }

        MapView {
            id: map
            scrollZoom: true
            anchors.fill: parent
            trackPoints: root.trackPoints
            markers: root.markers
            showZoomControls: true
            clip: true

            // Drag to pan. Panning is the inverse of the drag: dragging the map left should
            // reveal what is to the right, which is what moving the origin the other way
            // does.
            DragHandler {
                id: dragHandler
                target: null
                property real lastX: 0
                property real lastY: 0
                onActiveChanged: {
                    if (active) {
                        lastX = centroid.position.x
                        lastY = centroid.position.y
                        map.userControlled = true
                    }
                }
                onCentroidChanged: {
                    if (!active)
                        return
                    map.panX -= centroid.position.x - lastX
                    map.panY -= centroid.position.y - lastY
                    lastX = centroid.position.x
                    lastY = centroid.position.y
                }
            }


            HoverHandler {
                cursorShape: dragHandler.active ? Qt.ClosedHandCursor : Qt.OpenHandCursor
            }
        }

        Text {
            anchors.left: parent.left
            anchors.bottom: parent.bottom
            anchors.margins: Theme.spacingSmall
            text: qsTr("Drag to move · scroll or +/- to zoom · arrows to move · 0 to reset")
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeCaption
        }

        // Offline download - real, 2026-08-11 (André: "put this offline map cache in the
        // desktop version", matching Android's own TileCache.ts download button on
        // MapScreen.tsx). trackPoints + markers together is the same union MapView.qml's own
        // _trackBounds already fits the view to, so "this area" means the same area the map
        // is actually showing.
        Column {
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: Theme.spacingSmall
            spacing: Theme.spacingSmall / 2

            RoundedButton {
                anchors.right: parent.right
                text: TileCacheService.downloading
                      ? qsTr("Downloading %1/%2…").arg(TileCacheService.downloadDone).arg(TileCacheService.downloadTotal)
                      : qsTr("Download for offline")
                enabled: !TileCacheService.downloading && (root.trackPoints.length + root.markers.length) > 0
                onClicked: TileCacheService.downloadRegion(
                    root.trackPoints.concat(root.markers), MapService.provider)
            }

            Text {
                id: statusText
                anchors.right: parent.right
                visible: text.length > 0
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                text: ""

                Timer {
                    id: statusClearTimer
                    interval: 4000
                    onTriggered: statusText.text = ""
                }
                Connections {
                    target: TileCacheService
                    function onDownloadFinished(done, total, failed) {
                        statusText.text = failed > 0
                            ? qsTr("Saved %1/%2 tiles for offline use").arg(done - failed).arg(total)
                            : qsTr("Saved %1 tiles for offline use").arg(total)
                        statusClearTimer.restart()
                    }
                }
            }
        }
    }
}
