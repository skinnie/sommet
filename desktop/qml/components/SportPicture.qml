import QtQuick

import SommetUi
import "../SportPicture.js" as SP

// move-page/03 (André, 2026-11-18): a move with no GPS track shows its sport where the map would
// be - faint contour lines in a map tile's colours and the sport's Ambit badge, so it sits well
// next to the real maps. Drawn by shared/sport_picture.js, the same file Android's
// SportPicture.tsx uses, so both apps show the same picture.
Item {
    id: root

    property int activityId: -1      // ActivityTypes id (ActivityTypes.displayId)
    property string name: ""         // the sport name shown under the badge
    property string seed: ""         // anything stable for the move (its start); varies the lines

    readonly property var _entry: ActivityTypes.forId(activityId)
    readonly property int _w: Math.max(1, Math.round(width))
    readonly property int _h: Math.max(1, Math.round(height))

    Rectangle {
        id: clip
        anchors.fill: parent
        color: SP.LAND
        clip: true

        Image {
            anchors.fill: parent
            smooth: true
            cache: false
            sourceSize.width: root._w
            sourceSize.height: root._h
            source: root.width > 0 && root.height > 0
                    ? "data:image/svg+xml;utf8," + encodeURIComponent(
                          SP.sportPictureSvg(root._entry, root._w, root._h, SP.seedOf(root.seed)))
                    : ""
        }
        Text {
            x: 12
            width: parent.width - 24
            y: SP.nameTop(root._h)
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
            text: root.name || (root._entry ? root._entry.name : "")
            color: SP.NAME_COLOUR
            font.pixelSize: Theme.fontSizeLabel
            font.bold: true
        }
    }
}
