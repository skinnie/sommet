import QtQuick
import AmbitApp

// A silhouette of a Hammerhead Karoo (André, 2026-09-04, reference photo; geometry tuned against
// an offscreen render). A tall portrait slab that is almost all screen behind a thin even bezel,
// with two buttons down the right edge, and the out-front mount tab underneath. Drawn from plain
// shapes (see EtrexIcon.qml). Sized/coloured like Icon.qml (`size`/`color`).
Item {
    id: root
    property int size: 24
    property color color: Theme.text

    implicitWidth: size
    implicitHeight: size

    Rectangle {                 // out-front mount clip
        id: mount
        width: root.size * 0.24
        height: root.size * 0.11
        radius: height * 0.4
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        color: "transparent"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.05)
    }
    Rectangle {                 // body
        id: body
        width: root.size * 0.60
        height: root.size * 0.84
        radius: width * 0.24
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: mount.top
        anchors.bottomMargin: -root.size * 0.02
        color: "transparent"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.055)
    }
    Rectangle {                 // big screen, thin even bezel
        width: body.width * 0.80
        height: body.height * 0.82
        radius: width * 0.12
        anchors.centerIn: body
        color: "white"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.03)
    }
    Rectangle {                 // upper right button
        width: root.size * 0.05
        height: root.size * 0.12
        radius: width * 0.4
        anchors.left: body.right
        anchors.leftMargin: -width * 0.5
        anchors.top: body.top
        anchors.topMargin: body.height * 0.28
        color: root.color
    }
    Rectangle {                 // lower right button
        width: root.size * 0.05
        height: root.size * 0.12
        radius: width * 0.4
        anchors.left: body.right
        anchors.leftMargin: -width * 0.5
        anchors.top: body.top
        anchors.topMargin: body.height * 0.50
        color: root.color
    }
}
