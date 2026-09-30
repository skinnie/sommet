import QtQuick
import AmbitApp
import "../ActivityViewLogic.js" as AVL
import "../ActivityChartDraw.js" as ACD

// The activity screen's chart (André, 2026-09-27, designed in the mockup artifact
// 8KKpqaVBCdPUvDUAhzya8h): every picked metric overlaid in ONE box, each stretched to fit.
//  - One MAIN line (filled, the only one with a scale on the left, an "avg" tag and a max flag,
//    all with units); the others are thin context. Click a name in the legend to make it main.
//  - Hover: a card with every value at the cursor (and the dot on the map, via hovered()).
//  - Drag across = zoom into that stretch; mouse wheel zooms at the cursor; sideways wheel or
//    Shift+wheel moves along; double-click shows everything. Zoom lives in the caller so the
//    map can follow it (and set it).
//  - Overview strip underneath, while zoomed: where the zoom window sits; drag it to move, click to jump.
//  - Optional colour strip + zone-coloured main line (ribbon), green = easy, red = hard, and a
//    planned workout's power targets behind the power line (indoor rides).
// All the DRAWING is shared/activity_chart_draw.js (ActivityChartDraw.js here) - the same code the
// Android app's chart page runs; this file only feeds it and handles the mouse.
Item {
    id: root
    property var st: null                 // tools/activity_streams.py output
    property var channels: []             // [{id, label, unit, key, color, pace?, mul?, min?, min_span?}] - the ones switched on
    property string focusId: ""
    property bool isDist: true
    property var zoom: null               // [i0, i1] stream indexes, or null = everything
    property var ribbon: null             // {key, bands, colourOf(b) -> "z1".., label(b)} or null
    property int hoverIdx: -1
    property var target: null             // planned workout {name, blocks:[{t0,t1,lo,hi}]} (time axis only)
    property var zonePalette: ({})        // {z1..z7, down, casing} colours for the theme

    signal hovered(int idx)
    signal zoomRequested(var range)       // [i0, i1] or null
    signal focusRequested(string id)

    // The overview strip only shows while zoomed (André, 2026-09-29): unzoomed it just repeats the chart.
    implicitHeight: 300 + (zoom ? 66 : 0)

    readonly property var _xv: {
        if (!st || !st.streams || !st.streams.t) return []
        const s = st.streams
        if (isDist && s.dist) return s.dist.map(v => v === null ? NaN : v)
        const t0 = s.t[0]
        return s.t.map(v => v - t0)
    }
    readonly property int _n: _xv.length
    readonly property int _i0: zoom ? Math.max(0, zoom[0]) : 0
    readonly property int _i1: zoom ? Math.min(_n - 1, zoom[1]) : _n - 1
    readonly property var _focus: {
        for (const c of channels) if (c.id === focusId) return c
        return channels.length ? channels[0] : null
    }
    property var _geo: null               // set by the plot paint, used by the cursor + pointer code
    property var _sel: null               // [a, b] while dragging

    // Everything the shared drawing code needs, in plain values (colours as "#rrggbb").
    readonly property var _labels: ({
        altitude: qsTr("Altitude"), fasterUp: qsTr("(faster is up)"), plannedTarget: qsTr("Planned target"),
        avg: qsTr("avg"), max: qsTr("max"), best: qsTr("best"), top: qsTr("top"), target: qsTr("Target"),
        colour: qsTr("Colour"), stripHint: qsTr("Whole activity · drag the box to move along"),
        median: qsTr("median") })
    function _opts() {
        return {
            L: AVL, st: st, xv: _xv, i0: _i0, i1: _i1, channels: channels, focus: _focus, ribbon: ribbon,
            target: target, isDist: isDist, zone: zonePalette, labels: _labels,
            colors: { text: String(Theme.text), muted: String(Theme.mutedText), border: String(Theme.border),
                      borderStrong: String(Theme.borderStrong), card: String(Theme.card), cardNested: String(Theme.cardNested),
                      background: String(Theme.background), primary: String(Theme.primary), hard: String(Theme.hard),
                      warning: String(Theme.warning), secondary: String(Theme.secondary), accent: String(Theme.accent) }
        }
    }
    function _repaintAll() { plot.requestPaint(); cursor.requestPaint(); strip.requestPaint() }
    onStChanged: _repaintAll()
    onChannelsChanged: _repaintAll()
    onFocusIdChanged: _repaintAll()
    onIsDistChanged: _repaintAll()
    onZoomChanged: _repaintAll()
    onRibbonChanged: plot.requestPaint()
    onTargetChanged: plot.requestPaint()
    onHoverIdxChanged: cursor.requestPaint()
    onZonePaletteChanged: plot.requestPaint()
    onWidthChanged: _repaintAll()
    Connections { target: Theme; function onIsDarkChanged() { root._repaintAll() } }

    Canvas {
        id: plot
        width: parent.width
        height: 300
        renderStrategy: Canvas.Cooperative
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            root._geo = root.st && root._n > 1 ? ACD.drawPlot(ctx, width, height, root._opts()) : null
            cursor.requestPaint()
        }
    }
    Canvas {
        id: cursor
        anchors.fill: plot
        renderStrategy: Canvas.Cooperative
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            ACD.drawCursor(ctx, width, height, root._opts(), root._geo, root.hoverIdx, root._sel)
        }
    }

    property real _zoomA: 0               // fractional window, so small wheel steps accumulate
    property real _zoomB: 0
    function _setZoom(a, b) { zoomRequested(ACD.zoomRange(_n, a, b)) }

    MouseArea {
        anchors.fill: plot
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton
        property int pressIdx: -1
        property bool dragging: false
        onPositionChanged: (mouse) => {
            const k = ACD.idxAt(root._geo, root._xv, mouse.x)
            if (pressed && pressIdx >= 0) {
                if (Math.abs(k - pressIdx) >= 1) { dragging = true; root._sel = [pressIdx, k]; cursor.requestPaint() }
                return
            }
            if (k !== root.hoverIdx) root.hovered(k)
        }
        onExited: if (!pressed) root.hovered(-1)
        onPressed: (mouse) => {
            for (const h of (root._geo ? root._geo.legendHits : []))
                if (mouse.x >= h.x && mouse.x <= h.x + h.w && mouse.y >= h.y && mouse.y <= h.y + h.h) {
                    root.focusRequested(h.id); pressIdx = -1; return
                }
            pressIdx = ACD.idxAt(root._geo, root._xv, mouse.x); dragging = false
        }
        onReleased: {
            if (dragging && root._sel) {
                const a = Math.min(root._sel[0], root._sel[1]), b = Math.max(root._sel[0], root._sel[1])
                root._sel = null
                if (b - a >= 4) { root._zoomA = a; root._zoomB = b; root.zoomRequested([a, b]) }
                cursor.requestPaint()
            }
            pressIdx = -1; dragging = false
        }
        onDoubleClicked: root.zoomRequested(null)
        onWheel: (wheel) => {
            const n = root._n - 1
            let a = root.zoom ? root.zoom[0] : 0, b = root.zoom ? root.zoom[1] : n
            if (root.zoom && Math.round(root._zoomA) === a && Math.round(root._zoomB) === b) { a = root._zoomA; b = root._zoomB }
            const dx = wheel.angleDelta.x, dy = wheel.angleDelta.y
            if (Math.abs(dx) > Math.abs(dy) || (wheel.modifiers & Qt.ShiftModifier)) {
                if (!root.zoom) return
                const d = -(dx || dy) / 120 * (b - a) * 0.15
                root._zoomA = a + d; root._zoomB = b + d
                root._setZoom(a + d, b + d)
            } else {
                const c = ACD.idxAt(root._geo, root._xv, wheel.x)
                const f = Math.exp(-Math.max(-360, Math.min(360, dy)) / 120 * 0.25)
                const na = c - (c - a) * f, nb = c + (b - c) * f
                root._zoomA = na; root._zoomB = nb
                root._setZoom(na, nb)
                root.hovered(c)
            }
        }
    }

    Canvas {
        id: strip
        visible: !!root.zoom
        y: plot.height + 6
        width: parent.width
        height: 60
        renderStrategy: Canvas.Cooperative
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            if (root.st && root._n > 1) ACD.drawStrip(ctx, width, height, root._opts())
        }
    }
    MouseArea {
        anchors.fill: strip
        enabled: strip.visible
        cursorShape: Qt.OpenHandCursor
        function centreOn(k) {
            const w = root.zoom ? root._i1 - root._i0 : Math.round((root._n - 1) / 4)
            root._setZoom(k - w / 2, k + w / 2)
        }
        onPressed: (mouse) => centreOn(ACD.stripIdxAt(strip.width, root._xv, mouse.x))
        onPositionChanged: (mouse) => { if (pressed) centreOn(ACD.stripIdxAt(strip.width, root._xv, mouse.x)) }
        onWheel: (wheel) => {
            if (!root.zoom) return
            const w = root._i1 - root._i0, d = -(wheel.angleDelta.x || wheel.angleDelta.y) / 120 * w * 0.15
            root._setZoom(root._i0 + d, root._i1 + d)
        }
    }
}
