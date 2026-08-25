import QtQuick
import AmbitApp

// AMBITAPP_SPEC.md, "Activities": "Think Apple Photos. Large cards. Small map preview.
// Sport icon. Distance. Duration. Elevation." One card per recorded move.
Card {
    id: root
    width: 360
    padding: 0

    property var activity  // one entry from ActivityService.activities
    signal opened
    // Right-click → Delete (André, 2026-08-25) - the page owns the confirm + the delete.
    signal deleteRequested

    readonly property var _center: ActivityViewModel.trackCenter(activity.track)

    // Real, 2026-08-09 ("general desktop polish pass") - a real, unmet AMBITAPP_SPEC.md
    // requirement ("Subtle animations"): this card had zero feedback that it was even
    // clickable beyond the cursor shape. A small press-scale is a common, well-understood
    // tactile cue, low-risk to add since it's a pure transform, not a layout change.
    scale: cardTap.pressed ? 0.98 : 1.0
    Behavior on scale { NumberAnimation { duration: 100; easing.type: Easing.OutCubic } }

    TapHandler { id: cardTap; onTapped: root.opened() }
    // Right mouse button opens the context menu AT THE CURSOR - same fix as ActivityRow.qml
    // (Menu.popup() with no args opens at the parent's (0,0), which on a 360x280 card meant the
    // menu always appeared pinned to the top-left corner over the map thumbnail).
    TapHandler {
        acceptedButtons: Qt.RightButton
        onTapped: (eventPoint) => cardMenu.popup(eventPoint.position.x, eventPoint.position.y)
    }
    ThemedMenu {
        id: cardMenu
        ThemedMenuItem {
            text: qsTr("Delete activity")
            onTriggered: root.deleteRequested()
        }
    }

    Column {
        width: parent.width

        Item {
            width: parent.width
            height: 160

            MapView {
                anchors.fill: parent
                visible: root._center !== null
                latitude: root._center ? root._center.lat : 0
                longitude: root._center ? root._center.lon : 0
                zoomLevel: 12
                trackPoints: activity.track || []
            }
            // Previews are for identification, not interaction - the card itself opens the
            // real, large, interactive map (the spec's own "Selecting an activity opens:
            // Large MapLibre map"), so panning/zooming a thumbnail would just fight the
            // TapHandler above for no benefit.
            MouseArea { anchors.fill: parent; onClicked: root.opened() }

            Rectangle {
                visible: root._center === null
                anchors.fill: parent
                color: Theme.background
                Text {
                    anchors.centerIn: parent
                    text: qsTr("No GPS track")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                }
            }
        }

        Row {
            width: parent.width
            padding: Theme.spacingMedium
            spacing: Theme.spacingSmall

            // Same per-sport badge ActivityRow.qml's list view already shows, resolved off
            // `activity.name` through the one shared ActivityTypes table (2026-08-11: this
            // used to be a plain generic glyph here regardless of sport - Ambit, Kailash and
            // Garmin activities all looked identical in the grid, the default view, even
            // though Kailash/Garmin are named "Walk" the same way an Ambit "Walk" sport mode
            // is and so already resolve to the exact same "Walking" entry via forName()).
            ActivityBadge {
                activityId: ActivityTypes.forName(activity.name).id
                size: 20
            }

            Column {
                spacing: 2
                Text {
                    text: activity.name || qsTr("Untitled activity")
                    font.bold: true
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBodyLarge
                }
                Text {
                    text: ActivityViewModel.formatDate(activity.startTime)
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                }
            }
        }

        Row {
            width: parent.width
            leftPadding: Theme.spacingMedium
            rightPadding: Theme.spacingMedium
            bottomPadding: Theme.spacingMedium
            spacing: Theme.spacingLarge

            Text {
                text: ActivityViewModel.formatDistance(activity.distanceMeters)
                color: Theme.text
                font.pixelSize: Theme.fontSizeLabel
            }
            Text {
                text: ActivityViewModel.formatDuration(activity.durationSeconds)
                color: Theme.text
                font.pixelSize: Theme.fontSizeLabel
            }
            Text {
                text: ActivityViewModel.formatElevation(activity.ascentMeters)
                color: Theme.text
                font.pixelSize: Theme.fontSizeLabel
            }
        }
    }
}
