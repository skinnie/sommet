import QtQuick
import QtQuick.Controls
import AmbitApp

// Weight page (André, 2026-08-24). Body-weight history, sourced from intervals.icu today and
// swappable to a Garmin Index Scale 2 feed later (WeightService.source). Read-only. The chart
// is a dependency-free Canvas (same approach as RuleOutputChart) since the app has no charting
// library. Body-composition rows (fat/muscle/water) are left as a deliberate gap until a
// provider that carries them (the Index scale) is wired - see the note at the bottom.
Item {
    id: root

    readonly property var series: WeightService.series

    Component.onCompleted: if (WeightService.connected) WeightService.refresh()
    Connections {
        target: WeightService
        function onChanged() { chart.requestPaint() }
    }

    Flickable {
        anchors.fill: parent
        contentHeight: col.implicitHeight + Theme.spacingLarge * 2
        clip: true

        Column {
            id: col
            x: Theme.spacingLarge
            y: Theme.spacingLarge
            width: parent.width - Theme.spacingLarge * 2
            spacing: Theme.spacingMedium

            Text {
                text: qsTr("Weight")
                font.pixelSize: Theme.fontSizeLargeTitle
                font.bold: true
                color: Theme.text
            }

            // --- Add a manual weigh-in ---
            Row {
                width: parent.width
                spacing: Theme.spacingSmall
                RoundedButton {
                    text: qsTr("+ Add weigh-in")
                    onClicked: { manualDate.text = new Date().toISOString().slice(0, 10)
                                 manualKg.text = ""; manualFat.text = ""; manualRow.visible = true }
                }
                Text {
                    visible: WeightService.garminNeedsLogin
                    anchors.verticalCenter: parent.verticalCenter
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("(sign in to Garmin in Settings for body composition)")
                }
            }
            Card {
                id: manualRow
                width: parent.width
                visible: false
                Column {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Row {
                        width: parent.width
                        spacing: Theme.spacingSmall
                        RoundedTextField { id: manualDate; width: 120; placeholderText: qsTr("YYYY-MM-DD") }
                        RoundedTextField { id: manualKg; width: 90; placeholderText: qsTr("kg")
                            inputMethodHints: Qt.ImhFormattedNumbersOnly }
                        RoundedTextField { id: manualFat; width: 90; placeholderText: qsTr("fat %")
                            inputMethodHints: Qt.ImhFormattedNumbersOnly }
                        RoundedButton {
                            text: qsTr("Save")
                            enabled: manualDate.text.length === 10 && parseFloat(manualKg.text) > 0
                            onClicked: {
                                WeightService.addManualWeight(manualDate.text,
                                    parseFloat(manualKg.text), parseFloat(manualFat.text) || 0)
                                manualRow.visible = false
                            }
                        }
                        RoundedButton { text: qsTr("Cancel"); onClicked: manualRow.visible = false }
                    }
                    // #10 (André, 2026-09-02): explain why Save is greyed rather than leaving it
                    // silently disabled.
                    Text {
                        visible: manualDate.text.length > 0 && manualDate.text.length !== 10
                        text: qsTr("Enter the date as YYYY-MM-DD (for example 2026-09-02).")
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                    }
                }
            }

            // --- Nothing connected and nothing manual ---
            Card {
                width: parent.width
                visible: !WeightService.connected
                // #9 (André, 2026-09-02): one-tap route to set up the shared connection.
                Column {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.mutedText
                        text: qsTr("Connect intervals.icu or Garmin Connect, or add a " +
                                   "weigh-in manually above. All sources are merged, keeping the " +
                                   "reading with the most detail for each day.")
                    }
                    RoundedButton {
                        text: qsTr("Open Settings → Connections")
                        onClicked: NavBus.navigate("settings")
                    }
                }
            }

            // --- Latest + trend ---
            Card {
                width: parent.width
                visible: WeightService.connected && root.series.length > 0
                Row {
                    width: parent.width
                    spacing: Theme.spacingLarge
                    Column {
                        spacing: 2
                        Text { text: qsTr("Latest"); color: Theme.mutedText
                               font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            text: WeightService.latestWeightKg.toFixed(1) + qsTr(" kg")
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeDisplay
                            font.bold: true
                        }
                        Text { text: WeightService.latestDate; color: Theme.mutedText
                               font.pixelSize: Theme.fontSizeCaption }
                    }
                    Column {
                        spacing: 2
                        anchors.verticalCenter: parent.verticalCenter
                        visible: root.series.length > 1
                        Text { text: qsTr("Change"); color: Theme.mutedText
                               font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            readonly property real d: WeightService.changeKg
                            text: (d > 0 ? "+" : "") + d.toFixed(1) + qsTr(" kg")
                            color: d > 0 ? Theme.warning : (d < 0 ? Theme.success : Theme.mutedText)
                            font.pixelSize: Theme.fontSizeTitle
                            font.bold: true
                        }
                        Text { text: qsTr("over %1 weigh-ins").arg(root.series.length)
                               color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                    }
                }
            }

            // --- Body composition (Garmin Index only) ---
            Card {
                width: parent.width
                visible: WeightService.hasBodyComp
                Column {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Text { text: qsTr("Body composition"); font.bold: true; color: Theme.text
                           font.pixelSize: Theme.fontSizeBody }
                    Grid {
                        width: parent.width
                        columns: 3
                        columnSpacing: Theme.spacingLarge
                        rowSpacing: Theme.spacingMedium
                        Repeater {
                            model: [
                                { label: qsTr("Body fat"), key: "bodyFatPct", unit: "%" },
                                { label: qsTr("Muscle"),   key: "muscleMassKg", unit: " kg" },
                                { label: qsTr("Body water"), key: "bodyWaterPct", unit: "%" },
                                { label: qsTr("Bone"),     key: "boneMassKg", unit: " kg" },
                                { label: qsTr("BMI"),      key: "bmi", unit: "" },
                            ]
                            delegate: Column {
                                spacing: 2
                                visible: WeightService.latest[modelData.key] !== undefined
                                Text { text: modelData.label; color: Theme.mutedText
                                       font.pixelSize: Theme.fontSizeLabel }
                                Text {
                                    text: (WeightService.latest[modelData.key] !== undefined
                                           ? WeightService.latest[modelData.key] : "") + modelData.unit
                                    color: Theme.text; font.pixelSize: Theme.fontSizeSubtitle
                                    font.bold: true
                                }
                            }
                        }
                    }
                }
            }

            // --- Chart (hover/tap a point to see that day's reading) ---
            Card {
                width: parent.width
                visible: WeightService.connected && root.series.length > 1
                height: 240
                Canvas {
                    id: chart
                    anchors.fill: parent
                    anchors.margins: Theme.spacingSmall
                    // Pixel position of each plotted point, filled in onPaint, used for hit-testing.
                    property var pts: []
                    property int hoverIndex: -1
                    Connections { target: Theme; function onOverrideChanged() { chart.requestPaint() } }
                    onPaint: {
                        var ctx = getContext("2d"); ctx.reset();
                        var W = width, H = height; ctx.clearRect(0, 0, W, H);
                        var s = root.series;
                        if (!s || s.length < 2) { chart.pts = []; return; }

                        var xs = [], ys = [];
                        for (var i = 0; i < s.length; ++i) {
                            xs.push(Date.parse(s[i].date));
                            ys.push(s[i].weightKg);
                        }
                        var xMin = xs[0], xMax = xs[xs.length - 1];
                        if (xMax === xMin) xMax = xMin + 1;
                        var yMin = ys[0], yMax = ys[0];
                        for (var j = 1; j < ys.length; ++j) {
                            if (ys[j] < yMin) yMin = ys[j];
                            if (ys[j] > yMax) yMax = ys[j];
                        }
                        var pad = Math.max(0.5, (yMax - yMin) * 0.25);
                        yMin -= pad; yMax += pad;

                        var padL = 46, padR = 12, padT = 14, padB = 24;
                        var plotW = W - padL - padR, plotH = H - padT - padB;
                        function px(t) { return padL + (t - xMin) / (xMax - xMin) * plotW; }
                        function py(v) { return padT + (1 - (v - yMin) / (yMax - yMin)) * plotH; }

                        ctx.font = "10px sans-serif";
                        ctx.fillStyle = Theme.mutedText;
                        ctx.textAlign = "right"; ctx.textBaseline = "middle";
                        for (var g = 0; g <= 2; ++g) {
                            var vy = yMin + (yMax - yMin) * g / 2;
                            var yy = py(vy);
                            ctx.strokeStyle = Qt.rgba(Theme.mutedText.r, Theme.mutedText.g,
                                                      Theme.mutedText.b, 0.18);
                            ctx.lineWidth = 1;
                            ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(W - padR, yy); ctx.stroke();
                            ctx.fillText(vy.toFixed(1), padL - 6, yy);
                        }
                        ctx.textAlign = "center"; ctx.textBaseline = "top";
                        ctx.fillText(s[0].date, padL, H - padB + 4);
                        ctx.fillText(s[s.length - 1].date, W - padR, H - padB + 4);

                        ctx.strokeStyle = Qt.rgba(Theme.mutedText.r, Theme.mutedText.g,
                                                  Theme.mutedText.b, 0.5);
                        ctx.beginPath(); ctx.moveTo(padL, padT); ctx.lineTo(padL, H - padB);
                        ctx.lineTo(W - padR, H - padB); ctx.stroke();

                        ctx.strokeStyle = Theme.accent; ctx.lineWidth = 2; ctx.lineJoin = "round";
                        ctx.beginPath();
                        var pp = [];
                        for (var k = 0; k < s.length; ++k) {
                            var x = px(xs[k]), y = py(ys[k]);
                            pp.push({ x: x, y: y });
                            if (k === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
                        }
                        ctx.stroke();
                        for (var m = 0; m < s.length; ++m) {
                            ctx.fillStyle = (m === chart.hoverIndex) ? Theme.text : Theme.accent;
                            ctx.beginPath();
                            ctx.arc(pp[m].x, pp[m].y, m === chart.hoverIndex ? 5 : 3, 0, 2 * Math.PI);
                            ctx.fill();
                        }
                        chart.pts = pp;
                    }

                    MouseArea {
                        anchors.fill: parent
                        hoverEnabled: true
                        function pick(mx, my) {
                            var best = -1, bestD = 400;   // within ~20px
                            for (var i = 0; i < chart.pts.length; ++i) {
                                var dx = chart.pts[i].x - mx, dy = chart.pts[i].y - my;
                                var d = dx * dx + dy * dy;
                                if (d < bestD) { bestD = d; best = i; }
                            }
                            if (best !== chart.hoverIndex) { chart.hoverIndex = best; chart.requestPaint() }
                        }
                        onPositionChanged: (m) => pick(m.x, m.y)
                        onClicked: (m) => pick(m.x, m.y)
                        onExited: { chart.hoverIndex = -1; chart.requestPaint() }
                    }

                    // Tooltip for the hovered/tapped point.
                    Rectangle {
                        visible: chart.hoverIndex >= 0 && chart.hoverIndex < root.series.length
                        readonly property var pt: visible ? root.series[chart.hoverIndex] : null
                        readonly property var xy: (visible && chart.hoverIndex < chart.pts.length)
                                                  ? chart.pts[chart.hoverIndex] : { x: 0, y: 0 }
                        x: Math.max(2, Math.min(chart.width - width - 2, xy.x - width / 2))
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
                            Text { text: tipCol.p ? tipCol.p.weightKg.toFixed(1) + qsTr(" kg") : ""
                                   color: Theme.text; font.pixelSize: Theme.fontSizeCaption; font.bold: true }
                            // Every body-composition field the reading carries (Garmin days have
                            // them; intervals/manual days may not).
                            Repeater {
                                model: [
                                    { key: "bodyFatPct",   label: qsTr("Body fat"),   suffix: "%" },
                                    { key: "muscleMassKg", label: qsTr("Muscle"),     suffix: qsTr(" kg") },
                                    { key: "bodyWaterPct", label: qsTr("Body water"), suffix: "%" },
                                    { key: "boneMassKg",   label: qsTr("Bone"),       suffix: qsTr(" kg") },
                                    { key: "bmi",          label: qsTr("BMI"),        suffix: "" },
                                ]
                                delegate: Text {
                                    visible: tipCol.p && tipCol.p[modelData.key] !== undefined
                                    text: (tipCol.p && tipCol.p[modelData.key] !== undefined)
                                          ? modelData.label + " " + tipCol.p[modelData.key] + modelData.suffix
                                          : ""
                                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeTiny
                                }
                            }
                        }
                    }
                }
            }

            // --- Empty / error ---
            Text {
                width: parent.width
                visible: WeightService.connected && root.series.length === 0
                         && !WeightService.loading
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                text: WeightService.lastError.length > 0
                      ? WeightService.lastError
                      : qsTr("No weigh-ins found for the last year.")
            }

            // --- Merge note ---
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                text: qsTr("Merged from intervals.icu, Garmin Connect and manual entries — for " +
                           "each day the reading with the most detail wins. Body composition " +
                           "(fat, muscle, water) comes from Garmin.")
            }
        }
    }
}
