import QtQuick
import SommetUi

// A real silhouette of a Garmin eTrex 10 handheld GPS unit, redrawn 2026-08-08 against a
// real reference photo (etrex10.jpg) rather than guessed from memory like the first pass -
// that first version's separate antenna "bump" on top doesn't exist on a real eTrex 10 (flat/
// rounded top, no dome); the real silhouette is a single tall rounded body with small button
// nubs down each side (up/down/menu on the left, back/page on the right, matching the photo)
// and one big lower-body screen rather than a small upper one. Drawn from plain shapes rather
// than a font glyph, same reasoning as before: no icon in this app's Material Symbols subset
// represents a handheld GPS unit. Sized/colored the same way Icon.qml is (`size`/`color`), so
// it drops into the exact same call sites (`EtrexIcon { size: 32 }`).
Item {
    id: root
    property int size: 24
    property color color: Theme.text

    implicitWidth: size
    implicitHeight: size

    // Body - taller than wide, rounded top and bottom, matching the eTrex 10's real
    // proportions (a vertical handheld, not a square watch face). Narrower than the full
    // icon width so the side button nubs below have room to protrude without clipping.
    Rectangle {
        id: body
        width: root.size * 0.56
        height: root.size * 0.88
        radius: width * 0.32
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        color: "transparent"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.07)
    }

    // Left-side buttons: up/down rocker near the top, menu button below it - the real
    // photo's three left-edge controls.
    Rectangle {
        width: root.size * 0.1
        height: root.size * 0.16
        radius: width * 0.3
        anchors.right: body.left
        anchors.rightMargin: -width * 0.5
        anchors.top: body.top
        anchors.topMargin: body.height * 0.16
        color: root.color
    }
    Rectangle {
        width: root.size * 0.1
        height: root.size * 0.1
        radius: width * 0.3
        anchors.right: body.left
        anchors.rightMargin: -width * 0.5
        anchors.top: body.top
        anchors.topMargin: body.height * 0.44
        color: root.color
    }

    // Right-side buttons: back button near the top, page button below it - the real photo's
    // two right-edge controls.
    Rectangle {
        width: root.size * 0.1
        height: root.size * 0.14
        radius: width * 0.3
        anchors.left: body.right
        anchors.leftMargin: -width * 0.5
        anchors.top: body.top
        anchors.topMargin: body.height * 0.18
        color: root.color
    }
    Rectangle {
        width: root.size * 0.1
        height: root.size * 0.1
        radius: width * 0.3
        anchors.left: body.right
        anchors.leftMargin: -width * 0.5
        anchors.top: body.top
        anchors.topMargin: body.height * 0.42
        color: root.color
    }

    // Backlight/power button - the real photo's small circle top-right of the screen bezel.
    Rectangle {
        width: root.size * 0.13
        height: width
        radius: width / 2
        anchors.horizontalCenter: body.horizontalCenter
        anchors.horizontalCenterOffset: body.width * 0.2
        anchors.top: body.top
        anchors.topMargin: body.height * 0.14
        color: "transparent"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.04)
    }

    // Screen - large, filling most of the lower body, matching the real photo's map display
    // taking up the bottom two-thirds of the front face rather than a small upper strip.
    // White fill with a grey frame (2026-08-08 request) rather than a solid grey fill - the
    // real eTrex 10's screen is a light monochrome LCD, not a dark panel, so white reads
    // truer than the flat `root.color` fill this had before.
    Rectangle {
        width: body.width * 0.62
        height: body.height * 0.42
        radius: width * 0.08
        anchors.horizontalCenter: body.horizontalCenter
        anchors.bottom: body.bottom
        anchors.bottomMargin: body.height * 0.16
        color: "white"
        border.color: root.color
        border.width: Math.max(1, root.size * 0.035)
    }
}
