import QtQuick
import AmbitApp

// A small dependency-free date-series line chart (dots at each point), for the Health page's
// resting-HR and steps series. series = [{date: "YYYY-MM-DD", value: number}]. Same Canvas
// approach as RuleOutputChart / the Weight chart, generalised to a date x-axis.
Item {
    id: root
    property var series: []
    property string label: ""
    property string unit: ""
    // Line/dot colour. Defaults to the theme accent so existing charts are unchanged; the
    // Health page overrides it to give the Ambit3 morning-HRV series its own distinct colour,
    // since it is a different measurement from the overnight HRV line drawn beside it.
    property color lineColor: Theme.accent
    readonly property bool hasData: series && series.length > 1
    implicitHeight: 200

    onSeriesChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()
    onLineColorChanged: canvas.requestPaint()

    Column {
        anchors.fill: parent
        spacing: Theme.spacingSmall
        Row {
            spacing: Theme.spacingSmall
            Text { text: root.label; color: Theme.text; font.pixelSize: Theme.fontSizeLabel
                   font.bold: true }
            Text {
                visible: root.hasData
                text: (Math.round(root.series[root.series.length - 1].value * 10) / 10) + root.unit
                color: root.lineColor; font.pixelSize: Theme.fontSizeLabel; font.bold: true
            }
        }
        Rectangle {
            width: parent.width
            height: root.height - parent.spacing - 18
            radius: Theme.radiusSmall
            color: Theme.card
            border.color: Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.25)
            border.width: 1
            Canvas {
                id: canvas
                anchors.fill: parent
                anchors.margins: 1
                // Pixel position of each plotted point (filled in onPaint), used to hit-test the
                // pointer so hovering or tapping a point shows that day's reading - same pattern
                // as the Weight chart.
                property var pts: []
                property int hoverIndex: -1
                Connections { target: Theme; function onOverrideChanged() { canvas.requestPaint() } }
                onPaint: {
                    var ctx = getContext("2d"); ctx.reset();
                    var W = width, H = height; ctx.clearRect(0, 0, W, H);
                    var s = root.series;
                    if (!s || s.length < 2) return;
                    var xs = [], ys = [];
                    for (var i = 0; i < s.length; ++i) { xs.push(Date.parse(s[i].date)); ys.push(s[i].value); }
                    var xMin = xs[0], xMax = xs[xs.length - 1]; if (xMax === xMin) xMax = xMin + 1;
                    var yMin = ys[0], yMax = ys[0];
                    for (var j = 1; j < ys.length; ++j) { if (ys[j] < yMin) yMin = ys[j]; if (ys[j] > yMax) yMax = ys[j]; }
                    var pad = Math.max(1, (yMax - yMin) * 0.15); yMin -= pad; yMax += pad;
                    var padL = 46, padR = 12, padT = 12, padB = 22, pW = W - padL - padR, pH = H - padT - padB;
                    function px(t) { return padL + (t - xMin) / (xMax - xMin) * pW; }
                    function py(v) { return padT + (1 - (v - yMin) / (yMax - yMin)) * pH; }
                    ctx.font = "10px sans-serif"; ctx.fillStyle = Theme.mutedText;
                    ctx.textAlign = "right"; ctx.textBaseline = "middle";
                    for (var g = 0; g <= 2; ++g) {
                        var vy = yMin + (yMax - yMin) * g / 2, y = py(vy);
                        ctx.strokeStyle = Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.16);
                        ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
                        ctx.fillText(Math.round(vy).toString(), padL - 6, y);
                    }
                    ctx.textAlign = "center"; ctx.textBaseline = "top";
                    ctx.fillText(s[0].date, padL, H - padB + 4);
                    ctx.fillText(s[s.length - 1].date, W - padR, H - padB + 4);
                    ctx.strokeStyle = Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.5);
                    ctx.beginPath(); ctx.moveTo(padL, padT); ctx.lineTo(padL, H - padB); ctx.lineTo(W - padR, H - padB); ctx.stroke();
                    ctx.strokeStyle = root.lineColor; ctx.lineWidth = 2; ctx.lineJoin = "round"; ctx.beginPath();
                    var pp = [];
                    for (var k = 0; k < s.length; ++k) { var x = px(xs[k]), yy = py(ys[k]); pp.push({ x: x, y: yy }); if (k === 0) ctx.moveTo(x, yy); else ctx.lineTo(x, yy); }
                    ctx.stroke();
                    for (var m = 0; m < s.length; ++m) {
                        ctx.fillStyle = (m === canvas.hoverIndex) ? Theme.text : root.lineColor;
                        ctx.beginPath(); ctx.arc(pp[m].x, pp[m].y, m === canvas.hoverIndex ? 5 : 2.5, 0, 2 * Math.PI); ctx.fill();
                    }
                    canvas.pts = pp;
                }

                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    function pick(mx, my) {
                        var best = -1, bestD = 400;   // within ~20px
                        for (var i = 0; i < canvas.pts.length; ++i) {
                            var dx = canvas.pts[i].x - mx, dy = canvas.pts[i].y - my;
                            var d = dx * dx + dy * dy;
                            if (d < bestD) { bestD = d; best = i; }
                        }
                        if (best !== canvas.hoverIndex) { canvas.hoverIndex = best; canvas.requestPaint() }
                    }
                    onPositionChanged: (m) => pick(m.x, m.y)
                    onClicked: (m) => pick(m.x, m.y)
                    onExited: { canvas.hoverIndex = -1; canvas.requestPaint() }
                }

                // Tooltip for the hovered/tapped point: that day's date and value.
                Rectangle {
                    visible: canvas.hoverIndex >= 0 && canvas.hoverIndex < (root.series ? root.series.length : 0)
                    readonly property var pt: visible ? root.series[canvas.hoverIndex] : null
                    readonly property var xy: (visible && canvas.hoverIndex < canvas.pts.length)
                                              ? canvas.pts[canvas.hoverIndex] : { x: 0, y: 0 }
                    x: Math.max(2, Math.min(canvas.width - width - 2, xy.x - width / 2))
                    y: Math.max(2, xy.y - height - 8)
                    width: tipCol.implicitWidth + 16
                    height: tipCol.implicitHeight + 12
                    // Converged onto Theme.cardNested/Theme.border (2026-08-25, "redo them
                    // also" - the chart-hover tooltips, same token swap as every flat-tile
                    // element this session).
                    radius: Theme.radiusSmall
                    color: Theme.cardNested
                    border.color: Theme.border
                    border.width: 1
                    Column {
                        id: tipCol
                        anchors.centerIn: parent
                        spacing: 1
                        readonly property var p: parent.pt
                        Text { text: tipCol.p ? tipCol.p.date : ""
                               color: Theme.mutedText; font.pixelSize: Theme.fontSizeTiny }
                        Text { text: tipCol.p ? (Math.round(tipCol.p.value * 10) / 10) + root.unit : ""
                               color: root.lineColor; font.pixelSize: Theme.fontSizeCaption; font.bold: true }
                    }
                }
            }
        }
    }
}
