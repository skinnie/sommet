import QtQuick
import AmbitApp

// A silhouette of a Bryton Aero 60, redrawn 2026-09-25 from André's front-view reference
// (the first 09-24 version drew a landscape unit, which the Aero 60 is not). A portrait body a
// little squarer than the Magene's, a tall screen filling most of the face with the "bryton"
// strip above it and "Aero 60" on the chin, keys on both flanks (menu high on the left, up/down
// lower left; OK/LAP and back on the right) and two small keys on the bottom edge (light,
// power). Plain shapes, sized and coloured like Icon.qml (`size`/`color`), EdgeIcon's style.
Item {
    id: root
    property int size: 24
    property color color: Theme.text

    implicitWidth: size
    implicitHeight: size

    readonly property real keyW: Math.max(1, root.size * 0.05)

    Rectangle {                 // body - portrait, moderately rounded
        id: body
        width: root.size * 0.66
        height: root.size * 0.9
        radius: width * 0.16
        anchors.centerIn: parent
        color: "transparent"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.055)
    }
    Rectangle {                 // screen - tall, logo strip above, "Aero 60" chin below
        width: body.width * 0.74
        height: body.height * 0.58
        radius: width * 0.08
        anchors.horizontalCenter: body.horizontalCenter
        anchors.top: body.top
        anchors.topMargin: body.height * 0.17
        color: "white"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.03)
    }
    Rectangle {                 // "bryton" wordmark strip
        width: body.width * 0.34
        height: Math.max(1, root.size * 0.035)
        radius: height / 2
        anchors.horizontalCenter: body.horizontalCenter
        anchors.top: body.top
        anchors.topMargin: body.height * 0.08
        color: root.color
    }

    // Left flank: menu key high, up/down key lower.
    Rectangle {
        width: root.keyW; height: body.height * 0.09; radius: width * 0.4
        anchors.right: body.left; anchors.rightMargin: -width * 0.3
        y: body.y + body.height * 0.22
        color: root.color
    }
    Rectangle {
        width: root.keyW; height: body.height * 0.16; radius: width * 0.4
        anchors.right: body.left; anchors.rightMargin: -width * 0.3
        y: body.y + body.height * 0.50
        color: root.color
    }
    // Right flank: OK/LAP and back.
    Repeater {
        model: [0.46, 0.63]
        delegate: Rectangle {
            required property real modelData
            width: root.keyW; height: body.height * 0.12; radius: width * 0.4
            anchors.left: body.right; anchors.leftMargin: -width * 0.3
            y: body.y + body.height * modelData
            color: root.color
        }
    }
    // Bottom edge: light and power keys.
    Repeater {
        model: [0.36, 0.64]
        delegate: Rectangle {
            required property real modelData
            width: body.width * 0.12; height: root.keyW; radius: height * 0.4
            anchors.top: body.bottom; anchors.topMargin: -height * 0.3
            x: body.x + body.width * modelData - width / 2
            color: root.color
        }
    }
}
