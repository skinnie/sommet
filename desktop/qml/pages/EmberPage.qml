import QtQuick
import QtQuick.Controls
import AmbitApp

// Ember - the concept-mockup dashboard, made real (André, 2026-08-25: shared the original
// "Ember in Sommet" mockup - "this was your mockup mate" - as the target). Compact stat-tile
// row with a SMALL fasting ring + one-TAP coffee/water tiles, then the bar/line charts.
// Logs -> local backend (/api/ember/log); everything reads it back.
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: col.height + Theme.spacingLarge * 2
    clip: true

    readonly property string backend: "http://127.0.0.1:8766"
    property var summary: ({ "today": {}, "days": [], "fasts": [] })
    property real elapsedMs: 0
    property bool fasting: summary.today && summary.today.fastActive === true
    property int goalH: (summary.today && summary.today.fastGoalHours) || 16
    property real frac: fasting ? Math.min(1, elapsedMs / (goalH * 3600000)) : 0

    // right-click menus: coffee family (left-click quick-adds an espresso) + water sizes / teas
    property var coffeeDrinks: [
        { "name": qsTr("Espresso"), "kcal": 3, "caffeine": 63, "fast": "safe", "coffee": true },
        { "name": qsTr("Black coffee"), "kcal": 2, "caffeine": 95, "fast": "safe", "coffee": true },
        { "name": qsTr("Americano"), "kcal": 3, "caffeine": 95, "fast": "safe", "coffee": true },
        { "name": qsTr("Green tea"), "kcal": 2, "caffeine": 28, "fast": "safe", "coffee": false },
        { "name": qsTr("Black tea"), "kcal": 2, "caffeine": 47, "fast": "safe", "coffee": false },
        { "name": qsTr("Coffee with milk"), "kcal": 20, "caffeine": 95, "fast": "break", "coffee": true },
        { "name": qsTr("Latte"), "kcal": 120, "caffeine": 128, "fast": "break", "coffee": true },
        { "name": qsTr("Cappuccino"), "kcal": 80, "caffeine": 128, "fast": "break", "coffee": true }
    ]
    property var waterOptions: [
        { "name": qsTr("Glass"), "ml": 250, "fast": "safe" },
        { "name": qsTr("Bottle"), "ml": 500, "fast": "safe" },
        { "name": qsTr("Large bottle"), "ml": 750, "fast": "safe" },
        { "name": qsTr("Sparkling water"), "ml": 250, "fast": "safe" }
    ]

    Component.onCompleted: refresh()
    onFracChanged: miniRing.requestPaint()
    onSummaryChanged: miniRing.requestPaint()

    Timer {
        interval: 1000; running: true; repeat: true
        onTriggered: if (root.fasting && root.summary.today.fastStart)
                         root.elapsedMs = Date.now() - root.summary.today.fastStart
    }

    function refresh() {
        var xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState === XMLHttpRequest.DONE && xhr.status === 200) {
                root.summary = JSON.parse(xhr.responseText)
                var t = root.summary.today
                root.elapsedMs = (t && t.fastActive && t.fastStart) ? (Date.now() - t.fastStart) : 0
            }
        }
        xhr.open("GET", backend + "/api/ember"); xhr.send()
    }
    function postLog(payload) {
        var xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState === XMLHttpRequest.DONE && xhr.status === 200)
                root.summary = JSON.parse(xhr.responseText)
        }
        xhr.open("POST", backend + "/api/ember/log")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(JSON.stringify(payload))
    }
    function hhmm(ms) {
        var s = Math.max(0, Math.floor(ms / 1000))
        return (Math.floor(s / 3600) < 10 ? "0" : "") + Math.floor(s / 3600) + ":" + (Math.floor(s % 3600 / 60) < 10 ? "0" : "") + Math.floor(s % 3600 / 60)
    }
    function tToday(k, d) { return (root.summary.today && root.summary.today[k] !== undefined) ? root.summary.today[k] : d }
    function daySeries(f) { var o = [], d = root.summary.days || []; for (var i = 0; i < d.length; ++i) o.push({ "date": d[i].date, "value": d[i][f] }); return o }
    function fastSeries() { var o = [], f = root.summary.fasts || []; for (var i = 0; i < f.length; ++i) o.push({ "date": Qt.formatDate(new Date(f[i].end), "yyyy-MM-dd"), "value": f[i].hours }); return o }
    function avgFast() { var f = root.summary.fasts || []; if (!f.length) return 0; var s = 0; for (var i = 0; i < f.length; ++i) s += f[i].hours; return Math.round(s / f.length * 10) / 10 }
    function streak() {
        var f = root.summary.fasts || [], days = {}
        for (var i = 0; i < f.length; ++i) days[Qt.formatDate(new Date(f[i].end), "yyyy-MM-dd")] = true
        if (root.fasting) days[Qt.formatDate(new Date(), "yyyy-MM-dd")] = true // in-progress fast keeps today alive
        var n = 0, d = new Date()
        while (days[Qt.formatDate(d, "yyyy-MM-dd")]) { n++; d.setDate(d.getDate() - 1) }
        return n
    }

    // a small reusable stat tile
    component Tile: Card {
        property color accent: Theme.accent
        property string value: ""
        property string sub: ""
        property string tapHint: ""
        default property alias body: hole.data
        width: 100; height: 118; padding: Theme.spacingSmall
        Column {
            anchors.centerIn: parent; spacing: 2; width: parent.width
            Rectangle { width: 22; height: 3; radius: 2; color: accent; anchors.horizontalCenter: parent.horizontalCenter; visible: value !== "" }
            Item { id: hole; width: parent.width; height: childrenRect.height; visible: children.length > 0 }
            Text { visible: value !== ""; anchors.horizontalCenter: parent.horizontalCenter; text: value; color: Theme.text; font.pixelSize: Theme.fontSizeTitle; font.bold: true }
            Text { anchors.horizontalCenter: parent.horizontalCenter; text: sub; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
            Text { visible: tapHint !== ""; anchors.horizontalCenter: parent.horizontalCenter; text: tapHint; color: accent; font.pixelSize: Theme.fontSizeCaption; font.bold: true; topPadding: 2 }
        }
    }

    Column {
        id: col
        width: root.width - Theme.spacingLarge * 2
        x: Theme.spacingLarge; y: Theme.spacingLarge
        spacing: Theme.spacingMedium

        Column {
            width: parent.width; spacing: 1
            Text { text: qsTr("Ember"); color: Theme.text; font.pixelSize: Theme.fontSizeLargeTitle; font.bold: true }
            Text { text: qsTr("Tap a tile to log · right-click coffee or water for more options"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody }
        }

        // --- stat tiles (mockup row) ---
        Row {
            width: parent.width; spacing: Theme.spacingSmall
            property real cw: (width - Theme.spacingSmall * 5) / 6

            // fasting (small ring + Start/End)
            Card {
                width: parent.cw; height: 118; padding: Theme.spacingSmall
                Column {
                    anchors.centerIn: parent; spacing: 3; width: parent.width
                    Item {
                        width: 52; height: 52; anchors.horizontalCenter: parent.horizontalCenter
                        Canvas {
                            id: miniRing; anchors.fill: parent
                            Connections { target: Theme; function onOverrideChanged() { miniRing.requestPaint() } }
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                var cx = width / 2, cy = height / 2, r = width / 2 - 5
                                ctx.lineWidth = 5; ctx.lineCap = "round"
                                ctx.beginPath(); ctx.arc(cx, cy, r, 0, 2 * Math.PI)
                                ctx.strokeStyle = Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.18); ctx.stroke()
                                if (root.frac > 0) { ctx.beginPath(); ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + root.frac * 2 * Math.PI); ctx.strokeStyle = Theme.warning; ctx.stroke() }
                            }
                        }
                        Text { anchors.centerIn: parent; text: root.fasting ? Math.round(root.frac * 100) + "%" : "○"; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; font.bold: true }
                    }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: root.fasting ? root.hhmm(root.elapsedMs) : qsTr("—"); color: Theme.text; font.pixelSize: Theme.fontSizeSubtitle; font.bold: true }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: root.fasting ? qsTr("tap to stop") : qsTr("tap to fast"); color: Theme.warning; font.pixelSize: Theme.fontSizeCaption; font.bold: true; topPadding: 2 }
                }
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                            onClicked: { if (root.fasting) stopDlg.open(); else root.postLog({ "type": "fast-start", "goalHours": 16 }) } }
            }

            Tile { width: parent.cw; accent: Theme.success; value: "" + root.tToday("kcal", 0); sub: qsTr("kcal in") }

            // coffee - tap anywhere on the tile to add one
            Card {
                width: parent.cw; height: 118; padding: Theme.spacingSmall
                Column {
                    anchors.centerIn: parent; spacing: 2; width: parent.width
                    Rectangle { width: 22; height: 3; radius: 2; color: Theme.hard; anchors.horizontalCenter: parent.horizontalCenter }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: "" + root.tToday("coffees", 0); color: Theme.text; font.pixelSize: Theme.fontSizeTitle; font.bold: true }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("coffees"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("tap +"); color: Theme.hard; font.pixelSize: Theme.fontSizeCaption; font.bold: true; topPadding: 2 }
                }
                MouseArea {
                    anchors.fill: parent; acceptedButtons: Qt.LeftButton | Qt.RightButton; cursorShape: Qt.PointingHandCursor
                    onClicked: (m) => { if (m.button === Qt.RightButton) coffeeDlg.open()
                                        else root.postLog({ "type": "drink", "name": qsTr("Espresso"), "kcal": 3, "caffeineMg": 63, "isCoffee": true, "breaksFast": false }) }
                }
            }

            // water - tap anywhere on the tile to add a glass
            Card {
                width: parent.cw; height: 118; padding: Theme.spacingSmall
                Column {
                    anchors.centerIn: parent; spacing: 2; width: parent.width
                    Rectangle { width: 22; height: 3; radius: 2; color: Theme.accent; anchors.horizontalCenter: parent.horizontalCenter }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: (root.tToday("waterMl", 0) / 1000).toFixed(1) + " L"; color: Theme.text; font.pixelSize: Theme.fontSizeTitle; font.bold: true }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("water"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                    Text { anchors.horizontalCenter: parent.horizontalCenter; text: qsTr("tap +250"); color: Theme.accent; font.pixelSize: Theme.fontSizeCaption; font.bold: true; topPadding: 2 }
                }
                MouseArea {
                    anchors.fill: parent; acceptedButtons: Qt.LeftButton | Qt.RightButton; cursorShape: Qt.PointingHandCursor
                    onClicked: (m) => { if (m.button === Qt.RightButton) waterDlg.open()
                                        else root.postLog({ "type": "water", "volumeMl": 250 }) }
                }
            }

            Tile { width: parent.cw; accent: Theme.warning; value: root.streak() + ""; sub: qsTr("day streak") }
            Tile { width: parent.cw; accent: Theme.secondary; value: root.avgFast() + " h"; sub: qsTr("avg fast 14d") }
        }

        // --- charts (mockup: bars + line) ---
        Card { width: parent.width
            EmberBars { width: parent.width; label: qsTr("Fasting hours"); unit: "h"; goal: 16; decimals: 1; barColor: Theme.warning; series: root.fastSeries() } }
        Card { width: parent.width
            EmberBars { width: parent.width; label: qsTr("Calories in"); unit: " kcal"; barColor: Theme.success; series: root.daySeries("kcal") } }
        Row {
            width: parent.width; spacing: Theme.spacingMedium
            property real cw: (width - Theme.spacingMedium) / 2
            Card { width: parent.cw
                EmberBars { width: parent.width; label: qsTr("Coffee (cups/day)"); barColor: Theme.hard; series: root.daySeries("coffee") } }
            Card { width: parent.cw
                EmberBars { width: parent.width; label: qsTr("Water (litres)"); unit: " L"; goal: 2.5; decimals: 1; barColor: Theme.accent; series: root.daySeries("waterL") } }
        }
    }

    ThemedDialog {
        id: stopDlg
        anchors.centerIn: Overlay.overlay
        title: qsTr("Stop your fast?")
        Column {
            spacing: Theme.spacingMedium
            Text { width: 250; wrapMode: Text.WordWrap; color: Theme.text; font.pixelSize: Theme.fontSizeBody
                   text: qsTr("End your current fast now?") }
            Row {
                anchors.right: parent.right; spacing: Theme.spacingSmall
                RoundedButton { text: qsTr("Cancel"); onClicked: stopDlg.close() }
                RoundedButton { text: qsTr("Stop fast"); onClicked: { root.postLog({ "type": "fast-end" }); stopDlg.close() } }
            }
        }
    }

    // The stylish centred picker André asked for: a modal card in the middle of a dimmed
    // window (ThemedDialog's Overlay.modal scrim), not a cursor popup. One tap logs + closes.
    ThemedDialog {
        id: coffeeDlg
        anchors.centerIn: Overlay.overlay
        width: 400
        title: qsTr("Log a coffee or tea")
        Column {
            width: 360
            spacing: 2
            Repeater {
                model: root.coffeeDrinks
                delegate: Rectangle {
                    required property var modelData
                    width: parent.width; height: 54; radius: Theme.radiusSmall
                    color: coffeeHover.containsMouse
                           ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.10) : "transparent"

                    Column {
                        anchors.left: parent.left; anchors.leftMargin: Theme.spacingMedium
                        anchors.verticalCenter: parent.verticalCenter; spacing: 1
                        Text { text: modelData.name; color: Theme.text
                               font.pixelSize: Theme.fontSizeBody; font.bold: true }
                        Text { text: modelData.caffeine + qsTr(" mg caffeine · ") + modelData.kcal + qsTr(" kcal")
                               color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                    }
                    Rectangle {
                        anchors.right: parent.right; anchors.rightMargin: Theme.spacingMedium
                        anchors.verticalCenter: parent.verticalCenter
                        radius: height / 2; height: 22; width: coffeePill.width + 18
                        color: modelData.fast === "break"
                               ? Qt.rgba(Theme.warning.r, Theme.warning.g, Theme.warning.b, 0.15)
                               : Qt.rgba(Theme.success.r, Theme.success.g, Theme.success.b, 0.15)
                        Text {
                            id: coffeePill; anchors.centerIn: parent
                            text: modelData.fast === "break" ? qsTr("breaks fast") : qsTr("won't break")
                            color: modelData.fast === "break" ? Theme.warning : Theme.success
                            font.pixelSize: Theme.fontSizeCaption; font.bold: true
                        }
                    }
                    MouseArea {
                        id: coffeeHover; anchors.fill: parent
                        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            root.postLog({ "type": "drink", "name": modelData.name, "kcal": modelData.kcal,
                                "caffeineMg": (modelData.caffeine || 0), "isCoffee": (modelData.coffee === true),
                                "breaksFast": (modelData.fast === "break") })
                            coffeeDlg.close()
                        }
                    }
                }
            }
        }
    }

    ThemedDialog {
        id: waterDlg
        anchors.centerIn: Overlay.overlay
        width: 320
        title: qsTr("Log water")
        Column {
            width: 280
            spacing: 2
            Repeater {
                model: root.waterOptions
                delegate: Rectangle {
                    required property var modelData
                    width: parent.width; height: 46; radius: Theme.radiusSmall
                    color: waterHover.containsMouse
                           ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.10) : "transparent"

                    Text {
                        anchors.left: parent.left; anchors.leftMargin: Theme.spacingMedium
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.name; color: Theme.text
                        font.pixelSize: Theme.fontSizeBody; font.bold: true
                    }
                    Rectangle {
                        anchors.right: parent.right; anchors.rightMargin: Theme.spacingMedium
                        anchors.verticalCenter: parent.verticalCenter
                        radius: height / 2; height: 22; width: waterPill.width + 18
                        color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.15)
                        Text {
                            id: waterPill; anchors.centerIn: parent
                            text: modelData.ml + qsTr(" ml"); color: Theme.accent
                            font.pixelSize: Theme.fontSizeCaption; font.bold: true
                        }
                    }
                    MouseArea {
                        id: waterHover; anchors.fill: parent
                        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: { root.postLog({ "type": "water", "volumeMl": modelData.ml }); waterDlg.close() }
                    }
                }
            }
        }
    }
}
