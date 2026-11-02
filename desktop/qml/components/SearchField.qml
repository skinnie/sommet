import QtQuick
import QtQuick.Controls
import AmbitApp

// A search box (André, 2026-11-02, desktop picks #9): magnifier on the left, text from the left,
// a × to clear once there's text, and Esc clears too. Same field look as RoundedTextField
// (cardNested, hairline, 2px green border when focused, round corners). A drop-in for
// RoundedTextField: text, placeholderText, onTextChanged, onTextEdited and onAccepted work as
// before (clearing also emits textEdited).
TextField {
    id: root

    color: Theme.text
    placeholderText: qsTr("Search")
    placeholderTextColor: Theme.mutedText
    font.pixelSize: Theme.fontSizeBody
    selectionColor: Theme.primary
    selectedTextColor: Theme.card
    verticalAlignment: Text.AlignVCenter
    horizontalAlignment: TextInput.AlignLeft
    leftPadding: 32
    rightPadding: root.text.length > 0 ? 32 : Theme.spacingSmall

    property bool edited: false
    onTextEdited: edited = true

    function clearSearch() {
        if (root.text.length === 0)
            return
        root.clear()
        root.textEdited()
    }

    Keys.onEscapePressed: (event) => {
        if (root.text.length > 0) {
            root.clearSearch()
            event.accepted = true
        } else {
            event.accepted = false
        }
    }

    background: Rectangle {
        implicitWidth: 200
        implicitHeight: 36
        radius: Theme.radiusSmall
        color: Theme.cardNested
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Theme.primary : Theme.border
        Behavior on border.color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }
    }

    Icon {
        x: 10
        anchors.verticalCenter: parent.verticalCenter
        glyph: Icons.search
        size: 16
        color: Theme.mutedText
    }

    Rectangle {
        visible: root.text.length > 0
        x: root.width - width - 6
        anchors.verticalCenter: parent.verticalCenter
        width: 24
        height: 24
        radius: Theme.radiusSmall
        color: clearHover.hovered ? Theme.border : "transparent"
        Icon {
            anchors.centerIn: parent
            glyph: Icons.close
            size: 16
            color: clearHover.hovered ? Theme.text : Theme.mutedText
        }
        HoverHandler { id: clearHover; cursorShape: Qt.PointingHandCursor }
        TapHandler {
            onTapped: {
                root.clearSearch()
                root.forceActiveFocus()
            }
        }
        Accessible.role: Accessible.Button
        Accessible.name: qsTr("Clear")
        Accessible.onPressAction: root.clearSearch()
    }
}
