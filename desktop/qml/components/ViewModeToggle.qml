import QtQuick
import QtQuick.Controls
import SommetUi

// The map/list view control for the top of a list page (Activities/Routes/POIs). Moved out of
// Settings onto the pages themselves - André, 2026-08-16. Originally a two-segment pill, but it
// overlapped the metric column headers on the right; André asked for it "on the left side, as
// text, as a dropdown menu as the other stuff, aligned, on the same horizontal line" - so it is
// now a plain text label + caret that opens the same ThemedMenu the column headers use, sitting
// on the left of the header row. The choice still persists (the page binds `mode` to the relevant
// Theme.xView and writes it back on `chosen`).
Item {
    id: root
    property string mode: "map"
    signal chosen(string mode)

    implicitWidth: viewRow.implicitWidth
    implicitHeight: 26
    width: implicitWidth
    height: implicitHeight

    Row {
        id: viewRow
        anchors.verticalCenter: parent.verticalCenter
        spacing: 3
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.mode === "list" ? qsTr("List") : qsTr("Map")
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeCaption
        }
        Text {   // dropdown caret, same as the column headers
            anchors.verticalCenter: parent.verticalCenter
            text: "▾"
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeCaption
        }
    }

    TapHandler { onTapped: viewMenu.popup() }
    HoverHandler { cursorShape: Qt.PointingHandCursor }

    ThemedMenu {
        id: viewMenu
        ThemedMenuItem {
            text: qsTr("Map")
            checkable: true
            checked: root.mode !== "list"
            onTriggered: root.chosen("map")
        }
        ThemedMenuItem {
            text: qsTr("List")
            checkable: true
            checked: root.mode === "list"
            onTriggered: root.chosen("list")
        }
    }
}
