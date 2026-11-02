import QtQuick
import AmbitApp

// The result of an action, shown under the button that caused it (André, 2026-11-02, desktop
// picks #7). Replaces the loose green/red caption lines: a card with a thin coloured bar on the
// left, an icon, the text, and a × to close it. Hidden while `text` is empty; a new text shows
// it again after a close.
//
//   StatusNote { width: parent.width; text: BackupService.result; tone: BackupService.ok ? "success" : "error" }
//
// Errors from a service still go through ErrorBanner (one sentence + Send logs); this is for
// results written for the user.
Rectangle {
    id: root

    property string text
    // success | warning | error | working (a step still running: pulsing dots, no ×)
    property string tone: "success"
    property bool closable: tone !== "working"
    property bool _closed: false

    onTextChanged: _closed = false

    readonly property color toneColor: tone === "error" ? Theme.error
        : tone === "warning" ? Theme.warning
        : tone === "working" ? Theme.primary
        : Theme.success

    visible: text.length > 0 && !_closed
    implicitWidth: 38 + label.implicitWidth + (closable ? 34 : 12)
    implicitHeight: Math.max(label.implicitHeight, 18) + 16
    radius: Theme.radiusSmall
    color: Theme.card
    border.width: 1
    border.color: Theme.border

    Rectangle {
        x: 5
        y: 6
        width: 3
        height: parent.height - 12
        radius: 1.5
        color: root.toneColor
    }

    PulseDots {
        visible: root.tone === "working"
        x: 13
        y: 14
        dotSize: 5
        spacing: 2
        color: root.toneColor
    }

    Icon {
        visible: root.tone !== "working"
        x: 15
        y: 8
        size: 16
        glyph: root.tone === "error" ? Icons.error
             : root.tone === "warning" ? Icons.warningAmber
             : Icons.checkCircle
        color: root.toneColor
    }

    Text {
        id: label
        x: 38
        y: 8
        width: root.width - x - (root.closable ? 34 : 12)
        text: root.text
        color: Theme.text
        font.pixelSize: Theme.fontSizeLabel
        wrapMode: Text.WordWrap
    }

    Rectangle {
        visible: root.closable
        x: root.width - width - 6
        y: 5
        width: 24
        height: 24
        radius: Theme.radiusSmall
        color: closeHover.hovered ? Theme.cardNested : "transparent"
        Icon {
            anchors.centerIn: parent
            glyph: Icons.close
            size: 16
            color: closeHover.hovered ? Theme.text : Theme.mutedText
        }
        HoverHandler { id: closeHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: root._closed = true }
        Accessible.role: Accessible.Button
        Accessible.name: qsTr("Close")
        Accessible.onPressAction: root._closed = true
    }

    Accessible.role: Accessible.AlertMessage
    Accessible.name: root.text
}
