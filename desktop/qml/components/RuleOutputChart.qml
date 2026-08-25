import QtQuick
import AmbitApp

// A lightweight, dependency-free line chart for one logged Suunto App output (a ruleoutput
// series carried on an activity as {label, times[], values[]}). The app has no charting
// library by design (see ActivityDetail.qml); this draws directly on a Canvas so the
// "Charts" tab can show the real per-sample app data with no new dependency.
Item {
    id: root

    // { label: string, times: [seconds...], values: [int...] }
    property var series: null
    readonly property var _times: series && series.times ? series.times : []
    readonly property var _values: series && series.values ? series.values : []
    readonly property bool hasData: _values.length > 1

    implicitHeight: 200

    onSeriesChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()

    Column {
        anchors.fill: parent
        spacing: Theme.spacingSmall

        Text {
            text: (series && series.label ? series.label : "")
                  + (root.hasData ? "" : qsTr(" — no samples"))
            color: Theme.text
            font.pixelSize: Theme.fontSizeLabel
            font.bold: true
        }

        Rectangle {
            width: parent.width
            height: root.height - parent.spacing - 20
            radius: Theme.radiusSmall
            color: Theme.card
            border.color: Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.25)
            border.width: 1

            Canvas {
                id: canvas
                anchors.fill: parent
                anchors.margins: 1

                // Pixel position of each plotted sample (filled in onPaint), used to hit-test the
                // pointer so hovering or tapping shows that sample's time and value - same pattern
                // as the Weight and Health charts.
                property var pts: []
                property int hoverIndex: -1

                // Connect to theme changes so the line/labels repaint on light/dark switch.
                Connections { target: Theme; function onOverrideChanged() { canvas.requestPaint() } }

                onPaint: {
                    var ctx = getContext("2d");
                    ctx.reset();
                    var W = width, H = height;
                    ctx.clearRect(0, 0, W, H);
                    if (!root.hasData)
                        return;

                    var vals = root._values, times = root._times;
                    var n = vals.length;
                    var padL = 46, padR = 10, padT = 12, padB = 22;
                    var plotW = W - padL - padR, plotH = H - padT - padB;

                    var vMin = vals[0], vMax = vals[0];
                    for (var i = 1; i < n; ++i) {
                        if (vals[i] < vMin) vMin = vals[i];
                        if (vals[i] > vMax) vMax = vals[i];
                    }
                    if (vMax === vMin) vMax = vMin + 1;
                    var tMin = times.length ? times[0] : 0;
                    var tMax = times.length ? times[times.length - 1] : n - 1;
                    if (tMax === tMin) tMax = tMin + 1;

                    function px(t) { return padL + (t - tMin) / (tMax - tMin) * plotW; }
                    function py(v) { return padT + (1 - (v - vMin) / (vMax - vMin)) * plotH; }

                    var axis = Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.5);
                    var grid = Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.18);

                    // horizontal gridlines + y labels (min, mid, max)
                    ctx.font = "10px sans-serif";
                    ctx.fillStyle = Theme.mutedText;
                    ctx.textAlign = "right";
                    ctx.textBaseline = "middle";
                    for (var g = 0; g <= 2; ++g) {
                        var vy = vMin + (vMax - vMin) * g / 2;
                        var y = py(vy);
                        ctx.strokeStyle = grid;
                        ctx.lineWidth = 1;
                        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
                        ctx.fillText(Math.round(vy).toString(), padL - 6, y);
                    }

                    // x labels (start / end minutes)
                    ctx.textAlign = "center";
                    ctx.textBaseline = "top";
                    ctx.fillText("0:00", padL, H - padB + 4);
                    var endMin = Math.floor(tMax / 60), endSec = Math.round(tMax % 60);
                    ctx.fillText(endMin + ":" + (endSec < 10 ? "0" : "") + endSec,
                                 W - padR, H - padB + 4);

                    // axes
                    ctx.strokeStyle = axis;
                    ctx.beginPath();
                    ctx.moveTo(padL, padT); ctx.lineTo(padL, H - padB);
                    ctx.lineTo(W - padR, H - padB);
                    ctx.stroke();

                    // the series line
                    ctx.strokeStyle = Theme.accent;
                    ctx.lineWidth = 2;
                    ctx.lineJoin = "round";
                    ctx.beginPath();
                    var pp = [];
                    for (var k = 0; k < n; ++k) {
                        var x = px(times.length ? times[k] : k);
                        var yy = py(vals[k]);
                        pp.push({ x: x, y: yy });
                        if (k === 0) ctx.moveTo(x, yy); else ctx.lineTo(x, yy);
                    }
                    ctx.stroke();
                    // Only the hovered sample gets a dot (there can be hundreds of samples, so we
                    // don't dot them all - just highlight the one under the pointer).
                    if (canvas.hoverIndex >= 0 && canvas.hoverIndex < pp.length) {
                        ctx.fillStyle = Theme.text;
                        ctx.beginPath();
                        ctx.arc(pp[canvas.hoverIndex].x, pp[canvas.hoverIndex].y, 5, 0, 2 * Math.PI);
                        ctx.fill();
                    }
                    canvas.pts = pp;
                }

                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    function pick(mx, my) {
                        // Nearest by x (a dense time-series), so any vertical position over a
                        // sample's column selects it.
                        var best = -1, bestD = 1e9;
                        for (var i = 0; i < canvas.pts.length; ++i) {
                            var dx = Math.abs(canvas.pts[i].x - mx);
                            if (dx < bestD) { bestD = dx; best = i; }
                        }
                        if (bestD > 24) best = -1;   // pointer not near the line
                        if (best !== canvas.hoverIndex) { canvas.hoverIndex = best; canvas.requestPaint() }
                    }
                    onPositionChanged: (m) => pick(m.x, m.y)
                    onClicked: (m) => pick(m.x, m.y)
                    onExited: { canvas.hoverIndex = -1; canvas.requestPaint() }
                }

                // Tooltip for the hovered/tapped sample: its time (m:ss) and value.
                Rectangle {
                    id: tip
                    visible: canvas.hoverIndex >= 0 && canvas.hoverIndex < root._values.length
                    readonly property int idx: canvas.hoverIndex
                    readonly property var xy: (visible && idx < canvas.pts.length)
                                              ? canvas.pts[idx] : { x: 0, y: 0 }
                    readonly property int t: (visible && idx >= 0 && idx < root._times.length)
                                             ? root._times[idx] : 0
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
                        Text {
                            text: {
                                var mm = Math.floor(tip.t / 60);
                                var ss = Math.round(tip.t % 60);
                                return mm + ":" + (ss < 10 ? "0" : "") + ss;
                            }
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeTiny
                        }
                        Text {
                            text: (tip.idx >= 0 && tip.idx < root._values.length)
                                  ? "" + root._values[tip.idx] : ""
                            color: Theme.accent; font.pixelSize: Theme.fontSizeCaption; font.bold: true
                        }
                    }
                }
            }
        }
    }
}
