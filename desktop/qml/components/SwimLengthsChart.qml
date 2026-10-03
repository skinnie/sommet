import QtQuick
import QtQuick.Controls
import AmbitApp
import "../ActivityViewLogic.js" as AVL
import "../ActivityChartDraw.js" as ACD

// Pool swim chart (André, 2026-09-27): one bar per length, in order, grouped into sets - the
// lengths between rests at the wall (rest >= 10 s, as Apple/Garmin group them). Bar height is
// the time for that length (rest stacked on top in grey) or the pace per 100 m; colour is the
// stroke or the speed (green slowest, red fastest). Optional heart-rate line. Hover a bar for
// everything about that length. Data: tools/activity_streams.py lengths/sets.
Column {
    id: root
    property var st: null
    property var zonePalette: ({})
    property string yMode: "len"          // "len" = seconds per length, "pace" = per 100 m
    property string colourMode: "stroke"  // "stroke" | "speed"
    property bool showHr: false
    spacing: Theme.spacingSmall

    readonly property var _lengths: st && st.lengths ? st.lengths.filter(l => l.active && l.swim_s) : []
    readonly property real _pool: st && st.summary && st.summary.pool_length_m ? st.summary.pool_length_m : 25
    property int _hover: -1

    component Seg: Rectangle {
        property bool on: false
        property string label: ""
        signal picked()
        width: segText.implicitWidth + 20; height: 24; radius: 12
        color: on ? Theme.cardNested : "transparent"
        border.color: Theme.border
        Text { id: segText; anchors.centerIn: parent; text: parent.label; color: parent.on ? Theme.text : Theme.mutedText
               font.pixelSize: Theme.fontSizeLabel }
        TapHandler { onTapped: parent.picked() }
    }

    Flow {
        width: parent.width
        spacing: 8
        Seg { label: qsTr("Time per length"); on: root.yMode === "len"; onPicked: root.yMode = "len" }
        Seg { label: qsTr("Pace per 100 m"); on: root.yMode === "pace"; onPicked: root.yMode = "pace" }
        Item { width: 8; height: 1 }
        Seg { label: qsTr("Colour: stroke"); on: root.colourMode === "stroke"; onPicked: root.colourMode = "stroke" }
        Seg { label: qsTr("Colour: speed"); on: root.colourMode === "speed"; onPicked: root.colourMode = "speed" }
        Item { width: 8; height: 1 }
        Seg { label: qsTr("Heart rate"); on: root.showHr; visible: root._lengths.some(l => l.avg_hr); onPicked: root.showHr = !root.showHr }
    }

    Canvas {
        id: bars
        width: parent.width
        height: 250
        renderStrategy: Canvas.Cooperative
        property var _geo: null
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            // Drawn by the shared shared/activity_chart_draw.js (same code as the Android app).
            _geo = ACD.drawSwim(ctx, width, height, {
                L: AVL, st: root.st, yMode: root.yMode, colourMode: root.colourMode, showHr: root.showHr, hover: root._hover,
                zone: root.zonePalette, labels: { median: qsTr("median") },
                colors: { text: String(Theme.text), muted: String(Theme.mutedText), border: String(Theme.border),
                          borderStrong: String(Theme.borderStrong), background: String(Theme.background),
                          primary: String(Theme.primary), hard: String(Theme.hard),
                          secondary: String(Theme.secondary), warning: String(Theme.warning) } })
        }
        MouseArea {
            anchors.fill: parent
            hoverEnabled: true
            onPositionChanged: (mouse) => {
                const g = bars._geo
                if (!g) return
                const i = Math.floor((mouse.x - g.L) / g.bw)
                root._hover = i >= 0 && i < g.n ? i : -1
                bars.requestPaint()
            }
            onExited: { root._hover = -1; bars.requestPaint() }
        }
        ToolTip {
            visible: root._hover >= 0
            x: Math.min(bars.width - width, Math.max(0, (bars._geo ? bars._geo.L + (root._hover + 0.5) * bars._geo.bw : 0) - width / 2))
            y: 0
            text: {
                const l = root._lengths[root._hover]
                if (!l) return ""
                return qsTr("Length %1: %2 s (%3 per 100 m), %4, %5 strokes").arg(l.n).arg(l.swim_s.toFixed(1))
                        .arg(AVL.fmtClock(l.swim_s * 100 / root._pool)).arg(l.stroke || qsTr("stroke unknown")).arg(l.strokes || "?")
                       + (l.rest_s >= 1 ? qsTr(", then %1 s rest").arg(Math.round(l.rest_s)) : "")
                       + (l.avg_hr ? qsTr(", heart rate %1").arg(l.avg_hr) : "")
            }
        }
    }
    onYModeChanged: bars.requestPaint()
    onColourModeChanged: bars.requestPaint()
    onShowHrChanged: bars.requestPaint()
    onStChanged: bars.requestPaint()
    onWidthChanged: bars.requestPaint()
    Connections { target: Theme; function onIsDarkChanged() { bars.requestPaint() } }

    Flow {
        width: parent.width
        spacing: 14
        Repeater {
            // The strokes this swim has (or the speed scale) - shared with Android (ACD.swimLegend).
            model: ACD.swimLegend(root.st, root.colourMode)
            delegate: Row {
                spacing: 6
                Rectangle { width: 10; height: 10; radius: 3; anchors.verticalCenter: parent.verticalCenter
                            color: ACD.legendColour({ zone: root.zonePalette, colors: { primary: String(Theme.primary),
                                       secondary: String(Theme.secondary), warning: String(Theme.warning),
                                       muted: String(Theme.mutedText) } }, modelData.key) }
                Text { text: qsTr(modelData.label); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
            }
        }
        Row {
            visible: root.yMode === "len"
            spacing: 6
            Rectangle { width: 10; height: 10; radius: 3; color: Theme.borderStrong; anchors.verticalCenter: parent.verticalCenter }
            Text { text: qsTr("Rest at the wall"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
        }
    }
    Text {
        width: parent.width
        wrapMode: Text.WordWrap
        visible: root.st && root.st.sets && root.st.sets.length > 0
        text: root.st && root.st.sets
              ? qsTr("%1 sets between rests at the wall. Longest non-stop: %2 m.").arg(root.st.sets.length).arg(root.st.longest_nonstop_m)
              : ""
        color: Theme.mutedText
        font.pixelSize: Theme.fontSizeCaption
    }
}
