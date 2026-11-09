import QtQuick
import SommetUi

// Temporary content for pages whose real build is a later step (Home is Step 4, Routes/POIs
// redesign are Steps 8/9, etc.) - each page file already exists at its real name/location so
// later steps edit it in place rather than creating it from scratch.
Item {
    property string title
    property string stepNote

    Card {
        anchors.centerIn: parent
        width: 360

        Column {
            width: parent.width
            spacing: Theme.spacingSmall

            Text {
                text: title
                font.pixelSize: Theme.fontSizeLargeTitle
                font.bold: true
                color: Theme.text
            }
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: stepNote
                color: Theme.mutedText
            }
        }
    }
}
