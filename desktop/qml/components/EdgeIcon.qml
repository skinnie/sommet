import QtQuick
import AmbitApp

// A silhouette of a Garmin Edge (André, 2026-09-04, reference photo; geometry tuned against an
// offscreen render). Portrait body, a screen with even margins in the upper part, three front
// keys on the chin below it, and the quarter-turn out-front mount tab underneath. Drawn from
// plain shapes (see EtrexIcon.qml). Sized/coloured like Icon.qml (`size`/`color`).
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
        width: root.size * 0.62
        height: root.size * 0.84
        radius: width * 0.18
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: mount.top
        anchors.bottomMargin: -root.size * 0.02
        color: "transparent"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.055)
    }
    Rectangle {                 // screen - even margins, leaves a chin for the keys
        id: screen
        width: body.width * 0.74
        height: body.height * 0.55
        radius: width * 0.07
        anchors.horizontalCenter: body.horizontalCenter
        anchors.top: body.top
        anchors.topMargin: body.height * 0.13
        color: "white"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.03)
    }
    Row {                       // three front keys on the chin
        anchors.horizontalCenter: body.horizontalCenter
        anchors.top: screen.bottom
        anchors.topMargin: body.height * 0.09
        spacing: root.size * 0.06
        Repeater {
            model: 3
            delegate: Rectangle {
                width: root.size * 0.075
                height: root.size * 0.05
                radius: height * 0.4
                color: root.color
            }
        }
    }
}
