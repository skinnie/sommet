import QtQuick
import QtQuick.Controls
import AmbitApp

// Pick the Kailash's home location by pointing at it - real request, 2026-08-11 (André):
// "Kailash: remove the home latitude/longitude, call it: home coordinates, open a map (on
// current location), let the user drag the map to 'pick' a point for his home, then show the
// coordinates on the settings side."
//
// The two numbers were never the thing the user wanted to enter; a place was. Latitude and
// longitude are still shown - you need them to sanity-check a coordinate someone gave you -
// but they are now an output of pointing at a map rather than something to type blind.
//
// The POI add flow shares this exact map interaction (crosshair, drag-to-pick, search) but
// lives inline on PoisPage rather than behind a dialog - André's call after using both. Home
// stays a dialog because Settings is a long list; a map embedded mid-list would be lost.
ThemedDialog {
    id: root

    property real latitude: 0
    property real longitude: 0

    // Emitted once, with both values - the watch stores them as ONE grouped field (entry
    // 0x36, Latitude and Longitude sub-fields), so committing them together matches how the
    // device actually holds it rather than sending two half-updates.
    signal picked(real lat, real lon)

    title: qsTr("Home coordinates")
    standardButtons: Dialog.Cancel | Dialog.Ok
    width: Math.min(720, parent ? parent.width - Theme.spacingLarge * 2 : 720)
    height: Math.min(620, parent ? parent.height - Theme.spacingLarge * 2 : 620)

    onOpened: map.resetView()
    onAccepted: picked(latitude, longitude)

    contentItem: Column {
        spacing: Theme.spacingSmall

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: qsTr("Drag the map to put your home under the crosshair, or click a point. " +
                        "Scroll to zoom.")
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeBody
        }

        // Search by name, then fine-tune by hand on the map - André, 2026-08-11.
        PlaceSearchBar {
            width: parent.width
            onPlaceChosen: (lat, lon) => {
                root.latitude = lat
                root.longitude = lon
                map.resetView()
            }
        }

        Row {
            width: parent.width
            spacing: Theme.spacingSmall
            RoundedTextField {
                id: latField
                width: (parent.width - Theme.spacingSmall) / 2
                placeholderText: qsTr("Latitude")
                text: root.latitude.toFixed(6)
                onEditingFinished: {
                    const v = WatchUnits.parseDecimal(text)
                    if (!isNaN(v) && v >= -90 && v <= 90) {
                        root.latitude = v
                        map.resetView()
                    } else {
                        text = root.latitude.toFixed(6)
                    }
                }
            }
            RoundedTextField {
                id: lonField
                width: (parent.width - Theme.spacingSmall) / 2
                placeholderText: qsTr("Longitude")
                text: root.longitude.toFixed(6)
                onEditingFinished: {
                    const v = WatchUnits.parseDecimal(text)
                    if (!isNaN(v) && v >= -180 && v <= 180) {
                        root.longitude = v
                        map.resetView()
                    } else {
                        text = root.longitude.toFixed(6)
                    }
                }
            }
        }

        Item {
            width: parent.width
            height: root.height - 290

            MapView {
                id: map
                scrollZoom: true
                anchors.fill: parent
                clip: true
                latitude: root.latitude
                longitude: root.longitude
                zoomLevel: 14
                showMarker: true

                TapHandler {
                    onTapped: (event) => {
                        // Through the map's own inverse projection - the same maths the tiles
                        // are drawn with, rather than a second approximation that would drift
                        // from what is on screen.
                        root.latitude = map.latAtY(event.position.y)
                        root.longitude = map.lonAtX(event.position.x)
                        map.resetView()
                    }
                }
                DragHandler {
                    id: panner
                    target: null
                    property real lastX: 0
                    property real lastY: 0
                    onActiveChanged: {
                        if (active) {
                            lastX = centroid.position.x
                            lastY = centroid.position.y
                        } else {
                            // "let the user drag the map to pick a point": on release, the
                            // point now under the centre crosshair IS the choice. Without
                            // this, dragging would only scroll and the user would still have
                            // to click, which is not what was asked for.
                            root.latitude = map.latAtY(map.height / 2)
                            root.longitude = map.lonAtX(map.width / 2)
                            map.resetView()
                        }
                        map.userControlled = true
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
                    cursorShape: panner.active ? Qt.ClosedHandCursor : Qt.CrossCursor
                }

                // The crosshair the drag aims at. Drawn over the map, ignoring input so it
                // never eats a click meant for the map underneath.
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
    }
}
