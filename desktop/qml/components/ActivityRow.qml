import QtQuick
import QtQuick.Controls
import AmbitApp

// One activity as a LIST row - real request, 2026-08-11 (André, item 16): "For activities, in
// settings let's add the option: see as a map, see as a list. the first is the one we already
// have."
//
// Deliberately draws no map. That is the point of the list view beyond looking different:
// ActivityCard embeds a live MapView per card, and this project already learned on real
// hardware that too many simultaneous map instances crashed the app (see ActivitiesPage's own
// comment on why the grid virtualises). A list of rows costs nothing per entry, so a watch
// with a long history scrolls smoothly - which is the case where the card grid is heaviest.
Rectangle {
    id: root

    property var activity

    signal opened()
    // Right-click → Delete (André, 2026-08-25). The page owns the confirm dialog and the actual
    // delete; the row just reports that this activity was asked to go.
    signal deleteRequested()

    // 44px, matching NavItem.qml's own implicitHeight exactly (André, 2026-08-25: "list
    // should align with home... indoor with activities... walking with routes" - the row
    // rhythm now mirrors the nav rail's: 44px row + 2px gap = the same 46px pitch NavItem's
    // own Column produces, so each list row lands on the same y as its equivalent nav item).
    width: parent ? parent.width : 0
    height: 44
    radius: Theme.radiusCard
    color: "transparent"

    // Real bug, 2026-08-11 (André): "in activities there is the same flashing with grey when
    // i move on the activities. please solve it as you did before."
    //
    // Same cause as the sport-mode display rows: animating `color` between "transparent" and
    // Theme.card interpolates through rgba(0,0,0,0) -> opaque, so every frame in between is
    // a translucent BLACK, which on a light background reads as a grey flash. Nothing is
    // wrong with the endpoints; it is the path between them.
    //
    // The fix, same as before: the colour never animates. A sibling background sits at the
    // final colour the whole time and its OPACITY animates instead, which fades card-colour
    // over the page rather than through black.
    Rectangle {
        anchors.fill: parent
        radius: parent.radius
        color: Theme.card
        opacity: hover.hovered ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
    }

    HoverHandler { id: hover }
    TapHandler { onTapped: root.opened() }
    // Right mouse button opens the context menu AT THE CURSOR. Real bug (found 2026-08-25,
    // "the awful garbage bin icon... make it right click, delete"): Menu.popup() with no
    // arguments opens relative to its PARENT item's (0,0), not the click position - every
    // right-click on this row opened the menu pinned to the row's own top-left corner instead
    // of where the cursor actually was. Passing the TapHandler's own hit position fixes it.
    TapHandler {
        id: rightTap
        acceptedButtons: Qt.RightButton
        onTapped: (eventPoint) => rowMenu.popup(eventPoint.position.x, eventPoint.position.y)
    }
    ThemedMenu {
        id: rowMenu
        ThemedMenuItem {
            text: qsTr("Delete activity")
            onTriggered: root.deleteRequested()
        }
    }

    Row {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Theme.spacingMedium
        anchors.rightMargin: Theme.spacingMedium
        spacing: Theme.spacingMedium

        // Keyed on the activity's NAME: an activity read off the watch comes from its GPX,
        // which carries the sport mode's name but no numeric id. Anything unrecognised falls
        // back to the generic badge rather than guessing a sport.
        ActivityBadge {
            anchors.verticalCenter: parent.verticalCenter
            activityId: root.activity
                        ? ActivityTypes.forName(root.activity.name).id : 1
            size: 32
        }

        Column {
            anchors.verticalCenter: parent.verticalCenter
            width: parent.width * 0.42
            spacing: 1
            Text {
                width: parent.width
                elide: Text.ElideRight
                text: root.activity ? (root.activity.name || qsTr("Untitled activity")) : ""
                color: Theme.text
                font.pixelSize: Theme.fontSizeBody
                font.bold: true
            }
            Text {
                text: root.activity
                      ? ActivityViewModel.formatDate(root.activity.startTime) : ""
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
            }
        }

        // Configurable metric columns (André, 2026-08-16). The column set is Theme's own
        // persisted list of metric keys; each figure's value + unit come from ActivityMetrics
        // (which formats in the watch's unit setting). Widths come from the same catalogue so
        // the header dropdowns in ActivitiesPage line up over these columns to the pixel. Blank
        // (not a false 0) for a move that never recorded the metric - see ActivityMetrics.value.
        Repeater {
            model: Theme.activityColumnList()
            delegate: Item {
                required property var modelData   // a metric key, e.g. "distance"
                anchors.verticalCenter: parent.verticalCenter
                width: ActivityMetrics.widthFor(modelData)
                height: figure.implicitHeight

                Text {
                    id: figure
                    anchors.right: parent.right
                    text: root.activity ? ActivityMetrics.value(root.activity, modelData) : ""
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBody
                }
                HoverHandler { id: figureHover }
                ToolTip.visible: figureHover.hovered && figure.text.length > 0
                ToolTip.text: ActivityMetrics.labelFor(modelData)
                ToolTip.delay: 300
            }
        }
    }

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 1
        color: Theme.mutedText
        opacity: 0.15
    }
}
