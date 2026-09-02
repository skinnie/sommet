import QtQuick
import QtQuick.Controls
import AmbitApp

// Health page (André, 2026-08-24): daily resting heart rate and steps from Garmin Connect.
// Login is shared with the Weight page's Garmin sign-in (same token store), so this page just
// points there if you're not signed in. Read-only.
Item {
    id: root

    Component.onCompleted: HealthService.refresh()
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
                        onClicked: NavBus.navigate("settings")
                    }
                }
            }

            // --- Latest tiles ---
            Card {
                width: parent.width
                visible: HealthService.rhr.length > 0 || HealthService.steps.length > 0
                Row {
                    width: parent.width
                    spacing: Theme.spacingLarge * 2
                    Column {
                        spacing: 2
                        visible: HealthService.latestRhr > 0
                        Text { text: qsTr("Resting HR"); color: Theme.mutedText
                               font.pixelSize: Theme.fontSizeLabel }
                        Text { text: Math.round(HealthService.latestRhr) + qsTr(" bpm")
                               color: Theme.text; font.pixelSize: Theme.fontSizeDisplay; font.bold: true }
                    }
                    Column {
                        spacing: 2
                        visible: HealthService.latestSteps > 0
                        Text { text: qsTr("Steps (latest day)"); color: Theme.mutedText
                               font.pixelSize: Theme.fontSizeLabel }
                        Text { text: Math.round(HealthService.latestSteps).toLocaleString()
                               color: Theme.text; font.pixelSize: Theme.fontSizeDisplay; font.bold: true }
                    }
                    Column {
                        spacing: 2
                        visible: HealthService.latestHrv > 0
                        Text { text: qsTr("HRV (overnight)"); color: Theme.mutedText
                               font.pixelSize: Theme.fontSizeLabel }
                        Text { text: Math.round(HealthService.latestHrv) + qsTr(" ms")
                               color: Theme.text; font.pixelSize: Theme.fontSizeDisplay; font.bold: true }
                    }
                    Column {
                        spacing: 2
                        // The Ambit3's own morning/spot rMSSD - a DIFFERENT measurement from the
                        // overnight value above, so it gets its own tile (and its own coloured
                        // line below), tracked against its own baseline, not compared to it.
                        visible: HealthService.ambitHrvEnabled && HealthService.latestHrvAmbit > 0
                        Text { text: qsTr("Morning HRV (Ambit3)"); color: Theme.mutedText
                               font.pixelSize: Theme.fontSizeLabel }
                        Text { text: Math.round(HealthService.latestHrvAmbit) + qsTr(" ms")
                               color: Theme.text
                               font.pixelSize: Theme.fontSizeDisplay; font.bold: true }
                    }
                    Column {
                        spacing: 2
                        visible: HealthService.latestBodyBattery > 0
                        Text { text: qsTr("Body battery (peak)"); color: Theme.mutedText
                               font.pixelSize: Theme.fontSizeLabel }
                        Text { text: Math.round(HealthService.latestBodyBattery)
                               color: Theme.text; font.pixelSize: Theme.fontSizeDisplay; font.bold: true }
                    }
                    Column {
                        spacing: 2
                        visible: HealthService.latestSleep > 0
                        Text { text: qsTr("Sleep (last night)"); color: Theme.mutedText
                               font.pixelSize: Theme.fontSizeLabel }
                        Text { text: HealthService.latestSleep.toFixed(1) + qsTr(" h")
                               color: Theme.text; font.pixelSize: Theme.fontSizeDisplay; font.bold: true }
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
                visible: HealthService.ambitHrvEnabled && HealthService.hrvAmbit.length > 1
                variant: "flat"   // trend chart
                height: 220
                MetricChart {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingSmall
                    label: qsTr("Morning HRV (Ambit3)")
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

            // --- Empty / error ---
            Text {
                width: parent.width
                visible: !HealthService.needsLogin && !HealthService.loading
                         && HealthService.rhr.length === 0 && HealthService.steps.length === 0
                         && HealthService.hrv.length === 0 && HealthService.bodyBattery.length === 0
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                text: HealthService.lastError.length > 0
                      ? HealthService.lastError
                      : qsTr("No Garmin health data for the last 30 days.")
            }

            Text {
                width: parent.width; wrapMode: Text.WordWrap; color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                text: qsTr("Resting HR, HRV and steps are merged from intervals.icu and Garmin " +
                           "Connect (plus your watch's HRV and manual entries), de-duplicated by " +
                           "day. Body battery comes from Garmin. Sleep uses the single source you " +
                           "pick in Settings.")
            }
        }
    }
}
