import QtQuick
import AmbitApp

// A silhouette of a Hammerhead Karoo (André, 2026-09-04, reference photo of the black unit).
// The Karoo reads like a small portrait phone: a tall rounded body that is almost all screen,
// a thin even bezel, and one power/select button on the right edge. Drawn from plain shapes
// (see EtrexIcon.qml for why, not a font glyph). Sized/coloured like Icon.qml (`size`/`color`).
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

    // Body - tall portrait slab, generously rounded corners like the real unit.
    Rectangle {
        id: body
        width: root.size * 0.60
        height: root.size * 0.86
        radius: width * 0.26
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: mount.top
        anchors.bottomMargin: -root.size * 0.02
        color: "transparent"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.07)
    }

    // Screen - fills almost the whole face behind a thin even bezel (the Karoo's look).
    Rectangle {
        width: body.width * 0.80
        height: body.height * 0.84
        radius: width * 0.14
        anchors.centerIn: body
        color: "white"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.03)
    }

    // Power/select button on the right edge, upper third.
    Rectangle {
        width: root.size * 0.055
        height: root.size * 0.14
        radius: width * 0.4
        anchors.left: body.right
        anchors.leftMargin: -width * 0.5
        anchors.top: body.top
        anchors.topMargin: body.height * 0.22
        color: root.color
    }
}
