import QtQuick
import QtQuick.Controls
import SommetUi

// Real request 2026-08-09 ("for everything possible, never use true square stuff, always
// rounded corners") - QtQuick.Controls Basic style's CheckBox indicator has no radius at
// all (confirmed against the actual installed style's own CheckBox.qml - a plain square
// Rectangle). A drop-in replacement for `CheckBox { ... }` - extends CheckBox directly, so
// every existing property (checked/enabled/text/onToggled) keeps working unchanged.
CheckBox {
    id: root

    indicator: Rectangle {
        implicitWidth: 22
        implicitHeight: 22
        x: root.leftPadding
        y: root.topPadding + (root.availableHeight - height) / 2
        radius: 6
        color: root.checked ? Theme.primary : Theme.card
        border.width: 1
        // Real, 2026-08-10 ("the contour of the buttons when not selected...they are
        // black...not that visible") - see RoundedButton.qml's own comment on this same
        // fix across the Rounded* family.
        border.color: root.checked ? Theme.primary : Theme.mutedText
        Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }

        // A drawn tick (André, 2026-11-02, desktop picks #15): the icon font has no bare
        // checkmark, and the checkCircle glyph read as a circle inside a square. Two rounded
        // bars in an L, turned 45°, popping in over 120 ms.
        Item {
            anchors.centerIn: parent
            anchors.verticalCenterOffset: -1
            width: 6
            height: 11
            rotation: 45
            opacity: root.checked ? 1 : 0
            scale: root.checked ? 1 : 0.4
            Behavior on opacity { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
            Behavior on scale { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
            Rectangle { x: parent.width - width; width: 2.4; height: parent.height; radius: 1.2; color: Theme.card }
            Rectangle { y: parent.height - height; width: parent.width; height: 2.4; radius: 1.2; color: Theme.card }
        }
    }

    contentItem: Text {
        text: root.text
        color: root.enabled ? Theme.text : Theme.mutedText
        font.pixelSize: Theme.fontSizeLabel
        leftPadding: root.indicator ? root.indicator.width + Theme.spacingSmall : 0
        verticalAlignment: Text.AlignVCenter
    }
}
