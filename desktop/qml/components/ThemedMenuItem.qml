import QtQuick
import QtQuick.Controls
import AmbitApp

// A MenuItem styled to the app's rounded-card language, for ThemedMenu. Theme.text labels
// (RoundedComboBox learned the hard way that the Basic style pulls an OS-palette grey), a
// rounded primary-tint highlight inset from the menu's border, and a teal check for a
// checked/checkable row. Drop-in for `MenuItem { ... }`.
MenuItem {
    id: root
    implicitHeight: 34
    // A hidden item (e.g. "Send to Magene" with no Magene around) collapses instead of leaving
    // a blank row - Menu's ListView otherwise keeps its slot.
    height: visible ? implicitHeight : 0

    contentItem: Text {
        leftPadding: 22   // room for the check indicator
        rightPadding: Theme.spacingMedium
        text: root.text
        color: root.enabled ? Theme.text : Theme.mutedText
        font.pixelSize: Theme.fontSizeBody
        font.bold: root.checkable && root.checked
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    indicator: Item {
        implicitWidth: 22
        height: root.height
        Text {
            anchors.centerIn: parent
            visible: root.checkable && root.checked
            text: "✓"
            color: Theme.primary
            font.pixelSize: Theme.fontSizeBody
        }
    }

    background: Rectangle {
        anchors.fill: parent
        anchors.margins: 2
        radius: Theme.radiusSmall
        color: root.highlighted ? Theme.primary + "26" : "transparent"
    }
}
