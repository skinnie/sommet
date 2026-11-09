import QtQuick
import QtQuick.Controls
import SommetUi

// One tooltip for the whole app (André, 2026-11-02, desktop picks #10; on 2026-08-23 he said
// the stock ToolTip "doesn't match at all our design"). A card with a hairline, round corners
// and a small arrow, a quick fade, above its item, or below when the item is near the top of
// the window. Use it as a child of the item it explains:
//
//   HoverHandler { id: hover }
//   ThemedToolTip { visible: hover.hovered; text: qsTr("…") }
ToolTip {
    id: root

    delay: 300
    timeout: -1
    margins: 6
    padding: Theme.spacingSmall
    closePolicy: Popup.NoAutoClose

    property bool _below: false
    // Where the arrow sits. Near a window edge the tooltip is pushed inwards, so the arrow
    // follows the item's middle instead of staying in the middle of the box.
    property real _arrowX: width / 2
    function _placeArrow() {
        const p = root.parent
        if (!p || !root.visible)
            return
        const mid = bg.mapFromItem(p, p.width / 2, 0).x
        _arrowX = Math.max(12, Math.min(root.width - 12, mid))
    }
    onAboutToShow: {
        const p = root.parent
        _below = p ? p.mapToItem(null, 0, 0).y < root.implicitHeight + 16 : false
        _arrowX = width / 2
        Qt.callLater(root._placeArrow)
    }
    onWidthChanged: Qt.callLater(root._placeArrow)

    width: Math.min(contentItem.implicitWidth + leftPadding + rightPadding, 320)
    x: parent ? Math.round((parent.width - width) / 2) : 0
    y: parent ? (_below ? parent.height + 8 : -height - 8) : 0

    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 100 } }

    contentItem: Text {
        text: root.text
        color: Theme.text
        font.pixelSize: Theme.fontSizeLabel
        wrapMode: Text.WordWrap
    }

    background: Item {
        id: bg
        Rectangle {
            anchors.fill: parent
            radius: Theme.radiusSmall
            color: Theme.card
            border.width: 1
            border.color: Theme.border
        }
        // The arrow: a turned square with the same hairline, and a strip of card colour over
        // its inner half so it joins the box without a line.
        Rectangle {
            width: 9
            height: 9
            rotation: 45
            x: Math.round(root._arrowX - width / 2)
            y: root._below ? -4 : parent.height - 5
            color: Theme.card
            border.width: 1
            border.color: Theme.border
        }
        Rectangle {
            width: 14
            height: 6
            x: Math.round(root._arrowX - width / 2)
            y: root._below ? 1 : parent.height - 7
            color: Theme.card
        }
    }
}
