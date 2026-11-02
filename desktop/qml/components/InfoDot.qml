import QtQuick
import AmbitApp

// A small "(i)" that explains on hover, and stays open when clicked (touch screens), until
// clicked again. Drawn, not a glyph: the app's icon font has no info glyph. One for the whole
// app (André, 2026-11-02, desktop picks #10); it used to be two slightly different inline ones
// in TrainingProgramPage and ActivityOverview.
Rectangle {
    id: root

    property string text: ""
    property alias tip: root.text      // ActivityOverview's old name
    property bool pinned: false
    property int size: 18

    visible: text.length > 0
    implicitWidth: size
    implicitHeight: size
    radius: size / 2
    color: "transparent"
    border.width: 1
    border.color: dotHover.hovered || root.pinned ? Theme.primary : Theme.mutedText

    Text {
        anchors.centerIn: parent
        text: "i"
        color: dotHover.hovered || root.pinned ? Theme.primary : Theme.mutedText
        font.pixelSize: Math.round(root.size * 0.6)
        font.bold: true
    }

    HoverHandler { id: dotHover; cursorShape: Qt.PointingHandCursor }
    TapHandler { onTapped: root.pinned = !root.pinned }

    ThemedToolTip {
        visible: dotHover.hovered || root.pinned
        delay: root.pinned ? 0 : 150
        text: root.text
    }

    Accessible.role: Accessible.Button
    Accessible.name: qsTr("More information")
    Accessible.description: root.text
    Accessible.onPressAction: root.pinned = !root.pinned
}
