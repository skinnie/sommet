import QtQuick
import AmbitApp

// A silhouette of a Garmin Edge 1040 (André, 2026-09-04, reference photo). The Edge reads as a
// portrait unit with the screen in the upper ~two-thirds, a lower chin below it (with the small
// round logo), physical buttons on the lower left/right edges (start / lap), and the quarter-turn
// out-front mount tab underneath. Drawn from plain shapes (see EtrexIcon.qml). Sized/coloured
// like Icon.qml (`size`/`color`).
Item {
    id: root
    property int size: 24
    property color color: Theme.text

    implicitWidth: size
    implicitHeight: size

    // Out-front mount clip underneath.
    Rectangle {
        id: mount
        width: root.size * 0.22
        height: root.size * 0.1
        radius: height * 0.4
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        color: "transparent"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.05)
    }

    // Body - portrait, moderately rounded.
    Rectangle {
        id: body
        width: root.size * 0.62
        height: root.size * 0.86
        radius: width * 0.2
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: mount.top
        anchors.bottomMargin: -root.size * 0.02
        color: "transparent"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.07)
    }

    // Screen - upper two-thirds of the face.
    Rectangle {
        id: screen
        width: body.width * 0.78
        height: body.height * 0.60
        radius: width * 0.06
        anchors.horizontalCenter: body.horizontalCenter
        anchors.top: body.top
        anchors.topMargin: body.height * 0.1
        color: "white"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.03)
    }

    // Logo dot on the chin below the screen.
    Rectangle {
        width: root.size * 0.07
        height: width
        radius: width / 2
        anchors.horizontalCenter: body.horizontalCenter
        anchors.top: screen.bottom
        anchors.topMargin: body.height * 0.06
        color: "transparent"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.035)
    }

    // Start / lap buttons on the lower left and right edges.
    Rectangle {
        width: root.size * 0.055
        height: root.size * 0.12
        radius: width * 0.4
        anchors.right: body.left
        anchors.rightMargin: -width * 0.5
        anchors.top: body.top
        anchors.topMargin: body.height * 0.5
        color: root.color
    }
    Rectangle {
        width: root.size * 0.055
        height: root.size * 0.12
        radius: width * 0.4
        anchors.left: body.right
        anchors.leftMargin: -width * 0.5
        anchors.top: body.top
        anchors.topMargin: body.height * 0.5
        color: root.color
    }
}
