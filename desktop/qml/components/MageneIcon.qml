import QtQuick
import AmbitApp

// A silhouette of a Magene C406 Pro (André, 2026-09-24, from the product photo
// static.magene.com/img/p/9/5/95-large_default.jpg; proportions re-checked 2026-09-25 against his
// front-view line drawing: top key near the left corner, wordmark/label strips, chin keys low). A softly-rounded portrait body, one small
// button on the top edge left of centre, a near-square screen in the upper-middle (the "Magene"
// wordmark sits above it, "C406 Pro" below), and four front keys on the chin grouped two-left /
// two-right. No out-front mount in the reference shot, so unlike EdgeIcon/KarooIcon this is the
// bare head unit. Drawn from plain shapes (see EtrexIcon.qml). Sized/coloured like Icon.qml.
Item {
    id: root
    property int size: 24
    property color color: Theme.text

    implicitWidth: size
    implicitHeight: size

    Rectangle {                 // top button (small nub, left of centre)
        id: topBtn
        width: root.size * 0.10
        height: root.size * 0.05
        radius: height * 0.4
        anchors.bottom: body.top
        anchors.bottomMargin: -root.size * 0.01
        x: body.x + body.width * 0.24
        color: root.color
    }
    Rectangle {                 // body - portrait, generously rounded corners
        id: body
        width: root.size * 0.58
        height: root.size * 0.9
        radius: width * 0.24
        anchors.centerIn: parent
        color: "transparent"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.055)
    }
    Rectangle {                 // screen - near-square, wordmark above / label below
        id: screen
        width: body.width * 0.76
        height: body.height * 0.48
        radius: width * 0.06
        anchors.horizontalCenter: body.horizontalCenter
        anchors.top: body.top
        anchors.topMargin: body.height * 0.16
        color: "white"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.03)
    }
    Rectangle {                 // "Magene" wordmark strip above the screen
        width: body.width * 0.30
        height: Math.max(1, root.size * 0.03)
        radius: height / 2
        anchors.horizontalCenter: body.horizontalCenter
        anchors.bottom: screen.top
        anchors.bottomMargin: body.height * 0.045
        color: root.color
    }
    Rectangle {                 // "C406 Pro" label strip on the chin
        width: body.width * 0.26
        height: Math.max(1, root.size * 0.03)
        radius: height / 2
        anchors.horizontalCenter: body.horizontalCenter
        anchors.top: screen.bottom
        anchors.topMargin: body.height * 0.08
        color: root.color
    }
    Row {                       // two front keys, left group on the chin
        anchors.left: screen.left
        anchors.top: screen.bottom
        anchors.topMargin: body.height * 0.16
        spacing: root.size * 0.05
        Repeater {
            model: 2
            delegate: Rectangle {
                width: root.size * 0.06
                height: root.size * 0.045
                radius: height * 0.4
                color: root.color
            }
        }
    }
    Row {                       // two front keys, right group on the chin
        anchors.right: screen.right
        anchors.top: screen.bottom
        anchors.topMargin: body.height * 0.16
        spacing: root.size * 0.05
        Repeater {
            model: 2
            delegate: Rectangle {
                width: root.size * 0.06
                height: root.size * 0.045
                radius: height * 0.4
                color: root.color
            }
        }
    }
}
