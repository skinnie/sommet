import QtQuick
import SommetUi

// One total, with its equivalents underneath - the repeated unit of the Totals page.
//
// The equivalents rotate rather than all being listed at once: André asked for "funny/random"
// ones, and a wall of six facts stops being either. One at a time, changing every few
// seconds, keeps the card readable and gives the screen a reason to be revisited.
Card {
    id: root

    property string title: ""
    property string headline: ""
    property string subtitle: ""
    property var lines: []

    property int lineIndex: 0

    // Reset when the underlying facts change (a different year, or a re-read), so the card
    // never shows an equivalent belonging to the previous number.
    onLinesChanged: lineIndex = 0

    Timer {
        interval: 6000
        running: root.visible && root.lines.length > 1
        repeat: true
        onTriggered: root.lineIndex = (root.lineIndex + 1) % root.lines.length
    }

    Column {
        width: parent.width
        spacing: Theme.spacingSmall

        Text {
            text: root.title
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeBody
            font.bold: true
        }

        Text {
            text: root.headline
            color: Theme.text
            font.pixelSize: Theme.fontSizeTitle
            font.bold: true
        }

        Text {
            visible: root.subtitle.length > 0
            text: root.subtitle
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeCaption
        }

        // The rotating equivalent. Fixed height so the card does not jump as lines of
        // different lengths cycle through it.
        Item {
            width: parent.width
            height: Math.max(equivalent.implicitHeight, 34)
            visible: root.lines.length > 0

            Text {
                id: equivalent
                width: parent.width
                wrapMode: Text.WordWrap
                text: root.lines.length > 0
                      ? root.lines[Math.min(root.lineIndex, root.lines.length - 1)] : ""
                color: Theme.primary
                font.pixelSize: Theme.fontSizeBody
                // Cross-fade on change, so it reads as one line updating rather than two
                // different lines flickering.
                opacity: 1
                Behavior on text {
                    SequentialAnimation {
                        NumberAnimation { target: equivalent; property: "opacity"
                                          to: 0; duration: 160 }
                        PropertyAction { target: equivalent; property: "text" }
                        NumberAnimation { target: equivalent; property: "opacity"
                                          to: 1; duration: 220 }
                    }
                }
            }
        }
    }
}
