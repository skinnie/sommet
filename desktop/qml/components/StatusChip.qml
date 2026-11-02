import QtQuick
import AmbitApp

// One small status tag for the whole app (André, 2026-11-02, desktop picks #6): Connected,
// Experimental, Soon, Due, Update available, Off. A light tint, a thin border and the text, all
// in the tone's colour, the same size and corners everywhere.
//
//   StatusChip { text: qsTr("Connected"); tone: "success" }
Rectangle {
    id: root

    property string text
    // success | warning | error | primary | neutral
    property string tone: "neutral"

    readonly property color toneColor: tone === "success" ? Theme.success
        : tone === "warning" ? Theme.warning
        : tone === "error" ? Theme.error
        : tone === "primary" ? Theme.primary
        : Theme.mutedText

    implicitWidth: label.implicitWidth + 12
    implicitHeight: label.implicitHeight + 6
    radius: Theme.radiusSmall
    color: Qt.alpha(root.toneColor, root.tone === "neutral" ? 0.08 : 0.10)
    border.width: 1
    border.color: root.toneColor

    Text {
        id: label
        anchors.centerIn: parent
        text: root.text
        color: root.toneColor
        font.pixelSize: Theme.fontSizeCaption
    }

    Accessible.role: Accessible.StaticText
    Accessible.name: root.text
}
