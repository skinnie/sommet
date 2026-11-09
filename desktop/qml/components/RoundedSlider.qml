import QtQuick
import QtQuick.Controls
import SommetUi

// Same real request as RoundedButton.qml ("rounded squares on sport modes buttons and
// settings buttons/sliders") applied to Slider's own handle - the Basic style's default
// handle is a plain circle. A drop-in replacement for `Slider { ... }` - extends Slider
// directly, so every existing property (from/to/value/onMoved/enabled) keeps working
// unchanged.
Slider {
    id: root

    background: Rectangle {
        x: root.leftPadding
        y: root.topPadding + root.availableHeight / 2 - height / 2
        implicitWidth: 160
        implicitHeight: 4
        width: root.availableWidth
        height: implicitHeight
        radius: 2
        color: Theme.background

        Rectangle {
            width: root.visualPosition * parent.width
            height: parent.height
            radius: 2
            color: root.enabled ? Theme.primary : Theme.mutedText
        }
    }

    handle: Rectangle {
        x: root.leftPadding + root.visualPosition * (root.availableWidth - width)
        y: root.topPadding + root.availableHeight / 2 - height / 2
        implicitWidth: 18
        implicitHeight: 18
        radius: Theme.radiusSmall  // a rounded square, not a full circle
        color: root.enabled ? Theme.primary : Theme.mutedText
        border.width: 0
    }
}
