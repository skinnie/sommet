import QtQuick
import AmbitApp

// A silhouette of a Bryton Aero 60 (André, 2026-09-24, reference photo). Unlike the portrait
// Edge, the Aero is a landscape head unit: a wide rounded body with a big screen filling most of
// it, two buttons on the right flank, and the out-front mount tab underneath. Plain shapes, sized
// and coloured like Icon.qml (`size`/`color`), matching EdgeIcon.qml's drawing style.
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
    Rectangle {                 // landscape body
        id: body
        width: root.size * 0.9
        height: root.size * 0.66
        radius: height * 0.24
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: mount.top
        anchors.bottomMargin: -root.size * 0.02
        color: "transparent"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.055)
    }
    Rectangle {                 // screen - fills most of the face, offset left to leave a flank
        id: screen
        width: body.width * 0.66
        height: body.height * 0.64
        radius: width * 0.06
        anchors.verticalCenter: body.verticalCenter
        anchors.left: body.left
        anchors.leftMargin: body.width * 0.13
        color: "white"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.03)
    }
    Column {                    // two side keys on the right flank
        anchors.verticalCenter: body.verticalCenter
        anchors.right: body.right
        anchors.rightMargin: body.width * 0.075
        spacing: root.size * 0.07
        Repeater {
            model: 2
            delegate: Rectangle {
                width: root.size * 0.05
                height: root.size * 0.09
                radius: width * 0.4
                color: root.color
            }
        }
    }
}
