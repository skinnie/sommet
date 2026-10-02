import QtQuick
import AmbitApp

// A silhouette of the original Wahoo ELEMNT (André's unit, 2026-10-02). It is portrait, with a
// row of LEDs along the top edge, a screen taking most of the face, and three front keys under
// the screen (left / page / right). Power is on the left flank and zoom up/down on the right.
// Plain shapes, sized and coloured like Icon.qml (`size`/`color`), the same style as BrytonIcon.
Item {
    id: root
    property int size: 24
    property color color: Theme.text

    implicitWidth: size
    implicitHeight: size

    readonly property real keyW: Math.max(1, root.size * 0.05)

    Rectangle {                 // body - portrait, softly rounded
        id: body
        width: root.size * 0.62
        height: root.size * 0.92
        radius: width * 0.14
        anchors.centerIn: parent
        color: "transparent"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.055)
    }
    Row {                       // LED row across the top
        anchors.horizontalCenter: body.horizontalCenter
        anchors.top: body.top
        anchors.topMargin: body.height * 0.08
        spacing: body.width * 0.05
        Repeater {
            model: 5
            delegate: Rectangle {
                width: Math.max(1, root.size * 0.035); height: width; radius: width / 2
                color: root.color
            }
        }
    }
    Rectangle {                 // screen
        width: body.width * 0.74
        height: body.height * 0.56
        radius: width * 0.06
        anchors.horizontalCenter: body.horizontalCenter
        anchors.top: body.top
        anchors.topMargin: body.height * 0.16
        color: "white"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.03)
    }
    // Three front keys under the screen.
    Repeater {
        model: [0.24, 0.5, 0.76]
        delegate: Rectangle {
            required property real modelData
            width: body.width * 0.16; height: Math.max(1, root.size * 0.045); radius: height / 2
            x: body.x + body.width * modelData - width / 2
            y: body.y + body.height * 0.82
            color: root.color
        }
    }
    Rectangle {                 // power key, left flank
        width: root.keyW; height: body.height * 0.1; radius: width * 0.4
        anchors.right: body.left; anchors.rightMargin: -width * 0.3
        y: body.y + body.height * 0.2
        color: root.color
    }
    Repeater {                  // zoom up/down, right flank
        model: [0.3, 0.46]
        delegate: Rectangle {
            required property real modelData
            width: root.keyW; height: body.height * 0.12; radius: width * 0.4
            anchors.left: body.right; anchors.leftMargin: -width * 0.3
            y: body.y + body.height * modelData
            color: root.color
        }
    }
}
