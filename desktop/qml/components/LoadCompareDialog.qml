import QtQuick
import QtQuick.Controls
import SommetUi

// Fitness and fatigue from intervals.icu and from Sommet's own estimate over the same 90 days
// (sync/05, André 2026-11-18): the check before switching the Coach to Sommet and, one day,
// leaving intervals.icu. Green = intervals.icu, orange = Sommet; solid = fitness, dashed = fatigue.
ThemedDialog {
    id: root

    title: qsTr("Compare training load")
    standardButtons: Dialog.Close
    width: Math.min(760, parent ? parent.width - Theme.spacingLarge * 2 : 760)

    readonly property var cmp: CoachService.loadComparison
    readonly property var rows: cmp.rows || []

    onOpened: CoachService.compareLoadSources()

    function fmt(v) { return v === undefined ? "—" : (Math.round(v * 10) / 10).toLocaleString(Qt.locale(), "f", 1) }

    contentItem: Column {
        spacing: Theme.spacingSmall

        Text {
            width: parent.width; wrapMode: Text.WordWrap
            text: qsTr("intervals.icu works out load from every device you sync there. Sommet works it out " +
                       "from the moves in this app (heart rate when it has it, otherwise duration and sport).")
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
        }

        Text {
            visible: root.cmp.state === "loading"
            text: qsTr("Asking intervals.icu…")
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
        }
        Text {
            visible: root.cmp.state === "error"
            width: parent.width; wrapMode: Text.WordWrap
            text: root.cmp.error || ""
            color: Theme.error; font.pixelSize: Theme.fontSizeBody
        }

        Row {
            visible: root.cmp.state === "ready"
            spacing: Theme.spacingMedium
            Repeater {
                model: [
                    { label: qsTr("intervals.icu fitness"), c: Theme.primary, dash: false },
                    { label: qsTr("Sommet fitness"), c: Theme.hard, dash: false },
                    { label: qsTr("intervals.icu fatigue"), c: Theme.primary, dash: true },
                    { label: qsTr("Sommet fatigue"), c: Theme.hard, dash: true }
                ]
                delegate: Row {
                    id: entry
                    // The inner Repeater has its own modelData (the index), so keep ours here.
                    readonly property bool dashed: modelData.dash
                    readonly property color swatch: modelData.c
                    spacing: Theme.spacingSmall
                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        Repeater {
                            model: entry.dashed ? 3 : 1
                            Rectangle { width: entry.dashed ? 3 : 14; height: 3; radius: 1; color: entry.swatch }
                        }
                    }
                    Text { text: modelData.label; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                }
            }
        }

        Canvas {
            id: chart
            visible: root.cmp.state === "ready"
            width: parent.width
            height: 220
            property var s: root.rows
            property int hoverIndex: -1
            onSChanged: requestPaint()
            onHoverIndexChanged: requestPaint()
            onWidthChanged: requestPaint()
            onPaint: {
                var ctx = getContext("2d")
                ctx.reset()
                var s = chart.s
                if (!s || s.length < 2) return
                var maxV = 1
                for (var i = 0; i < s.length; i++)
                    maxV = Math.max(maxV, s[i].icuFitness, s[i].icuFatigue, s[i].localFitness, s[i].localFatigue)
                maxV *= 1.1
                var w = width, h = height, pad = 4, padL = 28, padB = 16
                function x(i) { return padL + (w - padL - pad) * i / (s.length - 1) }
                function y(v) { return h - padB - (h - padB - pad) * v / maxV }
                var step = maxV > 120 ? 50 : maxV > 60 ? 25 : maxV > 24 ? 10 : 5
                ctx.font = "10px sans-serif"
                ctx.textAlign = "right"; ctx.textBaseline = "middle"
                for (var tv = step; tv < maxV; tv += step) {
                    ctx.strokeStyle = Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.18)
                    ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(padL, y(tv)); ctx.lineTo(w - pad, y(tv)); ctx.stroke()
                    ctx.fillStyle = Theme.mutedText; ctx.fillText(String(tv), padL - 5, y(tv))
                }
                ctx.textAlign = "center"; ctx.textBaseline = "bottom"
                ctx.fillText(s[0].date, x(0) + 30, h)
                ctx.fillText(s[s.length - 1].date, x(s.length - 1) - 30, h)

                function line(key, color, dash, width) {
                    ctx.beginPath()
                    ctx.moveTo(x(0), y(s[0][key]))
                    for (var k = 1; k < s.length; k++) ctx.lineTo(x(k), y(s[k][key]))
                    ctx.strokeStyle = color; ctx.lineWidth = width
                    ctx.setLineDash(dash ? [4, 3] : []); ctx.stroke(); ctx.setLineDash([])
                }
                line("icuFatigue", Theme.primary, true, 1.4)
                line("localFatigue", Theme.hard, true, 1.4)
                line("icuFitness", Theme.primary, false, 2.4)
                line("localFitness", Theme.hard, false, 2.4)

                if (chart.hoverIndex >= 0 && chart.hoverIndex < s.length) {
                    var hx = x(chart.hoverIndex)
                    ctx.strokeStyle = Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.4)
                    ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(hx, pad); ctx.lineTo(hx, h - padB); ctx.stroke()
                }
            }
            MouseArea {
                anchors.fill: parent
                hoverEnabled: true
                onPositionChanged: (m) => {
                    var n = chart.s.length
                    if (n < 2) return
                    var padL = 28, pad = 4
                    chart.hoverIndex = Math.max(0, Math.min(n - 1, Math.round((m.x - padL) / (chart.width - padL - pad) * (n - 1))))
                }
                onExited: chart.hoverIndex = -1
            }
        }

        Text {
            visible: root.cmp.state === "ready"
            width: parent.width; wrapMode: Text.WordWrap
            color: Theme.text; font.pixelSize: Theme.fontSizeCaption
            text: {
                var i = chart.hoverIndex
                if (i < 0 || i >= root.rows.length)
                    return qsTr("Point at the chart to see a day.")
                var r = root.rows[i]
                return qsTr("%1 · fitness %2 (intervals.icu) / %3 (Sommet) · fatigue %4 / %5")
                    .arg(r.date).arg(root.fmt(r.icuFitness)).arg(root.fmt(r.localFitness))
                    .arg(root.fmt(r.icuFatigue)).arg(root.fmt(r.localFatigue))
            }
        }

        Row {
            visible: root.cmp.state === "ready"
            spacing: Theme.spacingLarge
            Repeater {
                model: [
                    { label: qsTr("Fitness, average gap"), v: root.cmp.fitnessGap },
                    { label: qsTr("Fatigue, average gap"), v: root.cmp.fatigueGap },
                    { label: qsTr("Form, average gap"), v: root.cmp.formGap }
                ]
                delegate: Column {
                    Text { text: modelData.label; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                    Text { text: root.fmt(modelData.v); color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.bold: true }
                }
            }
        }
        Text {
            visible: root.cmp.state === "ready" && root.cmp.fitnessBias !== undefined
            width: parent.width; wrapMode: Text.WordWrap
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
            text: root.cmp.fitnessBias >= 0
                  ? qsTr("On average Sommet's fitness reads %1 lower than intervals.icu's, over %2 days.").arg(root.fmt(root.cmp.fitnessBias)).arg(root.rows.length)
                  : qsTr("On average Sommet's fitness reads %1 higher than intervals.icu's, over %2 days.").arg(root.fmt(-root.cmp.fitnessBias)).arg(root.rows.length)
        }
    }
}
