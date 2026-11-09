pragma ComponentBehavior: Bound
import QtQuick
import SommetUi

// Sommet's "working" mark: three dots that pulse in turn (160 ms apart). Used by LoadingPill,
// busy buttons, the Coach while Claude writes, and the firmware steps. Dots, not a spinner
// (see LoadingPill).
Row {
    id: root

    property color color: Theme.primary
    property int dotSize: 6
    property bool running: visible

    spacing: 4

    Repeater {
        model: 3
        delegate: Rectangle {
            id: dot
            required property int index
            width: root.dotSize
            height: root.dotSize
            radius: root.dotSize / 2
            color: root.color
            opacity: 0.25
            SequentialAnimation on opacity {
                running: root.running
                loops: Animation.Infinite
                PauseAnimation { duration: dot.index * 160 }
                NumberAnimation { to: 1.0; duration: 320; easing.type: Easing.InOutQuad }
                NumberAnimation { to: 0.25; duration: 320; easing.type: Easing.InOutQuad }
                PauseAnimation { duration: (2 - dot.index) * 160 }
            }
        }
    }
}
