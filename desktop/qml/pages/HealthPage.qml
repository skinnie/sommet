import QtQuick
import QtQuick.Controls
import SommetUi

// Health page (André, 2026-08-24): daily resting heart rate and steps from Garmin Connect.
// Login is shared with the Weight page's Garmin sign-in (same token store), so this page just
// points there if you're not signed in. Read-only.
Item {
    id: root

    Component.onCompleted: HealthService.refresh()
    // The day of a series' newest reading, shown under each tile like Android does (André,
    // 2026-10-04: "Put the same as android") - a value from weeks ago must not read as today's.
    function lastDate(series) {
        return series && series.length > 0 ? DateFormat.date(series[series.length - 1].date) : ""
    }
    // For the number tiles (André, 2026-11-02, desktop picks #12): how old the latest value is,
    // and how it compares with the 7 days before it.
    function _day(iso) { return new Date(String(iso).slice(0, 10) + "T00:00:00") }
    function oldBadge(series) {
        if (!series || series.length === 0)
            return ""
        const now = new Date()
        const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
        const age = Math.round((today - _day(series[series.length - 1].date)) / 86400000)
        return age > 2 ? qsTr("%1 days old").arg(age) : ""
    }
    // "▲ 4 bpm vs 7-day average": the latest value against the mean of the readings from the 7
    // days before it. Needs at least 3 of them; otherwise the tile shows the date instead.
    function trend(series, unit, decimals) {
        if (!series || series.length < 4)
            return ""
        const last = series[series.length - 1]
        const lastDay = _day(last.date)
        let sum = 0, n = 0
        for (let i = series.length - 2; i >= 0; --i) {
            const days = Math.round((lastDay - _day(series[i].date)) / 86400000)
            if (days > 7)
                break
            if (days >= 1) { sum += series[i].value; n++ }
        }
        if (n < 3)
            return ""
        const diff = last.value - sum / n
        const size = Math.abs(diff).toFixed(decimals || 0)
        if (Number(size) === 0)
            return qsTr("Same as the 7-day average")
        return (diff > 0 ? "▲ " : "▼ ") + size + (unit ? " " + unit : "") + qsTr(" vs 7-day average")
    }
    // The tile caption: the trend when there is one, else the date of the value.
    function tileCaption(series, unit, decimals) {
        const t = trend(series, unit, decimals)
        return t.length > 0 ? t : lastDate(series)
    }
    // "This week" (André, 2026-10-26: "how much activity you did this week"): the watch's daily
    // activity kcal from Monday to today. The days are already merged over every watch read.
    readonly property var weekActivity: {
        const now = new Date()
        const monday = new Date(now.getFullYear(), now.getMonth(),
                                now.getDate() - (now.getDay() + 6) % 7)
        const from = Qt.formatDate(monday, "yyyy-MM-dd")
        const today = Qt.formatDate(now, "yyyy-MM-dd")
        const series = HealthService.watchActivity
        let kcal = 0, days = 0
        for (let i = 0; i < series.length; ++i) {
            if (series[i].date >= from && series[i].date <= today) {
                kcal += series[i].value
                days++
            }
        }
        return { kcal: kcal, days: days, from: from }
    }
    // Steps per day, Monday to Sunday of this week, from the merged steps (watch over the cloud
    // sources). value -1 = a day still to come or with no count.
    readonly property var weekSteps: {
        const now = new Date()
        const today = Qt.formatDate(now, "yyyy-MM-dd")
        const byDate = {}
        const series = HealthService.steps
        for (let i = 0; i < series.length; ++i)
            byDate[series[i].date] = series[i].value
        const days = []
        let total = 0, max = 0, any = false
        for (let d = 0; d < 7; ++d) {
            const day = new Date(now.getFullYear(), now.getMonth(),
                                 now.getDate() - (now.getDay() + 6) % 7 + d)
            const key = Qt.formatDate(day, "yyyy-MM-dd")
            const has = byDate[key] !== undefined && key <= today
            if (has) {
                total += byDate[key]
                max = Math.max(max, byDate[key])
                any = true
            }
            days.push({ label: Qt.locale("en").dayName(day.getDay(), Locale.ShortFormat),
                        value: has ? byDate[key] : -1, today: key === today })
        }
        for (let d = 0; d < days.length; ++d)
            days[d].max = max
        return { days: days, total: total, max: max, any: any }
    }
    // The Ambit3's recovery snapshot from the last watch read: {time, hours, peak, spanHours}.
    readonly property bool hasRecovery: HealthService.watchRecovery.hours !== undefined
    function recoveryText(h) {
        var m = Math.round(h * 60)
        return m < 60 ? m + qsTr(" min") : Math.floor(m / 60) + qsTr(" h ") + (m % 60) + qsTr(" min")
    }
    Connections {
        target: HealthService
        function onChanged() { /* charts repaint on their own series bindings */ }
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
                text: qsTr("Health")
                font.pixelSize: Theme.fontSizeLargeTitle
                font.bold: true
                color: Theme.text
            }

            // --- Needs Garmin sign-in ---
            Card {
                width: parent.width
                variant: "flat"
                visible: HealthService.needsLogin
                // #9 (André, 2026-09-02): one-tap route to the shared Garmin connection.
                Column {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Text {
                        width: parent.width; wrapMode: Text.WordWrap; color: Theme.mutedText
                        text: qsTr("Connect Garmin Connect to see your resting heart rate, steps, " +
                                   "HRV and body battery here.")
                    }
                    RoundedButton {
                        text: qsTr("Open Settings → Connections")
                        onClicked: { NavBus.pendingSettingsSection = "connections"; NavBus.navigate("settings") }
                    }
                }
            }

            // --- Latest tiles ---
            Card {
                width: parent.width
                visible: HealthService.rhr.length > 0 || HealthService.steps.length > 0
                         || HealthService.watchActivity.length > 0 || root.hasRecovery
                // One tile per number (desktop picks #12). Wraps: with every source on, the
                // tiles are wider than the card.
                Flow {
                    id: tiles
                    width: parent.width
                    spacing: Theme.spacingSmall
                    readonly property int tileWidth: 176
                    StatTile {
                        width: tiles.tileWidth
                        visible: HealthService.latestRhr > 0
                        label: qsTr("Resting HR")
                        value: Math.round(HealthService.latestRhr)
                        unit: qsTr("bpm")
                        caption: root.tileCaption(HealthService.rhr, qsTr("bpm"), 0)
                        badge: root.oldBadge(HealthService.rhr)
                    }
                    StatTile {
                        width: tiles.tileWidth
                        visible: HealthService.latestSteps > 0
                        label: qsTr("Steps (latest day)")
                        // Explicit locale + format, not the bare no-arg toLocaleString(): besides
                        // following the ambient OS locale (see DateFormat.qml's note), the
                        // no-arg form switches to scientific notation past 6 digits (e.g.
                        // "1,23457e+06"), which is a step count, never a number anyone wants in
                        // scientific notation.
                        value: Math.round(HealthService.latestSteps).toLocaleString(Qt.locale("en"), 'f', 0)
                        caption: root.lastDate(HealthService.steps)
                        badge: root.oldBadge(HealthService.steps)
                    }
                    StatTile {
                        width: tiles.tileWidth
                        // The watch's "Activity today" (kcal), read with the steps.
                        visible: HealthService.watchActivity.length > 0
                        label: qsTr("Activity (watch)")
                        value: Math.round(HealthService.latestWatchActivity)
                        unit: qsTr("kcal")
                        caption: root.lastDate(HealthService.watchActivity)
                        badge: root.oldBadge(HealthService.watchActivity)
                    }
                    StatTile {
                        width: tiles.tileWidth
                        // The watch's activity kcal from Monday to today, every watch merged.
                        visible: root.weekActivity.days > 0
                        label: qsTr("This week (watch)")
                        value: Math.round(root.weekActivity.kcal).toLocaleString(Qt.locale("en"), 'f', 0)
                        unit: qsTr("kcal")
                        caption: qsTr("since Monday ") + DateFormat.date(root.weekActivity.from)
                    }
                    StatTile {
                        width: tiles.tileWidth
                        // The watch's "Recovery time" at the last watch read, and its peak over
                        // the watch's last 24 h of quarter-hour samples.
                        visible: root.hasRecovery
                        label: qsTr("Recovery time (watch)")
                        value: root.hasRecovery ? root.recoveryText(HealthService.watchRecovery.hours) : ""
                        caption: !root.hasRecovery ? "" :
                                 DateFormat.date(String(HealthService.watchRecovery.time).slice(0, 10))
                                 + " " + String(HealthService.watchRecovery.time).slice(11, 16)
                                 + qsTr(" · peak ") + root.recoveryText(HealthService.watchRecovery.peak)
                    }
                    StatTile {
                        width: tiles.tileWidth
                        visible: HealthService.latestHrv > 0
                        label: qsTr("HRV (overnight)")
                        value: Math.round(HealthService.latestHrv)
                        unit: qsTr("ms")
                        caption: root.tileCaption(HealthService.hrv, qsTr("ms"), 0)
                        badge: root.oldBadge(HealthService.hrv)
                    }
                    StatTile {
                        width: tiles.tileWidth
                        // The Ambit3's own morning/spot rMSSD - a DIFFERENT measurement from the
                        // overnight value above, so it gets its own tile (and its own coloured
                        // line below), tracked against its own baseline, not compared to it.
                        visible: (HealthService.ambitHrvEnabled || HealthService.coospoHrvEnabled)
                                 && HealthService.latestHrvAmbit > 0
                        label: qsTr("Morning HRV")
                        value: Math.round(HealthService.latestHrvAmbit)
                        unit: qsTr("ms")
                        caption: root.tileCaption(HealthService.hrvAmbit, qsTr("ms"), 0)
                        badge: root.oldBadge(HealthService.hrvAmbit)
                    }
                    StatTile {
                        width: tiles.tileWidth
                        visible: HealthService.latestBodyBattery > 0
                        label: qsTr("Body battery (peak)")
                        value: Math.round(HealthService.latestBodyBattery)
                        caption: root.lastDate(HealthService.bodyBattery)
                        badge: root.oldBadge(HealthService.bodyBattery)
                    }
                    StatTile {
                        width: tiles.tileWidth
                        visible: HealthService.latestSleep > 0
                        label: qsTr("Sleep (last night)")
                        value: HealthService.latestSleep.toFixed(1)
                        unit: qsTr("h")
                        caption: root.tileCaption(HealthService.sleep, qsTr("h"), 1)
                        badge: root.oldBadge(HealthService.sleep)
                    }
                    StatTile {
                        width: tiles.tileWidth
                        // Train Libre's sleep health score (sleepscore.cpp), 0-100.
                        visible: HealthService.sleepScore.length > 0
                        label: qsTr("Sleep score")
                        value: Math.round(HealthService.latestSleepScore)
                        caption: root.lastDate(HealthService.sleepScore)
                        badge: root.oldBadge(HealthService.sleepScore)
                    }
                }
            }

            // --- Sleep & Recovery (Polar band, overnight HRV via PMD PPI over Bluetooth) ---
            Card {
                width: parent.width
                Column {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Text { text: qsTr("Sleep & Recovery (Polar band)"); color: Theme.text
                           font.pixelSize: Theme.fontSizeLabel; font.bold: true }
                    Text {
                        width: parent.width; wrapMode: Text.WordWrap; color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeLabel
                        text: qsTr("Wear a Polar Verity Sense and keep it near this computer. Tap " +
                                   "Start at bedtime, Stop in the morning — it records your beat-to-" +
                                   "beat intervals over Bluetooth and computes overnight HRV + " +
                                   "resting HR here, on your machine.")
                    }
                    Row {
                        spacing: Theme.spacingSmall
                        RoundedButton {
                            text: HealthService.sleepRecording ? qsTr("Stop (morning)")
                                                               : qsTr("Start (bedtime)")
                            onClicked: HealthService.sleepRecording
                                       ? HealthService.stopSleepRecording()
                                       : HealthService.startSleepRecording()
                        }
                    }
                    Text {
                        visible: HealthService.sleepMessage.length > 0
                        width: parent.width; wrapMode: Text.WordWrap
                        color: Theme.text; font.pixelSize: Theme.fontSizeLabel
                        text: HealthService.sleepMessage
                    }
                }
            }

            // --- Charts ---
            Card {
                width: parent.width
                visible: HealthService.rhr.length > 1
                variant: "flat"   // trend chart - supporting, not the headline tiles
                height: 220
                MetricChart {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingSmall
                    label: qsTr("Resting heart rate")
                    unit: qsTr(" bpm")
                    series: HealthService.rhr
                }
            }
            // Steps this week (André, 2026-10-26): one bar per day, Monday to Sunday. Days still to
            // come, and days nothing counted, show a flat stub.
            Card {
                width: parent.width
                visible: root.weekSteps.any
                variant: "flat"
                height: 180
                Column {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingSmall
                    spacing: Theme.spacingSmall
                    Row {
                        spacing: Theme.spacingSmall
                        Text { text: qsTr("Steps this week"); color: Theme.text
                               font.pixelSize: Theme.fontSizeLabel; font.bold: true }
                        Text { text: Math.round(root.weekSteps.total).toLocaleString(Qt.locale("en"), 'f', 0)
                               color: Theme.accent; font.pixelSize: Theme.fontSizeLabel; font.bold: true }
                    }
                    Row {
                        id: weekBars
                        width: parent.width
                        height: parent.height - y
                        spacing: Theme.spacingSmall
                        Repeater {
                            model: root.weekSteps.days
                            Column {
                                id: dayCol
                                required property var modelData
                                width: (parent.width - Theme.spacingSmall * 6) / 7
                                height: parent.height
                                spacing: 2
                                Item {
                                    width: parent.width
                                    height: dayCol.height - barValue.height - barDay.height - 4
                                    Rectangle {
                                        anchors.bottom: parent.bottom
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        width: Math.min(parent.width, 48)
                                        radius: Theme.radiusSmall / 2
                                        height: dayCol.modelData.value > 0
                                                ? Math.max(4, parent.height * dayCol.modelData.value
                                                              / Math.max(1, dayCol.modelData.max))
                                                : 4
                                        color: dayCol.modelData.value > 0 ? Theme.accent : Theme.border
                                    }
                                }
                                Text {
                                    id: barValue
                                    width: parent.width
                                    horizontalAlignment: Text.AlignHCenter
                                    text: dayCol.modelData.value >= 0
                                          ? Math.round(dayCol.modelData.value).toLocaleString(Qt.locale("en"), 'f', 0) : ""
                                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                }
                                Text {
                                    id: barDay
                                    width: parent.width
                                    horizontalAlignment: Text.AlignHCenter
                                    text: dayCol.modelData.label
                                    color: dayCol.modelData.today ? Theme.text : Theme.mutedText
                                    font.pixelSize: Theme.fontSizeCaption; font.bold: dayCol.modelData.today
                                }
                            }
                        }
                    }
                }
            }
            Card {
                width: parent.width
                visible: HealthService.steps.length > 1
                variant: "flat"   // trend chart
                height: 220
                MetricChart {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingSmall
                    label: qsTr("Steps")
                    series: HealthService.steps
                }
            }
            Card {
                width: parent.width
                visible: HealthService.watchActivity.length > 1
                variant: "flat"   // trend chart
                height: 220
                MetricChart {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingSmall
                    label: qsTr("Activity (watch)")
                    unit: qsTr(" kcal")
                    series: HealthService.watchActivity
                }
            }
            Card {
                width: parent.width
                visible: HealthService.hrv.length > 1
                variant: "flat"   // trend chart
                height: 220
                MetricChart {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingSmall
                    label: qsTr("HRV (overnight)")
                    unit: qsTr(" ms")
                    series: HealthService.hrv
                }
            }
            Card {
                width: parent.width
                // Separate card, separate colour: the Ambit3 morning/spot HRV line is its own
                // measurement (compare it to its own history, not to the overnight line above).
                visible: (HealthService.ambitHrvEnabled || HealthService.coospoHrvEnabled)
                         && HealthService.hrvAmbit.length > 1
                variant: "flat"   // trend chart
                height: 220
                MetricChart {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingSmall
                    label: qsTr("Morning HRV")
                    unit: qsTr(" ms")
                    series: HealthService.hrvAmbit
                }
            }
            Card {
                width: parent.width
                visible: HealthService.bodyBattery.length > 1
                variant: "flat"   // trend chart
                height: 220
                MetricChart {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingSmall
                    label: qsTr("Body battery (daily peak)")
                    series: HealthService.bodyBattery
                }
            }
            Card {
                width: parent.width
                visible: HealthService.sleep.length > 1
                variant: "flat"   // trend chart
                height: 220
                MetricChart {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingSmall
                    label: qsTr("Sleep")
                    unit: qsTr(" h")
                    series: HealthService.sleep
                }
            }
            Card {
                width: parent.width
                visible: HealthService.sleepScore.length > 1
                variant: "flat"   // trend chart
                height: 220
                MetricChart {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingSmall
                    // Duration-only for intervals.icu nights; Garmin nights add stages, awake
                    // time, bed time and regularity (Train Libre's Sleep Health Score).
                    label: qsTr("Sleep score")
                    unit: ""
                    series: HealthService.sleepScore
                }
            }

            // --- Loading / empty / error (desktop picks #11, #13) ---
            readonly property bool _noData: HealthService.rhr.length === 0 && HealthService.steps.length === 0
                                            && HealthService.hrv.length === 0 && HealthService.bodyBattery.length === 0
            SkeletonList {
                visible: !HealthService.needsLogin && HealthService.loading && parent._noData
                count: 3
            }
            ErrorBanner {
                detail: !HealthService.needsLogin && !HealthService.loading && parent._noData
                        ? HealthService.lastError : ""
                context: qsTr("reading health data")
                showPlainDetail: true
                canRetry: true
                onRetry: HealthService.refresh()
            }
            EmptyState {
                visible: !HealthService.needsLogin && !HealthService.loading && parent._noData
                         && HealthService.lastError.length === 0
                glyph: Icons.health
                title: qsTr("No health data yet")
                text: qsTr("Connect intervals.icu or Garmin Connect, or plug in your watch to read its steps and HRV.")
                actionText: qsTr("Open Settings → Connections")
                onAction: { NavBus.pendingSettingsSection = "connections"; NavBus.navigate("settings") }
            }

            Text {
                width: parent.width; wrapMode: Text.WordWrap; color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                text: qsTr("Resting HR, HRV and steps are merged from intervals.icu and Garmin " +
                           "Connect (plus your Ambit3's own steps and HRV, and manual entries), " +
                           "de-duplicated by day. The Ambit3 keeps 30 days of steps; Sommet keeps " +
                           "every day it reads at each sync, with its activity calories and recovery time. Body battery comes from Garmin. Sleep " +
                           "uses the single source you pick in Settings.")
            }
        }
    }
}
