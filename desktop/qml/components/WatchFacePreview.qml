import QtQuick
import SommetUi

// Real, 2026-08-09 ("Full SuuntoLink-style redesign") - matches SuuntoLink's own real
// circular screen-preview mockup (assets/ambit3 pcap/v2/screens sports modes/
// 8displaysmax.JPG, displaytype.JPG) - a plain circle showing the current screen's row
// layout. Deliberately shows row *position* (numbered, or a small graph glyph), not real
// field content - SuuntoLink's own mockup does the same (its circles just show "1"/"2"/"3",
// not the actual assigned data), and this project has no live sample values to show
// honestly here anyway.
Item {
    id: root

    // "1row" | "2rows" | "3rows" | "graph" | "map" | "builtin" - builtin/map get a plain
    // icon instead of numbered rows (there's real, confirmed data for what a built-in
    // screen's own row count even means - see custom_modes.py's system_tail_length()).
    property string layoutType: "3rows"
    property bool selected: false
    property real diameter: 120

    // When true, each numbered row is its own click target - André's idea, 2026-08-11:
    // "click on the numbers, to choose the data fields (appearing on a new window)". Pointing
    // at the row you mean is a shorter path than reading a list underneath and finding its
    // Change button, and it is something SuuntoLink does not do. Off by default so the small
    // thumbnails in the filmstrip stay a single target for selecting the display.
    property bool rowsClickable: false

    // 0 = Top, 1 = Center, 2 = Bottom, matching the order the rows are drawn and the order
    // custom_modes.py reports them.
    signal rowClicked(int rowIndex)

    implicitWidth: diameter
    implicitHeight: diameter

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: Theme.background
        border.width: root.selected ? 3 : 1
        border.color: root.selected ? Theme.primary : Theme.mutedText
        Behavior on border.color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }

        Column {
            anchors.centerIn: parent
            width: parent.width * 0.6
            spacing: parent.height * 0.06
            visible: root.layoutType === "1row" || root.layoutType === "2rows"
                     || root.layoutType === "3rows"

            Repeater {
                model: root.layoutType === "1row" ? 1 : root.layoutType === "2rows" ? 2 : 3
                delegate: Item {
                    id: rowItem
                    required property int index
                    width: parent.width
                    height: rowNumber.implicitHeight

                    // The whole band is the target, not just the digit - André, 2026-08-11:
                    // "the user will instinctively click on that zone... hence why I spoke
                    // about an area". Tinting the glyph alone told them WHAT was clickable
                    // but not HOW BIG it was, so the miss rate would be all the space around
                    // a small number.
                    Rectangle {
                        anchors.fill: parent
                        radius: height / 2
                        visible: root.rowsClickable && rowHover.hovered
                        color: Theme.primary
                        opacity: 0.16
                    }

                    Text {
                        id: rowNumber
                        anchors.fill: parent
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        text: String(rowItem.index + 1)
                        font.pixelSize: root.diameter * 0.16
                        font.bold: true
                        // Highlights under the pointer when the rows are clickable, so it is
                        // discoverable that they are.
                        color: root.rowsClickable && rowHover.hovered ? Theme.primary : Theme.text
                        Behavior on color { ColorAnimation { duration: 100 } }
                    }
                    HoverHandler {
                        id: rowHover
                        enabled: root.rowsClickable
                        cursorShape: Qt.PointingHandCursor
                    }
                    TapHandler {
                        enabled: root.rowsClickable
                        // Takes the tap before the whole-preview handler behind it, so
                        // clicking a number edits that row rather than re-selecting the
                        // display or opening the layout picker.
                        gesturePolicy: TapHandler.ReleaseWithinBounds
                        onTapped: root.rowClicked(rowItem.index)
                    }
                }
            }
        }

        // "1 Row with graph" - a small squiggle standing in for the real graph area
        // (matches SuuntoLink's own icon in displaytype.JPG), plus the one real data row
        // below it.
        Column {
            anchors.centerIn: parent
            width: parent.width * 0.7
            spacing: parent.height * 0.05
            visible: root.layoutType === "graph"

            // The graph AREA is a target too - André, 2026-08-11: "on the case of graph we
            // could click on graph or number..maybe an area". It is the right split
            // semantically: the squiggle stands for the value being graphed, which is stored
            // as row 0 (row 1 is its generated graph, not something anyone picks), and the
            // number below is the display's own data row at index 2.
            Item {
                width: parent.width
                height: root.diameter * 0.28

                // The graph ZONE is the target for the graphed value, and it looks like one.
                Rectangle {
                    anchors.fill: parent
                    radius: height / 2
                    visible: root.rowsClickable && graphAreaHover.hovered
                    color: Theme.primary
                    opacity: 0.16
                }

                HoverHandler {
                    id: graphAreaHover
                    enabled: root.rowsClickable
                    cursorShape: Qt.PointingHandCursor
                }
                TapHandler {
                    enabled: root.rowsClickable
                    gesturePolicy: TapHandler.ReleaseWithinBounds
                    onTapped: root.rowClicked(0)
                }

            Canvas {
                anchors.fill: parent
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    ctx.strokeStyle = (root.rowsClickable && graphAreaHover.hovered)
                                      ? Theme.accent : Theme.primary
                    ctx.lineWidth = 2
                    ctx.beginPath()
                    ctx.moveTo(0, height * 0.7)
                    ctx.lineTo(width * 0.25, height * 0.2)
                    ctx.lineTo(width * 0.5, height * 0.6)
                    ctx.lineTo(width * 0.75, height * 0.1)
                    ctx.lineTo(width, height * 0.5)
                    ctx.stroke()
                }
                // Repaint when the hover colour changes, or the squiggle keeps its old ink.
                Connections {
                    target: graphAreaHover
                    function onHoveredChanged() { parent.requestPaint() }
                }
            }
            }
            // A graph display's own single data row - the bottom one. Its stored index is 2
            // (Top/Center hold the graphed value and its generated graph), so that is what
            // this reports rather than 0.
            Item {
                width: parent.width
                height: graphRowNumber.implicitHeight
                Rectangle {
                    anchors.fill: parent
                    radius: height / 2
                    visible: root.rowsClickable && graphHover.hovered
                    color: Theme.primary
                    opacity: 0.16
                }
                Text {
                    id: graphRowNumber
                    anchors.fill: parent
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                    text: "1"
                    font.pixelSize: root.diameter * 0.14
                    font.bold: true
                    color: root.rowsClickable && graphHover.hovered ? Theme.primary : Theme.text
                    Behavior on color { ColorAnimation { duration: 100 } }
                }
                HoverHandler {
                    id: graphHover
                    enabled: root.rowsClickable
                    cursorShape: Qt.PointingHandCursor
                }
                TapHandler {
                    enabled: root.rowsClickable
                    gesturePolicy: TapHandler.ReleaseWithinBounds
                    onTapped: root.rowClicked(2)
                }
            }
        }

        Icon {
            visible: root.layoutType === "map"
            anchors.centerIn: parent
            glyph: Icons.routes
            size: root.diameter * 0.32
            color: Theme.mutedText
        }

        Icon {
            visible: root.layoutType === "builtin"
            anchors.centerIn: parent
            glyph: Icons.watch
            size: root.diameter * 0.32
            color: Theme.mutedText
        }
    }
}
