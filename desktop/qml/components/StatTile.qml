import QtQuick
import SommetUi

// One number in a tile (André, 2026-11-02, desktop picks #12): a label, the value with its
// unit, and a short caption under it, such as "▲ 4 vs 7-day average". `badge` adds a small tag
// next to the label, such as "3 days old". For Health, and later Coach and the activity summary.
Rectangle {
    id: root

    property string label
    property string value
    property string unit: ""
    property string caption: ""
    property string badge: ""
    property color valueColor: Theme.text

    implicitWidth: Math.max(130, col.implicitWidth + 24)
    implicitHeight: col.implicitHeight + 20
    radius: Theme.radiusSmall
    color: Theme.cardNested

    Column {
        id: col
        x: 12
        y: 10
        width: root.width - 24
        spacing: 2

        Row {
            spacing: 6
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: root.label
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
            }
            Rectangle {
                visible: root.badge.length > 0
                anchors.verticalCenter: parent.verticalCenter
                width: badgeText.implicitWidth + 8
                height: badgeText.implicitHeight + 2
                radius: 6
                color: "transparent"
                border.width: 1
                border.color: Theme.border
                Text {
                    id: badgeText
                    anchors.centerIn: parent
                    text: root.badge
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeTiny
                }
            }
        }

        Row {
            spacing: 4
            Text {
                id: valueText
                text: root.value
                color: root.valueColor
                font.pixelSize: Theme.fontSizeLargeTitle
                font.bold: true
            }
            Text {
                visible: root.unit.length > 0
                anchors.baseline: valueText.baseline
                text: root.unit
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeLabel
            }
        }

        Text {
            visible: root.caption.length > 0
            width: parent.width
            text: root.caption
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeCaption
            wrapMode: Text.WordWrap
        }
    }

    Accessible.role: Accessible.StaticText
    Accessible.name: root.label + ": " + root.value + (root.unit ? " " + root.unit : "")
                     + (root.caption ? ", " + root.caption : "")
}
