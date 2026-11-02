import QtQuick
import AmbitApp

// A loading indicator with some life in it - real request, 2026-08-11 (André, on the
// Activities page): "there is a message that appears says loading etc etc 'there is no
// faster path yet'...take out that part..and make it like a 'fancy loading button' linked
// to the theme..maybe something funny like 'Adventures loading...'".
//
// Takes its colours entirely from Theme, so it follows light/dark like everything else -
// "linked to the theme" was the ask, not a one-off palette.
//
// The three dots are pulsed rather than spun on purpose: a rotating spinner reads as "this
// might hang", a steady pulse reads as "this is working through something long", which is
// what a multi-minute flash read actually is. Animations only run while the pill is visible,
// so an off-screen one costs nothing.
Item {
    id: root

    // The playful line. The caller owns the wording - what is fun on Activities would be odd
    // elsewhere.
    property string text: qsTr("Loading...")
    // Optional second line for the honest detail (how long this really takes). Hidden when
    // empty, so the pill collapses to a single line for quick operations.
    property string detail: ""

    implicitWidth: pill.width
    implicitHeight: pill.height

    Rectangle {
        id: pill
        // The pill hugs its contents rather than being given a fixed size, so a longer
        // message in another language does not get clipped.
        // FIXED 32px height (2026-08-25, fourth pass - André: "reduce that adventure loading to
        // like 40%, is annoying me"). No longer tied to matching a nav slot exactly - the list
        // header's own position during loading is now a hardcoded value (see
        // ActivitiesPage.qml's activitiesViewToggle) that does NOT depend on this pill's real
        // height at all, so this can just be "small and out of the way" without needing to hit
        // an exact number. clip: true guards against the 2-line text ever visually overflowing
        // this shorter box.
        clip: true
        width: content.width + Theme.spacingLarge * 2
        height: 32
        radius: height / 2
        color: Theme.card
        border.width: 1
        // The accent at full strength drew a hard ring that read as a button you were meant
        // to press - this is a status, not a control, so the outline is only a hint of it.
        border.color: Qt.rgba(Theme.primary.r, Theme.primary.g, Theme.primary.b, 0.4)
        // Deliberately faint: this sits over a page that may already have real cards drawn
        // on it, so it has to be legible without competing with them.
        opacity: 0.97

        Column {
            id: content
            anchors.centerIn: parent
            spacing: 2

            Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: Theme.spacingSmall

                PulseDots {
                    anchors.verticalCenter: parent.verticalCenter
                    running: root.visible
                }

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: root.text
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBody
                    font.bold: true
                }
            }

            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                visible: root.detail.length > 0
                text: root.detail
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
            }
        }
    }
}
