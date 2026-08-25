import QtQuick
import AmbitApp

// Ember bar chart (André, 2026-08-25) - the amber/green bars from the Ember concept mockup,
// drawn on a Canvas like MetricChart, with an optional dashed goal line. series = [{date, value}].
Item {
    id: root
    property var series: []
    property string label: ""
    property string unit: ""
    property real goal: 0
    property color barColor: Theme.warning
    implicitHeight: 210

    onSeriesChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()
    onGoalChanged: canvas.requestPaint()

    Column {
        anchors.fill: parent
        spacing: Theme.spacingSmall
        Text { text: root.label; color: Theme.text; font.pixelSize: Theme.fontSizeLabel; font.bold: true }
        Canvas {
            id: canvas
            width: parent.width
            height: root.height - parent.spacing - 18
            Connections { target: Theme; function onOverrideChanged() { canvas.requestPaint() } }
            onPaint: {
                var ctx = getContext("2d"); ctx.reset()
                var W = width, H = height, s = root.series || []
                var padL = 40, padR = 10, padT = 12, padB = 22, pW = W - padL - padR, pH = H - padT - padB
                var maxV = root.goal || 0
                for (var i = 0; i < s.length; ++i) if (s[i].value > maxV) maxV = s[i].value
                maxV = (maxV * 1.15) || 1
                ctx.font = "10px sans-serif"
                ctx.textAlign = "right"; ctx.textBaseline = "middle"
                for (var g = 0; g <= 3; ++g) {
                    var v = maxV * g / 3, y = padT + pH - (v / maxV) * pH
                    ctx.strokeStyle = Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.14)
                    ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke()
                    ctx.fillStyle = Theme.mutedText; ctx.fillText(Math.round(v).toString(), padL - 6, y)
                }
                if (s.length) {
                    var bw = pW / s.length, bar = Math.min(24, bw * 0.6), rr = 4
                    for (var j = 0; j < s.length; ++j) {
                        var x = padL + bw * j + (bw - bar) / 2
                        var h = Math.max(2, (s[j].value / maxV) * pH), yy = padT + pH - h
                        ctx.fillStyle = root.barColor
                        ctx.beginPath()
                        ctx.moveTo(x, yy + rr); ctx.arcTo(x, yy, x + rr, yy, rr)
                        ctx.lineTo(x + bar - rr, yy); ctx.arcTo(x + bar, yy, x + bar, yy + rr, rr)
                        ctx.lineTo(x + bar, padT + pH); ctx.lineTo(x, padT + pH); ctx.closePath(); ctx.fill()
                        if (j % 2 === 0 || j === s.length - 1) {
                            ctx.fillStyle = Theme.mutedText; ctx.textAlign = "center"; ctx.textBaseline = "top"
                            ctx.fillText(("" + s[j].date).slice(8), x + bar / 2, H - padB + 4)
                            ctx.textAlign = "right"; ctx.textBaseline = "middle"
                        }
                    }
                }
                if (root.goal > 0) {
                    var gy = padT + pH - (root.goal / maxV) * pH
                    ctx.strokeStyle = Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.6)
                    ctx.setLineDash([4, 4]); ctx.lineWidth = 1.5
                    ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(W - padR, gy); ctx.stroke(); ctx.setLineDash([])
                    ctx.fillStyle = Theme.mutedText; ctx.textAlign = "right"; ctx.textBaseline = "bottom"
                    ctx.fillText(root.goal + root.unit, W - padR, gy - 2)
                }
            }
        }
    }
}
