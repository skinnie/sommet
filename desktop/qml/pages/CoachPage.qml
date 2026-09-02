import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AmbitApp

// Coach (v2 concept, implemented 2026-08-21) — composes the two Artifact mockups (Fresh
// Today's readiness beacon, Ride Coach's chat) into one screen, backed by CoachService's
// real readiness math over this app's own activities.db. See coachservice.h's own header
// comment for the two backend toggles (chat: canned/Claude, catalogue: sample/live) — both
// configured from SettingsPage's Coach card, not from this page.
Item {
    id: root

    readonly property var readiness: CoachService.readiness
    readonly property string light: readiness.light || "green"
    readonly property color lightColor:
        light === "green" ? Theme.success
        : light === "tempered" ? "#8FA33B"
        : light === "yellow" ? Theme.warning
        : Theme.error

    Component.onCompleted: CoachService.refreshReadiness()

    RowLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingLarge
        spacing: Theme.spacingLarge

        // ---- left: the readiness beacon ----
        Card {
            Layout.preferredWidth: 300
            Layout.fillHeight: true
            padding: Theme.spacingMedium

            ColumnLayout {
                width: parent.width
                spacing: Theme.spacingMedium

                RowLayout {
                    Layout.fillWidth: true
                    Text { text: qsTr("Today"); color: Theme.text; font.bold: true; font.pixelSize: Theme.fontSizeSubtitle }
                    Item { Layout.fillWidth: true }
                    RoundedButton {
                        text: Icons.sync
                        font.family: Icons.fontFamily
                        onClicked: CoachService.refreshReadiness()
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingSmall
                    spacing: Theme.spacingSmall / 2
                    Layout.alignment: Qt.AlignHCenter

                    Rectangle {
                        Layout.alignment: Qt.AlignHCenter
                        width: 64; height: 64; radius: 32
                        color: root.lightColor
                        Behavior on color { ColorAnimation { duration: 300 } }
                    }
                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        text: root.light === "green" ? qsTr("Fresh")
                            : root.light === "tempered" ? qsTr("Fresh, ease in")
                            : root.light === "yellow" ? qsTr("Some fatigue")
                            : qsTr("Deep fatigue")
                        color: root.lightColor
                        font.bold: true
                        font.pixelSize: Theme.fontSizeLargeTitle
                    }
                    Text {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.preferredWidth: 220
                        text: root.readiness.sentence || ""
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeLabel
                        wrapMode: Text.WordWrap
                        horizontalAlignment: Text.AlignHCenter
                    }
                }

                // ---- mini Fitness/Fatigue chart ----
                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.mutedText; opacity: 0.2 }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSmall
                    Rectangle { width: 12; height: 4; radius: 2; color: Theme.primary }
                    Text { text: qsTr("Fitness"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                    Rectangle { width: 12; height: 2; radius: 1; color: Theme.mutedText; opacity: 0.6 }
                    Text { text: qsTr("Fatigue"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                }
                Canvas {
                    id: chart
                    Layout.fillWidth: true
                    height: 90
                    property var series: CoachService.chartSeries
                    // Pixel position of each day's two points, for hit-testing the pointer so a
                    // hover/tap shows that day's fitness & fatigue - same pattern as the other charts.
                    property var pts: []
                    property int hoverIndex: -1
                    onSeriesChanged: requestPaint()
                    onHoverIndexChanged: requestPaint()
                    onPaint: {
                        var ctx = getContext("2d")
                        ctx.reset()
                        var s = series
                        if (!s || s.length < 2) return
                        var maxV = 1
                        for (var i = 0; i < s.length; i++)
                            maxV = Math.max(maxV, s[i].fitness, s[i].fatigue)
                        maxV *= 1.15
                        var w = width, h = height, pad = 4
                        function x(i) { return pad + (w - 2 * pad) * i / (s.length - 1) }
                        function y(v) { return h - pad - (h - 2 * pad) * v / maxV }

                        ctx.beginPath()
                        ctx.moveTo(x(0), y(s[0].fitness))
                        for (i = 1; i < s.length; i++) ctx.lineTo(x(i), y(s[i].fitness))
                        ctx.lineTo(x(s.length - 1), h - pad); ctx.lineTo(x(0), h - pad); ctx.closePath()
                        ctx.fillStyle = Qt.rgba(Theme.primary.r, Theme.primary.g, Theme.primary.b, 0.15)
                        ctx.fill()

                        ctx.beginPath()
                        ctx.moveTo(x(0), y(s[0].fatigue))
                        for (i = 1; i < s.length; i++) ctx.lineTo(x(i), y(s[i].fatigue))
                        ctx.strokeStyle = Theme.mutedText; ctx.globalAlpha = 0.7; ctx.lineWidth = 1.4
                        ctx.setLineDash([3, 3]); ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha = 1

                        ctx.beginPath()
                        ctx.moveTo(x(0), y(s[0].fitness))
                        for (i = 1; i < s.length; i++) ctx.lineTo(x(i), y(s[i].fitness))
                        ctx.strokeStyle = Theme.primary; ctx.lineWidth = 2.4; ctx.stroke()

                        // record point positions for hit-testing, and highlight the hovered day
                        var pp = []
                        for (i = 0; i < s.length; i++)
                            pp.push({ x: x(i), yF: y(s[i].fitness), yA: y(s[i].fatigue) })
                        chart.pts = pp
                        if (chart.hoverIndex >= 0 && chart.hoverIndex < pp.length) {
                            var hp = pp[chart.hoverIndex]
                            ctx.strokeStyle = Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.4)
                            ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(hp.x, pad); ctx.lineTo(hp.x, h - pad); ctx.stroke()
                            ctx.fillStyle = Theme.primary
                            ctx.beginPath(); ctx.arc(hp.x, hp.yF, 3.5, 0, 2 * Math.PI); ctx.fill()
                            ctx.fillStyle = Theme.mutedText
                            ctx.beginPath(); ctx.arc(hp.x, hp.yA, 3.5, 0, 2 * Math.PI); ctx.fill()
                        }
                    }

                    MouseArea {
                        anchors.fill: parent
                        hoverEnabled: true
                        function pick(mx) {
                            var best = -1, bestD = 1e9
                            for (var i = 0; i < chart.pts.length; ++i) {
                                var dx = Math.abs(chart.pts[i].x - mx)
                                if (dx < bestD) { bestD = dx; best = i }
                            }
                            chart.hoverIndex = best
                        }
                        onPositionChanged: (m) => pick(m.x)
                        onClicked: (m) => pick(m.x)
                        onExited: chart.hoverIndex = -1
                    }

                    // Tooltip: the hovered day's date, fitness and fatigue.
                    Rectangle {
                        id: coachTip
                        visible: chart.hoverIndex >= 0 && chart.series && chart.hoverIndex < chart.series.length
                        readonly property var pt: visible ? chart.series[chart.hoverIndex] : null
                        readonly property real px: (visible && chart.hoverIndex < chart.pts.length)
                                                   ? chart.pts[chart.hoverIndex].x : 0
                        x: Math.max(2, Math.min(chart.width - width - 2, px - width / 2))
                        y: 2
                        width: coachTipCol.implicitWidth + 14
                        height: coachTipCol.implicitHeight + 10
                        // Converged onto Theme.cardNested/Theme.border (2026-08-25, André:
                        // "redo them also" - the two chart-hover tooltips, same token swap as
                        // every other flat-tile element this session).
                        radius: Theme.radiusSmall
                        color: Theme.cardNested
                        border.color: Theme.border
                        border.width: 1
                        Column {
                            id: coachTipCol
                            anchors.centerIn: parent
                            spacing: 1
                            Text { text: coachTip.pt ? coachTip.pt.date : ""
                                   color: Theme.mutedText; font.pixelSize: Theme.fontSizeTiny }
                            Text { text: coachTip.pt ? qsTr("Fitness ") + Math.round(coachTip.pt.fitness) : ""
                                   color: Theme.primary; font.pixelSize: Theme.fontSizeCaption; font.bold: true }
                            Text { text: coachTip.pt ? qsTr("Fatigue ") + Math.round(coachTip.pt.fatigue) : ""
                                   color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; font.bold: true }
                        }
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.mutedText; opacity: 0.2 }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 3
                    columnSpacing: Theme.spacingSmall
                    Repeater {
                        model: [
                            { k: qsTr("Fitness"), v: root.readiness.fitness },
                            { k: qsTr("Fatigue"), v: root.readiness.fatigue },
                            { k: qsTr("Freshness"), v: root.readiness.freshness },
                        ]
                        // The reference tile for the app's whole flat-tile control language
                        // (André, 2026-08-25: "same type of button... like fitness fatigue and
                        // freshness"). Switched from Theme.background to Theme.cardNested so
                        // every other control this session copied this look from (RoundedButton,
                        // RoundedTextField, RoundedComboBox, the Coach workout cards, ...) is
                        // pulling the exact same token, not two near-identical-but-different
                        // greys that could drift apart later.
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 46
                            radius: Theme.radiusSmall
                            color: Theme.cardNested
                            Column {
                                anchors.centerIn: parent
                                spacing: 1
                                Text { anchors.horizontalCenter: parent.horizontalCenter; text: modelData.k; color: Theme.mutedText; font.pixelSize: 9 }
                                Text {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    text: modelData.v === undefined ? "—" : Math.round(modelData.v)
                                    color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.bold: true
                                }
                            }
                        }
                    }
                }

                Text {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingSmall
                    text: qsTr("Load is duration-based (minutes/day) — this device family has no power meter or HR strap decoded yet.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeTiny
                    wrapMode: Text.WordWrap
                }
                Item { Layout.fillHeight: true }
            }
        }

        // ---- right: the chat ----
        Card {
            // Flat: the readiness beacon on the left is the emotional centre (primary); the
            // conversation is the workspace beside it (2026-08-25 tune-up hierarchy).
            variant: "flat"
            Layout.fillWidth: true
            Layout.fillHeight: true
            padding: 0

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                RowLayout {
                    Layout.fillWidth: true
                    Layout.margins: Theme.spacingMedium
                    Rectangle {
                        width: 34; height: 34; radius: Theme.radiusSmall
                        color: Qt.rgba(Theme.primary.r, Theme.primary.g, Theme.primary.b, 0.14)
                        Text {
                            anchors.centerIn: parent
                            text: Icons.coach; font.family: Icons.fontFamily; color: Theme.primary; font.pixelSize: 18
                        }
                    }
                    Column {
                        Text { text: qsTr("Coach"); color: Theme.text; font.bold: true; font.pixelSize: Theme.fontSizeBodyLarge }
                        Text {
                            text: CoachService.chatBackend === "claude"
                                ? (CoachService.anthropicKeySet
                                    ? qsTr("AI chat on")
                                    : qsTr("Add your API key in Settings → Coach to turn on AI chat"))
                                : qsTr("Pre-written answers — turn on AI chat in Settings → Coach")
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                    Item { Layout.fillWidth: true }
                    RoundedButton {
                        text: Icons.settings
                        font.family: Icons.fontFamily
                        ToolTip.visible: hovered
                        ToolTip.text: qsTr("Chat backend & catalogue source: Settings → Coach")
                        onClicked: NavBus.navigate("settings")
                    }
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.mutedText; opacity: 0.2 }

                ListView {
                    id: chatList
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.margins: Theme.spacingMedium
                    spacing: Theme.spacingSmall
                    clip: true
                    model: CoachService.messages
                    onCountChanged: positionViewAtEnd()
                    delegate: Column {
                        width: chatList.width
                        // Doubled (André, 2026-08-25: "double the spacing between 'feel heavy'
                        // and sprints', consider that as a base for others") - was
                        // spacingSmall/2 (4px, an odd one-off halving used only here), now a
                        // full Theme.spacingSmall (8) like every other "small" gap in the app,
                        // rather than inventing a smaller half-step just for this transition.
                        spacing: Theme.spacingSmall
                        readonly property bool mine: modelData.role === "me"

                        Rectangle {
                            width: Math.min(bubbleText.implicitWidth + 24, chatList.width * 0.78)
                            height: bubbleText.implicitHeight + 18
                            radius: 14
                            color: mine ? Theme.primary : Theme.background
                            anchors.right: mine ? parent.right : undefined
                            anchors.left: mine ? undefined : parent.left
                            Text {
                                id: bubbleText
                                anchors.fill: parent
                                anchors.margins: 9
                                text: modelData.text || ""
                                color: mine ? "white" : Theme.text
                                font.pixelSize: Theme.fontSizeBody
                                wrapMode: Text.WordWrap
                            }
                        }
                        Repeater {
                            model: modelData.cards || []
                            // Real, 2026-08-25 (André: "on coach you have the sprints and
                            // location with different ones... same type of button, similar
                            // colour, [rounding] like fitness fatigue and freshness everywhere")
                            // - these workout-suggestion cards were transparent with a hard
                            // Theme.mutedText outline, the one place on Coach still standing
                            // out from the flat Theme.cardNested tile language every other
                            // control/option on this page now shares.
                            delegate: Rectangle {
                                width: Math.min(280, chatList.width * 0.78)
                                height: cardCol.implicitHeight + 20
                                radius: Theme.radiusSmall
                                color: Theme.cardNested
                                Column {
                                    id: cardCol
                                    anchors.fill: parent
                                    anchors.margins: 10
                                    spacing: 3
                                    RowLayout {
                                        width: parent.width
                                        Text { text: modelData.name || ""; color: Theme.text; font.bold: true; font.pixelSize: Theme.fontSizeLabel; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                        Text {
                                            text: modelData.durationSec ? Math.round(modelData.durationSec / 60) + qsTr("min") : ""
                                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                        }
                                    }
                                    Text {
                                        text: (modelData.intensity || "") + " · IF " + (modelData.intensityFactor ? modelData.intensityFactor.toFixed(2) : "—")
                                              + " · " + (modelData.load ? Math.round(modelData.load) : "—") + " TSS"
                                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                    }
                                }
                            }
                        }
                    }
                    footer: Item {
                        width: chatList.width
                        height: CoachService.sending ? 24 : 0
                        visible: CoachService.sending
                        Row {
                            anchors.left: parent.left
                            spacing: 4
                            anchors.verticalCenter: parent.verticalCenter
                            Repeater {
                                model: 3
                                delegate: Rectangle {
                                    width: 6; height: 6; radius: 3
                                    color: Theme.mutedText
                                    opacity: 0.5
                                }
                            }
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    // Matched to the "Ask the coach anything" row's own margins below (André,
                    // 2026-08-25: "'something shorter' should be aligned with 'ask the coach
                    // anything'") - this row was using spacingSmall (8) while the input row uses
                    // spacingMedium (16), so the chips sat indented from the input's left edge.
                    Layout.margins: Theme.spacingMedium
                    // Zero the BOTTOM margin so the gap down to the "Ask the coach anything" row
                    // is Theme.spacingMedium ONCE, not twice (André, 2026-08-26: that gap "should
                    // be similar to the spacing used between cards in home"). HomePage's own card
                    // Column uses `spacing: Theme.spacingMedium` (16) between cards; here the
                    // parent ColumnLayout has spacing 0 and each row carries margins on ALL FOUR
                    // sides, so this row's bottom 16 stacked on the input row's top 16 and
                    // produced a 32px gap - double Home's. Leaving the input row's own top margin
                    // as the single source of that 16.
                    Layout.bottomMargin: 0
                    spacing: Theme.spacingSmall
                    Repeater {
                        model: [qsTr("Something shorter"), qsTr("Outdoor instead"), qsTr("Send it to my watch")]
                        // Real, 2026-08-25 (André, after trying the flat tile look here first:
                        // "let's make that type of button default for all app") - RoundedButton
                        // itself now IS this flat/no-outline style, so this is back to a plain
                        // RoundedButton rather than a bespoke Rectangle duplicating the same look.
                        delegate: RoundedButton {
                            text: modelData
                            onClicked: CoachService.sendMessage(modelData)
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.margins: Theme.spacingMedium
                    spacing: Theme.spacingSmall

                    RoundedTextField {
                        id: input
                        Layout.fillWidth: true
                        horizontalAlignment: TextInput.AlignLeft
                        placeholderText: qsTr("Ask the coach anything…")
                        onAccepted: sendBtn.clicked()
                    }
                    RoundedButton {
                        id: sendBtn
                        text: qsTr("Send")
                        enabled: input.text.trim().length > 0
                        onClicked: {
                            CoachService.sendMessage(input.text)
                            input.text = ""
                        }
                    }
                }

                // ---- AI footprint note (only when the chat really calls Claude) ----
                Text {
                    Layout.fillWidth: true
                    Layout.leftMargin: Theme.spacingMedium
                    Layout.rightMargin: Theme.spacingMedium
                    Layout.bottomMargin: Theme.spacingSmall
                    visible: CoachService.chatBackend === "claude"
                    text: qsTr("🌱 Use AI mindfully — one chat ≈ ~2 g CO₂ (estimates vary ~0.03–15 g), about "
                             + "driving 20 m or a tree's next ~45 min. The readiness light and workout picks "
                             + "are plain maths (free, offline); only chatting uses AI.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeTiny
                    wrapMode: Text.WordWrap
                }
            }
        }
    }
}
